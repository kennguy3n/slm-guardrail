# KChat Guardrail Skills — Project Status

The project is stable. All planned features are implemented. The
repository ships **100 skill packs** (1 global baseline + 3
jurisdiction archetypes + 59 country packs + 38 community overlays)
together with the XLM-R encoder classifier integration, the
skill-pack compiler, ed25519 skill passports, the bias auditor, the
pack lifecycle store, an adversarial / obfuscation corpus,
regulatory alignment documentation, performance benchmarks, and the
community appeal flow.

No active feature development. Ongoing work is limited to operational
maintenance (see [Operational Maintenance](#operational-maintenance)
below).

For the build sequence and the artifacts produced in each phase, see
[PHASES.md](PHASES.md). For the historical record of session-by-
session changes, see [docs/CHANGELOG.md](docs/CHANGELOG.md).

## Development Summary

| Area | Focus | Status |
|---|---|---|
| Foundation | Repo scaffold, taxonomy, severity rubric, output schema, privacy contract | Complete |
| Global Baseline + Community Overlays | Global baseline skill + first 8 community overlays + first round of test cases | Complete |
| Jurisdiction Archetypes | Archetype overlays (`strict-adult`, `strict-hate`, `strict-marketplace`) | Complete |
| Hybrid Local Pipeline | Pipeline + encoder classifier integration (XLM-R via ONNX) | Complete |
| Compiler + Signing | Skill-pack compiler + ed25519 skill passports | Complete |
| Country-Specific Expansion | 40 country packs in the first expansion wave | Complete |
| Scale + Audit | 19 additional country packs, 30 additional community overlays, bias auditing, pack lifecycle, adversarial corpus, regulatory alignment, performance benchmarks, appeal flow | Complete |

## Operational Maintenance

Ongoing operational items the project owners track:

- **Skill-pack roster maintenance.** Re-validate each of the 59
  country packs against upstream legal changes on a quarterly
  cadence; refresh `lexicons/` and `normalization.yaml` as
  jurisdictions revise the underlying statutes.
- **Encoder re-train / re-export.** Periodically refresh
  `models/xlmr.onnx` against new adversarial / obfuscation corpora
  and re-run the bias auditor before publishing a new pack
  generation.
- **Community feedback ingestion.** The appeal-flow plumbing exists
  end-to-end; the public-facing intake form is operator-driven and
  graduates to a first-class deliverable once a production tenant is
  live.

See [docs/CHANGELOG.md](docs/CHANGELOG.md) for the full historical
record.
