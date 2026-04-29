"""Validation tests for kchat-skills/global/output_schema.json."""
from __future__ import annotations

import copy
import json

import jsonschema
import pytest
from jsonschema import Draft7Validator


# Phishing example output from ARCHITECTURE.md "Output Schema" section.
KNOWN_GOOD = {
    "severity": 3,
    "category": 7,
    "confidence": 0.81,
    "actions": {
        "label_only": False,
        "warn": True,
        "strong_warn": False,
        "critical_intervention": False,
        "suggest_redact": False,
    },
    "reason_codes": ["URL_RISK", "SCAM_PATTERN"],
    "rationale_id": "scam_credential_phish_v1",
    "resource_link_id": "kchat_help_phishing_v1",
    "counter_updates": [
        {"counter_id": "group_scam_links_24h", "delta": 1}
    ],
}


def test_output_schema_is_valid_json(global_dir):
    with (global_dir / "output_schema.json").open("r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert isinstance(loaded, dict)


def test_output_schema_is_a_valid_json_schema(output_schema):
    # Will raise jsonschema.SchemaError if not a valid Draft 7 schema.
    Draft7Validator.check_schema(output_schema)


def test_output_schema_required_fields(output_schema):
    required = set(output_schema.get("required", []))
    assert required == {
        "severity",
        "category",
        "confidence",
        "actions",
        "reason_codes",
        "rationale_id",
    }


def test_output_schema_validates_known_good_example(output_schema):
    jsonschema.validate(instance=KNOWN_GOOD, schema=output_schema)


def test_output_schema_rejects_missing_required_fields(output_schema):
    bad = copy.deepcopy(KNOWN_GOOD)
    del bad["rationale_id"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=output_schema)


def test_output_schema_rejects_severity_out_of_range(output_schema):
    bad = copy.deepcopy(KNOWN_GOOD)
    bad["severity"] = 9
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=output_schema)


def test_output_schema_rejects_category_out_of_range(output_schema):
    bad = copy.deepcopy(KNOWN_GOOD)
    bad["category"] = 99
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=output_schema)


def test_output_schema_rejects_confidence_out_of_range(output_schema):
    bad = copy.deepcopy(KNOWN_GOOD)
    bad["confidence"] = 1.5
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=output_schema)


def test_output_schema_rejects_unknown_reason_code(output_schema):
    bad = copy.deepcopy(KNOWN_GOOD)
    bad["reason_codes"] = ["NOT_A_REAL_CODE"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=output_schema)


def test_output_schema_rejects_missing_action_field(output_schema):
    bad = copy.deepcopy(KNOWN_GOOD)
    del bad["actions"]["suggest_redact"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=bad, schema=output_schema)


def test_output_schema_allows_null_resource_link_id(output_schema):
    ok = copy.deepcopy(KNOWN_GOOD)
    ok["resource_link_id"] = None
    jsonschema.validate(instance=ok, schema=output_schema)
