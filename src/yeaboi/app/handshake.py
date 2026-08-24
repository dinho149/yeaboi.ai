"""The startup handshake — how the desktop shell learns where the backend is.

``yeaboi app`` prints exactly one line to stdout once the server is bound:

    YEABOI_APP_READY {"url": ..., "token": ..., "pid": ..., "schema": ..., "version": ...}

stdout carries nothing else (the same discipline as the MCP server's stdio
rule); logs go to ``~/.yeaboi/logs/app/``. The same payload is written to
``run/app-handshake.json`` at 0600 so a restarted Electron can re-attach to a
still-running backend without respawning it — the instance lock's liveness
probe reads the token from there.

The wire shape is pinned by ``tests/unit/test_app_wire.py`` and documented in
``contracts/v1/app_http.md``; changing a key is a contract change.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

READY_PREFIX = "YEABOI_APP_READY "

_HANDSHAKE_FILENAME = "app-handshake.json"


@dataclass(frozen=True)
class Handshake:
    """What the shell needs to talk to this process."""

    url: str
    token: str
    pid: int
    schema: int
    version: str


def ready_line(handshake: Handshake) -> str:
    """The one stdout line, prefix + compact JSON (no trailing newline)."""
    return READY_PREFIX + json.dumps(asdict(handshake), separators=(",", ":"), sort_keys=True)


def parse_ready_line(line: str) -> Handshake:
    """Parse a ready line back into a :class:`Handshake` (raises ValueError)."""
    if not line.startswith(READY_PREFIX):
        raise ValueError("not a handshake line")
    return _from_dict(json.loads(line[len(READY_PREFIX) :]))


def handshake_path() -> Path:
    from yeaboi.paths import get_run_dir

    return get_run_dir() / _HANDSHAKE_FILENAME


def write_handshake(handshake: Handshake) -> Path:
    """Persist the handshake at 0600 for re-attach; returns the path."""
    from yeaboi.config import restrict_permissions

    path = handshake_path()
    payload = json.dumps(asdict(handshake), separators=(",", ":"), sort_keys=True)
    # Created 0600, not chmod'ed to it afterwards: the file holds the bearer
    # token, and between write and chmod it would be readable by anyone.
    path.unlink(missing_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    restrict_permissions(path, mode=0o600)
    logger.info("handshake written: %s (pid=%d)", path, handshake.pid)
    return path


def read_handshake() -> Handshake | None:
    """The persisted handshake, or None when absent/unreadable/malformed."""
    path = handshake_path()
    try:
        return _from_dict(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None
    except Exception:
        logger.warning("handshake file unreadable: %s", path, exc_info=True)
        return None


def clear_handshake() -> None:
    handshake_path().unlink(missing_ok=True)


def _from_dict(raw: dict) -> Handshake:
    return Handshake(
        url=str(raw["url"]),
        token=str(raw["token"]),
        pid=int(raw["pid"]),
        schema=int(raw["schema"]),
        version=str(raw["version"]),
    )
