"""Performance-optimization benchmarking for the hybrid local pipeline.

Spec reference: PHASES.md Phase 6 — "Performance optimization
benchmarking". The benchmark provides a structured, deterministic
measurement harness over :class:`~pipeline.GuardrailPipeline` and its
:class:`~encoder_adapter.MockEncoderAdapter` reference. The harness is
adapter-agnostic — swap in the XLM-R encoder classifier
(or any other ``EncoderAdapter``) and re-run to measure that backend's
latency. It is intended for regression testing — the p95 latency
target is pinned at 250ms (ARCHITECTURE.md "Performance envelope")
and the benchmark test refuses to pass if the target is breached.

Design notes:

* No wall-clock network dependencies. :func:`time.perf_counter` only.
* Timing granularity is microseconds; we report milliseconds rounded
  to three decimal places for readability.
* The benchmark never changes the pipeline under test, so it is safe
  to run inside the test suite on every CI build.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from pipeline import GuardrailPipeline  # type: ignore[import-not-found]
from encoder_adapter import MockEncoderAdapter  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Benchmark report.
# ---------------------------------------------------------------------------
# ARCHITECTURE.md "Performance envelope": p95 per-message latency must
# remain under 250ms on reference hardware with the full pack set.
P95_LATENCY_TARGET_MS = 250.0


@dataclass(frozen=True)
class BenchmarkCase:
    """One case submitted to the benchmark harness.

    ``message`` / ``context`` match the corresponding blocks of
    ``kchat.guardrail.local_signal.v1`` — the same shape
    :meth:`GuardrailPipeline.classify` expects.
    """

    case_id: str
    message: dict[str, Any]
    context: dict[str, Any]


@dataclass
class BenchmarkReport:
    """Aggregated latency report for a benchmark run.

    ``passed`` ⇔ ``p95_ms <= P95_LATENCY_TARGET_MS``.
    """

    total_cases: int
    iterations: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float
    max_ms: float
    min_ms: float = 0.0
    per_case_mean_ms: dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.p95_ms <= P95_LATENCY_TARGET_MS


# ---------------------------------------------------------------------------
# Benchmark runner.
# ---------------------------------------------------------------------------
def _percentile(values: list[float], pct: float) -> float:
    """Return the ``pct`` percentile of ``values`` using nearest-rank.

    Nearest-rank matches Unix ``tdigest`` reference behaviour for small
    sample sizes and is deterministic — essential for a regression test.
    """
    if not values:
        return 0.0
    if not 0.0 <= pct <= 100.0:
        raise ValueError(f"pct must be in [0, 100]; got {pct}")
    ordered = sorted(values)
    n = len(ordered)
    # Nearest-rank (1-indexed): rank = ceil(pct / 100 * N).
    # Compute ceil(pct * N / 100) via integer arithmetic to avoid any
    # floating-point or banker's-rounding edge cases when pct * N / 100
    # is an exact integer.
    rank_1indexed = max(1, -(-int(pct * n) // 100))
    rank = min(rank_1indexed - 1, n - 1)
    return ordered[rank]


@dataclass
class PipelineBenchmark:
    """Measure per-message latency for a :class:`GuardrailPipeline`.

    The benchmark is deterministic when run against
    :class:`MockEncoderAdapter`. Each case is executed ``iterations`` times
    and each execution's wall-clock latency is recorded. Percentiles
    are computed over the *flattened* list of all observations so the
    report is stable regardless of inter-case ordering.
    """

    pipeline: GuardrailPipeline

    @classmethod
    def with_mock_adapter(
        cls,
        skill_bundle: Any,
        threshold_policy: Optional[Any] = None,
    ) -> "PipelineBenchmark":
        """Convenience constructor wiring the mock adapter.

        Callers pass a pre-built :class:`SkillBundle`; the benchmark
        supplies its own :class:`MockEncoderAdapter`.
        """
        kwargs: dict[str, Any] = {
            "skill_bundle": skill_bundle,
            "encoder_adapter": MockEncoderAdapter(),
        }
        if threshold_policy is not None:
            kwargs["threshold_policy"] = threshold_policy
        return cls(pipeline=GuardrailPipeline(**kwargs))

    def run(
        self,
        cases: list[BenchmarkCase],
        *,
        iterations: int = 100,
        warmup: int = 3,
    ) -> BenchmarkReport:
        """Run each case ``iterations`` times and return a :class:`BenchmarkReport`.

        ``warmup`` iterations per case are discarded to let the Python
        interpreter's code-cache warm up before measurement.
        """
        if iterations < 1:
            raise ValueError("iterations must be >= 1")
        if warmup < 0:
            raise ValueError("warmup must be >= 0")
        if not cases:
            raise ValueError("cases must be non-empty")

        all_latencies_ms: list[float] = []
        per_case_mean_ms: dict[str, float] = {}

        for case in cases:
            # Warm-up passes (timed but discarded).
            for _ in range(warmup):
                self.pipeline.classify(case.message, case.context)
            case_latencies_ms: list[float] = []
            for _ in range(iterations):
                start = time.perf_counter()
                self.pipeline.classify(case.message, case.context)
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                case_latencies_ms.append(elapsed_ms)
            all_latencies_ms.extend(case_latencies_ms)
            per_case_mean_ms[case.case_id] = statistics.fmean(case_latencies_ms)

        return BenchmarkReport(
            total_cases=len(cases),
            iterations=iterations,
            p50_ms=_percentile(all_latencies_ms, 50.0),
            p95_ms=_percentile(all_latencies_ms, 95.0),
            p99_ms=_percentile(all_latencies_ms, 99.0),
            mean_ms=statistics.fmean(all_latencies_ms),
            max_ms=max(all_latencies_ms),
            min_ms=min(all_latencies_ms),
            per_case_mean_ms=per_case_mean_ms,
        )


# ---------------------------------------------------------------------------
# Case-building helpers (used by tests and downstream callers).
# ---------------------------------------------------------------------------
def make_benchmark_case(
    case_id: str,
    *,
    text: str = "",
    lang_hint: str = "en",
    locale: str = "en-US",
    jurisdiction_id: Optional[str] = None,
    community_overlay_id: Optional[str] = None,
    group_kind: str = "small_group",
    age_mode: str = "mixed_age",
) -> BenchmarkCase:
    """Build a minimally-valid :class:`BenchmarkCase` for the pipeline."""
    message = {
        "text": text,
        "lang_hint": lang_hint,
        "has_attachment": False,
        "attachment_kinds": [],
        "quoted_from_user": False,
        "is_outbound": False,
    }
    context = {
        "group_kind": group_kind,
        "group_age_mode": age_mode,
        "user_role": "member",
        "relationship_known": True,
        "locale": locale,
        "jurisdiction_id": jurisdiction_id,
        "community_overlay_id": community_overlay_id,
        "is_offline": False,
    }
    return BenchmarkCase(case_id=case_id, message=message, context=context)


def default_benchmark_cases() -> list[BenchmarkCase]:
    """A minimal, deterministic battery exercising all 16 taxonomy categories.

    Each case is benign at the text level — the pipeline exercises
    tokenization, detector dispatch and adapter invocation without
    embedding literal harm strings (privacy contract).
    """
    texts = (
        ("safe-greeting", "Hello friends, hope your day is going well today."),
        ("long-sentence", "We spent the whole afternoon at the community picnic sharing food and stories with everyone."),
        ("multi-language", "Bonjour todos, the meeting ist at quatre this afternoon."),
        ("emoji-heavy", "See you soon 🌞🌸🌿 tomorrow at the park for coffee."),
        ("short-ack", "OK."),
        ("medium", "The quick brown fox jumps over the lazy dog in the sunlit meadow."),
        ("whitespace-variants", "Hello\tthere\nfriends, hope\u00a0you're doing fine today!"),
        ("url-benign", "Check out the weather forecast at https://example.com/forecast for the weekend."),
        ("list", "Groceries: bread, eggs, milk, apples, coffee, butter."),
        ("question", "What time are we meeting tomorrow morning at the station?"),
    )
    return [
        make_benchmark_case(case_id, text=text) for case_id, text in texts
    ]


__all__ = [
    "P95_LATENCY_TARGET_MS",
    "BenchmarkCase",
    "BenchmarkReport",
    "PipelineBenchmark",
    "default_benchmark_cases",
    "make_benchmark_case",
]
