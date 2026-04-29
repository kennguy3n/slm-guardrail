"""Pack-lifecycle store — versioning, rollback, and expiry-review.

Spec reference: PHASES.md Phase 6 ("Implement skill pack versioning,
rollback, and expiry-review workflows — no pack older than its
``expires_on`` ships to devices") and ARCHITECTURE.md
"anti_misuse_controls.technical": "rollback: previous N signed packs
retained on device".

The :class:`PackStore` is a simple, JSON-serialisable in-memory ledger
of every signed pack version we know about, keyed by ``skill_id``.
It is *not* a key-value store on a device — devices ship a hardened
loader that reads JSON written by this module — but the dataflow is
the same: register a signed passport, mark it active, retain the
last :data:`MAX_RETAINED_VERSIONS` signed versions for rollback,
flag any pack that is expired or expiring soon for legal / cultural
re-review.

The module deliberately does *not* re-verify the passport signature
on every operation — that lives in :mod:`skill_passport`. Callers
are expected to call :meth:`SkillPassport.verify` before registering
the version. The ``signature_valid`` flag on :class:`PackVersion`
records the outcome of that verification at registration time.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

from skill_passport import SkillPassport  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------
# Per ARCHITECTURE.md anti-misuse controls: keep the previous N signed
# packs on device so a regressed pack can be rolled back without a
# re-download.
MAX_RETAINED_VERSIONS: int = 3

# Days ahead of ``expires_on`` at which a pack is flagged for review.
EXPIRY_REVIEW_WINDOW_DAYS: int = 30


# ---------------------------------------------------------------------------
# Dataclass.
# ---------------------------------------------------------------------------
@dataclass
class PackVersion:
    """A single signed pack version on a device.

    The store stamps ``signed_on`` from ``date.today()`` if the
    register call does not provide one — passports do not carry an
    explicit signing date today (only ``expires_on``), so the store
    records when *we* observed the signature.
    """

    skill_id: str
    version: str
    signed_on: date
    expires_on: date
    signature_valid: bool = True
    is_active: bool = False

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "signed_on": self.signed_on.isoformat(),
            "expires_on": self.expires_on.isoformat(),
            "signature_valid": bool(self.signature_valid),
            "is_active": bool(self.is_active),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "PackVersion":
        return cls(
            skill_id=str(payload["skill_id"]),
            version=str(payload["version"]),
            signed_on=date.fromisoformat(payload["signed_on"]),
            expires_on=date.fromisoformat(payload["expires_on"]),
            signature_valid=bool(payload.get("signature_valid", True)),
            is_active=bool(payload.get("is_active", False)),
        )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------
def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _version_tuple(v: str) -> tuple[int, ...]:
    """Best-effort semver tuple. Tolerates pre-release suffixes by
    truncating each component at the first non-digit (matching the
    convention in ``skill_passport._version_tuple``)."""
    parts: list[int] = []
    for p in v.split("."):
        digits = ""
        for ch in p:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


# ---------------------------------------------------------------------------
# PackStore.
# ---------------------------------------------------------------------------
class PackLifecycleError(ValueError):
    """Raised on lifecycle invariant violations (e.g. duplicate version)."""


@dataclass
class PackStore:
    """A device-local ledger of signed pack versions.

    Stored shape (JSON serialisation):

    .. code-block:: json

       {
         "schema_version": 1,
         "skills": {
           "kchat.global.guardrail.baseline": [<PackVersion>, ...],
           "kchat.jurisdiction.us.guardrail.v1": [<PackVersion>, ...]
         }
       }

    Each ``skills[<id>]`` list is kept sorted newest-first and bounded
    by :data:`MAX_RETAINED_VERSIONS`. Exactly one version per
    ``skill_id`` carries ``is_active=True``.
    """

    skills: dict[str, list[PackVersion]] = field(default_factory=dict)
    schema_version: int = 1

    # ------------------------------------------------------------------
    # Registration / retrieval.
    # ------------------------------------------------------------------
    def register(
        self,
        passport: SkillPassport,
        *,
        signed_on: Optional[date] = None,
        signature_valid: Optional[bool] = None,
    ) -> PackVersion:
        """Register a signed pack version.

        Marks the new version as active and demotes the previously
        active version. Trims older versions beyond
        :data:`MAX_RETAINED_VERSIONS`.
        """
        if not passport.skill_id:
            raise PackLifecycleError("passport is missing skill_id")
        if not passport.skill_version:
            raise PackLifecycleError("passport is missing skill_version")

        observed_signature_valid = (
            signature_valid
            if signature_valid is not None
            else passport.signature is not None
        )

        version = PackVersion(
            skill_id=passport.skill_id,
            version=passport.skill_version,
            signed_on=signed_on or _today_utc(),
            expires_on=passport.expires_on,
            signature_valid=observed_signature_valid,
            is_active=True,
        )

        history = self.skills.setdefault(passport.skill_id, [])
        for existing in history:
            existing.is_active = False
        # If the same version is registered twice, replace in-place
        # (latest wins) rather than duplicating.
        history = [
            v for v in history if v.version != passport.skill_version
        ]
        history.insert(0, version)

        # Sort newest-first by signed_on, falling back to semver.
        history.sort(
            key=lambda v: (v.signed_on, _version_tuple(v.version)),
            reverse=True,
        )

        # Re-mark the newest as active (in case a prior dated version
        # was registered out of order).
        for i, v in enumerate(history):
            v.is_active = i == 0

        # Cap retention at MAX_RETAINED_VERSIONS.
        del history[MAX_RETAINED_VERSIONS:]

        self.skills[passport.skill_id] = history
        return version

    def get_active(self, skill_id: str) -> Optional[PackVersion]:
        for v in self.skills.get(skill_id, []):
            if v.is_active:
                return v
        return None

    def get_history(self, skill_id: str) -> list[PackVersion]:
        # Return a copy so callers can't mutate internal state.
        return list(self.skills.get(skill_id, []))

    # ------------------------------------------------------------------
    # Rollback.
    # ------------------------------------------------------------------
    def rollback(self, skill_id: str) -> Optional[PackVersion]:
        """Roll back to the previous signed version.

        Returns the newly-active :class:`PackVersion`, or ``None`` if
        only one version is retained (nothing to roll back to). The
        previously active version is dropped from the history (a
        rolled-back pack cannot become active again without a fresh
        signing) so the next rollback walks further back.
        """
        history = self.skills.get(skill_id, [])
        if len(history) < 2:
            return None
        # The newest entry is the (currently) active version. Drop it
        # and promote the next one.
        history.pop(0)
        for i, v in enumerate(history):
            v.is_active = i == 0
        self.skills[skill_id] = history
        return history[0]

    # ------------------------------------------------------------------
    # Expiry / review.
    # ------------------------------------------------------------------
    def check_expiry(
        self,
        now: Optional[date] = None,
        days_ahead: int = EXPIRY_REVIEW_WINDOW_DAYS,
    ) -> list[str]:
        """Return ``skill_id`` for any pack that is expired or will
        expire within ``days_ahead`` days.

        Only the *active* version per skill is considered — older
        retained versions are not eligible to ship and so don't
        trigger a review.
        """
        today = now or _today_utc()
        cutoff = today + timedelta(days=days_ahead)
        out: list[str] = []
        for skill_id, history in self.skills.items():
            active = next((v for v in history if v.is_active), None)
            if active is None:
                continue
            if active.expires_on <= cutoff:
                out.append(skill_id)
        return sorted(out)

    def deactivate_expired(self, now: Optional[date] = None) -> list[str]:
        """Mark every active pack whose ``expires_on < now`` as
        inactive and return the deactivated skill_ids."""
        today = now or _today_utc()
        out: list[str] = []
        for skill_id, history in self.skills.items():
            for v in history:
                if v.is_active and v.expires_on < today:
                    v.is_active = False
                    out.append(skill_id)
        return sorted(out)

    def needs_review(
        self,
        days_ahead: int = EXPIRY_REVIEW_WINDOW_DAYS,
        now: Optional[date] = None,
    ) -> list[PackVersion]:
        """Return the active versions whose ``expires_on`` falls
        within ``days_ahead`` days of ``now``.

        Versions that are already expired are *not* included — they
        belong to :meth:`deactivate_expired`'s cohort, not the review
        queue.
        """
        today = now or _today_utc()
        cutoff = today + timedelta(days=days_ahead)
        out: list[PackVersion] = []
        for history in self.skills.values():
            for v in history:
                if not v.is_active:
                    continue
                if today <= v.expires_on <= cutoff:
                    out.append(v)
        out.sort(key=lambda v: (v.expires_on, v.skill_id))
        return out

    # ------------------------------------------------------------------
    # JSON round-trip.
    # ------------------------------------------------------------------
    def to_json(self) -> str:
        payload = {
            "schema_version": self.schema_version,
            "skills": {
                skill_id: [v.to_dict() for v in history]
                for skill_id, history in self.skills.items()
            },
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "PackStore":
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise PackLifecycleError(
                "PackStore JSON must deserialise to an object"
            )
        if "skills" not in payload or not isinstance(payload["skills"], dict):
            raise PackLifecycleError(
                "PackStore JSON missing 'skills' object"
            )
        skills: dict[str, list[PackVersion]] = {}
        for skill_id, history in payload["skills"].items():
            skills[skill_id] = [
                PackVersion.from_dict(item) for item in history
            ]
        return cls(
            skills=skills,
            schema_version=int(payload.get("schema_version", 1)),
        )

    # ------------------------------------------------------------------
    # Convenience.
    # ------------------------------------------------------------------
    def all_active(self) -> list[PackVersion]:
        out = [
            v
            for history in self.skills.values()
            for v in history
            if v.is_active
        ]
        out.sort(key=lambda v: v.skill_id)
        return out


__all__ = [
    "EXPIRY_REVIEW_WINDOW_DAYS",
    "MAX_RETAINED_VERSIONS",
    "PackLifecycleError",
    "PackStore",
    "PackVersion",
]
