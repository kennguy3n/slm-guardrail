"""Device-local, group-scoped, expiring counter store for KChat guardrail.

Spec reference: ARCHITECTURE.md "Community Labeling" (lines 537-561) and
community-overlay ``group_risk_counters`` blocks.

Key properties:

* **Device-local.** All state lives on the user's device. The store
  refuses to expose any upload primitive.
* **Group-scoped.** Each ``(group_id, counter_id)`` pair is independent,
  so different groups cannot interfere with each other's counters.
* **Expiring.** Every increment is a timestamped entry; entries older
  than the configured window are dropped at read time.
* **Encrypted at rest.** Persistence uses a pluggable ``DeviceKeystore``
  (the real implementation wraps the platform secure enclave — Android
  Keystore, iOS Keychain, etc.). The reference implementation in this
  module keeps the encryption contract exercised by tests without
  adding a runtime ``cryptography`` dependency.
* **SLM output schema aware.** :py:meth:`CounterStore.apply_counter_updates`
  consumes the ``counter_updates`` array defined in
  ``kchat-skills/global/output_schema.json``.

This module is the Phase 1 local-expiring-counter implementation
referenced by PHASES.md and the community-overlay ``group_risk_counters``
block in ARCHITECTURE.md.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional, Protocol

# Human-readable window aliases. Community overlays use strings like
# "24h" and "7d"; the store also accepts a raw int (seconds).
_WINDOW_ALIASES: dict[str, int] = {
    "24h": 24 * 3600,
    "7d": 7 * 24 * 3600,
    "30d": 30 * 24 * 3600,
}

_UNIT_MULTIPLIERS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}


def parse_window(window: int | str) -> int:
    """Parse a window spec into seconds.

    Accepts integers (already-seconds) and the community-overlay
    shorthand ``"<N><unit>"`` where unit is ``s`` / ``m`` / ``h`` /
    ``d`` (e.g. ``"24h"``, ``"7d"``, ``"30m"``). Raises ``ValueError``
    for any other form.
    """
    if isinstance(window, bool):  # bool is a subclass of int; reject.
        raise TypeError("window must be int or str, got bool")
    if isinstance(window, int):
        if window < 0:
            raise ValueError("window must be >= 0 seconds")
        return window
    if isinstance(window, str):
        if window in _WINDOW_ALIASES:
            return _WINDOW_ALIASES[window]
        if len(window) < 2:
            raise ValueError(f"Unrecognized window spec: {window!r}")
        unit = window[-1].lower()
        if unit not in _UNIT_MULTIPLIERS:
            raise ValueError(f"Unrecognized window unit in {window!r}")
        try:
            n = int(window[:-1])
        except ValueError as exc:
            raise ValueError(
                f"Unrecognized window spec: {window!r}"
            ) from exc
        if n < 0:
            raise ValueError("window must be >= 0 seconds")
        return n * _UNIT_MULTIPLIERS[unit]
    raise TypeError(
        f"window must be int or str, got {type(window).__name__}"
    )


class DeviceKeystore(Protocol):
    """Abstract device keystore.

    Real implementations wrap the platform secure-enclave API
    (Android Keystore, iOS Keychain, etc.) and MUST never expose raw
    key material off-device. The store uses the returned 32-byte key
    as the symmetric encryption key for at-rest blobs.
    """

    def get_counter_key(self) -> bytes: ...


class InMemoryKeystore:
    """Reference ``DeviceKeystore`` for tests and non-mobile hosts.

    Holds a 32-byte key in process memory. Not a production keystore.
    """

    def __init__(self, key: bytes | None = None) -> None:
        if key is None:
            key = os.urandom(32)
        if len(key) != 32:
            raise ValueError("key must be exactly 32 bytes")
        self._key = key

    def get_counter_key(self) -> bytes:
        return self._key


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    stream = bytearray()
    counter = 0
    while len(stream) < length:
        block = hashlib.sha256(
            key + nonce + counter.to_bytes(8, "big")
        ).digest()
        stream.extend(block)
        counter += 1
    return bytes(stream[:length])


def _encrypt(plaintext: bytes, key: bytes) -> bytes:
    """XOR-stream + HMAC-SHA256 encryption of ``plaintext``.

    .. warning::
       This is a reference implementation. Real device storage uses
       platform AEAD (AES-GCM / ChaCha20-Poly1305) from the secure
       enclave. This routine exists so the encrypt-at-rest contract
       can be exercised in tests without adding a ``cryptography``
       dependency to the repo.
    """
    nonce = os.urandom(16)
    stream = _keystream(key, nonce, len(plaintext))
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, stream))
    mac = hmac.new(
        key, nonce + ciphertext, hashlib.sha256
    ).digest()
    return nonce + mac + ciphertext


def _decrypt(blob: bytes, key: bytes) -> bytes:
    if len(blob) < 16 + 32:
        raise ValueError("ciphertext too short")
    nonce = blob[:16]
    mac = blob[16:48]
    ciphertext = blob[48:]
    expected_mac = hmac.new(
        key, nonce + ciphertext, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(mac, expected_mac):
        raise ValueError("ciphertext MAC mismatch")
    stream = _keystream(key, nonce, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, stream))


@dataclass
class CounterEntry:
    """A single timestamped increment."""

    ts: float  # epoch seconds
    delta: int


@dataclass
class CounterStore:
    """Device-local, group-scoped, expiring counter store.

    Parameters
    ----------
    path
        On-disk path for the encrypted counter blob. If ``None``, the
        store operates in memory only (suitable for tests or fully
        ephemeral scenarios).
    keystore
        ``DeviceKeystore`` used to derive the at-rest encryption key.
    default_window
        Default window applied to ``get_count`` / ``get_label`` when no
        per-call window is provided. Accepts int seconds or strings
        like ``"24h"``, ``"7d"``.
    clock
        Callable returning the current epoch seconds. Overridable for
        deterministic testing.
    """

    path: Optional[Path] = None
    keystore: DeviceKeystore = field(default_factory=InMemoryKeystore)
    default_window: int | str = "24h"
    clock: Callable[[], float] = field(default=time.time)
    _data: dict[str, dict[str, list[CounterEntry]]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        self._default_window_s = parse_window(self.default_window)
        if self.path is not None:
            self.path = Path(self.path)
            if self.path.exists():
                self._load()

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------
    def increment(
        self,
        group_id: str,
        counter_id: str,
        delta: int = 1,
        ts: float | None = None,
    ) -> None:
        """Increment ``(group_id, counter_id)`` by ``delta``.

        Each increment is stored as a timestamped entry; expiry applies
        at read time per the configured window.
        """
        if not isinstance(group_id, str) or not group_id:
            raise ValueError("group_id must be a non-empty string")
        if not isinstance(counter_id, str) or not counter_id:
            raise ValueError("counter_id must be a non-empty string")
        if isinstance(delta, bool) or not isinstance(delta, int):
            raise TypeError("delta must be int")
        if ts is None:
            ts = float(self.clock())
        group = self._data.setdefault(group_id, {})
        group.setdefault(counter_id, []).append(
            CounterEntry(ts=ts, delta=delta)
        )
        if self.path is not None:
            self._persist()

    def apply_counter_updates(
        self,
        group_id: str,
        counter_updates: Iterable[Mapping[str, object]],
        ts: float | None = None,
    ) -> None:
        """Apply a batch of ``counter_updates`` from the SLM output.

        ``counter_updates`` items must match the shape defined by
        ``kchat-skills/global/output_schema.json``:
        each entry has a non-empty ``counter_id`` string and an
        integer ``delta``.
        """
        for update in counter_updates:
            cid = update["counter_id"]
            delta = update["delta"]
            if not isinstance(cid, str) or not cid:
                raise ValueError(
                    "counter_updates[].counter_id must be a non-empty string"
                )
            if isinstance(delta, bool) or not isinstance(delta, int):
                raise TypeError("counter_updates[].delta must be int")
            self.increment(group_id, cid, delta=delta, ts=ts)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------
    def get_count(
        self,
        group_id: str,
        counter_id: str,
        window: int | str | None = None,
    ) -> int:
        """Return the sum of live deltas in the window.

        Expired entries are dropped in-place so the store does not
        grow unboundedly.
        """
        window_s = (
            self._default_window_s
            if window is None
            else parse_window(window)
        )
        now = float(self.clock())
        entries = self._data.get(group_id, {}).get(counter_id, [])
        cutoff = now - window_s
        live = [e for e in entries if e.ts >= cutoff]
        # Only persist-prune when using the configured default window;
        # an explicit shorter window must not destroy entries that are
        # still valid under the default window.
        if window is None and len(live) != len(entries):
            self._data.setdefault(group_id, {})[counter_id] = live
            if self.path is not None:
                self._persist()
        return sum(e.delta for e in live)

    def get_label(
        self,
        group_id: str,
        counter_id: str,
        thresholds: Mapping[str, int],
        window: int | str | None = None,
    ) -> str | None:
        """Return the most severe label whose threshold is met.

        Recognized keys, in order of decreasing severity:
        ``escalate_at``, ``strong_label_at``, ``label_at``. Returns
        ``None`` if no threshold is crossed.
        """
        count = self.get_count(
            group_id, counter_id, window=window
        )
        for key in ("escalate_at", "strong_label_at", "label_at"):
            threshold = thresholds.get(key)
            if threshold is None:
                continue
            if count >= threshold:
                return key
        return None

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------
    def expire(self, window: int | str | None = None) -> None:
        """Drop entries older than ``window`` for every counter."""
        window_s = (
            self._default_window_s
            if window is None
            else parse_window(window)
        )
        now = float(self.clock())
        cutoff = now - window_s
        for group_id in list(self._data.keys()):
            counters = self._data[group_id]
            for cid in list(counters.keys()):
                entries = counters[cid]
                live = [e for e in entries if e.ts >= cutoff]
                if live:
                    counters[cid] = live
                else:
                    del counters[cid]
            if not counters:
                del self._data[group_id]
        if self.path is not None:
            self._persist()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _serialize(self) -> bytes:
        payload = {
            gid: {
                cid: [
                    {"ts": e.ts, "delta": e.delta} for e in entries
                ]
                for cid, entries in counters.items()
            }
            for gid, counters in self._data.items()
        }
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    def _deserialize(self, blob: bytes) -> None:
        if not blob:
            self._data = {}
            return
        payload = json.loads(blob.decode("utf-8"))
        self._data = {
            gid: {
                cid: [
                    CounterEntry(ts=e["ts"], delta=e["delta"])
                    for e in entries
                ]
                for cid, entries in counters.items()
            }
            for gid, counters in payload.items()
        }

    def _persist(self) -> None:
        assert self.path is not None
        plaintext = self._serialize()
        key = self.keystore.get_counter_key()
        ciphertext = _encrypt(plaintext, key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=".counters."
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(ciphertext)
            tmp.replace(self.path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _load(self) -> None:
        assert self.path is not None
        blob = self.path.read_bytes()
        if not blob:
            return
        key = self.keystore.get_counter_key()
        plaintext = _decrypt(blob, key)
        self._deserialize(plaintext)


__all__ = [
    "CounterEntry",
    "CounterStore",
    "DeviceKeystore",
    "InMemoryKeystore",
    "parse_window",
]
