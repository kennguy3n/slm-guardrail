# KChat SLM Guardrail Skills — Progress

**Status:** In progress | ~35%
**Current phase:** Phase 0 — Foundation (partial)
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
- [ ] `kchat-skills/global/local_signal_schema.json` — SLM input contract.
- [ ] `kchat-skills/global/privacy_contract.yaml` — eight non-negotiable
  privacy rules expressed as enforceable constraints.

---

## Phase 1 — Global Baseline Skill + First Community Overlays

- [ ] Complete (non-stub) `kchat.global.guardrail.baseline` with full
  privacy rules, input contract, decision-policy, and `skill_selection`
  blocks.
- [ ] Runtime SLM instruction prompt (10-rule instruction) +
  compiled-prompt format reference at `kchat-skills/prompts/`.
- [ ] 8 community overlay skills:
  - [ ] `community.school`
  - [ ] `community.family`
  - [ ] `community.workplace`
  - [ ] `community.adult_only`
  - [ ] `community.marketplace`
  - [ ] `community.health_support`
  - [ ] `community.political`
  - [ ] `community.gaming`
- [ ] Local expiring counter implementation (device-local, no upload).
- [ ] Test-suite template (recall, precision, false-positive, latency
  targets) + first round of test cases for the global baseline.

---

## Phase 2 — Jurisdiction Archetype Overlays

- [ ] `kchat-skills/jurisdictions/_template/overlay.yaml`.
- [ ] `jurisdiction.archetype-strict-adult`.
- [ ] `jurisdiction.archetype-strict-hate`.
- [ ] `jurisdiction.archetype-strict-marketplace`.
- [ ] Local language asset structure (`lexicons/`, `normalization.yaml`,
  transliteration references).
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
