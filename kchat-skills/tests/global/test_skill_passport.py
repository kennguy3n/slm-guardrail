"""Tests for ``kchat-skills/compiler/skill_passport.py``.

Covers:

* Passport creation with all required fields.
* ed25519 signing and verification round-trip.
* Rejection of expired passports (``expires_on`` in the past).
* Rejection of passports overshooting the 18-month expiry window.
* Rejection of passports with invalid signatures.
* Model-compatibility validation.
* Schema validity of ``skill_passport.schema.json``.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import jsonschema
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from skill_passport import (  # type: ignore[import-not-found]
    MAX_EXPIRY_DAYS,
    PASSPORT_SCHEMA_VERSION,
    ModelCompatibility,
    PassportValidationError,
    SkillPassport,
    TestResults,
    build_passport,
    generate_keypair,
)


COMPILER_DIR = Path(__file__).resolve().parents[2] / "compiler"
SCHEMA_PATH = COMPILER_DIR / "skill_passport.schema.json"


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _passing_test_results() -> TestResults:
    return TestResults(
        child_safety_recall=0.99,
        child_safety_precision=0.95,
        privacy_leak_precision=0.93,
        scam_recall=0.90,
        protected_speech_false_positive=0.03,
        minority_language_false_positive=0.05,
        p95_latency_ms=180,
    )


def _model() -> ModelCompatibility:
    return ModelCompatibility(
        model_id="kchat.slm.tiny",
        model_min_version="1.0.0",
        max_instruction_tokens=1800,
        max_output_tokens=600,
    )


def _build(*, expires_on: date | None = None) -> SkillPassport:
    return build_passport(
        skill_id="kchat.community.workplace.guardrail.v1",
        skill_version="1.0.0",
        parent="kchat.global.guardrail.baseline",
        authored_by="trust_and_safety_team",
        legal_reviewers=("legal-1",),
        cultural_reviewers=("cultural-1",),
        trust_and_safety_reviewers=("ts-1",),
        model_compatibility=(_model(),),
        expires_on=expires_on or (date.today() + timedelta(days=180)),
        test_results=_passing_test_results(),
    )


# ---------------------------------------------------------------------------
# Construction.
# ---------------------------------------------------------------------------
class TestConstruction:
    def test_passport_has_all_required_fields(self):
        p = _build()
        d = p.to_dict(include_signature=False)
        for key in (
            "skill_id",
            "skill_version",
            "schema_version",
            "parent",
            "authored_by",
            "reviewed_by",
            "model_compatibility",
            "expires_on",
            "test_results",
        ):
            assert key in d
        assert d["schema_version"] == PASSPORT_SCHEMA_VERSION

    def test_unsigned_passport_has_no_signature(self):
        p = _build()
        assert p.signature is None
        d = p.to_dict(include_signature=True)
        assert "signature" not in d

    def test_to_dict_without_signature(self):
        p = _build()
        sk = Ed25519PrivateKey.generate()
        p.sign(private_key=sk, key_id="k1")
        d = p.to_dict(include_signature=False)
        assert "signature" not in d


# ---------------------------------------------------------------------------
# Sign / verify round-trip.
# ---------------------------------------------------------------------------
class TestSignVerify:
    def test_round_trip_passes(self):
        sk, pk = generate_keypair()
        p = _build()
        p.sign(private_key=sk, key_id="k1")
        assert p.signature is not None
        assert p.signature.algorithm == "ed25519"
        # No exception.
        p.verify(public_key=pk)

    def test_unsigned_rejected(self):
        sk, pk = generate_keypair()
        p = _build()
        with pytest.raises(PassportValidationError, match="unsigned"):
            p.verify(public_key=pk)

    def test_tampered_signature_rejected(self):
        sk, pk = generate_keypair()
        p = _build()
        p.sign(private_key=sk, key_id="k1")
        # Mutate a field without re-signing — verification must fail.
        p.skill_version = "9.9.9"
        with pytest.raises(PassportValidationError, match="signature mismatch"):
            p.verify(public_key=pk)

    def test_wrong_public_key_rejected(self):
        sk, _pk = generate_keypair()
        _, other_pk = generate_keypair()
        p = _build()
        p.sign(private_key=sk, key_id="k1")
        with pytest.raises(PassportValidationError):
            p.verify(public_key=other_pk)

    def test_unsupported_algorithm_rejected(self):
        from skill_passport import Signature  # local import to keep top-level light

        sk, pk = generate_keypair()
        p = _build()
        p.sign(private_key=sk, key_id="k1")
        # Replace algorithm metadata.
        original = p.signature
        assert original is not None
        p.signature = Signature(
            algorithm="ecdsa-sha256",
            key_id=original.key_id,
            value=original.value,
        )
        with pytest.raises(
            PassportValidationError, match="unsupported signature algorithm"
        ):
            p.verify(public_key=pk)


# ---------------------------------------------------------------------------
# Expiry handling.
# ---------------------------------------------------------------------------
class TestExpiry:
    def test_expired_passport_rejected(self):
        sk, pk = generate_keypair()
        # Build with a far-future expiry so signing is allowed, then verify
        # against a "now" that puts it in the past.
        future = date.today() + timedelta(days=30)
        p = _build(expires_on=future)
        p.sign(private_key=sk, key_id="k1")
        with pytest.raises(PassportValidationError, match="expired"):
            p.verify(public_key=pk, now=future + timedelta(days=1))

    def test_expiry_just_within_18_months_passes(self):
        sk, pk = generate_keypair()
        today = date.today()
        p = _build(expires_on=today + timedelta(days=MAX_EXPIRY_DAYS))
        p.sign(private_key=sk, key_id="k1")
        # No exception.
        p.verify(public_key=pk, now=today)

    def test_expiry_exceeding_18_months_rejected(self):
        sk, pk = generate_keypair()
        today = date.today()
        p = _build(expires_on=today + timedelta(days=MAX_EXPIRY_DAYS + 1))
        p.sign(private_key=sk, key_id="k1")
        with pytest.raises(
            PassportValidationError, match="exceeds the 18-month window"
        ):
            p.verify(public_key=pk, now=today)


# ---------------------------------------------------------------------------
# Model compatibility.
# ---------------------------------------------------------------------------
class TestModelCompatibility:
    def test_compatible_model_accepts(self):
        sk, pk = generate_keypair()
        p = _build()
        p.sign(private_key=sk, key_id="k1")
        # Same model id, same min version.
        p.verify(public_key=pk, model=_model())

    def test_higher_min_version_accepts(self):
        sk, pk = generate_keypair()
        p = _build()
        p.sign(private_key=sk, key_id="k1")
        runtime = ModelCompatibility(
            model_id="kchat.slm.tiny",
            model_min_version="1.5.0",
        )
        # Pack requires >= 1.0.0; runtime is 1.5.0 ⇒ OK.
        p.verify(public_key=pk, model=runtime)

    def test_lower_runtime_version_rejected(self):
        sk, pk = generate_keypair()
        p = _build()
        p.sign(private_key=sk, key_id="k1")
        runtime = ModelCompatibility(
            model_id="kchat.slm.tiny",
            model_min_version="0.9.0",
        )
        with pytest.raises(PassportValidationError, match="not compatible"):
            p.verify(public_key=pk, model=runtime)

    def test_different_model_id_rejected(self):
        sk, pk = generate_keypair()
        p = _build()
        p.sign(private_key=sk, key_id="k1")
        other = ModelCompatibility(
            model_id="kchat.slm.large",
            model_min_version="1.0.0",
        )
        with pytest.raises(PassportValidationError, match="not compatible"):
            p.verify(public_key=pk, model=other)

    @pytest.mark.parametrize(
        "have, want",
        [
            ("1.0", "1.0.0"),       # equal under zero-padding
            ("1", "1.0.0"),
            ("1.0.0", "1.0"),
            ("1.0.0", "1"),
            ("2", "1.9.9"),
        ],
    )
    def test_short_runtime_version_accepted_when_zero_pad_equivalent(
        self, have, want
    ):
        # Pack requires `want`, runtime advertises `have`. The shorter tuple
        # must be padded with zeros so e.g. "1.0" is treated as ">= 1.0.0".
        sk, pk = generate_keypair()
        p = build_passport(
            skill_id="kchat.global.guardrail.baseline",
            skill_version="1.0.0",
            parent=None,
            authored_by="kchat-core@kchat.example",
            legal_reviewers=("legal-review@kchat.example",),
            cultural_reviewers=("cultural-review@kchat.example",),
            trust_and_safety_reviewers=("trust@kchat.example",),
            model_compatibility=(
                ModelCompatibility(
                    model_id="kchat.slm.tiny",
                    model_min_version=want,
                ),
            ),
            expires_on=date.today() + timedelta(days=30),
            test_results=TestResults(
                child_safety_recall=0.99,
                child_safety_precision=0.95,
                privacy_leak_precision=0.95,
                scam_recall=0.90,
                protected_speech_false_positive=0.02,
                minority_language_false_positive=0.03,
                p95_latency_ms=180,
            ),
        )
        p.sign(private_key=sk, key_id="k1")
        runtime = ModelCompatibility(
            model_id="kchat.slm.tiny",
            model_min_version=have,
        )
        # Must NOT raise.
        p.verify(public_key=pk, model=runtime)


# ---------------------------------------------------------------------------
# JSON Schema validation.
# ---------------------------------------------------------------------------
class TestPassportSchema:
    @pytest.fixture(scope="class")
    def schema(self) -> dict:
        with SCHEMA_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)

    def test_schema_is_valid_jsonschema(self, schema):
        # Just compiling the validator is enough to confirm it parses.
        validator = jsonschema.Draft7Validator(schema)
        validator.check_schema(schema)

    def test_signed_passport_round_trips_through_schema(self, schema):
        sk, _pk = generate_keypair()
        p = _build()
        p.sign(private_key=sk, key_id="k1")
        d = p.to_dict()
        jsonschema.validate(d, schema)

    def test_passport_missing_required_field_rejected(self, schema):
        d = _build().to_dict()
        del d["skill_id"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(d, schema)

    def test_passport_with_invalid_signature_alg_rejected(self, schema):
        sk, _pk = generate_keypair()
        p = _build()
        p.sign(private_key=sk, key_id="k1")
        d = p.to_dict()
        d["signature"]["algorithm"] = "ecdsa-sha256"
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(d, schema)

    def test_signing_payload_excludes_signature(self):
        sk, _pk = generate_keypair()
        p = _build()
        unsigned_payload = p.signing_payload()
        p.sign(private_key=sk, key_id="k1")
        # signing_payload must not change once we attach a signature —
        # otherwise verification would always fail.
        assert p.signing_payload() == unsigned_payload
