# KChat SLM Guardrail Skills

> On-device guardrail skills for privacy-first, E2EE messaging — local safety
> assistants, not centralized moderation.

---

## What is this?

This project provides a **skill-based guardrail system** for tiny Small
Language Models (SLMs) running locally on user devices within
[KChat](https://github.com/kennguy3n/slm-chat-demo), an end-to-end encrypted
(E2EE) messaging app for large communities.

A guardrail skill classifies content **already visible on the user's device**,
produces local warnings / labels / suggestions, and **never transmits message
content, embeddings, hashes, or other content-derived evidence to servers** by
default. The SLM acts as a **local safety assistant** for the user — it does
not act as a centralized moderator on behalf of the platform.

KChat uses the **Messaging Layer Security** protocol
([RFC 9420](https://www.rfc-editor.org/rfc/rfc9420.html)) for group key
agreement, forward secrecy, and post-compromise security. MLS is deliberately
silent on application-layer policy: moderation, safety, abuse prevention, and
community rules are explicitly outside the protocol scope. This project fills
that gap — at the user's device, on the user's terms, in a transparent and
auditable way.

## Core Principles

- **Privacy-first.** Skills analyze only locally visible content. They never
  upload text, embeddings, hashes, message identifiers, or evidence to a
  server by default.
- **Transparency.** Users can see which skill packs are active, the version
  and signer of each pack, and the reason a particular skill was activated.
- **Layered skills.** Behaviour is composed from a *Global Baseline*, an
  optional *Jurisdiction Overlay*, and an optional *Community Overlay*.
- **Deterministic output.** The SLM emits a constrained JSON schema. Prompts
  are short and the taxonomy is compact so tiny models stay on-rail.
- **Hybrid pipeline.** Cheap deterministic local detectors run first; the SLM
  only does the contextual reasoning that detectors cannot.
- **Anti-misuse.** No vague categories, signed packs, expiry dates,
  protected-speech contexts, and required legal/cultural review for every
  jurisdiction overlay.

## Skill Architecture

Skills compose into a runtime bundle in three layers:

```
┌──────────────────────────────────────────────────────┐
│ GLOBAL BASELINE SKILL                                │
│  • Universal taxonomy (16 categories)                │
│  • Severity rubric (0–5)                             │
│  • Privacy rules                                     │
│  • Output schema                                     │
└──────────────────────────────────────────────────────┘
                       +
┌──────────────────────────────────────────────────────┐
│ JURISDICTION OVERLAY SKILL  (optional)               │
│  • Country / region-specific rules                   │
│  • Local language assets (lexicons, transliteration) │
│  • Legal ages, restricted symbols, election rules    │
└──────────────────────────────────────────────────────┘
                       +
┌──────────────────────────────────────────────────────┐
│ COMMUNITY OVERLAY SKILL  (optional)                  │
│  • Group profile (school / family / workplace / …)   │
│  • Group-specific rules and counters                 │
│  • Set by group admin, visible to all members        │
└──────────────────────────────────────────────────────┘
                       +
                  runtime context
```

The active runtime bundle is therefore:

```
active_skill_bundle =
    global_baseline
  + jurisdiction overlays
  + community overlay
  + runtime context
```

## Global Risk Taxonomy

The global baseline defines 16 categories. Every skill — including
jurisdiction and community overlays — must classify content into exactly one
of these IDs (overlays may *narrow* a category or raise its severity, but
they may not invent new categories).

| ID  | Category               | Description                                                                                       | Typical Local Action                                  |
| --- | ---------------------- | ------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| 0   | SAFE                   | No detected risk.                                                                                 | None.                                                 |
| 1   | CHILD_SAFETY           | Content sexualising or endangering minors; grooming patterns; CSAM indicators.                    | Block preview, hard warn, surface report flow.        |
| 2   | SELF_HARM              | Suicide ideation, self-injury planning, pro-ana / pro-mia content.                                | Soft warn, surface local crisis resources.            |
| 3   | VIOLENCE_THREAT        | Credible threats of physical violence against an identifiable target.                             | Strong warn, surface report and block flows.          |
| 4   | EXTREMISM              | Recruitment / glorification of violent extremist orgs (jurisdiction-listed).                      | Strong warn; jurisdictional override possible.        |
| 5   | HARASSMENT             | Targeted insults, doxxing, sustained pile-on, sexual harassment.                                  | Warn, suggest mute / report.                          |
| 6   | HATE                   | Dehumanising speech against a protected class.                                                    | Warn; protected-speech context check.                 |
| 7   | SCAM_FRAUD             | Phishing, advance-fee fraud, fake giveaways, impersonation.                                       | Warn, mark links, surface report.                     |
| 8   | MALWARE_LINK           | Links / attachments matching malware or credential-stealing patterns.                             | Block link preview, hard warn.                        |
| 9   | PRIVATE_DATA           | PII / financial / credentials / location of self or others.                                       | Warn before send / before display, suggest redaction. |
| 10  | SEXUAL_ADULT           | Adult sexual content between consenting adults.                                                   | Label; gated by group age mode + jurisdiction.        |
| 11  | DRUGS_WEAPONS          | Sale or facilitation of drugs / firearms / regulated goods.                                       | Warn; jurisdictional override common.                 |
| 12  | ILLEGAL_GOODS          | Stolen goods, counterfeit currency, trafficked items.                                             | Warn; surface report flow.                            |
| 13  | MISINFORMATION_HEALTH  | Health claims contradicting public-health consensus in a high-harm context.                       | Label, link to authoritative source.                  |
| 14  | MISINFORMATION_CIVIC   | Election / civic misinformation in a jurisdiction-flagged window.                                 | Label, link to electoral authority.                   |
| 15  | COMMUNITY_RULE         | Content violating an explicit community-overlay rule.                                             | Label per community overlay action.                   |

## Severity Rubric

| Level | Name        | Meaning                                                                | Action                                              |
| ----- | ----------- | ---------------------------------------------------------------------- | --------------------------------------------------- |
| 0     | None        | No risk detected.                                                      | None.                                               |
| 1     | Informational | Minor signal, useful as label only.                                  | Soft label; no interruption.                        |
| 2     | Caution     | Possible issue; user benefit from awareness.                           | Inline label; expandable explanation.               |
| 3     | Warn        | Likely policy / safety risk.                                           | Modal warning before display or send.               |
| 4     | Strong warn | High-confidence harm to user or third party.                           | Hard modal; require explicit acknowledge to view.   |
| 5     | Critical    | Imminent harm, child safety, or jurisdictional illegality.             | Block preview; surface report / crisis resources.   |

Severity is computed by the SLM under a hard-coded thresholds policy
(see [`ARCHITECTURE.md`](ARCHITECTURE.md#decision-policy)). Child-safety
categories have a severity floor of 5 regardless of model confidence.

## Folder Structure

```
/kchat-skills
├── global/
│   ├── baseline.yaml                # kchat.global.guardrail.baseline
│   ├── taxonomy.yaml                # 16-category global taxonomy
│   ├── severity.yaml                # 0–5 severity rubric
│   ├── output_schema.json           # constrained JSON output
│   ├── local_signal_schema.json     # SLM input contract
│   └── privacy_contract.yaml        # non-negotiable privacy rules
│
├── jurisdictions/
│   ├── _template/
│   │   └── overlay.yaml             # jurisdiction overlay template
│   ├── archetype-strict-adult/
│   ├── archetype-strict-hate/
│   ├── archetype-strict-marketplace/
│   ├── us/                          # United States (Phase 5)
│   ├── de/                          # Germany (Phase 5)
│   ├── br/                          # Brazil (Phase 5)
│   ├── in/                          # India (Phase 5)
│   ├── jp/                          # Japan (Phase 5)
│   └── <country-code>/              # filled per-country packs
│       ├── overlay.yaml
│       ├── lexicons/
│       ├── normalization.yaml
│       └── tests/
│
├── communities/
│   ├── _template/
│   │   └── overlay.yaml             # community overlay template
│   ├── school.yaml
│   ├── family.yaml
│   ├── workplace.yaml
│   ├── adult_only.yaml
│   ├── marketplace.yaml
│   ├── health_support.yaml
│   ├── political.yaml
│   └── gaming.yaml
│
├── prompts/
│   ├── runtime_instruction.txt      # 10-rule SLM instruction
│   └── compiled_examples/
│
├── compiler/
│   ├── pipeline.md
│   └── skill_passport.schema.json
│
├── tests/
│   ├── global/
│   ├── jurisdictions/
│   └── communities/
│
└── docs/
    ├── PROPOSAL.md
    ├── ARCHITECTURE.md
    └── PHASES.md
```

## Practical First Build

The first 12 starter skills cover the global baseline plus enough overlays
to validate the layering model. Items marked **(landed)** ship in this
repository today; the rest are scheduled for Phase 2.

1. `kchat.global.guardrail.baseline` — the global baseline skill. **(landed)**
2. `community.school` — minors-aware community overlay. **(landed)**
3. `community.family` — household / kin group overlay. **(landed)**
4. `community.workplace` — professional / B2B overlay. **(landed)**
5. `community.adult_only` — explicitly opt-in adult overlay. **(landed)**
6. `community.marketplace` — buy / sell / trade overlay. **(landed)**
7. `community.health_support` — peer-support overlay (loosens self-harm
   labels in supportive context, tightens medical-misinformation rules). **(landed)**
8. `community.political` — campaign / civic overlay. **(landed)**
9. `community.gaming` — large public gaming community overlay. **(landed)**
10. `jurisdiction.archetype-strict-adult` — strict adult-content jurisdiction
    archetype. **(landed)**
11. `jurisdiction.archetype-strict-hate` — strict hate / extremism
    jurisdiction archetype. **(landed)**
12. `jurisdiction.archetype-strict-marketplace` — strict marketplace /
    restricted-goods jurisdiction archetype. **(landed)**
13. `kchat.jurisdiction.us.guardrail.v1` — United States country pack
    (federal CSAM, FTO list, FTC / wire-fraud floors). **(landed)**
14. `kchat.jurisdiction.de.guardrail.v1` — Germany country pack
    (StGB §86a / NetzDG, Volksverhetzung StGB §130, JuSchG). **(landed)**
15. `kchat.jurisdiction.br.guardrail.v1` — Brazil country pack
    (ECA, Lei 7.716/89, TSE election rules). **(landed)**
16. `kchat.jurisdiction.in.guardrail.v1` — India country pack
    (UAPA, IPC §153A / §295A, IT Act §67). **(landed)**
17. `kchat.jurisdiction.jp.guardrail.v1` — Japan country pack
    (child-protection statute, tokushoho, drug & weapon laws). **(landed)**

These let us exercise the full bundle composition (global + jurisdiction +
community) end-to-end across both archetypal and concrete country
packs. Phase 5 will continue with 5–15 more country packs.

## Getting Started

This project is in **early development**. There is no runtime yet; the
deliverables are skill *definitions* (YAML), prompt templates, schemas, test
suites, and a compiler specification.

Phase 0 (foundation), Phase 1 (global baseline + community overlays),
Phase 2 (jurisdiction archetype overlays), Phase 3 (hybrid local
pipeline + SLM integration), and Phase 4 (skill-pack compiler +
signing) are complete. Phase 5 (country-specific expansion) has
shipped its first wave of 5 country packs (US, DE, BR, IN, JP), and
Phase 6 has shipped the bias-auditing framework and the pack
lifecycle / rollback / expiry-review store. The repository
currently ships:

- the complete (non-stub) global baseline
  ([`kchat-skills/global/baseline.yaml`](kchat-skills/global/baseline.yaml)),
  the SLM input contract
  ([`local_signal_schema.json`](kchat-skills/global/local_signal_schema.json))
  and privacy contract
  ([`privacy_contract.yaml`](kchat-skills/global/privacy_contract.yaml)),
- the runtime SLM instruction prompt
  ([`prompts/runtime_instruction.txt`](kchat-skills/prompts/runtime_instruction.txt))
  and compiled-prompt format reference
  ([`prompts/compiled_prompt_format.md`](kchat-skills/prompts/compiled_prompt_format.md)),
- the eight community overlays under
  [`kchat-skills/communities/`](kchat-skills/communities/),
- the device-local expiring counter implementation at
  [`kchat-skills/compiler/counters.py`](kchat-skills/compiler/counters.py),
- the 7-step hybrid local pipeline at
  [`kchat-skills/compiler/pipeline.py`](kchat-skills/compiler/pipeline.py),
  the backend-agnostic SLM runtime adapter at
  [`kchat-skills/compiler/slm_adapter.py`](kchat-skills/compiler/slm_adapter.py),
  and the hard-coded threshold policy at
  [`kchat-skills/compiler/threshold_policy.py`](kchat-skills/compiler/threshold_policy.py),
- the test-suite template at
  [`kchat-skills/tests/test_suite_template.yaml`](kchat-skills/tests/test_suite_template.yaml)
  and the first round of baseline test cases at
  [`kchat-skills/tests/global/test_baseline_cases.py`](kchat-skills/tests/global/test_baseline_cases.py),
- the jurisdiction overlay template and three archetype overlays
  (`archetype-strict-adult`, `archetype-strict-hate`,
  `archetype-strict-marketplace`) under
  [`kchat-skills/jurisdictions/`](kchat-skills/jurisdictions/), plus a
  per-archetype minority-language / code-switching false-positive
  corpus at
  [`kchat-skills/tests/jurisdictions/test_minority_language_fp.py`](kchat-skills/tests/jurisdictions/test_minority_language_fp.py),
- the Phase 4 skill-pack compiler at
  [`kchat-skills/compiler/compiler.py`](kchat-skills/compiler/compiler.py)
  with the metric validator at
  [`kchat-skills/compiler/metric_validator.py`](kchat-skills/compiler/metric_validator.py),
  the ed25519 skill-passport implementation at
  [`kchat-skills/compiler/skill_passport.py`](kchat-skills/compiler/skill_passport.py)
  (schema at
  [`kchat-skills/compiler/skill_passport.schema.json`](kchat-skills/compiler/skill_passport.schema.json)),
  and the anti-misuse validator at
  [`kchat-skills/compiler/anti_misuse.py`](kchat-skills/compiler/anti_misuse.py),
- 19 reference compiled prompts under
  [`kchat-skills/prompts/compiled_examples/`](kchat-skills/prompts/compiled_examples/)
  covering the global baseline, every Phase 1–2 community and
  jurisdiction overlay combination, and the five Phase 5 country
  packs (`country_us.txt`, `country_de.txt`, `country_br.txt`,
  `country_in.txt`, `country_jp.txt`),
- the Phase 5 first-wave country packs at
  [`kchat-skills/jurisdictions/us/`](kchat-skills/jurisdictions/us/),
  [`/de/`](kchat-skills/jurisdictions/de/),
  [`/br/`](kchat-skills/jurisdictions/br/),
  [`/in/`](kchat-skills/jurisdictions/in/), and
  [`/jp/`](kchat-skills/jurisdictions/jp/) — each with concrete
  legal-age, protected-class, listed-extremist-org, election-rule,
  and override values, a `normalization.yaml`, and per-language
  lexicons under `lexicons/` — all passing
  [`anti_misuse.validate_pack`](kchat-skills/compiler/anti_misuse.py),
- the Phase 6 bias auditor at
  [`kchat-skills/compiler/bias_audit.py`](kchat-skills/compiler/bias_audit.py)
  (per-protected-class and per-minority-language false-positive rates,
  disparity detection, structured `BiasAuditReport`),
- the Phase 6 pack-lifecycle store at
  [`kchat-skills/compiler/pack_lifecycle.py`](kchat-skills/compiler/pack_lifecycle.py)
  (`PackStore` with versioning, rollback, retention cap of 3,
  expiry / 30-day review window, JSON round-trip).

### Quick start

```bash
# 1. Clone
git clone https://github.com/kennguy3n/slm-guardrail.git
cd slm-guardrail

# 2. (optional) create a virtualenv
python3 -m venv .venv
source .venv/bin/activate

# 3. Install test dependencies
pip install -r requirements.txt
# or, equivalently:
pip install -e ".[test]"
```

### How to run tests

The test suite validates the structural primitives of the global
baseline (`taxonomy.yaml`, `severity.yaml`, `output_schema.json`,
`baseline.yaml`). It is pure Python — no SLM runtime is required.

```bash
pytest                                  # run all tests
pytest kchat-skills/tests/global        # only the global-baseline tests
pytest kchat-skills/tests/communities   # only the community-overlay tests
pytest kchat-skills/tests/jurisdictions # only the jurisdiction tests
pytest -v                               # verbose
```

### Project layout

The implementation tree lives under [`kchat-skills/`](kchat-skills/),
following the recommended folder structure documented in
[`ARCHITECTURE.md`](ARCHITECTURE.md#recommended-folder-structure):

```
kchat-skills/
├── global/               # global baseline skill: taxonomy, severity, schemas
├── jurisdictions/        # jurisdiction overlay packs (Phase 2+)
│   ├── _template/
│   │   └── overlay.yaml
│   ├── archetype-strict-adult/
│   │   ├── overlay.yaml          # severity floor 5 on category 10
│   │   ├── normalization.yaml
│   │   └── lexicons/
│   ├── archetype-strict-hate/
│   │   ├── overlay.yaml          # severity floor 5 on cat 4, 4 on cat 6
│   │   ├── normalization.yaml
│   │   └── lexicons/
│   ├── archetype-strict-marketplace/
│   │   ├── overlay.yaml          # severity floor 4 on cat 11 & 12
│   │   ├── normalization.yaml
│   │   └── lexicons/
│   ├── us/                       # United States (Phase 5)
│   ├── de/                       # Germany (Phase 5)
│   ├── br/                       # Brazil (Phase 5)
│   ├── in/                       # India (Phase 5; +Devanagari translit)
│   └── jp/                       # Japan (Phase 5; +translit_ja_v1)
├── communities/          # community overlay packs (Phase 1+)
│   ├── _template/         # community overlay template
│   ├── school.yaml        # minors-aware
│   ├── family.yaml        # household / kin
│   ├── workplace.yaml     # professional / B2B
│   ├── adult_only.yaml    # opt-in adult
│   ├── marketplace.yaml   # buy / sell / trade
│   ├── health_support.yaml
│   ├── political.yaml     # campaign / civic
│   └── gaming.yaml        # public gaming community
├── prompts/              # 10-rule SLM instruction + compiled examples
│   ├── runtime_instruction.txt
│   ├── compiled_prompt_format.md
│   └── compiled_examples/  # 14 reference compiled prompts (Phase 4)
├── compiler/             # skill-pack compiler (Phase 3-4)
│   ├── counters.py           # device-local expiring counter store (Phase 1)
│   ├── pipeline.py           # 7-step hybrid local pipeline (Phase 3)
│   ├── slm_adapter.py        # SLMAdapter Protocol + MockSLMAdapter (Phase 3)
│   ├── threshold_policy.py   # hard-coded threshold enforcement (Phase 3)
│   ├── metric_validator.py   # 7-metric validator (Phase 3)
│   ├── compiler.py           # skill-pack compiler pipeline (Phase 4)
│   ├── skill_passport.py     # ed25519 signing / verification (Phase 4)
│   ├── skill_passport.schema.json  # Draft-07 passport schema (Phase 4)
│   ├── anti_misuse.py        # anti-misuse validation rules (Phase 4)
│   ├── bias_audit.py         # bias auditor (Phase 6)
│   └── pack_lifecycle.py     # pack store / rollback / expiry (Phase 6)
├── tests/                # pytest validation suite
│   ├── test_suite_template.yaml    # metrics framework (Phase 1)
│   ├── test_test_suite_template.py
│   ├── global/
│   │   ├── test_baseline_cases.py  # first round of baseline cases
│   │   ├── test_counters.py
│   │   ├── test_pipeline.py        # 7-step hybrid pipeline
│   │   ├── test_slm_adapter.py     # SLMAdapter / MockSLMAdapter
│   │   ├── test_threshold_policy.py # hard-coded threshold policy
│   │   ├── test_metric_validator.py # 7-metric validator (Phase 3)
│   │   ├── test_compiler.py         # skill-pack compiler (Phase 4)
│   │   ├── test_skill_passport.py   # ed25519 passport (Phase 4)
│   │   ├── test_anti_misuse.py      # anti-misuse rules (Phase 4)
│   │   ├── test_compiled_examples.py # compiled-prompt references
│   │   ├── test_bias_audit.py       # bias auditor (Phase 6)
│   │   └── test_pack_lifecycle.py   # pack-lifecycle store (Phase 6)
│   ├── jurisdictions/
│   │   ├── test_jurisdiction_template.py
│   │   ├── test_archetype_strict_adult.py
│   │   ├── test_archetype_strict_hate.py
│   │   ├── test_archetype_strict_marketplace.py
│   │   ├── test_country_us.py       # United States pack (Phase 5)
│   │   ├── test_country_de.py       # Germany pack (Phase 5)
│   │   ├── test_country_br.py       # Brazil pack (Phase 5)
│   │   ├── test_country_in.py       # India pack (Phase 5)
│   │   ├── test_country_jp.py       # Japan pack (Phase 5)
│   │   └── test_minority_language_fp.py
│   └── communities/
└── docs/                 # pointers to the root-level project docs

tools/                    # repo-level utilities (run from repo root)
└── regenerate_compiled_examples.py  # refresh compiled_examples/*.txt
```

### Compiling a skill pack

The Phase 4 compiler resolves the global baseline plus optional
jurisdiction and community overlays into a single compiled prompt
(< 1800 instruction tokens) ready for the on-device SLM:

```bash
# Compile the global baseline only (writes to stdout):
python kchat-skills/compiler/compiler.py > /tmp/baseline.txt

# Compile baseline + jurisdiction archetype + community overlay:
python kchat-skills/compiler/compiler.py \
    --jurisdiction archetype-strict-marketplace \
    --community workplace \
    --out kchat-skills/prompts/compiled_examples/strict_marketplace_workplace.txt
```

The CLI is also available as
`PYTHONPATH=kchat-skills/compiler python -m compiler ...`. To
regenerate the full set of reference
compiled examples after changing baseline / overlays, run:

```bash
python tools/regenerate_compiled_examples.py
```

### Signing workflow

Every compiled bundle ships with an ed25519-signed *skill passport*
(see [`compiler/skill_passport.py`](kchat-skills/compiler/skill_passport.py)
and [`compiler/skill_passport.schema.json`](kchat-skills/compiler/skill_passport.schema.json)).
The passport carries identity (`skill_id`, `skill_version`, `parent`),
provenance (`authored_by`, `reviewed_by.legal/cultural/trust_and_safety`),
model compatibility (`model_id` / `model_min_version` /
`max_instruction_tokens` / `max_output_tokens`), an `expires_on`
date (max 18 months from issuance), the per-pack `test_results`
recorded by [`metric_validator`](kchat-skills/compiler/metric_validator.py),
and a base64 ed25519 `signature` covering the deterministic JSON
serialisation of every other field.

A pack is rejected by the runtime if any of the following is true:
the signature does not verify against the compiler's public key;
`expires_on` is in the past or more than 18 months in the future;
the runtime SLM is not listed in `model_compatibility`; or any of the
[`anti_misuse`](kchat-skills/compiler/anti_misuse.py) rules fail
(invented categories, overlay redefining `privacy_rules`, jurisdiction
pack missing `legal_review` / `cultural_review` signers, community
pack missing `trust_and_safety` signer, severity floors ≥ 4 without
protected-speech `allowed_contexts`, or lexicons without provenance).

### Bias Auditing

The Phase 6 bias auditor at
[`kchat-skills/compiler/bias_audit.py`](kchat-skills/compiler/bias_audit.py)
turns a list of `BiasAuditCase` rows (each tagged with a
`protected_class`, `language`, expected and predicted taxonomy id)
into a structured `BiasAuditReport`. It computes the per-protected-
class and per-minority-language false-positive rate (a case is a
false positive when `expected_category == SAFE` but
`predicted_category != SAFE`), flags any group that exceeds the
0.07 ceiling — bound to the `minority_language_false_positive`
shipping target — or shows >0.05 disparity vs. the overall mean,
and marks the audit `passed=False` if anything is flagged. The
compiler can invoke the auditor after the metric validator so every
signed pack carries evidence that its behaviour does not skew
across protected classes or languages.

### Pack Lifecycle

The Phase 6 pack store at
[`kchat-skills/compiler/pack_lifecycle.py`](kchat-skills/compiler/pack_lifecycle.py)
is a JSON-serialisable, device-local ledger of signed pack
versions. Each `PackVersion` records the `skill_id`,
`skill_version`, observed `signed_on` date, `expires_on`,
`signature_valid`, and `is_active` flags. The `PackStore` exposes:

- `register(passport)` — register a freshly signed `SkillPassport`,
  marking it active and demoting the previous version;
- `get_active(skill_id)` / `get_history(skill_id)` — current and
  historical versions for a given pack;
- `rollback(skill_id)` — fall back to the previously signed
  version. Per ARCHITECTURE.md `anti_misuse_controls.technical`,
  `MAX_RETAINED_VERSIONS = 3` versions are retained on device;
- `check_expiry(now=None)` / `deactivate_expired(now=None)` /
  `needs_review(days_ahead=30, now=None)` — flag and act on
  expired packs, and surface the review queue
  (`EXPIRY_REVIEW_WINDOW_DAYS = 30`);
- `to_json()` / `from_json(raw)` — round-trip the ledger for
  device-local persistence.

### Documentation

- [`PROPOSAL.md`](PROPOSAL.md) — rationale, scope, success metrics.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — technical design: layering, privacy
  architecture, hybrid pipeline, schemas, anti-misuse controls.
- [`PHASES.md`](PHASES.md) — phased roadmap from foundation through scaled
  skill library and continuous improvement.
- [`PROGRESS.md`](PROGRESS.md) — current status and changelog.

## References

- IETF RFC 9420 — *The Messaging Layer Security (MLS) Protocol*. <https://www.rfc-editor.org/rfc/rfc9420.html>
- European Union — *Digital Services Act (Regulation (EU) 2022/2065)*. <https://eur-lex.europa.eu/eli/reg/2022/2065/oj>
- NIST — *AI Risk Management Framework (AI RMF 1.0)*. <https://www.nist.gov/itl/ai-risk-management-framework>
- UNICEF — *Child Online Protection guidelines*. <https://www.unicef.org/childrens-rights-online>

## License

License: TBD.
