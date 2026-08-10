"""`yeaboi app`, as a supervisor would drive it.

This spawns the real process rather than calling the handler, because the thing
under test is the contract between the command and whatever starts it: a
desktop shell (Milestone 5) has no way to learn the port when it asked for one
it does not choose, and parsing Rich's styled output would be parsing a
presentation decision.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
LISTENING = re.compile(r"listening on (http://\S+)")


def _wait_for_line(process, pattern, timeout=25.0):
    """Read stdout until it matches, or give up.

    Reads line by line rather than `communicate()` because the process is meant
    to keep running — waiting for it to exit would hang until the timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = process.stdout.readline()
        if not line:
            if process.poll() is not None:
                return None
            continue
        found = pattern.search(line)
        if found:
            return found
    return None


@pytest.fixture
def app_process(tmp_path):
    """`yeaboi app --port 0` in its own root, torn down afterwards."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(tmp_path),
        # Its own data directory, so a test never touches a real ~/.yeaboi.
        "YEABOI_ROOT_DIR": str(tmp_path / "root"),
        "PYTHONPATH": str(REPO / "src"),
        "PYTHONUNBUFFERED": "1",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "yeaboi.cli", "app", "--port", "0"],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        yield process
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


class TestPortZeroIsDiscoverable:
    """Milestone 5 cannot start until a supervisor can find the server."""

    def test_it_prints_the_bound_url_on_stdout(self, app_process):
        found = _wait_for_line(app_process, LISTENING)
        assert found is not None, "no 'listening on' line — a shell cannot find the port"
        url = found.group(1)
        # 0 asks the OS for a free port; the printed one must be the real one.
        port = int(url.rsplit(":", 1)[1])
        assert port != 0

    def test_the_advertised_url_actually_answers(self, app_process):
        found = _wait_for_line(app_process, LISTENING)
        assert found is not None
        with urllib.request.urlopen(f"{found.group(1)}/api/health", timeout=10) as response:
            assert response.status == 200

    def test_it_keeps_running_until_told_to_stop(self, app_process):
        assert _wait_for_line(app_process, LISTENING) is not None
        # A server that exits after binding is the other way this contract
        # breaks, and it looks identical from a single request.
        time.sleep(0.5)
        assert app_process.poll() is None

    def test_it_stops_on_terminate(self, app_process):
        assert _wait_for_line(app_process, LISTENING) is not None
        app_process.terminate()
        app_process.wait(timeout=10)
        # A sidecar that outlives its parent leaves a server holding the user's
        # projects on a port they cannot see.
        assert app_process.poll() is not None
