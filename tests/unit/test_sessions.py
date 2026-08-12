"""Tests for sessions.py — SessionStore file hardening and schema bookkeeping.

(The store's behaviour is covered indirectly across the mode suites; this file
holds the direct SessionStore unit tests, starting with the security bits.)
"""

import os
import sqlite3
import stat

import pytest

from yeaboi.sessions import CURRENT_SCHEMA_VERSION, SessionStore


class TestSessionStoreFilePermissions:
    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_db_file_restricted_on_connect(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        store = SessionStore(db_path)
        try:
            assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
        finally:
            store._conn.close()

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_existing_lax_db_repaired(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        db_path.touch(mode=0o644)
        db_path.chmod(0o644)
        store = SessionStore(db_path)
        try:
            assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
        finally:
            store._conn.close()


class TestSchemaInfoSingleRow:
    """schema_info is a single-row table only by convention — opens must enforce it.

    Concurrent first-opens (TUI + MCP server + scheduler on the shared DB) race
    the stamp INSERT, leaving duplicate rows and making the version read
    arbitrary — one observed DB held 37 rows. Every open now dedupes to the
    single highest-version row.
    """

    def _rows(self, db_path):
        conn = sqlite3.connect(str(db_path))
        try:
            return [r[0] for r in conn.execute("SELECT schema_version FROM schema_info")]
        finally:
            conn.close()

    def _insert_rows(self, db_path, versions):
        conn = sqlite3.connect(str(db_path))
        for v in versions:
            conn.execute("INSERT INTO schema_info (schema_version) VALUES (?)", (v,))
        conn.commit()
        conn.close()

    def test_duplicate_rows_are_deduped_on_open(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        SessionStore(db_path).close()
        self._insert_rows(db_path, [CURRENT_SCHEMA_VERSION] * 30 + [1, 20, 25])

        store = SessionStore(db_path)
        try:
            assert not store.schema_mismatch
        finally:
            store.close()
        assert self._rows(db_path) == [CURRENT_SCHEMA_VERSION]

    def test_dedupe_keeps_the_newest_stamp(self, tmp_path):
        # A row stamped by a newer build must survive the dedupe, so the
        # newer-DB-older-code warning still fires and nothing downgrades it.
        db_path = tmp_path / "sessions.db"
        SessionStore(db_path).close()
        self._insert_rows(db_path, [CURRENT_SCHEMA_VERSION + 1, 3])

        store = SessionStore(db_path)
        try:
            assert store.schema_mismatch
        finally:
            store.close()
        assert self._rows(db_path) == [CURRENT_SCHEMA_VERSION + 1]

    def test_single_row_open_is_untouched(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        SessionStore(db_path).close()
        SessionStore(db_path).close()
        assert self._rows(db_path) == [CURRENT_SCHEMA_VERSION]
