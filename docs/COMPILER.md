# Skill-Pack Compiler

This document covers the Phase 4+ skill-pack compiler:
composing global + jurisdiction + community overlays, signing
(ed25519 skill passports), bias auditing, pack lifecycle,
performance benchmarking, the community appeal flow, and
regulatory alignment. For the high-level project pitch see the
[README](../README.md).

## Compiling a skill pack

The Phase 4 compiler resolves the global baseline plus optional
jurisdiction and community overlays into a single compiled prompt
(< 1800 instruction tokens) configuring the encoder classifier's
allowed actions / reason codes / counters:

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

## Signing workflow

Every compiled bundle ships with an ed25519-signed *skill passport*
(see [`compiler/skill_passport.py`](../kchat-skills/compiler/skill_passport.py)
and [`compiler/skill_passport.schema.json`](../kchat-skills/compiler/skill_passport.schema.json)).
The passport carries identity (`skill_id`, `skill_version`, `parent`),
provenance (`authored_by`, `reviewed_by.legal/cultural/trust_and_safety`),
model compatibility (`model_id` / `model_min_version` /
`max_instruction_tokens` / `max_output_tokens`), an `expires_on`
date (max 18 months from issuance), the per-pack `test_results`
recorded by [`metric_validator`](../kchat-skills/compiler/metric_validator.py),
and a base64 ed25519 `signature` covering the deterministic JSON
serialisation of every other field.

A pack is rejected by the runtime if any of the following is true:
the signature does not verify against the compiler's public key;
`expires_on` is in the past or more than 18 months in the future;
the runtime model is not listed in `model_compatibility`; or any of the
[`anti_misuse`](../kchat-skills/compiler/anti_misuse.py) rules fail
(invented categories, overlay redefining `privacy_rules`, jurisdiction
pack missing `legal_review` / `cultural_review` signers, community
pack missing `trust_and_safety` signer, severity floors ≥ 4 without
protected-speech `allowed_contexts`, or lexicons without provenance).

## Bias Auditing

The Phase 6 bias auditor at
[`kchat-skills/compiler/bias_audit.py`](../kchat-skills/compiler/bias_audit.py)
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

## Pack Lifecycle

The Phase 6 pack store at
[`kchat-skills/compiler/pack_lifecycle.py`](../kchat-skills/compiler/pack_lifecycle.py)
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

## Performance Benchmarking

The Phase 6 benchmark harness at
[`kchat-skills/compiler/benchmark.py`](../kchat-skills/compiler/benchmark.py)
wraps `GuardrailPipeline` plus an ``EncoderAdapter`` (typically
`MockEncoderAdapter` for deterministic regression tests, or
`XLMRAdapter` for real encoder timings) into a
deterministic measurement loop. `PipelineBenchmark.run(cases,
iterations=100)` records wall-clock latency per iteration using
`time.perf_counter` and returns a `BenchmarkReport` with p50 / p95 /
p99 / mean / max / min / per-case-mean in milliseconds. A report
`passed` iff the aggregate p95 is ≤ `P95_LATENCY_TARGET_MS = 250` —
the ARCHITECTURE.md “Performance envelope” target. The contract
test at
[`kchat-skills/tests/global/test_benchmark.py`](../kchat-skills/tests/global/test_benchmark.py)
parametrises across all 16 taxonomy categories and across the full
59-country set to verify latency does not regress as packs grow.

## Appeal Flow

The Phase 6 community-feedback spec at
[`kchat-skills/compiler/appeal_flow.py`](../kchat-skills/compiler/appeal_flow.py)
implements `AppealCase`, `AppealAggregator` and `AppealReport`.
`AppealCase` is privacy-contract-safe by construction — there is no
text / message / hash / embedding field; only a closed-enum
`user_context` in `{disagree_category, disagree_severity,
false_positive, missing_context}` and a stable `rationale_id`.
`AppealAggregator.aggregate(skill_id, window_days=30)` returns a
report whose `recommendation` is one of `{no_action,
review_suggested, urgent_review}`. Rules: any CHILD_SAFETY (cat 1)
appeal short-circuits to `urgent_review`; per-category rate ≥ 15%
(with at least 5 appeals) promotes to `urgent_review`; ≥ 5% promotes
to `review_suggested`.

## Regulatory Alignment

The Phase 6 regulatory documentation under
[`kchat-skills/docs/regulatory/`](../kchat-skills/docs/regulatory/)
maps each obligation of the EU Digital Services Act, NIST AI Risk
Management Framework 1.0, and UNICEF / ITU Child Online Protection
Guidelines to the concrete artefact(s) that satisfy it:

- [`eu_dsa_alignment.md`](../kchat-skills/docs/regulatory/eu_dsa_alignment.md)
  — transparency (Art. 14, 17), notice-and-action (Art. 16, 20),
  risk assessment (Art. 34, 35), protection of minors (Art. 28),
  transparency reporting (Art. 24).
- [`nist_ai_rmf_alignment.md`](../kchat-skills/docs/regulatory/nist_ai_rmf_alignment.md)
  — all four core functions (Govern, Map, Measure, Manage) plus the
  seven trustworthy-AI characteristics.
- [`unicef_itu_cop_alignment.md`](../kchat-skills/docs/regulatory/unicef_itu_cop_alignment.md)
  — child-rights due diligence plus a per-jurisdiction statutory
  table for all 59 country packs.
- [`README.md`](../kchat-skills/docs/regulatory/README.md) — index
  linking to all three.

