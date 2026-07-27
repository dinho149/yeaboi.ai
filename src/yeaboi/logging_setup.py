"""Central logging setup — one formatter, one fallback level, rotation everywhere.

Every log handler in the app is created here so that all log files share the
same format, rotation policy (2 MB x 3 backups), and level source (the
``LOG_LEVEL`` env var read via :func:`yeaboi.config.get_log_level`, default
``WARNING``).

All handlers attach to the ``yeaboi`` namespace logger. Every module in the
app uses ``logging.getLogger(__name__)`` under that namespace, so attaching a
handler here captures records from the whole app. There is deliberately no
per-logger namespace filtering: page runners are modal (only one mode page is
active at a time), so while e.g. Retro runs, all app records — including
shared infrastructure such as ``agent/llm.py`` and ``tools/*`` — land in
``logs/retro/retro.log`` *and* the always-on ``logs/tui/yeaboi.log``. An LLM
call made during a retro belongs in the retro log.

Handler keys:
  - ``"tui"``      — the always-on main log (``logs/tui/yeaboi.log``)
  - ``"<mode>"``   — per-mode logs (``logs/<mode>/<mode>.log``), attached by
                     :func:`mode_log` while that mode's page is open
  - ``"session"``  — the per-planning-session log
                     (``logs/planning/<session-id>.log``)

Note: the Analysis flow nests the planning session machinery — while an
analysis runs, records may land in analysis.log, the session log, and the TUI
log simultaneously. That is intentional: everything active lands in every
attached file.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from yeaboi.redaction import RedactingFormatter

logger = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3

_APP = "yeaboi"

# Tracked handlers, keyed "tui" | "<mode>" | "session". Module-level so that
# apply_level() can retune every attached handler live.
_handlers: dict[str, logging.Handler] = {}


def _level() -> int:
    """Resolve the configured level (LOG_LEVEL env var, fallback WARNING)."""
    from yeaboi.config import get_log_level

    return getattr(logging, get_log_level(), logging.WARNING)


class _SecureRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler whose files are always owner-only (0o600).

    Log files can carry sensitive detail (paths, project content, redacted-but-
    adjacent error text), so they get the same treatment as ``.env``. The chmod
    lives in ``_open()`` rather than a one-shot after construction because
    rollover renames the current file to ``.1`` (rename preserves perms) and
    then ``_open()``s a fresh base file at umask-default perms — overriding
    ``_open()`` hardens the initial file AND every post-rollover file.
    """

    def _open(self):  # noqa: ANN202 - matches the stdlib signature
        from yeaboi.config import restrict_permissions

        stream = super()._open()
        restrict_permissions(Path(self.baseFilename), mode=0o600)
        return stream


def _attach(key: str, path: Path) -> None:
    """Attach a rotating file handler under `key`. Idempotent per key."""
    if key in _handlers:  # already attached — page re-entry is a no-op
        return
    from yeaboi.config import restrict_permissions

    path.parent.mkdir(parents=True, exist_ok=True)
    # Log dirs are 0o700 like ~/.yeaboi itself — YEABOI_HOME may relocate the
    # tree somewhere that doesn't inherit the hardened root perms.
    restrict_permissions(path.parent, mode=0o700)
    handler = _SecureRotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
    # RedactingFormatter scrubs secrets from every record (incl. tracebacks)
    # at the one point where the final log line is assembled — see redaction.py.
    handler.setFormatter(RedactingFormatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    handler.setLevel(_level())
    logging.getLogger(_APP).addHandler(handler)
    logging.getLogger(_APP).setLevel(_level())
    _handlers[key] = handler
    logger.debug("Log handler attached: %s -> %s", key, path)


def detach(key: str) -> None:
    """Detach and close the handler registered under `key` (no-op if absent)."""
    handler = _handlers.pop(key, None)
    if handler is not None:
        handler.flush()
        handler.close()
        logging.getLogger(_APP).removeHandler(handler)


def configure_logging() -> None:
    """Attach the main rotating TUI handler (logs/tui/yeaboi.log). Idempotent.

    Called once, early in ``cli.main()`` — this is the always-on log every
    code path writes to for the lifetime of the process.
    """
    from yeaboi.paths import get_tui_log_path

    _attach("tui", get_tui_log_path())


def attach_mode_handler(mode: str) -> None:
    """Attach the rotating per-mode log (logs/<mode>/<mode>.log). Idempotent."""
    from yeaboi.paths import LOGS_DIR

    _attach(mode, LOGS_DIR / mode / f"{mode}.log")


@contextmanager
def mode_log(mode: str) -> Iterator[None]:
    """Route all app records to logs/<mode>/<mode>.log while the block runs.

    The preferred way to scope a mode page's logging — detaches on normal
    exit *and* on exception, so page runners can never leak handlers.
    """
    attach_mode_handler(mode)
    try:
        yield
    finally:
        detach(mode)


def attach_session_log(session_id: str) -> None:
    """Attach a rotating per-planning-session log (logs/planning/<id>.log).

    Safe to call repeatedly — a new session replaces the previous handler.
    """
    from yeaboi.paths import PLANNING_LOGS_DIR

    detach("session")
    _attach("session", PLANNING_LOGS_DIR / f"{session_id}.log")


def detach_session_log() -> None:
    """Detach the per-session log handler, flushing and closing the file."""
    detach("session")


def apply_level(level: str) -> None:
    """Apply `level` live to the yeaboi logger AND every tracked handler.

    Both must be updated: a DEBUG handler behind a WARNING logger stays
    silent, and vice versa. Called when the user changes the log level from
    the Settings page.
    """
    resolved = getattr(logging, level.upper(), logging.WARNING)
    logging.getLogger(_APP).setLevel(resolved)
    for handler in _handlers.values():
        handler.setLevel(resolved)
    logger.info("Log level applied live: %s", level.upper())
