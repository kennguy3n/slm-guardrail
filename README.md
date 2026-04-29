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
    archetype.
11. `jurisdiction.archetype-strict-hate` — strict hate / extremism
    jurisdiction archetype.
12. `jurisdiction.archetype-strict-marketplace` — strict marketplace /
    restricted-goods jurisdiction archetype.

These let us exercise the full bundle composition (global + jurisdiction +
community) end-to-end without committing to a specific country pack on day
one.

## Getting Started

This project is in **early development**. There is no runtime yet; the
deliverables are skill *definitions* (YAML), prompt templates, schemas, test
suites, and a compiler specification.

Phase 0 (foundation) and the first three Phase 1 deliverables are now
available: the complete (non-stub) global baseline
([`kchat-skills/global/baseline.yaml`](kchat-skills/global/baseline.yaml)),
the SLM input contract
([`local_signal_schema.json`](kchat-skills/global/local_signal_schema.json))
and privacy contract
([`privacy_contract.yaml`](kchat-skills/global/privacy_contract.yaml)),
the runtime SLM instruction prompt
([`prompts/runtime_instruction.txt`](kchat-skills/prompts/runtime_instruction.txt))
and compiled-prompt format reference
([`prompts/compiled_prompt_format.md`](kchat-skills/prompts/compiled_prompt_format.md)),
and the eight community overlays under
[`kchat-skills/communities/`](kchat-skills/communities/).

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
pytest                                 # run all tests
pytest kchat-skills/tests/global       # only the global-baseline tests
pytest kchat-skills/tests/communities  # only the community-overlay tests
pytest -v                              # verbose
```

### Project layout

The implementation tree lives under [`kchat-skills/`](kchat-skills/),
following the recommended folder structure documented in
[`ARCHITECTURE.md`](ARCHITECTURE.md#recommended-folder-structure):

```
kchat-skills/
├── global/               # global baseline skill: taxonomy, severity, schemas
├── jurisdictions/        # jurisdiction overlay packs (Phase 2+)
│   └── _template/
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
│   └── compiled_examples/
├── compiler/             # skill-pack compiler (Phase 4)
├── tests/                # pytest validation suite
│   ├── global/
│   ├── jurisdictions/
│   └── communities/
└── docs/                 # pointers to the root-level project docs
```

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
