"""Validation tests for kchat-skills/global/privacy_contract.yaml.

The privacy contract encodes the eight non-negotiable privacy rules from
PROPOSAL.md and the plaintext-handling / allowed-outputs / forbidden-outputs
blocks from ARCHITECTURE.md "Privacy Architecture".
"""
from __future__ import annotations

import yaml


REQUIRED_RULE_FIELDS = {"id", "rule", "enforceable_constraint"}
EXPECTED_CONTRACT_ID = "kchat.guardrail.privacy_contract.v1"


def test_privacy_contract_is_valid_yaml(global_dir):
    with (global_dir / "privacy_contract.yaml").open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    assert isinstance(loaded, dict)


def test_privacy_contract_has_contract_id(privacy_contract):
    assert privacy_contract["contract_id"] == EXPECTED_CONTRACT_ID


def test_privacy_contract_immutable_flag(privacy_contract):
    assert privacy_contract.get("immutable") is True


def test_privacy_contract_schema_version(privacy_contract):
    assert privacy_contract["schema_version"] == 1


def test_privacy_contract_has_eight_rules(privacy_contract):
    rules = privacy_contract["rules"]
    assert isinstance(rules, list)
    assert len(rules) == 8


def test_privacy_contract_rule_ids_are_1_to_8_unique(privacy_contract):
    ids = [r["id"] for r in privacy_contract["rules"]]
    assert sorted(ids) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert len(set(ids)) == 8


def test_privacy_contract_rule_required_fields(privacy_contract):
    for rule in privacy_contract["rules"]:
        missing = REQUIRED_RULE_FIELDS - set(rule.keys())
        assert not missing, f"rule {rule.get('id')!r} missing fields: {missing}"
        # rule and enforceable_constraint must be non-empty strings
        assert isinstance(rule["rule"], str) and rule["rule"].strip()
        assert (
            isinstance(rule["enforceable_constraint"], str)
            and rule["enforceable_constraint"].strip()
        )


def test_privacy_contract_has_plaintext_handling(privacy_contract):
    assert "plaintext_handling" in privacy_contract
    items = privacy_contract["plaintext_handling"]
    assert isinstance(items, list) and len(items) >= 1


def test_privacy_contract_has_allowed_outputs(privacy_contract):
    assert "allowed_outputs" in privacy_contract
    items = privacy_contract["allowed_outputs"]
    assert isinstance(items, list) and len(items) >= 1


def test_privacy_contract_has_forbidden_outputs(privacy_contract):
    assert "forbidden_outputs" in privacy_contract
    items = privacy_contract["forbidden_outputs"]
    assert isinstance(items, list) and len(items) >= 1


def test_privacy_contract_forbids_message_text_in_outputs(privacy_contract):
    forbidden = " ".join(privacy_contract["forbidden_outputs"]).lower()
    assert "original message text" in forbidden
    assert "8 tokens" in forbidden or "8 contiguous tokens" in forbidden


def test_privacy_contract_first_rule_is_on_device(privacy_contract):
    rule_one = privacy_contract["rules"][0]
    assert rule_one["id"] == 1
    text = rule_one["rule"].lower()
    assert "decrypted" in text
    assert "this device" in text
