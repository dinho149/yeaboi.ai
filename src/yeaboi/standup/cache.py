"""Persistent metadata cache for Standup activity enrichment.

Live activity lists are deliberately never cached. Only reusable metadata is:
repository discovery (short TTL), immutable commit file paths, and PR file paths
keyed by the PR's current revision/update marker.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

_CACHE_SCHEMA = """\
CREATE TABLE IF NOT EXISTS standup_metadata_cache (
    provider   TEXT NOT NULL,
    kind       TEXT NOT NULL,
    object_key TEXT NOT NULL,
    revision   TEXT NOT NULL,
    payload    TEXT NOT NULL,
    expires_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (provider, kind, object_key, revision)
);"""


class StandupMetadataCache:
    """Thread-safe SQLite cache shared by one Standup collection run."""

    def __init__(self, db_path: Path | str) -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.isolation_level = None
        self._conn.execute(_CACHE_SCHEMA)
        self._db_lock = threading.RLock()
        self._key_locks: dict[tuple[str, ...], threading.Lock] = {}
        self._memory: dict[tuple[str, ...], object] = {}

    def close(self) -> None:
        with self._db_lock:
            self._conn.close()

    def get(self, provider: str, kind: str, object_key: str, revision: str) -> object | None:
        with self._db_lock:
            row = self._conn.execute(
                """SELECT payload, expires_at FROM standup_metadata_cache
                   WHERE provider = ? AND kind = ? AND object_key = ? AND revision = ?""",
                (provider, kind, object_key, revision),
            ).fetchone()
        if row is None or (row[1] and row[1] < time.time()):
            return None
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return None

    def set(
        self,
        provider: str,
        kind: str,
        object_key: str,
        revision: str,
        payload: object,
        *,
        ttl_seconds: int = 0,
        replace_revisions: bool = False,
    ) -> None:
        now = time.time()
        expires_at = now + ttl_seconds if ttl_seconds else 0
        with self._db_lock:
            if replace_revisions:
                self._conn.execute(
                    """DELETE FROM standup_metadata_cache
                       WHERE provider = ? AND kind = ? AND object_key = ? AND revision <> ?""",
                    (provider, kind, object_key, revision),
                )
            self._conn.execute(
                """INSERT INTO standup_metadata_cache
                       (provider, kind, object_key, revision, payload, expires_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(provider, kind, object_key, revision) DO UPDATE SET
                       payload = excluded.payload,
                       expires_at = excluded.expires_at,
                       updated_at = excluded.updated_at""",
                (provider, kind, object_key, revision, json.dumps(payload), expires_at, now),
            )

    def get_or_compute(
        self,
        provider: str,
        kind: str,
        object_key: str,
        revision: str,
        compute: Callable[[], object],
        *,
        ttl_seconds: int = 0,
        cache_empty: bool = True,
        replace_revisions: bool = False,
    ) -> object:
        key = (provider, kind, object_key, revision)
        with self._db_lock:
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:
            cached = self.get(*key)
            if cached is not None:
                return cached
            value = compute()
            if cache_empty or value not in (None, [], {}, ""):
                self.set(
                    *key,
                    value,
                    ttl_seconds=ttl_seconds,
                    replace_revisions=replace_revisions,
                )
            return value

    def memoize(self, key: tuple[str, ...], compute: Callable[[], object]) -> object:
        """Single-flight process-local memo for non-serializable SDK objects."""
        lock_key = ("memory", *key)
        with self._db_lock:
            key_lock = self._key_locks.setdefault(lock_key, threading.Lock())
        with key_lock:
            with self._db_lock:
                if key in self._memory:
                    return self._memory[key]
            value = compute()
            with self._db_lock:
                self._memory[key] = value
            return value
