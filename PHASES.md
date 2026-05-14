# KChat Guardrail Skills — Development Roadmap

All phases on this roadmap are complete. This document records the
sequence in which the project was built, what each phase produced,
and the artifacts that resulted. Each phase corresponds to a tagged
release on the repository (`phase-0` through `phase-6`).

For the current state of the project, see
[PROGRESS.md](PROGRESS.md). For the historical changelog of
session-by-session changes, see [docs/CHANGELOG.md](docs/CHANGELOG.md).

---

## Phase 0 — Foundation

Established the structural and contractual groundwork that every
later phase builds on.

- Set up the repository structure to match the recommended folder
  layout (`/kchat-skills/global`, `/jurisdictions`, `/communities`,
  `/prompts`, `/compiler`, `/tests`, `/docs`).
- Created `/kchat-skills/global/` with `baseline.yaml` (stub),
  `taxonomy.yaml` (16-category global taxonomy), `severity.yaml`
  (0–5 severity rubric), and `output_schema.json` (constrained
  encoder classifier JSON output schema).
- Defined the encoder classifier input contract as
  `local_signal_schema.json`.
- Defined the privacy contract as `privacy_contract.yaml`,
  expressing the eight non-negotiable privacy rules as enforceable
  constraints (later validated by the compiler).
- Defined the global risk taxonomy (16 categories) and severity
  rubric (0–5) as structured YAML / JSON consumed by the compiler.

**Deliverables**

- Repository folder structure.
- `baseline.yaml` (stub), `taxonomy.yaml`, `severity.yaml`.
- `output_schema.json`, `local_signal_schema.json`.
- `privacy_contract.yaml`.

---

## Phase 1 — Global Baseline Skill + First Community Overlays

Shipped the global baseline as a complete, signed-style skill plus
eight community overlays so the layering model could be exercised
end-to-end.

- Implemented `kchat.global.guardrail.baseline` as the complete
  global baseline YAML — privacy rules, 16-category taxonomy,
  severity rubric, output schema, input contract, decision-policy
  thresholds, and the `skill_selection` block.
- Implemented the runtime classifier-bundle instruction prompt (the
  10-rule instruction) and the compiled-prompt format used by the
  runtime.
- Created 8 community overlay skills: `community.school`,
  `community.family`, `community.workplace`, `community.adult_only`,
  `community.marketplace`, `community.health_support`,
  `community.political`, `community.gaming`.
- Implemented community labeling using local expiring counters
  (device-local, group-level, no upload).
- Authored the test suite **template** (recall, precision,
  false-positive, latency targets) and the first round of test cases
  for the global baseline.

**Deliverables**

- Working `kchat.global.guardrail.baseline` skill.
- 8 community overlay skills.
- Test suite template + initial test cases for the global baseline.
- Runtime classifier-bundle instruction prompt + compiled-prompt
  format reference.

---

## Phase 2 — Jurisdiction Archetype Overlays

Validated the jurisdiction overlay model on three deliberately
different archetypes before spending review effort on real
countries.

- Created the jurisdiction overlay template at
  `/kchat-skills/jurisdictions/_template/overlay.yaml`.
- Implemented 3 jurisdiction archetype skills:
  `jurisdiction.archetype-strict-adult` (severity floor 5 on
  category 10), `jurisdiction.archetype-strict-hate` (severity floor
  4–5 on categories 4 and 6 with explicit protected contexts), and
  `jurisdiction.archetype-strict-marketplace` (severity floor 4 on
  categories 11 and 12).
- Defined the local language asset structure: `lexicons/`,
  `normalization.yaml` (NFKC, case fold, homoglyph map), and
  transliteration references.
- Created test suites for each archetype, including false-positive
  tests for minority languages and code-switching that exercise the
  `minority_language_false_positive ≤ 0.07` target.

**Deliverables**

- Jurisdiction overlay template.
- 3 archetype overlays.
- Local language asset structure and conventions.
- Per-archetype test suites including minority-language and
  code-switching false-positive tests.

---

## Phase 3 — Hybrid Local Pipeline + Encoder Classifier Integration

Turned skill packs into actual on-device behaviour.

- Implemented the 7-step hybrid local pipeline: text normalization
  (NFKC, case fold, homoglyph, transliteration), deterministic
  detectors (URL risk, PII, scam, lexicon, media descriptors), signal
  packaging into the encoder classifier input contract, encoder-based
  contextual classification (XLM-R via ONNX Runtime — deterministic
  argmax over fixed prototype embeddings), severity / threshold
  policy enforcement, local JSON output generation, and local
  expiring counter updates.
- Defined the runtime adapter interface — the boundary between the
  pipeline and any encoder classifier backend — so backends can be
  swapped without changing skill packs.
- Implemented the decision policy with hard-coded confidence
  thresholds (`label_only=0.45`, `warn=0.62`, `strong_warn=0.78`,
  `critical_intervention=0.85`) and uncertainty handling.
- Implemented child-safety priority rules with severity floor 5.
- Validated the pipeline against the test-suite metrics:
  `child_safety_recall ≥ 0.98`, `protected_speech_false_positive ≤
  0.05`, latency p95 ≤ 250 ms.

**Deliverables**

- Working hybrid pipeline implementation.
- Encoder classifier adapter interface specification and a reference
  adapter.
- Threshold enforcement.
- Child-safety severity-floor handling.
- Metric-validation report against the baseline and archetype packs.

---

## Phase 4 — Skill Pack Compiler + Signing

Turned authored YAML into signed, distributable skill packs that the
runtime can verify.

- Built the skill-pack compiler pipeline: policy authoring → legal /
  cultural review → YAML skill pack → test-suite generation →
  classifier-bundle compiler → signed compressed bundle.
- Implemented the skill passport with version, reviewers, model
  compatibility, and an ed25519 signature.
- Implemented anti-misuse validation rules: no vague categories;
  required legal review (and cultural review) for every jurisdiction
  pack; protected-context handling required for any category with
  severity floor ≥ 4.
- Built prompt compilation from the active skill bundle to the
  minimal compiled prompt (within the 1800-instruction-token budget).

**Deliverables**

- Compiler pipeline implementation.
- Skill passport schema + signing implementation.
- Bundle signing tooling (ed25519).
- Anti-misuse validation rules + tests.
- Compiled-prompt reference outputs for the baseline and archetype
  packs.

---

## Phase 5 — Country-Specific Expansion

Graduated from archetypes to real country packs.

- Expanded from 3 archetype jurisdictions to country-specific packs
  by filling in the jurisdiction overlay template per country.
- Prioritised countries based on the KChat user base (starting with
  the highest-population markets).
- For each country, completed legal review, cultural review, local
  language lexicons, transliteration / normalization rules, and
  country-specific test suites including minority-language
  false-positive tests.
- Shipped 40 country-specific packs in this wave.

**Deliverables**

- 40 country-specific jurisdiction overlays.
- Localized lexicons and normalization rules.
- Per-country test suites with passing metrics.

---

## Phase 6 — Scale, Audit, and Continuous Improvement

Scaled the skill library and built the operational scaffolding for
running it responsibly.

- Expanded the country coverage to **59 country packs** (added 19
  more) and the community overlay library to **38 overlays** (added
  30 more) so the active library totals **100 skill packs**.
- Implemented bias auditing for protected-class and minority-language
  effects across the signed library
  (`kchat-skills/compiler/bias_audit.py`).
- Implemented skill pack versioning, rollback, and expiry-review
  workflows (no pack older than its `expires_on` ships to devices) in
  `kchat-skills/compiler/pack_lifecycle.py`.
- Added an adversarial / obfuscation test corpus at
  `kchat-skills/tests/adversarial/` covering homoglyph, leetspeak,
  code-switching, unicode tricks, whitespace insertion, and
  image-text evasion.
- Optimised on-device latency to keep p95 ≤ 250 ms as the skill
  bundle grew; the latency benchmark
  (`kchat-skills/compiler/benchmark.py`) records committed
  measurements.
- Integrated community feedback and the appeal flow
  (`kchat-skills/compiler/appeal_flow.py`) so user appeals can
  produce skill-pack updates without weakening the privacy contract.
- Ran regulatory alignment reviews against the EU Digital Services
  Act, NIST AI Risk Management Framework, and UNICEF / ITU child
  online protection guidelines, documented under
  `kchat-skills/docs/regulatory/`.

**Deliverables**

- Scaled skill library (100 packs: 1 baseline + 3 archetypes + 59
  country packs + 38 community overlays — also reported as 97
  jurisdiction / community skills excluding the baseline).
- Audit framework for bias and minority-language effects.
- Versioning, rollback, and expiry-review workflows.
- Adversarial / obfuscation test corpus.
- Regulatory alignment documentation.
