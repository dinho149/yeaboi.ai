"""Tests for paths.py — export-dir helpers, the YEABOI_HOME root override, and move_data_tree."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from yeaboi import paths

_HELPERS = [
    (paths.get_analysis_export_dir, "analysis"),
    (paths.get_planning_export_dir, "planning"),
    (paths.get_standup_export_dir, "standup"),
    (paths.get_retro_export_dir, "retro"),
    (paths.get_performance_export_dir, "performance"),
    (paths.get_reporting_export_dir, "reporting"),
]


class TestExportDirHelpers:
    @pytest.mark.parametrize(("helper", "subdir"), _HELPERS)
    def test_defaults_under_constant(self, helper, subdir, monkeypatch, tmp_path):
        # Monkeypatch the module constant (the pattern existing suites rely on)
        const = f"{subdir.upper()}_EXPORTS_DIR"
        monkeypatch.setattr(paths, const, tmp_path / subdir)
        d = helper("MyProj")
        assert d == tmp_path / subdir / "myproj"
        assert d.is_dir()

    def test_performance_empty_key_fallback(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paths, "PERFORMANCE_EXPORTS_DIR", tmp_path / "performance")
        assert paths.get_performance_export_dir("").name == "engineer"

    def test_reporting_empty_key_fallback(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paths, "REPORTING_EXPORTS_DIR", tmp_path / "reporting")
        assert paths.get_reporting_export_dir("").name == "report"


class TestGetDbPathPermissions:
    """get_db_path() hardens the data dir (0o700) and DB file (0o600)."""

    @pytest.fixture(autouse=True)
    def _isolated_db(self, monkeypatch, tmp_path):
        monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(paths, "DB_PATH", tmp_path / "data" / "sessions.db")
        monkeypatch.setattr(paths, "LEGACY_DB_PATH", tmp_path / "sessions.db")

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_creates_restricted_db_and_dir(self):
        db = paths.get_db_path()
        assert (db.stat().st_mode & 0o777) == 0o600
        assert (db.parent.stat().st_mode & 0o777) == 0o700

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_repairs_existing_lax_db(self, tmp_path):
        (tmp_path / "data").mkdir()
        db = tmp_path / "data" / "sessions.db"
        db.touch(mode=0o644)
        db.chmod(0o644)
        paths.get_db_path()
        assert (db.stat().st_mode & 0o777) == 0o600

    def test_legacy_rename_still_works(self, tmp_path):
        (tmp_path / "sessions.db").write_bytes(b"")
        db = paths.get_db_path()
        assert db == tmp_path / "data" / "sessions.db"
        assert db.exists()
        assert not (tmp_path / "sessions.db").exists()


class TestResolveRoot:
    """YEABOI_HOME relocates the whole data tree (resolved once at import time)."""

    def test_default_is_home_yeaboi(self, monkeypatch):
        monkeypatch.delenv("YEABOI_HOME", raising=False)
        assert paths._resolve_root() == Path.home() / ".yeaboi"

    def test_override_used_when_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("YEABOI_HOME", str(tmp_path / "custom"))
        assert paths._resolve_root() == tmp_path / "custom"

    def test_tilde_expansion(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("YEABOI_HOME", "~/yb-data")
        assert paths._resolve_root() == tmp_path / "yb-data"

    def test_blank_override_ignored(self, monkeypatch):
        monkeypatch.setenv("YEABOI_HOME", "   ")
        assert paths._resolve_root() == Path.home() / ".yeaboi"

    def test_env_file_pinned_to_default_home(self):
        # The bootstrap .env holds YEABOI_HOME itself, so it never moves.
        assert paths.ENV_FILE == paths.DEFAULT_ROOT_DIR / ".env"
        assert paths.DEFAULT_ROOT_DIR == Path.home() / ".yeaboi"


class TestMoveDataTree:
    def _make_src(self, monkeypatch, tmp_path) -> Path:
        src = tmp_path / "old-home"
        src.mkdir()
        monkeypatch.setenv("YEABOI_HOME", str(src))
        return src

    def test_moves_children(self, monkeypatch, tmp_path):
        src = self._make_src(monkeypatch, tmp_path)
        (src / "data").mkdir()
        (src / "data" / "sessions.db").write_text("db")
        (src / "repl-history").write_text("hist")
        dest = tmp_path / "new-home"

        ok, msg = paths.move_data_tree(dest)
        assert ok
        assert (dest / "data" / "sessions.db").read_text() == "db"
        assert (dest / "repl-history").exists()
        assert not (src / "data").exists()
        assert "2 item(s)" in msg

    def test_env_file_is_skipped(self, monkeypatch, tmp_path):
        src = self._make_src(monkeypatch, tmp_path)
        (src / ".env").write_text("SECRET=1")
        dest = tmp_path / "new-home"

        ok, _ = paths.move_data_tree(dest)
        assert ok
        assert (src / ".env").exists()
        assert not (dest / ".env").exists()

    def test_existing_destination_child_skipped(self, monkeypatch, tmp_path):
        src = self._make_src(monkeypatch, tmp_path)
        (src / "exports").mkdir()
        (src / "exports" / "a.md").write_text("src")
        dest = tmp_path / "new-home"
        (dest / "exports").mkdir(parents=True)

        ok, msg = paths.move_data_tree(dest)
        assert ok
        assert (src / "exports" / "a.md").exists()  # left in place, not clobbered
        assert "skipped" in msg

    def test_missing_source_is_noop(self, monkeypatch, tmp_path):
        monkeypatch.setenv("YEABOI_HOME", str(tmp_path / "never-created"))
        ok, msg = paths.move_data_tree(tmp_path / "new-home")
        assert ok
        assert "No existing data" in msg

    def test_same_location_is_noop(self, monkeypatch, tmp_path):
        src = self._make_src(monkeypatch, tmp_path)
        (src / "data").mkdir()
        ok, msg = paths.move_data_tree(src)
        assert ok
        assert (src / "data").exists()
        assert "nothing to move" in msg

    def test_default_source_when_no_override(self, monkeypatch, tmp_path):
        # With YEABOI_HOME unset the source is ~/.yeaboi (here: a faked HOME).
        monkeypatch.delenv("YEABOI_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(paths, "DEFAULT_ROOT_DIR", tmp_path / ".yeaboi")
        (tmp_path / ".yeaboi").mkdir()
        (tmp_path / ".yeaboi" / "scrum-docs").mkdir()
        dest = tmp_path / "elsewhere"

        ok, _ = paths.move_data_tree(dest)
        assert ok
        assert (dest / "scrum-docs").exists()
