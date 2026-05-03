"""Encoder classifier runtime adapter — the boundary between the
hybrid local pipeline and any encoder-classifier backend.

Reference backend: **XLM-R** (see :mod:`xlmr_adapter`), an ONNX
INT8 export of the multilingual XLM-R encoder loaded through
:mod:`onnxruntime`. The Protocol itself is deliberately
backend-agnostic: any implementation that accepts a
``kchat.guardrail.local_signal.v1`` instance and returns a
``kchat.guardrail.output.v1`` instance is a valid adapter — the
pipeline never imports a specific model runtime.

Spec references:

* PHASES.md Phase 3 — "Define the runtime adapter interface — the
  boundary between the pipeline and any encoder-classifier backend
  (so we can swap backends without changing skill packs)."
* ARCHITECTURE.md "Hybrid Local Pipeline" step 4 —
  "Encoder-based contextual classification (XLM-R)".

This module ships:

* :class:`EncoderAdapter` — the :mod:`typing.Protocol` defining the
  contract. Any encoder-classifier backend whose ``classify`` method
  matches the shape is a valid implementation.
* :class:`MockEncoderAdapter` — a deterministic reference adapter. It
  returns fixed outputs keyed off the deterministic-local-detector
  signals so the full pipeline is testable end-to-end without a real
  model.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


# Taxonomy category ids used by the mock adapter. Keep in sync with
# ``kchat-skills/global/taxonomy.yaml``.
CAT_SAFE = 0
CAT_CHILD_SAFETY = 1
CAT_SELF_HARM = 2
CAT_VIOLENCE_THREAT = 3
CAT_EXTREMISM = 4
CAT_HARASSMENT = 5
CAT_HATE = 6
CAT_SCAM_FRAUD = 7
CAT_MALWARE_LINK = 8
CAT_PRIVATE_DATA = 9
CAT_SEXUAL_ADULT = 10
CAT_DRUGS_WEAPONS = 11
CAT_ILLEGAL_GOODS = 12
CAT_MISINFORMATION_HEALTH = 13
CAT_MISINFORMATION_CIVIC = 14
CAT_COMMUNITY_RULE = 15


def _zero_actions() -> dict[str, bool]:
    return {
        "label_only": False,
        "warn": False,
        "strong_warn": False,
        "critical_intervention": False,
        "suggest_redact": False,
    }


def _safe_output() -> dict[str, Any]:
    return {
        "severity": 0,
        "category": CAT_SAFE,
        "confidence": 0.05,
        "actions": _zero_actions(),
        "reason_codes": [],
        "rationale_id": "safe_benign_v1",
    }


def _safe_output_with_context(context_hints: list[str]) -> dict[str, Any]:
    """Like :func:`_safe_output`, but echoes the protected-speech
    context hints back into ``reason_codes`` for reviewer traceability.

    The output category is already SAFE so the threshold policy's
    protected-speech demotion rule is a no-op; the hints are kept on
    the record purely so reviewers can see *why* a borderline message
    was treated as SAFE (e.g. it carried ``NEWS_CONTEXT``).
    """
    out = _safe_output()
    if context_hints:
        out["reason_codes"] = list(dict.fromkeys(context_hints))
    return out


@runtime_checkable
class EncoderAdapter(Protocol):
    """Adapter contract implemented by any encoder-classifier backend.

    Implementations must:

    * Accept a dict matching ``kchat.guardrail.local_signal.v1``
      (validated before the call — the adapter may assume shape).
    * Return a dict matching ``kchat.guardrail.output.v1``. The
      pipeline validates the return shape; invalid outputs are
      rejected and re-coerced to SAFE.
    * Be deterministic — identical input must produce identical
      output. For encoder backends this is satisfied by argmax over
      fixed prototype embeddings.
    * Run with no network access. The pipeline enforces this at the
      step-3 packaging boundary; adapters are expected not to reach
      out.

    Optional return-shape extras (none of which the Protocol's
    ``classify(input) -> dict`` signature mentions explicitly):

    * ``_embedding``: ``list[float]`` — the raw encoder embedding
      (e.g. the 384-dim mean-pooled XLM-R vector from
      :class:`xlmr_adapter.XLMRAdapter`). Underscore-prefixed keys
      are not part of ``kchat.guardrail.output.v1`` proper; the
      schema admits them via ``patternProperties: {"^_": {}}`` so
      cross-pipeline caches (notably ``chat-storage-search``) can
      reuse a message's encoder pass without recomputing it.
      Adapters that do not have a meaningful embedding (e.g.
      :class:`MockEncoderAdapter`) MUST omit the key rather than
      emitting a zero-vector placeholder.
    """

    def classify(self, input: dict[str, Any]) -> dict[str, Any]:
        """Classify a packed local-signal input and return an output dict."""
        ...


class MockEncoderAdapter:
    """Deterministic reference adapter for pipeline tests.

    Used in tests, demos, and any environment that does not have the
    XLM-R encoder weights loaded. Maps deterministic-detector
    signals to category outputs:

    * ``url_risk > 0.8`` → SCAM_FRAUD (7).
    * any ``pii_patterns_hit`` → PRIVATE_DATA (9).
    * any ``scam_patterns_hit`` → SCAM_FRAUD (7).
    * ``lexicon_hits`` → highest-weight lexicon's ``category``.
    * ``media_descriptors[*].nsfw_score > 0.7`` → SEXUAL_ADULT (10).
    * otherwise SAFE (0).

    Priority order: CHILD_SAFETY > PRIVATE_DATA > SCAM_FRAUD >
    lexicon_hits > media NSFW > SAFE. CHILD_SAFETY is derived from a
    lexicon_hit on category 1 — the pipeline / threshold policy
    enforces the severity-5 floor downstream.

    Confidence is derived deterministically from the strongest signal.
    The adapter never invents reason codes not implied by the input
    signals.
    """

    def classify(self, input: dict[str, Any]) -> dict[str, Any]:
        signals = input.get("local_signals", {}) or {}

        lexicon_hits = signals.get("lexicon_hits") or []
        pii_hits = signals.get("pii_patterns_hit") or []
        scam_hits = signals.get("scam_patterns_hit") or []
        url_risk = float(signals.get("url_risk") or 0.0)
        media = signals.get("media_descriptors") or []
        context_hints = list(signals.get("context_hints") or [])

        # CHILD_SAFETY — any lexicon hit on category 1. Wins over every
        # other signal, including protected-speech context hints — the
        # threshold policy preserves the floor above all else.
        child_lex = [h for h in lexicon_hits if h.get("category") == CAT_CHILD_SAFETY]
        if child_lex:
            weight = max(float(h.get("weight", 0.5)) for h in child_lex)
            confidence = max(0.45, min(0.99, weight))
            return {
                "severity": 5,
                "category": CAT_CHILD_SAFETY,
                "confidence": confidence,
                "actions": {
                    **_zero_actions(),
                    "critical_intervention": True,
                },
                "reason_codes": ["LEXICON_HIT", "CHILD_SAFETY_FLOOR"],
                "rationale_id": "child_safety_floor_v1",
                "resource_link_id": "child_safety_resources_v1",
            }

        # NOTE: deterministic-signal branches below (PII / SCAM_FRAUD /
        # LEXICON / NSFW) emit their reason codes *without* the
        # protected-speech ``context_hints`` from the pipeline. This
        # is intentional: the threshold policy demotes any non-SAFE /
        # non-CHILD_SAFETY verdict carrying a protected-speech reason
        # code to SAFE, and we do **not** want that demotion to
        # silence a phishing URL detected in a school group chat or a
        # PII leak in a journalism community. Protected-speech
        # demotion only applies on the embedding-head path (mock has
        # no embedding head; the trained adapter does — see
        # ``xlmr_adapter.py``).
        #
        # The ``context_hints`` list is still consumed below: it is
        # used by the ``_safe_output()`` fallback at the bottom of the
        # method via the existing pipeline -> threshold-policy flow,
        # and is also visible to the threshold policy via the pipeline
        # ``local_signals`` view (it is *not* re-emitted here).

        # PRIVATE_DATA — any PII pattern. Deterministic — never demoted.
        if pii_hits:
            confidence = min(0.95, 0.55 + 0.1 * len(pii_hits))
            return {
                "severity": 3,
                "category": CAT_PRIVATE_DATA,
                "confidence": confidence,
                "actions": {
                    **_zero_actions(),
                    "warn": True,
                    "suggest_redact": True,
                },
                "reason_codes": ["PRIVATE_DATA_PATTERN"],
                "rationale_id": "private_data_pii_v1",
            }

        # SCAM_FRAUD — high URL risk or scam patterns. Deterministic.
        if url_risk > 0.8 or scam_hits:
            confidence = max(url_risk, 0.55 + 0.1 * len(scam_hits))
            confidence = min(0.95, confidence)
            reason_codes: list[str] = []
            if url_risk > 0.8:
                reason_codes.append("URL_RISK")
            if scam_hits:
                reason_codes.append("SCAM_PATTERN")
            return {
                "severity": 3,
                "category": CAT_SCAM_FRAUD,
                "confidence": confidence,
                "actions": {**_zero_actions(), "warn": True},
                "reason_codes": reason_codes,
                "rationale_id": "scam_credential_phish_v1",
            }

        # Lexicon-only hits — pick the highest-weight hit. Deterministic.
        if lexicon_hits:
            top = max(lexicon_hits, key=lambda h: float(h.get("weight", 0.0)))
            category = int(top.get("category", CAT_SAFE))
            weight = float(top.get("weight", 0.5))
            if category == CAT_SAFE:
                return _safe_output_with_context(context_hints)
            confidence = max(0.45, min(0.95, weight))
            return {
                "severity": 3,
                "category": category,
                "confidence": confidence,
                "actions": {**_zero_actions(), "warn": True},
                "reason_codes": ["LEXICON_HIT"],
                "rationale_id": f"lexicon_category_{category}_v1",
            }

        # Media NSFW. Deterministic — never demoted.
        for m in media:
            nsfw = m.get("nsfw_score")
            if nsfw is not None and float(nsfw) > 0.7:
                confidence = min(0.95, float(nsfw))
                return {
                    "severity": 3,
                    "category": CAT_SEXUAL_ADULT,
                    "confidence": confidence,
                    "actions": {**_zero_actions(), "warn": True},
                    "reason_codes": [],
                    "rationale_id": "sexual_adult_media_v1",
                }

        return _safe_output_with_context(context_hints)


__all__ = ["EncoderAdapter", "MockEncoderAdapter"]
