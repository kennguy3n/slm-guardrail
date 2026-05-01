"""Tests for ``XLMRMiniLMAdapter``.

Module under test: ``kchat-skills/compiler/xlmr_minilm_adapter.py``.
The adapter is **not** unit-tested against a real downloaded
encoder in CI — that would require network access. Instead we:

* Verify Protocol conformance.
* Verify the SAFE fallback path when the encoder cannot be loaded
  (the default, since the test environment doesn't have model
  weights cached).
* Verify the classification head's behaviour on top of a stub
  encoder (deterministic embeddings injected directly).
* Verify schema conformance, determinism, and signal-priority
  ordering against ``output_schema.json``.

See ``tools/run_guardrail_demo.py`` for the full end-to-end
exercise that does require a locally-cached XLM-R MiniLM-L6
checkpoint.
"""
from __future__ import annotations

from typing import Any

import jsonschema
import pytest

from slm_adapter import (  # type: ignore[import-not-found]
    CAT_SAFE,
    SLMAdapter,
)
from xlmr_minilm_adapter import (  # type: ignore[import-not-found]
    CATEGORY_PROTOTYPES,
    XLMR_MINILM_MODEL_ID,
    XLMR_MINILM_MODEL_NAME,
    XLMRMiniLMAdapter,
    _coerce_to_output_schema,
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
    determined by the input. This bypasses transformers / torch model
    loading entirely so the test doesn't require network or weights.
    """
    import torch  # type: ignore[import-not-found]

    adapter = XLMRMiniLMAdapter()

    n_categories = len(CATEGORY_PROTOTYPES)
    width = max(hidden, n_categories)
    eye = torch.eye(n_categories, width)

    def fake_ensure_loaded(self: XLMRMiniLMAdapter) -> None:
        self._tokenizer = object()
        self._model = object()
        self._prototype_embeddings = eye

    def fake_encode(self: XLMRMiniLMAdapter, text: str) -> Any:
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
        vec = torch.zeros(width)
        vec[idx] = 1.0
        return vec

    monkeypatch.setattr(
        XLMRMiniLMAdapter, "_ensure_loaded", fake_ensure_loaded
    )
    monkeypatch.setattr(XLMRMiniLMAdapter, "_encode", fake_encode)
    return adapter


# ---------------------------------------------------------------------------
# Protocol conformance.
# ---------------------------------------------------------------------------
def test_xlmr_minilm_adapter_satisfies_protocol():
    adapter = XLMRMiniLMAdapter()
    assert isinstance(adapter, SLMAdapter)


def test_xlmr_minilm_adapter_has_classify_method():
    adapter = XLMRMiniLMAdapter()
    assert callable(getattr(adapter, "classify", None))


def test_default_constants_advertise_xlmr_minilm():
    assert XLMR_MINILM_MODEL_NAME == "XLM-R-MiniLM-L6"
    assert "MiniLM" in XLMR_MINILM_MODEL_ID
    assert "XLMR" in XLMR_MINILM_MODEL_ID or "xlmr" in XLMR_MINILM_MODEL_ID
    assert len(CATEGORY_PROTOTYPES) == 16


# ---------------------------------------------------------------------------
# Fallback when model unavailable.
# ---------------------------------------------------------------------------
def test_unavailable_model_returns_safe(output_schema):
    adapter = XLMRMiniLMAdapter(model_path="/does/not/exist")
    out = adapter.classify(_input())
    jsonschema.validate(instance=out, schema=output_schema)
    assert out == safe_fallback_output()
    assert out["category"] == 0
    assert out["severity"] == 0


def test_unavailable_model_records_latency():
    adapter = XLMRMiniLMAdapter(model_path="/does/not/exist")
    adapter.classify(_input())
    assert adapter.last_latency_ms >= 0.0


def test_unavailable_model_is_deterministic_safe():
    adapter = XLMRMiniLMAdapter(model_path="/does/not/exist")
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


def test_coerce_out_of_range_category_collapses_to_safe():
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
    assert out == safe_fallback_output()


def test_coerce_out_of_range_severity_collapses_to_safe():
    raw = {"severity": 99, "category": 0, "confidence": 0.5}
    out = _coerce_to_output_schema(raw)
    assert out == safe_fallback_output()


def test_coerce_out_of_range_confidence_collapses_to_safe():
    raw = {"severity": 0, "category": 0, "confidence": 2.5}
    out = _coerce_to_output_schema(raw)
    assert out == safe_fallback_output()


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
    pytest.importorskip("torch")
    adapter = _stub_loaded_adapter(monkeypatch)
    out = adapter.classify(_input("hello friends"))
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == CAT_SAFE
    assert out["severity"] == 0


def test_stub_encoder_classifies_scam_text(
    monkeypatch, output_schema
):
    pytest.importorskip("torch")
    adapter = _stub_loaded_adapter(monkeypatch)
    out = adapter.classify(_input("this is a scam"))
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 7  # SCAM_FRAUD
    assert 0.0 <= out["confidence"] <= 1.0


def test_stub_encoder_is_deterministic(monkeypatch):
    pytest.importorskip("torch")
    adapter = _stub_loaded_adapter(monkeypatch)
    a = adapter.classify(_input("scam alert"))
    b = adapter.classify(_input("scam alert"))
    assert a == b


def test_stub_encoder_records_latency(monkeypatch):
    pytest.importorskip("torch")
    adapter = _stub_loaded_adapter(monkeypatch)
    adapter.classify(_input("hello"))
    assert adapter.last_latency_ms >= 0.0


# ---------------------------------------------------------------------------
# Signal overrides take precedence over embedding head.
# ---------------------------------------------------------------------------
def test_child_safety_lexicon_overrides_embedding(
    monkeypatch, output_schema
):
    pytest.importorskip("torch")
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
    pytest.importorskip("torch")
    adapter = _stub_loaded_adapter(monkeypatch)
    out = adapter.classify(
        _input("scam phish", pii_patterns_hit=["EMAIL", "PHONE"])
    )
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 9  # PRIVATE_DATA
    assert out["actions"]["suggest_redact"] is True


def test_url_risk_signal_overrides_embedding(monkeypatch, output_schema):
    pytest.importorskip("torch")
    adapter = _stub_loaded_adapter(monkeypatch)
    out = adapter.classify(_input("hello", url_risk=0.95))
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 7  # SCAM_FRAUD
    assert "URL_RISK" in out["reason_codes"]


def test_scam_pattern_signal_overrides_embedding(
    monkeypatch, output_schema
):
    pytest.importorskip("torch")
    adapter = _stub_loaded_adapter(monkeypatch)
    out = adapter.classify(
        _input("hello", scam_patterns_hit=["ADVANCE_FEE"])
    )
    jsonschema.validate(instance=out, schema=output_schema)
    assert out["category"] == 7  # SCAM_FRAUD
    assert "SCAM_PATTERN" in out["reason_codes"]


def test_lexicon_hate_signal_drives_category(monkeypatch, output_schema):
    pytest.importorskip("torch")
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
    pytest.importorskip("torch")
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
    pytest.importorskip("torch")
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
