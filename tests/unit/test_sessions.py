"""Tests for sessions.py — SessionStore file hardening.

(The store's behaviour is covered indirectly across the mode suites; this file
holds the direct SessionStore unit tests, starting with the security bits.)
"""

import os
import stat

import pytest

from yeaboi.sessions import SessionStore


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
