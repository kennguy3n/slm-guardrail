"""Structural validation + smoke tests for the sample-messages corpus.

Module under test: ``kchat-skills/samples/sample_messages.yaml``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from pipeline import GuardrailPipeline, SkillBundle  # type: ignore[import-not-found]
from slm_adapter import MockSLMAdapter  # type: ignore[import-not-found]
from threshold_policy import ThresholdPolicy  # type: ignore[import-not-found]


SAMPLES_PATH = (
    Path(__file__).resolve().parents[3] / "kchat-skills" / "samples" / "sample_messages.yaml"
)

REQUIRED_TOP_KEYS = ("case_id", "message", "context", "expected_category")
REQUIRED_MESSAGE_KEYS = (
    "text",
    "lang_hint",
    "has_attachment",
    "attachment_kinds",
    "quoted_from_user",
    "is_outbound",
)
REQUIRED_CONTEXT_KEYS = (
    "group_kind",
    "group_age_mode",
    "user_role",
    "relationship_known",
    "locale",
    "jurisdiction_id",
    "community_overlay_id",
    "is_offline",
)

CASE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@pytest.fixture(scope="module")
def samples() -> list[dict[str, Any]]:
    assert SAMPLES_PATH.exists(), (
        f"sample_messages.yaml not found at {SAMPLES_PATH}"
    )
    with SAMPLES_PATH.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    assert isinstance(loaded, list), "sample_messages.yaml must be a YAML list"
    assert loaded, "sample_messages.yaml must contain at least one case"
    return loaded


# ---------------------------------------------------------------------------
# Structural validation.
# ---------------------------------------------------------------------------
def test_sample_messages_yaml_loads(samples):
    assert len(samples) >= 20, "expected ~20-30 sample cases"


def test_each_case_has_required_top_level_keys(samples):
    for case in samples:
        for key in REQUIRED_TOP_KEYS:
            assert key in case, f"{case.get('case_id')}: missing key '{key}'"


def test_each_case_has_well_formed_message_block(samples):
    for case in samples:
        msg = case["message"]
        assert isinstance(msg, dict), case["case_id"]
        for key in REQUIRED_MESSAGE_KEYS:
            assert key in msg, f"{case['case_id']}: message.{key} missing"
        assert isinstance(msg["attachment_kinds"], list)
        assert isinstance(msg["has_attachment"], bool)
        assert isinstance(msg["quoted_from_user"], bool)
        assert isinstance(msg["is_outbound"], bool)


def test_each_case_has_well_formed_context_block(samples):
    for case in samples:
        ctx = case["context"]
        assert isinstance(ctx, dict), case["case_id"]
        for key in REQUIRED_CONTEXT_KEYS:
            assert key in ctx, f"{case['case_id']}: context.{key} missing"
        assert ctx["group_kind"] in {
            "dm",
            "small_group",
            "large_group",
            "public_channel",
        }
        assert ctx["group_age_mode"] in {
            "minor_present",
            "mixed_age",
            "adult_only",
        }
        assert ctx["user_role"] in {"member", "admin", "guest", "self"}
        assert isinstance(ctx["relationship_known"], bool)
        assert isinstance(ctx["is_offline"], bool)


def test_expected_category_is_in_taxonomy_range(samples):
    for case in samples:
        cat = case["expected_category"]
        assert isinstance(cat, int), case["case_id"]
        assert 0 <= cat <= 15, f"{case['case_id']}: expected_category={cat}"


def test_expected_severity_in_range_when_present(samples):
    for case in samples:
        if "expected_severity" not in case:
            continue
        sev = case["expected_severity"]
        assert isinstance(sev, int), case["case_id"]
        assert 0 <= sev <= 5, f"{case['case_id']}: expected_severity={sev}"


def test_case_ids_are_unique_and_well_formed(samples):
    ids: set[str] = set()
    for case in samples:
        case_id = case["case_id"]
        assert isinstance(case_id, str), case
        assert CASE_ID_RE.match(case_id), (
            f"case_id '{case_id}' must match ^[a-z0-9][a-z0-9-]*$"
        )
        assert case_id not in ids, f"duplicate case_id: {case_id}"
        ids.add(case_id)


def test_corpus_covers_all_taxonomy_categories_or_safe_baseline(samples):
    """At minimum the corpus must include some non-SAFE expected categories.

    We don't require all 16 categories — many are protected-speech or
    rare — but the corpus must exercise scam, PII, and adult signals.
    """
    expected = {c["expected_category"] for c in samples}
    assert 0 in expected, "missing SAFE baseline cases"
    assert 7 in expected, "missing SCAM_FRAUD cases"
    assert 9 in expected, "missing PRIVATE_DATA cases"
    assert 10 in expected, "missing SEXUAL_ADULT cases"


def test_corpus_includes_multilanguage_samples(samples):
    langs = {(case["message"].get("lang_hint") or "").split("-", 1)[0] for case in samples}
    # English plus at least one non-English sample.
    assert "en" in langs
    non_en = langs - {"en", ""}
    assert non_en, "expected at least one non-English sample (e.g. vi/es/de)"


# ---------------------------------------------------------------------------
# Pipeline smoke + schema conformance.
# ---------------------------------------------------------------------------
def _pipeline() -> GuardrailPipeline:
    return GuardrailPipeline(
        skill_bundle=SkillBundle(),
        slm_adapter=MockSLMAdapter(),
        threshold_policy=ThresholdPolicy(),
    )


def test_each_case_runs_through_pipeline_without_errors(samples, output_schema):
    pipe = _pipeline()
    for case in samples:
        out = pipe.classify(dict(case["message"]), dict(case["context"]))
        jsonschema.validate(instance=out, schema=output_schema)


def test_each_case_output_category_in_taxonomy_range(samples):
    pipe = _pipeline()
    for case in samples:
        out = pipe.classify(dict(case["message"]), dict(case["context"]))
        assert 0 <= int(out["category"]) <= 15, case["case_id"]
        assert 0 <= int(out["severity"]) <= 5, case["case_id"]


def test_pipeline_is_deterministic_per_case(samples):
    pipe = _pipeline()
    for case in samples:
        a = pipe.classify(dict(case["message"]), dict(case["context"]))
        b = pipe.classify(dict(case["message"]), dict(case["context"]))
        assert a == b, f"non-deterministic output for {case['case_id']}"
