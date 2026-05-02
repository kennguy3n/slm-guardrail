"""Tests for ``kchat-skills/compiler/bias_audit.py``.

Spec reference: PHASES.md Phase 6 — bias auditing for protected-class
and minority-language effects.

The test corpus is a mix of synthetic per-class / per-language cases
plus an integration test that runs the auditor against the existing
minority-language FP corpus from
``kchat-skills/tests/jurisdictions/test_minority_language_fp.py``.
The integration case verifies that when every prediction is SAFE
(category 0) the audit passes — the same shape Phase 3 will produce
on a clean pipeline run.
"""
from __future__ import annotations

import pytest

from bias_audit import (  # type: ignore[import-not-found]
    MAX_DISPARITY,
    MAX_PER_GROUP_FP_RATE,
    BiasAuditCase,
    BiasAuditReport,
    BiasAuditor,
)
from metric_validator import SAFE_CATEGORY  # type: ignore[import-not-found]


def _load_minority_language_corpus_as_bias_cases() -> list[BiasAuditCase]:
    """Load ``MINORITY_LANGUAGE_FP_CASES`` from the jurisdiction-tests
    corpus and project each case into a :class:`BiasAuditCase` with
    SAFE predictions.

    The corpus lives next to the jurisdiction tests; we load it by
    file-path importlib so we do not have to add the (separate) tests
    package to sys.path.
    """
    import importlib.util
    from pathlib import Path

    corpus_path = (
        Path(__file__).resolve().parents[1]
        / "jurisdictions"
        / "test_minority_language_fp.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_minority_language_fp_corpus_for_bias_audit", corpus_path
    )
    assert spec and spec.loader, (
        f"unable to import minority-language corpus at {corpus_path}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    out: list[BiasAuditCase] = []
    for c in module.MINORITY_LANGUAGE_FP_CASES:
        inp = c["input"]
        # Map the corpus's locale / lang_hint onto the bias-audit
        # ``language`` field. Fall back to "und" (undetermined) only
        # if neither is set; the corpus always provides a locale.
        message = inp.get("message", {})
        context = inp.get("context", {})
        language = (
            message.get("lang_hint")
            or context.get("locale", "und").split("-", 1)[0]
        )
        # The corpus is keyed by archetype/country, not by protected
        # class. We use the archetype as the protected_class proxy
        # so the auditor can still detect cross-archetype skew.
        protected_class = f"archetype:{c['archetype']}"
        out.append(
            BiasAuditCase(
                case_id=c["case_id"],
                protected_class=protected_class,
                language=language,
                expected_category=SAFE_CATEGORY,
                predicted_category=SAFE_CATEGORY,
                tags=tuple(c.get("tags", ())),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _case(
    case_id: str,
    *,
    protected_class: str = "unspecified",
    language: str = "en",
    expected: int = SAFE_CATEGORY,
    predicted: int = SAFE_CATEGORY,
    tags: tuple[str, ...] = (),
) -> BiasAuditCase:
    return BiasAuditCase(
        case_id=case_id,
        protected_class=protected_class,
        language=language,
        expected_category=expected,
        predicted_category=predicted,
        tags=tags,
    )


# ---------------------------------------------------------------------------
# Per-class FP computation.
# ---------------------------------------------------------------------------
class TestPerClassFp:
    def test_clean_run_is_zero(self):
        auditor = BiasAuditor()
        cases = [
            _case("c1", protected_class="race"),
            _case("c2", protected_class="religion"),
        ]
        per_class = auditor.audit_protected_class_effects(cases)
        assert per_class["race"] == (0.0, 1)
        assert per_class["religion"] == (0.0, 1)

    def test_single_fp_in_one_class(self):
        auditor = BiasAuditor()
        cases = [
            _case("c1", protected_class="race", predicted=6),  # FP
            _case("c2", protected_class="race"),  # SAFE -> SAFE
            _case("c3", protected_class="race"),  # SAFE -> SAFE
            _case("c4", protected_class="religion"),
        ]
        per_class = auditor.audit_protected_class_effects(cases)
        assert per_class["race"] == pytest.approx((1 / 3, 3))
        assert per_class["religion"] == (0.0, 1)

    def test_non_safe_expected_does_not_count(self):
        """A case whose expected_category is non-SAFE is not an FP candidate."""
        auditor = BiasAuditor()
        cases = [
            # expected harm; predicted as a different harm — not counted
            # as a SAFE-corpus false positive.
            _case(
                "c1",
                protected_class="race",
                expected=4,
                predicted=6,
            ),
        ]
        per_class = auditor.audit_protected_class_effects(cases)
        assert per_class == {}


# ---------------------------------------------------------------------------
# Per-language FP computation.
# ---------------------------------------------------------------------------
class TestPerLanguageFp:
    def test_clean_run_is_zero(self):
        auditor = BiasAuditor()
        cases = [
            _case("c1", language="en"),
            _case("c2", language="ja"),
        ]
        per_lang = auditor.audit_minority_language_effects(cases)
        assert per_lang["en"] == (0.0, 1)
        assert per_lang["ja"] == (0.0, 1)

    def test_minority_language_fp_isolated(self):
        auditor = BiasAuditor()
        cases = [
            _case("c1", language="hi", predicted=6),  # FP
            _case("c2", language="hi"),
            _case("c3", language="en"),
            _case("c4", language="en"),
        ]
        per_lang = auditor.audit_minority_language_effects(cases)
        assert per_lang["hi"] == pytest.approx((0.5, 2))
        assert per_lang["en"] == (0.0, 2)


# ---------------------------------------------------------------------------
# Disparity detection.
# ---------------------------------------------------------------------------
class TestDisparity:
    def test_no_disparity_on_uniform_groups(self):
        auditor = BiasAuditor()
        rates = {"a": (0.05, 100), "b": (0.05, 100), "c": (0.05, 100)}
        assert auditor.audit_disparity(rates) == []

    def test_one_group_significantly_higher_is_flagged(self):
        # Mean = (0.01 + 0.02 + 0.20) / 3 ≈ 0.077
        # 0.20 - 0.077 = 0.123 > MAX_DISPARITY (0.05) → flagged.
        auditor = BiasAuditor()
        rates = {"a": (0.01, 50), "b": (0.02, 50), "c": (0.20, 50)}
        assert auditor.audit_disparity(rates) == ["c"]

    def test_below_floor_disparity_not_flagged(self):
        # Mean = 0.04, all rates within 0.05 of mean → no flag.
        auditor = BiasAuditor()
        rates = {"a": (0.02, 50), "b": (0.04, 50), "c": (0.06, 50)}
        assert auditor.audit_disparity(rates) == []

    def test_custom_max_disparity_overrides_default(self):
        auditor = BiasAuditor()
        rates = {"a": (0.02, 50), "b": (0.04, 50), "c": (0.06, 50)}
        # Tighten the threshold; rate 0.06 vs mean 0.04 = 0.02 > 0.01 → flag.
        assert auditor.audit_disparity(rates, max_disparity=0.01) == ["c"]

    def test_empty_rates_returns_no_flags(self):
        auditor = BiasAuditor()
        assert auditor.audit_disparity({}) == []


# ---------------------------------------------------------------------------
# Full run_audit.
# ---------------------------------------------------------------------------
class TestRunAudit:
    def test_clean_audit_passes(self):
        auditor = BiasAuditor()
        cases = [
            _case("c1", protected_class="race", language="en"),
            _case("c2", protected_class="religion", language="en"),
            _case("c3", protected_class="sex", language="ja"),
        ]
        report = auditor.run_audit(cases, pack_id="kchat.test.v1")
        assert isinstance(report, BiasAuditReport)
        assert report.pack_id == "kchat.test.v1"
        assert report.passed
        assert report.flagged_classes == ()
        assert report.flagged_languages == ()
        assert report.overall_protected_class_fp_rate == 0.0
        assert report.overall_minority_language_fp_rate == 0.0

    def test_audit_fails_on_per_class_ceiling(self):
        # 5 FPs on `race` out of 5 cases → 1.0 FP rate, well above 0.07.
        auditor = BiasAuditor()
        cases = [
            _case(f"c{i}", protected_class="race", predicted=6)
            for i in range(5)
        ] + [_case("ok", protected_class="religion")]
        report = auditor.run_audit(cases, pack_id="kchat.test.v1")
        assert not report.passed
        assert "race" in report.flagged_classes

    def test_audit_fails_on_disparity(self):
        # Build rates where 'race' is 0.10 (above 0.07 ceiling) and
        # other classes are 0.0 — both ceiling and disparity flag it.
        auditor = BiasAuditor()
        cases = []
        # 10 race cases, 1 FP → 0.10 rate
        for i in range(9):
            cases.append(_case(f"r{i}", protected_class="race"))
        cases.append(_case("rfp", protected_class="race", predicted=6))
        for i in range(10):
            cases.append(_case(f"o{i}", protected_class="religion"))
        report = auditor.run_audit(cases, pack_id="kchat.test.v1")
        assert not report.passed
        assert "race" in report.flagged_classes

    def test_per_language_ceiling_flagged(self):
        auditor = BiasAuditor()
        cases = [
            _case("hi1", language="hi", predicted=6),
            _case("hi2", language="hi"),
            _case("en1", language="en"),
            _case("en2", language="en"),
        ]
        report = auditor.run_audit(cases, pack_id="kchat.test.v1")
        # hi FP rate = 0.5 > 0.07 ceiling.
        assert "hi" in report.flagged_languages
        assert not report.passed


# ---------------------------------------------------------------------------
# Edge cases.
# ---------------------------------------------------------------------------
class TestEdgeCases:
    def test_empty_results(self):
        auditor = BiasAuditor()
        report = auditor.run_audit([], pack_id="kchat.test.v1")
        assert report.passed
        assert report.per_class_results == {}
        assert report.per_language_results == {}
        assert report.overall_protected_class_fp_rate == 0.0
        assert report.overall_minority_language_fp_rate == 0.0

    def test_single_group(self):
        auditor = BiasAuditor()
        cases = [
            _case(f"c{i}", protected_class="race", language="en")
            for i in range(5)
        ]
        report = auditor.run_audit(cases, pack_id="kchat.test.v1")
        assert report.passed
        # Single-group disparity is impossible — mean equals the rate.

    def test_all_safe_predictions(self):
        auditor = BiasAuditor()
        cases = [
            _case(f"c{i}", protected_class="race", language="en")
            for i in range(20)
        ]
        report = auditor.run_audit(cases, pack_id="kchat.test.v1")
        assert report.passed
        assert report.overall_protected_class_fp_rate == 0.0


# ---------------------------------------------------------------------------
# Configuration / construction.
# ---------------------------------------------------------------------------
class TestConfiguration:
    def test_defaults_match_spec(self):
        auditor = BiasAuditor()
        assert auditor.max_per_group_fp_rate == MAX_PER_GROUP_FP_RATE == 0.07
        assert auditor.max_disparity == MAX_DISPARITY == 0.05

    def test_invalid_max_per_group_fp_rate_rejected(self):
        with pytest.raises(ValueError):
            BiasAuditor(max_per_group_fp_rate=0.0)
        with pytest.raises(ValueError):
            BiasAuditor(max_per_group_fp_rate=1.5)

    def test_invalid_max_disparity_rejected(self):
        with pytest.raises(ValueError):
            BiasAuditor(max_disparity=-0.1)
        with pytest.raises(ValueError):
            BiasAuditor(max_disparity=1.5)


# ---------------------------------------------------------------------------
# Integration with the existing minority-language FP corpus.
# ---------------------------------------------------------------------------
class TestIntegrationWithMinorityLanguageCorpus:
    def test_clean_run_against_minority_language_corpus(self):
        """When the pipeline produces SAFE for every FP-corpus case, the
        bias audit must pass.

        This mirrors the success case Phase 3 will produce when the
        deterministic detectors and the encoder classifier both correctly
        recognise the benign cases as SAFE.
        """
        cases = _load_minority_language_corpus_as_bias_cases()
        auditor = BiasAuditor()
        report = auditor.run_audit(cases, pack_id="kchat.global.guardrail.baseline")
        assert report.passed, (
            f"clean SAFE-only run must pass; got flagged classes "
            f"{report.flagged_classes}, flagged languages "
            f"{report.flagged_languages}"
        )
        # Every language bucket must have a sample size > 0.
        for language, (_rate, n) in report.per_language_results.items():
            assert n > 0, f"language {language!r} has empty sample"
