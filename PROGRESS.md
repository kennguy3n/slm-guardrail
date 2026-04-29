# KChat SLM Guardrail Skills — Progress

**Status:** In progress | ~90%
**Current phase:** Phase 4 — Skill Pack Compiler + Signing (complete)
**Last updated:** 2026-04-29

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

- [ ] 10–20 country-specific jurisdiction overlays (first wave).
- [ ] Localized lexicons + normalization rules per country.
- [ ] Per-country test suites with passing metrics.

---

## Phase 6 — Scale, Audit, Continuous Improvement

- [ ] 100–200 jurisdiction / community skills.
- [ ] Bias auditing for protected-class and minority-language effects.
- [ ] Versioning, rollback, and expiry-review workflows.
- [ ] Adversarial / obfuscation test corpus.
- [ ] Regulatory alignment (EU DSA, NIST AI RMF, UNICEF / ITU child
  online protection).

---

## Changelog

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
