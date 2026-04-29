"""SLM runtime adapter — the boundary between the hybrid local pipeline
and any tiny-SLM backend (ONNX, TFLite, llama.cpp, Core ML, etc.).

Spec references:

* PHASES.md Phase 3 — "Define the SLM runtime adapter interface — the
  boundary between the pipeline and any tiny-SLM backend (so we can
  swap backends without changing skill packs)."
* ARCHITECTURE.md "Hybrid Local Pipeline" step 4 — "SLM contextual
  classification (tiny SLM, temperature 0.0)".

The adapter is deliberately backend-agnostic: any implementation that
accepts a ``kchat.guardrail.local_signal.v1`` instance and returns a
``kchat.guardrail.output.v1`` instance is a valid adapter. The
pipeline never imports a specific model runtime.

This module ships:

* :class:`SLMAdapter` — the :mod:`typing.Protocol` defining the
  contract.
* :class:`MockSLMAdapter` — a deterministic reference adapter. It
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


@runtime_checkable
class SLMAdapter(Protocol):
    """Adapter contract implemented by any tiny-SLM backend.

    Implementations must:

    * Accept a dict matching ``kchat.guardrail.local_signal.v1``
      (validated before the call — the adapter may assume shape).
    * Return a dict matching ``kchat.guardrail.output.v1``. The
      pipeline validates the return shape; invalid outputs are
      rejected and re-coerced to SAFE.
    * Be deterministic at temperature 0.0 — identical input must
      produce identical output.
    * Run with no network access. The pipeline enforces this at the
      step-3 packaging boundary; adapters are expected not to reach
      out.
    """

    def classify(self, input: dict[str, Any]) -> dict[str, Any]:
        """Classify a packed local-signal input and return an output dict."""
        ...


class MockSLMAdapter:
    """Deterministic reference adapter for pipeline tests.

    Maps deterministic-detector signals to category outputs:

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

        # CHILD_SAFETY — any lexicon hit on category 1.
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

        # PRIVATE_DATA — any PII pattern.
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

        # SCAM_FRAUD — high URL risk or scam patterns.
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

        # Lexicon-only hits — pick the highest-weight hit.
        if lexicon_hits:
            top = max(lexicon_hits, key=lambda h: float(h.get("weight", 0.0)))
            category = int(top.get("category", CAT_SAFE))
            weight = float(top.get("weight", 0.5))
            if category == CAT_SAFE:
                return _safe_output()
            confidence = max(0.45, min(0.95, weight))
            return {
                "severity": 3,
                "category": category,
                "confidence": confidence,
                "actions": {**_zero_actions(), "warn": True},
                "reason_codes": ["LEXICON_HIT"],
                "rationale_id": f"lexicon_category_{category}_v1",
            }

        # Media NSFW.
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

        return _safe_output()


__all__ = ["SLMAdapter", "MockSLMAdapter"]
