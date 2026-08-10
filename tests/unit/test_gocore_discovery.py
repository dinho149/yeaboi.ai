"""Tests for src/yeaboi/gocore/discovery.py — binary resolution order."""

import os
import stat
from pathlib import Path

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


class TestWorkingDirectoryIsNeverTrusted:
    """shutil.which searches the CWD on Windows, and discovery is automatic.

    CPython prepends os.curdir to the search path on win32, so a planted
    ./yeaboi-core.exe would outrank PATH and be spawned with no prompt.
    """

    def test_a_binary_in_the_cwd_is_refused(self, tmp_path, monkeypatch):
        planted = tmp_path / "yeaboi-core"
        planted.write_text("#!/bin/sh\n", encoding="utf-8")
        planted.chmod(planted.stat().st_mode | stat.S_IEXEC)
        monkeypatch.delenv("YEABOI_CORE_BIN", raising=False)
        monkeypatch.chdir(tmp_path)
        # What which() returns on win32 for a curdir hit.
        monkeypatch.setattr("shutil.which", lambda name: str(Path(".") / name))
        assert find_core_binary() is None

    def test_an_absolute_cwd_hit_is_also_refused(self, tmp_path, monkeypatch):
        planted = tmp_path / "yeaboi-core"
        planted.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.delenv("YEABOI_CORE_BIN", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: str(planted))
        assert find_core_binary() is None

    def test_the_explicit_env_var_still_reaches_a_cwd_binary(self, tmp_path, monkeypatch):
        # The escape hatch: naming it is intent, finding it is not.
        binary = tmp_path / "yeaboi-core"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")
        binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("YEABOI_CORE_BIN", str(binary))
        assert find_core_binary() == str(binary)

    def test_a_binary_elsewhere_on_path_is_still_used(self, tmp_path, monkeypatch):
        monkeypatch.delenv("YEABOI_CORE_BIN", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/yeaboi-core")
        assert find_core_binary() == "/usr/local/bin/yeaboi-core"
