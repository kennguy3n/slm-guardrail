"""Validation tests for the 8 community overlays in kchat-skills/communities/.

See ARCHITECTURE.md "Community Overlay Template" (lines 491-535).
"""
from __future__ import annotations

import yaml
import pytest

from .conftest import COMMUNITY_FILES, COMMUNITIES_DIR


REQUIRED_KEYS = {
    "skill_id",
    "parent",
    "schema_version",
    "signers",
    "community_profile",
    "rules",
}
REQUIRED_PROFILE_KEYS = {"kind", "age_mode", "visibility", "set_by"}
VALID_AGE_MODES = {"minor_present", "mixed_age", "adult_only"}
VALID_ACTIONS = {"label_only", "warn", "strong_warn", "block"}


@pytest.mark.parametrize("name", COMMUNITY_FILES)
def test_community_yaml_is_valid(name):
    path = COMMUNITIES_DIR / name
    with path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    assert isinstance(loaded, dict), f"{name} must parse to a mapping"


@pytest.mark.parametrize("name", COMMUNITY_FILES)
def test_community_required_keys(community_overlays, name):
    overlay = community_overlays[name]
    missing = REQUIRED_KEYS - set(overlay.keys())
    assert not missing, f"{name} missing required keys: {missing}"


@pytest.mark.parametrize("name", COMMUNITY_FILES)
def test_community_parent_is_global_baseline(community_overlays, name):
    assert community_overlays[name]["parent"] == "kchat.global.guardrail.baseline"


@pytest.mark.parametrize("name", COMMUNITY_FILES)
def test_community_schema_version_is_1(community_overlays, name):
    assert community_overlays[name]["schema_version"] == 1


@pytest.mark.parametrize("name", COMMUNITY_FILES)
def test_community_signers_includes_trust_and_safety(community_overlays, name):
    signers = community_overlays[name]["signers"]
    assert isinstance(signers, list)
    assert "trust_and_safety" in signers


@pytest.mark.parametrize("name", COMMUNITY_FILES)
def test_community_profile_required_fields(community_overlays, name):
    profile = community_overlays[name]["community_profile"]
    missing = REQUIRED_PROFILE_KEYS - set(profile.keys())
    assert not missing, f"{name} community_profile missing: {missing}"


@pytest.mark.parametrize("name", COMMUNITY_FILES)
def test_community_age_mode_valid(community_overlays, name):
    age_mode = community_overlays[name]["community_profile"]["age_mode"]
    assert age_mode in VALID_AGE_MODES, (
        f"{name} has invalid age_mode {age_mode!r}"
    )


@pytest.mark.parametrize("name", COMMUNITY_FILES)
def test_community_skill_id_format(community_overlays, name):
    skill_id = community_overlays[name]["skill_id"]
    assert skill_id.startswith("kchat.community.")
    assert skill_id.endswith(".guardrail.v1")


@pytest.mark.parametrize("name", COMMUNITY_FILES)
def test_community_rules_categories_in_taxonomy_range(community_overlays, name):
    for rule in community_overlays[name]["rules"]:
        cat = rule.get("category")
        assert isinstance(cat, int), f"{name}: rule.category must be int"
        assert 0 <= cat <= 15, (
            f"{name}: rule.category={cat} outside 0..15 taxonomy range"
        )


@pytest.mark.parametrize("name", COMMUNITY_FILES)
def test_community_rule_actions_valid(community_overlays, name):
    for rule in community_overlays[name]["rules"]:
        # rule_set entries (community-rule sub-rules) carry their own
        # action; otherwise the top-level rule has an `action`.
        if "action" in rule:
            assert rule["action"] in VALID_ACTIONS, (
                f"{name}: invalid action {rule['action']!r}"
            )
        if "rule_set" in rule:
            for sub in rule["rule_set"]:
                assert sub["action"] in VALID_ACTIONS


# --- Specific overlays ----------------------------------------------------


def _find_rule(overlay: dict, category: int) -> dict | None:
    for r in overlay["rules"]:
        if r.get("category") == category:
            return r
    return None


def test_school_age_mode_minor_present_and_blocks_sexual_adult(community_overlays):
    school = community_overlays["school.yaml"]
    assert school["community_profile"]["age_mode"] == "minor_present"
    sexual_rule = _find_rule(school, 10)
    assert sexual_rule is not None, "school.yaml must define a SEXUAL_ADULT rule"
    assert sexual_rule["action"] == "block"


def test_adult_only_age_mode(community_overlays):
    adult_only = community_overlays["adult_only.yaml"]
    assert adult_only["community_profile"]["age_mode"] == "adult_only"


def test_health_support_loosens_self_harm(community_overlays):
    health = community_overlays["health_support.yaml"]
    self_harm = _find_rule(health, 2)
    assert self_harm is not None
    assert self_harm["action"] == "label_only"


def test_workplace_has_scam_links_counter(community_overlays):
    workplace = community_overlays["workplace.yaml"]
    counters = workplace.get("group_risk_counters", [])
    counter_ids = {c["counter_id"] for c in counters}
    assert "group_scam_links_24h" in counter_ids


def test_marketplace_tightens_scam_and_illegal_goods(community_overlays):
    marketplace = community_overlays["marketplace.yaml"]
    scam = _find_rule(marketplace, 7)
    illegal = _find_rule(marketplace, 12)
    drugs = _find_rule(marketplace, 11)
    assert scam is not None and scam["action"] == "strong_warn"
    assert illegal is not None and illegal["action"] == "strong_warn"
    assert drugs is not None and drugs["action"] == "strong_warn"


def test_political_tightens_civic_misinfo(community_overlays):
    political = community_overlays["political.yaml"]
    assert political["community_profile"]["age_mode"] == "adult_only"
    civic = _find_rule(political, 14)
    assert civic is not None and civic["action"] == "warn"


def test_gaming_has_violence_threats_counter(community_overlays):
    gaming = community_overlays["gaming.yaml"]
    counters = gaming.get("group_risk_counters", [])
    counter_ids = {c["counter_id"] for c in counters}
    assert "group_violence_threats_7d" in counter_ids


def test_family_age_mode_mixed_and_strong_warn_sexual_adult(community_overlays):
    family = community_overlays["family.yaml"]
    assert family["community_profile"]["age_mode"] == "mixed_age"
    sex = _find_rule(family, 10)
    assert sex is not None and sex["action"] == "strong_warn"


# --- Template -------------------------------------------------------------


def test_template_overlay_has_required_keys(community_template):
    missing = REQUIRED_KEYS - set(community_template.keys())
    assert not missing, f"template overlay.yaml missing: {missing}"
