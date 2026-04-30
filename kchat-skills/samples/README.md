# Sample Messages

A curated, privacy-safe corpus used by
[`tools/run_guardrail_demo.py`](../../tools/run_guardrail_demo.py) and
the test suite to drive the full hybrid local pipeline through every
taxonomy category and every protected-speech context.

## What's in here

- [`sample_messages.yaml`](sample_messages.yaml) — ~28 cases. Each case
  is a fixture that maps directly onto
  [`kchat.guardrail.local_signal.v1`](../global/local_signal_schema.json)
  fields plus an `expected_category` / `expected_severity` /
  `description` field used by the demo and tests.

The corpus covers:

- **Safe / benign** — greetings, logistics, multi-language samples
  (English, Vietnamese, Spanish, German, en↔vi code-switching).
- **Scam / fraud** — fake giveaway, credential harvest, advance fee.
  Each case uses high-risk-TLD URLs and scam-keyword combinations the
  deterministic detectors react to (no live phishing domains).
- **PII exposure** — email-, phone-, and credit-card-shaped patterns.
  No real PII; the digits in the credit-card sample are the canonical
  test value `4111 1111 1111 1111`.
- **Child safety** — discussions *about* safeguarding training in a
  minors-aware group. The corpus deliberately contains no
  CSAM-adjacent text; the SLM must not produce a CHILD_SAFETY false
  positive on these.
- **Hate / harassment** — counterspeech and discussions *about*
  harassment. These exercise the protected-speech contexts.
- **Health misinformation** — quoted health claims with explicit
  refutation. Exercises `NEWS_CONTEXT` / `QUOTED_SPEECH_CONTEXT`.
- **Marketplace violations** — admin reminding members of restricted
  goods rules in a marketplace community.
- **Sexual / adult** — explicit adult-only channel with NSFW image
  descriptor. Tests `group_age_mode: adult_only` gating.
- **Extremism** — news reporting on banned organisations (NEWS_CONTEXT).
- **Self-harm** — supportive resource sharing in a mental-health
  community (resource_link_id surfacing).
- **Drugs / weapons** — public-health bulletin (EDUCATION_CONTEXT).
- **Community rule** — mild off-topic in a workplace overlay.

## File format

```yaml
- case_id: "safe-greeting-01"          # stable string id (used in benchmark reports)
  message:                              # `kchat.guardrail.local_signal.v1.message`
    text: "Hey everyone, what time is the meeting tomorrow?"
    lang_hint: "en"
    has_attachment: false
    attachment_kinds: []
    quoted_from_user: false
    is_outbound: false
    # Optional: media_descriptors are moved into `local_signals` by
    # the pipeline (see kchat-skills/compiler/pipeline.py).
    # media_descriptors:
    #   - kind: image
    #     nsfw_score: 0.05
    #     violence_score: 0.0
    #     face_count: 4
  context:                              # `kchat.guardrail.local_signal.v1.context`
    group_kind: "small_group"
    group_age_mode: "mixed_age"
    user_role: "member"
    relationship_known: true
    locale: "en-US"
    jurisdiction_id: null               # set when a jurisdiction overlay is active
    community_overlay_id: null          # set when a community overlay is active
    is_offline: false
  expected_category: 0                  # 0..15 from `taxonomy.yaml`
  expected_severity: 0                  # 0..5 from `severity.yaml`
  description: "Benign scheduling message — should classify as SAFE."
```

`expected_category` / `expected_severity` describe the *deterministic*
verdict the demo expects from `MockSLMAdapter`. A real SLM (Bonsai-1.7B
via `LlamaCppSLMAdapter`) may produce a different but still
schema-conformant output; the demo prints both so divergence is
visible.

## Privacy contract

All fixtures comply with
[`privacy_contract.yaml`](../global/privacy_contract.yaml):

- No real PII (the credit-card number in `pii-credit-card-01` is the
  industry-standard test value `4111 1111 1111 1111`).
- No live phishing domains. URLs use synthetic `*.click` / `*.top` host
  names that the deterministic URL detector flags via the high-risk-TLD
  list in `kchat-skills/compiler/pipeline.py`.
- No CSAM-adjacent text. The CHILD_SAFETY-relevant case is administrative
  language about safeguarding training; the lack of a CHILD_SAFETY
  output is deliberate.

## How to use

### Run the demo against a local llama-server

```bash
# 1. Start llama-server (see "Running with a real SLM" in the top-level
#    README). Then:
python tools/run_guardrail_demo.py
```

### Run the demo with the deterministic mock adapter

```bash
python tools/run_guardrail_demo.py --mock
```

### Run with overlays

```bash
python tools/run_guardrail_demo.py \
  --jurisdiction us \
  --community workplace \
  --mock
```

### Benchmark + commit results

```bash
# Runs PipelineBenchmark over the corpus and writes
# kchat-skills/benchmarks/bonsai_1.7b_results.json (or _mock_*.json
# when --mock is set).
python tools/run_guardrail_demo.py --benchmark --commit-results
```

See [`kchat-skills/benchmarks/README.md`](../benchmarks/README.md) for
the benchmark methodology.

## Extending the corpus

When adding a case:

1. Use a stable `case_id` matching `^[a-z0-9-]+$`.
2. Keep `text` short (the demo prints it; long lines wrap in terminals).
3. Match `expected_category` to one of the 16 taxonomy ids in
   `kchat-skills/global/taxonomy.yaml`.
4. Pin `expected_severity` to the deterministic detector + threshold
   policy outcome — not the SLM's output. The detector behaviour is
   stable across SLM swaps.
5. Avoid literal harm content. The detectors react to *shapes* (URL
   TLDs, keyword combinations, PII patterns); descriptive language
   gives the SLM room to reason.
6. Run `pytest kchat-skills/tests/global/test_sample_messages.py` to
   confirm the case is well-formed.
