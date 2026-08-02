"""Tests for the reveal-in-file-manager helper.

Every path here must return a message rather than raise: the callers are inside
a TUI frame loop, where an exception is a crash.
"""

from __future__ import annotations

import subprocess

from yeaboi import os_open


def _fake_run(returncode: int = 0):
    class _Proc:
        pass

    proc = _Proc()
    proc.returncode = returncode
    proc.stdout = b""
    proc.stderr = b""
    return lambda *a, **k: proc


class TestOpenPath:
    def test_opens_an_existing_directory(self, tmp_path, monkeypatch):
        calls: list[list[str]] = []

        def _run(cmd, **kwargs):
            calls.append(cmd)
            return _fake_run()()

        monkeypatch.setattr(os_open.sys, "platform", "darwin")
        monkeypatch.setattr(os_open.shutil, "which", lambda name: "/usr/bin/open")
        monkeypatch.setattr(os_open.subprocess, "run", _run)
        assert os_open.open_path(tmp_path) == f"Opened {tmp_path}"
        assert calls == [["open", str(tmp_path)]]

    def test_linux_uses_xdg_open(self, tmp_path, monkeypatch):
        calls: list[list[str]] = []

        def _run(cmd, **kwargs):
            calls.append(cmd)
            return _fake_run()()

        monkeypatch.setattr(os_open.sys, "platform", "linux")
        monkeypatch.setattr(os_open.shutil, "which", lambda name: "/usr/bin/xdg-open")
        monkeypatch.setattr(os_open.subprocess, "run", _run)
        os_open.open_path(tmp_path)
        assert calls[0][0] == "xdg-open"

    def test_missing_path_says_so(self, tmp_path):
        target = tmp_path / "nope"
        assert os_open.open_path(target) == f"Not found: {target}"

    def test_no_opener_still_names_the_path(self, tmp_path, monkeypatch):
        """A headless box must still tell the user where to look."""
        monkeypatch.setattr(os_open.sys, "platform", "darwin")
        monkeypatch.setattr(os_open.shutil, "which", lambda name: None)
        assert str(tmp_path) in os_open.open_path(tmp_path)

    def test_unsupported_platform_still_names_the_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os_open.sys, "platform", "sunos5")
        assert str(tmp_path) in os_open.open_path(tmp_path)

    def test_nonzero_exit_degrades(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os_open.sys, "platform", "darwin")
        monkeypatch.setattr(os_open.shutil, "which", lambda name: "/usr/bin/open")
        monkeypatch.setattr(os_open.subprocess, "run", _fake_run(returncode=1))
        assert "Couldn't open it" in os_open.open_path(tmp_path)

    def test_windows_nonzero_exit_is_success(self, tmp_path, monkeypatch):
        """explorer.exe returns 1 even when it worked."""
        monkeypatch.setattr(os_open.sys, "platform", "win32")
        monkeypatch.setattr(os_open.shutil, "which", lambda name: "explorer.exe")
        monkeypatch.setattr(os_open.subprocess, "run", _fake_run(returncode=1))
        assert os_open.open_path(tmp_path) == f"Opened {tmp_path}"

    def test_timeout_never_raises(self, tmp_path, monkeypatch):
        def _boom(*a, **k):
            raise subprocess.TimeoutExpired("open", 10)

        monkeypatch.setattr(os_open.sys, "platform", "darwin")
        monkeypatch.setattr(os_open.shutil, "which", lambda name: "/usr/bin/open")
        monkeypatch.setattr(os_open.subprocess, "run", _boom)
        assert "Couldn't open it" in os_open.open_path(tmp_path)

    def test_expands_a_tilde_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(os_open.sys, "platform", "darwin")
        monkeypatch.setattr(os_open.shutil, "which", lambda name: "/usr/bin/open")
        monkeypatch.setattr(os_open.subprocess, "run", _fake_run())
        assert os_open.open_path("~") == f"Opened {tmp_path}"
