# KChat Guardrail Skills

> On-device guardrail skills for privacy-first, E2EE messaging — local
> safety assistants, not centralized moderation.

## What Is This?

A **skill-based guardrail system** for an on-device **encoder classifier**
running locally on user devices within
[KChat](https://github.com/kennguy3n/slm-chat-demo), an end-to-end
encrypted (E2EE) messaging app for large communities. The reference
backend is **XLM-R**, a multilingual transformer encoder; the
classifier interface is backend-agnostic so other encoder backends
can be plugged in without changing skill packs.

A guardrail skill classifies content **already visible on the user's
device**, produces local warnings, labels, and suggestions, and **never
transmits message content, embeddings, hashes, or other content-derived
evidence to servers** by default. The classifier acts as a **local
safety assistant** for the user — it does not act as a centralized
moderator on behalf of the platform.

KChat uses the **Messaging Layer Security** protocol
([RFC 9420](https://www.rfc-editor.org/rfc/rfc9420.html)) for group key
agreement, forward secrecy, and post-compromise security. MLS is
deliberately silent on application-layer policy: moderation, safety,
abuse prevention, and community rules are explicitly outside the
protocol scope. This project fills that gap — at the user's device, on
the user's terms, in a transparent and auditable way.

## Core Principles

- **Privacy-first.** Skills analyze only locally visible content. They
  never upload text, embeddings, hashes, message identifiers, or
  evidence to a server by default.
- **Transparency.** Users can see which skill packs are active, the
  version and signer of each pack, and the reason a particular skill
  was activated.
- **Layered skills.** Behaviour is composed from a Global Baseline, an
  optional Jurisdiction Overlay, and an optional Community Overlay.
- **Deterministic output.** The encoder classifier emits a constrained
  JSON schema via argmax over a fixed bank of category prototype
  embeddings, so identical input always produces identical output.
- **Hybrid pipeline.** Cheap deterministic local detectors run first;
  the classifier only does the contextual reasoning that detectors
  cannot.
- **Anti-misuse.** No vague categories, signed packs, expiry dates,
  protected-speech contexts, and required legal / cultural review for
  every jurisdiction overlay.

## Skill Architecture

Skills compose into a runtime bundle in three layers — a **Global
Baseline** (always on), an optional **Jurisdiction Overlay**, and an
optional **Community Overlay**. The active runtime bundle is:

    active_skill_bundle =
        global_baseline
      + jurisdiction overlays
      + community overlay
      + runtime context

See [ARCHITECTURE.md](ARCHITECTURE.md#skill-layering-model) for the
full layering diagram, conflict-resolution rules, and overlay
templates.

## Global Risk Taxonomy

The global baseline defines 16 categories. Every skill — including
jurisdiction and community overlays — must classify content into
exactly one of these IDs (overlays may *narrow* a category or raise
its severity, but they may not invent new categories). The current
taxonomy is:

`SAFE`, `CHILD_SAFETY`, `SELF_HARM`, `VIOLENCE_THREAT`, `EXTREMISM`,
`HARASSMENT`, `HATE`, `SCAM_FRAUD`, `MALWARE_LINK`, `PRIVATE_DATA`,
`SEXUAL_ADULT`, `DRUGS_WEAPONS`, `ILLEGAL_GOODS`,
`MISINFORMATION_HEALTH`, `MISINFORMATION_CIVIC`, `COMMUNITY_RULE`.

Severity is reported on a 0–5 rubric. See
[`kchat-skills/global/taxonomy.yaml`](kchat-skills/global/taxonomy.yaml),
[`kchat-skills/global/severity.yaml`](kchat-skills/global/severity.yaml),
and the canonical category descriptions and decision policy in
[ARCHITECTURE.md](ARCHITECTURE.md#global-risk-taxonomy).

## Quick Start

    # 1. Clone
    git clone https://github.com/kennguy3n/slm-guardrail.git
    cd slm-guardrail

    # 2. (optional) create a virtualenv
    python -m venv .venv && source .venv/bin/activate

    # 3. Install test dependencies
    pip install -e ".[test]"      # or: pip install -r requirements.txt

    # 4. Run the test suite
    pytest                        # ~40 tests, <1s

The `XLMRAdapter` degrades to a SAFE-only fallback when
`models/xlmr.onnx` is missing, so the test suite and demo run
out-of-the-box. To exercise the real encoder classifier (the
on-device runtime path), follow the ONNX export steps in
[`docs/RUNNING_XLMR.md`](docs/RUNNING_XLMR.md).

## Running Tests

    pytest                                  # all tests
    pytest kchat-skills/tests/global        # global-baseline only
    pytest kchat-skills/tests/jurisdictions # minority-language FP corpus
    pytest kchat-skills/tests/adversarial   # obfuscation / evasion corpus

The suite is pure Python — no encoder weights are required at test
time; the adapter degrades to a SAFE fallback when weights are
missing.

## Compiling a Skill Pack

`kchat-skills/compiler/compiler.py` composes the global baseline plus
jurisdiction and community overlays into a single compiled prompt for
the encoder classifier runtime:

    # Compile the global baseline only (writes the compiled prompt to stdout):
    python kchat-skills/compiler/compiler.py

    # Compile baseline + jurisdiction archetype + community overlay:
    python kchat-skills/compiler/compiler.py \
        --jurisdiction archetype-strict-adult \
        --community    workplace \
        --out          /tmp/strict_adult_workplace.txt

See [`docs/COMPILER.md`](docs/COMPILER.md) for the compiler internals,
the signing workflow (ed25519 skill passports), bias auditing,
pack lifecycle (versioning / rollback / retention), regulatory
alignment, performance benchmarks, and the community appeal flow.

## Repository Contents

The repository ships **100 skill packs**:

| Pack family | Count | Location |
|---|---|---|
| Global baseline | 1 | [`kchat-skills/global/`](kchat-skills/global/) |
| Jurisdiction archetypes | 3 | [`kchat-skills/jurisdictions/`](kchat-skills/jurisdictions/) |
| Country packs | 59 | [`kchat-skills/jurisdictions/<cc>/`](kchat-skills/jurisdictions/) |
| Community overlays | 38 | [`kchat-skills/communities/`](kchat-skills/communities/) |

For the full country / overlay roster (ISO codes, primary languages,
key legal references, age modes, notable category tightenings), see
[`docs/SUPPORTED_REGIONS.md`](docs/SUPPORTED_REGIONS.md).

## Documentation

| Document | Purpose |
|----------|---------|
| [PROPOSAL.md](PROPOSAL.md) | Problem statement, design goals, scope, success metrics |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Technical reference: taxonomy, severity, privacy contract, pipeline, schemas, templates |
| [PHASES.md](PHASES.md) | Development roadmap (all phases complete) |
| [PROGRESS.md](PROGRESS.md) | Current project status and next steps |
| [docs/COMPILER.md](docs/COMPILER.md) | Skill-pack compiler, signing, bias auditing, lifecycle, benchmarks |
| [docs/RUNNING_XLMR.md](docs/RUNNING_XLMR.md) | ONNX export guide and XLM-R encoder runtime |
| [docs/SUPPORTED_REGIONS.md](docs/SUPPORTED_REGIONS.md) | Full roster of 59 country packs and 38 community overlays |
| [docs/CHANGELOG.md](docs/CHANGELOG.md) | Historical development changelog |
| [kchat-skills/docs/regulatory/](kchat-skills/docs/regulatory/) | EU DSA, NIST AI RMF, UNICEF / ITU COP alignment mappings |

## References

- IETF RFC 9420 — *The Messaging Layer Security (MLS) Protocol*. <https://www.rfc-editor.org/rfc/rfc9420.html>
- European Union — *Digital Services Act (Regulation (EU) 2022/2065)*. <https://eur-lex.europa.eu/eli/reg/2022/2065/oj>
- NIST — *AI Risk Management Framework (AI RMF 1.0)*. <https://www.nist.gov/itl/ai-risk-management-framework>
- UNICEF — *Child Online Protection guidelines*. <https://www.unicef.org/childrens-rights-online>

## License

Proprietary — Copyright 2026 KChat Contributors. All rights reserved.
This code is provided as a reference implementation under a
Proprietary license. Contact the maintainers for licensing inquiries.
