"""Validate the test-suite template at kchat-skills/tests/test_suite_template.yaml.

The template encodes the Phase 1 metrics framework from PROPOSAL.md
"Success Metrics" (lines 169-180). Targets and operator signs are
load-bearing; the compiler consumes them verbatim in Phase 4.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


TEMPLATE_PATH = Path(__file__).resolve().parent / "test_suite_template.yaml"


@pytest.fixture(scope="module")
def template() -> dict:
    with TEMPLATE_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_template_file_exists():
    assert TEMPLATE_PATH.exists()


def test_template_parses_as_mapping(template):
    assert isinstance(template, dict)


def test_template_required_top_level_keys(template):
    required = {
        "schema_version",
        "test_suite_id",
        "metrics",
        "contracts",
        "coverage",
        "pack",
        "case_schema",
    }
    missing = required - set(template.keys())
    assert not missing, f"missing keys: {missing}"


def test_template_schema_version_is_1(template):
    assert template["schema_version"] == 1


# ---- Metrics framework ---------------------------------------------------
EXPECTED_METRICS: dict[str, tuple[str, float]] = {
    "child_safety_recall":              (">=", 0.98),
    "child_safety_precision":           (">=", 0.90),
    "privacy_leak_precision":           (">=", 0.90),
    "scam_recall":                      (">=", 0.85),
    "protected_speech_false_positive":  ("<=", 0.05),
    "minority_language_false_positive": ("<=", 0.07),
    "latency_p95_ms":                   ("<=", 250),
}


def _metrics_by_id(template: dict) -> dict[str, dict]:
    return {m["id"]: m for m in template["metrics"]}


def test_all_required_metrics_present(template):
    metrics = _metrics_by_id(template)
    missing = set(EXPECTED_METRICS.keys()) - set(metrics.keys())
    assert not missing, f"template missing metrics: {missing}"


@pytest.mark.parametrize(
    "metric_id,expected",
    sorted(EXPECTED_METRICS.items()),
)
def test_metric_operator_and_threshold(template, metric_id, expected):
    metrics = _metrics_by_id(template)
    op, threshold = expected
    assert metrics[metric_id]["operator"] == op
    assert metrics[metric_id]["threshold"] == threshold


def test_latency_metric_is_in_ms(template):
    metrics = _metrics_by_id(template)
    assert metrics["latency_p95_ms"].get("unit") == "ms"


# ---- Contracts -----------------------------------------------------------
def test_contracts_reference_global_schemas(template):
    contracts = template["contracts"]
    assert "local_signal_schema.json" in contracts["input_schema"]
    assert "output_schema.json" in contracts["output_schema"]


# ---- Coverage ------------------------------------------------------------
def test_coverage_requires_all_16_categories(template):
    per_cat = template["coverage"]["per_category"]["taxonomy_category_min_cases"]
    assert set(per_cat.keys()) == set(range(16))
    for cid, count in per_cat.items():
        assert isinstance(count, int) and count >= 10, (
            f"category {cid} coverage {count} must be >= 10"
        )


def test_coverage_includes_all_four_protected_speech_contexts(template):
    ctxs = set(template["coverage"]["protected_speech_contexts"])
    assert ctxs == {
        "QUOTED_SPEECH_CONTEXT",
        "NEWS_CONTEXT",
        "EDUCATION_CONTEXT",
        "COUNTERSPEECH_CONTEXT",
    }


def test_coverage_includes_threshold_boundary_confidences(template):
    confs = set(
        template["coverage"]["threshold_boundary_cases"]["confidences"]
    )
    # Exactly the decision-policy thresholds in baseline.yaml plus the
    # sentinel 0.44 which MUST map to SAFE.
    assert confs == {0.44, 0.45, 0.62, 0.78, 0.85}
