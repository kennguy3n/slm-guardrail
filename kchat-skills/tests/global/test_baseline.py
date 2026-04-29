"""Validation tests for kchat-skills/global/baseline.yaml (Phase 0 stub)."""
from __future__ import annotations

import yaml


REQUIRED_TOP_LEVEL_KEYS = {
    "skill_id",
    "schema_version",
    "decision_policy",
    "skill_selection",
}


def test_baseline_is_valid_yaml(global_dir):
    with (global_dir / "baseline.yaml").open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    assert isinstance(loaded, dict)


def test_baseline_required_top_level_keys(baseline):
    missing = REQUIRED_TOP_LEVEL_KEYS - set(baseline.keys())
    assert not missing, f"baseline.yaml missing top-level keys: {missing}"


def test_baseline_skill_id(baseline):
    assert baseline["skill_id"] == "kchat.global.guardrail.baseline"


def test_baseline_schema_version(baseline):
    assert baseline["schema_version"] == 1


def test_baseline_marked_as_stub(baseline):
    # Phase 0 deliverable is explicitly a stub; Phase 1 completes it.
    assert baseline.get("stub") is True
    assert baseline.get("phase") == 0


def test_baseline_decision_policy_thresholds(baseline):
    thresholds = baseline["decision_policy"]["thresholds"]
    assert thresholds["label_only"] == 0.45
    assert thresholds["warn"] == 0.62
    assert thresholds["strong_warn"] == 0.78
    assert thresholds["critical_intervention"] == 0.85


def test_baseline_thresholds_are_monotonically_increasing(baseline):
    t = baseline["decision_policy"]["thresholds"]
    assert t["label_only"] < t["warn"] < t["strong_warn"] < t["critical_intervention"]


def test_baseline_skill_selection_block(baseline):
    sel = baseline["skill_selection"]
    assert "preferred_inputs" in sel
    assert "avoid_inputs" in sel
    assert "conflict_resolution" in sel
    # Anti-misuse: location/identity inference must be in avoid_inputs.
    assert "gps_location" in sel["avoid_inputs"]
    assert "ip_geolocation" in sel["avoid_inputs"]
    assert "inferred_nationality" in sel["avoid_inputs"]


def test_baseline_skill_selection_conflict_resolution(baseline):
    cr = baseline["skill_selection"]["conflict_resolution"]
    assert cr["severity"]["rule"] == "take_max"
    assert cr["privacy_rules"]["rule"] == "immutable"
    assert cr["child_safety"]["rule"] == "floor_5"


def test_baseline_child_safety_severity_floor_5(baseline):
    csp = baseline.get("child_safety_policy")
    assert csp is not None
    assert csp["severity_floor"] == 5
    assert csp["priority"] == "highest"


def test_baseline_references_phase_0_artifacts(baseline):
    refs = baseline.get("references", {})
    # Must reference taxonomy, severity, output_schema (already authored)
    # plus local_signal_schema and privacy_contract (Phase 0 follow-up).
    for key in (
        "taxonomy",
        "severity",
        "output_schema",
        "local_signal_schema",
        "privacy_contract",
    ):
        assert key in refs, f"baseline.references missing {key!r}"
