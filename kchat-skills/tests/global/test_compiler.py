"""Tests for ``kchat-skills/compiler/compiler.py``.

Covers:

* Pack loading and parsing.
* Conflict resolution (severity take_max, action most_protective).
* Compiled prompt generation matching the format reference.
* Token-budget enforcement (reject when > 1800 instruction tokens).
* Privacy-rule immutability (reject overlays redefining rules).
* CHILD_SAFETY severity-5 floor pinned regardless of overlay config.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from compiler import (  # type: ignore[import-not-found]
    CHILD_SAFETY_CATEGORY,
    MAX_INSTRUCTION_TOKENS,
    ActiveSkillBundle,
    CategoryRule,
    CompiledPrompt,
    InstructionBudgetExceeded,
    PrivacyRuleViolation,
    SkillPackCompiler,
    assert_privacy_rules_intact,
    compile_prompt,
    estimate_tokens,
    load_pack,
    parse_compiled_sections,
    resolve_active_bundle,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
KCHAT_SKILLS = REPO_ROOT / "kchat-skills"


# ---------------------------------------------------------------------------
# Loading / parsing.
# ---------------------------------------------------------------------------
class TestLoadPack:
    def test_loads_baseline(self):
        baseline = load_pack(KCHAT_SKILLS / "global" / "baseline.yaml")
        assert baseline["skill_id"] == "kchat.global.guardrail.baseline"
        assert "privacy_rules" in baseline

    def test_loads_jurisdiction(self):
        ov = load_pack(
            KCHAT_SKILLS
            / "jurisdictions"
            / "archetype-strict-marketplace"
            / "overlay.yaml"
        )
        assert (
            ov["skill_id"]
            == "kchat.jurisdiction.archetype-strict-marketplace.guardrail.v1"
        )
        assert any(o["category"] == 11 for o in ov["overrides"])

    def test_loads_community(self):
        ov = load_pack(KCHAT_SKILLS / "communities" / "workplace.yaml")
        assert ov["skill_id"] == "kchat.community.workplace.guardrail.v1"

    def test_rejects_non_mapping(self, tmp_path: Path):
        p = tmp_path / "bad.yaml"
        p.write_text("- 1\n- 2\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_pack(p)


# ---------------------------------------------------------------------------
# Conflict resolution.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def baseline_pack() -> dict:
    return load_pack(KCHAT_SKILLS / "global" / "baseline.yaml")


@pytest.fixture(scope="session")
def jur_marketplace() -> dict:
    return load_pack(
        KCHAT_SKILLS
        / "jurisdictions"
        / "archetype-strict-marketplace"
        / "overlay.yaml"
    )


@pytest.fixture(scope="session")
def jur_strict_adult() -> dict:
    return load_pack(
        KCHAT_SKILLS / "jurisdictions" / "archetype-strict-adult" / "overlay.yaml"
    )


@pytest.fixture(scope="session")
def comm_workplace() -> dict:
    return load_pack(KCHAT_SKILLS / "communities" / "workplace.yaml")


@pytest.fixture(scope="session")
def comm_school() -> dict:
    return load_pack(KCHAT_SKILLS / "communities" / "school.yaml")


class TestResolveActiveBundle:
    def test_baseline_only(self, baseline_pack):
        bundle = resolve_active_bundle(baseline=baseline_pack)
        # CHILD_SAFETY pinned at 5.
        cs = bundle.category_rules[CHILD_SAFETY_CATEGORY]
        assert cs.severity_floor == 5
        assert cs.action == "critical_intervention"
        assert bundle.allowed_contexts == []

    def test_with_jurisdiction_marketplace(
        self, baseline_pack, jur_marketplace
    ):
        bundle = resolve_active_bundle(
            baseline=baseline_pack, jurisdiction=jur_marketplace
        )
        # DRUGS_WEAPONS (11) and ILLEGAL_GOODS (12) raised to floor 4.
        assert bundle.category_rules[11].severity_floor == 4
        assert bundle.category_rules[12].severity_floor == 4
        # Strong-warn comes from severity 4 default mapping.
        assert bundle.category_rules[11].action == "strong_warn"
        assert "QUOTED_SPEECH_CONTEXT" in bundle.allowed_contexts

    def test_with_community_workplace(self, baseline_pack, comm_workplace):
        bundle = resolve_active_bundle(
            baseline=baseline_pack, community=comm_workplace
        )
        # SEXUAL_ADULT (10) → strong_warn from community.
        assert bundle.category_rules[10].action == "strong_warn"
        # HARASSMENT (5) → warn from community.
        assert bundle.category_rules[5].action == "warn"

    def test_severity_take_max_across_layers(self, baseline_pack):
        # Synthetic overlays disagreeing on the same category.
        soft = {
            "skill_id": "soft",
            "overrides": [{"category": 11, "severity_floor": 2}],
        }
        hard = {
            "skill_id": "hard",
            "overrides": [{"category": 11, "severity_floor": 4}],
        }
        bundle = resolve_active_bundle(
            baseline=baseline_pack, jurisdiction=soft, community=hard
        )
        assert bundle.category_rules[11].severity_floor == 4

    def test_action_most_protective(self, baseline_pack):
        ov = {
            "skill_id": "ov",
            "rules": [
                {"category": 5, "action": "label_only"},
                {"category": 5, "action": "strong_warn"},  # later
            ],
        }
        bundle = resolve_active_bundle(baseline=baseline_pack, community=ov)
        assert bundle.category_rules[5].action == "strong_warn"

    def test_child_safety_floor_5_preserved_under_overlay(self, baseline_pack):
        # Overlay attempts to weaken category 1 — must still be pinned to 5.
        bad = {
            "skill_id": "bad",
            "overrides": [
                {"category": 1, "severity_floor": 1},
            ],
        }
        bundle = resolve_active_bundle(
            baseline=baseline_pack, jurisdiction=bad
        )
        assert bundle.category_rules[1].severity_floor == 5
        assert bundle.category_rules[1].action == "critical_intervention"

    def test_jurisdiction_plus_community_combine(
        self, baseline_pack, jur_marketplace, comm_workplace
    ):
        bundle = resolve_active_bundle(
            baseline=baseline_pack,
            jurisdiction=jur_marketplace,
            community=comm_workplace,
        )
        # Marketplace floor on 11 stays, workplace warn on 5 stays,
        # workplace SEXUAL_ADULT strong_warn on 10 stays.
        assert bundle.category_rules[11].severity_floor == 4
        assert bundle.category_rules[5].action == "warn"
        assert bundle.category_rules[10].action == "strong_warn"


# ---------------------------------------------------------------------------
# Privacy-rule immutability.
# ---------------------------------------------------------------------------
class TestPrivacyImmutability:
    def test_overlay_without_privacy_rules_passes(self, baseline_pack):
        ov = {"skill_id": "ok"}
        # No raise.
        assert_privacy_rules_intact(baseline_pack, ov) is None

    def test_overlay_redefining_privacy_rules_rejected(self, baseline_pack):
        bad = {
            "skill_id": "evil",
            "privacy_rules": {"rules": [{"id": 99, "rule": "leak everything"}]},
        }
        with pytest.raises(PrivacyRuleViolation):
            assert_privacy_rules_intact(baseline_pack, bad)

    def test_resolve_rejects_pack_with_privacy_block(self, baseline_pack):
        bad = {
            "skill_id": "evil",
            "privacy_rules": {"immutable": False},
        }
        with pytest.raises(PrivacyRuleViolation):
            resolve_active_bundle(
                baseline=baseline_pack, jurisdiction=bad
            )


# ---------------------------------------------------------------------------
# Compiled prompt generation.
# ---------------------------------------------------------------------------
class TestCompilePrompt:
    def _compile(
        self, baseline, *, jurisdiction=None, community=None
    ) -> CompiledPrompt:
        bundle = resolve_active_bundle(
            baseline=baseline,
            jurisdiction=jurisdiction,
            community=community,
        )
        return compile_prompt(
            bundle,
            prompts_dir=KCHAT_SKILLS / "prompts",
            global_dir=KCHAT_SKILLS / "global",
        )

    def test_baseline_only_has_all_six_sections(self, baseline_pack):
        prompt = self._compile(baseline_pack)
        for marker in (
            "[INSTRUCTION]",
            "[GLOBAL_BASELINE]",
            "[JURISDICTION_OVERLAY]",
            "[COMMUNITY_OVERLAY]",
            "[INPUT]",
            "[OUTPUT]",
        ):
            assert marker in prompt.text
        assert prompt.instruction_tokens > 0
        assert prompt.instruction_tokens <= MAX_INSTRUCTION_TOKENS

    def test_baseline_jurisdiction_community_matches_reference(
        self, baseline_pack, jur_marketplace, comm_workplace
    ):
        prompt = self._compile(
            baseline_pack,
            jurisdiction=jur_marketplace,
            community=comm_workplace,
        )
        # Matches the existing reference example shape.
        assert (
            "kchat.jurisdiction.archetype-strict-marketplace.guardrail.v1"
            in prompt.text
        )
        assert "kchat.community.workplace.guardrail.v1" in prompt.text
        assert "category 11 DRUGS_WEAPONS severity_floor 4" in prompt.text
        assert "category 5 HARASSMENT action=warn" in prompt.text
        assert "group_scam_links_24h" in prompt.text

    def test_jurisdiction_overlay_section_empty_when_absent(
        self, baseline_pack, comm_workplace
    ):
        prompt = self._compile(baseline_pack, community=comm_workplace)
        sections = parse_compiled_sections(prompt.text)
        assert sections["JURISDICTION_OVERLAY"] == ""

    def test_community_overlay_section_empty_when_absent(
        self, baseline_pack, jur_marketplace
    ):
        prompt = self._compile(baseline_pack, jurisdiction=jur_marketplace)
        sections = parse_compiled_sections(prompt.text)
        assert sections["COMMUNITY_OVERLAY"] == ""

    def test_instruction_section_byte_for_byte(self, baseline_pack):
        prompt = self._compile(baseline_pack)
        runtime = (KCHAT_SKILLS / "prompts" / "runtime_instruction.txt").read_text(
            encoding="utf-8"
        )
        # Strip any trailing newlines for the comparison.
        assert runtime.strip() in prompt.text


class TestTokenBudget:
    def test_estimate_tokens_basic(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("a") == 1
        assert estimate_tokens("a" * 8) == 2
        # 4 chars/token rule means 4001 chars → 1001 tokens.
        assert estimate_tokens("a" * 4001) == 1001

    def test_compile_rejects_oversized_prompt(self, baseline_pack):
        # Force a tiny budget so even the bare instruction blows it.
        bundle = resolve_active_bundle(baseline=baseline_pack)
        with pytest.raises(InstructionBudgetExceeded):
            compile_prompt(
                bundle,
                prompts_dir=KCHAT_SKILLS / "prompts",
                global_dir=KCHAT_SKILLS / "global",
                max_instruction_tokens=10,
            )

    def test_canonical_compile_under_1800(
        self, baseline_pack, jur_marketplace, comm_workplace
    ):
        bundle = resolve_active_bundle(
            baseline=baseline_pack,
            jurisdiction=jur_marketplace,
            community=comm_workplace,
        )
        prompt = compile_prompt(
            bundle,
            prompts_dir=KCHAT_SKILLS / "prompts",
            global_dir=KCHAT_SKILLS / "global",
        )
        assert prompt.instruction_tokens < MAX_INSTRUCTION_TOKENS


# ---------------------------------------------------------------------------
# Top-level orchestrator.
# ---------------------------------------------------------------------------
class TestSkillPackCompiler:
    def test_compile_baseline_only(self):
        compiler = SkillPackCompiler(repo_root=REPO_ROOT)
        prompt = compiler.compile()
        assert "[INSTRUCTION]" in prompt.text
        assert prompt.instruction_tokens < MAX_INSTRUCTION_TOKENS

    def test_compile_with_archetype_name(self):
        compiler = SkillPackCompiler(repo_root=REPO_ROOT)
        prompt = compiler.compile(
            jurisdiction="archetype-strict-marketplace",
            community="workplace",
        )
        assert "category 11 DRUGS_WEAPONS severity_floor 4" in prompt.text
        assert "kchat.community.workplace.guardrail.v1" in prompt.text

    def test_compile_with_dict_pack(self, baseline_pack, jur_marketplace):
        compiler = SkillPackCompiler(repo_root=REPO_ROOT)
        prompt = compiler.compile(jurisdiction=jur_marketplace)
        assert (
            "kchat.jurisdiction.archetype-strict-marketplace.guardrail.v1"
            in prompt.text
        )

    def test_compile_with_school_overlay(self):
        compiler = SkillPackCompiler(repo_root=REPO_ROOT)
        prompt = compiler.compile(community="school")
        assert "kchat.community.school.guardrail.v1" in prompt.text


# ---------------------------------------------------------------------------
# parse_compiled_sections round-trip.
# ---------------------------------------------------------------------------
class TestParseSections:
    def test_round_trip(self):
        compiler = SkillPackCompiler(repo_root=REPO_ROOT)
        prompt = compiler.compile(
            jurisdiction="archetype-strict-adult", community="school"
        )
        sections = parse_compiled_sections(prompt.text)
        assert "INSTRUCTION" in sections
        assert "GLOBAL_BASELINE" in sections
        assert "JURISDICTION_OVERLAY" in sections
        assert "COMMUNITY_OVERLAY" in sections
        assert "INPUT" in sections
        assert "OUTPUT" in sections

    def test_missing_section_raises(self):
        with pytest.raises(ValueError):
            parse_compiled_sections("some random text")
