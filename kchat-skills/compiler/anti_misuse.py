"""Anti-misuse validation rules for KChat guardrail skill packs.

Spec reference: ARCHITECTURE.md "Anti-Misuse Controls" (lines 716-748)
and PHASES.md Phase 4. Every rule below is a class of mistake that
would let a signed pack regress safety, so the compiler refuses to
sign any pack that fails one.

The validator distinguishes three pack kinds:

* **baseline**  — ``kchat.global.…``. Owns privacy rules; no overlay
  may redefine them.
* **jurisdiction**  — ``kchat.jurisdiction.…``. Required: ``signers``
  include both ``legal_review`` and ``cultural_review``. May raise
  severity floors but may not invent new categories.
* **community**  — ``kchat.community.…``. Required: ``signers``
  include ``trust_and_safety``.

Returned errors carry both a pack id and a human-readable message so
the compiler CLI can surface them to authors directly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


# Global taxonomy id range — the closed enum (0..15) defined in
# kchat-skills/global/taxonomy.yaml.
TAXONOMY_MIN = 0
TAXONOMY_MAX = 15

# Severity floors at or above this value require explicit protected-
# context handling per ARCHITECTURE.md anti_misuse_controls.protected_contexts.
PROTECTED_CONTEXT_REQUIRED_SEVERITY = 4

# Minimum protected-context reason codes a strict overlay must declare.
REQUIRED_PROTECTED_CONTEXTS: frozenset[str] = frozenset(
    {
        "QUOTED_SPEECH_CONTEXT",
        "NEWS_CONTEXT",
        "EDUCATION_CONTEXT",
        "COUNTERSPEECH_CONTEXT",
    }
)


class AntiMisuseError(ValueError):
    """A skill pack failed an anti-misuse validation rule."""


# ---------------------------------------------------------------------------
# Pack-kind detection.
# ---------------------------------------------------------------------------
_BASELINE_RE = re.compile(r"^kchat\.global\.")
_JURISDICTION_RE = re.compile(r"^kchat\.jurisdiction\.")
_COMMUNITY_RE = re.compile(r"^kchat\.community\.")


def pack_kind(pack: dict[str, Any]) -> str:
    skill_id = pack.get("skill_id", "")
    if _BASELINE_RE.match(skill_id):
        return "baseline"
    if _JURISDICTION_RE.match(skill_id):
        return "jurisdiction"
    if _COMMUNITY_RE.match(skill_id):
        return "community"
    raise AntiMisuseError(
        f"unrecognised skill_id '{skill_id}' — expected kchat.global / "
        "kchat.jurisdiction / kchat.community prefix"
    )


# ---------------------------------------------------------------------------
# Individual rules.
# ---------------------------------------------------------------------------
def assert_no_vague_categories(pack: dict[str, Any]) -> None:
    """Every referenced category id must be in the closed 0..15 enum."""
    for ov in pack.get("overrides") or []:
        cat = ov.get("category")
        if not isinstance(cat, int) or not (TAXONOMY_MIN <= cat <= TAXONOMY_MAX):
            raise AntiMisuseError(
                f"pack '{pack.get('skill_id')}' references invalid category "
                f"{cat!r}; valid range is {TAXONOMY_MIN}..{TAXONOMY_MAX}"
            )
    for r in pack.get("rules") or []:
        cat = r.get("category")
        if not isinstance(cat, int) or not (TAXONOMY_MIN <= cat <= TAXONOMY_MAX):
            raise AntiMisuseError(
                f"pack '{pack.get('skill_id')}' references invalid category "
                f"{cat!r}; valid range is {TAXONOMY_MIN}..{TAXONOMY_MAX}"
            )


def assert_no_invented_categories(pack: dict[str, Any]) -> None:
    """Reject taxonomy / categories blocks in any overlay.

    Only the global baseline may declare the taxonomy. Any overlay
    that ships its own ``taxonomy``, ``categories``, or ``new_categories``
    block is attempting to mint categories — exactly the failure
    ARCHITECTURE.md "no vague categories" guards against.
    """
    if pack_kind(pack) == "baseline":
        return
    for forbidden in ("taxonomy", "categories", "new_categories"):
        if forbidden in pack:
            raise AntiMisuseError(
                f"overlay '{pack.get('skill_id')}' may not declare a "
                f"'{forbidden}' block; only baseline owns the taxonomy"
            )


def assert_required_signers(pack: dict[str, Any]) -> None:
    """Pack-kind dictates which roles must appear in ``signers``."""
    kind = pack_kind(pack)
    signers = list(pack.get("signers") or [])
    if kind == "jurisdiction":
        missing = [
            r for r in ("legal_review", "cultural_review") if r not in signers
        ]
        if missing:
            raise AntiMisuseError(
                f"jurisdiction pack '{pack.get('skill_id')}' missing "
                f"required reviewer signers: {missing}"
            )
    if kind == "community":
        if "trust_and_safety" not in signers:
            raise AntiMisuseError(
                f"community pack '{pack.get('skill_id')}' missing "
                "required signer 'trust_and_safety'"
            )


def assert_protected_contexts_for_strict_floors(pack: dict[str, Any]) -> None:
    """Any category at floor >= 4 must declare ``allowed_contexts``."""
    strict_overrides = [
        ov
        for ov in pack.get("overrides") or []
        if int(ov.get("severity_floor", 0)) >= PROTECTED_CONTEXT_REQUIRED_SEVERITY
    ]
    if not strict_overrides:
        return
    contexts = pack.get("allowed_contexts") or []
    if not contexts:
        raise AntiMisuseError(
            f"pack '{pack.get('skill_id')}' raises severity_floor to >= "
            f"{PROTECTED_CONTEXT_REQUIRED_SEVERITY} but declares no "
            "allowed_contexts; protected-speech carve-outs are required"
        )
    missing = [c for c in REQUIRED_PROTECTED_CONTEXTS if c not in contexts]
    if missing:
        raise AntiMisuseError(
            f"pack '{pack.get('skill_id')}' allowed_contexts missing the "
            f"required protected-speech contexts: {missing}"
        )


def assert_privacy_rules_not_redefined(pack: dict[str, Any]) -> None:
    """Only the global baseline may declare ``privacy_rules``."""
    if pack_kind(pack) == "baseline":
        return
    if "privacy_rules" in pack:
        raise AntiMisuseError(
            f"overlay '{pack.get('skill_id')}' attempts to redefine "
            "privacy_rules; the 8 baseline privacy rules are immutable"
        )


def assert_lexicons_have_provenance(pack: dict[str, Any]) -> None:
    """Every lexicon declared by the pack must carry a ``provenance`` field.

    Lexicons without provenance cannot be reviewed; they are exactly
    the vector ARCHITECTURE.md anti_misuse_controls.narrowness guards
    against (``lexicons must declare provenance and reviewer``).
    """
    assets = pack.get("local_language_assets") or {}
    for lex in assets.get("lexicons") or []:
        if not lex.get("provenance"):
            raise AntiMisuseError(
                f"pack '{pack.get('skill_id')}' lexicon "
                f"'{lex.get('lexicon_id', '<unknown>')}' missing provenance"
            )
    # The pack-level ``signers`` block doubles as the reviewer for any
    # lexicons it ships. We require at least one signer to ensure the
    # lexicon authors are accountable.
    if assets.get("lexicons") and not (pack.get("signers") or []):
        raise AntiMisuseError(
            f"pack '{pack.get('skill_id')}' ships lexicons without any "
            "pack-level signers acting as reviewer"
        )


# ---------------------------------------------------------------------------
# Aggregator.
# ---------------------------------------------------------------------------
@dataclass
class AntiMisuseReport:
    """Aggregated validator output. ``passed`` ⇔ ``errors`` is empty."""

    skill_id: str
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors


def validate_pack(pack: dict[str, Any]) -> AntiMisuseReport:
    """Run every anti-misuse rule and return a :class:`AntiMisuseReport`.

    Aggregating mode: collect all rule failures rather than raising on
    the first. The compiler turns a non-empty report into a single
    :class:`AntiMisuseError` with all failures listed; this function
    returns the report directly so tests can assert on individual
    rule outcomes.
    """
    report = AntiMisuseReport(skill_id=str(pack.get("skill_id", "")))
    rules = (
        assert_no_vague_categories,
        assert_no_invented_categories,
        assert_required_signers,
        assert_protected_contexts_for_strict_floors,
        assert_privacy_rules_not_redefined,
        assert_lexicons_have_provenance,
    )
    for rule in rules:
        try:
            rule(pack)
        except AntiMisuseError as exc:
            report.errors.append(str(exc))
    return report


def validate_or_raise(pack: dict[str, Any]) -> None:
    """Run all rules; raise :class:`AntiMisuseError` if any fail."""
    report = validate_pack(pack)
    if not report.passed:
        raise AntiMisuseError(
            "anti-misuse validation failed for "
            f"'{report.skill_id}': " + "; ".join(report.errors)
        )


__all__ = [
    "AntiMisuseError",
    "AntiMisuseReport",
    "PROTECTED_CONTEXT_REQUIRED_SEVERITY",
    "REQUIRED_PROTECTED_CONTEXTS",
    "TAXONOMY_MAX",
    "TAXONOMY_MIN",
    "assert_lexicons_have_provenance",
    "assert_no_invented_categories",
    "assert_no_vague_categories",
    "assert_privacy_rules_not_redefined",
    "assert_protected_contexts_for_strict_floors",
    "assert_required_signers",
    "pack_kind",
    "validate_or_raise",
    "validate_pack",
]
