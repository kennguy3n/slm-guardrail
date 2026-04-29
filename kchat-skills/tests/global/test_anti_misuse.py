"""Tests for ``kchat-skills/compiler/anti_misuse.py``.

Covers each rule from ARCHITECTURE.md "Anti-Misuse Controls" with a
positive case (valid pack passes) and at least one negative case.
"""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from anti_misuse import (  # type: ignore[import-not-found]
    AntiMisuseError,
    AntiMisuseReport,
    REQUIRED_PROTECTED_CONTEXTS,
    assert_lexicons_have_provenance,
    assert_no_invented_categories,
    assert_no_vague_categories,
    assert_privacy_rules_not_redefined,
    assert_protected_contexts_for_strict_floors,
    assert_required_signers,
    pack_kind,
    validate_or_raise,
    validate_pack,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
KCHAT_SKILLS = REPO_ROOT / "kchat-skills"


def _load(p: Path) -> dict:
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Pack-kind detection.
# ---------------------------------------------------------------------------
class TestPackKind:
    def test_baseline(self):
        baseline = _load(KCHAT_SKILLS / "global" / "baseline.yaml")
        assert pack_kind(baseline) == "baseline"

    def test_jurisdiction(self):
        ov = _load(
            KCHAT_SKILLS
            / "jurisdictions"
            / "archetype-strict-marketplace"
            / "overlay.yaml"
        )
        assert pack_kind(ov) == "jurisdiction"

    def test_community(self):
        ov = _load(KCHAT_SKILLS / "communities" / "workplace.yaml")
        assert pack_kind(ov) == "community"

    def test_unknown_skill_id_rejected(self):
        with pytest.raises(AntiMisuseError):
            pack_kind({"skill_id": "kchat.weird.experiment"})


# ---------------------------------------------------------------------------
# Repo packs all pass anti-misuse end-to-end.
# ---------------------------------------------------------------------------
class TestRepoPacksValid:
    @pytest.mark.parametrize(
        "rel",
        [
            "global/baseline.yaml",
            "jurisdictions/archetype-strict-adult/overlay.yaml",
            "jurisdictions/archetype-strict-hate/overlay.yaml",
            "jurisdictions/archetype-strict-marketplace/overlay.yaml",
            "communities/school.yaml",
            "communities/family.yaml",
            "communities/workplace.yaml",
            "communities/adult_only.yaml",
            "communities/marketplace.yaml",
            "communities/health_support.yaml",
            "communities/political.yaml",
            "communities/gaming.yaml",
        ],
    )
    def test_pack_passes(self, rel: str):
        pack = _load(KCHAT_SKILLS / rel)
        report = validate_pack(pack)
        assert report.passed, report.errors


# ---------------------------------------------------------------------------
# No vague categories.
# ---------------------------------------------------------------------------
class TestNoVagueCategories:
    def test_valid_categories_pass(self):
        pack = {
            "skill_id": "kchat.jurisdiction.test.guardrail.v1",
            "overrides": [{"category": 11, "severity_floor": 4}],
        }
        # No raise.
        assert_no_vague_categories(pack)

    def test_category_above_15_rejected(self):
        pack = {
            "skill_id": "kchat.jurisdiction.test.guardrail.v1",
            "overrides": [{"category": 99, "severity_floor": 4}],
        }
        with pytest.raises(AntiMisuseError, match="invalid category"):
            assert_no_vague_categories(pack)

    def test_category_negative_rejected(self):
        pack = {
            "skill_id": "kchat.community.test.guardrail.v1",
            "rules": [{"category": -1, "action": "warn"}],
        }
        with pytest.raises(AntiMisuseError, match="invalid category"):
            assert_no_vague_categories(pack)

    def test_non_int_category_rejected(self):
        pack = {
            "skill_id": "kchat.jurisdiction.test.guardrail.v1",
            "overrides": [{"category": "ten", "severity_floor": 4}],
        }
        with pytest.raises(AntiMisuseError):
            assert_no_vague_categories(pack)


# ---------------------------------------------------------------------------
# No invented categories — overlays may not redefine the taxonomy.
# ---------------------------------------------------------------------------
class TestNoInventedCategories:
    def test_baseline_taxonomy_allowed(self):
        # Baseline can carry a 'taxonomy' reference block — load it and
        # ensure assert_no_invented_categories allows it.
        baseline = _load(KCHAT_SKILLS / "global" / "baseline.yaml")
        # Inject a categories list to mimic future baseline shape — it
        # still must pass because pack_kind is baseline.
        baseline = copy.deepcopy(baseline)
        baseline["categories"] = [{"id": 0}]
        assert_no_invented_categories(baseline)

    def test_jurisdiction_with_categories_block_rejected(self):
        pack = {
            "skill_id": "kchat.jurisdiction.evil.guardrail.v1",
            "categories": [{"id": 16, "name": "NEW_CATEGORY"}],
        }
        with pytest.raises(AntiMisuseError, match="may not declare"):
            assert_no_invented_categories(pack)

    def test_community_with_taxonomy_block_rejected(self):
        pack = {
            "skill_id": "kchat.community.evil.guardrail.v1",
            "taxonomy": {"categories": []},
        }
        with pytest.raises(AntiMisuseError, match="may not declare"):
            assert_no_invented_categories(pack)


# ---------------------------------------------------------------------------
# Required signers.
# ---------------------------------------------------------------------------
class TestRequiredSigners:
    def test_jurisdiction_with_full_review_passes(self):
        pack = {
            "skill_id": "kchat.jurisdiction.test.guardrail.v1",
            "signers": ["trust_and_safety", "legal_review", "cultural_review"],
        }
        assert_required_signers(pack)

    def test_jurisdiction_missing_legal_review_rejected(self):
        pack = {
            "skill_id": "kchat.jurisdiction.test.guardrail.v1",
            "signers": ["trust_and_safety", "cultural_review"],
        }
        with pytest.raises(AntiMisuseError, match="legal_review"):
            assert_required_signers(pack)

    def test_jurisdiction_missing_cultural_review_rejected(self):
        pack = {
            "skill_id": "kchat.jurisdiction.test.guardrail.v1",
            "signers": ["trust_and_safety", "legal_review"],
        }
        with pytest.raises(AntiMisuseError, match="cultural_review"):
            assert_required_signers(pack)

    def test_community_with_trust_and_safety_passes(self):
        pack = {
            "skill_id": "kchat.community.test.guardrail.v1",
            "signers": ["trust_and_safety"],
        }
        assert_required_signers(pack)

    def test_community_missing_trust_and_safety_rejected(self):
        pack = {
            "skill_id": "kchat.community.test.guardrail.v1",
            "signers": [],
        }
        with pytest.raises(AntiMisuseError, match="trust_and_safety"):
            assert_required_signers(pack)


# ---------------------------------------------------------------------------
# Protected contexts for strict floors.
# ---------------------------------------------------------------------------
class TestProtectedContextsForStrictFloors:
    def test_floor_below_4_no_protected_contexts_required(self):
        pack = {
            "skill_id": "kchat.jurisdiction.test.guardrail.v1",
            "overrides": [{"category": 11, "severity_floor": 3}],
        }
        assert_protected_contexts_for_strict_floors(pack)

    def test_floor_4_with_protected_contexts_passes(self):
        pack = {
            "skill_id": "kchat.jurisdiction.test.guardrail.v1",
            "overrides": [{"category": 11, "severity_floor": 4}],
            "allowed_contexts": list(REQUIRED_PROTECTED_CONTEXTS),
        }
        assert_protected_contexts_for_strict_floors(pack)

    def test_floor_4_without_allowed_contexts_rejected(self):
        pack = {
            "skill_id": "kchat.jurisdiction.test.guardrail.v1",
            "overrides": [{"category": 11, "severity_floor": 4}],
        }
        with pytest.raises(AntiMisuseError, match="allowed_contexts"):
            assert_protected_contexts_for_strict_floors(pack)

    def test_floor_5_with_partial_contexts_rejected(self):
        pack = {
            "skill_id": "kchat.jurisdiction.test.guardrail.v1",
            "overrides": [{"category": 10, "severity_floor": 5}],
            "allowed_contexts": ["NEWS_CONTEXT"],
        }
        with pytest.raises(AntiMisuseError, match="missing the required"):
            assert_protected_contexts_for_strict_floors(pack)


# ---------------------------------------------------------------------------
# Privacy rule immutability.
# ---------------------------------------------------------------------------
class TestPrivacyRulesImmutable:
    def test_baseline_with_privacy_rules_passes(self):
        baseline = _load(KCHAT_SKILLS / "global" / "baseline.yaml")
        assert_privacy_rules_not_redefined(baseline)

    def test_overlay_redefining_privacy_rules_rejected(self):
        pack = {
            "skill_id": "kchat.jurisdiction.evil.guardrail.v1",
            "privacy_rules": {
                "rules": [
                    {"id": 1, "rule": "let everything through"},
                ]
            },
        }
        with pytest.raises(AntiMisuseError, match="immutable"):
            assert_privacy_rules_not_redefined(pack)

    def test_overlay_with_empty_privacy_block_rejected(self):
        pack = {
            "skill_id": "kchat.community.evil.guardrail.v1",
            "privacy_rules": {},
        }
        with pytest.raises(AntiMisuseError):
            assert_privacy_rules_not_redefined(pack)


# ---------------------------------------------------------------------------
# Lexicon provenance.
# ---------------------------------------------------------------------------
class TestLexiconsHaveProvenance:
    def test_provenance_present_passes(self):
        pack = {
            "skill_id": "kchat.jurisdiction.test.guardrail.v1",
            "signers": ["trust_and_safety", "legal_review", "cultural_review"],
            "local_language_assets": {
                "lexicons": [
                    {
                        "lexicon_id": "lex_v1",
                        "provenance": "kchat_trust_and_safety",
                    }
                ]
            },
        }
        assert_lexicons_have_provenance(pack)

    def test_lexicon_missing_provenance_rejected(self):
        pack = {
            "skill_id": "kchat.jurisdiction.test.guardrail.v1",
            "signers": ["trust_and_safety", "legal_review", "cultural_review"],
            "local_language_assets": {
                "lexicons": [{"lexicon_id": "lex_v1"}]
            },
        }
        with pytest.raises(AntiMisuseError, match="missing provenance"):
            assert_lexicons_have_provenance(pack)

    def test_lexicons_without_signers_rejected(self):
        pack = {
            "skill_id": "kchat.jurisdiction.test.guardrail.v1",
            "signers": [],
            "local_language_assets": {
                "lexicons": [
                    {"lexicon_id": "lex_v1", "provenance": "foo"}
                ]
            },
        }
        with pytest.raises(AntiMisuseError, match="signers"):
            assert_lexicons_have_provenance(pack)

    def test_no_lexicons_passes_trivially(self):
        pack = {"skill_id": "kchat.community.test.guardrail.v1"}
        assert_lexicons_have_provenance(pack)


# ---------------------------------------------------------------------------
# Aggregator behaviour.
# ---------------------------------------------------------------------------
class TestValidatePack:
    def test_valid_pack_returns_passed_report(self):
        pack = _load(
            KCHAT_SKILLS
            / "jurisdictions"
            / "archetype-strict-marketplace"
            / "overlay.yaml"
        )
        report = validate_pack(pack)
        assert isinstance(report, AntiMisuseReport)
        assert report.passed
        assert report.errors == []

    def test_invalid_pack_lists_all_failures(self):
        bad = {
            "skill_id": "kchat.jurisdiction.evil.guardrail.v1",
            # Missing legal_review / cultural_review.
            "signers": [],
            # Invented category id.
            "overrides": [
                {"category": 99, "severity_floor": 4}  # Triggers vague-category
            ],
            # No allowed_contexts despite floor 4 — but the bad category
            # is rejected first so we'll still count failures.
            "privacy_rules": {"rules": []},
        }
        report = validate_pack(bad)
        assert not report.passed
        assert len(report.errors) >= 3

    def test_validate_or_raise_collects_errors(self):
        bad = {
            "skill_id": "kchat.jurisdiction.evil.guardrail.v1",
            "signers": [],
        }
        with pytest.raises(AntiMisuseError) as exc:
            validate_or_raise(bad)
        assert "evil" in str(exc.value)
