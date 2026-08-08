"""Tests for the background PyPI update check (src/yeaboi/update_check.py)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from yeaboi import update_check


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Isolate the module-level check state between tests."""
    monkeypatch.setattr(update_check, "_state", {"latest": "", "checked": False})
    monkeypatch.setattr(update_check, "_started", False)
    monkeypatch.setattr(update_check, "_restart_to", "")
    # restarted_version() caches the marker on first read; without this the value
    # one test popped out of its fake environ would answer every later test.
    monkeypatch.setattr(update_check, "_restarted_from", None)


class TestParseVersion:
    def test_plain_semver(self):
        assert update_check.parse_version("2.10.0") == (2, 10, 0)

    def test_two_components(self):
        assert update_check.parse_version("1.2") == (1, 2)

    def test_rc_suffix_keeps_leading_digits(self):
        assert update_check.parse_version("2.10.0rc1") == (2, 10, 0)

    def test_dev_local_suffix_stripped(self):
        assert update_check.parse_version("0.0.0+dev") == (0, 0, 0)

    def test_garbage_returns_none(self):
        assert update_check.parse_version("not-a-version") is None

    def test_empty_returns_none(self):
        assert update_check.parse_version("") is None

    def test_partial_garbage_stops_at_bad_component(self):
        assert update_check.parse_version("2.x.0") == (2,)


class TestIsNewer:
    def test_newer(self):
        assert update_check.is_newer("2.11.0", "2.10.0") is True

    def test_equal(self):
        assert update_check.is_newer("2.10.0", "2.10.0") is False

    def test_older(self):
        assert update_check.is_newer("2.9.0", "2.10.0") is False

    def test_minor_vs_patch_ordering(self):
        assert update_check.is_newer("2.10.1", "2.10.0") is True
        assert update_check.is_newer("3.0.0", "2.99.99") is True

    def test_unparseable_never_flags(self):
        assert update_check.is_newer("garbage", "2.10.0") is False
        assert update_check.is_newer("2.11.0", "garbage") is False


class TestDetectUpgradeCommand:
    def test_uv_tool_install(self, monkeypatch):
        monkeypatch.setattr(update_check.sys, "executable", "/Users/x/.local/share/uv/tools/yeaboi/bin/python")
        assert update_check.detect_upgrade_command() == "uv tool upgrade yeaboi"

    def test_pipx_install(self, monkeypatch):
        monkeypatch.setattr(update_check.sys, "executable", "/Users/x/.local/pipx/venvs/yeaboi/bin/python")
        assert update_check.detect_upgrade_command() == "pipx upgrade yeaboi"

    def test_unknown_falls_back_to_uv(self, monkeypatch):
        monkeypatch.setattr(update_check.sys, "executable", "/usr/bin/python3")
        assert update_check.detect_upgrade_command() == "uv tool upgrade yeaboi"


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class TestFetchLatestVersion:
    def test_good_response(self, monkeypatch):
        body = json.dumps({"info": {"version": "2.11.0"}}).encode()
        monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda req, timeout: _FakeResponse(body))
        assert update_check.fetch_latest_version() == "2.11.0"

    def test_network_error_returns_none(self, monkeypatch):
        def _boom(req, timeout):
            raise urllib.error.URLError("offline")

        monkeypatch.setattr(update_check.urllib.request, "urlopen", _boom)
        assert update_check.fetch_latest_version() is None

    def test_malformed_json_returns_none(self, monkeypatch):
        monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda req, timeout: _FakeResponse(b"not json"))
        assert update_check.fetch_latest_version() is None

    def test_missing_key_returns_none(self, monkeypatch):
        body = json.dumps({"info": {}}).encode()
        monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda req, timeout: _FakeResponse(body))
        assert update_check.fetch_latest_version() is None

    def test_non_string_version_returns_none(self, monkeypatch):
        body = json.dumps({"info": {"version": 2}}).encode()
        monkeypatch.setattr(update_check.urllib.request, "urlopen", lambda req, timeout: _FakeResponse(body))
        assert update_check.fetch_latest_version() is None


class TestStartBackgroundCheck:
    def test_dev_version_never_spawns_thread(self, monkeypatch):
        monkeypatch.setattr(update_check, "_current_version", lambda: "0.0.0+dev")
        spawned = []
        monkeypatch.setattr(update_check.threading, "Thread", lambda **kw: spawned.append(kw) or _NoopThread())
        update_check.start_background_check()
        assert spawned == []
        assert update_check._started is True

    def test_spawns_daemon_thread_once(self, monkeypatch):
        monkeypatch.setattr(update_check, "_current_version", lambda: "2.10.0")
        spawned = []

        def _fake_thread(**kw):
            spawned.append(kw)
            return _NoopThread()

        monkeypatch.setattr(update_check.threading, "Thread", _fake_thread)
        update_check.start_background_check()
        update_check.start_background_check()  # idempotent — second call is a no-op
        assert len(spawned) == 1
        assert spawned[0]["daemon"] is True

    def test_worker_records_latest(self, monkeypatch):
        monkeypatch.setattr(update_check, "_current_version", lambda: "2.10.0")
        monkeypatch.setattr(update_check, "fetch_latest_version", lambda: "2.11.0")

        class _InlineThread(_NoopThread):
            def __init__(self, target=None, **kw):
                self._target = target

            def start(self):
                self._target()

        monkeypatch.setattr(update_check.threading, "Thread", lambda target=None, **kw: _InlineThread(target=target))
        update_check.start_background_check()
        assert update_check._state["latest"] == "2.11.0"
        assert update_check._state["checked"] is True

    def test_worker_handles_fetch_failure(self, monkeypatch):
        monkeypatch.setattr(update_check, "_current_version", lambda: "2.10.0")
        monkeypatch.setattr(update_check, "fetch_latest_version", lambda: None)

        class _InlineThread(_NoopThread):
            def __init__(self, target=None, **kw):
                self._target = target

            def start(self):
                self._target()

        monkeypatch.setattr(update_check.threading, "Thread", lambda target=None, **kw: _InlineThread(target=target))
        update_check.start_background_check()
        assert update_check._state["latest"] == ""
        assert update_check._state["checked"] is True


class _NoopThread:
    def __init__(self, *a, **kw):
        pass

    def start(self):
        pass


class TestGetUpdateStatus:
    def test_shape(self, monkeypatch):
        monkeypatch.setattr(update_check, "_current_version", lambda: "2.10.0")
        status = update_check.get_update_status()
        assert set(status) == {"current", "latest", "update_available", "upgrade_command", "is_dev"}
        assert status["current"] == "2.10.0"
        assert status["update_available"] is False
        assert status["is_dev"] is False

    def test_update_available_when_latest_newer(self, monkeypatch):
        monkeypatch.setattr(update_check, "_current_version", lambda: "2.10.0")
        update_check._state["latest"] = "2.11.0"
        assert update_check.get_update_status()["update_available"] is True

    def test_no_update_when_latest_equal(self, monkeypatch):
        monkeypatch.setattr(update_check, "_current_version", lambda: "2.10.0")
        update_check._state["latest"] = "2.10.0"
        assert update_check.get_update_status()["update_available"] is False

    def test_dev_flag(self, monkeypatch):
        monkeypatch.setattr(update_check, "_current_version", lambda: "0.0.0+dev")
        status = update_check.get_update_status()
        assert status["is_dev"] is True
        assert status["update_available"] is False


class TestRunUpgrade:
    """The ctrl+U in-app upgrade runner — never raises, reports (ok, message)."""

    def test_success_returns_ok_and_stdout(self, monkeypatch):
        import subprocess

        monkeypatch.setattr(update_check, "detect_upgrade_command", lambda: "uv tool upgrade yeaboi")

        def _fake_run(args, **kwargs):
            assert args == ["uv", "tool", "upgrade", "yeaboi"]
            return subprocess.CompletedProcess(args, 0, stdout="Updated yeaboi", stderr="")

        monkeypatch.setattr(subprocess, "run", _fake_run)
        ok, msg = update_check.run_upgrade()
        assert ok is True
        assert "Updated yeaboi" in msg

    def test_nonzero_exit_returns_failure_with_stderr(self, monkeypatch):
        import subprocess

        monkeypatch.setattr(update_check, "detect_upgrade_command", lambda: "pipx upgrade yeaboi")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda args, **kw: subprocess.CompletedProcess(args, 1, stdout="", stderr="no network"),
        )
        ok, msg = update_check.run_upgrade()
        assert ok is False
        assert "no network" in msg

    def test_launch_exception_is_swallowed(self, monkeypatch):
        import subprocess

        monkeypatch.setattr(update_check, "detect_upgrade_command", lambda: "uv tool upgrade yeaboi")

        def _boom(*a, **k):
            raise FileNotFoundError("uv not found")

        monkeypatch.setattr(subprocess, "run", _boom)
        ok, msg = update_check.run_upgrade()
        assert ok is False
        assert "uv not found" in msg


class TestRelaunchCommand:
    """resolve_relaunch_command — how the app re-launches itself after an upgrade."""

    def test_uses_argv0_when_it_is_a_real_file(self, monkeypatch, tmp_path):
        script = tmp_path / "yeaboi"
        script.write_text("#!/bin/sh\n")
        monkeypatch.setattr(update_check.os, "name", "posix")
        monkeypatch.setattr(update_check.sys, "argv", [str(script), "--dry-run"])
        assert update_check.resolve_relaunch_command() == [str(script.resolve()), "--dry-run"]

    def test_falls_back_to_path_lookup_when_argv0_is_a_bare_name(self, monkeypatch):
        monkeypatch.setattr(update_check.os, "name", "posix")
        monkeypatch.setattr(update_check.sys, "argv", ["yeaboi", "--theme", "dark"])
        monkeypatch.setattr(update_check.shutil, "which", lambda name: "/usr/local/bin/yeaboi")
        assert update_check.resolve_relaunch_command() == ["/usr/local/bin/yeaboi", "--theme", "dark"]

    def test_unresolvable_returns_none(self, monkeypatch):
        monkeypatch.setattr(update_check.os, "name", "posix")
        monkeypatch.setattr(update_check.sys, "argv", ["yeaboi"])
        monkeypatch.setattr(update_check.shutil, "which", lambda name: None)
        assert update_check.resolve_relaunch_command() is None

    def test_non_posix_never_execs(self, monkeypatch):
        # os.execv on Windows spawns instead of replacing — two apps, one terminal.
        monkeypatch.setattr(update_check.os, "name", "nt")
        monkeypatch.setattr(update_check.sys, "argv", ["yeaboi"])
        monkeypatch.setattr(update_check.shutil, "which", lambda name: "C:\\yeaboi.exe")
        assert update_check.resolve_relaunch_command() is None


class TestRestartRequest:
    """The flag the ctrl+U flow leaves for cli.main to act on."""

    def test_none_pending_by_default(self):
        assert update_check.restart_requested() == ""

    def test_request_records_the_version(self):
        update_check.request_restart("2.13.0")
        assert update_check.restart_requested() == "2.13.0"

    def test_versionless_request_still_reads_as_pending(self):
        update_check.request_restart("")
        assert update_check.restart_requested() == "1"


@pytest.fixture
def _fake_environ(monkeypatch):
    """Give the module a throwaway environ.

    ``restart_in_place`` sets the marker on the REAL ``os.environ`` (exec inherits
    it, which is the whole point), so without this the marker leaks into every
    later test in the session — and a leaked marker silently skips the splash.
    """
    env: dict[str, str] = {}
    monkeypatch.setattr(update_check.os, "environ", env)
    return env


class TestRestartInPlace:
    """restart_in_place — replaces the process, and only returns when it failed."""

    def test_execs_the_relaunch_command_with_the_version_in_the_env(self, monkeypatch, _fake_environ):
        update_check.request_restart("2.13.0")
        monkeypatch.setattr(update_check, "resolve_relaunch_command", lambda: ["/bin/yeaboi", "--dry-run"])
        seen: dict = {}

        def _fake_execv(path, argv):
            seen["path"] = path
            seen["argv"] = argv
            seen["env"] = update_check.os.environ.get(update_check._RESTART_ENV)

        monkeypatch.setattr(update_check.os, "execv", _fake_execv)
        update_check.restart_in_place()
        assert seen["path"] == "/bin/yeaboi"
        assert seen["argv"] == ["/bin/yeaboi", "--dry-run"]
        assert seen["env"] == "2.13.0"

    def test_no_relaunch_command_returns_false_without_exec(self, monkeypatch):
        monkeypatch.setattr(update_check, "resolve_relaunch_command", lambda: None)

        def _boom(*a, **k):
            raise AssertionError("must not exec without a resolved command")

        monkeypatch.setattr(update_check.os, "execv", _boom)
        assert update_check.restart_in_place() is False

    def test_exec_failure_reports_false_and_clears_the_marker(self, monkeypatch, _fake_environ):
        update_check.request_restart("2.13.0")
        monkeypatch.setattr(update_check, "resolve_relaunch_command", lambda: ["/bin/yeaboi"])

        def _boom(path, argv):
            raise OSError("exec format error")

        monkeypatch.setattr(update_check.os, "execv", _boom)
        assert update_check.restart_in_place() is False
        # The marker must not linger — this process is carrying on as the old version.
        assert update_check.os.environ.get(update_check._RESTART_ENV) is None


class TestRestartedVersion:
    """The marker the relaunched process reads back out of its environment."""

    def test_empty_on_a_normal_launch(self, _fake_environ):
        assert update_check.restarted_version() == ""

    def test_reports_the_version_after_a_restart(self, _fake_environ):
        _fake_environ[update_check._RESTART_ENV] = "2.13.0"
        assert update_check.restarted_version() == "2.13.0"

    def test_the_marker_is_taken_out_of_the_environment(self, _fake_environ):
        # It describes THIS process. Left in place every child we spawn inherits it,
        # and a nested yeaboi would come up believing it was the relaunch.
        _fake_environ[update_check._RESTART_ENV] = "2.13.0"
        assert update_check.restarted_version() == "2.13.0"
        assert update_check._RESTART_ENV not in _fake_environ
        # Still answers after the pop — the value is cached, not re-read.
        assert update_check.restarted_version() == "2.13.0"


class TestIsFreshRestart:
    """The predicate the splash skip and the ✓ updated chip both gate on."""

    def test_false_on_a_normal_launch(self, _fake_environ):
        assert update_check.is_fresh_restart() is False

    def test_true_when_the_marker_matches_the_running_version(self, monkeypatch, _fake_environ):
        monkeypatch.setattr(update_check, "_current_version", lambda: "2.13.0")
        _fake_environ[update_check._RESTART_ENV] = "2.13.0"
        assert update_check.is_fresh_restart() is True

    def test_false_when_the_marker_is_for_another_version(self, monkeypatch, _fake_environ):
        # A stale/foreign marker, or an upgrade that didn't move the version, must
        # fall back to a normal launch rather than suppress the splash forever.
        monkeypatch.setattr(update_check, "_current_version", lambda: "2.12.0")
        _fake_environ[update_check._RESTART_ENV] = "2.13.0"
        assert update_check.is_fresh_restart() is False
