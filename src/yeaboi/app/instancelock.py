"""Single-instance guard for the desktop backend.

Exactly one ``yeaboi app`` may serve a given ``~/.yeaboi`` tree: two backends
would race the SQLite stores and double-run engines that assume one-at-a-time
use. The lock is an ``O_CREAT|O_EXCL`` file — no ``fcntl``, because this must
work on Windows — and staleness is decided by a *liveness probe*, not by pid
guessing: a lock is only honoured if the process it names still answers
``GET /api/health`` at the URL in the persisted handshake.

The happy conflict path is idempotent respawn: Electron restarted and spawned
a second ``yeaboi app`` while the first still runs. ``acquire()`` then returns
the *existing* handshake so the caller can print it and exit 0 — the shell
reconnects to the live backend and nothing is torn down.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from yeaboi.app.handshake import Handshake, read_handshake

logger = logging.getLogger(__name__)

_LOCK_FILENAME = "app.lock"
_PROBE_TIMEOUT_SECONDS = 2.0


class InstanceLockError(RuntimeError):
    """The lock could not be acquired and no live instance was found."""


@dataclass(frozen=True)
class Acquired:
    """We own the lock; the caller must :func:`release` it on exit."""

    path: Path


@dataclass(frozen=True)
class AlreadyRunning:
    """A live backend already serves this tree — reuse its handshake."""

    handshake: Handshake


def lock_path() -> Path:
    from yeaboi.paths import get_run_dir

    return get_run_dir() / _LOCK_FILENAME


def acquire() -> Acquired | AlreadyRunning:
    """Take the lock, or hand back the live instance it protects.

    Order: try the exclusive create; on conflict probe the recorded instance;
    if it answers, return :class:`AlreadyRunning`; if it does not, the lock is
    stale — remove it and retry once. A second conflict inside that retry means
    a concurrent starter won the race, and *its* instance is probed before
    giving up.
    """
    path = lock_path()
    for attempt in (1, 2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            live = _probe_existing()
            if live is not None:
                logger.info("existing backend is live (pid=%d) — reusing", live.pid)
                return AlreadyRunning(live)
            if attempt == 1:
                logger.warning("stale app lock removed: %s", path)
                path.unlink(missing_ok=True)
                continue
            raise InstanceLockError(f"could not acquire {path}: held, and holder answered the retry race") from None
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"pid": os.getpid()}, separators=(",", ":")))
        logger.info("app lock acquired: %s (pid=%d)", path, os.getpid())
        return Acquired(path)
    raise InstanceLockError(f"could not acquire {path}")  # pragma: no cover - loop always returns/raises


def release(acquired: Acquired) -> None:
    acquired.path.unlink(missing_ok=True)
    logger.info("app lock released: %s", acquired.path)


def _probe_existing() -> Handshake | None:
    """The handshake of a live already-running backend, else None."""
    handshake = read_handshake()
    if handshake is None:
        return None
    request = urllib.request.Request(  # noqa: S310 - loopback URL from our own 0600 handshake file
        f"{handshake.url}/api/health",
        headers={"Authorization": f"Bearer {handshake.token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_PROBE_TIMEOUT_SECONDS) as resp:  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    # The pid check ties the answer to the recorded process: a *different*
    # server on a recycled port must not be mistaken for ours.
    if payload.get("pid") != handshake.pid:
        return None
    return handshake
