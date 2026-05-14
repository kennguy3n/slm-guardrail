# Demo Results

This directory holds snapshots of the cross-community / cross-country
demo produced by
[`tools/demo_guardrail.py`](../tools/demo_guardrail.py). Each
snapshot is a pair of files (`.json` + `.md`) named by the UTC
timestamp of the run.

The demo loads the same corpus that the benchmark consumes
([`kchat-skills/samples/sample_messages.yaml`](../kchat-skills/samples/sample_messages.yaml)),
compiles the active skill bundle through `SkillPackCompiler`, runs
the full hybrid pipeline against either `XLMRAdapter` or
`MockEncoderAdapter`, and writes per-case verdicts plus aggregate
latency.

## Latest Snapshot

- [`demo_results_2026-05-03T06-05-25Z.md`](./demo_results_2026-05-03T06-05-25Z.md)
  — most recent committed run. 51 scenarios across the jurisdiction
  + community overlay matrix.
- [`demo_results_2026-05-03T06-05-25Z.json`](./demo_results_2026-05-03T06-05-25Z.json)
  — machine-readable version of the same run.

## Archived Snapshots

Earlier snapshots are preserved under [`archive/`](./archive/) for
historical reference. They run the same scenarios with minor
latency variance and are kept so changes in headline numbers stay
traceable across the project's history.

## How to Regenerate

```bash
python tools/demo_guardrail.py
```

The script writes a new timestamped JSON + Markdown pair into this
directory.
