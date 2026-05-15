"""Skill passport — schema, ed25519 signing, and verification.

Spec reference: ARCHITECTURE.md "Compiler Pipeline + Skill Passport"
(lines 681-712) and PHASES.md Phase 4.

A *signed pack* carries a passport that records:

* identity (``skill_id``, ``skill_version``, ``schema_version``,
  ``parent``)
* provenance (``authored_by``, ``reviewed_by`` per role)
* model compatibility (``model_id`` / ``model_min_version`` /
  ``max_instruction_tokens`` / ``max_output_tokens``)
* expiry (``expires_on`` — max 18 months from issuance)
* test results (the seven shipping metrics)
* signature (ed25519, base64-encoded)

The signature covers a deterministic JSON serialisation of every
non-signature field. Verification rejects:

* expired passports;
* passports whose ``expires_on`` is more than 18 months after now;
* tampered passports (signature mismatch);
* model-incompatibility (when an explicit model context is supplied).

The reference implementation uses the :mod:`cryptography` library;
test code generates ephemeral keys per case so no key material is
checked in.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


SIGNATURE_ALGORITHM = "ed25519"
PASSPORT_SCHEMA_VERSION = 1
MAX_EXPIRY_DAYS = 18 * 30  # ~18 months — matches ARCHITECTURE.md.


# ---------------------------------------------------------------------------
# Dataclasses — mirror the YAML schema from ARCHITECTURE.md lines 683-712.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ModelCompatibility:
    """Pack-level pin on the on-device model and tokenizer.

    P0-4: when ``model_checksum`` / ``tokenizer_checksum`` are
    populated the compiler also records the SHA-256 hex digest of
    the ONNX file and tokenizer file the pack was tested against.
    :meth:`SkillPassport.verify` cross-checks those digests against
    the runtime ``ModelCompatibility`` supplied by the host so a
    tampered or swapped binary is rejected before the pack is
    activated. Empty checksums on either side disable the check (for
    backwards compatibility with passports issued before P0-4).
    """

    model_id: str
    model_min_version: str
    max_instruction_tokens: int = 1800
    max_output_tokens: int = 600
    model_checksum: Optional[str] = None
    tokenizer_checksum: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "model_id": self.model_id,
            "model_min_version": self.model_min_version,
            "max_instruction_tokens": int(self.max_instruction_tokens),
            "max_output_tokens": int(self.max_output_tokens),
        }
        if self.model_checksum:
            out["model_checksum"] = self.model_checksum
        if self.tokenizer_checksum:
            out["tokenizer_checksum"] = self.tokenizer_checksum
        return out


@dataclass(frozen=True)
class TestResults:
    # Tell pytest not to collect this dataclass as a test class.
    __test__ = False

    child_safety_recall: float
    child_safety_precision: float
    privacy_leak_precision: float
    scam_recall: float
    protected_speech_false_positive: float
    minority_language_false_positive: float
    p95_latency_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "child_safety_recall": float(self.child_safety_recall),
            "child_safety_precision": float(self.child_safety_precision),
            "privacy_leak_precision": float(self.privacy_leak_precision),
            "scam_recall": float(self.scam_recall),
            "protected_speech_false_positive": float(
                self.protected_speech_false_positive
            ),
            "minority_language_false_positive": float(
                self.minority_language_false_positive
            ),
            "p95_latency_ms": int(self.p95_latency_ms),
        }


@dataclass(frozen=True)
class Reviewers:
    legal: tuple[str, ...] = ()
    cultural: tuple[str, ...] = ()
    trust_and_safety: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "legal": list(self.legal),
            "cultural": list(self.cultural),
            "trust_and_safety": list(self.trust_and_safety),
        }


@dataclass(frozen=True)
class Signature:
    algorithm: str
    key_id: str
    value: str  # base64 ed25519 signature

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "key_id": self.key_id,
            "value": self.value,
        }


@dataclass
class SkillPassport:
    """The ARCHITECTURE.md "skill_passport" YAML, in Python form."""

    skill_id: str
    skill_version: str
    parent: Optional[str]
    authored_by: str
    reviewed_by: Reviewers
    model_compatibility: tuple[ModelCompatibility, ...]
    expires_on: date
    test_results: TestResults
    schema_version: int = PASSPORT_SCHEMA_VERSION
    signature: Optional[Signature] = None

    # ------------------------------------------------------------------
    # Serialisation helpers.
    # ------------------------------------------------------------------
    def to_dict(self, *, include_signature: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "schema_version": int(self.schema_version),
            "parent": self.parent,
            "authored_by": self.authored_by,
            "reviewed_by": self.reviewed_by.to_dict(),
            "model_compatibility": [
                m.to_dict() for m in self.model_compatibility
            ],
            "expires_on": self.expires_on.isoformat(),
            "test_results": self.test_results.to_dict(),
        }
        if include_signature and self.signature is not None:
            out["signature"] = self.signature.to_dict()
        return out

    def signing_payload(self) -> bytes:
        """Deterministic JSON serialisation of the unsigned payload."""
        return json.dumps(
            self.to_dict(include_signature=False),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    # ------------------------------------------------------------------
    # Sign / verify.
    # ------------------------------------------------------------------
    def sign(self, *, private_key: Ed25519PrivateKey, key_id: str) -> "SkillPassport":
        sig_bytes = private_key.sign(self.signing_payload())
        self.signature = Signature(
            algorithm=SIGNATURE_ALGORITHM,
            key_id=key_id,
            value=base64.b64encode(sig_bytes).decode("ascii"),
        )
        return self

    def verify(
        self,
        *,
        public_key: Ed25519PublicKey,
        now: Optional[date] = None,
        model: Optional[ModelCompatibility] = None,
        revocation_list: Optional["RevocationList"] = None,
        revocation_list_public_key: Optional[Ed25519PublicKey] = None,
    ) -> None:
        """Validate the passport.

        Raises :class:`PassportValidationError` on any of:

        * missing or wrong-algorithm passport signature;
        * tampered passport signature;
        * passport already expired (``expires_on < now``);
        * passport overshooting the 18-month expiry budget
          (``expires_on > now + 18 months``);
        * model-id mismatch when ``model`` is supplied;
        * model / tokenizer SHA-256 checksum mismatch when both
          passport and runtime ``ModelCompatibility`` supply them
          (P0-4);
        * skill_id + skill_version listed in ``revocation_list``
          (P1-3);
        * ``revocation_list`` signature / expiry invalid when
          ``revocation_list_public_key`` is supplied (P1-3).

        **Revocation-list trust model.** ``RevocationList`` instances
        are themselves signed Ed25519 payloads with their own
        ``expires_on`` window. Callers must establish trust in the
        list before its ``is_revoked`` answer can be relied on:

        1. **Recommended** — pass ``revocation_list_public_key``
           alongside ``revocation_list``. ``verify()`` will run
           :meth:`RevocationList.verify_signature` against that key
           and fail closed if the list is unsigned, tampered, or
           expired *before* asking it whether the passport is
           revoked. This is the safest pattern and the one the
           tests demonstrate.
        2. Or, call ``revocation_list.verify_signature(...)``
           yourself before invoking this method and omit
           ``revocation_list_public_key``. The caller is then fully
           responsible for the list's integrity — a tampered or
           expired list passed in unverified is treated as trusted
           and may either falsely revoke or fail to revoke.

        Passing ``revocation_list`` without
        ``revocation_list_public_key`` and without an out-of-band
        verification step is a bug. There is no third option.
        """
        if self.signature is None:
            raise PassportValidationError("passport is unsigned")
        if self.signature.algorithm != SIGNATURE_ALGORITHM:
            raise PassportValidationError(
                f"unsupported signature algorithm: {self.signature.algorithm}"
            )
        try:
            public_key.verify(
                base64.b64decode(self.signature.value),
                self.signing_payload(),
            )
        except (InvalidSignature, ValueError) as exc:
            raise PassportValidationError("signature mismatch") from exc

        # Revocation check runs AFTER the passport's own signature
        # check so an attacker who forges a passport cannot also use
        # a forged revocation entry to make the passport appear to
        # have been a known revoked one. Both outcomes are rejection,
        # but the order keeps the failure modes distinct in the
        # caller's exception path.
        if revocation_list is not None:
            if revocation_list_public_key is not None:
                revocation_list.verify_signature(
                    public_key=revocation_list_public_key, now=now
                )
            if revocation_list.is_revoked(
                self.skill_id, self.skill_version
            ):
                entry = revocation_list.lookup(
                    self.skill_id, self.skill_version
                )
                reason = (
                    f" ({entry.reason})" if entry and entry.reason else ""
                )
                raise PassportValidationError(
                    f"passport revoked: "
                    f"{self.skill_id}@{self.skill_version}{reason}"
                )

        today = now or _today_utc()
        if self.expires_on < today:
            raise PassportValidationError(
                f"passport expired on {self.expires_on.isoformat()}"
            )
        max_expiry = today + timedelta(days=MAX_EXPIRY_DAYS)
        if self.expires_on > max_expiry:
            raise PassportValidationError(
                f"expires_on {self.expires_on.isoformat()} exceeds "
                f"the 18-month window (max {max_expiry.isoformat()})"
            )

        if model is not None:
            compatible: list[ModelCompatibility] = [
                mc for mc in self.model_compatibility
                if mc.model_id == model.model_id
                and _version_at_least(
                    model.model_min_version, mc.model_min_version
                )
            ]
            if not compatible:
                raise PassportValidationError(
                    f"passport not compatible with model "
                    f"{model.model_id}@{model.model_min_version}"
                )
            # P0-4: checksums are advisory — a mismatch on any of
            # them is a hard verify failure, but pre-P0-4 passports
            # without checksums still verify against runtimes that
            # do have checksums and vice versa.
            self._verify_artefact_checksums(model, compatible)


    @staticmethod
    def _verify_artefact_checksums(
        runtime: ModelCompatibility,
        compatible: list[ModelCompatibility],
    ) -> None:
        """P0-4: cross-check model/tokenizer checksums.

        Either side may omit a checksum (older passports, runtimes
        that have not computed them). When BOTH sides supply a
        checksum for the same artefact, they MUST agree — a
        mismatch is a hard verify failure so a tampered or swapped
        binary cannot pass verification.

        Note: a passport may carry multiple ``ModelCompatibility``
        entries for the same ``model_id`` (e.g. a v1.0 entry pinning
        an older checksum and a v2.0 entry pinning the current one).
        ``verify()`` only passes us entries whose ``model_min_version``
        is ≤ the runtime version, so for a v2.0 runtime BOTH entries
        appear here. We accept the passport if **at least one**
        compatible entry's checksums are consistent with the runtime
        (either matching or vacuously skipped). Only if every
        compatible entry has a concrete mismatch do we fail — that
        case really does mean none of the pinned binaries are the
        one we loaded.
        """
        if not compatible:
            return
        mismatches: list[str] = []
        for mc in compatible:
            entry_mismatch: str | None = None
            pairs = (
                ("model_checksum", mc.model_checksum, runtime.model_checksum),
                (
                    "tokenizer_checksum",
                    mc.tokenizer_checksum,
                    runtime.tokenizer_checksum,
                ),
            )
            for field_name, passport_checksum, runtime_checksum in pairs:
                if not passport_checksum or not runtime_checksum:
                    continue
                if (
                    passport_checksum.strip().lower()
                    != runtime_checksum.strip().lower()
                ):
                    entry_mismatch = (
                        f"{field_name} mismatch for model "
                        f"{runtime.model_id}@{runtime.model_min_version} "
                        f"(passport={passport_checksum}, "
                        f"runtime={runtime_checksum})"
                    )
                    break
            if entry_mismatch is None:
                # At least one compatible entry agrees with the runtime
                # (or has nothing to check). Passport verifies.
                return
            mismatches.append(entry_mismatch)

        raise PassportValidationError(
            "no compatible model entry matched runtime checksums: "
            + "; ".join(mismatches)
        )


class PassportValidationError(ValueError):
    """Raised when a skill passport fails verification."""


# ---------------------------------------------------------------------------
# P1-3 — Revocation.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RevocationEntry:
    """A single revoked (skill_id, skill_version) tuple.

    ``revoked_on`` is an ISO-8601 date; ``reason`` is free-text
    operator-facing context (e.g. ``"compromised-key"``,
    ``"safety-regression"``); ``revoked_by`` is the identity of the
    operator who added the entry (mirrors ``authored_by`` on the
    passport itself).
    """

    skill_id: str
    skill_version: str
    revoked_on: date
    reason: str
    revoked_by: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "revoked_on": self.revoked_on.isoformat(),
            "reason": self.reason,
            "revoked_by": self.revoked_by,
        }


@dataclass
class RevocationList:
    """A signed list of revoked passports.

    The list is itself authenticated by an ed25519 signature over a
    deterministic JSON serialisation of every non-signature field so
    a tampered revocation list cannot silently un-revoke a known-bad
    pack.

    The list is intentionally tiny in the reference implementation —
    production deployments should keep the file under a few hundred
    entries and rely on monotonic ``issued_on`` / ``expires_on``
    rotation to retire old entries. The signing / verification
    surface mirrors :class:`SkillPassport`.
    """

    entries: tuple[RevocationEntry, ...]
    issued_on: date
    expires_on: date
    signature: Optional[Signature] = None

    def to_dict(self, *, include_signature: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "entries": [e.to_dict() for e in self.entries],
            "issued_on": self.issued_on.isoformat(),
            "expires_on": self.expires_on.isoformat(),
        }
        if include_signature and self.signature is not None:
            out["signature"] = self.signature.to_dict()
        return out

    def signing_payload(self) -> bytes:
        return json.dumps(
            self.to_dict(include_signature=False),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def sign(
        self, *, private_key: Ed25519PrivateKey, key_id: str
    ) -> "RevocationList":
        sig_bytes = private_key.sign(self.signing_payload())
        self.signature = Signature(
            algorithm=SIGNATURE_ALGORITHM,
            key_id=key_id,
            value=base64.b64encode(sig_bytes).decode("ascii"),
        )
        return self

    def verify_signature(
        self,
        *,
        public_key: Ed25519PublicKey,
        now: Optional[date] = None,
    ) -> None:
        """Verify the list's own signature + expiry."""
        if self.signature is None:
            raise PassportValidationError("revocation list is unsigned")
        if self.signature.algorithm != SIGNATURE_ALGORITHM:
            raise PassportValidationError(
                f"unsupported signature algorithm: {self.signature.algorithm}"
            )
        try:
            public_key.verify(
                base64.b64decode(self.signature.value),
                self.signing_payload(),
            )
        except (InvalidSignature, ValueError) as exc:
            raise PassportValidationError(
                "revocation list signature mismatch"
            ) from exc
        today = now or _today_utc()
        if self.expires_on < today:
            raise PassportValidationError(
                f"revocation list expired on {self.expires_on.isoformat()}"
            )

    def is_revoked(self, skill_id: str, skill_version: str) -> bool:
        return self.lookup(skill_id, skill_version) is not None

    def lookup(
        self, skill_id: str, skill_version: str
    ) -> Optional[RevocationEntry]:
        for e in self.entries:
            if e.skill_id == skill_id and e.skill_version == skill_version:
                return e
        return None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RevocationList":
        entries = tuple(
            RevocationEntry(
                skill_id=str(e["skill_id"]),
                skill_version=str(e["skill_version"]),
                revoked_on=date.fromisoformat(str(e["revoked_on"])),
                reason=str(e.get("reason", "")),
                revoked_by=str(e.get("revoked_by", "")),
            )
            for e in data.get("entries", [])
        )
        sig = None
        sig_block = data.get("signature")
        if isinstance(sig_block, dict):
            sig = Signature(
                algorithm=str(sig_block.get("algorithm", "")),
                key_id=str(sig_block.get("key_id", "")),
                value=str(sig_block.get("value", "")),
            )
        return cls(
            entries=entries,
            issued_on=date.fromisoformat(str(data["issued_on"])),
            expires_on=date.fromisoformat(str(data["expires_on"])),
            signature=sig,
        )

    @classmethod
    def load(cls, path: Any) -> "RevocationList":
        """Load a JSON or YAML revocation list from disk.

        YAML is preferred for operator-edited lists; JSON is preferred
        when the list is produced by tooling. Both forms encode the
        same schema as :meth:`to_dict`.
        """
        import pathlib

        p = pathlib.Path(path)
        text = p.read_text(encoding="utf-8")
        if p.suffix in {".yaml", ".yml"}:
            try:
                import yaml  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - exercised in setups w/o yaml
                raise PassportValidationError(
                    "PyYAML is required to load a YAML revocation list"
                ) from exc
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
        if not isinstance(data, dict):
            raise PassportValidationError(
                "revocation list root must be an object"
            )
        return cls.from_dict(data)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _version_tuple(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for p in v.split("."):
        # tolerate semver pre-release suffixes by truncating at non-digit.
        digits = ""
        for ch in p:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _version_at_least(have: str, want: str) -> bool:
    h, w = _version_tuple(have), _version_tuple(want)
    # Pad the shorter tuple with zeros so e.g. ``1.0`` and ``1.0.0`` compare
    # equal — Python's native tuple comparison would otherwise treat the
    # shorter tuple as less, incorrectly rejecting compatible runtime
    # versions in the security-critical model-compatibility check.
    length = max(len(h), len(w))
    h = h + (0,) * (length - len(h))
    w = w + (0,) * (length - len(w))
    return h >= w


def generate_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Convenience wrapper around :class:`Ed25519PrivateKey.generate`."""
    sk = Ed25519PrivateKey.generate()
    return sk, sk.public_key()


def build_passport(
    *,
    skill_id: str,
    skill_version: str,
    parent: Optional[str],
    authored_by: str,
    legal_reviewers: tuple[str, ...] = (),
    cultural_reviewers: tuple[str, ...] = (),
    trust_and_safety_reviewers: tuple[str, ...] = (),
    model_compatibility: tuple[ModelCompatibility, ...],
    expires_on: date,
    test_results: TestResults,
) -> SkillPassport:
    """Construct an unsigned passport with consistent typing."""
    return SkillPassport(
        skill_id=skill_id,
        skill_version=skill_version,
        parent=parent,
        authored_by=authored_by,
        reviewed_by=Reviewers(
            legal=tuple(legal_reviewers),
            cultural=tuple(cultural_reviewers),
            trust_and_safety=tuple(trust_and_safety_reviewers),
        ),
        model_compatibility=tuple(model_compatibility),
        expires_on=expires_on,
        test_results=test_results,
    )


__all__ = [
    "MAX_EXPIRY_DAYS",
    "ModelCompatibility",
    "PASSPORT_SCHEMA_VERSION",
    "PassportValidationError",
    "RevocationEntry",
    "RevocationList",
    "Reviewers",
    "SIGNATURE_ALGORITHM",
    "Signature",
    "SkillPassport",
    "TestResults",
    "build_passport",
    "generate_keypair",
]
