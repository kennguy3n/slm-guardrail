"""Validation tests for kchat-skills/global/severity.yaml."""
from __future__ import annotations

import yaml


def test_severity_is_valid_yaml(global_dir):
    with (global_dir / "severity.yaml").open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    assert isinstance(loaded, dict)


def test_severity_has_levels_list(severity):
    assert "levels" in severity
    assert isinstance(severity["levels"], list)


def test_severity_has_exactly_6_levels(severity):
    assert len(severity["levels"]) == 6


def test_severity_levels_are_0_to_5(severity):
    levels = [lvl["level"] for lvl in severity["levels"]]
    assert sorted(levels) == [0, 1, 2, 3, 4, 5]


def test_severity_required_fields_present(severity):
    required = {"level", "name", "meaning", "action"}
    for lvl in severity["levels"]:
        missing = required - set(lvl.keys())
        assert not missing, f"level {lvl.get('level')!r} missing fields: {missing}"


def test_severity_level_5_is_critical(severity):
    by_level = {lvl["level"]: lvl for lvl in severity["levels"]}
    assert by_level[5]["name"].lower() == "critical"


def test_severity_level_0_is_none(severity):
    by_level = {lvl["level"]: lvl for lvl in severity["levels"]}
    assert by_level[0]["name"].lower() == "none"


def test_child_safety_floor_block_present(severity):
    floor = severity.get("child_safety_floor")
    assert floor is not None, "child_safety_floor block required"
    assert floor.get("category_id") == 1
    assert floor.get("category_name") == "CHILD_SAFETY"
    assert floor.get("severity_floor") == 5
