"""Tests for the 7-step hybrid local pipeline.

Module under test: ``kchat-skills/compiler/pipeline.py``. See
ARCHITECTURE.md "Hybrid Local Pipeline" (lines 252-281) and PHASES.md
Phase 3.
"""
from __future__ import annotations

from typing import Any

import jsonschema
import pytest

from counters import (  # type: ignore[import-not-found]
    CounterStore,
    InMemoryKeystore,
)
from pipeline import (  # type: ignore[import-not-found]
    GuardrailPipeline,
    LexiconEntry,
    SkillBundle,
    derive_context_hints,
    detect_pii,
    detect_scam,
    extract_media_descriptors,
    match_lexicons,
    normalize_text,
    pack_signals,
    score_url_risk,
)
from encoder_adapter import MockEncoderAdapter  # type: ignore[import-not-found]
from threshold_policy import ThresholdPolicy  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Step 1 — normalize_text
# ---------------------------------------------------------------------------
class TestNormalizeText:
    def test_nfkc_applied(self):
        # 'ｈｅｌｌｏ' (fullwidth) → 'hello'
        assert normalize_text("\uff48\uff45\uff4c\uff4c\uff4f") == "hello"

    def test_case_fold_applied(self):
        assert normalize_text("HELLO") == "hello"

    def test_homoglyph_map_applied(self):
        # Cyrillic 'а' → Latin 'a'.
        assert "a" in normalize_text("\u0430bc")

    def test_disable_nfkc(self):
        text = "\uff48\uff45\uff4c\uff4c\uff4f"
        out = normalize_text(text, nfkc=False)
        assert out == text  # no transformation

    def test_disable_case_fold(self):
        out = normalize_text("HELLO", case_fold=False)
        assert out == "HELLO"

    def test_empty_string(self):
        assert normalize_text("") == ""


# ---------------------------------------------------------------------------
# Step 2 — deterministic detectors
# ---------------------------------------------------------------------------
class TestDeterministicDetectors:
    def test_url_risk_empty(self):
        assert score_url_risk("hello world") == 0.0

    def test_url_risk_plain_url(self):
        assert 0.0 < score_url_risk("click https://example.com") <= 1.0

    def test_url_risk_high_tld(self):
        score = score_url_risk("see http://bad.zip/payload")
        assert score >= 0.85

    def test_url_risk_keyword(self):
        score = score_url_risk("https://example.com/login")
        assert score >= 0.85

    def test_detect_pii_email(self):
        assert "EMAIL" in detect_pii("contact me at foo@bar.com")

    def test_detect_pii_phone(self):
        assert "PHONE" in detect_pii("call 415-555-1234 please")

    def test_detect_pii_none(self):
        assert detect_pii("hello world") == []

    def test_detect_scam_giveaway(self):
        hits = detect_scam("congratulations you won a free gift".casefold())
        assert "FAKE_GIVEAWAY" in hits

    def test_detect_scam_credential(self):
        hits = detect_scam("please verify your password now".casefold())
        assert "CREDENTIAL_HARVEST" in hits

    def test_match_lexicons_hit(self):
        lex = [LexiconEntry("lex_v1", 6, ["forbidden_term"], 0.7)]
        hits = match_lexicons("this contains forbidden_term here", lex)
        assert len(hits) == 1
        assert hits[0] == {
            "lexicon_id": "lex_v1",
            "category": 6,
            "weight": 0.7,
        }

    def test_match_lexicons_miss(self):
        lex = [LexiconEntry("lex_v1", 6, ["x"], 0.7)]
        assert match_lexicons("nothing here", lex) == []

    def test_extract_media_descriptors_clamps(self):
        out = extract_media_descriptors(
            [{"kind": "image", "nsfw_score": 1.5, "face_count": -2}]
        )
        assert out[0]["nsfw_score"] == 1.0
        assert out[0]["face_count"] == 0

    def test_extract_media_descriptors_none(self):
        assert extract_media_descriptors(None) == []


# ---------------------------------------------------------------------------
# Step 3 — pack_signals produces valid local_signal instances
# ---------------------------------------------------------------------------
def _message() -> dict[str, Any]:
    return {
        "text": "hello",
        "lang_hint": "en",
        "has_attachment": False,
        "attachment_kinds": [],
        "quoted_from_user": False,
        "is_outbound": False,
    }


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


def _empty_local_signals() -> dict[str, Any]:
    return {
        "url_risk": 0.0,
        "pii_patterns_hit": [],
        "scam_patterns_hit": [],
        "lexicon_hits": [],
        "media_descriptors": [],
        "context_hints": [],
    }


class TestPackSignals:
    def test_pack_signals_matches_local_signal_schema(self, local_signal_schema):
        packed = pack_signals(
            message=_message(),
            context=_context(),
            local_signals=_empty_local_signals(),
        )
        jsonschema.validate(instance=packed, schema=local_signal_schema)

    def test_pack_signals_pins_constraints(self):
        packed = pack_signals(
            message=_message(),
            context=_context(),
            local_signals=_empty_local_signals(),
        )
        assert packed["constraints"]["temperature"] == 0.0
        assert packed["constraints"]["max_output_tokens"] == 600
        assert packed["constraints"]["output_format"] == "json"
        assert (
            packed["constraints"]["schema_id"] == "kchat.guardrail.output.v1"
        )


# ---------------------------------------------------------------------------
# End-to-end pipeline
# ---------------------------------------------------------------------------
def _benign_message() -> dict[str, Any]:
    return {"text": "see you at dinner"}


def _pii_message() -> dict[str, Any]:
    return {"text": "my email is alice@example.com"}


def _scam_message() -> dict[str, Any]:
    return {"text": "congratulations you won a free prize, claim now"}


def _url_risk_message() -> dict[str, Any]:
    return {"text": "verify your account at https://secure-login.zip/login"}


@pytest.fixture
def pipeline() -> GuardrailPipeline:
    return GuardrailPipeline(
        skill_bundle=SkillBundle(),
        encoder_adapter=MockEncoderAdapter(),
        threshold_policy=ThresholdPolicy(),
    )


class TestPipelineEndToEnd:
    def test_benign_message_is_safe(self, pipeline, output_schema):
        out = pipeline.classify(_benign_message(), _context())
        jsonschema.validate(instance=out, schema=output_schema)
        assert out["category"] == 0
        assert out["severity"] == 0

    def test_pii_message_triggers_private_data(self, pipeline, output_schema):
        out = pipeline.classify(_pii_message(), _context())
        jsonschema.validate(instance=out, schema=output_schema)
        assert out["category"] == 9  # PRIVATE_DATA
        assert out["actions"]["suggest_redact"] is True

    def test_scam_message_triggers_scam_fraud(self, pipeline, output_schema):
        out = pipeline.classify(_scam_message(), _context())
        jsonschema.validate(instance=out, schema=output_schema)
        assert out["category"] == 7  # SCAM_FRAUD

    def test_url_risk_triggers_scam_fraud(self, pipeline, output_schema):
        out = pipeline.classify(_url_risk_message(), _context())
        jsonschema.validate(instance=out, schema=output_schema)
        assert out["category"] == 7

    def test_child_safety_lexicon_pins_severity_5(self, output_schema):
        bundle = SkillBundle(
            lexicons=[
                LexiconEntry("cs_v1", 1, ["badtoken"], weight=0.95)
            ]
        )
        p = GuardrailPipeline(
            skill_bundle=bundle, encoder_adapter=MockEncoderAdapter()
        )
        out = p.classify({"text": "this has badtoken inside"}, _context())
        jsonschema.validate(instance=out, schema=output_schema)
        assert out["category"] == 1
        assert out["severity"] == 5
        assert out["actions"]["critical_intervention"] is True


# ---------------------------------------------------------------------------
# Step 5 — threshold policy is applied
# ---------------------------------------------------------------------------
class _AlwaysLowConfidenceAdapter:
    def classify(self, input: dict[str, Any]) -> dict[str, Any]:
        return {
            "severity": 3,
            "category": 7,
            "confidence": 0.10,
            "actions": {
                "label_only": False,
                "warn": True,
                "strong_warn": False,
                "critical_intervention": False,
                "suggest_redact": False,
            },
            "reason_codes": ["SCAM_PATTERN"],
            "rationale_id": "test_v1",
        }


def test_pipeline_coerces_low_confidence_to_safe():
    p = GuardrailPipeline(
        skill_bundle=SkillBundle(),
        encoder_adapter=_AlwaysLowConfidenceAdapter(),
    )
    out = p.classify({"text": "hi"}, _context())
    assert out["category"] == 0
    assert all(v is False for v in out["actions"].values())


# ---------------------------------------------------------------------------
# Step 7 — counter updates
# ---------------------------------------------------------------------------
class _CounterEmittingAdapter:
    def classify(self, input: dict[str, Any]) -> dict[str, Any]:
        return {
            "severity": 3,
            "category": 7,
            "confidence": 0.70,
            "actions": {
                "label_only": False,
                "warn": True,
                "strong_warn": False,
                "critical_intervention": False,
                "suggest_redact": False,
            },
            "reason_codes": ["SCAM_PATTERN"],
            "rationale_id": "scam_v1",
            "counter_updates": [
                {"counter_id": "group_scam_links_24h", "delta": 1}
            ],
        }


def test_pipeline_applies_counter_updates_to_store():
    store = CounterStore(keystore=InMemoryKeystore(b"\x00" * 32))
    p = GuardrailPipeline(
        skill_bundle=SkillBundle(),
        encoder_adapter=_CounterEmittingAdapter(),
        counter_store=store,
    )
    out = p.classify({"text": "hi"}, _context(), group_id="group-1")
    assert out["counter_updates"]
    assert store.get_count("group-1", "group_scam_links_24h") == 1


def test_pipeline_skips_counter_updates_without_group_id():
    store = CounterStore(keystore=InMemoryKeystore(b"\x00" * 32))
    p = GuardrailPipeline(
        skill_bundle=SkillBundle(),
        encoder_adapter=_CounterEmittingAdapter(),
        counter_store=store,
    )
    p.classify({"text": "hi"}, _context())  # no group_id
    assert store.get_count("group-1", "group_scam_links_24h") == 0


# ---------------------------------------------------------------------------
# Classifier adapter receives original text (not normalized form)
# ---------------------------------------------------------------------------
class _RecordingAdapter:
    def __init__(self) -> None:
        self.last_input: dict[str, Any] | None = None

    def classify(self, input: dict[str, Any]) -> dict[str, Any]:
        self.last_input = input
        return {
            "severity": 0,
            "category": 0,
            "confidence": 0.05,
            "actions": {
                "label_only": False,
                "warn": False,
                "strong_warn": False,
                "critical_intervention": False,
                "suggest_redact": False,
            },
            "reason_codes": [],
            "rationale_id": "safe_benign_v1",
        }


def test_encoder_receives_original_text_not_normalized():
    adapter = _RecordingAdapter()
    p = GuardrailPipeline(
        skill_bundle=SkillBundle(), encoder_adapter=adapter
    )
    raw = "HELLO\uff11\uff12\uff13"
    p.classify({"text": raw}, _context())
    assert adapter.last_input is not None
    assert adapter.last_input["message"]["text"] == raw


# ---------------------------------------------------------------------------
# Bundle jurisdiction / community ids flow into context
# ---------------------------------------------------------------------------
def test_bundle_jurisdiction_id_flows_into_packed_context():
    adapter = _RecordingAdapter()
    bundle = SkillBundle(
        jurisdiction_id="kchat.jurisdiction.archetype-strict-marketplace.guardrail.v1",
        community_overlay_id="kchat.community.workplace.guardrail.v1",
    )
    p = GuardrailPipeline(skill_bundle=bundle, encoder_adapter=adapter)
    p.classify({"text": "hi"}, _context())
    assert adapter.last_input is not None
    ctx = adapter.last_input["context"]
    assert ctx["jurisdiction_id"] == (
        "kchat.jurisdiction.archetype-strict-marketplace.guardrail.v1"
    )
    assert ctx["community_overlay_id"] == "kchat.community.workplace.guardrail.v1"


# ---------------------------------------------------------------------------
# Step 2 (auxiliary) — derive_context_hints
# ---------------------------------------------------------------------------
class TestDeriveContextHints:
    @staticmethod
    def _msg(quoted: bool = False) -> dict[str, Any]:
        return {
            "text": "x",
            "lang_hint": "en",
            "has_attachment": False,
            "attachment_kinds": [],
            "quoted_from_user": quoted,
            "is_outbound": False,
        }

    @staticmethod
    def _ctx(overlay: str | None) -> dict[str, Any]:
        return {
            "group_kind": "small_group",
            "group_age_mode": "mixed_age",
            "user_role": "member",
            "relationship_known": True,
            "locale": "en-US",
            "jurisdiction_id": None,
            "community_overlay_id": overlay,
            "is_offline": False,
        }

    def test_journalism_overlay_emits_news_context(self):
        hints = derive_context_hints(
            message=self._msg(quoted=False),
            context=self._ctx("kchat.community.journalism.guardrail.v1"),
        )
        assert "NEWS_CONTEXT" in hints

    def test_quoted_from_user_emits_quoted_speech_context(self):
        hints = derive_context_hints(
            message=self._msg(quoted=True),
            context=self._ctx(None),
        )
        assert "QUOTED_SPEECH_CONTEXT" in hints

    def test_education_overlay_emits_education_context(self):
        hints = derive_context_hints(
            message=self._msg(quoted=False),
            context=self._ctx("kchat.community.education_higher.guardrail.v1"),
        )
        assert "EDUCATION_CONTEXT" in hints

    def test_lgbtq_support_overlay_emits_counterspeech_context(self):
        hints = derive_context_hints(
            message=self._msg(quoted=False),
            context=self._ctx("kchat.community.lgbtq_support.guardrail.v1"),
        )
        assert "COUNTERSPEECH_CONTEXT" in hints

    def test_journalism_plus_quoted_emits_both(self):
        hints = derive_context_hints(
            message=self._msg(quoted=True),
            context=self._ctx("kchat.community.journalism.guardrail.v1"),
        )
        assert "NEWS_CONTEXT" in hints
        assert "QUOTED_SPEECH_CONTEXT" in hints

    def test_unrelated_overlay_emits_nothing(self):
        hints = derive_context_hints(
            message=self._msg(quoted=False),
            context=self._ctx("kchat.community.gaming.guardrail.v1"),
        )
        assert hints == []

    def test_no_overlay_no_quoted_emits_nothing(self):
        hints = derive_context_hints(
            message=self._msg(quoted=False),
            context=self._ctx(None),
        )
        assert hints == []


# ---------------------------------------------------------------------------
# Pipeline-level: protected-speech context + non-SAFE encoder verdict
# round-trips to SAFE via threshold demotion.
# ---------------------------------------------------------------------------
class _AlwaysViolenceAdapter:
    """Adapter that mimics an encoder that classifies anything as
    VIOLENCE_THREAT (3) at confidence 0.50 — used to exercise the
    protected-speech demotion path end-to-end."""

    def classify(self, input: dict[str, Any]) -> dict[str, Any]:
        signals = input.get("local_signals") or {}
        context_hints = list(signals.get("context_hints") or [])
        return {
            "severity": 2,
            "category": 3,
            "confidence": 0.50,
            "actions": {
                "label_only": True,
                "warn": False,
                "strong_warn": False,
                "critical_intervention": False,
                "suggest_redact": False,
            },
            "reason_codes": list(context_hints),
            "rationale_id": "test_violence_v1",
        }


def test_pipeline_news_quote_demotes_violence_to_safe():
    """End-to-end: a quoted news message ends up SAFE even when the
    encoder labels it VIOLENCE_THREAT."""
    p = GuardrailPipeline(
        skill_bundle=SkillBundle(
            community_overlay_id="kchat.community.journalism.guardrail.v1"
        ),
        encoder_adapter=_AlwaysViolenceAdapter(),
    )
    msg = {"text": "Reuters reports an attack.", "quoted_from_user": True}
    out = p.classify(msg, _context())
    assert out["category"] == 0
    assert out["severity"] == 0
    assert "NEWS_CONTEXT" in out["reason_codes"]
    assert "QUOTED_SPEECH_CONTEXT" in out["reason_codes"]
    assert out["rationale_id"] == "safe_protected_speech_v1"


def test_pipeline_no_overlay_keeps_violence_label():
    """Sanity: same encoder, no protected-speech context — verdict
    survives the threshold policy at label_only."""
    p = GuardrailPipeline(
        skill_bundle=SkillBundle(),
        encoder_adapter=_AlwaysViolenceAdapter(),
    )
    out = p.classify({"text": "i will hurt them"}, _context())
    assert out["category"] == 3  # VIOLENCE_THREAT
    assert out["actions"]["label_only"] is True


def test_pipeline_packs_context_hints_into_local_signals():
    """The packaged input passed to the adapter must include
    context_hints in local_signals — the schema requires it."""
    adapter = _RecordingAdapter()
    bundle = SkillBundle(
        community_overlay_id="kchat.community.journalism.guardrail.v1"
    )
    p = GuardrailPipeline(skill_bundle=bundle, encoder_adapter=adapter)
    p.classify(
        {"text": "headline", "quoted_from_user": True}, _context()
    )
    assert adapter.last_input is not None
    hints = adapter.last_input["local_signals"]["context_hints"]
    assert "NEWS_CONTEXT" in hints
    assert "QUOTED_SPEECH_CONTEXT" in hints


# ---------------------------------------------------------------------------
# Regression: protected-speech demotion must NOT silence deterministic
# detector signals (phishing URLs, PII, scam patterns, lexicon hits,
# NSFW media). The threshold-policy rule is intentionally narrow so a
# phishing URL in a school group is still flagged as SCAM_FRAUD, a PII
# leak in a journalism community is still flagged as PRIVATE_DATA,
# etc. — even though the surrounding context attaches a protected-
# speech hint.
#
# These tests lock in the fix for the deterministic-branch demotion
# bug found by Devin Review on commit 91ccd9f.
# ---------------------------------------------------------------------------
def test_phishing_url_in_school_context_stays_scam_fraud():
    """A high-risk phishing URL posted in a school group (which carries
    EDUCATION_CONTEXT) must NOT be demoted to SAFE."""
    p = GuardrailPipeline(
        skill_bundle=SkillBundle(
            community_overlay_id="kchat.community.school.guardrail.v1"
        ),
        encoder_adapter=MockEncoderAdapter(),
    )
    msg = {
        "text": "Kids click here: https://free-iphone.xyz/claim",
        "quoted_from_user": False,
    }
    ctx = _context()
    ctx["group_age_mode"] = "minor_present"
    out = p.classify(msg, ctx)
    assert out["category"] == 7  # SCAM_FRAUD
    assert out["severity"] >= 3
    # URL_RISK reason code must survive — it's the deterministic signal.
    assert "URL_RISK" in out["reason_codes"]
    # The protected-speech hint must NOT appear on the output of a
    # deterministic-driven branch.
    assert "EDUCATION_CONTEXT" not in out["reason_codes"]


def test_pii_in_journalism_context_stays_private_data():
    """A PII leak in a journalism community must NOT be demoted to
    SAFE just because NEWS_CONTEXT is attached."""
    p = GuardrailPipeline(
        skill_bundle=SkillBundle(
            community_overlay_id="kchat.community.journalism.guardrail.v1"
        ),
        encoder_adapter=MockEncoderAdapter(),
    )
    msg = {
        "text": "leaked: john.doe@example.com / 415-555-0199",
        "quoted_from_user": False,
    }
    out = p.classify(msg, _context())
    assert out["category"] == 9  # PRIVATE_DATA
    assert out["severity"] >= 3
    assert "PRIVATE_DATA_PATTERN" in out["reason_codes"]
    assert "NEWS_CONTEXT" not in out["reason_codes"]


def test_scam_pattern_in_education_context_stays_scam_fraud():
    """A scam pattern hit (advance-fee / fake-giveaway) in an education
    community must NOT be demoted to SAFE."""
    p = GuardrailPipeline(
        skill_bundle=SkillBundle(
            community_overlay_id="kchat.community.education.guardrail.v1"
        ),
        encoder_adapter=MockEncoderAdapter(),
    )
    msg = {
        "text": (
            "Congratulations! You have won a $500 gift card. "
            "Claim your prize at https://win-now.click/claim"
        ),
        "quoted_from_user": False,
    }
    out = p.classify(msg, _context())
    assert out["category"] == 7  # SCAM_FRAUD
    assert out["severity"] >= 3
    assert "EDUCATION_CONTEXT" not in out["reason_codes"]


def test_quoted_phishing_url_still_flagged_as_scam_fraud():
    """Even when the phishing URL is quoted (QUOTED_SPEECH_CONTEXT),
    it remains a deterministic SCAM_FRAUD hit. Quoting a phishing URL
    does not make it safe."""
    p = GuardrailPipeline(
        skill_bundle=SkillBundle(),
        encoder_adapter=MockEncoderAdapter(),
    )
    msg = {
        "text": "Friend forwarded this: https://win-now.click/claim",
        "quoted_from_user": True,
    }
    out = p.classify(msg, _context())
    assert out["category"] == 7  # SCAM_FRAUD
    assert "URL_RISK" in out["reason_codes"]
    assert "QUOTED_SPEECH_CONTEXT" not in out["reason_codes"]


def test_news_quote_about_violence_still_demotes_to_safe():
    """The original ``extremism-news-context-01`` regression: a
    quoted news report mentioning a banned organisation + attack
    must still be demoted to SAFE when only the embedding head fired.
    This proves the fix did NOT regress the protected-speech path."""
    p = GuardrailPipeline(
        skill_bundle=SkillBundle(
            community_overlay_id="kchat.community.journalism.guardrail.v1"
        ),
        encoder_adapter=_AlwaysViolenceAdapter(),
    )
    msg = {
        "text": (
            "Reuters reports a banned organization claimed "
            "responsibility for the attack."
        ),
        "quoted_from_user": True,
    }
    out = p.classify(msg, _context())
    assert out["category"] == 0  # SAFE
    assert out["severity"] == 0
    assert "NEWS_CONTEXT" in out["reason_codes"]
    assert "QUOTED_SPEECH_CONTEXT" in out["reason_codes"]
    assert out["rationale_id"] == "safe_protected_speech_v1"


# ---------------------------------------------------------------------------
# P0-1 — Embedding boundary.
#
# Privacy contract rule 5 (``kchat-skills/global/privacy_contract.yaml``)
# forbids the public output schema from ever carrying embeddings,
# hashes, or any other commitment to message content. The XLM-R
# adapter stashes its raw embedding on
# :attr:`XLMRAdapter.last_embedding` instead, where cross-pipeline
# consumers like ``chat-storage-search`` can read it without going
# through the schema boundary.
#
# These tests assert the pipeline strips any ``_``-prefixed key that
# a misbehaving adapter might attach to its output dict, on every
# threshold-policy branch:
#
# * Default SAFE pass-through
# * Rule 1 — child-safety floor
# * Rule 2 — protected-speech demotion (fresh-dict early return)
# * Rule 3 — uncertainty handling (fresh-dict early return)
# * Rule 4 — non-SAFE action re-derivation
# ---------------------------------------------------------------------------
def _underscore_attaching_adapter(
    *, severity: int, category: int, confidence: float, **extra: Any
):
    """Build a stub adapter whose ``classify`` attaches an underscore-
    prefixed extra (``_embedding`` plus a second sentinel ``_secret``).

    The pipeline MUST strip these from the public output dict on
    every threshold-policy branch — privacy contract rule 5 forbids
    embeddings, hashes, or any other commitment to message content
    on the public output boundary.
    """
    sentinel: list[float] = [0.0] * 384
    sentinel[0] = 1.0

    raw: dict[str, Any] = {
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
        "_embedding": sentinel,
        "_secret": "must-not-leak",
    }
    raw.update(extra)

    class _Adapter:
        def classify(self, input: dict[str, Any]) -> dict[str, Any]:
            # Fresh copy per call — mirrors the real adapter contract.
            return {
                **raw,
                "_embedding": list(raw["_embedding"]),
                "_secret": raw["_secret"],
            }

    return _Adapter()


class TestEmbeddingBoundary:
    """Every threshold-policy branch must strip underscore-prefixed extras.

    Mapped to the rules in
    :meth:`compiler.threshold_policy.ThresholdPolicy.apply`:

    * Rule 1 — child-safety floor (mutates ``_deepcopy_output`` in place)
    * Rule 2 — protected-speech demotion (constructs a fresh dict)
    * Rule 3 — uncertainty handling (constructs a fresh dict)
    * Rule 4 — non-SAFE action re-derivation (mutates in place)
    * Default SAFE pass-through (mutates in place)
    """

    @staticmethod
    def _assert_no_underscore_keys(out: dict[str, Any]) -> None:
        leaked = [k for k in out if isinstance(k, str) and k.startswith("_")]
        assert not leaked, (
            f"pipeline leaked underscore-prefixed keys: {leaked}; "
            f"privacy contract rule 5 forbids embeddings/extras on output"
        )
        assert "_embedding" not in out
        assert "_secret" not in out

    def test_safe_passthrough_strips_underscore_keys(self):
        adapter = _underscore_attaching_adapter(
            severity=0, category=0, confidence=0.0
        )
        p = GuardrailPipeline(
            skill_bundle=SkillBundle(),
            encoder_adapter=adapter,
        )
        out = p.classify({"text": "hi there"}, _context())
        self._assert_no_underscore_keys(out)

    def test_action_rederivation_strips_underscore_keys(self):
        # Rule 4: non-SAFE category, confidence high enough to keep
        # category but low enough that the policy re-derives actions.
        adapter = _underscore_attaching_adapter(
            severity=2, category=7, confidence=0.55
        )
        p = GuardrailPipeline(
            skill_bundle=SkillBundle(),
            encoder_adapter=adapter,
        )
        out = p.classify({"text": "see you at dinner"}, _context())
        self._assert_no_underscore_keys(out)

    def test_uncertainty_demotion_strips_underscore_keys(self):
        # Rule 3: non-SAFE category at confidence < LABEL_ONLY (0.45).
        adapter = _underscore_attaching_adapter(
            severity=3, category=7, confidence=0.10,
        )
        p = GuardrailPipeline(
            skill_bundle=SkillBundle(),
            encoder_adapter=adapter,
        )
        out = p.classify({"text": "hi"}, _context())
        assert out["category"] == 0, "uncertainty handling should demote to SAFE"
        self._assert_no_underscore_keys(out)

    def test_protected_speech_demotion_strips_underscore_keys(self):
        # Rule 2: non-SAFE category with a protected-speech reason
        # code carried by the encoder. Fresh-dict early return.
        adapter = _underscore_attaching_adapter(
            severity=2,
            category=6,  # HATE
            confidence=0.80,
            reason_codes=["NEWS_CONTEXT"],
        )
        p = GuardrailPipeline(
            skill_bundle=SkillBundle(),
            encoder_adapter=adapter,
        )
        out = p.classify({"text": "Reuters reports the speech"}, _context())
        # P1-1: NEWS_CONTEXT alone (overlay-derived, confidence 0.3)
        # is below the demotion floor — the policy now keeps the
        # category and downgrades to 'warn with context' rather than
        # silently demoting to SAFE. The boundary test only cares
        # that no underscore keys survive.
        self._assert_no_underscore_keys(out)

    def test_child_safety_floor_strips_underscore_keys(self):
        # Rule 1: CHILD_SAFETY floor mutates ``out`` in place via
        # ``_deepcopy_output``.
        adapter = _underscore_attaching_adapter(
            severity=2, category=1, confidence=0.50
        )
        p = GuardrailPipeline(
            skill_bundle=SkillBundle(),
            encoder_adapter=adapter,
        )
        out = p.classify({"text": "irrelevant"}, _context())
        assert out["category"] == 1
        assert out["severity"] == 5
        assert out["actions"]["critical_intervention"] is True
        self._assert_no_underscore_keys(out)

    def test_adapter_without_embedding_does_not_break_pipeline(self):
        # Adapters that omit ``_embedding`` (e.g. ``MockEncoderAdapter``)
        # MUST keep working — the pipeline must never synthesise the
        # field on its own.
        p = GuardrailPipeline(
            skill_bundle=SkillBundle(),
            encoder_adapter=MockEncoderAdapter(),
        )
        out = p.classify({"text": "see you at dinner"}, _context())
        assert "_embedding" not in out, (
            "pipeline must not synthesise _embedding when the adapter omits it"
        )
