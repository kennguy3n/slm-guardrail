"""Tests for ``kchat-skills/compiler/metric_validator.py``.

Covers the seven shipping metrics, boundary-value checks at the
canonical thresholds, the child-safety-recall floor, and end-to-end
validation against the :class:`GuardrailPipeline` + :class:`MockEncoderAdapter`.
"""
from __future__ import annotations

from typing import Any

import pytest

from metric_validator import (  # type: ignore[import-not-found]
    CHILD_SAFETY_CATEGORY,
    PRIVATE_DATA_CATEGORY,
    SAFE_CATEGORY,
    SCAM_FRAUD_CATEGORY,
    MetricReport,
    MetricThresholds,
    MetricValidator,
    MetricVerdict,
    TestCaseResult,
    false_positive_rate,
    latency_p95,
    percentile,
    precision_for_category,
    recall_for_category,
)
from pipeline import (  # type: ignore[import-not-found]
    GuardrailPipeline,
    LexiconEntry,
    SkillBundle,
)
from encoder_adapter import MockEncoderAdapter  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _r(
    case_id: str,
    expected: int,
    predicted: int,
    *,
    latency_ms: float = 1.0,
    tags: tuple[str, ...] = (),
) -> TestCaseResult:
    return TestCaseResult(
        case_id=case_id,
        expected_category=expected,
        predicted_category=predicted,
        latency_ms=latency_ms,
        tags=tags,
    )


def _all_safe_filler(n: int) -> list[TestCaseResult]:
    """Generic SAFE→SAFE rows so unrelated metrics don't trip on empty input."""
    return [
        _r(f"safe-{i}", SAFE_CATEGORY, SAFE_CATEGORY, latency_ms=1.0)
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Threshold immutability — guards against pack tampering.
# ---------------------------------------------------------------------------
class TestMetricThresholds:
    def test_canonical_values_match_template(self):
        t = MetricThresholds()
        assert t.child_safety_recall == 0.98
        assert t.child_safety_precision == 0.90
        assert t.privacy_leak_precision == 0.90
        assert t.scam_recall == 0.85
        assert t.protected_speech_false_positive == 0.05
        assert t.minority_language_false_positive == 0.07
        assert t.latency_p95_ms == 250.0

    def test_non_canonical_values_rejected(self):
        with pytest.raises(ValueError):
            MetricThresholds(child_safety_recall=0.5)
        with pytest.raises(ValueError):
            MetricThresholds(latency_p95_ms=500.0)


# ---------------------------------------------------------------------------
# Per-metric primitive computations.
# ---------------------------------------------------------------------------
class TestRecallForCategory:
    def test_perfect_recall(self):
        rs = [
            _r("a", CHILD_SAFETY_CATEGORY, CHILD_SAFETY_CATEGORY),
            _r("b", CHILD_SAFETY_CATEGORY, CHILD_SAFETY_CATEGORY),
        ]
        recall, n = recall_for_category(rs, CHILD_SAFETY_CATEGORY)
        assert recall == 1.0
        assert n == 2

    def test_partial_recall(self):
        rs = [
            _r("a", CHILD_SAFETY_CATEGORY, CHILD_SAFETY_CATEGORY),
            _r("b", CHILD_SAFETY_CATEGORY, SAFE_CATEGORY),  # missed
            _r("c", CHILD_SAFETY_CATEGORY, CHILD_SAFETY_CATEGORY),
            _r("d", CHILD_SAFETY_CATEGORY, SAFE_CATEGORY),  # missed
        ]
        recall, n = recall_for_category(rs, CHILD_SAFETY_CATEGORY)
        assert recall == pytest.approx(0.5)
        assert n == 4

    def test_no_positives_returns_one(self):
        rs = _all_safe_filler(5)
        recall, n = recall_for_category(rs, CHILD_SAFETY_CATEGORY)
        assert recall == 1.0
        assert n == 0


class TestPrecisionForCategory:
    def test_perfect_precision(self):
        rs = [
            _r("a", PRIVATE_DATA_CATEGORY, PRIVATE_DATA_CATEGORY),
            _r("b", PRIVATE_DATA_CATEGORY, PRIVATE_DATA_CATEGORY),
        ]
        precision, n = precision_for_category(rs, PRIVATE_DATA_CATEGORY)
        assert precision == 1.0
        assert n == 2

    def test_with_false_positives(self):
        rs = [
            _r("a", PRIVATE_DATA_CATEGORY, PRIVATE_DATA_CATEGORY),  # TP
            _r("b", SAFE_CATEGORY, PRIVATE_DATA_CATEGORY),  # FP
            _r("c", SAFE_CATEGORY, PRIVATE_DATA_CATEGORY),  # FP
            _r("d", PRIVATE_DATA_CATEGORY, PRIVATE_DATA_CATEGORY),  # TP
        ]
        precision, n = precision_for_category(rs, PRIVATE_DATA_CATEGORY)
        assert precision == pytest.approx(0.5)
        assert n == 4

    def test_no_predictions_returns_one(self):
        rs = _all_safe_filler(5)
        precision, n = precision_for_category(rs, PRIVATE_DATA_CATEGORY)
        assert precision == 1.0
        assert n == 0


class TestFalsePositiveRate:
    def test_protected_speech_no_fp(self):
        rs = [
            _r("a", SAFE_CATEGORY, SAFE_CATEGORY, tags=("protected_speech",)),
            _r("b", SAFE_CATEGORY, SAFE_CATEGORY, tags=("protected_speech",)),
        ]
        rate, n = false_positive_rate(rs, tag="protected_speech")
        assert rate == 0.0
        assert n == 2

    def test_protected_speech_some_fp(self):
        rs = [
            _r("a", SAFE_CATEGORY, SAFE_CATEGORY, tags=("protected_speech",)),
            _r("b", SAFE_CATEGORY, 5, tags=("protected_speech",)),  # FP
            _r("c", SAFE_CATEGORY, SAFE_CATEGORY, tags=("protected_speech",)),
            _r("d", SAFE_CATEGORY, 6, tags=("protected_speech",)),  # FP
        ]
        rate, n = false_positive_rate(rs, tag="protected_speech")
        assert rate == pytest.approx(0.5)
        assert n == 4

    def test_minority_language_filter(self):
        rs = [
            _r("a", SAFE_CATEGORY, SAFE_CATEGORY, tags=("minority_language",)),
            _r("b", SAFE_CATEGORY, 4, tags=("minority_language",)),
            _r("c", SAFE_CATEGORY, SAFE_CATEGORY, tags=("protected_speech",)),
        ]
        rate, n = false_positive_rate(rs, tag="minority_language")
        assert rate == pytest.approx(0.5)
        assert n == 2

    def test_empty_corpus(self):
        rate, n = false_positive_rate([], tag="protected_speech")
        assert rate == 0.0
        assert n == 0


class TestPercentileAndLatency:
    def test_percentile_basic(self):
        assert percentile([1, 2, 3, 4, 5], 0.5) == 3.0

    def test_percentile_p95(self):
        # 100 values 1..100 → p95 ≈ 95.05 (linear interpolation)
        values = list(range(1, 101))
        assert percentile(values, 0.95) == pytest.approx(95.05, rel=0.01)

    def test_percentile_empty(self):
        assert percentile([], 0.95) == 0.0

    def test_latency_p95_aggregates_results(self):
        rs = [_r(f"x{i}", 0, 0, latency_ms=float(i)) for i in range(1, 101)]
        p95, n = latency_p95(rs)
        assert n == 100
        assert p95 == pytest.approx(95.05, rel=0.01)


# ---------------------------------------------------------------------------
# Pass / fail boundaries for each metric.
# ---------------------------------------------------------------------------
class TestChildSafetyRecallBoundary:
    """The child_safety_recall >= 0.98 floor is the load-bearing metric."""

    def _build_results(self, *, hit_rate: float) -> list[TestCaseResult]:
        # 100 child-safety positive cases; first ``hit_rate * 100`` correctly
        # predicted as CHILD_SAFETY, the rest predicted as SAFE.
        n_total = 100
        n_hits = int(round(hit_rate * n_total))
        out = [
            _r(f"cs-{i}", CHILD_SAFETY_CATEGORY, CHILD_SAFETY_CATEGORY)
            for i in range(n_hits)
        ]
        out.extend(
            _r(f"cs-{i}", CHILD_SAFETY_CATEGORY, SAFE_CATEGORY)
            for i in range(n_hits, n_total)
        )
        return out

    def test_recall_exactly_98_passes(self):
        rs = self._build_results(hit_rate=0.98) + _all_safe_filler(20)
        report = MetricValidator().validate_results(rs)
        v = report.get("child_safety_recall")
        assert v.actual == pytest.approx(0.98)
        assert v.passed is True

    def test_recall_just_below_98_fails(self):
        rs = self._build_results(hit_rate=0.97) + _all_safe_filler(20)
        report = MetricValidator().validate_results(rs)
        v = report.get("child_safety_recall")
        assert v.actual < 0.98
        assert v.passed is False
        assert "child_safety_recall" in report.failed_metrics
        assert report.passed is False

    def test_recall_just_above_98_passes(self):
        rs = self._build_results(hit_rate=0.99) + _all_safe_filler(20)
        report = MetricValidator().validate_results(rs)
        v = report.get("child_safety_recall")
        assert v.passed is True


class TestProtectedSpeechFPBoundary:
    def test_fp_at_5pct_passes(self):
        # 100 protected-speech cases, 5 false positives (=5%).
        rs = [
            _r(f"ps-{i}", SAFE_CATEGORY, SAFE_CATEGORY, tags=("protected_speech",))
            for i in range(95)
        ] + [
            _r(f"ps-fp-{i}", SAFE_CATEGORY, 5, tags=("protected_speech",))
            for i in range(5)
        ]
        rs += _all_safe_filler(5)
        report = MetricValidator().validate_results(rs)
        v = report.get("protected_speech_false_positive")
        assert v.actual == pytest.approx(0.05)
        assert v.passed is True

    def test_fp_just_above_5pct_fails(self):
        rs = [
            _r(f"ps-{i}", SAFE_CATEGORY, SAFE_CATEGORY, tags=("protected_speech",))
            for i in range(94)
        ] + [
            _r(f"ps-fp-{i}", SAFE_CATEGORY, 5, tags=("protected_speech",))
            for i in range(6)
        ]
        rs += _all_safe_filler(5)
        report = MetricValidator().validate_results(rs)
        v = report.get("protected_speech_false_positive")
        assert v.actual > 0.05
        assert v.passed is False


class TestLatencyP95Boundary:
    def test_latency_at_250ms_passes(self):
        rs = [_r(f"x{i}", 0, 0, latency_ms=200.0) for i in range(100)]
        rs[-1] = _r("slow", 0, 0, latency_ms=250.0)
        report = MetricValidator().validate_results(rs)
        v = report.get("latency_p95_ms")
        assert v.actual <= 250.0
        assert v.passed is True

    def test_latency_above_250ms_fails(self):
        # Force p95 to clearly exceed 250 ms.
        rs = [_r(f"x{i}", 0, 0, latency_ms=300.0) for i in range(100)]
        report = MetricValidator().validate_results(rs)
        v = report.get("latency_p95_ms")
        assert v.actual > 250.0
        assert v.passed is False


class TestPrivacyLeakAndScamBoundaries:
    def test_privacy_precision_at_90_passes(self):
        # 9 TP + 1 FP → precision 0.90.
        rs = [
            _r(f"tp-{i}", PRIVATE_DATA_CATEGORY, PRIVATE_DATA_CATEGORY)
            for i in range(9)
        ] + [
            _r("fp", SAFE_CATEGORY, PRIVATE_DATA_CATEGORY),
        ]
        rs += _all_safe_filler(5)
        report = MetricValidator().validate_results(rs)
        v = report.get("privacy_leak_precision")
        assert v.actual == pytest.approx(0.9)
        assert v.passed is True

    def test_privacy_precision_just_below_90_fails(self):
        rs = [
            _r(f"tp-{i}", PRIVATE_DATA_CATEGORY, PRIVATE_DATA_CATEGORY)
            for i in range(8)
        ] + [
            _r(f"fp-{i}", SAFE_CATEGORY, PRIVATE_DATA_CATEGORY) for i in range(2)
        ]
        rs += _all_safe_filler(5)
        report = MetricValidator().validate_results(rs)
        v = report.get("privacy_leak_precision")
        assert v.actual < 0.9
        assert v.passed is False

    def test_scam_recall_at_85_passes(self):
        rs = [
            _r(f"tp-{i}", SCAM_FRAUD_CATEGORY, SCAM_FRAUD_CATEGORY)
            for i in range(17)
        ] + [
            _r(f"miss-{i}", SCAM_FRAUD_CATEGORY, SAFE_CATEGORY) for i in range(3)
        ]
        rs += _all_safe_filler(5)
        report = MetricValidator().validate_results(rs)
        v = report.get("scam_recall")
        assert v.actual == pytest.approx(0.85)
        assert v.passed is True


class TestMinorityLanguageBoundary:
    def test_fp_at_7pct_passes(self):
        rs = [
            _r(
                f"ml-{i}",
                SAFE_CATEGORY,
                SAFE_CATEGORY,
                tags=("minority_language",),
            )
            for i in range(93)
        ] + [
            _r(
                f"ml-fp-{i}",
                SAFE_CATEGORY,
                6,
                tags=("minority_language",),
            )
            for i in range(7)
        ]
        rs += _all_safe_filler(5)
        report = MetricValidator().validate_results(rs)
        v = report.get("minority_language_false_positive")
        assert v.actual == pytest.approx(0.07)
        assert v.passed is True

    def test_fp_above_7pct_fails(self):
        rs = [
            _r(
                f"ml-{i}",
                SAFE_CATEGORY,
                SAFE_CATEGORY,
                tags=("minority_language",),
            )
            for i in range(90)
        ] + [
            _r(
                f"ml-fp-{i}",
                SAFE_CATEGORY,
                6,
                tags=("minority_language",),
            )
            for i in range(10)
        ]
        report = MetricValidator().validate_results(rs)
        v = report.get("minority_language_false_positive")
        assert v.actual > 0.07
        assert v.passed is False


# ---------------------------------------------------------------------------
# Report aggregation.
# ---------------------------------------------------------------------------
class TestMetricReport:
    def test_passing_report(self):
        rs = _all_safe_filler(10)
        report = MetricValidator().validate_results(rs)
        assert isinstance(report, MetricReport)
        assert report.passed is True
        assert report.failed_metrics == ()
        assert len(report.verdicts) == 7

    def test_failing_report_lists_failures(self):
        rs = [
            _r(f"miss-{i}", CHILD_SAFETY_CATEGORY, SAFE_CATEGORY)
            for i in range(50)
        ]
        report = MetricValidator().validate_results(rs)
        assert report.passed is False
        assert "child_safety_recall" in report.failed_metrics

    def test_report_as_dict_round_trip(self):
        rs = _all_safe_filler(5)
        report = MetricValidator().validate_results(rs)
        d = report.as_dict()
        assert d["passed"] is True
        assert isinstance(d["verdicts"], list)
        assert len(d["verdicts"]) == 7
        for v in d["verdicts"]:
            for key in (
                "metric_id",
                "operator",
                "threshold",
                "actual",
                "passed",
                "sample_size",
            ):
                assert key in v


# ---------------------------------------------------------------------------
# End-to-end validation against the GuardrailPipeline + MockEncoderAdapter.
# ---------------------------------------------------------------------------
def _safe_input(text: str = "hello", *, tags=()) -> dict[str, Any]:
    return {
        "input": {
            "message": {
                "text": text,
                "lang_hint": "en",
                "has_attachment": False,
                "attachment_kinds": [],
                "quoted_from_user": False,
                "is_outbound": False,
            },
            "context": {
                "group_kind": "small_group",
                "group_age_mode": "mixed_age",
                "user_role": "member",
                "relationship_known": True,
                "locale": "en-US",
                "jurisdiction_id": None,
                "community_overlay_id": None,
                "is_offline": False,
            },
        },
        "tags": list(tags),
    }


class TestRunPipelineEndToEnd:
    def _baseline_pipeline(self) -> GuardrailPipeline:
        bundle = SkillBundle(
            lexicons=[
                # Category-1 hit triggers child-safety floor in MockEncoderAdapter.
                LexiconEntry(
                    "child_safety_lex_v1",
                    1,
                    ["sextortion_pattern_token"],
                    0.9,
                ),
            ]
        )
        return GuardrailPipeline(skill_bundle=bundle, encoder_adapter=MockEncoderAdapter())

    def test_pipeline_passes_all_metrics_on_baseline_cases(self):
        pipeline = self._baseline_pipeline()

        cases: list[dict[str, Any]] = []

        # 30 child-safety positives — pipeline detects via lexicon hit.
        for i in range(30):
            c = _safe_input("sextortion_pattern_token here")
            c["case_id"] = f"cs-{i}"
            c["expected_output"] = {"category": CHILD_SAFETY_CATEGORY}
            cases.append(c)

        # 20 SAFE benign messages.
        for i in range(20):
            c = _safe_input("hello there")
            c["case_id"] = f"safe-{i}"
            c["expected_output"] = {"category": SAFE_CATEGORY}
            cases.append(c)

        # 20 protected-speech contexts that must remain SAFE.
        for i in range(20):
            c = _safe_input("news report on sextortion", tags=("protected_speech",))
            # Strip the trigger token so the lexicon misses; protected
            # speech should not fire.
            c["input"]["message"]["text"] = "news report on a topic"
            c["case_id"] = f"ps-{i}"
            c["expected_output"] = {"category": SAFE_CATEGORY}
            cases.append(c)

        # 20 minority-language benign messages.
        for i in range(20):
            c = _safe_input("hola amigo", tags=("minority_language",))
            c["case_id"] = f"ml-{i}"
            c["expected_output"] = {"category": SAFE_CATEGORY}
            cases.append(c)

        # 10 scam-fraud positives via scam regex pattern.
        for i in range(10):
            c = _safe_input("congratulations you won a prize")
            c["case_id"] = f"sc-{i}"
            c["expected_output"] = {"category": SCAM_FRAUD_CATEGORY}
            cases.append(c)

        # 10 PRIVATE_DATA positives via PII regex.
        for i in range(10):
            c = _safe_input("contact me at foo@example.com")
            c["case_id"] = f"pd-{i}"
            c["expected_output"] = {"category": PRIVATE_DATA_CATEGORY}
            cases.append(c)

        report = pipeline.validate_metrics(cases)
        assert report.passed is True, report.as_dict()

    def test_pipeline_fails_when_child_safety_recall_drops(self):
        pipeline = self._baseline_pipeline()
        # Every case is "child-safety" by *expectation* but the message
        # text never triggers the lexicon — so recall = 0/N.
        cases = []
        for i in range(50):
            c = _safe_input("totally benign text")
            c["case_id"] = f"cs-{i}"
            c["expected_output"] = {"category": CHILD_SAFETY_CATEGORY}
            cases.append(c)

        report = pipeline.validate_metrics(cases)
        assert report.passed is False
        assert "child_safety_recall" in report.failed_metrics
