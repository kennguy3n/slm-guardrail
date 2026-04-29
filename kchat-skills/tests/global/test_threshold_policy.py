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
    """An SLM that tries to emit warn=true at confidence 0.10 must be re-coerced."""
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
