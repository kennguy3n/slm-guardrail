"""Bias auditing framework for KChat guardrail skill packs.

Spec reference: PHASES.md Phase 6 — "Implement bias auditing for
protected-class and minority-language effects across the signed
library." ARCHITECTURE.md "Anti-Misuse Controls" lists per-class /
per-language false-positive monitoring as a shipping requirement;
this module turns a list of audit cases into a structured report
that fails the build if any group exceeds the per-class FP target
or shows excess disparity vs. the overall mean.

A *bias audit case* is a single test-suite outcome tagged with the
protected class and language under evaluation. The auditor:

* groups cases by ``protected_class`` and computes the false-positive
  rate per class — a case is a false positive when
  ``expected_category == SAFE`` (0) but ``predicted_category != 0``;
* groups cases by ``language`` and computes the same FP rate;
* compares each group's FP rate to the overall mean and flags any
  group whose rate exceeds the mean by more than ``MAX_DISPARITY``;
* fails the audit if any per-class or per-language FP rate exceeds
  ``MAX_PER_GROUP_FP_RATE`` (kept in lock-step with the
  ``minority_language_false_positive`` target from
  ``test_suite_template.yaml``) or if any disparity is flagged.

The auditor is deliberately stateless: callers feed in a list of
:class:`BiasAuditCase`, get back a :class:`BiasAuditReport`. The
compiler invokes :func:`BiasAuditor.run_audit` after the metric
validator passes, so every signed pack carries evidence that its
behaviour does not skew across protected classes or languages.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

from metric_validator import SAFE_CATEGORY  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Thresholds.
# ---------------------------------------------------------------------------
# Maximum tolerated false-positive rate for any single protected class
# or minority language. Bound to the ``minority_language_false_positive``
# shipping target so the audit can never silently pass on a value the
# metric validator would already reject.
MAX_PER_GROUP_FP_RATE: float = 0.07

# Maximum tolerated disparity (absolute) between any group's FP rate
# and the overall mean. A group whose FP rate exceeds the mean by more
# than this is flagged as a disparity violation.
MAX_DISPARITY: float = 0.05


# ---------------------------------------------------------------------------
# Dataclasses.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BiasAuditCase:
    """One row of evidence for the bias auditor.

    Attributes
    ----------
    case_id
        Stable id from the source corpus (``test_minority_language_fp``,
        per-country FP corpora, etc.). Preserved for reporting only.
    protected_class
        The protected-class id under audit (e.g. ``"race"``,
        ``"religion"``, ``"sex"``). Cases that do not bear on a
        specific protected class can use ``"unspecified"``.
    language
        IETF BCP 47 code for the language of the case (``"en"``,
        ``"hi"``, ``"ja"`` …).
    expected_category
        The taxonomy id the test-suite expects. ``SAFE_CATEGORY`` (0)
        marks the case as a SAFE / false-positive corpus member.
    predicted_category
        The taxonomy id the pipeline produced.
    tags
        Free-form tags carried over from the source case. Preserved
        for reporting; the auditor itself ignores them.
    """

    case_id: str
    protected_class: str
    language: str
    expected_category: int
    predicted_category: int
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class BiasAuditReport:
    """Structured output of a single bias audit run."""

    pack_id: str
    per_class_results: dict[str, tuple[float, int]]
    per_language_results: dict[str, tuple[float, int]]
    overall_protected_class_fp_rate: float
    overall_minority_language_fp_rate: float
    flagged_classes: tuple[str, ...]
    flagged_languages: tuple[str, ...]
    passed: bool

    def as_dict(self) -> dict:
        return {
            "pack_id": self.pack_id,
            "per_class_results": {
                k: list(v) for k, v in self.per_class_results.items()
            },
            "per_language_results": {
                k: list(v) for k, v in self.per_language_results.items()
            },
            "overall_protected_class_fp_rate": (
                self.overall_protected_class_fp_rate
            ),
            "overall_minority_language_fp_rate": (
                self.overall_minority_language_fp_rate
            ),
            "flagged_classes": list(self.flagged_classes),
            "flagged_languages": list(self.flagged_languages),
            "passed": self.passed,
        }


# ---------------------------------------------------------------------------
# Auditor.
# ---------------------------------------------------------------------------
def _is_false_positive(case: BiasAuditCase) -> bool:
    """A SAFE-expected case that the pipeline mis-classified."""
    return (
        case.expected_category == SAFE_CATEGORY
        and case.predicted_category != SAFE_CATEGORY
    )


def _group_rates(
    cases: Iterable[BiasAuditCase], key: str
) -> dict[str, tuple[float, int]]:
    """Per-group FP rate. Returns ``{group: (fp_rate, sample_size)}``."""
    buckets: dict[str, list[BiasAuditCase]] = defaultdict(list)
    for c in cases:
        buckets[getattr(c, key)].append(c)
    out: dict[str, tuple[float, int]] = {}
    for group, group_cases in buckets.items():
        # Only SAFE-expected cases contribute to a false-positive
        # rate; mis-categorising a SAFE case as anything else is the
        # FP we audit for. Cases with a non-SAFE expected category
        # are TP / TN / FN territory and don't move the FP needle.
        safe_cases = [
            c for c in group_cases if c.expected_category == SAFE_CATEGORY
        ]
        n = len(safe_cases)
        if n == 0:
            continue
        fp = sum(1 for c in safe_cases if _is_false_positive(c))
        out[group] = (fp / n, n)
    return out


@dataclass
class BiasAuditor:
    """Run protected-class and minority-language FP audits over a result set.

    The auditor is intentionally a thin orchestration layer over the
    rate-computation helpers — keeping the dataflow explicit so the
    compiler can log per-group rates without re-implementing them.
    """

    max_per_group_fp_rate: float = MAX_PER_GROUP_FP_RATE
    max_disparity: float = MAX_DISPARITY

    def __post_init__(self) -> None:
        if not (0.0 < self.max_per_group_fp_rate <= 1.0):
            raise ValueError(
                f"max_per_group_fp_rate must be in (0, 1]; got "
                f"{self.max_per_group_fp_rate}"
            )
        if not (0.0 <= self.max_disparity <= 1.0):
            raise ValueError(
                f"max_disparity must be in [0, 1]; got {self.max_disparity}"
            )

    # ------------------------------------------------------------------
    # Public API.
    # ------------------------------------------------------------------
    def audit_protected_class_effects(
        self, results: Iterable[BiasAuditCase]
    ) -> dict[str, tuple[float, int]]:
        """Per-protected-class FP rates."""
        return _group_rates(results, key="protected_class")

    def audit_minority_language_effects(
        self, results: Iterable[BiasAuditCase]
    ) -> dict[str, tuple[float, int]]:
        """Per-language FP rates."""
        return _group_rates(results, key="language")

    def audit_disparity(
        self,
        per_group_rates: dict[str, tuple[float, int]],
        max_disparity: Optional[float] = None,
    ) -> list[str]:
        """Return groups whose FP rate exceeds the mean by > max_disparity."""
        if not per_group_rates:
            return []
        ceiling = (
            self.max_disparity if max_disparity is None else max_disparity
        )
        rates = [r for r, _n in per_group_rates.values()]
        mean = sum(rates) / len(rates)
        flagged = [
            group
            for group, (rate, _n) in per_group_rates.items()
            if rate - mean > ceiling
        ]
        # Stable order — easiest to reason about in failure messages.
        return sorted(flagged)

    def run_audit(
        self,
        results: Iterable[BiasAuditCase],
        pack_id: str,
    ) -> BiasAuditReport:
        """Full audit producing a :class:`BiasAuditReport`."""
        # Materialise once; the helpers iterate twice.
        cases = list(results)

        per_class = self.audit_protected_class_effects(cases)
        per_language = self.audit_minority_language_effects(cases)

        flagged_classes_disparity = set(self.audit_disparity(per_class))
        flagged_languages_disparity = set(self.audit_disparity(per_language))

        # Per-group ceiling violations (FP rate above max_per_group_fp_rate).
        flagged_classes_ceiling = {
            group
            for group, (rate, _n) in per_class.items()
            if rate > self.max_per_group_fp_rate
        }
        flagged_languages_ceiling = {
            group
            for group, (rate, _n) in per_language.items()
            if rate > self.max_per_group_fp_rate
        }

        flagged_classes = tuple(
            sorted(flagged_classes_disparity | flagged_classes_ceiling)
        )
        flagged_languages = tuple(
            sorted(flagged_languages_disparity | flagged_languages_ceiling)
        )

        overall_class = (
            sum(rate for rate, _n in per_class.values()) / len(per_class)
            if per_class
            else 0.0
        )
        overall_language = (
            sum(rate for rate, _n in per_language.values()) / len(per_language)
            if per_language
            else 0.0
        )

        return BiasAuditReport(
            pack_id=pack_id,
            per_class_results=per_class,
            per_language_results=per_language,
            overall_protected_class_fp_rate=overall_class,
            overall_minority_language_fp_rate=overall_language,
            flagged_classes=flagged_classes,
            flagged_languages=flagged_languages,
            passed=not (flagged_classes or flagged_languages),
        )


__all__ = [
    "BiasAuditCase",
    "BiasAuditReport",
    "BiasAuditor",
    "MAX_DISPARITY",
    "MAX_PER_GROUP_FP_RATE",
]
