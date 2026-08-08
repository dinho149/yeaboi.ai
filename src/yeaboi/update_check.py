"""Background PyPI update check — fire-and-forget, never blocks or crashes the app.

Mirrors the telemetry pattern (``telemetry.send_telemetry``): stdlib ``urllib``
with a short timeout, every error swallowed at debug level. The check runs once
per process on a daemon thread; the TUI polls :func:`get_update_status` each
frame to render the bottom-left version hint on the mode-select screen.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import threading
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

_PYPI_URL = "https://pypi.org/pypi/yeaboi/json"

# Written by the daemon worker thread, read by the render thread. Single-key
# dict writes are atomic under the GIL, so no lock is needed.
_state: dict = {"latest": "", "checked": False}
_started = False

# Set on the process that is ABOUT to be replaced by :func:`restart_in_place`, and
# read back by the fresh process from its environment. Carries the version we
# upgraded to, so the relaunch can skip the splash and confirm what it is running.
_RESTART_ENV = "YEABOI_RESTARTED"

# The marker this process was launched with, captured on first read (see
# :func:`restarted_version`). None means "not read out of the environment yet".
_restarted_from: str | None = None

# Pending in-app restart request: the version we upgraded to, or "" for none.
# Written by the ctrl+U flow deep inside the TUI, read by ``cli.main`` once the
# terminal has been restored — see :func:`request_restart`.
_restart_to = ""


def parse_version(version: str) -> tuple[int, ...] | None:
    """Parse ``X.Y.Z``-style strings into a comparable int tuple.

    Local suffixes (``0.0.0+dev``) are stripped; each dot component keeps only
    its leading digit run (``2.10.0rc1`` -> ``(2, 10, 0)``). Returns ``None``
    when the first component has no digits — callers treat unparseable versions
    conservatively (never flag an update).
    """
    if not version:
        return None
    parts = version.split("+", 1)[0].split(".")
    numbers: list[int] = []
    for part in parts:
        match = re.match(r"\d+", part.strip())
        if match is None:
            break
        numbers.append(int(match.group()))
    return tuple(numbers) if numbers else None


def is_newer(latest: str, current: str) -> bool:
    """True when ``latest`` is strictly newer than ``current``; False when unsure."""
    latest_t = parse_version(latest)
    current_t = parse_version(current)
    if latest_t is None or current_t is None:
        return False
    return latest_t > current_t


def detect_upgrade_command() -> str:
    """Best-effort detection of how yeaboi was installed (uv tool vs pipx).

    uv tool venvs live under ``~/.local/share/uv/tools/``; pipx venvs under
    ``~/.local/pipx/venvs/``. Falls back to the documented uv install method.
    """
    try:
        exe = Path(sys.executable).resolve().as_posix()
    except OSError:
        exe = sys.executable or ""
    if "/pipx/venvs/" in exe:
        return "pipx upgrade yeaboi"
    return "uv tool upgrade yeaboi"


def run_upgrade(*, timeout: float = 300.0) -> tuple[bool, str]:
    """Run the detected upgrade command; return ``(ok, message)``.

    Powers the in-app ``ctrl+U`` update shortcut. Best-effort and never raises: a
    launch failure or non-zero exit returns ``(False, <detail>)`` so the caller can
    fall back to showing the manual command. On success the freshly-installed code
    only takes effect in a NEW process — the caller restarts via
    :func:`restart_in_place` (or, when that isn't possible, says so).
    """
    import shlex
    import subprocess

    command = detect_upgrade_command()
    try:
        proc = subprocess.run(  # noqa: S603 - command comes from detect_upgrade_command (fixed uv/pipx strings)
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - report any launch/timeout failure to the UI
        logger.warning("upgrade command failed to run: %s", exc)
        return False, str(exc)
    if proc.returncode == 0:
        logger.info("upgrade succeeded via '%s'", command)
        return True, (proc.stdout or "").strip()
    logger.warning("upgrade exited %s via '%s'", proc.returncode, command)
    return False, (proc.stderr or proc.stdout or "").strip()


def resolve_relaunch_command() -> list[str] | None:
    """The argv that re-launches *this* install, or None when we can't work it out.

    Backs :func:`restart_in_place`. Prefers ``sys.argv[0]`` (the console script uv
    or pipx generated — still valid after an in-place upgrade, since the upgrade
    rewrites the venv behind it), falling back to a PATH lookup for when the process
    was started by bare name and ``argv[0]`` isn't a path we can exec. The original
    flags ride along, so a restart preserves ``--dry-run``/``--theme``.

    Returns None on non-POSIX: ``os.execv`` on Windows does not replace the process,
    it spawns and detaches, which would leave two apps fighting over one terminal.
    The TUI is POSIX-only anyway (``ui/shared/_input.py`` imports ``termios``), so
    this is a guard rather than a limitation.
    """
    if os.name != "posix":
        return None
    argv0 = sys.argv[0] if sys.argv else ""
    candidate = ""
    if argv0:
        try:
            path = Path(argv0)
            # A .py argv[0] means ``python -m yeaboi`` / a direct script run, not the
            # console script: exec'ing it either fails ENOEXEC or (with a shebang)
            # re-runs us outside the venv the upgrade just rewrote. Fall through to
            # the PATH lookup, which finds the real console script.
            if path.is_file() and path.suffix != ".py":
                candidate = str(path.resolve())
        except OSError:
            candidate = ""
        if not candidate:
            candidate = shutil.which(Path(argv0).name) or ""
    if not candidate:
        candidate = shutil.which("yeaboi") or ""
    if not candidate:
        logger.warning("cannot resolve a relaunch command (argv0=%r)", argv0)
        return None
    return [candidate, *sys.argv[1:]]


def request_restart(version: str) -> None:
    """Record that the app should relaunch itself once the TUI has torn down.

    The ctrl+U flow runs several frames deep inside the mode-select ``Live``
    context, and ``os.execv`` does NOT run ``atexit`` handlers — exec'ing from there
    would hand the new process a terminal still in raw mode, with mouse tracking on
    and the alternate screen active. Only ``cli.main``'s ``finally`` unwinds all of
    that, so the flow leaves the request here and unwinds; ``cli.main`` calls
    :func:`restart_in_place` after the terminal is clean.
    """
    global _restart_to
    _restart_to = version or "1"
    logger.info("restart requested after upgrade to v%s", version)


def restart_requested() -> str:
    """The version a restart was requested for, or "" when none is pending."""
    return _restart_to


def restarted_version() -> str:
    """The version this process was relaunched onto, or "" for a normal launch.

    Read from the environment the previous process image set just before exec'ing,
    so it survives the process replacement that module state cannot — then *popped*
    and cached, because the marker describes this process and nothing else. Left in
    the environment it would be inherited by every child we spawn (the upgrade
    subprocess, a board server, cloudflared), and a nested yeaboi would come up
    believing it was the relaunch.
    """
    global _restarted_from
    if _restarted_from is None:
        _restarted_from = os.environ.pop(_RESTART_ENV, "")
    return _restarted_from


def is_fresh_restart() -> bool:
    """True when this process is the relaunch AND it really is on the new version.

    The marker carries the version we upgraded to, so comparing it against the
    running version self-heals: a stale or foreign ``YEABOI_RESTARTED`` in the
    environment, or an upgrade that didn't actually move the version, just falls
    back to a normal launch rather than silently suppressing the splash forever.
    """
    marker = restarted_version()
    return bool(marker) and marker == _current_version()


def restart_in_place() -> bool:
    """Replace this process with a fresh yeaboi. Only returns when it *failed*.

    ``os.execv`` swaps the process image, so the freshly installed code really is
    what runs next and there's no orphaned parent holding the terminal. Call it only
    after the terminal has been restored — exec skips ``atexit``.

    Returns False (having changed nothing the caller can't recover from) when there's
    no resolvable relaunch command or exec itself fails, so the caller can fall back
    to telling the user to restart by hand.
    """
    command = resolve_relaunch_command()
    if command is None:
        return False
    # exec does not flush Python's buffers — anything still pending would be lost.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:  # noqa: BLE001 - a closed stream must not block the restart
            pass
    # The kernel's input buffer survives exec. Anything typed during the upgrade and
    # not consumed by the countdown would be replayed into the fresh process's
    # mode-select loop, where a buffered "q" quits the app we just restarted.
    try:
        import termios

        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except Exception:  # noqa: BLE001 - no tty / no termios: there is nothing to drain
        pass
    os.environ[_RESTART_ENV] = _restart_to or "1"
    logger.info("restarting in place: %s", command)
    try:
        os.execv(command[0], command)  # noqa: S606 - argv is our own console script + our own flags
    except OSError as exc:
        logger.warning("restart failed to exec %s: %s", command[0], exc)
        os.environ.pop(_RESTART_ENV, None)
        return False
    return False  # unreachable: a successful execv never returns


def fetch_latest_version(timeout: float = 3.0) -> str | None:
    """Fetch the latest released version from PyPI; None on any failure."""
    try:
        req = urllib.request.Request(_PYPI_URL, headers={"Accept": "application/json"})  # noqa: S310 - fixed https PyPI constant
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https PyPI constant
            data = json.loads(resp.read().decode("utf-8"))
        version = data["info"]["version"]
        return version if isinstance(version, str) else None
    except Exception:
        # Never let the update check crash or nag — offline is a normal state.
        logger.debug("update check failed (this is fine)", exc_info=True)
        return None


def _current_version() -> str:
    from yeaboi import __version__

    return __version__


def start_background_check() -> None:
    """Spawn the one-shot PyPI check on a daemon thread (idempotent)."""
    global _started
    if _started:
        return
    current = _current_version()
    if current == "0.0.0+dev":
        logger.info("update check skipped: running from source tree (dev version)")
        _started = True
        return

    def _worker() -> None:
        latest = fetch_latest_version()
        if latest:
            _state["latest"] = latest
            if is_newer(latest, current):
                logger.info("update available: %s -> %s", current, latest)
            else:
                logger.info("yeaboi is up to date (%s)", current)
        _state["checked"] = True

    _started = True
    logger.info("update check started (current version %s)", current)
    threading.Thread(target=_worker, name="update-check", daemon=True).start()


def get_update_status() -> dict:
    """Snapshot of the update check for the UI (and the test monkeypatch seam)."""
    current = _current_version()
    latest = _state["latest"]
    return {
        "current": current,
        "latest": latest,
        "update_available": bool(latest) and is_newer(latest, current),
        "upgrade_command": detect_upgrade_command(),
        "is_dev": current == "0.0.0+dev",
    }
