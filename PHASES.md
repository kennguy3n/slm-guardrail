# KChat Guardrail Skills — Development Phases

This roadmap is sequential at the phase level. Within a phase, individual
skills can be developed in parallel. Each phase produces a tagged release
on completion (`phase-0`, `phase-1`, …).

---

## Phase 0 — Foundation

Goal: lay the structural and contractual groundwork. Nothing in later phases
should require revisiting Phase 0 artifacts.

- Set up the repository structure to match the recommended folder layout
  (`/kchat-skills/global`, `/jurisdictions`, `/communities`, `/prompts`,
  `/compiler`, `/tests`, `/docs`).
- Create `/kchat-skills/global/` with:
  - `baseline.yaml` — the global baseline skill stub.
  - `taxonomy.yaml` — the 16-category global taxonomy.
  - `severity.yaml` — the 0–5 severity rubric.
  - `output_schema.json` — the constrained encoder classifier JSON output schema.
- Define the encoder classifier input contract as `local_signal_schema.json`.
- Define the privacy contract as `privacy_contract.yaml`, expressing the
  eight non-negotiable privacy rules as enforceable constraints (validated
  by the compiler in Phase 4).
- Define the global risk taxonomy (16 categories) and severity rubric
  (0–5) as structured YAML / JSON consumed by the compiler.

**Deliverables**

- Repository folder structure.
- `baseline.yaml` (stub), `taxonomy.yaml`, `severity.yaml`.
- `output_schema.json`, `local_signal_schema.json`.
- `privacy_contract.yaml`.

---

## Phase 1 — Global Baseline Skill + First Community Overlays

Goal: ship the global baseline as a complete, signed-style skill plus eight
community overlays so the layering model can be exercised end-to-end.

- Implement `kchat.global.guardrail.baseline` as the complete global
  baseline YAML. This includes the privacy rules, the 16-category
  taxonomy, the severity rubric, the output schema, the input contract,
  the decision-policy thresholds, and the `skill_selection` block.
- Implement the runtime classifier-bundle instruction prompt (the
  10-rule instruction) and the compiled-prompt format used by the
  runtime.
- Create the first 8 community overlay skills:
  - `community.school` — minors-aware overlay.
  - `community.family` — household / kin overlay.
  - `community.workplace` — professional / B2B overlay.
  - `community.adult_only` — explicitly opt-in adult overlay.
  - `community.marketplace` — buy / sell / trade overlay.
  - `community.health_support` — peer-support overlay (loosens self-harm
    labels in supportive context, tightens medical-misinformation rules).
  - `community.political` — campaign / civic overlay.
  - `community.gaming` — large public gaming community overlay.
- Implement community labeling using local expiring counters (device-local,
  group-level, no upload).
- Author the test suite **template** (recall, precision, false-positive,
  latency targets) and write the first round of test cases for the global
  baseline.

**Deliverables**

- Working `kchat.global.guardrail.baseline` skill.
- 8 community overlay skills.
- Test suite template + initial test cases for the global baseline.
- Runtime classifier-bundle instruction prompt + compiled-prompt format reference.

---

## Phase 2 — Jurisdiction Archetype Overlays

Goal: validate the jurisdiction overlay model on three deliberately
different archetypes before spending review effort on real countries.

- Create the jurisdiction overlay template at
  `/kchat-skills/jurisdictions/_template/overlay.yaml`.
- Implement 3 jurisdiction archetype skills:
  - `jurisdiction.archetype-strict-adult` — strict adult-content
    archetype (severity floor 5 on category 10).
  - `jurisdiction.archetype-strict-hate` — strict hate / extremism
    archetype (severity floor 4–5 on categories 4 and 6, with explicit
    protected contexts).
  - `jurisdiction.archetype-strict-marketplace` — strict marketplace /
    restricted-goods archetype (severity floor 4 on categories 11 and
    12).
- Define the local language asset structure: `lexicons/`,
  `normalization.yaml` (NFKC, case fold, homoglyph map), transliteration
  references.
- Create test suites for each archetype, including **false-positive tests
  for minority languages and code-switching** to ensure the
  `minority_language_false_positive ≤ 0.07` target is exercised.

**Deliverables**

- Jurisdiction overlay template.
- 3 archetype overlays.
- Local language asset structure and conventions.
- Per-archetype test suites including minority-language and
  code-switching false-positive tests.

---

## Phase 3 — Hybrid Local Pipeline + Encoder Classifier Integration

Goal: turn skill packs into actual on-device behaviour.

- Implement the 7-step hybrid local pipeline:
  1. Text normalization (Unicode NFKC, case fold, homoglyph map,
     transliteration).
  2. Deterministic local detectors (URL risk, PII patterns, scam
     patterns, lexicon matching, media descriptor signals).
  3. Signal packaging into the encoder classifier input contract.
  4. Encoder-based contextual classification (XLM-R MiniLM-L6 —
     deterministic argmax over fixed prototype embeddings).
  5. Severity / threshold policy enforcement.
  6. Local JSON output generation.
  7. Local expiring counter updates (device-local only).
- Define the runtime adapter interface — the boundary between the
  pipeline and any encoder-classifier backend (so we can swap backends
  without changing skill packs).
- Implement the decision policy with hard-coded confidence thresholds
  (`label_only=0.45`, `warn=0.62`, `strong_warn=0.78`,
  `critical_intervention=0.85`) and uncertainty handling.
- Implement child-safety priority rules with severity floor 5.
- Validate the pipeline against the test-suite metrics:
  `child_safety_recall ≥ 0.98`, `protected_speech_false_positive ≤
  0.05`, latency p95 ≤ 250 ms.

**Deliverables**

- Working hybrid pipeline implementation.
- Encoder classifier adapter interface specification + a reference adapter.
- Threshold enforcement.
- Child-safety severity-floor handling.
- Metric-validation report against Phase 1 / Phase 2 packs.

---

## Phase 4 — Skill Pack Compiler + Signing

Goal: turn authored YAML into signed, distributable skill packs that the
runtime can verify.

- Build the skill-pack compiler pipeline: policy authoring → legal /
  cultural review → YAML skill pack → test-suite generation →
  classifier-bundle compiler → signed compressed bundle.
- Implement the skill passport with version, reviewers, model
  compatibility, and an ed25519 signature.
- Implement validation rules for anti-misuse controls:
  - No vague categories.
  - Required legal review (and cultural review) for every jurisdiction
    pack.
  - Protected-context handling required for any category with severity
    floor ≥ 4.
- Build prompt compilation from the active skill bundle to the minimal
  compiled prompt (within the 1800-instruction-token budget).

**Deliverables**

- Compiler pipeline implementation.
- Skill passport schema + signing implementation.
- Bundle signing tooling (ed25519).
- Anti-misuse validation rules + tests.
- Compiled-prompt reference outputs for the Phase 1–2 packs.

---

## Phase 5 — Country-Specific Expansion

Goal: graduate from archetypes to real country packs.

- Expand from 3 archetype jurisdictions to real country-specific packs
  by filling in the jurisdiction overlay template per country.
- Prioritise countries based on the KChat user base (start with the
  highest-population markets).
- For each country, complete:
  - Legal review.
  - Cultural review.
  - Local language lexicons.
  - Transliteration and normalization rules.
  - Country-specific test suites including minority-language false-
    positive tests.
- **Target: 10–20 country packs** in the first expansion wave.

**Deliverables**

- Country-specific jurisdiction overlays (10–20).
- Localized lexicons and normalization rules.
- Per-country test suites with passing metrics.

---

## Phase 6 — Scale, Audit, and Continuous Improvement

Goal: scale the skill library and operate it responsibly.

- Scale to **100–200 jurisdiction / community skills** through ongoing
  expansion and contributions from regional reviewers.
- Implement bias auditing for protected-class and minority-language
  effects across the signed library.
- Implement skill pack versioning, rollback, and expiry-review
  workflows (no pack older than its `expires_on` ships to devices).
- Continuously expand the test suite with adversarial / obfuscation
  test cases (homoglyph attacks, code-switching, leetspeak, image-text,
  evasion patterns).
- Performance optimization for on-device latency targets — keep p95
  ≤ 250 ms as model size and skill bundles grow.
- Integrate community feedback and the **appeal flow** so user appeals
  can produce skill-pack updates without weakening the privacy
  contract.
- Run regulatory alignment reviews against the EU Digital Services Act,
  NIST AI Risk Management Framework, and UNICEF / ITU child online
  protection guidelines.

**Deliverables**

- Scaled skill library (100–200 packs).
- Audit framework for bias and minority-language effects.
- Versioning, rollback, and expiry-review workflows.
- Adversarial / obfuscation test corpus.
- Regulatory alignment documentation.
