"""Tests for the device-local expiring counter store.

Module under test: ``kchat-skills/compiler/counters.py``. See
ARCHITECTURE.md "Community Labeling" (lines 537-561) for the spec.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from counters import (  # type: ignore[import-not-found]
    CounterStore,
    InMemoryKeystore,
    parse_window,
)


# ---------------------------------------------------------------------------
# Window parsing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "spec,expected",
    [
        ("24h", 24 * 3600),
        ("7d", 7 * 24 * 3600),
        ("30d", 30 * 24 * 3600),
        ("60s", 60),
        ("5m", 300),
        (3600, 3600),
        (0, 0),
    ],
)
def test_parse_window_valid(spec, expected):
    assert parse_window(spec) == expected


@pytest.mark.parametrize(
    "spec",
    ["bogus", "10x", "h", "", "-1h"],
)
def test_parse_window_invalid(spec):
    with pytest.raises((ValueError, TypeError)):
        parse_window(spec)


def test_parse_window_rejects_negative_int():
    with pytest.raises(ValueError):
        parse_window(-1)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def frozen_clock():
    """Mutable clock driven by the tests."""

    class Clock:
        now: float = 1_700_000_000.0

        def __call__(self) -> float:
            return self.now

        def advance(self, seconds: float) -> None:
            self.now += seconds

    return Clock()


@pytest.fixture
def store(frozen_clock):
    return CounterStore(
        default_window="24h",
        clock=frozen_clock,
    )


# ---------------------------------------------------------------------------
# Basic increment / get
# ---------------------------------------------------------------------------
def test_increment_and_get_count(store):
    store.increment("g1", "scam_links_24h")
    store.increment("g1", "scam_links_24h", delta=2)
    assert store.get_count("g1", "scam_links_24h") == 3


def test_default_delta_is_one(store):
    store.increment("g1", "x")
    assert store.get_count("g1", "x") == 1


def test_missing_counter_returns_zero(store):
    assert store.get_count("g1", "never-set") == 0


def test_delta_must_be_int(store):
    with pytest.raises(TypeError):
        store.increment("g1", "x", delta=1.5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        store.increment("g1", "x", delta=True)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["", None])
def test_group_id_and_counter_id_must_be_non_empty_strings(store, bad):
    with pytest.raises((ValueError, TypeError)):
        store.increment(bad, "cid")  # type: ignore[arg-type]
    with pytest.raises((ValueError, TypeError)):
        store.increment("gid", bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------
def test_entries_older_than_window_are_not_counted(store, frozen_clock):
    store.increment("g1", "c", delta=5)
    frozen_clock.advance(25 * 3600)  # > 24h
    assert store.get_count("g1", "c") == 0


def test_entries_within_window_still_counted(store, frozen_clock):
    store.increment("g1", "c", delta=5)
    frozen_clock.advance(1 * 3600)  # 1h < 24h
    assert store.get_count("g1", "c") == 5


def test_mixed_fresh_and_stale_entries(store, frozen_clock):
    store.increment("g1", "c", delta=2)
    frozen_clock.advance(23 * 3600)
    store.increment("g1", "c", delta=3)
    frozen_clock.advance(2 * 3600)  # original is now 25h old
    # Only the fresher entry should remain.
    assert store.get_count("g1", "c") == 3


def test_explicit_window_override(store, frozen_clock):
    store.increment("g1", "c")
    frozen_clock.advance(2 * 3600)  # 2h
    assert store.get_count("g1", "c", window="1h") == 0
    assert store.get_count("g1", "c", window="24h") == 1


def test_expire_drops_old_entries(store, frozen_clock):
    store.increment("g1", "c")
    frozen_clock.advance(25 * 3600)
    store.expire()
    # Internal state should have been cleared; group vanished.
    assert store._data == {}  # noqa: SLF001


# ---------------------------------------------------------------------------
# Label generation against thresholds
# ---------------------------------------------------------------------------
def test_get_label_below_any_threshold(store):
    thresholds = {"label_at": 3, "strong_label_at": 6, "escalate_at": 10}
    for _ in range(2):
        store.increment("g1", "scam_links_24h")
    assert store.get_label("g1", "scam_links_24h", thresholds) is None


def test_get_label_label_at(store):
    thresholds = {"label_at": 3, "strong_label_at": 6, "escalate_at": 10}
    for _ in range(3):
        store.increment("g1", "scam_links_24h")
    assert store.get_label("g1", "scam_links_24h", thresholds) == "label_at"


def test_get_label_strong_label_at(store):
    thresholds = {"label_at": 3, "strong_label_at": 6, "escalate_at": 10}
    for _ in range(6):
        store.increment("g1", "scam_links_24h")
    assert (
        store.get_label("g1", "scam_links_24h", thresholds)
        == "strong_label_at"
    )


def test_get_label_escalate_at(store):
    thresholds = {"label_at": 3, "strong_label_at": 6, "escalate_at": 10}
    for _ in range(10):
        store.increment("g1", "scam_links_24h")
    assert (
        store.get_label("g1", "scam_links_24h", thresholds)
        == "escalate_at"
    )


def test_get_label_handles_sparse_thresholds(store):
    # violence threats counter in ARCHITECTURE.md uses only label_at +
    # escalate_at.
    thresholds = {"label_at": 1, "escalate_at": 3}
    store.increment("g1", "violence_threats_7d")
    assert (
        store.get_label("g1", "violence_threats_7d", thresholds)
        == "label_at"
    )
    for _ in range(2):
        store.increment("g1", "violence_threats_7d")
    assert (
        store.get_label("g1", "violence_threats_7d", thresholds)
        == "escalate_at"
    )


# ---------------------------------------------------------------------------
# Group scoping
# ---------------------------------------------------------------------------
def test_counters_are_scoped_per_group(store):
    store.increment("group-a", "c")
    store.increment("group-a", "c")
    store.increment("group-b", "c")
    assert store.get_count("group-a", "c") == 2
    assert store.get_count("group-b", "c") == 1
    assert store.get_count("group-c", "c") == 0


def test_counters_are_scoped_per_counter_id(store):
    store.increment("g", "scam_links_24h")
    store.increment("g", "violence_threats_7d", delta=5)
    assert store.get_count("g", "scam_links_24h") == 1
    assert store.get_count("g", "violence_threats_7d", window="7d") == 5


# ---------------------------------------------------------------------------
# output_schema counter_updates consumption
# ---------------------------------------------------------------------------
def test_apply_counter_updates_from_output_schema(store):
    # This is exactly the shape of the `counter_updates` array in
    # kchat-skills/global/output_schema.json.
    updates = [
        {"counter_id": "group_scam_links_24h", "delta": 1},
        {"counter_id": "group_scam_links_24h", "delta": 1},
        {"counter_id": "group_violence_threats_7d", "delta": 2},
    ]
    store.apply_counter_updates("group-42", updates)
    assert store.get_count("group-42", "group_scam_links_24h") == 2
    assert (
        store.get_count(
            "group-42", "group_violence_threats_7d", window="7d"
        )
        == 2
    )


def test_apply_counter_updates_empty_list_is_noop(store):
    store.apply_counter_updates("g", [])
    assert store._data == {}  # noqa: SLF001


def test_apply_counter_updates_rejects_bad_delta_type(store):
    with pytest.raises(TypeError):
        store.apply_counter_updates(
            "g",
            [{"counter_id": "c", "delta": "1"}],  # type: ignore[list-item]
        )


def test_apply_counter_updates_rejects_empty_counter_id(store):
    with pytest.raises(ValueError):
        store.apply_counter_updates(
            "g", [{"counter_id": "", "delta": 1}]
        )


# ---------------------------------------------------------------------------
# Encrypted persistence
# ---------------------------------------------------------------------------
def test_store_persists_encrypted_on_disk(tmp_path, frozen_clock):
    key = b"\x00" * 32
    keystore = InMemoryKeystore(key=key)
    path = tmp_path / "counters.bin"

    s1 = CounterStore(
        path=path, keystore=keystore, clock=frozen_clock
    )
    s1.increment("g1", "c", delta=7)

    # File exists and is not plaintext JSON.
    assert path.exists()
    blob = path.read_bytes()
    with pytest.raises((ValueError, json.JSONDecodeError, UnicodeDecodeError)):
        json.loads(blob.decode("utf-8", errors="replace"))
    # Counter id / group id must not appear verbatim in the blob.
    assert b"g1" not in blob
    assert b'"c"' not in blob

    # New store reads the on-disk blob back correctly.
    s2 = CounterStore(
        path=path,
        keystore=InMemoryKeystore(key=key),
        clock=frozen_clock,
    )
    assert s2.get_count("g1", "c") == 7


def test_store_rejects_tampered_blob(tmp_path, frozen_clock):
    key = b"\x11" * 32
    keystore = InMemoryKeystore(key=key)
    path = tmp_path / "counters.bin"

    s1 = CounterStore(
        path=path, keystore=keystore, clock=frozen_clock
    )
    s1.increment("g1", "c", delta=3)
    blob = bytearray(path.read_bytes())
    blob[-1] ^= 0xFF  # flip a ciphertext bit
    path.write_bytes(bytes(blob))

    with pytest.raises(ValueError):
        CounterStore(
            path=path,
            keystore=InMemoryKeystore(key=key),
            clock=frozen_clock,
        )


def test_store_rejects_wrong_key(tmp_path, frozen_clock):
    path = tmp_path / "counters.bin"
    s1 = CounterStore(
        path=path,
        keystore=InMemoryKeystore(key=b"\x01" * 32),
        clock=frozen_clock,
    )
    s1.increment("g1", "c", delta=3)

    with pytest.raises(ValueError):
        CounterStore(
            path=path,
            keystore=InMemoryKeystore(key=b"\x02" * 32),
            clock=frozen_clock,
        )


# ---------------------------------------------------------------------------
# Safety invariant — the counter store is device-local, not a moderation
# bus. No public method may look like it ships state off-device.
# ---------------------------------------------------------------------------
def test_store_has_no_public_upload_methods():
    forbidden_prefixes = ("upload", "export", "send", "emit_remote", "publish")
    for attr in dir(CounterStore):
        if attr.startswith("_"):
            continue
        if not callable(getattr(CounterStore, attr, None)):
            continue
        for prefix in forbidden_prefixes:
            assert not attr.lower().startswith(prefix), (
                f"CounterStore exposes forbidden-looking method {attr!r}; "
                "the counter store is device-local and must not provide "
                "an upload primitive."
            )
