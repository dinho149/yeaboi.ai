"""Live-TUI smoke test: boot ``yeaboi --dry-run`` in a real pseudo-terminal.

Every other TUI test drives screen builders and key handlers with StringIO
consoles and fake key callables — none of them exercise the *live* terminal
path (raw mode, alt-screen, mouse tracking, real control sequences). That is
exactly the surface a dependency bump (e.g. ``rich``) can break without any
unit test noticing, so this test guards the auto-merged dependency pipeline:
it launches the real CLI in a pty, waits for the mode-select screen to render,
sends ``q``, and asserts a clean exit.

Skipped on Windows (no ``pty``); marked ``slow`` like the other integration
tests that pay real startup cost (the splash animation alone is ~2s).
"""

import os
import re
import select
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

# importorskip, not a skipif marker: on Windows these imports fail at
# collection time, before any marker could be evaluated.
fcntl = pytest.importorskip("fcntl", reason="pty/termios are POSIX-only (the TUI itself is too)")
termios = pytest.importorskip("termios", reason="pty/termios are POSIX-only (the TUI itself is too)")

pytestmark = pytest.mark.slow

# Plain-text fragments that appear once the mode-select screen has rendered.
# Card titles are drawn as ASCII-art block glyphs, so we match the screen
# chrome instead: the version row ("v… · c changelog"), the tip bar, and the
# music bar. Matched after ANSI-stripping; any single hit counts, so a copy
# tweak to one element can't break the test.
_MODE_SCREEN_MARKERS = ("changelog", "Tip:", "channel")

# Chrome of the Humans/Agents landing split, which now renders BEFORE the mode
# menu. Fragments of the heading/hints so a copy tweak can't break the test.
_CATEGORY_SCREEN_MARKERS = ("working with", "choose", "switch")

_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[a-zA-Z]"  # CSI sequences (colours, cursor movement, modes)
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC sequences (window title etc.)
    r"|\x1b[()][0-9A-B]"  # charset selection
    r"|\x1b[=>]"  # keypad modes
)


def _strip_ansi(raw: bytes) -> str:
    return _ANSI_RE.sub("", raw.decode("utf-8", errors="replace"))


def _spawn_tui_in_pty(tmp_path: Path) -> tuple[subprocess.Popen, int]:
    """Launch ``yeaboi --dry-run`` attached to a new pty; return (proc, master_fd)."""
    # Isolate the whole ~/.yeaboi tree in tmp and pre-seed .env so
    # is_first_run() is False and the setup wizard never opens.
    home = tmp_path / "home"
    (home / ".yeaboi").mkdir(parents=True)
    (home / ".yeaboi" / ".env").write_text("ANTHROPIC_API_KEY=test-key-dry-run-only\n")

    env = {
        **os.environ,
        "HOME": str(home),
        "TERM": "xterm-256color",
        "LOG_LEVEL": "ERROR",
        # Belt-and-braces: dry-run makes no LLM calls, but never let a real
        # key from the developer environment leak into the subprocess.
        "ANTHROPIC_API_KEY": "test-key-dry-run-only",
    }
    env.pop("YEABOI_HOME", None)

    master_fd, slave_fd = os.openpty()
    # A generous fixed size so no card/title is truncated by narrow-terminal
    # fallbacks (splash picks its wordmark based on width).
    fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 140, 0, 0))

    proc = subprocess.Popen(
        [sys.executable, "-m", "yeaboi.cli", "--dry-run"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        env=env,
        start_new_session=True,  # own session so the pty is its controlling terminal path
        close_fds=True,
    )
    os.close(slave_fd)  # parent keeps only the master end
    return proc, master_fd


def _read_until(master_fd: int, proc: subprocess.Popen, predicate, timeout: float) -> bytes:
    """Accumulate pty output until predicate(accumulated) is true or timeout."""
    deadline = time.monotonic() + timeout
    buf = b""
    while time.monotonic() < deadline:
        if predicate(buf):
            return buf
        ready, _, _ = select.select([master_fd], [], [], 0.25)
        if ready:
            try:
                chunk = os.read(master_fd, 65536)
            except OSError:  # pty closed — process exited
                break
            if not chunk:
                break
            buf += chunk
        elif proc.poll() is not None and not predicate(buf):
            break  # died before rendering what we waited for
    return buf


class TestTuiLiveSmoke:
    def test_dry_run_boots_to_mode_select_and_quits_cleanly(self, tmp_path):
        """The real TUI reaches the landing split, then mode-select, and exits 0 on 'q'."""
        proc, master_fd = _spawn_tui_in_pty(tmp_path)
        try:
            # Strip only a bounded tail: the markers repaint on EVERY frame, and
            # re-stripping the whole multi-MB buffer each poll throttles the pty
            # drain quadratically (the child blocks on a full pty buffer) —
            # since the full-screen background tint landed, that pushed the
            # match past the deadline. 256 KiB spans several full frames.
            landed = _read_until(
                master_fd,
                proc,
                lambda b: any(m in _strip_ansi(b[-262_144:]) for m in _CATEGORY_SCREEN_MARKERS),
                timeout=30.0,
            )
            text = _strip_ansi(landed[-262_144:])
            assert any(m in text for m in _CATEGORY_SCREEN_MARKERS), (
                f"category screen never rendered; exit={proc.poll()}; last output:\n{text[-2000:]}"
            )

            # Enter picks the preselected category (Humans) → the mode menu.
            os.write(master_fd, b"\r")
            booted = _read_until(
                master_fd,
                proc,
                lambda b: any(m in _strip_ansi(b[-262_144:]) for m in _MODE_SCREEN_MARKERS),
                timeout=30.0,
            )
            text = _strip_ansi(booted[-262_144:])
            assert any(m in text for m in _MODE_SCREEN_MARKERS), (
                f"mode-select screen never rendered; exit={proc.poll()}; last output:\n{text[-2000:]}"
            )
            # Alt-screen must have been entered — the strongest signal that the
            # live terminal path (not a fallback print) is actually running.
            assert b"\x1b[?1049h" in landed + booted, "TUI never entered the alternate screen buffer"

            os.write(master_fd, b"q")
            # Drain until the pty hits EOF or the process exits, so the pty
            # buffer can't block the child's final writes.
            deadline = time.monotonic() + 15.0
            while proc.poll() is None and time.monotonic() < deadline:
                ready, _, _ = select.select([master_fd], [], [], 0.25)
                if ready:
                    try:
                        if not os.read(master_fd, 65536):
                            break
                    except OSError:
                        break
            # EOF can land a beat before the exit status is reapable — wait.
            try:
                returncode = proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                returncode = None
            assert returncode == 0, f"TUI did not exit cleanly on 'q' (returncode={returncode})"
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            os.close(master_fd)
