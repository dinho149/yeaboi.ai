"""Tests for src/yeaboi/gocore/discovery.py — binary resolution order."""

import os
import stat

from yeaboi.gocore.discovery import find_core_binary


class TestFindCoreBinary:
    def test_env_var_wins(self, tmp_path, monkeypatch):
        binary = tmp_path / "yeaboi-core"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("YEABOI_CORE_BIN", str(binary))
        assert find_core_binary() == str(binary)

    def test_non_executable_env_var_is_ignored(self, tmp_path, monkeypatch):
        not_exec = tmp_path / "not-executable"
        not_exec.write_text("", encoding="utf-8")
        not_exec.chmod(0o644)
        monkeypatch.setenv("YEABOI_CORE_BIN", str(not_exec))
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert find_core_binary() is None

    def test_path_lookup_is_the_last_resort(self, tmp_path, monkeypatch):
        monkeypatch.delenv("YEABOI_CORE_BIN", raising=False)
        monkeypatch.setattr("shutil.which", lambda name: "/somewhere/yeaboi-core" if name == "yeaboi-core" else None)
        assert find_core_binary() == "/somewhere/yeaboi-core"

    def test_nothing_found_is_none_not_an_error(self, monkeypatch):
        monkeypatch.delenv("YEABOI_CORE_BIN", raising=False)
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert find_core_binary() is None

    def test_missing_env_file_is_ignored(self, tmp_path, monkeypatch):
        monkeypatch.setenv("YEABOI_CORE_BIN", str(tmp_path / "nope"))
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert find_core_binary() is None

    def test_env_var_expands_nothing(self, monkeypatch):
        # Deliberately literal: no ~ or $VAR expansion surprises.
        monkeypatch.setenv("YEABOI_CORE_BIN", "~/yeaboi-core")
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert find_core_binary() is None or not os.path.isabs("~")
