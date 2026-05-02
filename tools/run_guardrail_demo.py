#!/usr/bin/env python3
"""End-to-end demo for the KChat Guardrail pipeline.

Spec references:

* PHASES.md Phase 3 / Phase 6 — sample-data demonstration + perf benchmark.
* ARCHITECTURE.md "Hybrid Local Pipeline" — drives the full pipeline
  end-to-end against either ``MockSLMAdapter`` (no encoder weights
  required) or ``XLMRMiniLMAdapter`` against a locally-cached
  XLM-R MiniLM-L6 checkpoint.

Usage::

    # 1. Mock adapter — works without any model weights.
    python tools/run_guardrail_demo.py --mock

    # 2. Real XLM-R MiniLM-L6 encoder.
    python tools/run_guardrail_demo.py
    python tools/run_guardrail_demo.py --jurisdiction us --community workplace

    # 3. Benchmark + commit results.
    python tools/run_guardrail_demo.py --benchmark --commit-results
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Make the ``kchat-skills/compiler`` package importable.
REPO_ROOT = Path(__file__).resolve().parents[1]
COMPILER_DIR = REPO_ROOT / "kchat-skills" / "compiler"
SAMPLES_PATH = REPO_ROOT / "kchat-skills" / "samples" / "sample_messages.yaml"
BENCH_DIR = REPO_ROOT / "kchat-skills" / "benchmarks"
sys.path.insert(0, str(COMPILER_DIR))

import yaml  # noqa: E402

from benchmark import (  # type: ignore[import-not-found]  # noqa: E402
    BenchmarkCase,
    PipelineBenchmark,
)
from compiler import SkillPackCompiler  # type: ignore[import-not-found]  # noqa: E402
from pipeline import (  # type: ignore[import-not-found]  # noqa: E402
    GuardrailPipeline,
    SkillBundle,
)
from slm_adapter import MockSLMAdapter  # type: ignore[import-not-found]  # noqa: E402
from threshold_policy import ThresholdPolicy  # type: ignore[import-not-found]  # noqa: E402
from xlmr_minilm_adapter import (  # type: ignore[import-not-found]  # noqa: E402
    XLMR_MINILM_MODEL_ID,
    XLMR_MINILM_MODEL_NAME,
    XLMRMiniLMAdapter,
)


MODEL_INSTRUCTIONS = """\
XLM-R MiniLM-L6 weights are not available at: {model_path}

The encoder is ~80 MB. Two ways to make it available:

1) Hugging Face cache (recommended for one-off local runs):
       pip install transformers torch sentencepiece
       python -c "from transformers import AutoTokenizer, AutoModel; \\
           AutoTokenizer.from_pretrained('{model_id}'); \\
           AutoModel.from_pretrained('{model_id}')"
   The weights land in ~/.cache/huggingface and the next run picks
   them up automatically.

2) Local checkout (recommended for offline / CI / mobile bundling):
       huggingface-cli download {model_id} --local-dir ./models/xlmr-minilm-l6
       python tools/run_guardrail_demo.py --model-path ./models/xlmr-minilm-l6

Or rerun this script with --mock to use the deterministic
MockSLMAdapter (no model weights required).
"""


def is_model_available(model_path: str) -> bool:
    """Return ``True`` iff ``model_path`` resolves to usable encoder weights.

    Accepts either a local directory containing the standard
    ``config.json`` + tokenizer / weight files, or a Hugging Face
    model id whose tokenizer + model can be instantiated through the
    transformers cache. The check is local-only — it never attempts a
    network fetch.
    """
    if not model_path:
        return False
    candidate = Path(model_path)
    if candidate.is_dir():
        return (candidate / "config.json").exists()

    # HF model id — only treat as available if the transformers cache
    # already has it so we never trigger a download from this script.
    try:
        from transformers.utils import (  # type: ignore[import-not-found]
            cached_file,
        )
    except Exception:  # noqa: BLE001 — transformers not installed
        return False
    try:
        result = cached_file(
            model_path,
            "config.json",
            _raise_exceptions_for_missing_entries=False,
            local_files_only=True,
        )
    except Exception:  # noqa: BLE001 — anything fishy → not available
        return False
    return result is not None


def load_samples(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or []
    if not isinstance(loaded, list):
        raise ValueError(f"{path}: expected a YAML list at the top level")
    for case in loaded:
        if not isinstance(case, dict):
            raise ValueError(f"{path}: each entry must be a mapping")
        for required in ("case_id", "message", "context"):
            if required not in case:
                raise ValueError(
                    f"{path}: case missing required field '{required}'"
                )
    return loaded


def build_pipeline(
    *,
    use_mock: bool,
    model_path: str,
    jurisdiction: Optional[str],
    community: Optional[str],
) -> tuple[GuardrailPipeline, Any]:
    bundle = SkillBundle(
        jurisdiction_id=(
            f"kchat.jurisdiction.{jurisdiction}.guardrail.v1"
            if jurisdiction
            else None
        ),
        community_overlay_id=(
            f"kchat.community.{community}.guardrail.v1" if community else None
        ),
    )
    if use_mock:
        adapter: Any = MockSLMAdapter()
    else:
        adapter = XLMRMiniLMAdapter(model_path=model_path)
    pipeline = GuardrailPipeline(
        skill_bundle=bundle,
        slm_adapter=adapter,
        threshold_policy=ThresholdPolicy(),
    )
    return pipeline, adapter


def run_cases(
    pipeline: GuardrailPipeline,
    adapter: Any,
    cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        message = dict(case["message"])
        context = dict(case["context"])
        start = time.perf_counter()
        out = pipeline.classify(message, context)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        adapter_latency = float(getattr(adapter, "last_latency_ms", 0.0) or 0.0)
        actions = [k for k, v in (out.get("actions") or {}).items() if v]
        results.append(
            {
                "case_id": case["case_id"],
                "expected_category": case.get("expected_category"),
                "expected_severity": case.get("expected_severity"),
                "category": out.get("category"),
                "severity": out.get("severity"),
                "confidence": out.get("confidence"),
                "actions": actions,
                "reason_codes": out.get("reason_codes") or [],
                "rationale_id": out.get("rationale_id"),
                "pipeline_latency_ms": round(elapsed_ms, 3),
                "adapter_latency_ms": round(adapter_latency, 3),
                "passed": (
                    case.get("expected_category") is None
                    or int(case["expected_category"]) == int(out.get("category", -1))
                ),
            }
        )
    return results


def print_results_table(results: list[dict[str, Any]]) -> None:
    header = (
        "case_id",
        "exp",
        "cat",
        "sev",
        "conf",
        "actions",
        "ms",
        "ok",
    )
    rows = [header]
    for r in results:
        rows.append(
            (
                str(r["case_id"]),
                str(r["expected_category"]),
                str(r["category"]),
                str(r["severity"]),
                f"{(r['confidence'] or 0.0):.2f}",
                ",".join(r["actions"]) or "-",
                f"{r['pipeline_latency_ms']:.2f}",
                "Y" if r["passed"] else "N",
            )
        )
    widths = [
        max(len(row[i]) for row in rows) for i in range(len(rows[0]))
    ]
    for i, row in enumerate(rows):
        line = "  ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(row))
        print(line)
        if i == 0:
            print("  ".join("-" * w for w in widths))
    n_passed = sum(1 for r in results if r["passed"])
    print(f"\n{n_passed}/{len(results)} cases matched expected category.")


def to_benchmark_cases(
    cases: list[dict[str, Any]],
) -> list[BenchmarkCase]:
    out: list[BenchmarkCase] = []
    for case in cases:
        out.append(
            BenchmarkCase(
                case_id=str(case["case_id"]),
                message=dict(case["message"]),
                context=dict(case["context"]),
            )
        )
    return out


def detect_model_version(model_path: str) -> Optional[str]:
    """Return a short version string for the loaded encoder, or None.

    Reads ``config.json`` from a local checkpoint and surfaces the
    ``transformers_version`` / ``model_type`` fields if present.
    """
    candidate = Path(model_path)
    config_paths: list[Path] = []
    if candidate.is_dir():
        config_paths.append(candidate / "config.json")
    for p in config_paths:
        if not p.is_file():
            continue
        try:
            with p.open("r", encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        version = cfg.get("transformers_version") or cfg.get("model_type")
        if isinstance(version, str) and version:
            return version
    return None


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the KChat Guardrail pipeline against the sample-message "
            "corpus and print a results table. Optionally benchmark and "
            "commit results."
        )
    )
    parser.add_argument(
        "--model-path",
        default=XLMR_MINILM_MODEL_ID,
        help=(
            "Path or HF model id for the XLM-R MiniLM-L6 encoder "
            f"(default: {XLMR_MINILM_MODEL_ID})."
        ),
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help=(
            "Use the deterministic MockSLMAdapter instead of the encoder. "
            "No model weights required."
        ),
    )
    parser.add_argument(
        "--jurisdiction",
        default=None,
        help="Jurisdiction overlay name (e.g. 'us', 'de', 'br').",
    )
    parser.add_argument(
        "--community",
        default=None,
        help="Community overlay name (e.g. 'workplace', 'school').",
    )
    parser.add_argument(
        "--samples",
        default=str(SAMPLES_PATH),
        help="Path to the sample-messages YAML (default: kchat-skills/samples/sample_messages.yaml).",
    )
    parser.add_argument(
        "--benchmark",
        action="store_true",
        help="Run the PipelineBenchmark harness and print the BenchmarkReport.",
    )
    parser.add_argument(
        "--benchmark-iterations",
        type=int,
        default=20,
        help="Iterations per case when --benchmark is set (default: 20).",
    )
    parser.add_argument(
        "--benchmark-warmup",
        type=int,
        default=2,
        help="Per-case warm-up iterations for --benchmark (default: 2).",
    )
    parser.add_argument(
        "--commit-results",
        action="store_true",
        help=(
            "Write the benchmark report to "
            "kchat-skills/benchmarks/<model>_results.json. "
            "Requires --benchmark."
        ),
    )
    parser.add_argument(
        "--results-name",
        default=None,
        help=(
            "Override the results filename (without extension). "
            "Default: 'xlmr_minilm_l6_results' (or "
            "'xlmr_minilm_l6_mock_results' with --mock)."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose logging.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    samples_path = Path(args.samples).resolve()
    if not samples_path.exists():
        print(f"error: samples file not found: {samples_path}", file=sys.stderr)
        return 2

    if not args.mock and not is_model_available(args.model_path):
        print(
            MODEL_INSTRUCTIONS.format(
                model_path=args.model_path,
                model_id=XLMR_MINILM_MODEL_ID,
            ),
            file=sys.stderr,
        )
        return 3

    cases = load_samples(samples_path)

    print(f"Loaded {len(cases)} sample cases from {samples_path}")
    if args.jurisdiction or args.community:
        print(
            "Active overlays: "
            f"jurisdiction={args.jurisdiction or '-'} "
            f"community={args.community or '-'}"
        )

    # Compile the prompt — even with --mock we exercise the compiler so
    # the demo doubles as a smoke test for the compiler config. The
    # compiled prompt is no longer fed to a generative model, but the
    # compiler still validates skill-bundle merge + token budgets.
    compiler = SkillPackCompiler(repo_root=REPO_ROOT)
    compiled = compiler.compile(
        jurisdiction=args.jurisdiction,
        community=args.community,
    )
    print(
        f"Compiled prompt: {compiled.instruction_tokens} instruction tokens "
        f"(budget 1800)."
    )

    pipeline, adapter = build_pipeline(
        use_mock=args.mock,
        model_path=args.model_path,
        jurisdiction=args.jurisdiction,
        community=args.community,
    )

    print(
        "Adapter: "
        + (
            "MockSLMAdapter"
            if args.mock
            else f"XLMRMiniLMAdapter -> {args.model_path}"
        )
    )
    print()

    results = run_cases(pipeline, adapter, cases)
    print_results_table(results)

    bench_report = None
    if args.benchmark:
        print()
        print("Running PipelineBenchmark...")
        bench = PipelineBenchmark(pipeline=pipeline)
        bench_report = bench.run(
            to_benchmark_cases(cases),
            iterations=args.benchmark_iterations,
            warmup=args.benchmark_warmup,
        )
        print(
            f"  total_cases : {bench_report.total_cases}\n"
            f"  iterations  : {bench_report.iterations}\n"
            f"  p50 (ms)    : {bench_report.p50_ms:.3f}\n"
            f"  p95 (ms)    : {bench_report.p95_ms:.3f}\n"
            f"  p99 (ms)    : {bench_report.p99_ms:.3f}\n"
            f"  mean (ms)   : {bench_report.mean_ms:.3f}\n"
            f"  max (ms)    : {bench_report.max_ms:.3f}\n"
            f"  min (ms)    : {bench_report.min_ms:.3f}\n"
            f"  passed      : {bench_report.passed}"
        )

    if args.commit_results:
        if bench_report is None:
            print(
                "error: --commit-results requires --benchmark",
                file=sys.stderr,
            )
            return 4
        BENCH_DIR.mkdir(parents=True, exist_ok=True)
        if args.results_name:
            results_name = args.results_name
        elif args.mock:
            results_name = "xlmr_minilm_l6_mock_results"
        else:
            results_name = "xlmr_minilm_l6_results"
        out_path = BENCH_DIR / f"{results_name}.json"
        record = {
            "model_name": (
                XLMR_MINILM_MODEL_NAME if not args.mock else "MockSLMAdapter"
            ),
            "model_id": (
                XLMR_MINILM_MODEL_ID if not args.mock else None
            ),
            "model_version": (
                detect_model_version(args.model_path)
                if not args.mock
                else None
            ),
            "adapter": (
                "MockSLMAdapter" if args.mock else "XLMRMiniLMAdapter"
            ),
            "model_path": None if args.mock else args.model_path,
            "jurisdiction": args.jurisdiction,
            "community": args.community,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "samples_path": str(samples_path.relative_to(REPO_ROOT)),
            "report": {
                "total_cases": bench_report.total_cases,
                "iterations": bench_report.iterations,
                "p50_ms": round(bench_report.p50_ms, 3),
                "p95_ms": round(bench_report.p95_ms, 3),
                "p99_ms": round(bench_report.p99_ms, 3),
                "mean_ms": round(bench_report.mean_ms, 3),
                "max_ms": round(bench_report.max_ms, 3),
                "min_ms": round(bench_report.min_ms, 3),
                "passed": bench_report.passed,
                "per_case_mean_ms": {
                    case_id: round(value, 3)
                    for case_id, value in bench_report.per_case_mean_ms.items()
                },
            },
            "per_case_results": results,
        }
        out_path.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"\nWrote benchmark report to {out_path.relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
