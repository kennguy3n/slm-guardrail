"""Tests for the encoder classifier runtime adapter protocol +
MockSLMAdapter.

Module under test: ``kchat-skills/compiler/slm_adapter.py``. See
ARCHITECTURE.md "Hybrid Local Pipeline" step 4 and PHASES.md Phase 3.
The protocol class name (``SLMAdapter``) is preserved for backwards
compatibility — it now matches any encoder-classifier backend, not
just generative SLMs.
"""
from __future__ import annotations

from typing import Any

import jsonschema
import pytest

from slm_adapter import (  # type: ignore[import-not-found]
    MockSLMAdapter,
    SLMAdapter,
)


# ---------------------------------------------------------------------------
# Helpers — minimal valid local_signal instance.
# ---------------------------------------------------------------------------
def _input(**signals: Any) -> dict[str, Any]:
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
            "text": "",
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


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------
def test_mock_adapter_satisfies_protocol():
    adapter = MockSLMAdapter()
    assert isinstance(adapter, SLMAdapter)


def test_mock_adapter_has_classify_method():
    adapter = MockSLMAdapter()
    assert callable(getattr(adapter, "classify", None))


# ---------------------------------------------------------------------------
# Deterministic output
# ---------------------------------------------------------------------------
def test_mock_adapter_is_deterministic():
    adapter = MockSLMAdapter()
    inp = _input(pii_patterns_hit=["EMAIL"])
    a = adapter.classify(inp)
    b = adapter.classify(inp)
    assert a == b, "MockSLMAdapter must be deterministic"


# ---------------------------------------------------------------------------
# Schema conformance
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "signals",
    [
        {},
        {"url_risk": 0.95},
        {"pii_patterns_hit": ["EMAIL", "PHONE"]},
        {"scam_patterns_hit": ["PHISHING_LINK"]},
        {"lexicon_hits": [{"lexicon_id": "x", "category": 1, "weight": 0.8}]},
        {"lexicon_hits": [{"lexicon_id": "x", "category": 6, "weight": 0.7}]},
        {"media_descriptors": [{"kind": "image", "nsfw_score": 0.9}]},
    ],
)
def test_mock_adapter_output_matches_output_schema(signals, output_schema):
    adapter = MockSLMAdapter()
    out = adapter.classify(_input(**signals))
    jsonschema.validate(instance=out, schema=output_schema)


# ---------------------------------------------------------------------------
# Category-specific behaviour
# ---------------------------------------------------------------------------
def test_mock_adapter_returns_safe_for_empty_signals():
    adapter = MockSLMAdapter()
    out = adapter.classify(_input())
    assert out["category"] == 0
    assert out["severity"] == 0


def test_mock_adapter_flags_url_risk_as_scam():
    adapter = MockSLMAdapter()
    out = adapter.classify(_input(url_risk=0.9))
    assert out["category"] == 7  # SCAM_FRAUD
    assert "URL_RISK" in out["reason_codes"]


def test_mock_adapter_flags_pii_as_private_data():
    adapter = MockSLMAdapter()
    out = adapter.classify(_input(pii_patterns_hit=["EMAIL"]))
    assert out["category"] == 9  # PRIVATE_DATA
    assert out["actions"]["suggest_redact"] is True


def test_mock_adapter_flags_child_safety_lexicon():
    adapter = MockSLMAdapter()
    out = adapter.classify(
        _input(
            lexicon_hits=[
                {"lexicon_id": "cs_v1", "category": 1, "weight": 0.9}
            ]
        )
    )
    assert out["category"] == 1  # CHILD_SAFETY
    assert out["severity"] == 5  # child-safety floor
    assert out["actions"]["critical_intervention"] is True
    assert "CHILD_SAFETY_FLOOR" in out["reason_codes"]


def test_mock_adapter_flags_media_nsfw():
    adapter = MockSLMAdapter()
    out = adapter.classify(
        _input(
            media_descriptors=[
                {"kind": "image", "nsfw_score": 0.85}
            ]
        )
    )
    assert out["category"] == 10  # SEXUAL_ADULT


def test_mock_adapter_covers_all_16_categories_via_lexicon():
    """Feed one lexicon hit per category and verify the adapter returns a valid output for each."""
    adapter = MockSLMAdapter()
    for category in range(16):
        out = adapter.classify(
            _input(
                lexicon_hits=[
                    {"lexicon_id": "x", "category": category, "weight": 0.7}
                ]
            )
        )
        # Child-safety gets special severity-5 handling; PII / scam
        # paths aren't exercised through lexicon_hits, so categories
        # that the mock maps elsewhere still produce a valid output.
        assert 0 <= out["category"] <= 15
        assert 0 <= out["severity"] <= 5
        assert 0.0 <= out["confidence"] <= 1.0


# ---------------------------------------------------------------------------
# Priority ordering — CHILD_SAFETY wins over everything else.
# ---------------------------------------------------------------------------
def test_mock_adapter_child_safety_wins_over_pii():
    adapter = MockSLMAdapter()
    out = adapter.classify(
        _input(
            pii_patterns_hit=["EMAIL"],
            lexicon_hits=[
                {"lexicon_id": "cs_v1", "category": 1, "weight": 0.9}
            ],
        )
    )
    assert out["category"] == 1


def test_mock_adapter_pii_wins_over_scam():
    adapter = MockSLMAdapter()
    out = adapter.classify(
        _input(
            url_risk=0.9,
            pii_patterns_hit=["EMAIL"],
        )
    )
    assert out["category"] == 9  # PRIVATE_DATA wins


# ---------------------------------------------------------------------------
# Temperature constraint.
# ---------------------------------------------------------------------------
def test_input_constraints_pin_temperature_0_0():
    """Sanity: the local_signal schema forces temperature 0.0, which is what the adapter must honour."""
    inp = _input()
    assert inp["constraints"]["temperature"] == 0.0
