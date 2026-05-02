# Guardrail Benchmark Results

This directory pins the **committed benchmark measurements** for the
hybrid local guardrail pipeline against
[`nreimers/mMiniLMv2-L6-H384-distilled-from-XLMR-Large`](https://huggingface.co/nreimers/mMiniLMv2-L6-H384-distilled-from-XLMR-Large)
— the **XLM-R MiniLM-L6** multilingual encoder classifier — loaded
through `transformers`.

The 250 ms p95 latency target from
[`ARCHITECTURE.md` "Performance envelope"](../../ARCHITECTURE.md#performance-envelope)
is enforced by the contract test
[`kchat-skills/tests/global/test_benchmark.py`](../tests/global/test_benchmark.py).
The committed JSON files in this directory are produced by
[`tools/run_guardrail_demo.py`](../../tools/run_guardrail_demo.py)
and capture *real-hardware* measurements on top of that contract.

## Files

- `xlmr_minilm_l6_results.json` — committed run against
  `XLMRMiniLMAdapter` with locally-loaded XLM-R MiniLM-L6 weights.
- `xlmr_minilm_l6_mock_results.json` *(optional)* — committed run against
  `MockSLMAdapter`. Useful as a reference for the *deterministic*
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

# Real XLM-R MiniLM-L6 — record + commit
./tools/run_benchmark.sh

# Record only, no git commit
./tools/run_benchmark.sh --mock --no-commit

# Record, commit, and push
./tools/run_benchmark.sh --mock --push
```

The script:

- Verifies Python deps are importable (`transformers`, `torch`,
  `sentencepiece`, plus the test-time deps already in
  `requirements.txt`) and checks that the XLM-R MiniLM-L6 weights are
  resolvable locally (Hugging Face cache or an explicit
  `--model-path`).
- Falls back to `--mock` automatically if the encoder weights are
  not available (and the real XLM-R MiniLM-L6 run is skipped with a
  warning).
- Runs `tools/run_guardrail_demo.py` with `--benchmark
  --commit-results --benchmark-iterations 100 --benchmark-warmup 5`,
  writing `xlmr_minilm_l6_results.json` and/or
  `xlmr_minilm_l6_mock_results.json` here.
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
# 1. Install runtime deps (transformers + torch are required for the
#    real-model run; pure-mock runs do not need them).
pip install -r requirements.txt
pip install transformers torch sentencepiece

# 2. Cache the XLM-R MiniLM-L6 weights (~80 MB) into the local
#    Hugging Face cache. The adapter loads them offline after this.
python -c "from transformers import AutoTokenizer, AutoModel; \
    AutoTokenizer.from_pretrained('nreimers/mMiniLMv2-L6-H384-distilled-from-XLMR-Large'); \
    AutoModel.from_pretrained('nreimers/mMiniLMv2-L6-H384-distilled-from-XLMR-Large')"

# 3. Re-run the demo with --benchmark --commit-results
cd /path/to/slm-guardrail
python tools/run_guardrail_demo.py --benchmark --commit-results
```

The script writes `xlmr_minilm_l6_results.json` here. Commit the file
to pin the measurement to the current encoder weights.

### Mock-adapter reference

```bash
python tools/run_guardrail_demo.py --mock --benchmark --commit-results
# writes xlmr_minilm_l6_mock_results.json
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
