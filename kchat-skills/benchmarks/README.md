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

## Post-optimization results (2026-05-03)

Re-run after the cross-repo optimization wave (improvements A–H:
unified llama-server, Apple MLX scaffolding, embedding cache, INT4
quantization, DirectML EP for Windows, model warm-up, Whisper MLX).
For slm-guardrail specifically the relevant changes that landed
between the previous baseline and this run were:

- **D + E** — cross-pipeline `_embedding` cache + INT4 export tier
  (PR #19).
- **F (docs)** — Windows DirectML EP path documented as
  preferred-on-Windows reference impl; no runtime change here, the
  on-device adapter still uses the CPU EP on Linux.

### Environment

- CPU: AMD EPYC 7763 64-Core Processor (8 cores allocated)
- OS: Ubuntu 22.04.5 LTS, Linux x86_64
- Python: 3.12.8
- onnxruntime: 1.25.1 (CPU EP)
- Encoder weights: `models/xlmr.onnx` (107 MB INT8, exported via
  `tools/export_xlmr_onnx.py` from
  `nreimers/mMiniLMv2-L6-H384-distilled-from-XLMR-Large`)
- Tokenizer: `models/xlmr.spm` (5 MB SentencePiece)
- Iterations: 100 (5 warm-up) per case; 27 cases.

### Headline pipeline latency — XLMRAdapter (real model)

| Metric    | Previous (2026-05-02) | Current (2026-05-03) | Δ       | Target |
|-----------|----------------------:|---------------------:|---------|-------:|
| p50 (ms)  |                 2.778 |                2.483 | −10.6%  |    —   |
| p95 (ms)  |                 3.338 |                2.975 | −10.9%  | ≤ 250  |
| p99 (ms)  |                 4.415 |                3.048 | −31.0%  |    —   |
| mean (ms) |                 2.757 |                2.421 | −12.2%  |    —   |
| max (ms)  |                59.332 |                7.899 | −86.7%  |    —   |
| min (ms)  |                 1.654 |                1.571 |  −5.0%  |    —   |
| passed    |                  true |                 true |    —    |  true  |

p95 stays two orders of magnitude under the 250 ms SLO; p99 and max
in particular tightened materially (the previous 59 ms tail outlier
on the warm path is gone).

### Cold-start / first-inference latency

The first case (`safe-greeting-01`) pays the
`onnxruntime.InferenceSession(...)` initialisation cost on the
runtime adapter:

| Metric                         | Previous | Current | Δ      |
|--------------------------------|---------:|--------:|--------|
| `safe-greeting-01` adapter ms  | 1346.802 |  781.79 | −42.0% |
| `safe-greeting-01` pipeline ms | 1346.905 |  782.06 | −42.0% |

This is consistent with the warm-up improvements landed across the
companion repos (cv-guard PR #26, slm-chat-demo PR #68): the
on-device pattern is to pre-warm on app boot so this cost never
hits user-perceived latency. The number above is the unwarmed
first-call cost when the session is constructed lazily inside the
benchmark process; subsequent calls (rows 2–27 in the benchmark
table) all complete in 1.7–3.2 ms.

### Warm inference per-case mean

All 27 cases stay between 1.65 ms (`media-image-benign-01`) and
3.03 ms (`scam-fake-giveaway-01`); see `xlmr_results.json
.report.per_case_mean_ms` for the full breakdown. No case crossed
4 ms.

### Classification accuracy

27 / 27 cases match the expected `(category, severity)` from the
sample corpus, identical to the previous run. Spot-checked
high-severity rows (scams, PII, NSFW media) all reach
`critical_intervention` / `warn,suggest_redact` as before.

### Mock-adapter reference (unchanged purpose)

`xlmr_mock_results.json` shows the deterministic fast-path latency
without any neural model:

| Metric    | Current |
|-----------|--------:|
| p50 (ms)  |   0.038 |
| p95 (ms)  |   0.049 |
| p99 (ms)  |   0.066 |
| mean (ms) |   0.037 |

This is the floor the pipeline could hit if the encoder were
replaced with rule-only logic; it bounds how much of the 2.4 ms
mean is attributable to the XLM-R encoder forward pass (≈ 2.4 ms)
versus the surrounding pipeline (~0.04 ms).

### Cross-community / cross-country demo

`results/demo_results_2026-05-03T06-05-25Z.{json,md}` exercises 51
scenarios across the jurisdiction + community overlay matrix:

- 13 flagged, 38 safe (matches the committed demo expectations).
- p50 = 0.037 ms, p95 = 0.054 ms, p99 = 0.069 ms (mock-fast-path).
- Verdict: PASS vs the 250 ms p95 target.

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
