"""Community feedback + appeal flow.

Spec reference: PHASES.md Phase 6 — "Community feedback + appeal
flow". The appeal flow runs **on-device only**: the privacy contract
(see ``kchat-skills/global/baseline.yaml`` ``privacy_rules``) forbids
uploading message text, embeddings, hashes, or any content-derived
data. This module records **metadata only** — the structured fields
below — and aggregates them into a per-skill recommendation.

Design:

* :class:`AppealCase` — an individual appeal record. Fields are a
  closed enum / scalars only. There is **no** free-text field, no
  hashing input, and no identifier derived from message content.
* :class:`AppealAggregator` — device-local store with `submit` and
  ``aggregate`` entry points.
* :class:`AppealReport` — aggregated recommendation surface, emitted
  to host applications for routing to human review. Three
  recommendation levels (``no_action``, ``review_suggested``,
  ``urgent_review``).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Closed enumerations — privacy-contract-safe.
# ---------------------------------------------------------------------------
USER_CONTEXT_VALUES: frozenset[str] = frozenset(
    {
        "disagree_category",
        "disagree_severity",
        "false_positive",
        "missing_context",
    }
)

RECOMMENDATION_VALUES: frozenset[str] = frozenset(
    {
        "no_action",
        "review_suggested",
        "urgent_review",
    }
)

# Taxonomy bounds mirror anti_misuse.py — category 0 is SAFE, 1 is
# CHILD_SAFETY, and any appeal on category 1 goes straight to
# urgent_review per the child-safety invariant.
TAXONOMY_MIN = 0
TAXONOMY_MAX = 15
CHILD_SAFETY_CATEGORY = 1

# Severity range matches the output schema (0..5).
SEVERITY_MIN = 0
SEVERITY_MAX = 5

# Thresholds that promote an aggregated report to a stronger
# recommendation. All thresholds are per-category.
REVIEW_SUGGESTED_APPEAL_RATE = 0.05
URGENT_REVIEW_APPEAL_RATE = 0.15
# Child-safety category uses a more aggressive threshold — any
# non-trivial appeal rate is escalated.
URGENT_CHILD_SAFETY_RATE = 0.01
# Minimum number of appeals before a per-category rate is meaningful.
MIN_APPEALS_FOR_REVIEW = 5


# ---------------------------------------------------------------------------
# Errors.
# ---------------------------------------------------------------------------
class AppealFlowError(ValueError):
    """An appeal record or aggregation violated a contract invariant."""


# Closed set of fields allowed on an AppealCase. Any attempt to
# deserialise a case with additional fields is a privacy-contract
# violation and rejected.
_ALLOWED_CASE_FIELDS: frozenset[str] = frozenset(
    {
        "appeal_id",
        "skill_id",
        "category",
        "severity",
        "rationale_id",
        "user_context",
        "timestamp",
    }
)


# ---------------------------------------------------------------------------
# Appeal case.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AppealCase:
    """One appeal submitted by a user against a classification.

    Privacy invariant: every field below is a closed enum, numeric
    scalar, or opaque identifier. There is **no** message text,
    embedding, content hash, or free-text field. The `appeal_id`
    is a client-generated opaque string (UUID, ULID, etc.) that must
    not encode message content.
    """

    appeal_id: str
    skill_id: str
    category: int
    severity: int
    rationale_id: str
    user_context: str
    timestamp: datetime

    def __post_init__(self) -> None:
        if not self.appeal_id or not isinstance(self.appeal_id, str):
            raise AppealFlowError("appeal_id must be a non-empty string")
        if not self.skill_id or not isinstance(self.skill_id, str):
            raise AppealFlowError("skill_id must be a non-empty string")
        if not isinstance(self.category, int) or not (
            TAXONOMY_MIN <= self.category <= TAXONOMY_MAX
        ):
            raise AppealFlowError(
                f"category {self.category!r} out of range "
                f"{TAXONOMY_MIN}..{TAXONOMY_MAX}"
            )
        if not isinstance(self.severity, int) or not (
            SEVERITY_MIN <= self.severity <= SEVERITY_MAX
        ):
            raise AppealFlowError(
                f"severity {self.severity!r} out of range "
                f"{SEVERITY_MIN}..{SEVERITY_MAX}"
            )
        if not self.rationale_id or not isinstance(self.rationale_id, str):
            raise AppealFlowError("rationale_id must be a non-empty string")
        if self.user_context not in USER_CONTEXT_VALUES:
            raise AppealFlowError(
                f"user_context {self.user_context!r} must be one of "
                f"{sorted(USER_CONTEXT_VALUES)}"
            )
        if not isinstance(self.timestamp, datetime):
            raise AppealFlowError("timestamp must be a datetime instance")
        if self.timestamp.tzinfo is None:
            raise AppealFlowError("timestamp must be timezone-aware (UTC)")


# ---------------------------------------------------------------------------
# Aggregator output.
# ---------------------------------------------------------------------------
@dataclass
class AppealReport:
    """Aggregated view of appeals for a single skill pack.

    The report is content-free by design: every field is either a
    closed-enum recommendation, a stable identifier, or a rate /
    count scalar. Host applications that persist reports must not
    associate them with message content.
    """

    skill_id: str
    window_days: int
    total_appeals: int
    per_category_appeal_rates: dict[int, float]
    per_category_appeal_counts: dict[int, int]
    top_rationale_ids: list[tuple[str, int]]
    top_user_contexts: list[tuple[str, int]]
    recommendation: str

    def __post_init__(self) -> None:
        if self.recommendation not in RECOMMENDATION_VALUES:
            raise AppealFlowError(
                f"recommendation {self.recommendation!r} must be one of "
                f"{sorted(RECOMMENDATION_VALUES)}"
            )
        if self.total_appeals < 0:
            raise AppealFlowError("total_appeals must be >= 0")
        if self.window_days < 1:
            raise AppealFlowError("window_days must be >= 1")


# ---------------------------------------------------------------------------
# Aggregator.
# ---------------------------------------------------------------------------
class AppealAggregator:
    """Device-local appeal store.

    Holds appeals in memory only; persistence is the host
    application's responsibility (e.g. encrypted sqlite). The
    aggregator itself never serialises content.
    """

    def __init__(self, *, now: Optional[datetime] = None) -> None:
        self._appeals: list[AppealCase] = []
        # ``now`` is injected for tests; defaults to wall-clock UTC.
        self._now_fn = (lambda: now) if now is not None else _utcnow

    # ---- Submission ----------------------------------------------------
    def submit(self, appeal: AppealCase) -> None:
        """Record an appeal. Rejects invalid or duplicate appeal_ids."""
        if not isinstance(appeal, AppealCase):
            raise AppealFlowError(
                f"submit() expects AppealCase, got {type(appeal).__name__}"
            )
        if any(a.appeal_id == appeal.appeal_id for a in self._appeals):
            raise AppealFlowError(
                f"duplicate appeal_id {appeal.appeal_id!r}"
            )
        self._appeals.append(appeal)

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._appeals)

    # ---- Aggregation ---------------------------------------------------
    def aggregate(
        self,
        skill_id: str,
        *,
        window_days: int = 30,
    ) -> AppealReport:
        """Compute a :class:`AppealReport` for ``skill_id`` over the window."""
        if window_days < 1:
            raise AppealFlowError("window_days must be >= 1")
        cutoff = self._now_fn() - timedelta(days=window_days)
        scoped = [
            a
            for a in self._appeals
            if a.skill_id == skill_id and a.timestamp >= cutoff
        ]
        total = len(scoped)

        per_category_counts: dict[int, int] = dict(
            Counter(a.category for a in scoped)
        )
        per_category_rates: dict[int, float] = (
            {cat: cnt / total for cat, cnt in per_category_counts.items()}
            if total
            else {}
        )

        rationale_counter = Counter(a.rationale_id for a in scoped)
        user_context_counter = Counter(a.user_context for a in scoped)

        recommendation = _classify_recommendation(
            per_category_counts=per_category_counts,
            per_category_rates=per_category_rates,
            total=total,
        )

        return AppealReport(
            skill_id=skill_id,
            window_days=window_days,
            total_appeals=total,
            per_category_appeal_rates=per_category_rates,
            per_category_appeal_counts=per_category_counts,
            top_rationale_ids=rationale_counter.most_common(5),
            top_user_contexts=user_context_counter.most_common(5),
            recommendation=recommendation,
        )

    # ---- Introspection -------------------------------------------------
    def skill_ids(self) -> list[str]:
        """Return the distinct skill_ids seen by this aggregator."""
        seen: dict[str, None] = {}
        for a in self._appeals:
            seen[a.skill_id] = None
        return list(seen.keys())

    def clear(self) -> None:
        """Drop every appeal. Intended for test teardown only."""
        self._appeals.clear()


# ---------------------------------------------------------------------------
# Recommendation classifier.
# ---------------------------------------------------------------------------
def _classify_recommendation(
    *,
    per_category_counts: dict[int, int],
    per_category_rates: dict[int, float],
    total: int,
) -> str:
    """Apply threshold logic to produce a recommendation.

    Rules (checked in order — first match wins):

    1. Any category-1 (CHILD_SAFETY) appeal → urgent_review (unconditional).
    2. Any category with rate >= 15% → urgent_review.
    3. Any category with rate >= 5% → review_suggested.
    4. Otherwise → no_action.

    A per-category rate is only considered once the category has at
    least ``MIN_APPEALS_FOR_REVIEW`` appeals, so a single false-positive
    report doesn't escalate. The child-safety rule is an exception:
    any appeal on category 1 short-circuits to ``urgent_review``
    regardless of count — on-device child-safety invariants override
    rate-based thresholds.
    """
    if total == 0:
        return "no_action"

    # Child-safety short-circuit — any appeal on CHILD_SAFETY_CATEGORY is
    # material, regardless of rate or count. On-device child-safety
    # invariants override every rate-based threshold below.
    if per_category_counts.get(CHILD_SAFETY_CATEGORY, 0) > 0:
        return "urgent_review"

    # Any category at or above the urgent threshold.
    for cat, rate in per_category_rates.items():
        count = per_category_counts.get(cat, 0)
        if count < MIN_APPEALS_FOR_REVIEW:
            continue
        if rate >= URGENT_REVIEW_APPEAL_RATE:
            return "urgent_review"

    # Any category at or above the review-suggested threshold.
    for cat, rate in per_category_rates.items():
        count = per_category_counts.get(cat, 0)
        if count < MIN_APPEALS_FOR_REVIEW:
            continue
        if rate >= REVIEW_SUGGESTED_APPEAL_RATE:
            return "review_suggested"

    return "no_action"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "AppealAggregator",
    "AppealCase",
    "AppealFlowError",
    "AppealReport",
    "CHILD_SAFETY_CATEGORY",
    "MIN_APPEALS_FOR_REVIEW",
    "RECOMMENDATION_VALUES",
    "REVIEW_SUGGESTED_APPEAL_RATE",
    "URGENT_CHILD_SAFETY_RATE",
    "URGENT_REVIEW_APPEAL_RATE",
    "USER_CONTEXT_VALUES",
]
