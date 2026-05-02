"""Hard-coded threshold policy enforcement for the on-device pipeline.

Spec reference: ARCHITECTURE.md "Decision Policy" (lines 353-373) and
PHASES.md Phase 3 — "Implement the decision policy with hard-coded
confidence thresholds (`label_only=0.45`, `warn=0.62`,
`strong_warn=0.78`, `critical_intervention=0.85`) and uncertainty
handling."

The thresholds are **immutable**. The encoder classifier cannot override
them — any classifier output that asserts an action inconsistent with
its confidence is re-coerced by :meth:`ThresholdPolicy.apply` before the output leaves
the device.

Child-safety floor: any positive CHILD_SAFETY signal at confidence
``>= 0.45`` is pinned to severity 5 with ``critical_intervention=True``
per ARCHITECTURE.md line 373.

Protected-speech demotion: when ``reason_codes`` carries one of
``NEWS_CONTEXT`` / ``EDUCATION_CONTEXT`` / ``COUNTERSPEECH_CONTEXT`` /
``QUOTED_SPEECH_CONTEXT`` (forwarded from ``local_signals.context_hints``
by the classifier) and the verdict is non-SAFE / non-CHILD_SAFETY, the
output is demoted to SAFE — protecting news coverage, education, and
counterspeech from false positives. CHILD_SAFETY always wins because
its floor is evaluated first.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Canonical taxonomy id for CHILD_SAFETY — kept in sync with
# kchat-skills/global/taxonomy.yaml.
CHILD_SAFETY_CATEGORY = 1
SAFE_CATEGORY = 0

# Reason codes that signal the message lives in a protected-speech
# context. Any non-SAFE / non-CHILD_SAFETY verdict carrying one of
# these is demoted to SAFE by :meth:`ThresholdPolicy.apply`. Kept in
# sync with ``local_signal_schema.json`` ``context_hints`` enum.
PROTECTED_SPEECH_REASON_CODES: frozenset[str] = frozenset(
    {
        "NEWS_CONTEXT",
        "EDUCATION_CONTEXT",
        "COUNTERSPEECH_CONTEXT",
        "QUOTED_SPEECH_CONTEXT",
    }
)


@dataclass(frozen=True)
class ThresholdPolicy:
    """Immutable decision-policy enforcer.

    The four confidence thresholds are class-level constants; they
    are intentionally not configurable. Constructing a ``ThresholdPolicy``
    with different thresholds is deliberately not supported — the
    values below are the only ones the compiler accepts for a signed
    pack.
    """

    LABEL_ONLY: float = 0.45
    WARN: float = 0.62
    STRONG_WARN: float = 0.78
    CRITICAL_INTERVENTION: float = 0.85

    def __post_init__(self) -> None:
        # Refuse to instantiate with non-canonical thresholds — the
        # hard-coded values are the contract.
        if (
            self.LABEL_ONLY != 0.45
            or self.WARN != 0.62
            or self.STRONG_WARN != 0.78
            or self.CRITICAL_INTERVENTION != 0.85
        ):
            raise ValueError(
                "ThresholdPolicy thresholds are hard-coded; "
                "non-canonical values are rejected"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def apply(self, raw_output: dict[str, Any]) -> dict[str, Any]:
        """Return a policy-enforced copy of ``raw_output``.

        Rules, in order:

        1. Child-safety floor: CHILD_SAFETY with confidence ``>=
           LABEL_ONLY`` is pinned to severity 5 and
           ``critical_intervention=True``.
        2. Protected-speech demotion: any non-SAFE / non-CHILD_SAFETY
           verdict whose ``reason_codes`` include one of
           ``NEWS_CONTEXT`` / ``EDUCATION_CONTEXT`` /
           ``COUNTERSPEECH_CONTEXT`` / ``QUOTED_SPEECH_CONTEXT`` is
           demoted to SAFE. The protected-speech reason codes are
           preserved on the demoted output so reviewers can see why.
        3. Uncertainty handling: any non-zero category with confidence
           ``< LABEL_ONLY`` is coerced to SAFE (category 0, severity 0,
           all actions false).
        4. Action flags are recomputed from confidence — the
           classifier cannot assert ``warn`` at confidence 0.10.
        5. ``suggest_redact`` from the raw output is preserved (it is
           a content-type hint, not a confidence-driven flag).
        """
        out = _deepcopy_output(raw_output)
        category = int(out.get("category", SAFE_CATEGORY))
        confidence = float(out.get("confidence", 0.0))

        # Rule 1: Child-safety floor — wins over every other rule.
        if category == CHILD_SAFETY_CATEGORY and confidence >= self.LABEL_ONLY:
            out["severity"] = 5
            out["actions"] = _blank_actions(critical_intervention=True)
            reasons = set(out.get("reason_codes") or [])
            reasons.add("CHILD_SAFETY_FLOOR")
            out["reason_codes"] = sorted(reasons)
            return out

        # Rule 2: Protected-speech demotion. Applies only to non-SAFE
        # categories; CHILD_SAFETY is already handled by rule 1.
        reason_codes_in = list(out.get("reason_codes") or [])
        protected_present = [
            r for r in reason_codes_in if r in PROTECTED_SPEECH_REASON_CODES
        ]
        if category != SAFE_CATEGORY and protected_present:
            return {
                "severity": 0,
                "category": SAFE_CATEGORY,
                "confidence": confidence,
                "actions": _blank_actions(),
                "reason_codes": sorted(set(protected_present)),
                "rationale_id": "safe_protected_speech_v1",
            }

        # Rule 3: Uncertainty handling.
        if category != SAFE_CATEGORY and confidence < self.LABEL_ONLY:
            return {
                "severity": 0,
                "category": SAFE_CATEGORY,
                "confidence": confidence,
                "actions": _blank_actions(),
                "reason_codes": [],
                "rationale_id": out.get("rationale_id") or "safe_benign_v1",
            }

        # Rule 4: Re-derive action flags from confidence for non-SAFE
        # categories.
        if category != SAFE_CATEGORY:
            suggest_redact = bool(
                (out.get("actions") or {}).get("suggest_redact", False)
            )
            actions = _blank_actions()
            if confidence >= self.CRITICAL_INTERVENTION:
                actions["critical_intervention"] = True
            elif confidence >= self.STRONG_WARN:
                actions["strong_warn"] = True
            elif confidence >= self.WARN:
                actions["warn"] = True
            elif confidence >= self.LABEL_ONLY:
                actions["label_only"] = True
            actions["suggest_redact"] = suggest_redact
            out["actions"] = actions

        return out

    # ------------------------------------------------------------------
    # Tie-break helper (ARCHITECTURE.md lines 368-370: "Tied categories
    # at the same severity break in favour of the lower-numbered
    # category").
    # ------------------------------------------------------------------
    @staticmethod
    def tie_break(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """Return the tie-break winner from ``candidates``.

        The winner is the highest-severity candidate; ties are broken
        in favour of the lower-numbered taxonomy category.
        """
        if not candidates:
            raise ValueError("candidates must be non-empty")
        return min(
            candidates,
            key=lambda c: (
                -int(c.get("severity", 0)),
                int(c.get("category", 0)),
            ),
        )


def _blank_actions(
    *,
    label_only: bool = False,
    warn: bool = False,
    strong_warn: bool = False,
    critical_intervention: bool = False,
    suggest_redact: bool = False,
) -> dict[str, bool]:
    return {
        "label_only": label_only,
        "warn": warn,
        "strong_warn": strong_warn,
        "critical_intervention": critical_intervention,
        "suggest_redact": suggest_redact,
    }


def _deepcopy_output(raw: dict[str, Any]) -> dict[str, Any]:
    # Hand-rolled shallow+nested copy — the output shape is known and
    # avoids pulling copy.deepcopy for every pipeline call.
    actions = dict((raw.get("actions") or {}))
    out: dict[str, Any] = {
        "severity": int(raw.get("severity", 0)),
        "category": int(raw.get("category", SAFE_CATEGORY)),
        "confidence": float(raw.get("confidence", 0.0)),
        "actions": actions if actions else _blank_actions(),
        "reason_codes": list(raw.get("reason_codes") or []),
        "rationale_id": raw.get("rationale_id") or "safe_benign_v1",
    }
    if "resource_link_id" in raw:
        out["resource_link_id"] = raw["resource_link_id"]
    if "counter_updates" in raw:
        out["counter_updates"] = [dict(u) for u in raw["counter_updates"]]
    return out


__all__ = [
    "ThresholdPolicy",
    "CHILD_SAFETY_CATEGORY",
    "SAFE_CATEGORY",
    "PROTECTED_SPEECH_REASON_CODES",
]
