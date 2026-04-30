# Guardrail Benchmark Results

This directory pins the **committed benchmark measurements** for the
hybrid local guardrail pipeline against the
[`Bonsai-1.7B`](https://huggingface.co/prism-ml/Bonsai-1.7B-gguf)
GGUF model running under
[`kennguy3n/llama.cpp` (branch `prism`)](https://github.com/kennguy3n/llama.cpp/tree/prism).

The 250 ms p95 latency target from
[`ARCHITECTURE.md` "Performance envelope"](../../ARCHITECTURE.md#performance-envelope)
is enforced by the contract test
[`kchat-skills/tests/global/test_benchmark.py`](../tests/global/test_benchmark.py).
The committed JSON files in this directory are produced by
[`tools/run_guardrail_demo.py`](../../tools/run_guardrail_demo.py)
and capture *real-hardware* measurements on top of that contract.

## Files

- `bonsai_1.7b_results.json` — committed run against
  `LlamaCppSLMAdapter` + a real `llama-server` instance loading
  `Bonsai-1.7B.gguf`.
- `bonsai_1.7b_mock_results.json` *(optional)* — committed run against
  `MockSLMAdapter`. Useful as a reference for the *deterministic*
  fast path latency, independent of any model.

## What gets measured

Each case from
[`kchat-skills/samples/sample_messages.yaml`](../samples/sample_messages.yaml)
is sent through the full
[`GuardrailPipeline`](../compiler/pipeline.py) — normalize → detectors →
pack signals → SLM → thresholds → JSON → counters — and per-message
wall-clock latency is recorded.

The committed JSON contains:

- **Provenance** — `model_name`, `model_url`, `llama_cpp_repo`,
  `llama_cpp_commit` (auto-detected if a sibling `llama.cpp` checkout is
  present), `adapter`, `server_url`, `jurisdiction`, `community`,
  `timestamp_utc`, `samples_path`.
- **Aggregate latency** — `total_cases`, `iterations`, `p50_ms`,
  `p95_ms`, `p99_ms`, `mean_ms`, `min_ms`, `max_ms`, plus the
  per-case mean (`per_case_mean_ms`).
- **Per-case results** — for every sample: the deterministic +
  threshold-policy outcome (category, severity, confidence,
  actions, reason_codes), pipeline + adapter latency, and
  whether the actual category matched the expected category.
- **Pass / fail** — `report.passed = (p95_ms <= 250 ms)`.

## How to reproduce

```bash
# 1. Build kennguy3n/llama.cpp (branch: prism)
git clone --branch prism https://github.com/kennguy3n/llama.cpp.git ../llama-cpp
cd ../llama-cpp && cmake -B build && cmake --build build --config Release && cd -

# 2. Download Bonsai-1.7B.gguf (~1 GB)
wget https://huggingface.co/prism-ml/Bonsai-1.7B-gguf/resolve/main/Bonsai-1.7B.gguf \
  -O Bonsai-1.7B.gguf

# 3. Start llama-server
./build/bin/llama-server -m Bonsai-1.7B.gguf --port 8080 -c 4096 &
LLAMA_PID=$!
trap "kill ${LLAMA_PID}" EXIT

# 4. Re-run the demo with --benchmark --commit-results
cd /path/to/slm-guardrail
python tools/run_guardrail_demo.py --benchmark --commit-results
```

The script writes `bonsai_1.7b_results.json` here. Commit the file to
pin the measurement to the current llama.cpp commit + GGUF artefact.

### Mock-adapter reference

```bash
python tools/run_guardrail_demo.py --mock --benchmark --commit-results
# writes bonsai_1.7b_mock_results.json
```

## How to read the results

- `report.p95_ms` is the headline number. A value `> 250` means the
  release-blocking p95 SLO is violated; investigate before merging.
- `report.per_case_mean_ms` highlights outliers — cases consistently
  slower than the median often correspond to large lexicon hits or
  high `media_descriptors` counts on the input.
- `per_case_results[*].passed` is `false` when the SLM-driven category
  diverges from the deterministic expectation. A few divergences are
  expected for protected-speech cases (the SLM applies context the
  detectors cannot); a *systematic* divergence usually indicates a
  prompt-budget regression or a model degradation.

## Privacy

The committed JSON contains only:

- Detector signal categories (e.g. `URL_RISK`, `EMAIL`).
- Output schema fields (severity, category, confidence, actions, reason
  codes, rationale_id).
- Per-case latency.

It deliberately does **not** capture message text, embeddings, hashes,
or any identifier — see
[`privacy_contract.yaml`](../global/privacy_contract.yaml).
