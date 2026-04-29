"""Validation tests for kchat-skills/global/baseline.yaml (Phase 1 complete)."""
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


def test_baseline_is_complete(baseline):
    # Phase 1 completes the baseline. The stub flag must be absent or
    # explicitly False, the phase must be 1, and the status must be
    # "complete".
    assert baseline.get("stub", False) is False
    assert baseline.get("phase") == 1
    assert baseline.get("status") == "complete"


def test_baseline_privacy_rules_block_present(baseline):
    pr = baseline.get("privacy_rules")
    assert pr is not None
    # Either the contract is referenced or all 8 rules are inlined.
    assert pr.get("immutable") is True
    assert "contract_ref" in pr
    rules = pr.get("rules", [])
    assert isinstance(rules, list)
    # Inline rules must enumerate all 8 rule ids 1..8.
    ids = sorted(r["id"] for r in rules)
    assert ids == [1, 2, 3, 4, 5, 6, 7, 8]


def test_baseline_input_contract_references_local_signal_schema(baseline):
    ic = baseline.get("input_contract")
    assert ic is not None
    assert ic["schema_id"] == "kchat.guardrail.local_signal.v1"
    required = set(ic.get("required_blocks", []))
    assert required == {"message", "context", "local_signals", "constraints"}


def test_baseline_references_runtime_instruction_and_compiled_prompt_format(baseline):
    refs = baseline.get("references", {})
    assert "runtime_instruction" in refs
    assert "compiled_prompt_format" in refs


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
    # Must reference all five Phase 0 structural primitives.
    for key in (
        "taxonomy",
        "severity",
        "output_schema",
        "local_signal_schema",
        "privacy_contract",
    ):
        assert key in refs, f"baseline.references missing {key!r}"
