"""Validation tests for kchat-skills/global/local_signal_schema.json.

The local-signal schema is the encoder classifier input contract: a Draft-07
JSON Schema defining the structured object the on-device runtime hands to
the classifier. See ARCHITECTURE.md "Encoder Classifier Execution Contract".
"""
from __future__ import annotations

import copy

import jsonschema
import pytest
from jsonschema import Draft7Validator


REQUIRED_TOP_LEVEL = {"message", "context", "local_signals", "constraints"}


KNOWN_GOOD_INPUT: dict = {
    "message": {
        "text": "URGENT: Your account will be suspended. Verify here: http://bank-login.example/secure",
        "lang_hint": "en",
        "has_attachment": True,
        "attachment_kinds": ["link"],
        "quoted_from_user": False,
        "is_outbound": False,
    },
    "context": {
        "group_kind": "small_group",
        "group_age_mode": "adult_only",
        "user_role": "member",
        "relationship_known": False,
        "locale": "en-US",
        "jurisdiction_id": None,
        "community_overlay_id": "kchat.community.workplace.guardrail.v1",
        "is_offline": False,
    },
    "local_signals": {
        "url_risk": 0.92,
        "pii_patterns_hit": [],
        "scam_patterns_hit": ["PHISHING_LINK", "CREDENTIAL_HARVEST"],
        "lexicon_hits": [
            {"lexicon_id": "global.scam.en.v1", "category": 7, "weight": 0.78}
        ],
        "media_descriptors": [],
    },
    "constraints": {
        "max_output_tokens": 600,
        "temperature": 0.0,
        "output_format": "json",
        "schema_id": "kchat.guardrail.output.v1",
    },
}


def test_local_signal_schema_is_valid_draft7(local_signal_schema):
    Draft7Validator.check_schema(local_signal_schema)


def test_local_signal_schema_meta(local_signal_schema):
    assert local_signal_schema["$schema"] == "http://json-schema.org/draft-07/schema#"
    assert local_signal_schema["$id"] == "kchat.guardrail.local_signal.v1"
    assert local_signal_schema["type"] == "object"


def test_local_signal_schema_required_top_level_fields(local_signal_schema):
    required = set(local_signal_schema.get("required", []))
    assert required == REQUIRED_TOP_LEVEL


def test_local_signal_schema_top_level_additional_properties_false(local_signal_schema):
    assert local_signal_schema["additionalProperties"] is False
    for key in REQUIRED_TOP_LEVEL:
        prop = local_signal_schema["properties"][key]
        assert prop.get("additionalProperties") is False, (
            f"{key} block must set additionalProperties: false"
        )


def test_known_good_example_validates(local_signal_schema):
    jsonschema.validate(KNOWN_GOOD_INPUT, local_signal_schema)


@pytest.mark.parametrize(
    "missing_top",
    sorted(REQUIRED_TOP_LEVEL),
)
def test_missing_required_top_level_field_rejected(local_signal_schema, missing_top):
    bad = copy.deepcopy(KNOWN_GOOD_INPUT)
    bad.pop(missing_top)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, local_signal_schema)


def test_invalid_group_kind_rejected(local_signal_schema):
    bad = copy.deepcopy(KNOWN_GOOD_INPUT)
    bad["context"]["group_kind"] = "broadcast"  # not in enum
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, local_signal_schema)


def test_invalid_group_age_mode_rejected(local_signal_schema):
    bad = copy.deepcopy(KNOWN_GOOD_INPUT)
    bad["context"]["group_age_mode"] = "everyone"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, local_signal_schema)


def test_invalid_user_role_rejected(local_signal_schema):
    bad = copy.deepcopy(KNOWN_GOOD_INPUT)
    bad["context"]["user_role"] = "owner"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, local_signal_schema)


def test_invalid_attachment_kind_rejected(local_signal_schema):
    bad = copy.deepcopy(KNOWN_GOOD_INPUT)
    bad["message"]["attachment_kinds"] = ["sticker"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, local_signal_schema)


@pytest.mark.parametrize("bad_value", [-0.01, 1.01, 2.0, -1.0])
def test_url_risk_out_of_range_rejected(local_signal_schema, bad_value):
    bad = copy.deepcopy(KNOWN_GOOD_INPUT)
    bad["local_signals"]["url_risk"] = bad_value
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, local_signal_schema)


def test_invalid_types_rejected(local_signal_schema):
    bad = copy.deepcopy(KNOWN_GOOD_INPUT)
    bad["message"]["has_attachment"] = "yes"  # should be bool
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, local_signal_schema)

    bad = copy.deepcopy(KNOWN_GOOD_INPUT)
    bad["context"]["is_offline"] = 1  # should be bool, not int
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, local_signal_schema)


def test_constraints_are_pinned(local_signal_schema):
    """The constraints block is intentionally pinned to fixed values."""
    bad = copy.deepcopy(KNOWN_GOOD_INPUT)
    bad["constraints"]["max_output_tokens"] = 1024
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, local_signal_schema)

    bad = copy.deepcopy(KNOWN_GOOD_INPUT)
    bad["constraints"]["temperature"] = 0.7
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, local_signal_schema)

    bad = copy.deepcopy(KNOWN_GOOD_INPUT)
    bad["constraints"]["schema_id"] = "kchat.guardrail.output.v2"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, local_signal_schema)


def test_lexicon_hit_category_must_be_in_taxonomy_range(local_signal_schema):
    bad = copy.deepcopy(KNOWN_GOOD_INPUT)
    bad["local_signals"]["lexicon_hits"] = [
        {"lexicon_id": "global.scam.en.v1", "category": 16, "weight": 0.5}
    ]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, local_signal_schema)
