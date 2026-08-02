"""Reveal a file or folder in the desktop file manager.

Exists for one reason: yeaboi keeps things in folders the user never chose
(``~/.yeaboi/transcripts``, ``~/.yeaboi/exports``), and telling somebody a path
is not the same as taking them there. "Drop a recording in
``~/.yeaboi/transcripts``" is a chore; a keypress that opens the folder is not.

Stdlib + subprocess, no new dependency — the same external-binary-with-graceful-
degradation pattern as :mod:`yeaboi.clipboard` and :mod:`yeaboi.voice`: probe with
``shutil.which``, run under a hard timeout, and return a message instead of
raising, because every caller is inside a TUI frame loop where an exception is a
crash.

Deliberately NOT ``webbrowser.open``: that handles URLs (see ``feedback.py`` and
``standup/gap_issues.py``) and on some Linux desktops opens a ``file://`` path in
a *browser* rather than the file manager, which is not what "show me the folder"
means.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# A wedged file manager must never freeze the TUI for longer than this. The
# command only has to *hand off* to the desktop, so this is generous.
_TIMEOUT_SECONDS = 10


def _opener() -> list[str] | None:
    """The platform's "open this path" command, or None when there isn't one."""
    if sys.platform == "darwin":
        return ["open"] if shutil.which("open") else None
    if sys.platform.startswith("linux"):
        return ["xdg-open"] if shutil.which("xdg-open") else None
    if sys.platform.startswith("win"):
        return ["explorer"] if shutil.which("explorer") else None
    return None


def open_path(path: Path | str) -> str:
    """Open ``path`` in the desktop file manager. Returns a user-facing status.

    Never raises. A missing opener, a headless session or a non-existent path all
    degrade to a message naming the path, so the user can still copy it by hand.
    """
    target = Path(path).expanduser()
    if not target.exists():
        logger.warning("os_open: %s does not exist", target)
        return f"Not found: {target}"

    cmd = _opener()
    if cmd is None:
        logger.info("os_open: no opener available on %s", sys.platform)
        return f"Open it yourself: {target}"

    try:
        proc = subprocess.run([*cmd, str(target)], capture_output=True, timeout=_TIMEOUT_SECONDS, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("os_open: %s failed for %s: %s", cmd[0], target, exc)
        return f"Couldn't open it — the path is {target}"

    # explorer.exe returns 1 even on success, which is why the check is not
    # `returncode == 0` on every platform.
    if proc.returncode != 0 and not sys.platform.startswith("win"):
        logger.warning("os_open: %s exited %d for %s", cmd[0], proc.returncode, target)
        return f"Couldn't open it — the path is {target}"

    logger.info("os_open: opened %s via %s", target, cmd[0])
    return f"Opened {target}"
