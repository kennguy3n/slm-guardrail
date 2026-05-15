"""Held-out evaluation runner.

Runs :class:`kchat-skills.compiler.pipeline.GuardrailPipeline` over the
held-out benign / adversarial YAML files in this directory and
emits a calibration report.

The runner is intentionally minimal — it stands up the same
deterministic detectors as the runtime, and uses a stub encoder by
default so the eval is deterministic and CI-friendly. A real
encoder can be plugged in by passing ``--encoder xlmr``.

Usage::

    python kchat-skills/eval/eval_runner.py \\
        --benign kchat-skills/eval/held_out_benign.yaml \\
        --adversarial kchat-skills/eval/held_out_adversarial.yaml \\
        --output /tmp/calibration.json

Exit codes:

* ``0`` — all pack-passport gates pass.
* ``1`` — at least one gate failed (see ``--output``).
* ``2`` — runner error (bad YAML, missing file, etc).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _ensure_compiler_on_path() -> None:
    """Make ``kchat-skills/compiler`` importable like the test suite does."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    compiler_dir = repo_root / "kchat-skills" / "compiler"
    if str(compiler_dir) not in sys.path:
        sys.path.insert(0, str(compiler_dir))


# ---------------------------------------------------------------------------
# Pack-passport gate thresholds. Must stay in sync with
# `kchat-skills/compiler/metric_validator.py` and the passport's
# `test_results` block.
# ---------------------------------------------------------------------------
GATE_CHILD_SAFETY_RECALL_MIN: float = 0.95
GATE_PRIVACY_LEAK_PRECISION_MIN: float = 0.85
GATE_PROTECTED_SPEECH_FP_MAX: float = 0.05
GATE_MINORITY_LANGUAGE_FP_MAX: float = 0.10


@dataclass
class CaseResult:
    case_id: str
    language: str
    tags: tuple[str, ...]
    expected: dict[str, Any]
    actual: dict[str, Any]
    passed: bool
    notes: str = ""


@dataclass
class CalibrationReport:
    n_cases: int = 0
    n_passed: int = 0
    n_failed: int = 0
    per_language: dict[str, dict[str, int]] = field(default_factory=dict)
    per_tag: dict[str, dict[str, int]] = field(default_factory=dict)
    failures: list[CaseResult] = field(default_factory=list)

    # Aggregate metrics that gate the pack passport.
    child_safety_recall: float = 0.0
    privacy_leak_precision: float = 0.0
    protected_speech_false_positive: float = 0.0
    minority_language_false_positive: float = 0.0
    expected_calibration_error: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_cases": self.n_cases,
            "n_passed": self.n_passed,
            "n_failed": self.n_failed,
            "per_language": self.per_language,
            "per_tag": self.per_tag,
            "child_safety_recall": self.child_safety_recall,
            "privacy_leak_precision": self.privacy_leak_precision,
            "protected_speech_false_positive": (
                self.protected_speech_false_positive
            ),
            "minority_language_false_positive": (
                self.minority_language_false_positive
            ),
            "expected_calibration_error": self.expected_calibration_error,
            "failures": [
                {
                    "case_id": f.case_id,
                    "language": f.language,
                    "tags": list(f.tags),
                    "expected": f.expected,
                    "actual": f.actual,
                    "notes": f.notes,
                }
                for f in self.failures
            ],
        }


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "PyYAML is required to run the eval runner."
        ) from exc
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _build_pipeline_input(case: dict[str, Any]) -> dict[str, Any]:
    """Hydrate a case dict into a local_signal_schema-compatible input.

    The eval cases are intentionally terse — the runner fills in
    defaults for fields the pipeline requires but the case author
    did not need to set explicitly (e.g. empty local-signal blocks
    when the case is testing only the lexicon path).
    """
    msg = dict(case.get("message", {}))
    msg.setdefault("text", "")
    msg.setdefault("language_hint", case.get("language", "und"))
    msg.setdefault("quoted_from_user", False)
    msg.setdefault("attachments", [])

    ctx = dict(case.get("context", {}))
    ctx.setdefault("group_kind", "small_group")
    ctx.setdefault("group_age_mode", "adult_only")
    ctx.setdefault("user_role", "member")
    ctx.setdefault("relationship_known", False)
    ctx.setdefault("locale", "en-US")
    ctx.setdefault("jurisdiction_id", None)
    ctx.setdefault("community_overlay_id", None)
    ctx.setdefault("is_offline", False)

    local_signals = case.get("local_signals") or {
        "url_risk": [],
        "pii_patterns_hit": [],
        "scam_patterns_hit": [],
        "lexicon_hits": [],
        "media_signals": [],
    }
    constraints = case.get("constraints") or {
        "max_output_tokens": 600,
        "rationale_id_set": "default",
    }
    return {
        "message": msg,
        "context": ctx,
        "local_signals": local_signals,
        "constraints": constraints,
    }


def _evaluate_case(
    case: dict[str, Any], pipeline_output: dict[str, Any]
) -> CaseResult:
    expected = case.get("expected") or {}
    notes: list[str] = []
    passed = True

    if "category" in expected:
        if pipeline_output.get("category") != expected["category"]:
            passed = False
            notes.append(
                f"category={pipeline_output.get('category')} "
                f"want {expected['category']}"
            )
    if "severity" in expected:
        if pipeline_output.get("severity") != expected["severity"]:
            passed = False
            notes.append(
                f"severity={pipeline_output.get('severity')} "
                f"want {expected['severity']}"
            )
    if "severity_at_least" in expected:
        if int(pipeline_output.get("severity", 0)) < int(
            expected["severity_at_least"]
        ):
            passed = False
            notes.append(
                f"severity={pipeline_output.get('severity')} "
                f">= {expected['severity_at_least']}"
            )
    if "reason_codes_must_include" in expected:
        codes = set(pipeline_output.get("reason_codes") or [])
        wanted = set(expected["reason_codes_must_include"])
        missing = wanted - codes
        if missing:
            passed = False
            notes.append(f"reason_codes missing {sorted(missing)}")
    if "reason_codes_must_exclude" in expected:
        codes = set(pipeline_output.get("reason_codes") or [])
        forbidden = set(expected["reason_codes_must_exclude"])
        leaked = codes & forbidden
        if leaked:
            passed = False
            notes.append(f"reason_codes forbidden {sorted(leaked)}")

    tags = tuple(case.get("tags") or ())
    return CaseResult(
        case_id=str(case.get("case_id", "")),
        language=str(case.get("language", "und")),
        tags=tags,
        expected=expected,
        actual={
            "category": pipeline_output.get("category"),
            "severity": pipeline_output.get("severity"),
            "confidence": pipeline_output.get("confidence"),
            "reason_codes": list(pipeline_output.get("reason_codes") or []),
            "rationale_id": pipeline_output.get("rationale_id"),
        },
        passed=passed,
        notes="; ".join(notes),
    )


def _ece(confidences: list[float], correctness: list[bool], buckets: int = 10) -> float:
    """Expected Calibration Error across ``buckets`` confidence bins."""
    if not confidences:
        return 0.0
    n = len(confidences)
    total = 0.0
    for b in range(buckets):
        lo = b / buckets
        hi = (b + 1) / buckets
        idx = [
            i for i, c in enumerate(confidences) if (lo <= c < hi) or (b == buckets - 1 and c == 1.0)
        ]
        if not idx:
            continue
        avg_conf = sum(confidences[i] for i in idx) / len(idx)
        acc = sum(1.0 for i in idx if correctness[i]) / len(idx)
        total += (len(idx) / n) * abs(avg_conf - acc)
    return total


def _aggregate(results: list[CaseResult]) -> CalibrationReport:
    report = CalibrationReport(n_cases=len(results))
    for r in results:
        if r.passed:
            report.n_passed += 1
        else:
            report.n_failed += 1
            report.failures.append(r)

        lang_bucket = report.per_language.setdefault(
            r.language, {"n": 0, "passed": 0, "failed": 0}
        )
        lang_bucket["n"] += 1
        lang_bucket["passed" if r.passed else "failed"] += 1

        for tag in r.tags:
            tag_bucket = report.per_tag.setdefault(
                tag, {"n": 0, "passed": 0, "failed": 0}
            )
            tag_bucket["n"] += 1
            tag_bucket["passed" if r.passed else "failed"] += 1

    # Pack-passport gate metrics.
    child_total = sum(
        1 for r in results if "child_safety" in r.tags
    )
    child_caught = sum(
        1 for r in results if "child_safety" in r.tags and r.passed
    )
    report.child_safety_recall = (
        child_caught / child_total if child_total else 1.0
    )

    pii_total = sum(1 for r in results if "pii" in r.tags)
    pii_caught = sum(1 for r in results if "pii" in r.tags and r.passed)
    report.privacy_leak_precision = (
        pii_caught / pii_total if pii_total else 1.0
    )

    benign_total = sum(
        1 for r in results if "benign_false_positive" in r.tags
    )
    benign_flagged = sum(
        1
        for r in results
        if "benign_false_positive" in r.tags and not r.passed
    )
    report.protected_speech_false_positive = (
        benign_flagged / benign_total if benign_total else 0.0
    )

    minority_total = sum(
        1 for r in results if "minority_language" in r.tags
    )
    minority_missed = sum(
        1
        for r in results
        if "minority_language" in r.tags and not r.passed
    )
    report.minority_language_false_positive = (
        minority_missed / minority_total if minority_total else 0.0
    )

    confidences = [
        float(r.actual.get("confidence") or 0.0) for r in results
    ]
    correctness = [r.passed for r in results]
    report.expected_calibration_error = _ece(confidences, correctness)

    return report


def _gates_pass(report: CalibrationReport) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if report.child_safety_recall < GATE_CHILD_SAFETY_RECALL_MIN:
        failures.append(
            f"child_safety_recall={report.child_safety_recall:.3f} "
            f"< {GATE_CHILD_SAFETY_RECALL_MIN}"
        )
    if report.privacy_leak_precision < GATE_PRIVACY_LEAK_PRECISION_MIN:
        failures.append(
            f"privacy_leak_precision={report.privacy_leak_precision:.3f} "
            f"< {GATE_PRIVACY_LEAK_PRECISION_MIN}"
        )
    if (
        report.protected_speech_false_positive
        > GATE_PROTECTED_SPEECH_FP_MAX
    ):
        failures.append(
            f"protected_speech_false_positive="
            f"{report.protected_speech_false_positive:.3f} "
            f"> {GATE_PROTECTED_SPEECH_FP_MAX}"
        )
    if (
        report.minority_language_false_positive
        > GATE_MINORITY_LANGUAGE_FP_MAX
    ):
        failures.append(
            f"minority_language_false_positive="
            f"{report.minority_language_false_positive:.3f} "
            f"> {GATE_MINORITY_LANGUAGE_FP_MAX}"
        )
    return not failures, failures


def _build_pipeline(encoder_kind: str) -> Any:
    """Wire up the runtime ``GuardrailPipeline`` with a chosen encoder.

    ``stub`` (default) — returns a SAFE-category, low-confidence
    output so the calibration report depends only on the
    deterministic detectors and the threshold policy. This is the
    CI-friendly path.

    ``xlmr`` — boots :class:`XLMRAdapter`. Requires the ONNX model
    + tokenizer to be present on disk.
    """
    _ensure_compiler_on_path()
    from pipeline import (  # type: ignore[import-not-found]
        GuardrailPipeline,
        SkillBundle,
    )

    class _StubAdapter:
        health_state = "healthy"
        last_embedding: Any = None

        def classify(self, input: dict[str, Any]) -> dict[str, Any]:
            return {
                "severity": 0,
                "category": 0,
                "confidence": 0.5,
                "actions": {
                    "label_only": False,
                    "warn": False,
                    "strong_warn": False,
                    "critical_intervention": False,
                    "suggest_redact": False,
                },
                "reason_codes": [],
                "rationale_id": "stub_v1",
                "user_counters_delta": [],
            }

    bundle = SkillBundle(lexicons=[])
    if encoder_kind == "xlmr":
        from xlmr_adapter import (  # type: ignore[import-not-found]
            XLMRAdapter,
        )

        adapter = XLMRAdapter()
        return GuardrailPipeline(skill_bundle=bundle, encoder_adapter=adapter)
    return GuardrailPipeline(
        skill_bundle=bundle, encoder_adapter=_StubAdapter()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--benign",
        type=Path,
        required=True,
        help="Path to the held-out benign YAML.",
    )
    parser.add_argument(
        "--adversarial",
        type=Path,
        required=True,
        help="Path to the held-out adversarial YAML.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path for the JSON calibration report.",
    )
    parser.add_argument(
        "--encoder",
        choices=("stub", "xlmr"),
        default="stub",
        help="Encoder adapter to use (default: stub).",
    )
    args = parser.parse_args(argv)

    try:
        pipeline = _build_pipeline(args.encoder)
    except Exception as exc:  # pragma: no cover
        print(f"failed to build pipeline: {exc}", file=sys.stderr)
        return 2

    benign = _load_yaml(args.benign).get("cases", [])
    adversarial = _load_yaml(args.adversarial).get("cases", [])

    results: list[CaseResult] = []
    for case in benign + adversarial:
        pipeline_input = _build_pipeline_input(case)
        try:
            out = pipeline.classify(
                pipeline_input["message"], pipeline_input["context"]
            )
        except Exception as exc:
            results.append(
                CaseResult(
                    case_id=str(case.get("case_id", "")),
                    language=str(case.get("language", "und")),
                    tags=tuple(case.get("tags") or ()),
                    expected=case.get("expected") or {},
                    actual={},
                    passed=False,
                    notes=f"pipeline raised: {exc!r}",
                )
            )
            continue
        results.append(_evaluate_case(case, out))

    report = _aggregate(results)
    passed, gate_failures = _gates_pass(report)

    payload = report.to_dict()
    payload["gates"] = {
        "passed": passed,
        "failures": gate_failures,
    }
    if args.output is not None:
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    else:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")

    return 0 if passed else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
