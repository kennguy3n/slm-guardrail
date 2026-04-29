"""Validation tests for kchat-skills/global/taxonomy.yaml."""
from __future__ import annotations

import yaml


EXPECTED_NAMES = {
    0: "SAFE",
    1: "CHILD_SAFETY",
    2: "SELF_HARM",
    3: "VIOLENCE_THREAT",
    4: "EXTREMISM",
    5: "HARASSMENT",
    6: "HATE",
    7: "SCAM_FRAUD",
    8: "MALWARE_LINK",
    9: "PRIVATE_DATA",
    10: "SEXUAL_ADULT",
    11: "DRUGS_WEAPONS",
    12: "ILLEGAL_GOODS",
    13: "MISINFORMATION_HEALTH",
    14: "MISINFORMATION_CIVIC",
    15: "COMMUNITY_RULE",
}


def test_taxonomy_is_valid_yaml(global_dir):
    with (global_dir / "taxonomy.yaml").open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    assert isinstance(loaded, dict)


def test_taxonomy_has_categories_list(taxonomy):
    assert "categories" in taxonomy
    assert isinstance(taxonomy["categories"], list)


def test_taxonomy_has_exactly_16_categories(taxonomy):
    assert len(taxonomy["categories"]) == 16


def test_taxonomy_ids_are_0_to_15(taxonomy):
    ids = [c["id"] for c in taxonomy["categories"]]
    assert sorted(ids) == list(range(16))


def test_taxonomy_no_duplicate_ids(taxonomy):
    ids = [c["id"] for c in taxonomy["categories"]]
    assert len(ids) == len(set(ids)), "duplicate taxonomy ids"


def test_taxonomy_required_fields_present(taxonomy):
    required = {"id", "name", "description", "typical_local_action"}
    for cat in taxonomy["categories"]:
        missing = required - set(cat.keys())
        assert not missing, f"category {cat.get('id')!r} missing fields: {missing}"


def test_taxonomy_id_0_is_safe(taxonomy):
    by_id = {c["id"]: c for c in taxonomy["categories"]}
    assert by_id[0]["name"] == "SAFE"


def test_taxonomy_id_1_is_child_safety(taxonomy):
    by_id = {c["id"]: c for c in taxonomy["categories"]}
    assert by_id[1]["name"] == "CHILD_SAFETY"


def test_taxonomy_names_match_architecture(taxonomy):
    by_id = {c["id"]: c["name"] for c in taxonomy["categories"]}
    assert by_id == EXPECTED_NAMES


def test_taxonomy_schema_version_present(taxonomy):
    assert taxonomy.get("schema_version") == 1
