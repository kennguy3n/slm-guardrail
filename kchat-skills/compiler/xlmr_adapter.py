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
   and returns it. Out-of-range fields collapse to a degraded
   rule-only fallback.
6. Stashes the raw mean-pooled, L2-normalised XLM-R embedding on the
   adapter instance as :attr:`XLMRAdapter.last_embedding` (a 384-dim
   ``list[float]`` or ``None``). The embedding is **never** attached
   to the returned output dict — privacy contract rule 5 forbids
   embeddings, hashes, or commitments to message content in the
   public output schema. Cross-pipeline consumers that legitimately
   need the embedding (e.g. ``chat-storage-search``) read it from
   the adapter instance directly, never through the schema boundary.

Design constraints inherited from the EncoderAdapter contract:

* **Deterministic.** No sampling — argmax over fixed prototype
  embeddings (or the trained linear head). Identical input →
  identical output.
* **Offline.** Model weights are loaded from a local path
  (``model_path``). No network calls during inference. If weights
  are missing, the adapter falls back to a degraded rule-only mode
  (see :attr:`XLMRAdapter.health_state`).
* **Degraded-mode fallback (not silent SAFE).** If the ONNX session
  fails to load, raises an exception during inference, or produces
  an embedding outside the expected shape, the adapter returns a
  bare-shape output dict tagged with ``model_health="model_unavailable"``
  / ``"inference_error"`` and ``rationale_id="model_unavailable_rule_only_v1"``.
  The pipeline keeps the deterministic detectors active so PII,
  scam, URL-risk, child-safety lexicon, and NSFW media verdicts
  still fire — the encoder being unavailable must not be silently
  laundered into a confident SAFE verdict.
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

import hashlib
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
    HEALTH_TO_MODEL_HEALTH_OUTPUT,
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
#
# NOTE: ``WARN_WITH_CONTEXT`` is intentionally absent here even though
# it is a valid reason code in the output schema. The adapter never
# emits it — it is injected by :mod:`threshold_policy` AFTER the
# adapter has already returned, when a non-SAFE verdict is paired
# with a protected-speech context hint and the threshold policy
# decides to "warn with context" rather than fully demote. The
# canonical reason-code enum therefore lives in the output schema
# (``kchat-skills/global/output_schema.json``); these two whitelists
# (adapter-emitted vs schema-valid) intentionally do not coincide.
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


# ---------------------------------------------------------------------------
# Health state — P0-2.
#
# ``XLMRAdapter.health_state`` records why the encoder could not run
# on the most recent call so the pipeline can keep the deterministic
# detectors active and surface ``model_health`` to the UI rather than
# silently emitting a confident SAFE verdict.
# ---------------------------------------------------------------------------
HEALTH_HEALTHY = "healthy"
HEALTH_MODEL_UNAVAILABLE = "model_unavailable"
HEALTH_TOKENIZER_UNAVAILABLE = "tokenizer_unavailable"
HEALTH_DEPENDENCY_MISSING = "dependency_missing"
HEALTH_INFERENCE_ERROR = "inference_error"

VALID_HEALTH_STATES: frozenset[str] = frozenset(
    {
        HEALTH_HEALTHY,
        HEALTH_MODEL_UNAVAILABLE,
        HEALTH_TOKENIZER_UNAVAILABLE,
        HEALTH_DEPENDENCY_MISSING,
        HEALTH_INFERENCE_ERROR,
    }
)

# Mapping from internal ``health_state`` values to the coarser
# ``model_health`` enum exposed on the output schema. The schema only
# distinguishes the values the UI needs to reason about; richer
# states stay on the adapter instance for telemetry / debugging.
#
# Canonical table lives on :mod:`encoder_adapter` so the pipeline and
# every backend project internal states onto the output schema the
# same way. Re-exported here for back-compat with older imports.
_OUTPUT_MODEL_HEALTH: dict[str, str] = HEALTH_TO_MODEL_HEALTH_OUTPUT

# Rationale id used when the encoder could not run and the verdict is
# coming from the deterministic-detectors-only degraded path. The UI
# uses this to distinguish 'classifier confidently produced SAFE'
# from 'classifier could not run; only rules fired'.
DEGRADED_RATIONALE_ID = "model_unavailable_rule_only_v1"


def degraded_fallback_output(
    *,
    health_state: str = HEALTH_MODEL_UNAVAILABLE,
) -> dict[str, Any]:
    """Return a degraded ``kchat.guardrail.output.v1``-shaped dict.

    Emitted whenever the XLM-R encoder cannot produce a verdict
    (ONNX model missing, onnxruntime / sentencepiece import error,
    runtime exception during inference). Unlike a bare SAFE output,
    the dict is explicitly tagged with the schema-level
    ``model_health`` enum and a distinct ``rationale_id`` so the
    pipeline can keep deterministic detectors active and the calling
    UI can distinguish 'safe message' from 'model could not run'.

    The privacy contract requires the encoder to fail closed, not
    open — the output category is still SAFE so the pipeline never
    invents a harm label out of thin air, but every consumer can see
    that the verdict came from the fallback path.
    """
    if health_state not in VALID_HEALTH_STATES:
        health_state = HEALTH_MODEL_UNAVAILABLE
    return {
        "severity": 0,
        "category": CAT_SAFE,
        "confidence": 0.05,
        "actions": _zero_actions(),
        "reason_codes": [],
        "rationale_id": DEGRADED_RATIONALE_ID,
        "model_health": _OUTPUT_MODEL_HEALTH.get(
            health_state, "model_unavailable"
        ),
    }


# Backwards-compatibility shim — a number of older callers (tests,
# benchmarks, demo scripts) still import ``safe_fallback_output``. The
# function now returns the degraded fallback shape; the new name is
# preferred for clarity.
def safe_fallback_output() -> dict[str, Any]:
    """Deprecated alias for :func:`degraded_fallback_output`.

    Retained so existing callers keep working. Prefer
    :func:`degraded_fallback_output` in new code; it accepts an
    explicit ``health_state`` argument.
    """
    return degraded_fallback_output()


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
    # P0-4: optional SHA-256 hex digests of the ONNX model file and the
    # SentencePiece tokenizer file. When supplied, the adapter computes
    # the on-disk digests at load time and refuses to load any artefact
    # whose digest does not match (``_load_failed`` is set and the
    # adapter degrades to the rule-only mode). ``None`` skips the
    # check and only records ``loaded_*_checksum`` for attestation.
    expected_model_checksum: Optional[str] = None
    expected_tokenizer_checksum: Optional[str] = None
    logger: Optional[logging.Logger] = None
    last_latency_ms: float = field(default=0.0, init=False)
    # P0-1: the raw mean-pooled, L2-normalised XLM-R embedding from the
    # most recent ``classify()`` call. The privacy contract forbids
    # embeddings on the public output dict, so cross-pipeline consumers
    # that legitimately need the vector (notably ``chat-storage-search``)
    # read it from this attribute instead of through the schema
    # boundary. ``None`` when the adapter has not yet run or the most
    # recent call took the degraded fallback path.
    last_embedding: Optional[list[float]] = field(
        default=None, init=False, repr=False
    )
    # P0-2: health state recorded after every load attempt and every
    # ``classify()`` call. ``healthy`` means the encoder produced the
    # verdict; everything else is a degraded path and the pipeline
    # keeps deterministic detectors active.
    health_state: str = field(default=HEALTH_HEALTHY, init=False)
    # P0-4: SHA-256 digests of the model / tokenizer files actually
    # loaded into the session. Populated on a successful load even
    # when the caller does not supply ``expected_*_checksum``, so
    # attestation / passport-verification flows can read them back.
    loaded_model_checksum: Optional[str] = field(default=None, init=False)
    loaded_tokenizer_checksum: Optional[str] = field(
        default=None, init=False
    )
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

        Returns :func:`degraded_fallback_output` whenever the encoder
        cannot be loaded, raises during inference, or produces output
        that cannot be coerced into the output schema. The returned
        dict carries a ``model_health`` field (one of
        ``healthy`` / ``model_unavailable`` / ``inference_error``) so
        the pipeline can keep deterministic detectors active even
        when the encoder fails.
        """
        log = self.logger or _module_logger
        start = time.perf_counter()
        # Reset transient per-call state. ``last_embedding`` is cleared
        # so a previous call's vector is never observable on the
        # current call's fallback path.
        self.last_embedding = None

        try:
            self._ensure_loaded()
        except Exception as exc:  # noqa: BLE001 — defensive boundary
            self.last_latency_ms = (time.perf_counter() - start) * 1000.0
            self.health_state = HEALTH_MODEL_UNAVAILABLE
            log.warning(
                "XLM-R model load failed (%s); falling back to degraded",
                exc,
            )
            return degraded_fallback_output(
                health_state=self.health_state
            )

        if self._load_failed or self._session is None:
            self.last_latency_ms = (time.perf_counter() - start) * 1000.0
            # ``_ensure_loaded`` records the precise reason in
            # ``self.health_state``; preserve it but ensure we never
            # report ``healthy`` when the session is gone.
            if self.health_state == HEALTH_HEALTHY:
                self.health_state = HEALTH_MODEL_UNAVAILABLE
            return degraded_fallback_output(
                health_state=self.health_state
            )

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
            self.health_state = HEALTH_INFERENCE_ERROR
            log.warning(
                "XLM-R inference error (%s); falling back to degraded",
                exc,
            )
            return degraded_fallback_output(
                health_state=self.health_state
            )

        try:
            raw_output = self._classify_from_embedding_and_signals(
                embedding, local_signals
            )
        except Exception as exc:  # noqa: BLE001 — defensive boundary
            self.last_latency_ms = (time.perf_counter() - start) * 1000.0
            self.health_state = HEALTH_INFERENCE_ERROR
            log.warning(
                "XLM-R classification error (%s); falling back to degraded",
                exc,
            )
            return degraded_fallback_output(
                health_state=self.health_state
            )

        # P0-1: Stash the raw mean-pooled, L2-normalised embedding on
        # the adapter instance for cross-pipeline consumers (e.g.
        # ``chat-storage-search``). Privacy rule 5 forbids attaching
        # the embedding to the output dict — readers fetch it from
        # ``adapter.last_embedding`` directly so it never crosses the
        # schema boundary.
        try:
            self.last_embedding = [float(x) for x in _to_list(embedding)]
        except Exception:  # noqa: BLE001 — never block on a malformed embedding
            self.last_embedding = None

        self.last_latency_ms = (time.perf_counter() - start) * 1000.0
        self.health_state = HEALTH_HEALTHY
        raw_output["model_health"] = _OUTPUT_MODEL_HEALTH[HEALTH_HEALTHY]
        coerced = _coerce_to_output_schema(raw_output)
        # Keep adapter telemetry in sync with what the caller actually
        # gets. If _coerce_to_output_schema downgraded the response to
        # the inference-error fallback, reflect that on self.health_state
        # so the next .last_latency_ms / health probe doesn't claim
        # "healthy" while we just shipped a degraded output.
        if coerced.get("model_health") != _OUTPUT_MODEL_HEALTH[HEALTH_HEALTHY]:
            self.health_state = HEALTH_INFERENCE_ERROR
        return coerced

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
            self.health_state = HEALTH_DEPENDENCY_MISSING
            (self.logger or _module_logger).warning(
                "onnxruntime/sentencepiece/numpy unavailable (%s); "
                "XLM-R adapter will degrade to rule-only for every call",
                exc,
            )
            return

        if not Path(self.tokenizer_path).is_file():
            self._load_failed = True
            self.health_state = HEALTH_TOKENIZER_UNAVAILABLE
            (self.logger or _module_logger).warning(
                "XLM-R tokenizer not found at %r; degrading to rule-only",
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
            self.health_state = HEALTH_MODEL_UNAVAILABLE
            (self.logger or _module_logger).warning(
                "XLM-R ONNX model not found at %r; degrading to rule-only",
                resolved_model_path,
            )
            return

        # P0-4: compute on-disk SHA-256 digests for both artefacts so
        # callers can attest 'what model is actually running on this
        # device?' through ``loaded_model_checksum`` /
        # ``loaded_tokenizer_checksum``. If the caller supplied an
        # expected digest, refuse to load any artefact whose digest
        # does not match — the adapter must not silently load a
        # tampered model.
        try:
            model_digest = _sha256_file(resolved_model_path)
            tokenizer_digest = _sha256_file(self.tokenizer_path)
        except Exception as exc:  # noqa: BLE001 — defensive boundary
            self._load_failed = True
            self.health_state = HEALTH_MODEL_UNAVAILABLE
            (self.logger or _module_logger).warning(
                "Failed to hash XLM-R artefacts (%s); degrading to rule-only",
                exc,
            )
            return
        if (
            self.expected_model_checksum is not None
            and not _checksums_match(
                self.expected_model_checksum, model_digest
            )
        ):
            self._load_failed = True
            self.health_state = HEALTH_MODEL_UNAVAILABLE
            (self.logger or _module_logger).warning(
                "XLM-R model checksum mismatch (expected %r, got %r); "
                "refusing to load. Degrading to rule-only.",
                self.expected_model_checksum,
                model_digest,
            )
            return
        if (
            self.expected_tokenizer_checksum is not None
            and not _checksums_match(
                self.expected_tokenizer_checksum, tokenizer_digest
            )
        ):
            self._load_failed = True
            self.health_state = HEALTH_TOKENIZER_UNAVAILABLE
            (self.logger or _module_logger).warning(
                "XLM-R tokenizer checksum mismatch (expected %r, got %r); "
                "refusing to load. Degrading to rule-only.",
                self.expected_tokenizer_checksum,
                tokenizer_digest,
            )
            return
        self.loaded_model_checksum = model_digest
        self.loaded_tokenizer_checksum = tokenizer_digest

        try:
            tokenizer = spm.SentencePieceProcessor()
            tokenizer.Load(self.tokenizer_path)
        except Exception as exc:  # noqa: BLE001 — defensive boundary
            self._load_failed = True
            self.health_state = HEALTH_TOKENIZER_UNAVAILABLE
            (self.logger or _module_logger).warning(
                "Failed to load SentencePiece tokenizer from %r (%s); "
                "degrading to rule-only",
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
            self.health_state = HEALTH_MODEL_UNAVAILABLE
            (self.logger or _module_logger).warning(
                "Failed to load XLM-R ONNX model from %r (%s); degrading "
                "to rule-only",
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
            self.health_state = HEALTH_INFERENCE_ERROR
            (self.logger or _module_logger).warning(
                "Failed to encode XLM-R category prototypes (%s); "
                "degrading to rule-only",
                exc,
            )
            self._tokenizer = None
            self._session = None
            self._prototype_embeddings = None
            return

        # Successful end-to-end load — record health and continue with
        # optional trained-head loading. The trained head is optional;
        # its absence is not a degradation.
        self.health_state = HEALTH_HEALTHY

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


def _sha256_file(path: str, *, chunk_size: int = 1 << 20) -> str:
    """Return the SHA-256 hex digest of the file at ``path``.

    Streams the file in 1 MiB chunks so the model artefact does not
    have to fit in memory. Raises if the path does not exist or is
    unreadable (callers treat the exception as a load failure and
    degrade to rule-only).
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _checksums_match(expected: str, actual: str) -> bool:
    """Case-insensitive, whitespace-insensitive hex-digest comparison."""
    if not isinstance(expected, str) or not isinstance(actual, str):
        return False
    return expected.strip().lower() == actual.strip().lower()


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
    be repaired collapses to a degraded fallback tagged with
    ``model_health = inference_error``: the encoder ran, but produced
    something the schema validator can't accept, which is
    operationally indistinguishable from an inference failure as far
    as the calling UI is concerned. This is **not** the same shape as
    ``HEALTH_MODEL_UNAVAILABLE`` (model couldn't load at all) — the
    distinction matters because the rule-only path stays in play in
    both cases, but only the latter means "the encoder weights aren't
    on disk".
    """
    inference_error = lambda: degraded_fallback_output(  # noqa: E731
        health_state=HEALTH_INFERENCE_ERROR
    )
    try:
        category = int(parsed.get("category", CAT_SAFE))
    except (TypeError, ValueError):
        return inference_error()
    if not 0 <= category <= 15:
        return inference_error()

    try:
        severity = int(parsed.get("severity", 0))
    except (TypeError, ValueError):
        return inference_error()
    if not 0 <= severity <= 5:
        return inference_error()

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        return inference_error()
    if not 0.0 <= confidence <= 1.0:
        return inference_error()

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

    # Pass through the safety-relevant ``model_health`` status signal
    # so downstream consumers (pipeline + UI) can distinguish a
    # confident SAFE verdict from a degraded fallback. Any value not
    # in the schema enum is dropped silently rather than crashing.
    health = parsed.get("model_health")
    if isinstance(health, str) and health in {
        "healthy",
        "model_unavailable",
        "inference_error",
    }:
        out["model_health"] = health

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
    "DEGRADED_RATIONALE_ID",
    "HEALTH_DEPENDENCY_MISSING",
    "HEALTH_HEALTHY",
    "HEALTH_INFERENCE_ERROR",
    "HEALTH_MODEL_UNAVAILABLE",
    "HEALTH_TOKENIZER_UNAVAILABLE",
    "VALID_HEALTH_STATES",
    "XLMR_MODEL_NAME",
    "XLMRAdapter",
    "degraded_fallback_output",
    "safe_fallback_output",
]
