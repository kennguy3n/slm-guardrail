# KChat SLM Guardrail Skills — Progress

**Status:** In progress | ~65%
**Current phase:** Phase 2 — Jurisdiction Archetype Overlays (partial)
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
- [ ] `jurisdiction.archetype-strict-marketplace`.
- [x] Local language asset structure (`lexicons/`, `normalization.yaml`,
  transliteration references) — landed for both archetype overlays.
- [ ] Per-archetype test suites including minority-language and
  code-switching false-positive tests (target
  `minority_language_false_positive ≤ 0.07`).

---

## Phase 3 — Hybrid Local Pipeline + SLM Integration

- [ ] 7-step hybrid pipeline implementation (normalize → detectors →
  pack signals → SLM → thresholds → JSON → counters).
- [ ] SLM runtime adapter interface + reference adapter.
- [ ] Hard-coded threshold enforcement (`label_only=0.45`,
  `warn=0.62`, `strong_warn=0.78`, `critical_intervention=0.85`).
- [ ] Child-safety severity-floor handling.
- [ ] Metric validation: `child_safety_recall ≥ 0.98`,
  `protected_speech_false_positive ≤ 0.05`, p95 latency ≤ 250 ms.

---

## Phase 4 — Skill Pack Compiler + Signing

- [ ] Compiler pipeline (authoring → review → tests → prompt compiler
  → signed bundle).
- [ ] Skill passport schema + ed25519 signing.
- [ ] Anti-misuse validation rules + tests.
- [ ] Compiled-prompt reference outputs for Phase 1–2 packs.

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
