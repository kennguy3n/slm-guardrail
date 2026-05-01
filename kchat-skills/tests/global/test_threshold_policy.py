"""Tests for the hard-coded threshold policy.

Module under test: ``kchat-skills/compiler/threshold_policy.py``. See
ARCHITECTURE.md "Decision Policy" (lines 353-373).
"""
from __future__ import annotations

from typing import Any

import jsonschema
import pytest

from threshold_policy import (  # type: ignore[import-not-found]
    CHILD_SAFETY_CATEGORY,
    PROTECTED_SPEECH_REASON_CODES,
    SAFE_CATEGORY,
    ThresholdPolicy,
)


def _raw(
    *,
    category: int,
    severity: int,
    confidence: float,
    rationale_id: str = "test_v1",
    actions: dict[str, bool] | None = None,
    reason_codes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "category": category,
        "severity": severity,
        "confidence": confidence,
        "actions": actions
        or {
            "label_only": False,
            "warn": False,
            "strong_warn": False,
            "critical_intervention": False,
            "suggest_redact": False,
        },
        "reason_codes": reason_codes or [],
        "rationale_id": rationale_id,
    }


# ---------------------------------------------------------------------------
# Threshold constants
# ---------------------------------------------------------------------------
def test_thresholds_are_hard_coded():
    p = ThresholdPolicy()
    assert p.LABEL_ONLY == 0.45
    assert p.WARN == 0.62
    assert p.STRONG_WARN == 0.78
    assert p.CRITICAL_INTERVENTION == 0.85


def test_thresholds_cannot_be_overridden():
    with pytest.raises(ValueError):
        ThresholdPolicy(LABEL_ONLY=0.5)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Uncertainty handling (< 0.45 => SAFE for non-zero categories)
# ---------------------------------------------------------------------------
def test_confidence_044_with_non_zero_category_is_safe():
    p = ThresholdPolicy()
    out = p.apply(_raw(category=7, severity=3, confidence=0.44))
    assert out["category"] == SAFE_CATEGORY
    assert out["severity"] == 0
    assert all(v is False for v in out["actions"].values())


def test_confidence_below_label_only_with_safe_category_stays_safe():
    p = ThresholdPolicy()
    out = p.apply(_raw(category=0, severity=0, confidence=0.10))
    assert out["category"] == SAFE_CATEGORY
    assert out["severity"] == 0


# ---------------------------------------------------------------------------
# Threshold boundaries
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "confidence,expected_action",
    [
        (0.45, "label_only"),
        (0.62, "warn"),
        (0.78, "strong_warn"),
        (0.85, "critical_intervention"),
    ],
)
def test_threshold_boundaries(confidence, expected_action):
    p = ThresholdPolicy()
    out = p.apply(_raw(category=7, severity=3, confidence=confidence))
    assert out["actions"][expected_action] is True
    for key, value in out["actions"].items():
        if key == expected_action:
            continue
        if key == "suggest_redact":
            continue
        assert value is False, (
            f"only {expected_action} should be true at confidence {confidence}"
        )


def test_confidence_just_above_boundary_activates_that_tier():
    p = ThresholdPolicy()
    assert p.apply(_raw(category=7, severity=3, confidence=0.61))[
        "actions"
    ]["label_only"] is True
    assert p.apply(_raw(category=7, severity=3, confidence=0.77))[
        "actions"
    ]["warn"] is True
    assert p.apply(_raw(category=7, severity=3, confidence=0.84))[
        "actions"
    ]["strong_warn"] is True


# ---------------------------------------------------------------------------
# Child-safety floor
# ---------------------------------------------------------------------------
def test_child_safety_at_confidence_045_pins_severity_5():
    p = ThresholdPolicy()
    out = p.apply(
        _raw(
            category=CHILD_SAFETY_CATEGORY,
            severity=2,
            confidence=0.45,
        )
    )
    assert out["severity"] == 5
    assert out["actions"]["critical_intervention"] is True
    assert "CHILD_SAFETY_FLOOR" in out["reason_codes"]


def test_child_safety_at_confidence_044_is_safe():
    """Confidence below label_only maps CHILD_SAFETY to SAFE per the
    uncertainty rule (ARCHITECTURE.md line 373 gates the floor at >= 0.45)."""
    p = ThresholdPolicy()
    out = p.apply(
        _raw(
            category=CHILD_SAFETY_CATEGORY,
            severity=2,
            confidence=0.44,
        )
    )
    assert out["category"] == SAFE_CATEGORY
    assert out["severity"] == 0


def test_child_safety_high_confidence_keeps_critical_intervention():
    p = ThresholdPolicy()
    out = p.apply(
        _raw(
            category=CHILD_SAFETY_CATEGORY,
            severity=5,
            confidence=0.90,
        )
    )
    assert out["severity"] == 5
    assert out["actions"]["critical_intervention"] is True


# ---------------------------------------------------------------------------
# Tie-break (lower-numbered category wins)
# ---------------------------------------------------------------------------
def test_tie_break_lower_numbered_category_wins():
    candidates = [
        {"category": 6, "severity": 3, "confidence": 0.7},
        {"category": 4, "severity": 3, "confidence": 0.7},
    ]
    winner = ThresholdPolicy.tie_break(candidates)
    assert winner["category"] == 4


def test_tie_break_prefers_higher_severity():
    candidates = [
        {"category": 4, "severity": 3, "confidence": 0.7},
        {"category": 6, "severity": 4, "confidence": 0.7},
    ]
    winner = ThresholdPolicy.tie_break(candidates)
    assert winner["category"] == 6


def test_tie_break_empty_raises():
    with pytest.raises(ValueError):
        ThresholdPolicy.tie_break([])


# ---------------------------------------------------------------------------
# Output schema conformance
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "confidence,category",
    [
        (0.10, 7),
        (0.45, 7),
        (0.62, 6),
        (0.78, 3),
        (0.85, 1),
        (0.95, 1),
    ],
)
def test_apply_produces_valid_output_schema(
    confidence, category, output_schema
):
    p = ThresholdPolicy()
    out = p.apply(_raw(category=category, severity=3, confidence=confidence))
    jsonschema.validate(instance=out, schema=output_schema)


# ---------------------------------------------------------------------------
# Input does not override thresholds
# ---------------------------------------------------------------------------
def test_slm_cannot_assert_warn_below_warn_threshold():
    """A classifier output that asserts warn=true at confidence 0.10 must be re-coerced."""
    p = ThresholdPolicy()
    out = p.apply(
        _raw(
            category=7,
            severity=3,
            confidence=0.10,
            actions={
                "label_only": True,
                "warn": True,
                "strong_warn": False,
                "critical_intervention": False,
                "suggest_redact": False,
            },
        )
    )
    # Below label_only with non-SAFE category maps to SAFE.
    assert out["category"] == 0
    assert all(v is False for v in out["actions"].values())


def test_slm_cannot_assert_critical_intervention_at_low_confidence():
    p = ThresholdPolicy()
    out = p.apply(
        _raw(
            category=7,
            severity=3,
            confidence=0.50,
            actions={
                "label_only": False,
                "warn": False,
                "strong_warn": False,
                "critical_intervention": True,
                "suggest_redact": False,
            },
        )
    )
    # At 0.50, only label_only should be set.
    assert out["actions"]["label_only"] is True
    assert out["actions"]["critical_intervention"] is False


def test_suggest_redact_is_preserved():
    p = ThresholdPolicy()
    out = p.apply(
        _raw(
            category=9,
            severity=3,
            confidence=0.70,
            actions={
                "label_only": False,
                "warn": False,
                "strong_warn": False,
                "critical_intervention": False,
                "suggest_redact": True,
            },
        )
    )
    assert out["actions"]["suggest_redact"] is True
    assert out["actions"]["warn"] is True


# ---------------------------------------------------------------------------
# Protected-speech demotion (NEWS_CONTEXT / EDUCATION_CONTEXT /
# COUNTERSPEECH_CONTEXT / QUOTED_SPEECH_CONTEXT).
# ---------------------------------------------------------------------------
def test_protected_speech_reason_codes_constant():
    assert PROTECTED_SPEECH_REASON_CODES == frozenset(
        {
            "NEWS_CONTEXT",
            "EDUCATION_CONTEXT",
            "COUNTERSPEECH_CONTEXT",
            "QUOTED_SPEECH_CONTEXT",
        }
    )


@pytest.mark.parametrize(
    "protected_code",
    sorted(PROTECTED_SPEECH_REASON_CODES),
)
def test_protected_speech_demotes_violence_threat_to_safe(protected_code):
    """News quotes / educational refs / counterspeech / user quotes
    must demote a non-CHILD_SAFETY non-SAFE verdict back to SAFE."""
    p = ThresholdPolicy()
    out = p.apply(
        _raw(
            category=3,  # VIOLENCE_THREAT
            severity=2,
            confidence=0.50,
            actions={
                "label_only": True,
                "warn": False,
                "strong_warn": False,
                "critical_intervention": False,
                "suggest_redact": False,
            },
            reason_codes=[protected_code],
        )
    )
    assert out["category"] == SAFE_CATEGORY
    assert out["severity"] == 0
    assert out["actions"] == {
        "label_only": False,
        "warn": False,
        "strong_warn": False,
        "critical_intervention": False,
        "suggest_redact": False,
    }
    assert protected_code in out["reason_codes"]
    assert out["rationale_id"] == "safe_protected_speech_v1"


def test_protected_speech_demotes_extremism_to_safe():
    p = ThresholdPolicy()
    out = p.apply(
        _raw(
            category=4,  # EXTREMISM
            severity=2,
            confidence=0.62,
            reason_codes=["NEWS_CONTEXT"],
        )
    )
    assert out["category"] == SAFE_CATEGORY
    assert "NEWS_CONTEXT" in out["reason_codes"]


def test_protected_speech_preserves_multiple_protected_codes():
    """When multiple protected-speech reason codes are present (e.g. a
    news quote that's also user-quoted), all of them are kept on the
    demoted output for review traceability."""
    p = ThresholdPolicy()
    out = p.apply(
        _raw(
            category=3,
            severity=2,
            confidence=0.55,
            reason_codes=[
                "NEWS_CONTEXT",
                "QUOTED_SPEECH_CONTEXT",
                "LEXICON_HIT",
            ],
        )
    )
    assert out["category"] == SAFE_CATEGORY
    assert "NEWS_CONTEXT" in out["reason_codes"]
    assert "QUOTED_SPEECH_CONTEXT" in out["reason_codes"]
    # Non-protected codes (LEXICON_HIT) are dropped from the demoted
    # output — only the protected-speech reason codes are preserved.
    assert "LEXICON_HIT" not in out["reason_codes"]


def test_child_safety_floor_wins_over_news_context():
    """CHILD_SAFETY at >= LABEL_ONLY confidence is non-negotiable —
    even a news quote does not demote it. Public-interest reporting
    of CSAM must still surface the floor."""
    p = ThresholdPolicy()
    out = p.apply(
        _raw(
            category=CHILD_SAFETY_CATEGORY,
            severity=5,
            confidence=0.90,
            reason_codes=["NEWS_CONTEXT", "QUOTED_SPEECH_CONTEXT"],
        )
    )
    assert out["category"] == CHILD_SAFETY_CATEGORY
    assert out["severity"] == 5
    assert out["actions"]["critical_intervention"] is True
    assert "CHILD_SAFETY_FLOOR" in out["reason_codes"]


def test_safe_with_protected_speech_stays_safe():
    """Already-SAFE verdict carrying a protected-speech code is left
    alone (rule 2 only fires for non-SAFE categories)."""
    p = ThresholdPolicy()
    out = p.apply(
        _raw(
            category=SAFE_CATEGORY,
            severity=0,
            confidence=0.20,
            reason_codes=["NEWS_CONTEXT"],
        )
    )
    assert out["category"] == SAFE_CATEGORY
    assert out["severity"] == 0


def test_non_protected_reason_code_does_not_demote():
    """Sanity: the demotion only triggers on the protected-speech
    enum members. Other reason codes (e.g. LEXICON_HIT) leave the
    verdict intact."""
    p = ThresholdPolicy()
    out = p.apply(
        _raw(
            category=6,  # HATE
            severity=3,
            confidence=0.70,
            reason_codes=["LEXICON_HIT"],
        )
    )
    assert out["category"] == 6
    assert out["actions"]["warn"] is True


def test_protected_speech_at_low_confidence_still_demotes():
    """Demotion happens regardless of confidence, since rule 2 sits
    above rule 3 (uncertainty handling). A low-confidence VIOLENCE
    verdict carrying NEWS_CONTEXT becomes SAFE with the protected
    reason code preserved (not the empty reason_codes that rule 3
    would otherwise produce)."""
    p = ThresholdPolicy()
    out = p.apply(
        _raw(
            category=3,
            severity=2,
            confidence=0.20,  # below LABEL_ONLY
            reason_codes=["NEWS_CONTEXT"],
        )
    )
    assert out["category"] == SAFE_CATEGORY
    assert "NEWS_CONTEXT" in out["reason_codes"]
    assert out["rationale_id"] == "safe_protected_speech_v1"
