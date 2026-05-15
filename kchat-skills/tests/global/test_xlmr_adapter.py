"""Tests for ``XLMRAdapter``.

Module under test: ``kchat-skills/compiler/xlmr_adapter.py``.
The adapter is **not** unit-tested against a real exported encoder
in CI — that would require shipping the ONNX model. Instead we:

* Verify Protocol conformance.
* Verify the SAFE fallback path when the encoder cannot be loaded
  (the default, since the test environment doesn't have an ONNX
  artefact on disk).
* Verify the classification head's behaviour on top of a stub
  encoder (deterministic numpy embeddings injected directly).
* Verify schema conformance, determinism, and signal-priority
  ordering against ``output_schema.json``.

See ``tools/run_guardrail_demo.py`` for the full end-to-end exercise
that does require a locally-exported ``models/xlmr.onnx`` checkpoint.
"""
from __future__ import annotations

from typing import Any

import jsonschema
import pytest

from encoder_adapter import (  # type: ignore[import-not-found]
    CAT_SAFE,
    EncoderAdapter,
)
from xlmr_adapter import (  # type: ignore[import-not-found]
    CATEGORY_PROTOTYPES,
    DEFAULT_ONNX_INT4_MODEL_PATH,
    DEFAULT_ONNX_MODEL_PATH,
    DEFAULT_TOKENIZER_PATH,
    HEALTH_INFERENCE_ERROR,
    XLMR_MODEL_NAME,
    XLMRAdapter,
    _coerce_to_output_schema,
    degraded_fallback_output,
    safe_fallback_output,
)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _input(text: str = "hi", **signals: Any) -> dict[str, Any]:
    local_signals = {
        "url_risk": 0.0,
        "pii_patterns_hit": [],
        "scam_patterns_hit": [],
        "lexicon_hits": [],
        "media_descriptors": [],
        "context_hints": [],
    }
    local_signals.update(signals)
    return {
        "message": {
            "text": text,
            "lang_hint": "en",
            "has_attachment": False,
            "attachment_kinds": [],
            "quoted_from_user": False,
            "is_outbound": False,
        },
        "context": {
            "group_kind": "small_group",
            "group_age_mode": "mixed_age",
            "user_role": "member",
            "relationship_known": True,
            "locale": "en-US",
            "jurisdiction_id": None,
            "community_overlay_id": None,
            "is_offline": False,
        },
        "local_signals": local_signals,
        "constraints": {
            "max_output_tokens": 600,
            "temperature": 0.0,
            "output_format": "json",
            "schema_id": "kchat.guardrail.output.v1",
        },
    }


def _stub_loaded_adapter(monkeypatch: pytest.MonkeyPatch, *, hidden: int = 8):
    """Return an adapter with a deterministic stub encoder pre-loaded.

    The stub's prototype embeddings are a one-hot identity matrix
    (16 × hidden), and ``_encode`` returns a one-hot vector keyed off
    the input text so the cosine-similarity argmax is fully
    determined by the input. This bypasses onnxruntime / sentencepiece
    loading entirely so the test doesn't require a real ONNX export.
    """
    np = pytest.importorskip("numpy")

    adapter = XLMRAdapter()

    n_categories = len(CATEGORY_PROTOTYPES)
    width = max(hidden, n_categories)
    eye = np.eye(n_categories, width, dtype=np.float32)

    def fake_ensure_loaded(self: XLMRAdapter) -> None:
        self._tokenizer = object()
        self._session = object()
        self._prototype_embeddings = eye

    def fake_encode(self: XLMRAdapter, text: str) -> Any:
        # Map a few keywords → category indices for deterministic
        # tests; everything else maps to SAFE.
        keyword_map = {
            "scam": 7,
            "phish": 7,
            "hate": 6,
            "porn": 10,
            "weapon": 11,
        }
        idx = CAT_SAFE
        lowered = (text or "").lower()
        for kw, cat in keyword_map.items():
            if kw in lowered:
                idx = cat
                break
        vec = np.zeros(width, dtype=np.float32)
        vec[idx] = 1.0
        return vec

    monkeypatch.setattr(
        XLMRAdapter, "_ensure_loaded", fake_ensure_loaded
    )
    monkeypatch.setattr(XLMRAdapter, "_encode", fake_encode)
    return adapter


# ---------------------------------------------------------------------------
# Protocol conformance.
# ---------------------------------------------------------------------------
def test_xlmr_adapter_satisfies_protocol():
    adapter = XLMRAdapter()
    assert isinstance(adapter, EncoderAdapter)


def test_xlmr_adapter_has_classify_method():
    adapter = XLMRAdapter()
    assert callable(getattr(adapter, "classify", None))


def test_health_to_model_health_output_is_canonical():
    """Pipeline and XLM-R adapter MUST agree on how internal
    ``health_state`` values project onto the output schema's
    ``model_health`` enum. The canonical table lives on
    :mod:`encoder_adapter`; both the adapter and the pipeline must
    import the SAME object, not their own copies.
    """
    from encoder_adapter import (  # type: ignore[import-not-found]
        HEALTH_TO_MODEL_HEALTH_OUTPUT as canonical,
    )
    from pipeline import (  # type: ignore[import-not-found]
        _ADAPTER_HEALTH_TO_OUTPUT as pipeline_view,
    )
    from xlmr_adapter import (  # type: ignore[import-not-found]
        _OUTPUT_MODEL_HEALTH as adapter_view,
    )
    assert pipeline_view is canonical
    assert adapter_view is canonical
    # Every value is part of the output_schema model_health enum.
    allowed = {"healthy", "model_unavailable", "inference_error"}
    assert set(canonical.values()) <= allowed


def test_default_constants_advertise_xlmr():
    assert XLMR_MODEL_NAME == "XLM-R"
    assert DEFAULT_ONNX_MODEL_PATH.endswith(".onnx")
    assert DEFAULT_TOKENIZER_PATH.endswith(".spm")
    assert len(CATEGORY_PROTOTYPES) == 16


# ---------------------------------------------------------------------------
# Fallback when model unavailable.
# ---------------------------------------------------------------------------
def test_unavailable_model_returns_safe(output_schema):
    adapter = XLMRAdapter(
        model_path="/does/not/exist.onnx",
        tokenizer_path="/does/not/exist.spm",
    )
    out = adapter.classify(_input())
    jsonschema.validate(instance=out, schema=output_schema)
    assert out == safe_fallback_output()
    assert out["category"] == 0
    assert out["severity"] == 0


def test_unavailable_model_records_latency():
    adapter = XLMRAdapter(
        model_path="/does/not/exist.onnx",
        tokenizer_path="/does/not/exist.spm",
    )
    adapter.classify(_input())
    assert adapter.last_latency_ms >= 0.0


def test_unavailable_model_is_deterministic_safe():
    adapter = XLMRAdapter(
        model_path="/does/not/exist.onnx",
        tokenizer_path="/does/not/exist.spm",
    )
    a = adapter.classify(_input("anything"))
    b = adapter.classify(_input("anything"))
    assert a == b


def test_safe_fallback_output_schema(output_schema):
    jsonschema.validate(instance=safe_fallback_output(), schema=output_schema)


# ---------------------------------------------------------------------------
# Coerce-to-output-schema behaviour (mirrors the old adapter contract).
# ---------------------------------------------------------------------------
def test_coerce_drops_invalid_reason_codes(output_schema):
    raw = {
        "severity": 2,
        "category": 7,
        "confidence": 0.7,
        "actions": {
            "label_only": True,
            "warn": False,
            "strong_warn": False,
            "critical_intervention": False,
            "suggest_redact": False,
        },
        "reason_codes": ["URL_RISK", "NOT_REAL", "URL_RISK"],
        "rationale_id": "scam_link_v1",
    }
    out = _coerce_to_output_schema(raw)
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["reason_codes"] == ["URL_RISK"]


def test_coerce_out_of_range_category_collapses_to_inference_error():
    raw = {
        "severity": 0,
        "category": 99,
        "confidence": 0.5,
        "actions": {
            "label_only": False,
            "warn": False,
            "strong_warn": False,
            "critical_intervention": False,
            "suggest_redact": False,
        },
        "reason_codes": [],
        "rationale_id": "x",
    }
    out = _coerce_to_output_schema(raw)
    # The encoder produced an out-of-range category, which is
    # operationally a *bad inference* — not a missing model. The
    # fallback must carry model_health="inference_error", not
    # "model_unavailable".
    assert out == degraded_fallback_output(
        health_state=HEALTH_INFERENCE_ERROR
    )
    assert out["model_health"] == "inference_error"


def test_coerce_out_of_range_severity_collapses_to_inference_error():
    raw = {"severity": 99, "category": 0, "confidence": 0.5}
    out = _coerce_to_output_schema(raw)
    assert out == degraded_fallback_output(
        health_state=HEALTH_INFERENCE_ERROR
    )
    assert out["model_health"] == "inference_error"


def test_coerce_out_of_range_confidence_collapses_to_inference_error():
    raw = {"severity": 0, "category": 0, "confidence": 2.5}
    out = _coerce_to_output_schema(raw)
    assert out == degraded_fallback_output(
        health_state=HEALTH_INFERENCE_ERROR
    )
    assert out["model_health"] == "inference_error"


def test_safe_fallback_output_is_model_unavailable_shape():
    # Backwards-compat shim must STILL return the
    # model_unavailable shape (its historical default) so existing
    # callers that explicitly want "no encoder ran" semantics keep
    # working unchanged.
    out = safe_fallback_output()
    assert out["model_health"] == "model_unavailable"
    assert out["rationale_id"] == "model_unavailable_rule_only_v1"


def test_coerce_keeps_well_formed_counter_updates():
    raw = {
        "severity": 2,
        "category": 5,
        "confidence": 0.7,
        "actions": {},
        "reason_codes": [],
        "rationale_id": "harassment_v1",
        "counter_updates": [
            {"counter_id": "harassment_30d", "delta": 1},
            {"counter_id": "", "delta": 1},  # dropped
            {"counter_id": "ok", "delta": "5"},  # dropped
        ],
    }
    out = _coerce_to_output_schema(raw)
    assert out["counter_updates"] == [
        {"counter_id": "harassment_30d", "delta": 1}
    ]


# ---------------------------------------------------------------------------
# Classification head — stubbed encoder.
# ---------------------------------------------------------------------------
def test_stub_encoder_classifies_safe_text(
    monkeypatch, output_schema
):
    pytest.importorskip("numpy")
    adapter = _stub_loaded_adapter(monkeypatch)
    out = adapter.classify(_input("hello friends"))
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == CAT_SAFE
    assert out["severity"] == 0


def test_stub_encoder_classifies_scam_text(
    monkeypatch, output_schema
):
    pytest.importorskip("numpy")
    adapter = _stub_loaded_adapter(monkeypatch)
    out = adapter.classify(_input("this is a scam"))
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 7  # SCAM_FRAUD
    assert 0.0 <= out["confidence"] <= 1.0


def test_stub_encoder_is_deterministic(monkeypatch):
    pytest.importorskip("numpy")
    adapter = _stub_loaded_adapter(monkeypatch)
    a = adapter.classify(_input("scam alert"))
    b = adapter.classify(_input("scam alert"))
    assert a == b


def test_stub_encoder_records_latency(monkeypatch):
    pytest.importorskip("numpy")
    adapter = _stub_loaded_adapter(monkeypatch)
    adapter.classify(_input("hello"))
    assert adapter.last_latency_ms >= 0.0


# ---------------------------------------------------------------------------
# Signal overrides take precedence over embedding head.
# ---------------------------------------------------------------------------
def test_child_safety_lexicon_overrides_embedding(
    monkeypatch, output_schema
):
    pytest.importorskip("numpy")
    adapter = _stub_loaded_adapter(monkeypatch)
    out = adapter.classify(
        _input(
            "scam scam scam",  # would otherwise hit category 7
            lexicon_hits=[
                {"lexicon_id": "cs_v1", "category": 1, "weight": 0.9}
            ],
        )
    )
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 1  # CHILD_SAFETY
    assert out["severity"] == 5  # child-safety floor
    assert out["actions"]["critical_intervention"] is True
    assert "CHILD_SAFETY_FLOOR" in out["reason_codes"]


def test_pii_signal_overrides_embedding(monkeypatch, output_schema):
    pytest.importorskip("numpy")
    adapter = _stub_loaded_adapter(monkeypatch)
    out = adapter.classify(
        _input("scam phish", pii_patterns_hit=["EMAIL", "PHONE"])
    )
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 9  # PRIVATE_DATA
    assert out["actions"]["suggest_redact"] is True


def test_url_risk_signal_overrides_embedding(monkeypatch, output_schema):
    pytest.importorskip("numpy")
    adapter = _stub_loaded_adapter(monkeypatch)
    out = adapter.classify(_input("hello", url_risk=0.95))
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 7  # SCAM_FRAUD
    assert "URL_RISK" in out["reason_codes"]


def test_scam_pattern_signal_overrides_embedding(
    monkeypatch, output_schema
):
    pytest.importorskip("numpy")
    adapter = _stub_loaded_adapter(monkeypatch)
    out = adapter.classify(
        _input("hello", scam_patterns_hit=["ADVANCE_FEE"])
    )
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 7  # SCAM_FRAUD
    assert "SCAM_PATTERN" in out["reason_codes"]


def test_lexicon_hate_signal_drives_category(monkeypatch, output_schema):
    pytest.importorskip("numpy")
    adapter = _stub_loaded_adapter(monkeypatch)
    out = adapter.classify(
        _input(
            "hello",
            lexicon_hits=[
                {"lexicon_id": "hate_v1", "category": 6, "weight": 0.8}
            ],
        )
    )
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 6  # HATE
    assert "LEXICON_HIT" in out["reason_codes"]


def test_media_nsfw_signal_drives_sexual_adult(
    monkeypatch, output_schema
):
    pytest.importorskip("numpy")
    adapter = _stub_loaded_adapter(monkeypatch)
    out = adapter.classify(
        _input(
            "hello",
            media_descriptors=[{"kind": "image", "nsfw_score": 0.85}],
        )
    )
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 10  # SEXUAL_ADULT


# ---------------------------------------------------------------------------
# Output range invariants.
# ---------------------------------------------------------------------------
def test_categories_always_in_range(monkeypatch, output_schema):
    pytest.importorskip("numpy")
    adapter = _stub_loaded_adapter(monkeypatch)
    for text in ["hello", "scam", "phish", "hate", "porn", "weapon", ""]:
        out = adapter.classify(_input(text))
        jsonschema.validate(instance=out, schema=output_schema)
        assert 0 <= out["category"] <= 15
        assert 0 <= out["severity"] <= 5
        assert 0.0 <= out["confidence"] <= 1.0


def test_safe_fallback_categories_in_range():
    out = safe_fallback_output()
    assert 0 <= out["category"] <= 15
    assert 0 <= out["severity"] <= 5
    assert 0.0 <= out["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# Trained linear head loading + classification.
# ---------------------------------------------------------------------------
def _make_synthetic_head_weights(tmp_path, target_category: int):
    """Write a synthetic Linear(384, 16) ``.npz`` archive that maps any
    embedding to ``target_category`` with high confidence.

    The weight matrix is a rank-1 tensor whose target row is filled
    with 1s and every other row is filled with -1s, so for any input
    embedding the target logit dominates.
    """
    import numpy as np  # type: ignore[import-not-found]

    weight = -np.ones((16, 384), dtype=np.float32)
    weight[target_category] = np.ones(384, dtype=np.float32)
    bias = np.zeros(16, dtype=np.float32)
    path = tmp_path / "synthetic_head.npz"
    np.savez(path, weight=weight, bias=bias)
    return path


def test_trained_head_loads_and_drives_classification(
    monkeypatch, tmp_path, output_schema
):
    """When a trained head file is present, the adapter uses it as
    the primary embedding-stage classifier and tags the rationale_id
    with ``_trained``."""
    np = pytest.importorskip("numpy")
    head_path = _make_synthetic_head_weights(
        tmp_path, target_category=7  # SCAM_FRAUD
    )
    adapter = XLMRAdapter(head_weights_path=str(head_path))
    adapter._maybe_load_trained_head()
    assert adapter._trained_head is not None

    # Plug in the same stub encoder used by the prototype tests so we
    # can drive classify() without real model weights.
    width = 384
    n = len(CATEGORY_PROTOTYPES)
    eye = np.zeros((n, width), dtype=np.float32)
    for i in range(n):
        eye[i, i] = 1.0

    def fake_ensure_loaded(self) -> None:
        self._tokenizer = object()
        self._session = object()
        self._prototype_embeddings = eye
        # Don't reset _trained_head — it's set above.

    def fake_encode(self, text: str):
        v = np.zeros(width, dtype=np.float32)
        v[0] = 1.0
        return v

    monkeypatch.setattr(
        XLMRAdapter, "_ensure_loaded", fake_ensure_loaded
    )
    monkeypatch.setattr(XLMRAdapter, "_encode", fake_encode)

    out = adapter.classify(_input("any text"))
    jsonschema.validate(instance=out, schema=output_schema)
    # Synthetic head pins prediction to category 7 regardless of input.
    assert out["category"] == 7
    assert "_trained" in out["rationale_id"]


def test_trained_head_missing_falls_back_to_prototypes(
    monkeypatch, tmp_path, output_schema
):
    """When the trained head file is absent, the adapter must still
    classify via the prototype path and tag rationale with ``_proto``."""
    pytest.importorskip("numpy")
    adapter = XLMRAdapter(
        head_weights_path=str(tmp_path / "does_not_exist.npz")
    )
    adapter._maybe_load_trained_head()
    assert adapter._trained_head is None

    # Use the existing prototype-path stub.
    adapter = _stub_loaded_adapter(monkeypatch)
    # Sanity: prototype path tags rationale with ``_proto``.
    out = adapter.classify(_input("hello"))
    jsonschema.validate(instance=out, schema=output_schema)
    assert "_proto" in out["rationale_id"]


def test_trained_head_rejects_wrong_shape(tmp_path):
    """A malformed .npz (wrong-shape weight) must NOT load — the
    adapter should silently fall back to ``None`` so the prototype
    path takes over."""
    np = pytest.importorskip("numpy")

    path = tmp_path / "bad_head.npz"
    np.savez(
        path,
        weight=np.zeros((8, 64), dtype=np.float32),  # wrong shape
        bias=np.zeros(8, dtype=np.float32),
    )

    adapter = XLMRAdapter(head_weights_path=str(path))
    adapter._maybe_load_trained_head()
    assert adapter._trained_head is None


def test_trained_head_then_deterministic_signal_takes_precedence(
    monkeypatch, tmp_path, output_schema
):
    """Even with a trained head loaded, deterministic-signal branches
    (PII, SCAM, LEXICON, NSFW) win over the embedding-head argmax,
    and their reason codes are emitted *without* protected-speech
    context hints."""
    np = pytest.importorskip("numpy")
    head_path = _make_synthetic_head_weights(
        tmp_path, target_category=11  # would return WEAPONS_DRUGS
    )
    adapter = XLMRAdapter(head_weights_path=str(head_path))
    adapter._maybe_load_trained_head()

    eye = np.eye(16, 384, dtype=np.float32)

    def fake_ensure_loaded(self) -> None:
        self._tokenizer = object()
        self._session = object()
        self._prototype_embeddings = eye

    monkeypatch.setattr(
        XLMRAdapter, "_ensure_loaded", fake_ensure_loaded
    )
    monkeypatch.setattr(
        XLMRAdapter,
        "_encode",
        lambda self, text: np.eye(16, 384, dtype=np.float32)[0],
    )

    # PII-driven input under journalism context — the deterministic
    # branch must beat the trained head AND the reason codes must
    # NOT carry NEWS_CONTEXT.
    out = adapter.classify(
        _input(
            "leaked email",
            pii_patterns_hit=[{"kind": "email"}],
            context_hints=["NEWS_CONTEXT"],
        )
    )
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 9  # PRIVATE_DATA, not 11 from the head
    assert out["reason_codes"] == ["PRIVATE_DATA_PATTERN"]
    assert "NEWS_CONTEXT" not in out["reason_codes"]


# ---------------------------------------------------------------------------
# P0-1: embedding is stashed on the adapter instance, never on the output.
# ---------------------------------------------------------------------------
def test_classify_output_has_no_underscore_keys(monkeypatch, output_schema):
    """``XLMRAdapter.classify()`` MUST NOT attach any ``_``-prefixed
    extras to the returned dict. Privacy rule 5 forbids embeddings,
    hashes, or any other commitment to message content on the public
    output boundary."""
    pytest.importorskip("numpy")
    adapter = _stub_loaded_adapter(monkeypatch, hidden=384)
    out = adapter.classify(_input("hello"))
    jsonschema.validate(instance=out, schema=output_schema)

    leaked = [k for k in out if isinstance(k, str) and k.startswith("_")]
    assert not leaked, (
        f"XLMRAdapter leaked underscore-prefixed keys on output: {leaked}"
    )
    assert "_embedding" not in out


def test_classify_stashes_embedding_on_adapter(monkeypatch):
    """The raw mean-pooled embedding is exposed on the *adapter*
    instance as ``last_embedding`` for cross-pipeline consumers
    (e.g. ``chat-storage-search``). It never crosses the schema
    boundary."""
    pytest.importorskip("numpy")
    adapter = _stub_loaded_adapter(monkeypatch, hidden=384)
    out = adapter.classify(_input("hello"))
    assert "_embedding" not in out
    assert adapter.last_embedding is not None
    assert isinstance(adapter.last_embedding, list)
    assert len(adapter.last_embedding) == 384
    assert all(isinstance(x, float) for x in adapter.last_embedding), (
        "last_embedding values must be plain Python floats so the "
        "vector is JSON-serialisable across the FFI boundary"
    )


def test_signal_branch_still_stashes_embedding(monkeypatch, output_schema):
    """Even when a deterministic-signal branch (PII / SCAM / etc.)
    overrides the embedding-head argmax, the raw embedding is still
    stashed on the adapter so the search cache stays consistent."""
    pytest.importorskip("numpy")
    adapter = _stub_loaded_adapter(monkeypatch, hidden=384)
    out = adapter.classify(_input("hello", pii_patterns_hit=["EMAIL"]))
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 9  # PRIVATE_DATA wins over the embedding head
    assert "_embedding" not in out
    assert adapter.last_embedding is not None
    assert len(adapter.last_embedding) == 384


def test_embedding_is_deterministic(monkeypatch):
    """Identical input → identical embedding (matches the EncoderAdapter
    Protocol's determinism contract)."""
    pytest.importorskip("numpy")
    adapter_a = _stub_loaded_adapter(monkeypatch, hidden=384)
    adapter_a.classify(_input("scam alert"))
    first = list(adapter_a.last_embedding or [])

    adapter_b = _stub_loaded_adapter(monkeypatch, hidden=384)
    adapter_b.classify(_input("scam alert"))
    second = list(adapter_b.last_embedding or [])
    assert first == second


def test_degraded_fallback_clears_last_embedding():
    """When the encoder cannot run, the degraded fallback dict has no
    embedding extras and the adapter's ``last_embedding`` is cleared
    so a previous call's vector is never observable."""
    adapter = XLMRAdapter(
        model_path="/does/not/exist.onnx",
        tokenizer_path="/does/not/exist.spm",
    )
    out = adapter.classify(_input())
    assert "_embedding" not in out
    assert adapter.last_embedding is None
    # And the degraded mode is surfaced to the UI.
    assert out["model_health"] == "model_unavailable"
    assert out["rationale_id"] == "model_unavailable_rule_only_v1"


def test_coerce_strips_embedding(output_schema):
    """``_coerce_to_output_schema`` MUST drop any ``_embedding`` a
    misbehaving caller hands it. Privacy rule 5 forbids embeddings
    on the public output boundary."""
    raw = {
        "severity": 0,
        "category": 0,
        "confidence": 0.1,
        "actions": {
            "label_only": False,
            "warn": False,
            "strong_warn": False,
            "critical_intervention": False,
            "suggest_redact": False,
        },
        "reason_codes": [],
        "rationale_id": "xlmr_safe_proto_v1",
        "_embedding": [0.0] * 384,
    }
    out = _coerce_to_output_schema(raw)
    jsonschema.validate(instance=out, schema=output_schema)
    assert "_embedding" not in out


def test_coerce_drops_malformed_embedding(output_schema):
    """A malformed ``_embedding`` (non-list, non-numeric items) must
    silently drop, not crash. The rest of the dict is unaffected."""
    raw = {
        "severity": 0,
        "category": 0,
        "confidence": 0.1,
        "actions": {
            "label_only": False,
            "warn": False,
            "strong_warn": False,
            "critical_intervention": False,
            "suggest_redact": False,
        },
        "reason_codes": [],
        "rationale_id": "xlmr_safe_proto_v1",
        "_embedding": "not a list",
    }
    out = _coerce_to_output_schema(raw)
    jsonschema.validate(instance=out, schema=output_schema)
    assert "_embedding" not in out


def test_output_schema_rejects_underscore_extras(output_schema):
    """P0-1: the output schema's ``additionalProperties: false`` rule
    must REJECT any ``_``-prefixed extra. Privacy rule 5 forbids
    embeddings / hashes / commitments to message content on the
    public output boundary."""
    invalid = {
        "severity": 0,
        "category": 0,
        "confidence": 0.1,
        "actions": {
            "label_only": False,
            "warn": False,
            "strong_warn": False,
            "critical_intervention": False,
            "suggest_redact": False,
        },
        "reason_codes": [],
        "rationale_id": "xlmr_safe_proto_v1",
        "_embedding": [0.1, 0.2, 0.3],
    }
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=invalid, schema=output_schema)


# ---------------------------------------------------------------------------
# INT4 model_path support.
# ---------------------------------------------------------------------------
def test_default_int4_path_constant():
    """The INT4 default path is ``models/xlmr.int4.onnx`` so callers
    can opt into the smaller ~50 MB checkpoint without hard-coding
    the filename."""
    assert DEFAULT_ONNX_INT4_MODEL_PATH == "models/xlmr.int4.onnx"
    assert DEFAULT_ONNX_INT4_MODEL_PATH != DEFAULT_ONNX_MODEL_PATH


def test_prefer_int4_field_default_false():
    """``prefer_int4`` is opt-in — the default behaviour is to load
    the INT8 ``models/xlmr.onnx`` file so existing benchmarks stay
    bit-for-bit reproducible."""
    adapter = XLMRAdapter()
    assert adapter.prefer_int4 is False


def test_prefer_int4_resolves_to_int4_when_present(tmp_path, monkeypatch):
    """When ``prefer_int4=True`` and the INT4 file exists on disk,
    the adapter swaps the resolved ``model_path`` over to it."""
    # Create dummy files at the expected paths so the load probes
    # find them. Use ``monkeypatch.chdir`` so the relative
    # ``DEFAULT_ONNX_*_MODEL_PATH`` constants resolve into the
    # tmp_path sandbox.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "xlmr.onnx").write_bytes(b"int8 placeholder")
    (tmp_path / "models" / "xlmr.int4.onnx").write_bytes(b"int4 placeholder")
    (tmp_path / "models" / "xlmr.spm").write_bytes(b"tokenizer placeholder")

    adapter = XLMRAdapter(prefer_int4=True)
    # Stub out the actual onnxruntime / sentencepiece loads — we are
    # only exercising the path-resolution logic, not the inference
    # session itself.
    import onnxruntime as ort  # type: ignore[import-not-found]
    import sentencepiece as spm  # type: ignore[import-not-found]

    monkeypatch.setattr(spm, "SentencePieceProcessor", lambda: type(
        "T", (), {"Load": lambda self, p: None}
    )())
    monkeypatch.setattr(
        ort,
        "InferenceSession",
        lambda *a, **k: type("S", (), {"get_inputs": lambda self: ()})(),
    )
    # Skip the prototype-encoding step (which needs a real session).
    monkeypatch.setattr(
        XLMRAdapter,
        "_encode_batch",
        lambda self, texts: __import__("numpy").zeros(
            (len(texts), 4), dtype=__import__("numpy").float32
        ),
    )

    adapter._ensure_loaded()
    assert adapter.model_path == DEFAULT_ONNX_INT4_MODEL_PATH


def test_prefer_int4_falls_back_to_int8_when_int4_missing(
    tmp_path, monkeypatch
):
    """``prefer_int4=True`` is a soft hint — when the INT4 file is
    absent, the adapter must keep loading the INT8 default rather
    than failing."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "xlmr.onnx").write_bytes(b"int8 placeholder")
    (tmp_path / "models" / "xlmr.spm").write_bytes(b"tokenizer placeholder")
    # NOTE: no INT4 file on disk.

    adapter = XLMRAdapter(prefer_int4=True)
    import onnxruntime as ort  # type: ignore[import-not-found]
    import sentencepiece as spm  # type: ignore[import-not-found]

    monkeypatch.setattr(spm, "SentencePieceProcessor", lambda: type(
        "T", (), {"Load": lambda self, p: None}
    )())
    monkeypatch.setattr(
        ort,
        "InferenceSession",
        lambda *a, **k: type("S", (), {"get_inputs": lambda self: ()})(),
    )
    monkeypatch.setattr(
        XLMRAdapter,
        "_encode_batch",
        lambda self, texts: __import__("numpy").zeros(
            (len(texts), 4), dtype=__import__("numpy").float32
        ),
    )

    adapter._ensure_loaded()
    assert adapter.model_path == DEFAULT_ONNX_MODEL_PATH


def test_explicit_model_path_honoured_over_prefer_int4(
    tmp_path, monkeypatch
):
    """Explicit ``model_path`` arguments are honoured verbatim, even
    when ``prefer_int4=True`` and an INT4 file is present — callers
    that want a specific tier should pass the path directly."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "xlmr.int4.onnx").write_bytes(b"int4 placeholder")
    (tmp_path / "models" / "xlmr.spm").write_bytes(b"tokenizer placeholder")
    custom = tmp_path / "models" / "custom.onnx"
    custom.write_bytes(b"custom placeholder")

    adapter = XLMRAdapter(prefer_int4=True, model_path=str(custom))
    import onnxruntime as ort  # type: ignore[import-not-found]
    import sentencepiece as spm  # type: ignore[import-not-found]

    monkeypatch.setattr(spm, "SentencePieceProcessor", lambda: type(
        "T", (), {"Load": lambda self, p: None}
    )())
    monkeypatch.setattr(
        ort,
        "InferenceSession",
        lambda *a, **k: type("S", (), {"get_inputs": lambda self: ()})(),
    )
    monkeypatch.setattr(
        XLMRAdapter,
        "_encode_batch",
        lambda self, texts: __import__("numpy").zeros(
            (len(texts), 4), dtype=__import__("numpy").float32
        ),
    )

    adapter._ensure_loaded()
    assert adapter.model_path == str(custom)
