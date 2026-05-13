# Running the XLM-R Encoder Classifier

This document covers the on-device XLM-R encoder classifier path:
ONNX export, INT8/INT4 quantisation, running the demo, and
benchmarking. For the high-level project pitch see the
[README](../README.md).

## Overview

The skill packs ship with a backend-agnostic
[`EncoderAdapter`](../kchat-skills/compiler/encoder_adapter.py) protocol;
the [`XLMRAdapter`](../kchat-skills/compiler/xlmr_adapter.py) implementation
loads an **XLM-R** encoder (a multilingual transformer encoder
exported once to ONNX INT8 — see
[`tools/export_xlmr_onnx.py`](../tools/export_xlmr_onnx.py) for the
exact source artifact and conversion pipeline) via
[`onnxruntime`](https://onnxruntime.ai). On-device
the runtime depends on `onnxruntime` + `sentencepiece` + `numpy` only
(no PyTorch / `transformers`). The exported model is ~107 MB INT8,
loads in well under a second on CPU, and runs inference in single-digit
milliseconds per message — well inside the 250 ms p95 envelope (latest
benchmark on this VM measured p95 ≈ 3.3 ms across 100 iterations × 27
cases; see `kchat-skills/benchmarks/xlmr_results.json`).

Two interchangeable embedding-stage classifiers are supported:

1. **Trained linear head** — when
   [`kchat-skills/compiler/data/xlmr_head.npz`](../kchat-skills/compiler/data/)
   is present, the adapter loads it and uses the head's softmax over
   logits as the embedding-stage classifier. The committed checkpoint
   is a `Linear(384, 16)` trained on the 175-example multilingual
   corpus in
   [`training_data.py`](../kchat-skills/compiler/training_data.py) via
   [`train_xlmr_head.py`](../kchat-skills/compiler/train_xlmr_head.py),
   then converted from the trainer's `.pt` to the runtime `.npz` via
   [`tools/export_xlmr_onnx.py`](../tools/export_xlmr_onnx.py)
   (88.5% train accuracy). Rationale ids end in `_trained_v1`.
2. **Zero-shot prototype fallback** — when the trained head is missing
   or fails to load, the adapter falls back to a low-temperature
   softmax over cosine similarities against a fixed bank of category
   prototype embeddings. Rationale ids end in `_proto_v1`.

In either case, deterministic detector branches (CHILD_SAFETY,
PRIVATE_DATA, SCAM_FRAUD, lexicon, NSFW media) run first and beat the
embedding-stage classifier.

The export pipeline also supports an optional INT4 (block-wise
weight-only) variant via `python tools/export_xlmr_onnx.py
--quantize-int4 --output-dir models`. The INT4 model is ~55 MB on
disk vs ~107 MB for INT8 — recommended for mobile devices with tight
storage budgets. Both `MatMul` and `Gather` ops are quantised
(quantising `Gather` is what brings the file under the ~50 MB
target — MatMul-only INT4 leaves the 250 002 × 384 word-embedding
table at FP32 and the file stays north of 370 MB).
`--validate-int4` additionally loads both the INT8 and INT4 sessions,
runs the multilingual smoke corpus through each, and asserts per-row
cosine similarity is above the configurable `--int4-min-cosine`
floor (default `0.94` — empirically `min ≈ 0.95`, `mean ≈ 0.96` on
the smoke corpus; aggressive embedding-`Gather` quantisation costs
~5 cosine points vs INT8 and is what unlocks the storage win, so
callers that need > 0.99 cosine should keep shipping the INT8 file).
To load the INT4 file at runtime, either pass the explicit path to
`XLMRAdapter(model_path="models/xlmr.int4.onnx")` or set
`prefer_int4=True` and let the adapter auto-resolve when the INT4
file is on disk.

`XLMRAdapter.classify()` also returns the raw 384-dim mean-pooled,
L2-normalised XLM-R embedding alongside the classification result
under the internal key `_embedding` (a `list[float]`). The schema
admits underscore-prefixed extras via `patternProperties: {"^_": {}}`,
so downstream consumers (e.g. `chat-storage-search`) can cache the
embedding in their `search_vector` table and avoid re-computing it
during semantic search — a message's XLM-R encoder pass is computed
at most once across guardrail and search.

```bash
# 1. Install the runtime dependencies (already in requirements.txt /
#    pyproject's `demo` extra). The adapter only needs onnxruntime,
#    sentencepiece and numpy.
pip install -r requirements.txt

# 2. One-time ONNX export from the HuggingFace checkpoint. This
#    requires transformers + torch + onnx + onnxscript, but only at
#    export time — they are NOT runtime dependencies. We pin
#    `transformers<5` because v5 changed the positional signature of
#    `XLMRobertaModel.forward()` and breaks the legacy `torch.onnx`
#    tracer; the export script in turn forces `dynamo=False`,
#    because the dynamo-based exporter on torch >= 2.5 emits an
#    INT8 graph that `onnxruntime` rejects (`tensor(float16)`
#    `DequantizeLinear` scales) and an FP32 graph whose
#    `scaled_dot_product_attention` fails on dynamic shapes.
pip install -e ".[export]"
# or, equivalently:
# pip install "transformers<5" torch onnx onnxruntime sentencepiece onnxscript
python tools/export_xlmr_onnx.py --output-dir models
# -> writes models/xlmr.onnx (INT8-quantised, ~107 MB) and
#    models/xlmr.spm (~5 MB SentencePiece tokenizer)

# 2b. (optional) additionally produce an INT4 (block-wise weight-only)
#     ONNX file at models/xlmr.int4.onnx. Recommended for mobile
#     devices with tight storage budgets.
python tools/export_xlmr_onnx.py --quantize-int4 --output-dir models
# -> writes models/xlmr.int4.onnx (~55 MB) alongside the INT8
#    models/xlmr.onnx (both `MatMul` and `Gather` ops quantised to
#    4-bit; quantising the embedding `Gather` is what brings the
#    file size down).

# 2c. (optional) export and validate INT4 against INT8 — loads both
#     sessions, runs a multilingual smoke corpus through each, and
#     asserts per-row cosine similarity is above the configurable
#     --int4-min-cosine floor (default 0.94).
python tools/export_xlmr_onnx.py --quantize-int4 --validate-int4 \
    --output-dir models

# 3. Run the demo against the local ONNX checkpoint
python tools/run_guardrail_demo.py \
    --model-path models/xlmr.onnx --tokenizer-path models/xlmr.spm
python tools/run_guardrail_demo.py --jurisdiction us --community workplace \
    --model-path models/xlmr.onnx --tokenizer-path models/xlmr.spm

# 4. Run benchmarks and commit the results
python tools/run_guardrail_demo.py --benchmark --commit-results \
    --model-path models/xlmr.onnx --tokenizer-path models/xlmr.spm
# -> writes kchat-skills/benchmarks/xlmr_results.json

# Mock-only mode (no model weights required) for quick smoke tests
python tools/run_guardrail_demo.py --mock
```

The demo loads
[`kchat-skills/samples/sample_messages.yaml`](../kchat-skills/samples/sample_messages.yaml)
(format documented in
[`kchat-skills/samples/README.md`](../kchat-skills/samples/README.md)),
compiles the active skill bundle through `SkillPackCompiler`, runs the
full hybrid pipeline against either `XLMRAdapter` or
`MockEncoderAdapter`, and prints a per-case table plus an optional
`PipelineBenchmark` report. See
[`kchat-skills/benchmarks/README.md`](../kchat-skills/benchmarks/README.md)
for the benchmark methodology and committed results.
