"""Contract tests for the community-feedback / appeal-flow module.

Spec reference: PHASES.md Phase 6 — "Community feedback + appeal
flow". The privacy invariant ("metadata only, never content") is the
load-bearing invariant of this module; the tests below pin it
explicitly.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from appeal_flow import (  # type: ignore[import-not-found]
    CHILD_SAFETY_CATEGORY,
    MIN_APPEALS_FOR_REVIEW,
    RECOMMENDATION_VALUES,
    REVIEW_SUGGESTED_APPEAL_RATE,
    URGENT_CHILD_SAFETY_RATE,
    URGENT_REVIEW_APPEAL_RATE,
    USER_CONTEXT_VALUES,
    AppealAggregator,
    AppealCase,
    AppealFlowError,
    AppealReport,
)


NOW = datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc)


def _case(
    appeal_id: str = "a-1",
    *,
    skill_id: str = "kchat.jurisdiction.us.guardrail.v1",
    category: int = 5,
    severity: int = 3,
    rationale_id: str = "rat-001",
    user_context: str = "false_positive",
    timestamp: datetime | None = None,
) -> AppealCase:
    return AppealCase(
        appeal_id=appeal_id,
        skill_id=skill_id,
        category=category,
        severity=severity,
        rationale_id=rationale_id,
        user_context=user_context,
        timestamp=timestamp or NOW,
    )


# ---------------------------------------------------------------------------
# Privacy invariant — THE load-bearing test.
# ---------------------------------------------------------------------------
def test_appeal_case_has_no_content_fields():
    """AppealCase must not expose message text, hashes, or embeddings.

    The allowed fields are the closed-enum / scalar set documented in
    ``appeal_flow.py``. Any deviation constitutes a privacy-contract
    violation.
    """
    allowed = {
        "appeal_id",
        "skill_id",
        "category",
        "severity",
        "rationale_id",
        "user_context",
        "timestamp",
    }
    fields = {f.name for f in dataclasses.fields(AppealCase)}
    assert fields == allowed, (
        "AppealCase must contain exactly the closed-enum / scalar field "
        f"set; got {fields}"
    )

    # Explicitly forbid common content-leakage field names.
    forbidden = {
        "text",
        "message",
        "content",
        "content_hash",
        "embedding",
        "embedding_vector",
        "hash",
        "free_text",
        "user_comment",
    }
    assert fields.isdisjoint(forbidden), (
        f"AppealCase must not include content-derived fields; "
        f"violating: {fields & forbidden}"
    )


def test_appeal_report_has_no_content_fields():
    allowed = {
        "skill_id",
        "window_days",
        "total_appeals",
        "per_category_appeal_rates",
        "per_category_appeal_counts",
        "top_rationale_ids",
        "top_user_contexts",
        "recommendation",
    }
    fields = {f.name for f in dataclasses.fields(AppealReport)}
    assert fields == allowed


# ---------------------------------------------------------------------------
# AppealCase construction + validation.
# ---------------------------------------------------------------------------
def test_appeal_case_valid_construction():
    c = _case()
    assert c.appeal_id == "a-1"
    assert c.category == 5
    assert c.user_context == "false_positive"


@pytest.mark.parametrize("bad_id", ["", None, 0])
def test_appeal_case_rejects_bad_appeal_id(bad_id):
    with pytest.raises(AppealFlowError):
        AppealCase(
            appeal_id=bad_id,  # type: ignore[arg-type]
            skill_id="skill",
            category=0,
            severity=0,
            rationale_id="r",
            user_context="false_positive",
            timestamp=NOW,
        )


@pytest.mark.parametrize("cat", [-1, 16, 100])
def test_appeal_case_rejects_out_of_range_category(cat):
    with pytest.raises(AppealFlowError):
        _case(category=cat)


@pytest.mark.parametrize("sev", [-1, 6, 99])
def test_appeal_case_rejects_out_of_range_severity(sev):
    with pytest.raises(AppealFlowError):
        _case(severity=sev)


def test_appeal_case_rejects_unknown_user_context():
    with pytest.raises(AppealFlowError):
        _case(user_context="nope_not_an_enum_value")


def test_appeal_case_requires_timezone_aware_timestamp():
    with pytest.raises(AppealFlowError):
        _case(timestamp=datetime(2026, 4, 29, 12, 0))


def test_user_context_enum_matches_spec():
    assert USER_CONTEXT_VALUES == frozenset(
        {
            "disagree_category",
            "disagree_severity",
            "false_positive",
            "missing_context",
        }
    )


def test_recommendation_enum_matches_spec():
    assert RECOMMENDATION_VALUES == frozenset(
        {"no_action", "review_suggested", "urgent_review"}
    )


# ---------------------------------------------------------------------------
# Aggregator — submission.
# ---------------------------------------------------------------------------
def test_submit_then_aggregate_single_appeal():
    agg = AppealAggregator(now=NOW)
    agg.submit(_case("a1", category=5))
    report = agg.aggregate("kchat.jurisdiction.us.guardrail.v1")
    assert report.total_appeals == 1
    assert report.per_category_appeal_counts == {5: 1}
    assert report.recommendation == "no_action"
    assert report.window_days == 30


def test_submit_rejects_non_appeal_case():
    agg = AppealAggregator(now=NOW)
    with pytest.raises(AppealFlowError):
        agg.submit("not a case")  # type: ignore[arg-type]


def test_submit_rejects_duplicate_appeal_id():
    agg = AppealAggregator(now=NOW)
    agg.submit(_case("dup"))
    with pytest.raises(AppealFlowError):
        agg.submit(_case("dup"))


def test_aggregate_scopes_to_skill_id():
    agg = AppealAggregator(now=NOW)
    agg.submit(_case("a1", skill_id="skill.a", category=5))
    agg.submit(_case("a2", skill_id="skill.b", category=5))
    r = agg.aggregate("skill.a")
    assert r.total_appeals == 1
    assert r.per_category_appeal_counts == {5: 1}


def test_empty_aggregate_returns_no_action():
    agg = AppealAggregator(now=NOW)
    r = agg.aggregate("unknown-skill")
    assert r.total_appeals == 0
    assert r.recommendation == "no_action"
    assert r.per_category_appeal_counts == {}
    assert r.per_category_appeal_rates == {}
    assert r.top_rationale_ids == []


def test_aggregate_rejects_zero_window():
    agg = AppealAggregator(now=NOW)
    with pytest.raises(AppealFlowError):
        agg.aggregate("skill.a", window_days=0)


def test_aggregate_window_filtering():
    agg = AppealAggregator(now=NOW)
    # Inside window.
    agg.submit(
        _case("in", category=5, timestamp=NOW - timedelta(days=10))
    )
    # Outside window.
    agg.submit(
        _case("out", category=5, timestamp=NOW - timedelta(days=45))
    )
    r = agg.aggregate("kchat.jurisdiction.us.guardrail.v1", window_days=30)
    assert r.total_appeals == 1


def test_aggregate_counts_top_rationales_and_user_contexts():
    agg = AppealAggregator(now=NOW)
    for i in range(3):
        agg.submit(
            _case(
                f"a{i}",
                rationale_id="rat-most",
                user_context="false_positive",
            )
        )
    agg.submit(_case("b1", rationale_id="rat-other", user_context="missing_context"))
    r = agg.aggregate("kchat.jurisdiction.us.guardrail.v1")
    assert r.top_rationale_ids[0] == ("rat-most", 3)
    assert r.top_user_contexts[0] == ("false_positive", 3)


# ---------------------------------------------------------------------------
# Recommendation thresholds.
# ---------------------------------------------------------------------------
def test_child_safety_single_appeal_triggers_urgent_review():
    """Any CHILD_SAFETY appeal escalates immediately."""
    agg = AppealAggregator(now=NOW)
    agg.submit(_case("c1", category=CHILD_SAFETY_CATEGORY, severity=5))
    r = agg.aggregate("kchat.jurisdiction.us.guardrail.v1")
    assert r.recommendation == "urgent_review"


def test_child_safety_escalates_when_diluted_by_high_volume():
    """A single CHILD_SAFETY appeal still escalates even when the
    overall appeal volume is high enough that the child-safety rate
    falls below URGENT_CHILD_SAFETY_RATE and the count is below
    MIN_APPEALS_FOR_REVIEW.

    Regression test for a bug where the child-safety short-circuit
    was guarded on ``rate >= URGENT_CHILD_SAFETY_RATE or count >=
    MIN_APPEALS_FOR_REVIEW`` and so failed to escalate in this
    dilution regime, contradicting the spec that every category-1
    appeal is material regardless of count.
    """
    agg = AppealAggregator(now=NOW)
    # 1 child-safety appeal out of 200 total — rate = 0.5% < 1%.
    agg.submit(_case("cs-0", category=CHILD_SAFETY_CATEGORY, severity=5))
    for i in range(199):
        agg.submit(_case(f"bg-{i}", category=0))
    r = agg.aggregate("kchat.jurisdiction.us.guardrail.v1")
    assert r.per_category_appeal_counts[CHILD_SAFETY_CATEGORY] == 1
    assert (
        r.per_category_appeal_rates[CHILD_SAFETY_CATEGORY]
        < 0.01  # URGENT_CHILD_SAFETY_RATE
    )
    assert r.recommendation == "urgent_review"


def test_low_volume_does_not_escalate_below_min_appeals():
    """Below ``MIN_APPEALS_FOR_REVIEW`` a single category can't drive urgent."""
    agg = AppealAggregator(now=NOW)
    # 4 appeals all on category 5 — insufficient to count.
    for i in range(MIN_APPEALS_FOR_REVIEW - 1):
        agg.submit(_case(f"a{i}", category=5))
    r = agg.aggregate("kchat.jurisdiction.us.guardrail.v1")
    assert r.recommendation == "no_action"


def test_review_suggested_at_5_pct_rate():
    """Cat 5 at ~6% rate with all other cats below MIN_APPEALS_FOR_REVIEW."""
    agg = AppealAggregator(now=NOW)
    # Drop MIN_APPEALS_FOR_REVIEW - 1 = 4 appeals into several benign
    # categories so none of them trigger on count.
    total_filler = 0
    for cat in (0, 3, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15):
        for i in range(MIN_APPEALS_FOR_REVIEW - 1):
            agg.submit(_case(f"f{cat}-{i}", category=cat))
            total_filler += 1
    # 7 appeals on cat 5 — above the MIN floor, below the 15% urgent rate.
    for i in range(7):
        agg.submit(_case(f"a5-{i}", category=5))
    r = agg.aggregate("kchat.jurisdiction.us.guardrail.v1")
    assert r.recommendation == "review_suggested"
    assert r.per_category_appeal_rates[5] >= REVIEW_SUGGESTED_APPEAL_RATE
    assert r.per_category_appeal_rates[5] < URGENT_REVIEW_APPEAL_RATE


def test_urgent_review_at_15_pct_rate():
    """Cat 5 at ~20% rate — urgent."""
    agg = AppealAggregator(now=NOW)
    for cat in (0, 3, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15):
        for i in range(MIN_APPEALS_FOR_REVIEW - 1):
            agg.submit(_case(f"f{cat}-{i}", category=cat))
    for i in range(15):
        agg.submit(_case(f"a5-{i}", category=5))
    r = agg.aggregate("kchat.jurisdiction.us.guardrail.v1")
    assert r.recommendation == "urgent_review"
    assert r.per_category_appeal_rates[5] >= URGENT_REVIEW_APPEAL_RATE


def test_thresholds_monotonic_in_rate():
    assert URGENT_REVIEW_APPEAL_RATE > REVIEW_SUGGESTED_APPEAL_RATE
    assert URGENT_CHILD_SAFETY_RATE < REVIEW_SUGGESTED_APPEAL_RATE


# ---------------------------------------------------------------------------
# Multi-skill + clear().
# ---------------------------------------------------------------------------
def test_skill_ids_returns_distinct():
    agg = AppealAggregator(now=NOW)
    agg.submit(_case("a1", skill_id="s.a"))
    agg.submit(_case("a2", skill_id="s.b"))
    agg.submit(_case("a3", skill_id="s.a"))
    assert sorted(agg.skill_ids()) == ["s.a", "s.b"]


def test_clear_empties_store():
    agg = AppealAggregator(now=NOW)
    agg.submit(_case("a1"))
    agg.clear()
    r = agg.aggregate("kchat.jurisdiction.us.guardrail.v1")
    assert r.total_appeals == 0


# ---------------------------------------------------------------------------
# Edge cases.
# ---------------------------------------------------------------------------
def test_report_rejects_unknown_recommendation():
    with pytest.raises(AppealFlowError):
        AppealReport(
            skill_id="s",
            window_days=30,
            total_appeals=0,
            per_category_appeal_rates={},
            per_category_appeal_counts={},
            top_rationale_ids=[],
            top_user_contexts=[],
            recommendation="not_a_value",
        )


def test_report_rejects_negative_totals():
    with pytest.raises(AppealFlowError):
        AppealReport(
            skill_id="s",
            window_days=30,
            total_appeals=-1,
            per_category_appeal_rates={},
            per_category_appeal_counts={},
            top_rationale_ids=[],
            top_user_contexts=[],
            recommendation="no_action",
        )
