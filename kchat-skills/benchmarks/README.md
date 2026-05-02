# Guardrail Benchmark Results

This directory pins the **committed benchmark measurements** for the
hybrid local guardrail pipeline against the **XLM-R** multilingual
encoder classifier — the same model used by
[`XLMRAdapter`](../compiler/xlmr_adapter.py), exported once to ONNX
INT8 by [`tools/export_xlmr_onnx.py`](../../tools/export_xlmr_onnx.py)
and loaded on-device through `onnxruntime`.

The 250 ms p95 latency target from
[`ARCHITECTURE.md` "Performance envelope"](../../ARCHITECTURE.md#performance-envelope)
is enforced by the contract test
[`kchat-skills/tests/global/test_benchmark.py`](../tests/global/test_benchmark.py).
The committed JSON files in this directory are produced by
[`tools/run_guardrail_demo.py`](../../tools/run_guardrail_demo.py)
and capture *real-hardware* measurements on top of that contract.

## Files

- `xlmr_results.json` — committed run against
  `XLMRAdapter` with the locally-exported XLM-R ONNX model.
- `xlmr_mock_results.json` *(optional)* — committed run against
  `MockEncoderAdapter`. Useful as a reference for the *deterministic*
  fast path latency, independent of any model.

## What gets measured

Each case from
[`kchat-skills/samples/sample_messages.yaml`](../samples/sample_messages.yaml)
is sent through the full
[`GuardrailPipeline`](../compiler/pipeline.py) — normalize → detectors →
pack signals → encoder classifier → thresholds → JSON → counters — and
per-message wall-clock latency is recorded.

The committed JSON contains:

- **Provenance** — `model_name`, `model_id`, `model_path`,
  `model_version` (auto-detected from the encoder's `config.json`
  fingerprint), `adapter`, `jurisdiction`, `community`,
  `timestamp_utc`, `samples_path`.
- **Aggregate latency** — `total_cases`, `iterations`, `p50_ms`,
  `p95_ms`, `p99_ms`, `mean_ms`, `min_ms`, `max_ms`, plus the
  per-case mean (`per_case_mean_ms`).
- **Per-case results** — for every sample: the deterministic +
  threshold-policy outcome (category, severity, confidence,
  actions, reason_codes), pipeline + adapter latency, and
  whether the actual category matched the expected category.
- **Pass / fail** — `report.passed = (p95_ms <= 250 ms)`.

## Quick run

The end-to-end benchmark + record + commit workflow is wrapped by
[`tools/run_benchmark.sh`](../../tools/run_benchmark.sh):

```bash
# Mock adapter (no encoder weights needed) — record + commit
./tools/run_benchmark.sh --mock

# Real XLM-R — record + commit
./tools/run_benchmark.sh

# Record only, no git commit
./tools/run_benchmark.sh --mock --no-commit

# Record, commit, and push
./tools/run_benchmark.sh --mock --push
```

The script:

- Verifies Python deps are importable (`onnxruntime`,
  `sentencepiece`, `numpy`, plus the test-time deps already in
  `requirements.txt`) and checks that the XLM-R ONNX model and
  SentencePiece tokenizer are resolvable locally (`models/xlmr.onnx`
  + `models/xlmr.spm` by default, or an explicit `--model-path` /
  `--tokenizer-path`).
- Falls back to `--mock` automatically if the ONNX model is not
  available (and the real XLM-R run is skipped with a warning).
- Runs `tools/run_guardrail_demo.py` with `--benchmark
  --commit-results --benchmark-iterations 100 --benchmark-warmup 5`,
  writing `xlmr_results.json` and/or `xlmr_mock_results.json` here.
- Runs `tools/demo_guardrail.py` to write timestamped JSON +
  Markdown to `results/`.
- Prints a summary of every recorded run with its `p95_ms` against
  the 250 ms target, and **exits non-zero if the target is breached**
  on any run.
- Unless `--no-commit` is passed, runs `git add
  kchat-skills/benchmarks/*.json results/` and commits with message
  `bench: record benchmark results <ISO-8601 UTC>`. Pass `--push` to
  also `git push` the new commit.

## How to reproduce manually

```bash
# 1. Install runtime deps. The on-device runtime only needs
#    onnxruntime + sentencepiece + numpy (already in
#    requirements.txt). Pure-mock runs do not need any of them.
pip install -r requirements.txt

# 2. One-time export of the XLM-R encoder + tokenizer + head .npz
#    (requires transformers + torch + onnx, but only at export time).
pip install transformers torch onnx
python tools/export_xlmr_onnx.py --output-dir models
# -> writes models/xlmr.onnx (~25 MB INT8) and models/xlmr.spm

# 3. Re-run the demo with --benchmark --commit-results
cd /path/to/slm-guardrail
python tools/run_guardrail_demo.py --benchmark --commit-results
```

The script writes `xlmr_results.json` here. Commit the file
to pin the measurement to the current encoder weights.

### Mock-adapter reference

```bash
python tools/run_guardrail_demo.py --mock --benchmark --commit-results
# writes xlmr_mock_results.json
```

## How to read the results

- `report.p95_ms` is the headline number. A value `> 250` means the
  release-blocking p95 SLO is violated; investigate before merging.
- `report.per_case_mean_ms` highlights outliers — cases consistently
  slower than the median often correspond to large lexicon hits or
  high `media_descriptors` counts on the input.
- `per_case_results[*].passed` is `false` when the encoder-driven
  category diverges from the deterministic expectation. A few
  divergences are expected for protected-speech cases (the encoder
  applies context the detectors cannot); a *systematic* divergence
  usually indicates a regression in the classification head or in the
  encoder weights.
