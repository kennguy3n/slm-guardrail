"""P0-1 — Export boundary contract tests.

Privacy contract rule 5 (``kchat-skills/global/privacy_contract.yaml``):

    no embedding, model commitment, or hash of message content is
    permitted to leave the device.

These tests pin the contract end-to-end:

* The XLM-R adapter never attaches ``_embedding`` (or any other
  ``_``-prefixed key) to the dict returned from ``classify()``.
* The full :class:`GuardrailPipeline` never emits an ``_``-prefixed
  key on any threshold-policy branch (default SAFE pass-through,
  child-safety floor, protected-speech demotion, uncertainty
  handling, action re-derivation).
* The public output schema (``kchat.guardrail.output.v1``) declares
  ``additionalProperties: false`` AND does NOT declare a
  ``patternProperties`` block. The schema is the single source of
  truth for what may cross the device boundary; an explicit
  patternProperties admitting ``_*`` keys would silently re-open the
  embedding-exfiltration channel that this PR closed.
* The appeal-flow module never carries embedding-like fields on any
  of the data classes it exports.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
KCHAT_ROOT = REPO_ROOT / "kchat-skills"
COMPILER_ROOT = KCHAT_ROOT / "compiler"

sys.path.insert(0, str(COMPILER_ROOT))


# ---------------------------------------------------------------------------
# Schema-level contract: no patternProperties, additionalProperties is false.
# ---------------------------------------------------------------------------
def test_output_schema_has_no_pattern_properties():
    """The public output schema MUST NOT carry a ``patternProperties``
    block. An earlier iteration of this code admitted ``^_`` keys via
    ``patternProperties`` so the XLM-R adapter could smuggle a raw
    embedding back to ``chat-storage-search``; that channel violates
    privacy rule 5 and has been closed by this PR."""
    schema_path = KCHAT_ROOT / "global" / "output_schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert "patternProperties" not in schema, (
        "output_schema.json must not declare patternProperties — "
        "doing so re-opens the embedding-exfiltration channel"
    )
    assert schema.get("additionalProperties") is False, (
        "output_schema.json must keep additionalProperties: false so "
        "stray underscore-prefixed extras fail validation"
    )


def test_output_schema_text_does_not_mention_embedding():
    """Defence in depth — even the schema *text* must not reference
    ``_embedding`` or refer to embeddings on the public output
    boundary."""
    schema_text = (
        KCHAT_ROOT / "global" / "output_schema.json"
    ).read_text(encoding="utf-8")
    assert "_embedding" not in schema_text
    assert '"^_"' not in schema_text


# ---------------------------------------------------------------------------
# XLMRAdapter contract: no underscore keys on classify(), embedding is
# stashed on the instance.
# ---------------------------------------------------------------------------
def _stub_loaded_xlmr_adapter(monkeypatch, hidden: int = 384):
    """Mirror of ``tests/global/test_xlmr_adapter._stub_loaded_adapter``.

    Kept locally so this contract test does not couple to private
    test helpers from another module.
    """
    np = pytest.importorskip("numpy")
    pytest.importorskip("onnxruntime")
    pytest.importorskip("sentencepiece")

    from xlmr_adapter import (  # type: ignore[import-not-found]
        CATEGORY_PROTOTYPES,
        XLMRAdapter,
    )

    adapter = XLMRAdapter()
    adapter._tokenizer = object()
    adapter._session = object()
    adapter._input_names = ("input_ids", "attention_mask")
    adapter._prototype_embeddings = np.eye(
        len(CATEGORY_PROTOTYPES), hidden, dtype=np.float32
    )
    adapter._load_failed = False
    adapter.health_state = "healthy"

    def _fake_encode(self, text: str):
        return np.eye(1, hidden, dtype=np.float32)[0]

    monkeypatch.setattr(XLMRAdapter, "_encode", _fake_encode)
    return adapter


def _xlmr_input(text: str = "hello world", **local_signals: Any) -> dict[str, Any]:
    return {
        "message": {"text": text},
        "context": {},
        "local_signals": local_signals or {},
        "constraints": {
            "output_format": "json",
            "schema_id": "kchat.guardrail.output.v1",
        },
    }


def test_xlmr_classify_emits_no_underscore_keys(monkeypatch):
    """``XLMRAdapter.classify()`` MUST NOT attach ``_embedding`` or
    any other ``_``-prefixed key to the returned dict."""
    adapter = _stub_loaded_xlmr_adapter(monkeypatch)
    out = adapter.classify(_xlmr_input("hello"))
    leaked = [k for k in out if isinstance(k, str) and k.startswith("_")]
    assert not leaked, f"XLMRAdapter leaked underscore keys: {leaked}"


def test_xlmr_classify_stashes_embedding_on_instance(monkeypatch):
    """The embedding is exposed on the adapter instance, never on
    the output dict."""
    adapter = _stub_loaded_xlmr_adapter(monkeypatch)
    out = adapter.classify(_xlmr_input("hello"))
    assert "_embedding" not in out
    assert adapter.last_embedding is not None
    assert isinstance(adapter.last_embedding, list)
    assert len(adapter.last_embedding) == 384


def test_xlmr_degraded_fallback_omits_underscore_keys():
    """Degraded fallback path must not synthesise underscore keys
    either — privacy rule 5 applies on every code path."""
    from xlmr_adapter import (  # type: ignore[import-not-found]
        XLMRAdapter,
        degraded_fallback_output,
    )

    adapter = XLMRAdapter(
        model_path="/does/not/exist.onnx",
        tokenizer_path="/does/not/exist.spm",
    )
    out = adapter.classify(_xlmr_input())
    leaked = [k for k in out if isinstance(k, str) and k.startswith("_")]
    assert not leaked, leaked
    assert adapter.last_embedding is None

    # And the function-level helper carries the same invariant.
    direct = degraded_fallback_output()
    leaked_direct = [
        k for k in direct if isinstance(k, str) and k.startswith("_")
    ]
    assert not leaked_direct, leaked_direct


# ---------------------------------------------------------------------------
# Full pipeline contract: no underscore keys ever survive end-to-end.
# ---------------------------------------------------------------------------
def _underscore_attacking_adapter(
    *, severity: int = 0, category: int = 0, confidence: float = 0.0, **extra
):
    """Stub adapter that aggressively attaches underscore-prefixed
    extras the pipeline MUST strip. We attach two distinct keys
    (``_embedding`` and ``_secret``) to confirm the strip is total
    rather than name-specific."""
    raw = {
        "severity": severity,
        "category": category,
        "confidence": confidence,
        "actions": {
            "label_only": False,
            "warn": False,
            "strong_warn": False,
            "critical_intervention": False,
            "suggest_redact": False,
        },
        "reason_codes": [],
        "rationale_id": "test_v1",
        "_embedding": [1.0, 2.0, 3.0],
        "_secret": "must-not-leak",
    }
    raw.update(extra)

    class _Adapter:
        health_state = "healthy"

        def classify(self, _input: dict[str, Any]) -> dict[str, Any]:
            return {
                **raw,
                "_embedding": list(raw["_embedding"]),
                "_secret": raw["_secret"],
            }

    return _Adapter()


def _build_pipeline(adapter):
    from pipeline import (  # type: ignore[import-not-found]
        GuardrailPipeline,
        SkillBundle,
    )

    return GuardrailPipeline(
        skill_bundle=SkillBundle(),
        encoder_adapter=adapter,
    )


def _context() -> dict[str, Any]:
    return {
        "group_kind": "small_group",
        "group_age_mode": "mixed_age",
        "user_role": "member",
        "relationship_known": True,
        "locale": "en-US",
        "jurisdiction_id": None,
        "community_overlay_id": None,
        "is_offline": False,
    }


@pytest.mark.parametrize(
    "severity, category, confidence, reason_codes, label",
    [
        (0, 0, 0.0, [], "safe_passthrough"),
        (2, 1, 0.50, [], "child_safety_floor"),
        (
            2,
            6,
            0.80,
            ["NEWS_CONTEXT", "EDUCATION_CONTEXT", "QUOTED_SPEECH_CONTEXT"],
            "protected_speech_demotion",
        ),
        (3, 7, 0.10, [], "uncertainty_demotion"),
        (2, 7, 0.55, [], "action_rederivation"),
    ],
)
def test_pipeline_strips_underscore_keys_on_every_branch(
    severity, category, confidence, reason_codes, label
):
    """Every threshold-policy branch MUST strip underscore keys.

    The branches under test (matching
    :meth:`threshold_policy.ThresholdPolicy.apply`):

    * Default SAFE pass-through (rule 5)
    * Rule 1 — child-safety floor
    * Rule 2 — protected-speech demotion
    * Rule 3 — uncertainty handling
    * Rule 4 — non-SAFE action re-derivation
    """
    adapter = _underscore_attacking_adapter(
        severity=severity,
        category=category,
        confidence=confidence,
        reason_codes=reason_codes,
    )
    pipeline = _build_pipeline(adapter)
    out = pipeline.classify({"text": "hi"}, _context())
    leaked = [k for k in out if isinstance(k, str) and k.startswith("_")]
    assert not leaked, (
        f"branch {label!r} leaked underscore keys: {leaked} "
        f"(privacy rule 5 forbids embeddings/extras on output)"
    )


# ---------------------------------------------------------------------------
# Appeal flow output shapes.
# ---------------------------------------------------------------------------
def test_appeal_flow_module_text_has_no_embedding_fields():
    """Scan the appeal flow module for any ``embedding`` /
    ``_embedding`` reference. The appeal flow handles user-visible
    pack lifecycle events and MUST NOT carry embeddings on any of
    the data classes it exposes — the same privacy rule 5 boundary
    applies."""
    appeal_text = (
        KCHAT_ROOT / "compiler" / "appeal_flow.py"
    ).read_text(encoding="utf-8")
    assert "_embedding" not in appeal_text
    # Permit incidental occurrences of the word "embedding" in
    # comments / docstrings; the contract is that there must be no
    # *field* named like one.
    for needle in (
        "embedding:",
        "embedding =",
        '"embedding"',
        "'embedding'",
    ):
        assert needle not in appeal_text, (
            f"appeal_flow.py declares an embedding field ({needle!r}); "
            f"privacy rule 5 forbids embeddings on the public output boundary"
        )


# ---------------------------------------------------------------------------
# Threshold policy: no legacy ``_carry_internal_extras`` left in source.
# ---------------------------------------------------------------------------
def test_threshold_policy_has_no_carry_internal_extras():
    """The legacy ``_carry_internal_extras`` helper used to forward
    every ``_``-prefixed key through every fresh-dict branch of
    :meth:`ThresholdPolicy.apply`. P0-1 removed it in favour of
    :func:`_forward_health_signal` which only forwards the schema-
    level ``model_health`` enum."""
    src = (
        KCHAT_ROOT / "compiler" / "threshold_policy.py"
    ).read_text(encoding="utf-8")
    assert "_carry_internal_extras" not in src, (
        "_carry_internal_extras must not be present — it was the "
        "function that smuggled _embedding through the policy branches"
    )
