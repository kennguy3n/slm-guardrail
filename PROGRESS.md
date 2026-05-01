# KChat SLM Guardrail Skills — Progress

**Status:** Complete | 100% + demo layer
**Current phase:** Phase 6 complete — 100 jurisdiction/community skills + Bonsai-1.7B SLM integration
**Last updated:** 2026-04-30

This file tracks delivery against the phased plan in
[`PHASES.md`](PHASES.md). Each phase ends with a tagged release
(`phase-0`, `phase-1`, …); items marked `[x]` are landed in `main`.

---

## Phase 0 — Foundation

- [x] Repository folder structure (`kchat-skills/global`, `…/jurisdictions`,
  `…/communities`, `…/prompts`, `…/compiler`, `…/tests`, `…/docs`).
- [x] `kchat-skills/global/baseline.yaml` — global baseline skill **stub**
  (decision-policy thresholds, skill-selection block, child-safety policy
  stub, references). Full implementation lands in Phase 1.
- [x] `kchat-skills/global/taxonomy.yaml` — 16-category global taxonomy.
- [x] `kchat-skills/global/severity.yaml` — 0–5 severity rubric with
  child-safety floor of 5.
- [x] `kchat-skills/global/output_schema.json` — constrained SLM JSON
  output schema (Draft-07 JSON Schema).
- [x] `kchat-skills/tests/global/` — pytest validation suite for the
  files above (taxonomy, severity, output schema, baseline).
- [x] `requirements.txt` + `pyproject.toml` (pytest, PyYAML, jsonschema).
- [x] `kchat-skills/global/local_signal_schema.json` — SLM input contract.
- [x] `kchat-skills/global/privacy_contract.yaml` — eight non-negotiable
  privacy rules expressed as enforceable constraints.

---

## Phase 1 — Global Baseline Skill + First Community Overlays

- [x] Complete (non-stub) `kchat.global.guardrail.baseline` with full
  privacy rules, input contract, decision-policy, and `skill_selection`
  blocks.
- [x] Runtime SLM instruction prompt (10-rule instruction) +
  compiled-prompt format reference at `kchat-skills/prompts/`.
- [x] 8 community overlay skills:
  - [x] `community.school`
  - [x] `community.family`
  - [x] `community.workplace`
  - [x] `community.adult_only`
  - [x] `community.marketplace`
  - [x] `community.health_support`
  - [x] `community.political`
  - [x] `community.gaming`
- [x] Local expiring counter implementation (device-local, no upload) —
  `kchat-skills/compiler/counters.py` with a pluggable device keystore,
  group / counter scoping, time-windowed expiry, and
  `counter_updates`-array consumption from the SLM output schema.
- [x] Test-suite template (recall, precision, false-positive, latency
  targets) + first round of test cases for the global baseline —
  `kchat-skills/tests/test_suite_template.yaml` and
  `kchat-skills/tests/global/test_baseline_cases.py`.

---

## Phase 2 — Jurisdiction Archetype Overlays

- [x] `kchat-skills/jurisdictions/_template/overlay.yaml`.
- [x] `jurisdiction.archetype-strict-adult`.
- [x] `jurisdiction.archetype-strict-hate`.
- [x] `jurisdiction.archetype-strict-marketplace`.
- [x] Local language asset structure (`lexicons/`, `normalization.yaml`,
  transliteration references) — landed for all three archetype overlays.
- [x] Per-archetype test suites including minority-language and
  code-switching false-positive tests (target
  `minority_language_false_positive ≤ 0.07`).

---

## Phase 3 — Hybrid Local Pipeline + SLM Integration

- [x] 7-step hybrid pipeline implementation (normalize → detectors →
  pack signals → SLM → thresholds → JSON → counters) at
  `kchat-skills/compiler/pipeline.py`.
- [x] SLM runtime adapter interface + reference adapter at
  `kchat-skills/compiler/slm_adapter.py` (Protocol + `MockSLMAdapter`).
- [x] Hard-coded threshold enforcement (`label_only=0.45`,
  `warn=0.62`, `strong_warn=0.78`, `critical_intervention=0.85`) at
  `kchat-skills/compiler/threshold_policy.py`, including child-safety
  severity-floor handling.
- [x] Metric validation: `child_safety_recall ≥ 0.98`,
  `protected_speech_false_positive ≤ 0.05`, p95 latency ≤ 250 ms —
  `kchat-skills/compiler/metric_validator.py` plus pipeline hook
  `GuardrailPipeline.validate_metrics`.

---

## Phase 4 — Skill Pack Compiler + Signing

- [x] Compiler pipeline (authoring → review → tests → prompt compiler
  → signed bundle) — `kchat-skills/compiler/compiler.py`
  (`SkillPackCompiler`, conflict resolution, 1800-token budget
  enforcement).
- [x] Skill passport schema + ed25519 signing —
  `kchat-skills/compiler/skill_passport.py` and
  `kchat-skills/compiler/skill_passport.schema.json`.
- [x] Anti-misuse validation rules + tests —
  `kchat-skills/compiler/anti_misuse.py`.
- [x] Compiled-prompt reference outputs for Phase 1–2 packs —
  14 combinations under `kchat-skills/prompts/compiled_examples/`.

---

## Phase 5 — Country-Specific Expansion

- [x] 59 country-specific jurisdiction overlays landed — Phase 5
  wave 1 (US, DE, BR, IN, JP), Phase 5 wave 2 (MX, CA, AR, CO, CL,
  PE, FR, GB, ES, IT, NL, PL, SE, PT, CH, AT, KR, ID, PH, TH, VN,
  MY, SG, TW, PK, BD, NG, ZA, EG, SA, AE, KE, AU, NZ, TR), and
  Phase 6 expansion (RU, UA, RO, GR, CZ, HU, DK, FI, NO, IE, IL,
  IQ, MA, DZ, GH, TZ, ET, EC, UY).
- [x] Localized lexicons + normalization rules per country.
- [x] Per-country test suites with passing metrics.

---

## Phase 6 — Scale, Audit, Continuous Improvement

- [x] 100–200 jurisdiction / community skills — 59 country packs
  + 38 community overlays + 3 archetype overlays = 100 packs landed.
- [x] Bias auditing for protected-class and minority-language effects.
- [x] Versioning, rollback, and expiry-review workflows.
- [x] Adversarial / obfuscation test corpus — 60 cases across 6
  techniques (homoglyph, leetspeak, code-switching, unicode tricks,
  whitespace insertion, image-text evasion) under
  `kchat-skills/tests/adversarial/`.
- [x] Regulatory alignment (EU DSA, NIST AI RMF, UNICEF / ITU child
  online protection) — `kchat-skills/docs/regulatory/` + contract
  test at `kchat-skills/tests/global/test_regulatory_docs.py`.
- [x] Performance optimization benchmarking —
  `kchat-skills/compiler/benchmark.py` + contract tests at
  `kchat-skills/tests/global/test_benchmark.py`. Enforces the
  250 ms p95 latency target from ARCHITECTURE.md.
- [x] Community feedback + appeal flow —
  `kchat-skills/compiler/appeal_flow.py` +
  `kchat-skills/tests/global/test_appeal_flow.py`. Privacy
  invariant pinned: no message text, hashes, or embeddings
  persisted at any layer.

---

## Changelog

### 2026-04-30 — Vietnam (vi-VN) demo expansion

- `tools/demo_guardrail.py` — adds 8 Vietnam scenarios on top of the
  existing demo matrix:
  - 4 benign Vietnamese-language community scenarios (school /
    workplace / marketplace / health_support, each with
    `jurisdiction_cc="vn"`, `locale="vi-VN"`, `lang_hint="vi"`).
  - 2 harmful Vietnamese-language scenarios — a `.xyz` fake-prize
    scam and a credit-card + email PII leak — both now flag as
    `SCAM_FRAUD` (cat 7) and `PRIVATE_DATA` (cat 9) respectively
    with `critical_intervention` actions, exactly matching the
    behaviour of the equivalent English scenarios.
  - 2 Vietnamese ↔ English code-switching scenarios — a benign
    Bến Thành hotpot plan and a `.top` TLD discount-link with
    "free shipping" mixed copy. The `.top` link flags as
    `SCAM_FRAUD`, confirming the URL detector fires on
    code-switched messages.
- Total demo coverage: **51 scenarios** across 11+ countries
  (US, DE, JP, BR, FR, SA, IN, KR, MX, EG, ID, TH, **VN**, TR, PL,
  NG), 8+ community types (school / workplace / gaming / dating /
  health_support / marketplace / political / journalism / family /
  adult_only), and 10+ mixed-language / code-switching scenarios
  (US en+es, DE de+tr, IN hi+en, SG en+zh, CA en+fr, MY ms+en,
  PH en+tl, CH de+fr, NG en+yo, **VN vi+en**).
- `results/demo_results_<timestamp>.json/.md` — regenerated reports
  show Vietnam in both the Per-scenario tables and the
  Per-(jurisdiction, community) latency table
  (`vn/school`, `vn/workplace`, `vn/marketplace`,
  `vn/health_support`, `vn/none`).
- 250 ms p95 target — still PASS at 51-case corpus
  (overall p95 ≪ 1 ms with `MockSLMAdapter`).

### 2026-04-30 — Cross-community / cross-country demo + results

- `tools/demo_guardrail.py` — end-to-end demo script exercising the
  guardrail pipeline across 8+ community types, 10+ countries with
  core-language messages, and 8+ mixed-language / code-switching
  scenarios. Captures classification results (category, severity,
  confidence, actions, reason_codes), per-group latency metrics
  (p50 / p95 / p99 / mean / max / min), and pass/fail against the
  250 ms p95 target.
- `results/demo_results_<timestamp>.json` — structured JSON results
  with per-scenario classification output and aggregate latency.
- `results/demo_results_<timestamp>.md` — human-readable Markdown
  report with summary tables, per-community, per-country, and
  mixed-language breakdowns, and performance metrics.

### 2026-04-30 — Sample data layer + real SLM integration (Bonsai-1.7B)

- `kchat-skills/compiler/llama_cpp_adapter.py` — `LlamaCppSLMAdapter`
  implementing the `SLMAdapter` Protocol against a llama.cpp server
  (kennguy3n/llama.cpp, branch prism). Connects via HTTP to the
  OpenAI-compatible `/v1/chat/completions` endpoint with constrained
  JSON output (`response_format: {"type": "json_object"}`,
  `temperature: 0.0`). Uses Bonsai-1.7B-gguf from
  https://huggingface.co/prism-ml/Bonsai-1.7B-gguf. Stdlib-only
  (`urllib.request` + `json`); falls back to a SAFE output when the
  server is unreachable, returns malformed JSON, or returns
  out-of-range fields.
- `kchat-skills/samples/sample_messages.yaml` — curated sample data
  layer with 27 messages covering safe / scam / PII / child-safety /
  hate / harassment / health-misinfo / civic-misinfo / marketplace /
  sexual-adult / extremism / self-harm / drugs / community-rule
  contexts plus multi-language samples (English, Vietnamese, Spanish,
  German, en↔vi code-switching). All cases comply with the privacy
  contract — no real PII, no live phishing domains, no CSAM-adjacent
  text.
- `kchat-skills/samples/README.md` — sample-data format reference,
  privacy contract, and usage examples.
- `tools/run_guardrail_demo.py` — end-to-end demo script: health-checks
  llama-server, loads samples, compiles a prompt via `SkillPackCompiler`
  (with optional `--jurisdiction` / `--community`), runs the pipeline
  with either `LlamaCppSLMAdapter` or `MockSLMAdapter` (`--mock`),
  prints a results table, optionally runs `PipelineBenchmark`
  (`--benchmark`) and commits results (`--commit-results`).
- `kchat-skills/benchmarks/` — committed benchmark results directory
  with reproduction instructions; `bonsai_1.7b_results.json` is
  generated on demand by the demo script.
- `kchat-skills/tests/global/test_llama_cpp_adapter.py` — adapter
  Protocol conformance, fallback behaviour, JSON parsing, output
  schema coercion, request-shape constraints (temperature 0.0,
  `response_format: json_object`).
- `kchat-skills/tests/global/test_sample_messages.py` — sample data
  structural validation (required keys, taxonomy range,
  case-id uniqueness, multi-language coverage) and MockSLMAdapter
  smoke tests.
- `pyproject.toml` — adds optional `[project.optional-dependencies].demo`
  group (PyYAML only).

### 2026-04-29 — Phase 6 skill expansion to 100 packs

- `kchat-skills/communities/` — 30 additional community overlays
  (religious, sports, creative_arts, education_higher, volunteer,
  neighborhood, parenting, dating, fitness, travel, book_club, music,
  photography, cooking, tech_support, language_learning, pet_owners,
  environmental, journalism, legal_support, mental_health, startup,
  nonprofit, seniors, lgbtq_support, veterans, hobbyist, science,
  open_source, emergency_response). Each passes
  `anti_misuse.validate_pack`.
- `kchat-skills/jurisdictions/` — 19 additional country packs
  (RU, UA, RO, GR, CZ, HU, DK, FI, NO, IE, IL, IQ, MA, GH, TZ,
  ET, DZ, EC, UY). Each ships overlay.yaml + normalization.yaml +
  per-language lexicons.
- Per-pack test files for all 49 new skills under
  `kchat-skills/tests/communities/test_community_overlays.py` and
  `kchat-skills/tests/jurisdictions/test_country_<cc>.py`.
- `kchat-skills/tests/jurisdictions/test_minority_language_fp.py`
  — 76 new minority-language / code-switching false-positive cases
  (4 per new country); `ARCHETYPES` extended to 62 archetypes;
  `MIN_MINORITY_LANGUAGE_CASES` raised to 118 and
  `MIN_CODE_SWITCHING_CASES` raised to 98.
- 19 new compiled-prompt references at
  `kchat-skills/prompts/compiled_examples/country_<cc>.txt`
  (73 total). `tools/regenerate_compiled_examples.py` COMBOS and
  `kchat-skills/tests/global/test_compiled_examples.py` updated;
  new `test_phase6_all_19_country_packs_covered` and
  `test_total_compiled_example_count_is_73` assertions pin the set.
- `kchat-skills/docs/regulatory/unicef_itu_cop_alignment.md`
  per-jurisdiction table extended to 59 rows; the regulatory-doc
  contract test is renamed to
  `test_unicef_itu_references_core_artefacts_and_all_59_countries`.

### 2026-04-29 — Phase 5 full expansion + Phase 6 complete

- `kchat-skills/jurisdictions/` — 35 additional country packs
  (MX, CA, AR, CO, CL, PE, FR, GB, ES, IT, NL, PL, SE, PT, CH, AT,
  KR, ID, PH, TH, VN, MY, SG, TW, PK, BD, NG, ZA, EG, SA, AE, KE,
  AU, NZ, TR). Each country ships the full `overlay.yaml` +
  `normalization.yaml` + one `lexicons/<lang>.yaml` per primary
  language. Category 1 (CHILD_SAFETY) severity floor 5 is asserted
  on every pack via `assert_no_relaxed_child_safety`. Multi-language
  countries (CH, ES, SG, ZA, PK, PH, MY, AE, KE, NZ, BD) ship one
  lexicon per primary language.
- `kchat-skills/tests/jurisdictions/test_country_<cc>.py` — 35 new
  per-country test files calling `A.run_all_structural_assertions`
  and per-country legal-age / protected-class / override assertions.
- `kchat-skills/tests/jurisdictions/test_minority_language_fp.py` —
  ~140 additional benign minority-language + code-switching cases
  (4 per country); `ARCHETYPES` now enumerates all 40 country codes
  plus the 3 archetype overlays (43 archetypes). Minority-language
  floor raised to ≥ 80 cases, code-switching floor raised to ≥ 60
  cases. `MIN_CASES_PER_ARCHETYPE` held at 4.
- `kchat-skills/tests/jurisdictions/conftest.py` — dynamic fixture
  generation for all 40 countries via
  `_PHASE5_SECOND_WAVE_COUNTRY_CODES`.
- `kchat-skills/prompts/compiled_examples/country_<cc>.txt` — 35
  new reference compiled prompts (54 total — 19 existing + 35 new).
  `tools/regenerate_compiled_examples.py` COMBOS extended; the
  byte-for-byte test `test_compiled_examples.py` covers every entry
  and a new `test_phase5_all_40_country_packs_covered` assertion
  pins the full 40-country set.
- `kchat-skills/tests/adversarial/corpus.yaml` — 60 adversarial
  test cases across 6 evasion techniques (homoglyph attacks,
  leetspeak, code-switching, unicode tricks, whitespace insertion,
  image-text evasion). Detection-rate floor: ≥ 0.80 per technique.
- `kchat-skills/tests/adversarial/test_adversarial_corpus.py` —
  technique-specific decoders (extra homoglyph fold, leet decode,
  zero-width / BiDi strip, NFKC/NFKD) + per-case detection logic
  and per-technique assertions.
- `kchat-skills/docs/regulatory/eu_dsa_alignment.md`,
  `nist_ai_rmf_alignment.md`, `unicef_itu_cop_alignment.md`, plus
  `README.md` index — obligation-to-artefact maps for the EU DSA,
  NIST AI RMF 1.0, and UNICEF / ITU Child Online Protection
  Guidelines. Each document references the specific source
  artefacts (baseline.yaml, anti_misuse.py, appeal_flow.py,
  bias_audit.py, metric_validator.py, skill_passport.py).
- `kchat-skills/tests/global/test_regulatory_docs.py` — contract
  test pinning that every alignment doc exists, is non-empty,
  references the relevant source artefacts, and — for the UNICEF /
  ITU doc — contains a statutory-grounding row for every one of
  the 40 country packs.
- `kchat-skills/compiler/benchmark.py` — `PipelineBenchmark`,
  `BenchmarkCase`, `BenchmarkReport` + `default_benchmark_cases`.
  Measures p50 / p95 / p99 / mean / max / min per-message latency
  against `MockSLMAdapter`; `passed` iff p95 ≤ 250 ms.
- `kchat-skills/tests/global/test_benchmark.py` — constructor /
  invariant checks, per-taxonomy parametrisation (all 16 cats),
  baseline-only / jurisdiction-only / full-stack latency targets,
  and a 40-country scaling test.
- `kchat-skills/compiler/appeal_flow.py` — `AppealCase`,
  `AppealAggregator`, `AppealReport`. Closed-enum `user_context`
  (`disagree_category`, `disagree_severity`, `false_positive`,
  `missing_context`) and closed-enum `recommendation` (`no_action`,
  `review_suggested`, `urgent_review`). Category-1 child-safety
  appeals short-circuit to urgent_review.
- `kchat-skills/tests/global/test_appeal_flow.py` — privacy
  invariant pinned (no text / content / hash / embedding fields),
  plus threshold, aggregation, window, duplicate-id, multi-skill,
  and edge-case coverage.

### 2026-04-29 — Phase 5 first wave + Phase 6 partial

- `kchat-skills/jurisdictions/us/`, `…/de/`, `…/br/`, `…/in/`,
  `…/jp/` — first wave of five country-specific jurisdiction
  overlays. Each ships an `overlay.yaml` (concrete legal-age,
  protected-class, listed-extremist-org, election-rule, and
  override values for the country), a `normalization.yaml`
  (NFKC + case-fold + per-country transliteration refs — Devanagari
  for IN, romaji for JP), and one `lexicons/<lang>.yaml` per
  primary language with provenance metadata. All five overlays
  pass `anti_misuse.validate_pack`.
- `kchat-skills/tests/jurisdictions/test_country_us.py`,
  `…/test_country_de.py`, `…/test_country_br.py`,
  `…/test_country_in.py`, `…/test_country_jp.py` plus
  `_country_pack_assertions.py` — per-country structural tests
  asserting the country-specific severity floors, marketplace
  ages, protected-class enumeration, election-authority
  references, and the shared structural invariants (parent,
  schema_version, signers, forbidden criteria, allowed contexts,
  expiry budget, user notice, lexicon provenance).
- `kchat-skills/tests/jurisdictions/test_minority_language_fp.py`
  — extended with 21 new false-positive cases covering Spanish/
  English (US), Navajo + Cherokee (US), Turkish/German + Sorbian
  (DE), Tupi + Guarani + Portuguese/English (BR), Tamil + Bengali
  + Urdu + Hinglish (IN), and Okinawan + Ainu + Japanese/English
  (JP). `ARCHETYPES` now includes the 5 country codes alongside
  the 3 archetype overlays.
- `kchat-skills/tests/jurisdictions/conftest.py` — added
  `<cc>_overlay` / `<cc>_normalization` fixtures for the five
  country packs.
- `kchat-skills/compiler/bias_audit.py` — Phase 6 bias auditor.
  Computes per-protected-class and per-minority-language false-
  positive rates from a list of `BiasAuditCase`, flags any group
  exceeding the 0.07 ceiling or showing >0.05 disparity vs. the
  overall mean, and emits a structured `BiasAuditReport`. Bound
  to `metric_validator.SAFE_CATEGORY` and the
  `minority_language_false_positive` shipping target.
- `kchat-skills/compiler/pack_lifecycle.py` — Phase 6 pack-store
  module. `PackStore` tracks signed `PackVersion` entries per
  `skill_id`, retains the last `MAX_RETAINED_VERSIONS=3` versions
  for rollback, exposes `register / get_active / get_history /
  rollback / check_expiry / deactivate_expired / needs_review` +
  `to_json / from_json` for device-local persistence, and uses
  `EXPIRY_REVIEW_WINDOW_DAYS=30` to flag packs needing legal /
  cultural re-review.
- `kchat-skills/tests/global/test_bias_audit.py`,
  `kchat-skills/tests/global/test_pack_lifecycle.py` — 47 new
  tests covering per-class / per-language FP computation,
  disparity detection, edge cases (empty / single-group / all-
  SAFE), pack registration / retention cap / rollback / expiry /
  needs-review / JSON round-trip, plus an integration test that
  runs the bias auditor against the existing minority-language
  FP corpus.
- `kchat-skills/prompts/compiled_examples/country_us.txt`,
  `…/country_de.txt`, `…/country_br.txt`, `…/country_in.txt`,
  `…/country_jp.txt` — five new reference compiled prompts. All
  under the 1800-token instruction budget.
- `tools/regenerate_compiled_examples.py` — extended COMBOS to
  include the five country packs.

### 2026-04-29 — Phase 3 close + Phase 4 complete

- `kchat-skills/compiler/metric_validator.py` — metric validation
  framework. Computes recall / precision / FP / p95 latency from
  test-case results, returns a per-metric `MetricVerdict` against the
  seven shipping thresholds (`child_safety_recall ≥ 0.98`,
  `child_safety_precision ≥ 0.90`, `privacy_leak_precision ≥ 0.90`,
  `scam_recall ≥ 0.85`, `protected_speech_false_positive ≤ 0.05`,
  `minority_language_false_positive ≤ 0.07`, `latency_p95_ms ≤ 250`).
  Wired into `GuardrailPipeline.validate_metrics` so the compiler can
  refuse to sign bundles whose metrics regress.
- `kchat-skills/compiler/compiler.py` — Phase 4 compiler pipeline.
  Loads baseline + jurisdiction + community YAML, applies
  conflict-resolution rules from `baseline.skill_selection`
  (severity take_max, action most_protective, immutable
  privacy_rules, CHILD_SAFETY pinned to severity 5), emits a single
  compiled prompt within the 1800 instruction-token budget. Includes
  `python -m compiler.compiler` CLI.
- `kchat-skills/compiler/skill_passport.py` +
  `skill_passport.schema.json` — `SkillPassport` dataclass mirroring
  ARCHITECTURE.md lines 683-712, ed25519 signing / verification via
  `cryptography`, expiry checks (max 18 months), model-compatibility
  checks, deterministic JSON signing payload, Draft-07 JSON Schema.
- `kchat-skills/compiler/anti_misuse.py` — anti-misuse validator.
  Rejects vague categories, invented categories, jurisdiction packs
  missing `legal_review` / `cultural_review` signers, community
  packs missing `trust_and_safety`, severity floors ≥ 4 without
  protected-speech `allowed_contexts`, overlays redefining
  `privacy_rules`, and lexicons without provenance.
- `kchat-skills/prompts/compiled_examples/` — 14 reference compiled
  prompts: baseline only, baseline + each of the 8 community
  overlays, baseline + each of the 3 jurisdiction archetypes, plus
  `strict_marketplace_workplace` and `strict_adult_school` combined
  examples. All under the 1800-token instruction budget.
- `tools/regenerate_compiled_examples.py` — regenerator script for
  the reference compiled examples.
- `kchat-skills/tests/global/test_metric_validator.py`,
  `test_compiler.py`, `test_skill_passport.py`, `test_anti_misuse.py`,
  `test_compiled_examples.py` — 175+ new tests covering every
  Phase 3-4 module and every reference compiled prompt.
- `requirements.txt` / `pyproject.toml` — added
  `cryptography>=42.0` for ed25519 signing.

### 2026-04-29 — Phase 2 close + Phase 3 partial

- `kchat-skills/jurisdictions/archetype-strict-marketplace/` — third
  archetype overlay: DRUGS_WEAPONS (category 11) and ILLEGAL_GOODS
  (category 12) at severity_floor 4, with `lexicons/`,
  `normalization.yaml`, all 5 forbidden criteria, all 4 protected-
  speech contexts, and `trust_and_safety + legal_review +
  cultural_review` signers.
- `kchat-skills/tests/jurisdictions/test_archetype_strict_marketplace.py`
  — 18 structural tests for the new archetype.
- `kchat-skills/tests/jurisdictions/test_minority_language_fp.py` —
  minority-language and code-switching false-positive corpus for all
  three archetypes, with structural contract validation against the
  `local_signal_schema.json` / `output_schema.json` pair, per-archetype
  / per-tag coverage floors, and a pin on the 0.07 target declared in
  the test-suite template.
- `kchat-skills/compiler/pipeline.py` — 7-step hybrid local pipeline
  (normalize → deterministic detectors → signal packaging → SLM adapter
  → threshold policy → output → counter updates) with a `SkillBundle`
  carrier and a fully offline-capable `GuardrailPipeline.classify`
  entry point.
- `kchat-skills/compiler/slm_adapter.py` — backend-agnostic
  `SLMAdapter` Protocol plus a deterministic `MockSLMAdapter` that
  maps detector signals to all 16 taxonomy categories for end-to-end
  pipeline tests without a real model.
- `kchat-skills/compiler/threshold_policy.py` — immutable
  `ThresholdPolicy` with hard-coded confidence thresholds, uncertainty
  handling (< 0.45 → SAFE), lower-numbered-category tie-break, and the
  CHILD_SAFETY severity-5 floor.
- `kchat-skills/tests/global/test_pipeline.py`,
  `test_slm_adapter.py`, `test_threshold_policy.py` — unit and end-
  to-end tests for the new compiler modules.

### 2026-04-29 — Phase 1 close + Phase 2 partial

- `kchat-skills/compiler/counters.py` — device-local, group-scoped,
  expiring counter store. Pluggable `DeviceKeystore`, XOR-stream +
  HMAC-SHA256 reference at-rest encryption, JSON serialisation,
  `apply_counter_updates` that consumes the `counter_updates` array
  from `kchat.guardrail.output.v1`.
- `kchat-skills/tests/test_suite_template.yaml` — metrics framework
  (child-safety recall/precision, privacy-leak precision, scam recall,
  protected-speech / minority-language false-positive, p95 latency)
  plus per-category coverage and threshold-boundary coverage rules.
- `kchat-skills/tests/global/test_baseline_cases.py` — first round of
  baseline test cases covering all 16 taxonomy categories, four
  protected-speech contexts, child-safety floor, and the full set of
  decision-policy threshold boundaries (0.44, 0.45, 0.62, 0.78, 0.85).
- `kchat-skills/jurisdictions/_template/overlay.yaml` — jurisdiction
  overlay template with all required activation / forbidden-criteria /
  local-definitions / language-assets / overrides / allowed-contexts /
  user-notice blocks.
- `kchat-skills/jurisdictions/archetype-strict-adult/` — SEXUAL_ADULT
  (category 10) severity_floor = 5 archetype, plus `lexicons/` and
  `normalization.yaml`.
- `kchat-skills/jurisdictions/archetype-strict-hate/` — EXTREMISM
  (category 4) severity_floor = 5 and HATE (category 6) severity_floor
  = 4 archetype, with explicit protected-speech contexts, plus
  `lexicons/` and `normalization.yaml`.
- Test suites for counters (39 tests), test-suite template (17),
  baseline cases (100), jurisdiction template (18), and each archetype
  (14 + 15).

### 2026-04-29 — Phase 0 complete + Phase 1 partial

- `local_signal_schema.json` — SLM input contract (Draft-07 JSON Schema).
- `privacy_contract.yaml` — eight non-negotiable privacy rules as
  enforceable constraints.
- Phase 0 complete: all foundation artifacts landed.
- Complete (non-stub) `baseline.yaml` with full privacy rules, input
  contract references.
- Runtime SLM instruction prompt (`runtime_instruction.txt`) — 10-rule
  instruction.
- Compiled-prompt format reference and workplace example.
- 8 community overlay skills: school, family, workplace, adult_only,
  marketplace, health_support, political, gaming.
- Community overlay template at `communities/_template/overlay.yaml`.
- Test suites for local_signal_schema, privacy_contract, prompts, and
  all 8 community overlays.

### 2026-04-29 — Phase 0 partial

- Repository structure for `kchat-skills/` matching the recommended
  folder layout in `ARCHITECTURE.md`.
- Global baseline stub (`baseline.yaml`) wired to taxonomy, severity,
  output schema, plus decision policy thresholds, skill-selection block,
  and child-safety policy stub.
- 16-category global taxonomy (`taxonomy.yaml`).
- 0–5 severity rubric (`severity.yaml`) with child-safety floor of 5.
- Constrained SLM JSON output schema (`output_schema.json`, Draft-07).
- Pytest validation suite covering taxonomy, severity, output schema,
  and baseline structure (40 tests).
- `requirements.txt` and `pyproject.toml` for the test toolchain
  (pytest, PyYAML, jsonschema).
- Quick-start, test instructions, and project-structure section in
  `README.md`.
