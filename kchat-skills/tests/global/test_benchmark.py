"""Contract tests for the performance-optimization benchmark harness.

Spec reference: PHASES.md Phase 6 — "Performance optimization
benchmarking" and ARCHITECTURE.md "Performance envelope".
"""
from __future__ import annotations

import pytest

from benchmark import (  # type: ignore[import-not-found]
    P95_LATENCY_TARGET_MS,
    BenchmarkCase,
    BenchmarkReport,
    PipelineBenchmark,
    _percentile,  # type: ignore[attr-defined]
    default_benchmark_cases,
    make_benchmark_case,
)
from pipeline import SkillBundle  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------
@pytest.fixture
def baseline_bundle() -> SkillBundle:
    """Bundle with neither jurisdiction nor community overlays."""
    return SkillBundle()


@pytest.fixture
def jurisdiction_bundle() -> SkillBundle:
    """Bundle representative of a jurisdiction-overlay deployment."""
    return SkillBundle(
        jurisdiction_id="kchat.jurisdiction.us.guardrail.v1",
    )


@pytest.fixture
def full_bundle() -> SkillBundle:
    """Baseline + jurisdiction + community overlay bundle."""
    return SkillBundle(
        jurisdiction_id="kchat.jurisdiction.us.guardrail.v1",
        community_overlay_id="kchat.community.school.v1",
    )


@pytest.fixture
def small_benchmark() -> PipelineBenchmark:
    return PipelineBenchmark.with_mock_adapter(SkillBundle())


# ---------------------------------------------------------------------------
# Construction + invariants.
# ---------------------------------------------------------------------------
def test_report_dataclass_fields():
    report = BenchmarkReport(
        total_cases=1,
        iterations=1,
        p50_ms=1.0,
        p95_ms=2.0,
        p99_ms=3.0,
        mean_ms=1.5,
        max_ms=3.0,
        min_ms=1.0,
    )
    assert report.passed is True


def test_report_fails_when_p95_exceeds_target():
    report = BenchmarkReport(
        total_cases=1,
        iterations=1,
        p50_ms=100.0,
        p95_ms=P95_LATENCY_TARGET_MS + 1.0,
        p99_ms=P95_LATENCY_TARGET_MS + 5.0,
        mean_ms=150.0,
        max_ms=300.0,
        min_ms=50.0,
    )
    assert report.passed is False


def test_p95_target_is_positive():
    assert P95_LATENCY_TARGET_MS > 0
    assert P95_LATENCY_TARGET_MS == 250.0


def test_percentile_nearest_rank_is_deterministic():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert _percentile(values, 50.0) in {5.0, 6.0}
    assert _percentile(values, 95.0) == 10.0
    # Identical inputs produce identical outputs.
    assert _percentile(list(values), 75.0) == _percentile(list(values), 75.0)


def test_benchmark_rejects_empty_cases(small_benchmark: PipelineBenchmark):
    with pytest.raises(ValueError):
        small_benchmark.run([], iterations=1)


def test_benchmark_rejects_zero_iterations(
    small_benchmark: PipelineBenchmark,
):
    case = make_benchmark_case("c", text="hi there")
    with pytest.raises(ValueError):
        small_benchmark.run([case], iterations=0)


def test_benchmark_rejects_negative_warmup(
    small_benchmark: PipelineBenchmark,
):
    case = make_benchmark_case("c", text="hi there")
    with pytest.raises(ValueError):
        small_benchmark.run([case], iterations=1, warmup=-1)


# ---------------------------------------------------------------------------
# End-to-end latency — mock adapter.
# ---------------------------------------------------------------------------
def test_p95_under_target_with_mock_adapter_baseline_only(
    baseline_bundle: SkillBundle,
):
    bench = PipelineBenchmark.with_mock_adapter(baseline_bundle)
    cases = default_benchmark_cases()
    report = bench.run(cases, iterations=50, warmup=2)
    assert report.total_cases == len(cases)
    assert report.iterations == 50
    assert report.passed, (
        f"baseline-only p95 {report.p95_ms:.3f}ms exceeded "
        f"target {P95_LATENCY_TARGET_MS}ms"
    )
    # Also guard the mean is much smaller than the p95 target.
    assert report.mean_ms < P95_LATENCY_TARGET_MS
    assert report.min_ms <= report.p50_ms <= report.p95_ms <= report.p99_ms


def test_p95_under_target_with_jurisdiction_overlay(
    jurisdiction_bundle: SkillBundle,
):
    bench = PipelineBenchmark.with_mock_adapter(jurisdiction_bundle)
    report = bench.run(default_benchmark_cases(), iterations=30, warmup=1)
    assert report.passed


def test_p95_under_target_with_full_overlay_stack(
    full_bundle: SkillBundle,
):
    bench = PipelineBenchmark.with_mock_adapter(full_bundle)
    report = bench.run(default_benchmark_cases(), iterations=30, warmup=1)
    assert report.passed


# ---------------------------------------------------------------------------
# Parametrize across all 16 taxonomy categories — we run a minimal
# per-category case to ensure the benchmark harness handles every
# taxonomy id without raising.
# ---------------------------------------------------------------------------
CATEGORY_IDS = tuple(range(0, 16))


@pytest.mark.parametrize("category_id", CATEGORY_IDS)
def test_benchmark_runs_for_each_taxonomy_category(
    baseline_bundle: SkillBundle, category_id: int
):
    bench = PipelineBenchmark.with_mock_adapter(baseline_bundle)
    case = make_benchmark_case(
        f"cat-{category_id}",
        text=f"benchmark category {category_id} benign content sample text.",
    )
    report = bench.run([case], iterations=5, warmup=1)
    assert report.total_cases == 1
    assert report.iterations == 5
    assert report.mean_ms > 0.0


# ---------------------------------------------------------------------------
# Scaling with 40 country packs — the full-pack set must not blow past
# the latency target.
# ---------------------------------------------------------------------------
FORTY_COUNTRY_CODES: tuple[str, ...] = (
    "us", "de", "br", "in", "jp",
    "mx", "ca", "ar", "co", "cl", "pe",
    "fr", "gb", "es", "it", "nl", "pl", "se", "pt", "ch", "at",
    "kr", "id", "ph", "th", "vn", "my", "sg", "tw", "pk", "bd",
    "ng", "za", "eg", "sa", "ae", "ke",
    "au", "nz", "tr",
)


def test_all_40_country_codes_present_in_scaling_test():
    assert len(FORTY_COUNTRY_CODES) == 40
    assert len(set(FORTY_COUNTRY_CODES)) == 40


def test_p95_under_target_across_all_40_country_bundles():
    """Build a benchmark case per country bundle and verify latency.

    This sanity-checks that pipeline dispatch does not scale linearly
    in the number of country packs (each pipeline instance uses only
    its resolved skill bundle, so the full-pack count is irrelevant at
    runtime — but the test would catch any regression that tied the
    hot path to a global pack lookup).
    """
    cases: list[BenchmarkCase] = []
    for cc in FORTY_COUNTRY_CODES:
        cases.append(
            make_benchmark_case(
                f"country-{cc}",
                text="benchmark benign message text for latency sampling.",
                jurisdiction_id=f"kchat.jurisdiction.{cc}.guardrail.v1",
            )
        )
    bundle = SkillBundle(
        jurisdiction_id="kchat.jurisdiction.us.guardrail.v1",
    )
    bench = PipelineBenchmark.with_mock_adapter(bundle)
    report = bench.run(cases, iterations=5, warmup=1)
    assert report.total_cases == 40
    assert report.passed, (
        f"40-country p95 {report.p95_ms:.3f}ms exceeded "
        f"target {P95_LATENCY_TARGET_MS}ms"
    )


def test_per_case_mean_is_recorded_for_each_case(
    small_benchmark: PipelineBenchmark,
):
    cases = default_benchmark_cases()
    report = small_benchmark.run(cases, iterations=3, warmup=1)
    assert set(report.per_case_mean_ms.keys()) == {c.case_id for c in cases}
    for mean_ms in report.per_case_mean_ms.values():
        assert mean_ms > 0.0
