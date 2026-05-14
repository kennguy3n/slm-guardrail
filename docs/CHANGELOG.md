# Changelog

Historical record of changes to the slm-guardrail repository. For
the current high-level status see [`../PROGRESS.md`](../PROGRESS.md);
for the build sequence see [`../PHASES.md`](../PHASES.md).

The first section summarises the deliverables shipped in each
development phase. The second section is a session-by-session
changelog ordered newest first.

---

## Phase Deliverables Summary

### Phase 0 — Foundation

- Repository folder structure (`kchat-skills/global`, `.../jurisdictions`,
  `.../communities`, `.../prompts`, `.../compiler`, `.../tests`,
  `.../docs`).
- `kchat-skills/global/baseline.yaml` — global baseline skill stub
  (decision-policy thresholds, skill-selection block, child-safety
  policy stub, references).
- `kchat-skills/global/taxonomy.yaml` — 16-category global taxonomy.
- `kchat-skills/global/severity.yaml` — 0–5 severity rubric with
  child-safety floor of 5.
- `kchat-skills/global/output_schema.json` — constrained encoder
  classifier JSON output schema (Draft-07).
- `kchat-skills/tests/global/` — pytest validation suite for the
  files above.
- `requirements.txt` + `pyproject.toml` (pytest, PyYAML,
  jsonschema).
- `kchat-skills/global/local_signal_schema.json` — encoder classifier
  input contract.
- `kchat-skills/global/privacy_contract.yaml` — eight non-negotiable
  privacy rules expressed as enforceable constraints.

### Phase 1 — Global Baseline Skill + First Community Overlays

- Complete `kchat.global.guardrail.baseline` with full privacy
  rules, input contract, decision-policy, and `skill_selection`
  blocks.
- Runtime classifier-bundle instruction prompt (10-rule instruction)
  and compiled-prompt format reference at `kchat-skills/prompts/`.
- 8 community overlay skills: `community.school`, `community.family`,
  `community.workplace`, `community.adult_only`,
  `community.marketplace`, `community.health_support`,
  `community.political`, `community.gaming`.
- Local expiring counter implementation
  (`kchat-skills/compiler/counters.py`) with pluggable device
  keystore, group / counter scoping, time-windowed expiry, and
  `counter_updates`-array consumption.
- Test-suite template
  (`kchat-skills/tests/test_suite_template.yaml`) and first round of
  baseline test cases
  (`kchat-skills/tests/global/test_baseline_cases.py`).

### Phase 2 — Jurisdiction Archetype Overlays

- `kchat-skills/jurisdictions/_template/overlay.yaml`.
- `jurisdiction.archetype-strict-adult`.
- `jurisdiction.archetype-strict-hate`.
- `jurisdiction.archetype-strict-marketplace`.
- Local language asset structure (`lexicons/`, `normalization.yaml`,
  transliteration references) for all three archetype overlays.
- Per-archetype test suites including minority-language and
  code-switching false-positive tests (target
  `minority_language_false_positive ≤ 0.07`).

### Phase 3 — Hybrid Local Pipeline + Encoder Classifier Integration

- 7-step hybrid pipeline implementation (normalize → detectors →
  pack signals → encoder classifier → thresholds → JSON → counters)
  at `kchat-skills/compiler/pipeline.py`.
- Encoder classifier runtime adapter interface and reference adapter
  at `kchat-skills/compiler/encoder_adapter.py` (`EncoderAdapter`
  Protocol + `MockEncoderAdapter`).
- Hard-coded threshold enforcement (`label_only=0.45`, `warn=0.62`,
  `strong_warn=0.78`, `critical_intervention=0.85`) at
  `kchat-skills/compiler/threshold_policy.py`, including
  child-safety severity-floor handling.
- Metric validation: `child_safety_recall ≥ 0.98`,
  `protected_speech_false_positive ≤ 0.05`, p95 latency ≤ 250 ms —
  `kchat-skills/compiler/metric_validator.py` plus
  `GuardrailPipeline.validate_metrics`.

### Phase 4 — Skill Pack Compiler + Signing

- Compiler pipeline (authoring → review → tests → prompt compiler →
  signed bundle) at `kchat-skills/compiler/compiler.py`
  (`SkillPackCompiler`, conflict resolution, 1800-token budget
  enforcement).
- Skill passport schema and ed25519 signing
  (`kchat-skills/compiler/skill_passport.py` plus
  `skill_passport.schema.json`).
- Anti-misuse validation rules and tests
  (`kchat-skills/compiler/anti_misuse.py`).
- Compiled-prompt reference outputs for the baseline and archetype
  packs (initial 14 combinations under
  `kchat-skills/prompts/compiled_examples/`).

### Phase 5 — Country-Specific Expansion

- 40 country-specific jurisdiction overlays (first expansion wave).
- Localized lexicons and normalization rules per country.
- Per-country test suites with passing metrics.

### Phase 6 — Scale, Audit, Continuous Improvement

- Scaled skill library to 100 packs (1 baseline + 3 archetypes + 59
  country packs + 38 community overlays).
- Bias auditing for protected-class and minority-language effects
  (`kchat-skills/compiler/bias_audit.py`).
- Versioning, rollback, and expiry-review workflows
  (`kchat-skills/compiler/pack_lifecycle.py`).
- Adversarial / obfuscation test corpus — 60 cases across 6
  techniques (homoglyph, leetspeak, code-switching, unicode tricks,
  whitespace insertion, image-text evasion) under
  `kchat-skills/tests/adversarial/`.
- Regulatory alignment documentation (EU DSA, NIST AI RMF, UNICEF /
  ITU child online protection) under `kchat-skills/docs/regulatory/`
  plus a contract test at
  `kchat-skills/tests/global/test_regulatory_docs.py`.
- Performance benchmarking harness
  (`kchat-skills/compiler/benchmark.py`) plus contract tests
  enforcing the 250 ms p95 latency target.
- Community feedback and appeal flow
  (`kchat-skills/compiler/appeal_flow.py`) with the privacy
  invariant pinned: no message text, hashes, or embeddings
  persisted at any layer.

---

## Session Changelog

### 2026-05-03 — XLM-R `_embedding` pass-through and INT4 export

- `XLMRAdapter.classify()` now returns the raw 384-dim L2-normalised
  XLM-R embedding alongside the classification result under the
  internal key `_embedding`. The output schema admits
  underscore-prefixed extras via `patternProperties: {"^_": {}}` so
  downstream consumers can cache a message's embedding once across
  guardrail and search.
- `tools/export_xlmr_onnx.py` gains a `--quantize-int4` flag that
  produces `models/xlmr.int4.onnx` alongside the existing
  `models/xlmr.onnx`. `--validate-int4` cross-checks per-row cosine
  similarity between the two checkpoints with a configurable
  `--int4-min-cosine` floor (default 0.94).
- `XLMRAdapter` accepts an explicit `model_path` argument and a
  `prefer_int4=True` hint that auto-resolves to the INT4 file when
  present. The INT4 model is ~55 MB on disk vs ~107 MB for INT8.
- README "Running with XLM-R" section documents the INT4 export
  workflow and the storage / cosine trade-off.

### 2026-05-02 — XLM-R unification and ONNX conversion

- Renamed the encoder backend to plain "XLM-R" throughout docs and
  code. The on-device adapter is `XLMRAdapter`; the underlying source
  artifact is documented inside `tools/export_xlmr_onnx.py`.
- The on-device adapter now loads an ONNX INT8 model via
  `onnxruntime` and tokenises with `sentencepiece` directly. The
  runtime no longer imports `transformers` or `torch`.
- Runtime dependencies replaced PyTorch / `transformers` with
  `onnxruntime` + `sentencepiece` + `numpy`. The trainer
  (`train_xlmr_head.py`) and the export script remain offline-only
  tools.
- Head weights converted from `.pt` to `.npz`; the on-device adapter
  loads them with `numpy.load()` and runs the head as a pure numpy
  matmul.

### 2026-05-02 — EncoderAdapter rename

- Renamed `slm_adapter.py` → `encoder_adapter.py`. The Protocol is
  now `EncoderAdapter` and the deterministic reference is
  `MockEncoderAdapter`. Pipeline parameter and attribute renamed
  `slm_adapter` → `encoder_adapter`. Tests, demos, benchmark scripts,
  and docs all updated.
- Documentation across the repository simplified to describe the
  encoder-only architecture without legacy backend references.

### 2026-05-02 — Protected-speech context demotion and trained linear head

- `kchat-skills/compiler/pipeline.derive_context_hints()` infers
  four protected-speech contexts (NEWS_CONTEXT, EDUCATION_CONTEXT,
  COUNTERSPEECH_CONTEXT, QUOTED_SPEECH_CONTEXT) from
  `message.quoted_from_user` plus the active community overlay id.
- `threshold_policy.ThresholdPolicy` adds a demotion rule: any
  non-SAFE / non-CHILD_SAFETY verdict carrying a protected-speech
  reason code is demoted to SAFE with
  `rationale_id = safe_protected_speech_v1`. The CHILD_SAFETY floor
  is preserved above all else.
- `kchat-skills/compiler/training_data.py` ships a 175-example
  multilingual labelled corpus; `train_xlmr_head.py` fits a
  `Linear(384, 16)` head on top of frozen XLM-R [CLS] embeddings
  (88.5% train accuracy) and exports it to
  `kchat-skills/compiler/data/xlmr_head.npz`. `XLMRAdapter` loads
  the trained head at startup and falls back to the zero-shot
  prototype path when missing.
- Protected-speech demotion is scoped to the embedding-head path
  only — deterministic-signal branches (PII, SCAM_FRAUD, lexicon,
  NSFW media) emit their reason codes verbatim.

### 2026-05-01 — XLM-R encoder classifier integration

- Introduced the XLM-R encoder adapter
  (`kchat-skills/compiler/xlmr_adapter.py`, originally
  `xlmr_minilm_adapter.py`) implementing the `EncoderAdapter`
  Protocol against the multilingual XLM-R encoder.
- The adapter runs each classification as a cosine-similarity
  argmax against a bank of 16 category prototype embeddings, blended
  with deterministic local signals (URL risk, PII, scam patterns,
  lexicon hits, media descriptors).
- Falls back to a SAFE output when the encoder weights are missing,
  runtime dependencies are unavailable, or inference raises.

### 2026-04-30 — Vietnam (vi-VN) demo expansion

- `tools/demo_guardrail.py` — added 8 Vietnam scenarios covering 4
  benign Vietnamese-language community scenarios, 2 harmful
  Vietnamese-language scenarios (scam, PII leak), and 2 Vietnamese
  ↔ English code-switching scenarios.
- Total demo coverage: 51 scenarios across 11+ countries, 8+
  community types, and 10+ mixed-language / code-switching
  scenarios. 250 ms p95 target still PASS at 51-case corpus.
- `results/demo_results_<timestamp>.json/.md` regenerated to include
  Vietnam in the per-scenario tables and per-(jurisdiction,
  community) latency table.

### 2026-04-30 — Cross-community / cross-country demo

- `tools/demo_guardrail.py` — end-to-end demo exercising the
  guardrail pipeline across 8+ community types, 10+ countries, and
  8+ mixed-language / code-switching scenarios.
- Captures classification results (category, severity, confidence,
  actions, reason codes), per-group latency metrics (p50, p95, p99,
  mean, max, min), and pass / fail against the 250 ms p95 target.
- Writes structured JSON and human-readable Markdown reports to
  `results/demo_results_<timestamp>.{json,md}`.

### 2026-04-30 — Sample data layer

- Curated sample-message corpus
  (`kchat-skills/samples/sample_messages.yaml`) with 27 messages
  covering every taxonomy category and multi-language samples
  (English, Vietnamese, Spanish, German, en↔vi code-switching). All
  cases comply with the privacy contract.
- `tools/run_guardrail_demo.py` — driver that instantiates
  `XLMRAdapter` (or `MockEncoderAdapter` with `--mock`) and runs the
  pipeline end-to-end against the curated sample corpus.
- `kchat-skills/benchmarks/` — committed benchmark results
  directory; `xlmr_results.json` is generated on demand by the demo
  script.
- New tests cover adapter Protocol conformance, fallback behaviour,
  output schema coercion, classification head behaviour, signal
  priority overrides, sample data structural validation, and
  `MockEncoderAdapter` smoke tests.

### 2026-04-29 — Skill expansion to 100 packs

- 30 additional community overlays (religious, sports,
  creative_arts, education_higher, volunteer, neighborhood,
  parenting, dating, fitness, travel, book_club, music, photography,
  cooking, tech_support, language_learning, pet_owners,
  environmental, journalism, legal_support, mental_health, startup,
  nonprofit, seniors, lgbtq_support, veterans, hobbyist, science,
  open_source, emergency_response). Each passes
  `anti_misuse.validate_pack`.
- 19 additional country packs (RU, UA, RO, GR, CZ, HU, DK, FI, NO,
  IE, IL, IQ, MA, GH, TZ, ET, DZ, EC, UY). Each ships
  `overlay.yaml`, `normalization.yaml`, and per-language lexicons.
- Per-pack test files for all 49 new skills.
- 19 new compiled-prompt references at
  `kchat-skills/prompts/compiled_examples/country_<cc>.txt`
  (73 total).
- Regulatory alignment documents extended to cover all 59 country
  packs.

### 2026-04-29 — Phase 5 full expansion + Phase 6 close

- 35 additional country packs (MX, CA, AR, CO, CL, PE, FR, GB, ES,
  IT, NL, PL, SE, PT, CH, AT, KR, ID, PH, TH, VN, MY, SG, TW, PK,
  BD, NG, ZA, EG, SA, AE, KE, AU, NZ, TR). Each country ships
  `overlay.yaml` + `normalization.yaml` + one `lexicons/<lang>.yaml`
  per primary language. CHILD_SAFETY severity floor 5 enforced on
  every pack.
- 35 new per-country test files plus ~140 additional benign
  minority-language and code-switching false-positive cases.
- 35 new reference compiled prompts (54 total at this stage).
- Adversarial test corpus
  (`kchat-skills/tests/adversarial/corpus.yaml`) — 60 cases across
  6 evasion techniques with per-technique decoders (homoglyph fold,
  leet decode, zero-width / BiDi strip, NFKC/NFKD) and a 0.80
  detection-rate floor.
- Regulatory alignment documents — EU DSA, NIST AI RMF 1.0, and
  UNICEF / ITU Child Online Protection — plus a contract test
  pinning artefact references and the per-jurisdiction statutory
  table.
- Latency benchmark harness (`kchat-skills/compiler/benchmark.py`):
  `PipelineBenchmark`, `BenchmarkReport`, `default_benchmark_cases`.
  `passed` iff p95 ≤ 250 ms.
- Appeal flow (`kchat-skills/compiler/appeal_flow.py`):
  closed-enum `user_context` and `recommendation`, CHILD_SAFETY
  appeals short-circuit to `urgent_review`. Privacy invariant
  pinned: no text, hash, or embedding fields.

### 2026-04-29 — Phase 5 first wave + Phase 6 partial

- First wave of 5 country-specific jurisdiction overlays (US, DE,
  BR, IN, JP). Each ships overlay, normalization, and per-language
  lexicons with provenance metadata. All pass
  `anti_misuse.validate_pack`.
- Per-country structural tests plus 21 new minority-language /
  code-switching false-positive cases.
- Bias auditor (`kchat-skills/compiler/bias_audit.py`) — per-class
  and per-minority-language false-positive rate computation,
  disparity detection, structured `BiasAuditReport`. Bound to
  `metric_validator.SAFE_CATEGORY` and the
  `minority_language_false_positive` shipping target.
- Pack store (`kchat-skills/compiler/pack_lifecycle.py`) — tracks
  signed `PackVersion` entries per `skill_id`, retains
  `MAX_RETAINED_VERSIONS=3` for rollback, exposes registration,
  retrieval, rollback, expiry checks, JSON round-trip, and a
  30-day review window.
- 47 new tests covering bias auditing, pack lifecycle, and an
  integration test running the bias auditor against the existing
  minority-language FP corpus.

### 2026-04-29 — Phase 3 close + Phase 4 complete

- Metric validation framework
  (`kchat-skills/compiler/metric_validator.py`) computes recall /
  precision / FP / p95 latency from test-case results against the
  seven shipping thresholds and exposes a
  `GuardrailPipeline.validate_metrics` hook the compiler uses to
  refuse to sign regressing bundles.
- Skill-pack compiler (`kchat-skills/compiler/compiler.py`) loads
  baseline + jurisdiction + community YAML, applies the
  `baseline.skill_selection` conflict-resolution rules, and emits a
  single compiled prompt within the 1800 instruction-token budget.
- Skill passport (`kchat-skills/compiler/skill_passport.py` plus
  `skill_passport.schema.json`) — ed25519 signing / verification via
  `cryptography`, max 18-month expiry, model-compatibility checks,
  deterministic JSON signing payload, Draft-07 JSON Schema.
- Anti-misuse validator (`kchat-skills/compiler/anti_misuse.py`)
  rejects vague categories, invented categories, jurisdiction packs
  missing legal / cultural review signers, community packs missing
  trust-and-safety signer, severity floors ≥ 4 without
  protected-speech allowed contexts, overlays redefining
  `privacy_rules`, and lexicons without provenance.
- 14 reference compiled prompts under
  `kchat-skills/prompts/compiled_examples/`, all under the 1800-token
  instruction budget, plus 175+ new tests across the compiler
  modules.
- `cryptography>=42.0` added to `requirements.txt` and
  `pyproject.toml` for ed25519 signing.

### 2026-04-29 — Phase 2 close + Phase 3 partial

- Third archetype overlay
  (`kchat-skills/jurisdictions/archetype-strict-marketplace/`) —
  DRUGS_WEAPONS (category 11) and ILLEGAL_GOODS (category 12) at
  severity_floor 4, with full lexicons, normalization, forbidden
  criteria, and protected-speech contexts.
- Per-archetype minority-language / code-switching false-positive
  corpus pins the 0.07 target declared in the test-suite template.
- 7-step hybrid local pipeline
  (`kchat-skills/compiler/pipeline.py`) — normalize → deterministic
  detectors → signal packaging → encoder classifier adapter →
  threshold policy → output → counter updates — with a `SkillBundle`
  carrier and a fully offline-capable `GuardrailPipeline.classify`
  entry point.
- Backend-agnostic `EncoderAdapter` Protocol
  (`kchat-skills/compiler/encoder_adapter.py`) plus a deterministic
  `MockEncoderAdapter`.
- Immutable `ThresholdPolicy` with hard-coded thresholds,
  uncertainty handling (< 0.45 → SAFE), lower-numbered-category
  tie-break, and the CHILD_SAFETY severity-5 floor.

### 2026-04-29 — Phase 1 close + Phase 2 partial

- Device-local, group-scoped, expiring counter store
  (`kchat-skills/compiler/counters.py`) with a pluggable
  `DeviceKeystore`, XOR-stream + HMAC-SHA256 reference at-rest
  encryption, JSON serialisation, and consumption of the
  `counter_updates` array from `kchat.guardrail.output.v1`.
- Metrics framework
  (`kchat-skills/tests/test_suite_template.yaml`) plus per-category
  and threshold-boundary coverage rules.
- First round of baseline test cases covering all 16 taxonomy
  categories, the four protected-speech contexts, the child-safety
  floor, and the full set of decision-policy threshold boundaries
  (0.44, 0.45, 0.62, 0.78, 0.85).
- Jurisdiction overlay template
  (`kchat-skills/jurisdictions/_template/overlay.yaml`) with all
  required activation, forbidden-criteria, local-definitions,
  language-assets, overrides, allowed-contexts, and user-notice
  blocks.
- First two archetype overlays:
  `jurisdiction.archetype-strict-adult` (SEXUAL_ADULT severity_floor
  5) and `jurisdiction.archetype-strict-hate` (EXTREMISM
  severity_floor 5, HATE severity_floor 4) with full lexicons,
  normalization, and protected-speech contexts.

### 2026-04-29 — Phase 0 complete + Phase 1 partial

- Encoder classifier input contract (`local_signal_schema.json`,
  Draft-07 JSON Schema).
- Privacy contract (`privacy_contract.yaml`) — eight non-negotiable
  privacy rules expressed as enforceable constraints.
- Complete (non-stub) baseline with full privacy rules and input
  contract references.
- Runtime classifier-bundle instruction prompt
  (`runtime_instruction.txt`) — 10-rule instruction.
- Compiled-prompt format reference and workplace example.
- First 8 community overlay skills and the community overlay
  template.
- Test suites for `local_signal_schema`, `privacy_contract`,
  prompts, and all 8 community overlays.

### 2026-04-29 — Phase 0 partial

- Repository structure for `kchat-skills/` matching the recommended
  folder layout in `ARCHITECTURE.md`.
- Global baseline stub wired to taxonomy, severity, output schema,
  decision-policy thresholds, skill-selection block, and
  child-safety policy stub.
- 16-category global taxonomy (`taxonomy.yaml`).
- 0–5 severity rubric (`severity.yaml`) with child-safety floor of
  5.
- Constrained encoder classifier JSON output schema
  (`output_schema.json`, Draft-07).
- Pytest validation suite covering taxonomy, severity, output
  schema, and baseline structure (40 tests).
- `requirements.txt` and `pyproject.toml` for the test toolchain
  (pytest, PyYAML, jsonschema).
- Quick-start, test instructions, and project-structure section in
  `README.md`.
