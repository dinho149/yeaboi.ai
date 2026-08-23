"""run_app — the process entry point, driven on a worker thread over real HTTP."""

from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from yeaboi.app.handshake import parse_ready_line
from yeaboi.app.run import run_app


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("yeaboi.paths.get_run_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def no_dispatcher(monkeypatch):
    """Skip the MCP dispatcher — its startup imports every tool module."""
    from yeaboi.app.dispatch import DispatcherUnavailableError, McpDispatcher

    def fail_fast(self, timeout=30):
        raise DispatcherUnavailableError("disabled for test")

    monkeypatch.setattr(McpDispatcher, "start", fail_fast)


@pytest.fixture
def interactive_reset():
    """run_app flips fs_policy to interactive; put it back for other tests."""
    from yeaboi import fs_policy

    yield
    fs_policy.set_interactive(False)


class TestRunApp:
    def test_full_lifecycle_over_the_api(self, run_dir, no_dispatcher, interactive_reset):
        lines: list[str] = []
        exit_codes: list[int] = []
        thread = threading.Thread(target=lambda: exit_codes.append(run_app(_emit=lines.append)), daemon=True)
        thread.start()

        deadline = threading.Event()
        for _ in range(200):
            if lines:
                break
            deadline.wait(0.05)
        assert lines, "run_app never emitted a handshake"
        handshake = parse_ready_line(lines[0])

        with urllib.request.urlopen(f"{handshake.url}/api/health", timeout=5) as resp:
            assert json.loads(resp.read())["pid"] == handshake.pid

        stop = urllib.request.Request(
            f"{handshake.url}/api/shutdown",
            method="POST",
            headers={"Authorization": f"Bearer {handshake.token}"},
        )
        with urllib.request.urlopen(stop, timeout=5) as resp:
            assert resp.status == 200

        thread.join(timeout=15)
        assert not thread.is_alive(), "run_app did not stop after /api/shutdown"
        assert exit_codes == [0]
        # Teardown removed both run files.
        assert not (run_dir / "app-handshake.json").exists()
        assert not (run_dir / "app.lock").exists()

    def test_respawn_reuses_the_live_instance(self, run_dir, no_dispatcher, interactive_reset):
        first: list[str] = []
        thread = threading.Thread(target=lambda: run_app(_emit=first.append), daemon=True)
        thread.start()
        for _ in range(200):
            if first:
                break
            threading.Event().wait(0.05)
        handshake = parse_ready_line(first[0])

        second: list[str] = []
        try:
            assert run_app(_emit=second.append) == 0
            assert parse_ready_line(second[0]) == handshake, "respawn must re-print the live handshake"
        finally:
            stop = urllib.request.Request(
                f"{handshake.url}/api/shutdown",
                method="POST",
                headers={"Authorization": f"Bearer {handshake.token}"},
            )
            urllib.request.urlopen(stop, timeout=5).close()
            thread.join(timeout=15)
