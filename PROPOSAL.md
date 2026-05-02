# KChat Guardrail Skills — Project Proposal

## Problem Statement

KChat is an end-to-end encrypted (E2EE) messaging app for **large
communities** — schools, workplaces, family groups, marketplaces, public
interest groups, and political / civic groups across many jurisdictions.
End-to-end encryption is non-negotiable for the trust model: only the people
in a conversation can read its contents.

This creates a **content safety gap**:

- Traditional server-side moderation is incompatible with E2EE. The server
  cannot read message content, so it cannot scan, classify, label, or
  remove harmful messages.
- The Messaging Layer Security protocol
  ([RFC 9420](https://www.rfc-editor.org/rfc/rfc9420.html)) provides group
  key agreement, forward secrecy, and post-compromise security — but it is
  deliberately silent on **application-layer policy**. Moderation, safety,
  abuse prevention, and community rules are explicitly outside the
  protocol's scope.
- Users in global communities face wildly different **legal**, **cultural**,
  and **community-specific** safety requirements. A single global rule set
  is either too lax to protect at-risk users (e.g. minors, people in
  abusive situations) or too restrictive to respect protected speech in
  permissive jurisdictions.

We need a content-safety mechanism that is *useful*, *adaptable*, and
*compatible with E2EE* — without recreating server-side moderation through a
back door.

## Proposed Solution

Run an **encoder classifier on-device** — the **XLM-R**
multilingual encoder — as a **local safety assistant**. The
classifier never sees anything the user is not already entitled to
see; it classifies content already decrypted on the user's device and
produces local warnings, labels, and suggestions. **No plaintext, no
embeddings, no hashes, no message identifiers, and no evidence are uploaded
to a server by default.**

Safety rules are delivered as **versioned, signed, transparent skill packs**
in three layers:

1. **Global Baseline** — universal taxonomy, severity rubric, privacy rules,
   and output schema. Always active.
2. **Jurisdiction Overlay** — country / region-specific rules. Activated
   only by user-selected region, group-declared jurisdiction, app-store
   region, or enterprise policy. Never by GPS, inferred nationality,
   ethnicity, or religion.
3. **Community Overlay** — group-specific rules set by the group admin and
   visible to all members.

The runtime active bundle is:

```
active_skill_bundle =
    global_baseline
  + jurisdiction overlays
  + community overlay
  + runtime context
```

## Non-Negotiable Privacy Rules

These rules are baked into the global baseline as the `privacy_rules` block.
Every skill pack — global, jurisdiction, community — inherits them, and
the skill pack compiler **rejects** any pack that attempts to weaken them.

```yaml
privacy_rules:
  - The model only analyzes content already decrypted and visible on this
    device to this user.
  - The model must never request more context than is provided in the
    structured input.
  - The model must never attempt to identify the sender, recipient, group
    membership, or social graph beyond what is in the structured input.
  - The model output must never contain the original message text, message
    fragments longer than 8 tokens, message IDs, or any direct quote that
    could re-identify the message off-device.
  - The model output must not contain hashes, fingerprints, embeddings, or
    cryptographic commitments to message content.
  - The model must not request, assume, or infer plaintext from other
    conversations, other devices, or other users.
  - The model must not produce content that would only be useful to a
    server-side moderator (for example, "report this message ID to backend
    X" or "send this label to a central queue").
  - The model must operate identically when offline; it must not depend on
    server-side completion of any safety decision.
```

These eight rules are the **product floor**. Any future skill, jurisdiction
overlay, or community overlay that conflicts with them is invalid by
construction.

## Design Goals

1. **Run on efficient encoder models.** Reference backend:
   **XLM-R** (multilingual, 384 hidden, exported once to a ~25 MB
   INT8-quantised ONNX checkpoint and loaded on-device through
   ONNX Runtime — no PyTorch / `transformers` runtime dependency).
   The compiled skill bundle still respects the **< 1800 instruction
   token** budget so it remains compatible with any future
   classifier backend, but the encoder itself runs as a deterministic argmax over
   a fixed bank of prototype embeddings — no temperature, no token
   generation. Models are expected to run on commodity phones with no
   remote inference.
2. **Deterministic taxonomy.** **16 global categories**, **6 severity
   levels** (0–5). Overlays may narrow categories or raise severity but
   may never invent new ones.
3. **Constrained JSON output.** A single JSON schema consumed directly by
   the UI — no free-form prose, no chain-of-thought leakage, no novel
   fields.
4. **Hybrid local pipeline.** Cheap deterministic detectors first; the
   encoder classifier does only the contextual reasoning that detectors
   cannot. End-to-end: normalize → deterministic detectors → encoder-based
   contextual classification → threshold policy → local JSON → local
   counters.
5. **Country / community adaptation via overlays, not global model
   changes.** The same encoder weights serve every jurisdiction; only the
   skill pack changes.
6. **Anti-misuse controls.** Transparency (active packs visible to user),
   narrowness (no vague categories), protected contexts (news / satire /
   education / counterspeech / quoted speech), required legal and cultural
   review, user rights (visibility, appeal, opt-out where lawful), and
   technical safeguards (signed packs, rollback, expiry).
7. **Regulatory alignment.** EU Digital Services Act (transparency,
   notice, appeal); NIST AI Risk Management Framework (validity,
   reliability, transparency, accountability); UNICEF / ITU child online
   protection.

## Scope — What This Project IS

- Skill definitions (YAML) for the global baseline, jurisdiction overlays,
  and community overlays.
- The risk taxonomy and severity rubric.
- Classifier-bundle instruction templates and the compiled-prompt format.
- Input / output contracts (JSON schemas) for the encoder classifier.
- Test suite templates and accuracy / latency metrics.
- A skill-pack compiler pipeline specification (authoring → review →
  compile → sign → distribute).
- Language-adaptation assets (lexicons, normalization, transliteration
  references) embedded in jurisdiction overlays.

## Scope — What This Project IS NOT

- **Not a messaging app or protocol implementation.** KChat itself,
  including MLS, lives in the [KChat](https://github.com/kennguy3n/slm-chat-demo)
  repository. This project produces inputs to that runtime.
- **Not a model training project.** Skills are *prompts and configs* for
  existing encoder classifiers (and any other backend that satisfies the
  ``EncoderAdapter`` Protocol). We do not fine-tune or pre-train models here.
- **Not server-side moderation.** No skill output is uploaded by default.
  No skill may demand that it be.
- **Not legal advice.** Jurisdiction packs are **product policy guidance**
  written and reviewed jointly with legal and cultural reviewers. They are
  not a substitute for case-specific legal counsel.

## Target Users

- **KChat Trust & Safety team** — owns the global baseline and approves
  jurisdiction / community overlays.
- **Policy authors** — write skills following the templates and review
  guidelines in this repo.
- **Legal and cultural reviewers** — sign off on jurisdiction overlays
  before they ship.
- **Community admins** — pick or compose a community overlay for their
  group; the choice is visible to all members.
- **Encoder classifier runtime engineers** — implement the on-device
  pipeline that consumes compiled skill packs, runs the classifier
  (XLM-R via ONNX Runtime today, swappable via the
  ``EncoderAdapter`` Protocol),
  and renders local warnings.

## Success Metrics

Drawn from the test-suite metrics in the underlying analysis (§15). All
metrics are evaluated against the test suites included in the skill packs;
runtime telemetry never replaces them.

| Metric                              | Target          |
| ----------------------------------- | --------------- |
| `child_safety_recall`               | ≥ 0.98          |
| `child_safety_precision`            | ≥ 0.90          |
| `privacy_leak_precision`            | ≥ 0.90          |
| `scam_recall`                       | ≥ 0.85          |
| `protected_speech_false_positive`   | ≤ 0.05          |
| `minority_language_false_positive`  | ≤ 0.07          |
| Latency (p95, on-device)            | ≤ 250 ms        |

A skill pack that does not meet these thresholds on its bundled tests
**fails** the compiler pipeline and is not signed or shipped.

## References

- IETF RFC 9420 — *The Messaging Layer Security (MLS) Protocol*. <https://www.rfc-editor.org/rfc/rfc9420.html>
- European Union — *Digital Services Act (Regulation (EU) 2022/2065)*. <https://eur-lex.europa.eu/eli/reg/2022/2065/oj>
- NIST — *AI Risk Management Framework (AI RMF 1.0)*. <https://www.nist.gov/itl/ai-risk-management-framework>
- UNICEF — *Child Online Protection guidelines*. <https://www.unicef.org/childrens-rights-online>
