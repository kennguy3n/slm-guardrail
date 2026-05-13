# KChat Guardrail Skills — Progress

**Status:** All six phases complete. The repository ships **100 skills**
(59 country packs + 38 community overlays + 3 jurisdiction archetypes
+ the global baseline) plus the XLM-R encoder classifier integration,
skill-pack compiler, ed25519 skill passports, bias auditor, pack
lifecycle store, adversarial / obfuscation corpus, regulatory
alignment, performance benchmarks, and the community appeal flow.

This file tracks delivery against the phased plan in
[`PHASES.md`](PHASES.md). For the per-session changelog, the
encoder-classifier evolution history, and the
phase-by-phase deliverable checklists, see
[`docs/DEVELOPMENT_LOG.md`](docs/DEVELOPMENT_LOG.md).

## Phase summary

| Phase | Focus | Status |
|---|---|---|
| 0 | Foundation: repo scaffold, taxonomy, severity rubric, output schema, privacy contract | Complete |
| 1 | Global baseline skill + first 8 community overlays + first round of test cases | Complete |
| 2 | Jurisdiction archetype overlays (`strict-adult`, `strict-hate`, `strict-marketplace`) | Complete |
| 3 | Hybrid local pipeline + encoder classifier integration (XLM-R via ONNX) | Complete |
| 4 | Skill-pack compiler + signing (ed25519 skill passports) | Complete |
| 5 | Country-specific expansion — 40 country packs (Phase 5 wave) | Complete |
| 6 | Scale + audit + continuous improvement: 19 additional country packs, 30 additional community overlays, bias auditing, pack lifecycle, adversarial corpus, regulatory alignment, performance benchmarks, appeal flow | Complete |

## Known gaps and next steps

The platform is feature-frozen at the end of Phase 6. There is no
in-flight skill or compiler work on `main`.

Upcoming operational items — to be tracked in
[`PHASES.md`](PHASES.md) when scheduled:

- **Skill-pack roster maintenance.** Re-validate each of the 59
  country packs against upstream legal changes on a quarterly
  cadence; refresh `lexicons/` and `normalization.yaml` as
  jurisdictions revise the underlying statutes.
- **Encoder re-train / re-export.** Periodically refresh
  `models/xlmr.onnx` against new adversarial / obfuscation corpora
  and re-run the bias auditor before publishing a new pack
  generation.
- **Community feedback ingestion.** The appeal-flow plumbing exists
  end-to-end but the public-facing intake form is operator-driven;
  promote it to a first-class deliverable once a production tenant
  is live.

See [`docs/DEVELOPMENT_LOG.md`](docs/DEVELOPMENT_LOG.md) for the full
historical record.
