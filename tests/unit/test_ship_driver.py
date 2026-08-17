"""Tests for the headless coding-agent driver (ship/driver.py).

The driver is exercised against a real subprocess — a Python shim standing in
for the ``claude`` binary — because the things it guards (prompt over stdin,
process-group kill, env stripping, lenient envelope parsing) are exactly the
things a mock would fake away.
"""

from __future__ import annotations

import json
import stat
import threading
import time

import pytest

from yeaboi.ship import driver


def _make_shim(tmp_path, body: str) -> str:
    """Write an executable Python shim that plays the claude binary."""
    shim = tmp_path / "claude-shim"
    shim.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    return str(shim)


ECHO_ENVELOPE = """
import json, sys
prompt = sys.stdin.read()
print(json.dumps({
    "type": "result",
    "result": f"got {len(prompt)} chars",
    "session_id": "sess-shim",
    "total_cost_usd": 0.12,
    "num_turns": 3,
    "is_error": False,
}))
"""


class TestRun:
    def test_success_parses_the_envelope(self, tmp_path):
        d = driver.ClaudeCodeDriver(binary=_make_shim(tmp_path, ECHO_ENVELOPE))
        result = d.run("x" * 500, tmp_path)
        assert result.ok
        assert result.output == "got 500 chars"  # the prompt travelled over stdin
        assert result.session_id == "sess-shim"
        assert result.cost_usd == pytest.approx(0.12)
        assert result.num_turns == 3
        assert result.warnings == ()

    def test_non_envelope_output_degrades_to_raw_text(self, tmp_path):
        shim = _make_shim(tmp_path, "import sys; sys.stdin.read(); print('plain text, no json')")
        result = driver.ClaudeCodeDriver(binary=shim).run("go", tmp_path)
        assert result.ok
        assert "plain text" in result.output
        assert any("JSON envelope" in w for w in result.warnings)

    def test_nonzero_exit_fails_with_the_tail(self, tmp_path):
        shim = _make_shim(tmp_path, "import sys; sys.stdin.read(); print('boom: quota'); sys.exit(2)")
        result = driver.ClaudeCodeDriver(binary=shim).run("go", tmp_path)
        assert not result.ok
        assert result.returncode == 2
        assert "boom: quota" in result.error

    def test_is_error_envelope_fails_even_on_exit_zero(self, tmp_path):
        body = "import sys, json; sys.stdin.read(); print(json.dumps({'result': 'declined', 'is_error': True}))"
        result = driver.ClaudeCodeDriver(binary=_make_shim(tmp_path, body)).run("go", tmp_path)
        assert not result.ok

    def test_timeout_kills_the_process(self, tmp_path):
        shim = _make_shim(tmp_path, "import sys, time; sys.stdin.read(); time.sleep(60)")
        started = time.monotonic()
        result = driver.ClaudeCodeDriver(binary=shim).run("go", tmp_path, timeout_s=0.5)
        assert result.timed_out
        assert not result.ok
        assert time.monotonic() - started < 15

    def test_cancel_event_stops_the_run(self, tmp_path):
        shim = _make_shim(tmp_path, "import sys, time; sys.stdin.read(); time.sleep(60)")
        cancel = threading.Event()
        threading.Timer(0.4, cancel.set).start()
        result = driver.ClaudeCodeDriver(binary=shim).run("go", tmp_path, timeout_s=60, cancel_event=cancel)
        assert result.cancelled
        assert not result.ok

    def test_session_env_vars_are_stripped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_SESSION_ID", "outer-session")
        monkeypatch.setenv("CLAUDECODE", "1")
        body = (
            "import sys, os, json; sys.stdin.read(); "
            "print(json.dumps({'result': repr(sorted(k for k in os.environ if k.startswith('CLAUDE')))}))"
        )
        result = driver.ClaudeCodeDriver(binary=_make_shim(tmp_path, body)).run("go", tmp_path)
        assert "CLAUDE_SESSION_ID" not in result.output
        assert "CLAUDECODE" not in result.output

    def test_on_line_sees_output_and_may_raise(self, tmp_path):
        shim = _make_shim(tmp_path, "import sys; sys.stdin.read(); print('line one'); print('line two')")
        seen: list[str] = []

        def _cb(line: str) -> None:
            seen.append(line)
            raise RuntimeError("UI hiccup")  # must never kill the pump

        result = driver.ClaudeCodeDriver(binary=shim).run("go", tmp_path, on_line=_cb)
        assert result.ok
        assert "line one" in seen

    def test_missing_binary_reports_instead_of_raising(self, tmp_path):
        result = driver.ClaudeCodeDriver(binary=str(tmp_path / "nope")).run("go", tmp_path)
        assert not result.ok
        assert "could not launch" in result.error

    def test_the_permission_envelope_is_mechanical_not_prose(self, tmp_path):
        # The "nothing pushes without approval" guarantee rests on these argv
        # flags: acceptEdits (files editable headlessly, Bash denied) plus an
        # explicit git-push/gh deny list. Losing them would leave only a
        # sentence in the prompt between the agent and `git push`.
        body = "import sys, json; sys.stdin.read(); print(json.dumps({'result': ' '.join(sys.argv[1:])}))"
        result = driver.ClaudeCodeDriver(binary=_make_shim(tmp_path, body)).run("go", tmp_path)
        assert "--permission-mode acceptEdits" in result.output
        assert "--disallowedTools Bash(git push:*) Bash(gh:*)" in result.output


class TestAvailability:
    def test_missing_binary(self):
        ok, detail = driver.ClaudeCodeDriver(binary="definitely-not-a-real-binary").available()
        assert not ok
        assert "not found" in detail

    def test_probe_reads_the_version(self, tmp_path, monkeypatch):
        shim = _make_shim(tmp_path, "print('9.9.9 (shim)')")
        monkeypatch.setenv("PATH", str(tmp_path), prepend=":")
        ok, detail = driver.ClaudeCodeDriver(binary=shim).available()
        assert ok
        assert "9.9.9" in detail


class TestEnvelopeParsing:
    def test_whole_output_json(self):
        assert driver._parse_envelope('{"result": "hi"}') == {"result": "hi"}

    def test_json_after_progress_noise(self):
        raw = "warming up...\nstill going\n" + json.dumps({"result": "done"})
        assert driver._parse_envelope(raw) == {"result": "done"}

    def test_garbage_is_empty_not_an_error(self):
        assert driver._parse_envelope("complete nonsense {broken") == {}
        assert driver._parse_envelope("") == {}
        assert driver._parse_envelope("[1, 2]") == {}
