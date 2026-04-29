"""Tests for ``kchat-skills/compiler/pack_lifecycle.py``.

Spec reference: PHASES.md Phase 6 — versioning, rollback, and
expiry-review workflows. Mirrors ARCHITECTURE.md
``anti_misuse_controls.technical`` ("rollback: previous N signed
packs retained on device").
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from pack_lifecycle import (  # type: ignore[import-not-found]
    EXPIRY_REVIEW_WINDOW_DAYS,
    MAX_RETAINED_VERSIONS,
    PackLifecycleError,
    PackStore,
    PackVersion,
)
from skill_passport import (  # type: ignore[import-not-found]
    ModelCompatibility,
    Reviewers,
    Signature,
    SkillPassport,
    TestResults,
    build_passport,
    generate_keypair,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers.
# ---------------------------------------------------------------------------
def _test_results() -> TestResults:
    return TestResults(
        child_safety_recall=0.99,
        child_safety_precision=0.95,
        privacy_leak_precision=0.95,
        scam_recall=0.90,
        protected_speech_false_positive=0.03,
        minority_language_false_positive=0.05,
        p95_latency_ms=180,
    )


def _passport(
    *,
    skill_id: str = "kchat.global.guardrail.baseline",
    skill_version: str = "1.0.0",
    expires_on: date | None = None,
    signed: bool = True,
) -> SkillPassport:
    p = build_passport(
        skill_id=skill_id,
        skill_version=skill_version,
        parent=None,
        authored_by="kchat_trust_and_safety",
        legal_reviewers=("legal_reviewer_a",),
        cultural_reviewers=("cultural_reviewer_a",),
        trust_and_safety_reviewers=("ts_reviewer_a",),
        model_compatibility=(
            ModelCompatibility(
                model_id="kchat-slm-q4", model_min_version="1.0.0"
            ),
        ),
        expires_on=expires_on or (date.today() + timedelta(days=180)),
        test_results=_test_results(),
    )
    if signed:
        sk, _pk = generate_keypair()
        p.sign(private_key=sk, key_id="test-key-id")
    return p


# ---------------------------------------------------------------------------
# Registration / retrieval.
# ---------------------------------------------------------------------------
class TestRegistration:
    def test_register_marks_active(self):
        store = PackStore()
        store.register(_passport())
        active = store.get_active("kchat.global.guardrail.baseline")
        assert active is not None
        assert active.is_active
        assert active.version == "1.0.0"
        assert active.signature_valid

    def test_register_demotes_previous_active(self):
        store = PackStore()
        store.register(_passport(skill_version="1.0.0"))
        store.register(_passport(skill_version="1.1.0"))
        history = store.get_history("kchat.global.guardrail.baseline")
        assert [v.version for v in history] == ["1.1.0", "1.0.0"]
        assert history[0].is_active
        assert not history[1].is_active

    def test_register_unsigned_passport_marks_signature_invalid(self):
        store = PackStore()
        store.register(_passport(signed=False))
        active = store.get_active("kchat.global.guardrail.baseline")
        assert active is not None
        assert active.signature_valid is False

    def test_get_history_returns_copy(self):
        store = PackStore()
        store.register(_passport())
        history = store.get_history("kchat.global.guardrail.baseline")
        history.clear()
        assert store.get_history("kchat.global.guardrail.baseline"), (
            "internal store must not be mutated by external list ops"
        )

    def test_register_rejects_missing_skill_id(self):
        bad = _passport()
        bad.skill_id = ""
        store = PackStore()
        with pytest.raises(PackLifecycleError):
            store.register(bad)

    def test_register_rejects_missing_skill_version(self):
        bad = _passport()
        bad.skill_version = ""
        store = PackStore()
        with pytest.raises(PackLifecycleError):
            store.register(bad)


# ---------------------------------------------------------------------------
# Version ordering.
# ---------------------------------------------------------------------------
class TestVersionOrdering:
    def test_history_is_newest_first(self):
        store = PackStore()
        for v in ("1.0.0", "1.1.0", "1.2.0"):
            store.register(_passport(skill_version=v))
        history = store.get_history("kchat.global.guardrail.baseline")
        assert [v.version for v in history] == ["1.2.0", "1.1.0", "1.0.0"]

    def test_re_register_same_version_replaces_in_place(self):
        store = PackStore()
        store.register(_passport(skill_version="1.0.0"))
        store.register(_passport(skill_version="1.1.0"))
        # Re-register 1.0.0 — should replace, not duplicate.
        store.register(_passport(skill_version="1.0.0"))
        history = store.get_history("kchat.global.guardrail.baseline")
        versions = [v.version for v in history]
        assert versions.count("1.0.0") == 1


# ---------------------------------------------------------------------------
# Rollback.
# ---------------------------------------------------------------------------
class TestRollback:
    def test_rollback_returns_previous(self):
        store = PackStore()
        store.register(_passport(skill_version="1.0.0"))
        store.register(_passport(skill_version="1.1.0"))
        store.register(_passport(skill_version="1.2.0"))
        rolled = store.rollback("kchat.global.guardrail.baseline")
        assert rolled is not None
        assert rolled.version == "1.1.0"
        assert rolled.is_active

    def test_rollback_twice_walks_further_back(self):
        store = PackStore()
        store.register(_passport(skill_version="1.0.0"))
        store.register(_passport(skill_version="1.1.0"))
        store.register(_passport(skill_version="1.2.0"))
        store.rollback("kchat.global.guardrail.baseline")
        rolled_again = store.rollback("kchat.global.guardrail.baseline")
        assert rolled_again is not None
        assert rolled_again.version == "1.0.0"

    def test_rollback_with_only_one_version_returns_none(self):
        store = PackStore()
        store.register(_passport(skill_version="1.0.0"))
        assert store.rollback("kchat.global.guardrail.baseline") is None
        # And the single version is still active.
        active = store.get_active("kchat.global.guardrail.baseline")
        assert active is not None and active.version == "1.0.0"

    def test_rollback_unknown_skill_id_returns_none(self):
        store = PackStore()
        assert store.rollback("kchat.does.not.exist") is None


# ---------------------------------------------------------------------------
# Retention cap.
# ---------------------------------------------------------------------------
class TestRetentionCap:
    def test_max_retained_versions_drops_oldest(self):
        store = PackStore()
        # Register N+1 versions; oldest must be dropped.
        for i in range(MAX_RETAINED_VERSIONS + 1):
            store.register(_passport(skill_version=f"1.{i}.0"))
        history = store.get_history("kchat.global.guardrail.baseline")
        assert len(history) == MAX_RETAINED_VERSIONS
        assert history[0].version == f"1.{MAX_RETAINED_VERSIONS}.0"
        assert "1.0.0" not in [v.version for v in history], (
            "oldest version must be dropped when retention cap exceeded"
        )

    def test_constants_are_at_documented_values(self):
        assert MAX_RETAINED_VERSIONS == 3
        assert EXPIRY_REVIEW_WINDOW_DAYS == 30

    def test_register_stale_version_when_full_returns_none(self):
        """If the store is already at MAX_RETAINED_VERSIONS and the
        caller registers a version older than every retained one, the
        new entry must NOT be silently dropped while still being
        returned, and the existing active version must NOT be
        demoted.
        """
        store = PackStore()
        # Fill the store with 3 newer versions (signed today).
        for v in ("1.1.0", "1.2.0", "1.3.0"):
            store.register(_passport(skill_version=v))
        assert (
            store.get_active("kchat.global.guardrail.baseline").version
            == "1.3.0"
        )

        # Register an older version dated yesterday so it sorts to the
        # tail of the candidate list and is dropped by the trim.
        stale = store.register(
            _passport(skill_version="1.0.0"),
            signed_on=date.today() - timedelta(days=1),
        )

        # Caller is told the version was not retained.
        assert stale is None

        # The active version is unchanged — we did not silently
        # demote 1.3.0 just because a stale pack was offered.
        active = store.get_active("kchat.global.guardrail.baseline")
        assert active is not None
        assert active.version == "1.3.0"
        assert active.is_active

        # The store still holds exactly the 3 newer versions, in
        # newest-first order.
        history = store.get_history("kchat.global.guardrail.baseline")
        assert [v.version for v in history] == ["1.3.0", "1.2.0", "1.1.0"]
        # And the stale version is definitely not in the store.
        assert "1.0.0" not in [v.version for v in history]


# ---------------------------------------------------------------------------
# Expiry checks.
# ---------------------------------------------------------------------------
class TestExpiry:
    def test_check_expiry_identifies_expired_pack(self):
        store = PackStore()
        store.register(
            _passport(
                skill_id="kchat.global.guardrail.baseline",
                expires_on=date(2026, 1, 1),
            )
        )
        # As of 2026-04-29 the pack is expired.
        flagged = store.check_expiry(now=date(2026, 4, 29))
        assert "kchat.global.guardrail.baseline" in flagged

    def test_check_expiry_identifies_soon_to_expire(self):
        store = PackStore()
        store.register(
            _passport(
                skill_id="kchat.jurisdiction.us.guardrail.v1",
                expires_on=date(2026, 5, 15),
            )
        )
        # 16 days out — within the 30-day review window.
        flagged = store.check_expiry(now=date(2026, 4, 29))
        assert "kchat.jurisdiction.us.guardrail.v1" in flagged

    def test_check_expiry_ignores_far_future_packs(self):
        store = PackStore()
        store.register(
            _passport(
                skill_id="kchat.jurisdiction.de.guardrail.v1",
                expires_on=date(2027, 10, 29),
            )
        )
        flagged = store.check_expiry(now=date(2026, 4, 29))
        assert "kchat.jurisdiction.de.guardrail.v1" not in flagged

    def test_deactivate_expired_marks_inactive(self):
        store = PackStore()
        store.register(
            _passport(
                skill_id="kchat.global.guardrail.baseline",
                expires_on=date(2026, 1, 1),
            )
        )
        store.register(
            _passport(
                skill_id="kchat.jurisdiction.us.guardrail.v1",
                expires_on=date(2027, 10, 29),
            )
        )
        deactivated = store.deactivate_expired(now=date(2026, 4, 29))
        assert deactivated == ["kchat.global.guardrail.baseline"]
        assert (
            store.get_active("kchat.global.guardrail.baseline") is None
        )
        assert (
            store.get_active("kchat.jurisdiction.us.guardrail.v1")
            is not None
        )

    def test_needs_review_returns_packs_in_window(self):
        store = PackStore()
        store.register(
            _passport(
                skill_id="kchat.jurisdiction.us.guardrail.v1",
                expires_on=date(2026, 5, 15),
            )
        )
        store.register(
            _passport(
                skill_id="kchat.jurisdiction.de.guardrail.v1",
                expires_on=date(2027, 10, 29),
            )
        )
        review = store.needs_review(now=date(2026, 4, 29))
        ids = [v.skill_id for v in review]
        assert "kchat.jurisdiction.us.guardrail.v1" in ids
        assert "kchat.jurisdiction.de.guardrail.v1" not in ids

    def test_needs_review_excludes_already_expired(self):
        """Already-expired packs belong to deactivate_expired, not the
        review queue."""
        store = PackStore()
        store.register(
            _passport(
                skill_id="kchat.jurisdiction.us.guardrail.v1",
                expires_on=date(2026, 1, 1),
            )
        )
        review = store.needs_review(now=date(2026, 4, 29))
        assert review == []


# ---------------------------------------------------------------------------
# JSON round-trip.
# ---------------------------------------------------------------------------
class TestJsonRoundTrip:
    def test_round_trip_preserves_state(self):
        store = PackStore()
        store.register(
            _passport(
                skill_id="kchat.jurisdiction.us.guardrail.v1",
                skill_version="1.0.0",
            )
        )
        store.register(
            _passport(
                skill_id="kchat.jurisdiction.us.guardrail.v1",
                skill_version="1.1.0",
            )
        )
        store.register(
            _passport(
                skill_id="kchat.jurisdiction.de.guardrail.v1",
                skill_version="1.0.0",
            )
        )

        raw = store.to_json()
        restored = PackStore.from_json(raw)

        for skill_id in (
            "kchat.jurisdiction.us.guardrail.v1",
            "kchat.jurisdiction.de.guardrail.v1",
        ):
            orig = store.get_history(skill_id)
            new = restored.get_history(skill_id)
            assert [v.to_dict() for v in orig] == [v.to_dict() for v in new]

    def test_from_json_rejects_non_object(self):
        with pytest.raises(PackLifecycleError):
            PackStore.from_json("[]")

    def test_from_json_rejects_missing_skills(self):
        with pytest.raises(PackLifecycleError):
            PackStore.from_json('{"schema_version":1}')


# ---------------------------------------------------------------------------
# Integration with SkillPassport.
# ---------------------------------------------------------------------------
class TestIntegrationWithSkillPassport:
    def test_register_real_signed_passport(self):
        sk, _pk = generate_keypair()
        passport = build_passport(
            skill_id="kchat.jurisdiction.jp.guardrail.v1",
            skill_version="1.0.0",
            parent="kchat.global.guardrail.baseline",
            authored_by="kchat_trust_and_safety",
            legal_reviewers=("legal_reviewer_jp",),
            cultural_reviewers=("cultural_reviewer_jp",),
            trust_and_safety_reviewers=("ts_reviewer_jp",),
            model_compatibility=(
                ModelCompatibility(
                    model_id="kchat-slm-q4", model_min_version="1.0.0"
                ),
            ),
            expires_on=date.today() + timedelta(days=200),
            test_results=_test_results(),
        )
        passport.sign(private_key=sk, key_id="jp-key-id")

        store = PackStore()
        version = store.register(passport)
        assert version.skill_id == "kchat.jurisdiction.jp.guardrail.v1"
        assert version.signature_valid is True
        assert version.is_active

    def test_all_active_returns_one_per_skill(self):
        store = PackStore()
        store.register(
            _passport(skill_id="kchat.jurisdiction.us.guardrail.v1")
        )
        store.register(
            _passport(
                skill_id="kchat.jurisdiction.us.guardrail.v1",
                skill_version="1.1.0",
            )
        )
        store.register(
            _passport(skill_id="kchat.jurisdiction.de.guardrail.v1")
        )
        actives = store.all_active()
        assert len(actives) == 2
        ids = {v.skill_id for v in actives}
        assert ids == {
            "kchat.jurisdiction.us.guardrail.v1",
            "kchat.jurisdiction.de.guardrail.v1",
        }


# ---------------------------------------------------------------------------
# PackVersion dataclass round-trip.
# ---------------------------------------------------------------------------
class TestPackVersionRoundTrip:
    def test_to_dict_from_dict(self):
        pv = PackVersion(
            skill_id="kchat.jurisdiction.us.guardrail.v1",
            version="1.0.0",
            signed_on=date(2026, 4, 29),
            expires_on=date(2027, 10, 29),
            signature_valid=True,
            is_active=True,
        )
        roundtripped = PackVersion.from_dict(pv.to_dict())
        assert roundtripped == pv
