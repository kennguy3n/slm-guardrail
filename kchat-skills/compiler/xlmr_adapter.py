"""XLM-R ``EncoderAdapter`` — runs the on-device guardrail encoder
classifier through ONNX Runtime.

Spec references:

* PHASES.md Phase 3 — "Define the runtime adapter interface — the
  boundary between the pipeline and any encoder-classifier backend (so
  we can swap backends without changing skill packs)."
* ARCHITECTURE.md "Hybrid Local Pipeline" step 4 — "Encoder-based
  contextual classification (XLM-R)".

This adapter is one concrete implementation of the
:class:`encoder_adapter.EncoderAdapter` Protocol. It targets the
**XLM-R** multilingual encoder model exported to **ONNX INT8** and
loaded through :mod:`onnxruntime`. The model is encoder-only — no
chat completions, no temperature, no token budgets for generation.
The adapter:

1. Tokenises the message text with the SentencePiece tokenizer
   shipped alongside the ONNX model.
2. Runs the encoder once via :class:`onnxruntime.InferenceSession` to
   get a contextual embedding (mean-pooled across the sequence and
   L2-normalised).
3. Compares the embedding to a fixed bank of *category prototype*
   embeddings (one per taxonomy category) and selects the
   highest-similarity category, OR uses a trained ``Linear(384, 16)``
   head loaded from ``xlmr_head.npz`` when available.
4. Blends the embedding-derived category with deterministic
   ``local_signals`` (URL risk, PII patterns, scam patterns, lexicon
   hits, media descriptors) — signals take precedence when they are
   strong, embeddings break ties otherwise.
5. Coerces the final dict to the ``kchat.guardrail.output.v1`` schema
   and returns it. Out-of-range fields collapse to a SAFE fallback.
6. Attaches the raw mean-pooled, L2-normalised XLM-R embedding to
   the returned dict under the internal key ``_embedding`` (a 384-dim
   ``list[float]``). The underscore prefix signals this is not a
   first-class ``kchat.guardrail.output.v1`` field; downstream
   consumers (notably ``chat-storage-search``) cache the embedding in
   their ``search_vector`` table so a message's XLM-R encoder pass is
   computed at most once across the guardrail and search pipelines.
   The schema permits ``_*`` extras via ``patternProperties``.

Design constraints inherited from the EncoderAdapter contract:

* **Deterministic.** No sampling — argmax over fixed prototype
  embeddings (or the trained linear head). Identical input →
  identical output.
* **Offline.** Model weights are loaded from a local path
  (``model_path``). No network calls during inference. If weights
  are missing, the adapter falls back to a SAFE output.
* **Privacy-safe fallback.** If the ONNX session fails to load,
  raises an exception during inference, or produces an embedding
  outside the expected shape, the adapter returns a SAFE output
  (category 0, severity 0) rather than raising.
* **No PyTorch / transformers runtime dependency.** The adapter only
  needs :mod:`onnxruntime` + :mod:`sentencepiece` + :mod:`numpy` at
  runtime — small, ship-friendly dependencies suitable for iOS,
  Android, macOS, and Windows bundling. The one-time export script
  (``tools/export_xlmr_onnx.py``) uses transformers + torch but those
  do not ship on-device.
* **No generative state.** The adapter has no notion of compiled
  prompts, response_format, max_tokens, or temperature — the
  encoder-classifier backend has no generative side and intentionally
  ignores any such fields in ``input.constraints``.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from encoder_adapter import (  # type: ignore[import-not-found]
    CAT_CHILD_SAFETY,
    CAT_PRIVATE_DATA,
    CAT_SAFE,
    CAT_SCAM_FRAUD,
    CAT_SEXUAL_ADULT,
    EncoderAdapter,
)


# ---------------------------------------------------------------------------
# Model identity.
# ---------------------------------------------------------------------------
XLMR_MODEL_NAME = "XLM-R"

# Default location of the ONNX-exported encoder relative to the repo
# root. ``tools/export_xlmr_onnx.py`` produces this artefact.
DEFAULT_ONNX_MODEL_PATH = "models/xlmr.onnx"
# Optional INT4 (block-wise weight-only) variant of the same encoder.
# Produced by ``tools/export_xlmr_onnx.py --quantize-int4`` and
# preferred on storage-constrained devices. ~50 MB on disk vs the
# ~107 MB INT8 default.
DEFAULT_ONNX_INT4_MODEL_PATH = "models/xlmr.int4.onnx"
# Default location of the SentencePiece tokenizer model. XLM-R uses
# SentencePiece BPE; the export script copies the tokenizer alongside
# the ONNX model.
DEFAULT_TOKENIZER_PATH = "models/xlmr.spm"

# XLM-R / fairseq tokenizer offset. SentencePiece piece ids are
# shifted by ``+1`` and the four special tokens occupy ids 0..3:
# ``<s>=0, <pad>=1, </s>=2, <unk>=3``. Pieces whose ``piece_to_id``
# returns 0 (i.e. SentencePiece's own ``<unk>``) map to 3 (the
# fairseq ``<unk>`` slot).
_FAIRSEQ_OFFSET = 1
_BOS_ID = 0  # <s>
_PAD_ID = 1  # <pad>
_EOS_ID = 2  # </s>
_UNK_ID = 3  # <unk>


# ---------------------------------------------------------------------------
# Category prototypes — short multilingual-friendly descriptions used
# for zero-shot classification via cosine similarity. Index = taxonomy
# category id (0..15). Order MUST match ``encoder_adapter.CAT_*`` constants.
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

    Used whenever the encoder cannot run (ONNX model missing,
    onnxruntime / sentencepiece import error, runtime exception during
    inference). Matches the privacy-first invariant: when in doubt,
    the runtime degrades to SAFE rather than to a permissive label.
    """
    return {
        "severity": 0,
        "category": CAT_SAFE,
        "confidence": 0.05,
        "actions": _zero_actions(),
        "reason_codes": [],
        "rationale_id": "xlmr_safe_fallback_v1",
    }


# ---------------------------------------------------------------------------
# Adapter.
# ---------------------------------------------------------------------------
@dataclass
class XLMRAdapter:
    """EncoderAdapter backed by the XLM-R encoder, loaded as ONNX.

    Parameters
    ----------
    model_path
        Local filesystem path to the ONNX-exported XLM-R encoder.
        Defaults to :data:`DEFAULT_ONNX_MODEL_PATH` (the INT8
        ``models/xlmr.onnx`` file). Callers running on
        storage-constrained devices may pass
        :data:`DEFAULT_ONNX_INT4_MODEL_PATH` to load the ~50 MB INT4
        variant produced by ``tools/export_xlmr_onnx.py
        --quantize-int4``. See also ``prefer_int4`` for an
        auto-resolving alternative. The adapter only loads the model
        on first :meth:`classify` call so constructing one is cheap.
    tokenizer_path
        Local filesystem path to the SentencePiece tokenizer model
        (``.spm`` / ``.model`` file). Defaults to
        :data:`DEFAULT_TOKENIZER_PATH`.
    prefer_int4
        When True and ``model_path`` is left at the INT8 default,
        the adapter prefers the INT4 file
        (:data:`DEFAULT_ONNX_INT4_MODEL_PATH`) if it exists on disk.
        If the INT4 file is missing, the adapter falls back to the
        INT8 default. Explicit ``model_path`` arguments are honoured
        verbatim regardless of this flag — pass an explicit path
        when you want to disable the auto-resolve. Useful as an
        on-device storage-tier hint without forcing the caller to
        probe the filesystem.
    max_seq_length
        Sequence length used by the tokenizer. The XLM-R encoder
        supports up to 512 tokens; 128 is plenty for short chat
        messages and ~3-4× faster per call.
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
    head_weights_path
        Optional path to the trained ``Linear(384, 16)`` head stored
        as a numpy ``.npz`` archive (keys: ``weight``, ``bias``).
        Defaults to the conventional location next to this module:
        ``compiler/data/xlmr_head.npz``.
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

    model_path: str = DEFAULT_ONNX_MODEL_PATH
    tokenizer_path: str = DEFAULT_TOKENIZER_PATH
    max_seq_length: int = 128
    similarity_threshold: float = 0.30
    softmax_temperature: float = 0.01
    min_margin: float = 0.10
    head_weights_path: Optional[str] = None
    prefer_int4: bool = False
    logger: Optional[logging.Logger] = None
    last_latency_ms: float = field(default=0.0, init=False)
    _tokenizer: Any = field(default=None, init=False, repr=False)
    _session: Any = field(default=None, init=False, repr=False)
    _input_names: tuple[str, ...] = field(
        default=(), init=False, repr=False
    )
    _prototype_embeddings: Any = field(
        default=None, init=False, repr=False
    )
    _trained_head: Optional[tuple[Any, Any]] = field(
        default=None, init=False, repr=False
    )
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
                "XLM-R model load failed (%s); falling back to SAFE",
                exc,
            )
            return safe_fallback_output()

        if self._load_failed or self._session is None:
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
                "XLM-R inference error (%s); falling back to SAFE",
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
                "XLM-R classification error (%s); falling back to SAFE",
                exc,
            )
            return safe_fallback_output()

        # Attach the raw mean-pooled, L2-normalised embedding as an
        # internal extra (key prefixed with ``_`` to signal it is not
        # part of the public output schema). Downstream consumers
        # (e.g. ``chat-storage-search``) cache this 384-dim vector in
        # the ``search_vector`` table so a message's XLM-R embedding
        # is computed at most once across guardrail + search. The
        # field is preserved by ``_coerce_to_output_schema``.
        try:
            raw_output["_embedding"] = [float(x) for x in _to_list(embedding)]
        except Exception:  # noqa: BLE001 — never block on a malformed embedding
            pass

        self.last_latency_ms = (time.perf_counter() - start) * 1000.0
        return _coerce_to_output_schema(raw_output)

    # ------------------------------------------------------------------
    # Internals — model loading + encoding.
    # ------------------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if self._session is not None or self._load_failed:
            return
        try:
            import numpy as np  # noqa: F401  # type: ignore[import-not-found]
            import onnxruntime as ort  # type: ignore[import-not-found]
            import sentencepiece as spm  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001 — soft dependency
            self._load_failed = True
            (self.logger or _module_logger).warning(
                "onnxruntime/sentencepiece/numpy unavailable (%s); "
                "XLM-R adapter will return SAFE for every call",
                exc,
            )
            return

        if not Path(self.tokenizer_path).is_file():
            self._load_failed = True
            (self.logger or _module_logger).warning(
                "XLM-R tokenizer not found at %r; falling back to SAFE",
                self.tokenizer_path,
            )
            return

        # If ``prefer_int4`` is set and the caller is using the
        # default INT8 path, transparently swap in the INT4 file when
        # it exists on disk. Explicit ``model_path`` arguments are
        # honoured verbatim — callers that want a specific tier pass
        # the path directly.
        resolved_model_path = self.model_path
        if (
            self.prefer_int4
            and resolved_model_path == DEFAULT_ONNX_MODEL_PATH
            and Path(DEFAULT_ONNX_INT4_MODEL_PATH).is_file()
        ):
            (self.logger or _module_logger).info(
                "prefer_int4 hint set; loading %s instead of the INT8 default",
                DEFAULT_ONNX_INT4_MODEL_PATH,
            )
            resolved_model_path = DEFAULT_ONNX_INT4_MODEL_PATH
            self.model_path = resolved_model_path

        if not Path(resolved_model_path).is_file():
            self._load_failed = True
            (self.logger or _module_logger).warning(
                "XLM-R ONNX model not found at %r; falling back to SAFE",
                resolved_model_path,
            )
            return

        try:
            tokenizer = spm.SentencePieceProcessor()
            tokenizer.Load(self.tokenizer_path)
        except Exception as exc:  # noqa: BLE001 — defensive boundary
            self._load_failed = True
            (self.logger or _module_logger).warning(
                "Failed to load SentencePiece tokenizer from %r (%s); "
                "falling back to SAFE",
                self.tokenizer_path,
                exc,
            )
            return

        try:
            sess_options = ort.SessionOptions()
            sess_options.graph_optimization_level = (
                ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            )
            session = ort.InferenceSession(
                self.model_path,
                sess_options=sess_options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:  # noqa: BLE001 — model load failed
            self._load_failed = True
            (self.logger or _module_logger).warning(
                "Failed to load XLM-R ONNX model from %r (%s); falling "
                "back to SAFE",
                self.model_path,
                exc,
            )
            return

        self._tokenizer = tokenizer
        self._session = session
        self._input_names = tuple(i.name for i in session.get_inputs())

        # Pre-compute prototype embeddings once. They are kept as a
        # fallback even when a trained classification head is loaded
        # — if the head file goes missing or its shape doesn't match,
        # the adapter degrades gracefully to prototype argmax.
        try:
            self._prototype_embeddings = self._encode_batch(
                list(CATEGORY_PROTOTYPES)
            )
        except Exception as exc:  # noqa: BLE001 — defensive boundary
            self._load_failed = True
            (self.logger or _module_logger).warning(
                "Failed to encode XLM-R category prototypes (%s); "
                "falling back to SAFE",
                exc,
            )
            self._tokenizer = None
            self._session = None
            self._prototype_embeddings = None
            return

        # Optional: load the trained linear head produced by
        # ``train_xlmr_head.py``. When present, the adapter uses it
        # as the primary embedding-stage classifier instead of the
        # zero-shot prototype argmax.
        self._maybe_load_trained_head()

    def _maybe_load_trained_head(self) -> None:
        """Try to load the trained Linear(384, 16) head from disk.

        Looks at ``head_weights_path`` (if set) or the conventional
        ``compiler/data/xlmr_head.npz`` location next to this module.
        Sets ``self._trained_head`` to ``(weight, bias)`` numpy arrays
        on success, or leaves it as ``None`` to fall back to
        prototypes.
        """
        try:
            import numpy as np  # type: ignore[import-not-found]
        except Exception:  # noqa: BLE001 — soft dependency
            return

        candidate = self.head_weights_path
        if candidate is None:
            here = Path(__file__).resolve().parent
            default = here / "data" / "xlmr_head.npz"
            if default.exists():
                candidate = str(default)
        if candidate is None or not Path(candidate).exists():
            return

        try:
            with np.load(candidate) as npz:
                if "weight" not in npz.files or "bias" not in npz.files:
                    raise KeyError(
                        "xlmr_head.npz must contain 'weight' and 'bias' arrays"
                    )
                weight = np.asarray(npz["weight"], dtype=np.float32)
                bias = np.asarray(npz["bias"], dtype=np.float32)
        except Exception as exc:  # noqa: BLE001 — defensive boundary
            (self.logger or _module_logger).warning(
                "Failed to load trained head from %r (%s); falling back "
                "to prototype argmax",
                candidate,
                exc,
            )
            return

        if weight.shape != (16, 384) or bias.shape != (16,):
            (self.logger or _module_logger).warning(
                "Trained head shape %s/%s does not match (16, 384) / "
                "(16,); falling back to prototype argmax",
                tuple(weight.shape),
                tuple(bias.shape),
            )
            return

        self._trained_head = (weight, bias)
        (self.logger or _module_logger).info(
            "Loaded trained XLM-R head from %s", candidate
        )

    # ------------------------------------------------------------------
    # Tokenisation.
    # ------------------------------------------------------------------
    def _tokenize(
        self, texts: list[str]
    ) -> tuple[Any, Any]:
        """Tokenise ``texts`` into XLM-R-compatible input arrays.

        Returns ``(input_ids, attention_mask)`` as ``int64`` numpy
        arrays of shape ``(batch, seq_len)``. Each sequence starts
        with ``<s>`` (0) and ends with ``</s>`` (2); shorter sequences
        are right-padded with ``<pad>`` (1).
        """
        import numpy as np  # type: ignore[import-not-found]

        if self._tokenizer is None:
            raise RuntimeError("tokenizer not loaded")

        sp = self._tokenizer
        max_payload = max(2, self.max_seq_length - 2)  # leave room for <s>/</s>

        rows: list[list[int]] = []
        for text in texts:
            pieces: list[int] = sp.EncodeAsIds(text or "")
            # XLM-R / fairseq offset: shift SP ids by +1 except the
            # SP <unk> (id 0) which maps to fairseq <unk> (3).
            shifted: list[int] = [
                _UNK_ID if pid == 0 else pid + _FAIRSEQ_OFFSET
                for pid in pieces[:max_payload]
            ]
            rows.append([_BOS_ID, *shifted, _EOS_ID])

        seq_len = max(len(r) for r in rows) if rows else 2
        seq_len = min(seq_len, self.max_seq_length)

        input_ids = np.full(
            (len(rows), seq_len), fill_value=_PAD_ID, dtype=np.int64
        )
        attention_mask = np.zeros(
            (len(rows), seq_len), dtype=np.int64
        )
        for i, row in enumerate(rows):
            n = min(len(row), seq_len)
            input_ids[i, :n] = row[:n]
            attention_mask[i, :n] = 1

        return input_ids, attention_mask

    def _build_session_inputs(
        self, input_ids: Any, attention_mask: Any
    ) -> dict[str, Any]:
        """Map standard tokenizer outputs onto the ONNX session input names."""
        import numpy as np  # type: ignore[import-not-found]

        feed: dict[str, Any] = {}
        names = self._input_names or ("input_ids", "attention_mask")
        for name in names:
            if name == "input_ids":
                feed[name] = input_ids
            elif name == "attention_mask":
                feed[name] = attention_mask
            elif name == "token_type_ids":
                feed[name] = np.zeros_like(input_ids)
            else:
                # Unknown input — be conservative and zero-fill.
                feed[name] = np.zeros_like(input_ids)
        return feed

    def _encode_batch(self, texts: list[str]) -> Any:
        """Encode ``texts`` and return a ``(len(texts), hidden)`` numpy array.

        Uses mean-pooling across the sequence with attention masking,
        then L2-normalises each row so cosine similarity reduces to a
        plain dot product.
        """
        import numpy as np  # type: ignore[import-not-found]

        if self._tokenizer is None or self._session is None:
            raise RuntimeError("model not loaded")

        input_ids, attention_mask = self._tokenize(texts)
        feed = self._build_session_inputs(input_ids, attention_mask)
        outputs = self._session.run(None, feed)
        # Convention: first output is ``last_hidden_state``
        # ``(batch, seq, hidden)``.
        last_hidden = np.asarray(outputs[0], dtype=np.float32)
        mask = attention_mask.astype(np.float32)[..., None]
        summed = (last_hidden * mask).sum(axis=1)
        counts = mask.sum(axis=1)
        counts = np.maximum(counts, 1.0)
        pooled = summed / counts
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-12)
        return (pooled / norms).astype(np.float32)

    def _prototype_softmax(self, embedding: Any) -> list[float]:
        """Zero-shot prototype path: cosine + low-temperature softmax.

        Used either as the primary embedding-stage classifier when no
        trained head is available, or as a fallback when the trained
        head raises during inference.
        """
        sims = self._prototype_embeddings @ embedding
        sims_list = [float(s) for s in _to_list(sims)]
        temp = self.softmax_temperature if self.softmax_temperature > 0 else 1.0
        scaled = [s / temp for s in sims_list]
        max_scaled = max(scaled)
        exps = [math.exp(s - max_scaled) for s in scaled]
        z = sum(exps) or 1.0
        return [e / z for e in exps]

    def _encode(self, text: str) -> Any:
        """Encode a single ``text`` and return a 1D numpy array."""
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
        if self._prototype_embeddings is None:
            return safe_fallback_output()

        # --- Strong, deterministic overrides (mirror the deterministic
        # detector contract — these always trump the embedding head).
        lexicon_hits = signals.get("lexicon_hits") or []
        pii_hits = signals.get("pii_patterns_hit") or []
        scam_hits = signals.get("scam_patterns_hit") or []
        url_risk = float(signals.get("url_risk") or 0.0)
        media = signals.get("media_descriptors") or []
        context_hints = list(signals.get("context_hints") or [])

        # Helper: append protected-speech context hints to a reason
        # code list. The threshold policy uses these to demote the
        # verdict back to SAFE — see ``threshold_policy.py``.
        #
        # IMPORTANT: ``_with_context()`` is **only** applied on the
        # embedding-head branches below (zero-shot prototype path AND
        # trained-head path), where the encoder may have been confused
        # by surface tokens that look harmful but are protected speech
        # (e.g. a news quote about a violent attack). It is **not**
        # applied on the deterministic-signal branches (PII / SCAM /
        # LEXICON / NSFW) — those signals are concrete and must not
        # be silenced just because the message lives in a journalism /
        # education / counterspeech community. Letting protected-speech
        # demotion silence a phishing URL in a school group is exactly
        # the bug ``test_protected_speech_does_not_demote_*`` locks down.
        def _with_context(codes: list[str]) -> list[str]:
            merged = list(codes)
            for hint in context_hints:
                if hint not in merged:
                    merged.append(hint)
            return merged

        # CHILD_SAFETY — any lexicon hit on category 1 hits the
        # severity-5 floor regardless of embedding similarity *and*
        # regardless of any protected-speech context hints.
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

        # PRIVATE_DATA — any PII pattern. Deterministic, never demoted.
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

        # SCAM_FRAUD — high URL risk or scam patterns. Deterministic.
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

        # Lexicon-only hits — pick the highest-weight hit. Deterministic.
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

        # Media NSFW. Deterministic, never demoted.
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

        # --- Embedding head.
        # Two paths are supported:
        #
        #   1. **Trained head (preferred):** a ``Linear(384, 16)``
        #      fitted by ``train_xlmr_head.py`` on a small labelled
        #      corpus (see ``training_data.py``). Logits are
        #      normalised with a plain softmax — no temperature
        #      scaling needed because the linear layer learns its own
        #      scale during training.
        #   2. **Zero-shot prototypes (fallback):** cosine similarity
        #      against the fixed ``CATEGORY_PROTOTYPES`` embeddings,
        #      passed through a low-temperature softmax. XLM-R's
        #      multilingual embedding space is dense (raw cosine
        #      values typically cluster between 0.93 and 0.96), so the
        #      temperature amplifies relative differences.
        #
        # Both paths feed into the same argmax + top-1/top-2 margin
        # gate below, so the rest of the head (uncertainty handling,
        # protected-speech demotion downstream) is identical.
        if self._trained_head is not None:
            try:
                weight, bias = self._trained_head
                probs = _softmax(_to_list(weight @ embedding + bias))
            except Exception as exc:  # noqa: BLE001 — defensive boundary
                (self.logger or _module_logger).warning(
                    "Trained head inference failed (%s); falling back "
                    "to prototype argmax",
                    exc,
                )
                probs = self._prototype_softmax(embedding)
        else:
            probs = self._prototype_softmax(embedding)

        # Argmax + top-2 margin.
        ranked = sorted(
            range(len(probs)), key=lambda i: probs[i], reverse=True
        )
        best_idx = ranked[0]
        best_prob = probs[best_idx]
        runner_prob = probs[ranked[1]] if len(ranked) > 1 else 0.0
        margin = best_prob - runner_prob

        head_tag = "trained" if self._trained_head is not None else "proto"

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
                "reason_codes": _with_context([]),
                "rationale_id": f"xlmr_safe_{head_tag}_v1",
            }

        # Otherwise return the predicted category at default severity 2
        # (label only) — the threshold policy and severity rubric
        # downstream will promote / demote based on context. Protected-
        # speech context hints are forwarded so the threshold policy
        # can demote false positives like a news quote about violence.
        confidence = max(0.05, min(0.95, float(best_prob)))
        return {
            "severity": 2,
            "category": best_idx,
            "confidence": confidence,
            "actions": {**_zero_actions(), "label_only": True},
            "reason_codes": _with_context([]),
            "rationale_id": (
                f"xlmr_category_{best_idx}_{head_tag}_v1"
            ),
        }


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
_module_logger = logging.getLogger("kchat.guardrail.xlmr")


def _to_list(arr: Any) -> list[float]:
    """Convert a numpy / torch / list-like 1D tensor into a Python list."""
    if hasattr(arr, "tolist"):
        return list(arr.tolist())
    return list(arr)


def _softmax(values: list[float]) -> list[float]:
    if not values:
        return []
    max_v = max(values)
    exps = [math.exp(v - max_v) for v in values]
    z = sum(exps) or 1.0
    return [e / z for e in exps]


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
        rationale_id = "xlmr_default_rationale_v1"

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

    # Pass through internal underscore-prefixed extras (e.g. ``_embedding``
    # — the raw mean-pooled XLM-R embedding consumed by downstream
    # cross-pipeline caches such as ``chat-storage-search``). These
    # keys are not part of ``kchat.guardrail.output.v1`` proper; the
    # schema admits them via ``patternProperties: {"^_": {}}``.
    embedding_in = parsed.get("_embedding")
    if isinstance(embedding_in, list) and all(
        isinstance(x, (int, float)) and not isinstance(x, bool)
        for x in embedding_in
    ):
        out["_embedding"] = [float(x) for x in embedding_in]

    return out


# Make ``isinstance(adapter, EncoderAdapter)`` cheap for tests that import
# the protocol.
_PROTOCOL_REFERENCE: EncoderAdapter = XLMRAdapter()  # type: ignore[assignment]
del _PROTOCOL_REFERENCE


__all__ = [
    "CATEGORY_PROTOTYPES",
    "DEFAULT_ONNX_INT4_MODEL_PATH",
    "DEFAULT_ONNX_MODEL_PATH",
    "DEFAULT_TOKENIZER_PATH",
    "XLMR_MODEL_NAME",
    "XLMRAdapter",
    "safe_fallback_output",
]
