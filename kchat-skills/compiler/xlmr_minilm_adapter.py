"""XLM-R MiniLM-L6 ``SLMAdapter`` — runs the on-device guardrail
encoder classifier.

Spec references:

* PHASES.md Phase 3 — "Define the runtime adapter interface — the
  boundary between the pipeline and any encoder-classifier backend (so
  we can swap backends without changing skill packs)."
* ARCHITECTURE.md "Hybrid Local Pipeline" step 4 — "Encoder-based
  contextual classification (XLM-R MiniLM-L6)".

This adapter is one concrete implementation of the
:class:`slm_adapter.SLMAdapter` Protocol. It targets the
**XLM-R MiniLM-L6** encoder model (``nreimers/mMiniLMv2-L6-H384-distilled-from-XLMR-Large``)
loaded via :mod:`transformers`. The model is encoder-only — no chat
completions, no temperature, no token budgets for generation. The
adapter:

1. Tokenises the message text.
2. Runs the encoder once to get a contextual embedding (the [CLS]
   token, mean-pooled across the sequence and L2-normalised).
3. Compares the embedding to a fixed bank of *category prototype*
   embeddings (one per taxonomy category) and selects the
   highest-similarity category.
4. Blends the embedding-derived category with deterministic
   ``local_signals`` (URL risk, PII patterns, scam patterns, lexicon
   hits, media descriptors) — signals take precedence when they are
   strong, embeddings break ties otherwise.
5. Coerces the final dict to the ``kchat.guardrail.output.v1`` schema
   and returns it. Out-of-range fields collapse to a SAFE fallback.

Design constraints inherited from the SLMAdapter contract:

* **Deterministic.** No sampling — argmax over fixed prototype
  embeddings. Identical input → identical output.
* **Offline.** Model weights are loaded from a local path (``model_path``).
  No network calls during inference. If weights are missing, the
  adapter falls back to a SAFE output.
* **Privacy-safe fallback.** If the encoder fails to load, raises an
  exception during inference, or produces an embedding outside the
  expected shape, the adapter returns a SAFE output (category 0,
  severity 0) rather than raising.
* **No generative state.** The adapter has no notion of compiled
  prompts, response_format, max_tokens, or temperature — those are
  remnants of the old chat-completions backend and are intentionally
  ignored if present in ``input.constraints``.
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from slm_adapter import (  # type: ignore[import-not-found]
    CAT_CHILD_SAFETY,
    CAT_PRIVATE_DATA,
    CAT_SAFE,
    CAT_SCAM_FRAUD,
    CAT_SEXUAL_ADULT,
    SLMAdapter,
)


# ---------------------------------------------------------------------------
# Model identity.
# ---------------------------------------------------------------------------
XLMR_MINILM_MODEL_NAME = "XLM-R-MiniLM-L6"
XLMR_MINILM_MODEL_ID = (
    "nreimers/mMiniLMv2-L6-H384-distilled-from-XLMR-Large"
)


# ---------------------------------------------------------------------------
# Category prototypes — short multilingual-friendly descriptions used
# for zero-shot classification via cosine similarity. Index = taxonomy
# category id (0..15). Order MUST match ``slm_adapter.CAT_*`` constants.
# ---------------------------------------------------------------------------
CATEGORY_PROTOTYPES: tuple[str, ...] = (
    "benign safe everyday conversation, friendly chat, neutral message",  # 0 SAFE
    "child safety risk, minor in danger, grooming, child sexual abuse material",  # 1 CHILD_SAFETY
    "self harm, suicide ideation, eating disorder, hopelessness disclosure",  # 2 SELF_HARM
    "violent threat, intent to harm a specific person, planning an attack",  # 3 VIOLENCE_THREAT
    "violent extremism, terrorism recruitment, hateful group glorification",  # 4 EXTREMISM
    "personal harassment, repeated targeted insults, doxxing, stalking",  # 5 HARASSMENT
    "hate speech against a protected group, slurs, dehumanising language",  # 6 HATE
    "scam, fraud, phishing, advance fee, fake giveaway, credential theft",  # 7 SCAM_FRAUD
    "malware link, suspicious download, exploit kit, drive-by infection",  # 8 MALWARE_LINK
    "private data, personally identifying information, account credentials, "
    "credit card, government id leak",  # 9 PRIVATE_DATA
    "explicit sexual adult content, pornography, graphic nudity",  # 10 SEXUAL_ADULT
    "drugs, weapons, illegal substances, firearm sale or trade",  # 11 DRUGS_WEAPONS
    "illegal goods, counterfeit, smuggled or stolen items, illegal services",  # 12 ILLEGAL_GOODS
    "health misinformation, dangerous medical claim, anti-vaccine falsehood",  # 13 MISINFORMATION_HEALTH
    "civic misinformation, election fraud claim, deceptive political content",  # 14 MISINFORMATION_CIVIC
    "community rule violation, group-specific etiquette breach, off-topic",  # 15 COMMUNITY_RULE
)


# Output-schema bounds. Mirror ``kchat-skills/global/output_schema.json``.
_VALID_REASON_CODES = frozenset(
    {
        "LEXICON_HIT",
        "SCAM_PATTERN",
        "PRIVATE_DATA_PATTERN",
        "URL_RISK",
        "QUOTED_SPEECH_CONTEXT",
        "NEWS_CONTEXT",
        "EDUCATION_CONTEXT",
        "COUNTERSPEECH_CONTEXT",
        "GROUP_AGE_MODE",
        "JURISDICTION_OVERRIDE",
        "COMMUNITY_RULE",
        "CHILD_SAFETY_FLOOR",
    }
)

_ACTION_KEYS = (
    "label_only",
    "warn",
    "strong_warn",
    "critical_intervention",
    "suggest_redact",
)


def _zero_actions() -> dict[str, bool]:
    return {key: False for key in _ACTION_KEYS}


def safe_fallback_output() -> dict[str, Any]:
    """Return a SAFE ``kchat.guardrail.output.v1``-shaped dict.

    Used whenever the encoder cannot run (model weights missing,
    transformers import error, runtime exception during inference).
    Matches the privacy-first invariant: when in doubt, the runtime
    degrades to SAFE rather than to a permissive label.
    """
    return {
        "severity": 0,
        "category": CAT_SAFE,
        "confidence": 0.05,
        "actions": _zero_actions(),
        "reason_codes": [],
        "rationale_id": "xlmr_minilm_safe_fallback_v1",
    }


# ---------------------------------------------------------------------------
# Adapter.
# ---------------------------------------------------------------------------
@dataclass
class XLMRMiniLMAdapter:
    """SLMAdapter backed by the XLM-R MiniLM-L6 encoder.

    Parameters
    ----------
    model_path
        Local filesystem path *or* Hugging Face model id from which to
        load the encoder. Defaults to :data:`XLMR_MINILM_MODEL_ID`. The
        adapter only loads the model on first :meth:`classify` call so
        constructing one is cheap.
    max_seq_length
        Sequence length used by the tokenizer. The XLM-R MiniLM-L6
        encoder supports up to 512 tokens; 128 is plenty for short
        chat messages and ~3-4× faster per call.
    similarity_threshold
        Reserved for backwards compatibility — the embedding head now
        uses margin-based confidence (see ``softmax_temperature`` /
        ``min_margin``) rather than a raw-cosine cutoff. Kept on the
        dataclass so existing callers can still construct the adapter
        with a positional argument.
    softmax_temperature
        Temperature applied to the cosine-similarity vector before
        softmax. XLM-R's encoder embedding space is dense, so raw
        cosine values cluster tightly (typically ``0.93``-``0.96``);
        the small temperature (``0.01`` by default) amplifies the
        relative differences so the resulting probabilities are
        well-calibrated for argmax + thresholding.
    min_margin
        Minimum probability margin between the top-1 and top-2 softmax
        scores required to commit to a non-SAFE prediction. If the
        margin is below this value the adapter returns SAFE — the
        encoder is "uncertain", and the deterministic detectors +
        threshold policy take over.
    logger
        Optional :class:`logging.Logger` used for structured per-call
        latency / error logging. Defaults to the module logger.

    Latency
    -------
    The most recent call's wall-clock latency in milliseconds is
    stored in :attr:`last_latency_ms` so callers (the demo / benchmark
    scripts) can record per-call timings without instrumenting the
    pipeline.
    """

    model_path: str = XLMR_MINILM_MODEL_ID
    max_seq_length: int = 128
    similarity_threshold: float = 0.30
    softmax_temperature: float = 0.01
    min_margin: float = 0.10
    logger: Optional[logging.Logger] = None
    last_latency_ms: float = field(default=0.0, init=False)
    _tokenizer: Any = field(default=None, init=False, repr=False)
    _model: Any = field(default=None, init=False, repr=False)
    _prototype_embeddings: Any = field(default=None, init=False, repr=False)
    _load_failed: bool = field(default=False, init=False, repr=False)

    # ------------------------------------------------------------------
    # Public API.
    # ------------------------------------------------------------------
    def classify(self, input: dict[str, Any]) -> dict[str, Any]:
        """Run the encoder over ``input`` and return a validated output dict.

        Returns :func:`safe_fallback_output` whenever the encoder
        cannot be loaded, raises during inference, or produces output
        that cannot be coerced into the output schema.
        """
        log = self.logger or _module_logger
        start = time.perf_counter()

        try:
            self._ensure_loaded()
        except Exception as exc:  # noqa: BLE001 — defensive boundary
            self.last_latency_ms = (time.perf_counter() - start) * 1000.0
            log.warning(
                "XLM-R MiniLM-L6 model load failed (%s); falling back to SAFE",
                exc,
            )
            return safe_fallback_output()

        if self._load_failed or self._model is None:
            self.last_latency_ms = (time.perf_counter() - start) * 1000.0
            return safe_fallback_output()

        message = input.get("message") or {}
        text = message.get("text") if isinstance(message, dict) else ""
        if not isinstance(text, str):
            text = ""
        local_signals = input.get("local_signals") or {}
        if not isinstance(local_signals, dict):
            local_signals = {}

        try:
            embedding = self._encode(text)
        except Exception as exc:  # noqa: BLE001 — defensive boundary
            self.last_latency_ms = (time.perf_counter() - start) * 1000.0
            log.warning(
                "XLM-R MiniLM-L6 inference error (%s); falling back to SAFE",
                exc,
            )
            return safe_fallback_output()

        try:
            raw_output = self._classify_from_embedding_and_signals(
                embedding, local_signals
            )
        except Exception as exc:  # noqa: BLE001 — defensive boundary
            self.last_latency_ms = (time.perf_counter() - start) * 1000.0
            log.warning(
                "XLM-R MiniLM-L6 classification error (%s); falling back to SAFE",
                exc,
            )
            return safe_fallback_output()

        self.last_latency_ms = (time.perf_counter() - start) * 1000.0
        return _coerce_to_output_schema(raw_output)

    # ------------------------------------------------------------------
    # Internals — model loading + encoding.
    # ------------------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._model is not None or self._load_failed:
            return
        try:
            import torch  # type: ignore[import-not-found]  # noqa: F401
            from transformers import (  # type: ignore[import-not-found]
                AutoModel,
                AutoTokenizer,
            )
        except Exception as exc:  # noqa: BLE001 — soft dependency
            self._load_failed = True
            (self.logger or _module_logger).warning(
                "transformers/torch unavailable (%s); XLM-R MiniLM-L6 "
                "adapter will return SAFE for every call",
                exc,
            )
            return

        path = self.model_path
        # Refuse network fetches when the path looks like an HF id and
        # there is no local cache for it. The pipeline is supposed to
        # run fully offline; configure ``model_path`` to a local
        # directory to enable real inference.
        if not _looks_like_local_path(path):
            (self.logger or _module_logger).info(
                "XLM-R MiniLM-L6 model_path=%r is not a local directory; "
                "transformers may need to download weights",
                path,
            )

        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                path, local_files_only=True
            )
            self._model = AutoModel.from_pretrained(
                path, local_files_only=True
            )
            self._model.eval()
        except Exception as exc:  # noqa: BLE001 — model load failed
            self._load_failed = True
            (self.logger or _module_logger).warning(
                "Failed to load XLM-R MiniLM-L6 from %r (%s); falling back to SAFE",
                path,
                exc,
            )
            self._tokenizer = None
            self._model = None
            return

        # Pre-compute prototype embeddings once.
        try:
            self._prototype_embeddings = self._encode_batch(
                list(CATEGORY_PROTOTYPES)
            )
        except Exception as exc:  # noqa: BLE001 — defensive boundary
            self._load_failed = True
            (self.logger or _module_logger).warning(
                "Failed to encode XLM-R MiniLM-L6 category prototypes (%s); "
                "falling back to SAFE",
                exc,
            )
            self._tokenizer = None
            self._model = None
            self._prototype_embeddings = None

    def _encode_batch(self, texts: list[str]) -> Any:
        """Encode ``texts`` and return a (len(texts), hidden) tensor.

        Uses mean-pooling across the sequence with attention masking,
        then L2-normalises each row so cosine similarity reduces to a
        plain dot product.
        """
        import torch  # type: ignore[import-not-found]

        if self._tokenizer is None or self._model is None:
            raise RuntimeError("model not loaded")
        encoded = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=self.max_seq_length,
            return_tensors="pt",
        )
        with torch.no_grad():
            output = self._model(**encoded)
        # Mean-pool over tokens with attention mask.
        last_hidden = output.last_hidden_state  # (batch, seq, hidden)
        mask = encoded["attention_mask"].unsqueeze(-1).float()
        summed = (last_hidden * mask).sum(dim=1)
        counts = mask.sum(dim=1).clamp(min=1.0)
        pooled = summed / counts
        # L2 normalize.
        norms = pooled.norm(dim=1, keepdim=True).clamp(min=1e-12)
        return pooled / norms

    def _encode(self, text: str) -> Any:
        """Encode a single ``text`` and return a 1D tensor."""
        batch = self._encode_batch([text or ""])
        return batch[0]

    # ------------------------------------------------------------------
    # Internals — classification head.
    # ------------------------------------------------------------------
    def _classify_from_embedding_and_signals(
        self,
        embedding: Any,
        signals: dict[str, Any],
    ) -> dict[str, Any]:
        """Combine the encoder embedding with deterministic signals.

        Signal-based overrides take precedence when they are
        unambiguous (CHILD_SAFETY lexicon, strong PII / scam patterns,
        URL risk above 0.8, NSFW media). Otherwise the highest-cosine
        prototype wins, gated by ``similarity_threshold``.
        """
        import torch  # type: ignore[import-not-found]

        if self._prototype_embeddings is None:
            return safe_fallback_output()

        # --- Strong, deterministic overrides (mirror the deterministic
        # detector contract — these always trump the embedding head).
        lexicon_hits = signals.get("lexicon_hits") or []
        pii_hits = signals.get("pii_patterns_hit") or []
        scam_hits = signals.get("scam_patterns_hit") or []
        url_risk = float(signals.get("url_risk") or 0.0)
        media = signals.get("media_descriptors") or []

        # CHILD_SAFETY — any lexicon hit on category 1 hits the
        # severity-5 floor regardless of embedding similarity.
        child_lex = [
            h for h in lexicon_hits if h.get("category") == CAT_CHILD_SAFETY
        ]
        if child_lex:
            weight = max(float(h.get("weight", 0.5)) for h in child_lex)
            confidence = max(0.45, min(0.99, weight))
            return {
                "severity": 5,
                "category": CAT_CHILD_SAFETY,
                "confidence": confidence,
                "actions": {
                    **_zero_actions(),
                    "critical_intervention": True,
                },
                "reason_codes": ["LEXICON_HIT", "CHILD_SAFETY_FLOOR"],
                "rationale_id": "child_safety_floor_v1",
                "resource_link_id": "child_safety_resources_v1",
            }

        # PRIVATE_DATA — any PII pattern.
        if pii_hits:
            confidence = min(0.95, 0.55 + 0.1 * len(pii_hits))
            return {
                "severity": 3,
                "category": CAT_PRIVATE_DATA,
                "confidence": confidence,
                "actions": {
                    **_zero_actions(),
                    "warn": True,
                    "suggest_redact": True,
                },
                "reason_codes": ["PRIVATE_DATA_PATTERN"],
                "rationale_id": "private_data_pii_v1",
            }

        # SCAM_FRAUD — high URL risk or scam patterns.
        if url_risk > 0.8 or scam_hits:
            confidence = max(url_risk, 0.55 + 0.1 * len(scam_hits))
            confidence = min(0.95, confidence)
            reason_codes: list[str] = []
            if url_risk > 0.8:
                reason_codes.append("URL_RISK")
            if scam_hits:
                reason_codes.append("SCAM_PATTERN")
            return {
                "severity": 3,
                "category": CAT_SCAM_FRAUD,
                "confidence": confidence,
                "actions": {**_zero_actions(), "warn": True},
                "reason_codes": reason_codes,
                "rationale_id": "scam_credential_phish_v1",
            }

        # Lexicon-only hits — pick the highest-weight hit.
        if lexicon_hits:
            top = max(
                lexicon_hits, key=lambda h: float(h.get("weight", 0.0))
            )
            category = int(top.get("category", CAT_SAFE))
            weight = float(top.get("weight", 0.5))
            if category != CAT_SAFE:
                confidence = max(0.45, min(0.95, weight))
                return {
                    "severity": 3,
                    "category": category,
                    "confidence": confidence,
                    "actions": {**_zero_actions(), "warn": True},
                    "reason_codes": ["LEXICON_HIT"],
                    "rationale_id": f"lexicon_category_{category}_v1",
                }

        # Media NSFW.
        for m in media:
            nsfw = m.get("nsfw_score") if isinstance(m, dict) else None
            if nsfw is not None and float(nsfw) > 0.7:
                confidence = min(0.95, float(nsfw))
                return {
                    "severity": 3,
                    "category": CAT_SEXUAL_ADULT,
                    "confidence": confidence,
                    "actions": {**_zero_actions(), "warn": True},
                    "reason_codes": [],
                    "rationale_id": "sexual_adult_media_v1",
                }

        # --- Embedding head: softmax-calibrated cosine similarity.
        # Both ``embedding`` and ``self._prototype_embeddings`` are
        # already L2-normalised, so cosine = dot product. XLM-R's
        # multilingual embedding space is dense (raw cosine values
        # typically cluster between 0.93 and 0.96 across all 16
        # prototypes), so we apply a low-temperature softmax to get
        # well-separated probabilities, then require a meaningful
        # top-1 vs top-2 margin before committing to a non-SAFE label.
        sims = self._prototype_embeddings @ embedding
        sims_list = sims.detach().cpu().tolist() if hasattr(
            sims, "detach"
        ) else list(sims)
        sims_list = [float(s) for s in sims_list]

        # Softmax with low temperature.
        temp = self.softmax_temperature if self.softmax_temperature > 0 else 1.0
        scaled = [s / temp for s in sims_list]
        max_scaled = max(scaled)
        exps = [math.exp(s - max_scaled) for s in scaled]
        z = sum(exps) or 1.0
        probs = [e / z for e in exps]

        # Argmax + top-2 margin.
        ranked = sorted(
            range(len(probs)), key=lambda i: probs[i], reverse=True
        )
        best_idx = ranked[0]
        best_prob = probs[best_idx]
        runner_prob = probs[ranked[1]] if len(ranked) > 1 else 0.0
        margin = best_prob - runner_prob

        # SAFE wins when the encoder is uncertain (small margin) or
        # when the SAFE prototype itself is the argmax.
        if best_idx == CAT_SAFE or margin < self.min_margin:
            return {
                "severity": 0,
                "category": CAT_SAFE,
                "confidence": max(
                    0.05, min(0.95, float(probs[CAT_SAFE]))
                ),
                "actions": _zero_actions(),
                "reason_codes": [],
                "rationale_id": "xlmr_minilm_safe_v1",
            }

        # Otherwise return the predicted category at default severity 2
        # (label only) — the threshold policy and severity rubric
        # downstream will promote / demote based on context.
        confidence = max(0.05, min(0.95, float(best_prob)))
        return {
            "severity": 2,
            "category": best_idx,
            "confidence": confidence,
            "actions": {**_zero_actions(), "label_only": True},
            "reason_codes": [],
            "rationale_id": f"xlmr_minilm_category_{best_idx}_v1",
        }


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
_module_logger = logging.getLogger("kchat.guardrail.xlmr_minilm")


def _looks_like_local_path(path: str) -> bool:
    """Return True iff ``path`` looks like a filesystem directory."""
    return bool(path) and Path(path).is_dir()


def _coerce_to_output_schema(parsed: dict[str, Any]) -> dict[str, Any]:
    """Validate / normalise an output dict against the output schema.

    Missing or out-of-range required fields are filled with safe
    defaults rather than raising. Any malformed structure that cannot
    be repaired collapses to :func:`safe_fallback_output`.
    """
    try:
        category = int(parsed.get("category", CAT_SAFE))
    except (TypeError, ValueError):
        return safe_fallback_output()
    if not 0 <= category <= 15:
        return safe_fallback_output()

    try:
        severity = int(parsed.get("severity", 0))
    except (TypeError, ValueError):
        return safe_fallback_output()
    if not 0 <= severity <= 5:
        return safe_fallback_output()

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        return safe_fallback_output()
    if not 0.0 <= confidence <= 1.0:
        return safe_fallback_output()

    actions_in = parsed.get("actions") or {}
    if not isinstance(actions_in, dict):
        actions_in = {}
    actions = _zero_actions()
    for key in _ACTION_KEYS:
        if isinstance(actions_in.get(key), bool):
            actions[key] = actions_in[key]

    reason_codes_in = parsed.get("reason_codes") or []
    if not isinstance(reason_codes_in, list):
        reason_codes_in = []
    reason_codes = [
        rc
        for rc in reason_codes_in
        if isinstance(rc, str) and rc in _VALID_REASON_CODES
    ]
    # Deduplicate while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for rc in reason_codes:
        if rc not in seen:
            seen.add(rc)
            deduped.append(rc)

    rationale_id = parsed.get("rationale_id")
    if not isinstance(rationale_id, str) or not rationale_id:
        rationale_id = "xlmr_minilm_default_rationale_v1"

    out: dict[str, Any] = {
        "severity": severity,
        "category": category,
        "confidence": confidence,
        "actions": actions,
        "reason_codes": deduped,
        "rationale_id": rationale_id,
    }

    resource_link_id = parsed.get("resource_link_id")
    if isinstance(resource_link_id, str) and resource_link_id:
        out["resource_link_id"] = resource_link_id

    counter_updates_in = parsed.get("counter_updates") or []
    if isinstance(counter_updates_in, list):
        cleaned: list[dict[str, Any]] = []
        for upd in counter_updates_in:
            if not isinstance(upd, dict):
                continue
            counter_id = upd.get("counter_id")
            delta = upd.get("delta")
            if (
                isinstance(counter_id, str)
                and counter_id
                and isinstance(delta, int)
                and not isinstance(delta, bool)
            ):
                cleaned.append({"counter_id": counter_id, "delta": delta})
        if cleaned:
            out["counter_updates"] = cleaned

    return out


# ``json`` is unused below but imported lazily for parity with the
# old adapter — keep the import resolved so static analysers stay happy.
_ = json


# Make ``isinstance(adapter, SLMAdapter)`` cheap for tests that import
# the protocol.
_PROTOCOL_REFERENCE: SLMAdapter = XLMRMiniLMAdapter()  # type: ignore[assignment]
del _PROTOCOL_REFERENCE


__all__ = [
    "CATEGORY_PROTOTYPES",
    "XLMR_MINILM_MODEL_ID",
    "XLMR_MINILM_MODEL_NAME",
    "XLMRMiniLMAdapter",
    "safe_fallback_output",
]
