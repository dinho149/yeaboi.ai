"""Filesystem sandbox policy — yeaboi may only touch ~/.yeaboi unless allowed.

Every read or write of a user-supplied path routes through
:func:`resolve_and_check`. The allowed set, checked in order:

1. ``paths.ROOT_DIR`` — the data home (follows ``YEABOI_HOME`` automatically).
2. ``paths.DEFAULT_ROOT_DIR`` — the pinned ``~/.yeaboi`` bootstrap home (its
   ``.env`` stays there even when the tree is relocated).
3. :data:`BUILTIN_ALLOWED` — the app's own narrow feature paths, each
   mode-scoped and documented below. These keep first-run features working
   without any configuration.
4. The user whitelist — ``YEABOI_ALLOWED_PATHS`` (comma-separated, persisted
   in ``~/.yeaboi/.env`` via ``config.set_allowed_paths``); applies to reads
   AND writes (one list — mode only narrows builtins and phrases messages).
5. Session grants — "allow once" consents and ``--allow-path`` CLI values;
   in-memory only, gone at process exit.

Resolution semantics (the load-bearing part): candidate paths go through
``Path(...).expanduser().resolve(strict=False)`` and containment uses
``Path.is_relative_to`` against equally-resolved roots. Resolving follows
symlinks, so a symlink inside an allowed directory that points outside
resolves outside — and is denied. String-prefix comparison is never used
(``/repo`` must not authorize ``/repo-secret``).

Enforcement is headless-first: the module starts non-interactive, and a
denial raises :class:`SandboxViolationError` immediately with an actionable
message. The TUI calls :func:`set_interactive`; then denials ALSO queue a
:class:`ConsentRequest` that the main thread pops after the graph turn
(``pop_pending_denials``) to show the Allow once / Always allow / Deny
popup. The exception still propagates — inside the agent graph,
``ToolNode(handle_tool_errors=True)`` converts it into a ToolMessage the LLM
relays, which is exactly the turn-based pause the ``human_review`` node uses.

# See docs: "Guardrails" — input guardrails; this is the filesystem layer.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

Mode = Literal["read", "write"]

_MODE_VERB = {"read": "read from", "write": "write to"}


class SandboxViolationError(PermissionError):
    """A path outside the sandbox was denied. The message names every remedy."""

    def __init__(self, path: Path, mode: Mode, context: str = "") -> None:
        self.path = path
        self.mode: Mode = mode
        self.context = context
        where = f" ({context})" if context else ""
        super().__init__(
            f"yeaboi is sandboxed to its data directory (~/.yeaboi, or $YEABOI_HOME) "
            f"and cannot {_MODE_VERB[mode]} '{path}'{where}. Allow this path via "
            f"Settings → Allowed Paths, the --allow-path flag, or by adding it to "
            f"YEABOI_ALLOWED_PATHS (comma-separated) in ~/.yeaboi/.env."
        )


@dataclass(frozen=True)
class ConsentRequest:
    """A denied access the TUI should ask the user about after the turn."""

    path: Path
    mode: Mode
    context: str


@dataclass(frozen=True)
class BuiltinRule:
    """A pre-approved app feature path.

    ``root`` is late-bound (a callable) because some roots change per process
    (``Path.cwd()``) and tests monkeypatch ``Path.home``.
    """

    root: Callable[[], Path]
    modes: frozenset[str]
    reason: str


_RW = frozenset({"read", "write"})
_RO = frozenset({"read"})

# The app's own narrow feature paths — pre-approved so features work out of
# the box. Every entry documents why it exists; anything not listed here goes
# through the user whitelist.
BUILTIN_ALLOWED: tuple[BuiltinRule, ...] = (
    BuiltinRule(lambda: Path.cwd() / ".env", _RO, "project-local .env override (config.load_dotenv)"),
    BuiltinRule(lambda: Path.cwd() / "SCRUM.md", _RO, "planning context default (load_project_context)"),
    BuiltinRule(lambda: Path.cwd() / "scrum-docs", _RO, "planning docs default (load_project_context)"),
    BuiltinRule(lambda: Path.home() / ".openclaw", _RW, "OpenClaw setup wizard + skill install"),
    BuiltinRule(lambda: Path.home() / ".aws" / "config", _RO, "Bedrock profile auto-detect"),
    BuiltinRule(lambda: Path.home() / "Library" / "LaunchAgents", _RW, "standup schedule launchd plists"),
    BuiltinRule(
        lambda: Path.home() / "Library" / "Application Support" / "yeaboi",
        _RW,
        "standup schedule wrapper scripts",
    ),
    BuiltinRule(lambda: Path.home() / ".scrum-agent", _RW, "one-time legacy-root migration"),
)

# Session state — shared across the TUI main thread and the graph worker
# thread that executes tools, hence the lock.
_lock = threading.Lock()
_session_grants: set[Path] = set()
_pending: list[ConsentRequest] = []
_interactive = False


def _resolve(raw: str | Path) -> Path:
    return Path(raw).expanduser().resolve(strict=False)


def _allowed_roots() -> list[tuple[Path, frozenset[str]]]:
    """Assemble the current allowed set (roots resolved at check time).

    Late imports keep this module import-cheap and cycle-free (paths must not
    import fs_policy).
    """
    from yeaboi import paths
    from yeaboi.config import get_allowed_paths

    roots: list[tuple[Path, frozenset[str]]] = [
        (_resolve(paths.ROOT_DIR), _RW),
        (_resolve(paths.DEFAULT_ROOT_DIR), _RW),
    ]
    roots.extend((_resolve(rule.root()), rule.modes) for rule in BUILTIN_ALLOWED)
    roots.extend((_resolve(entry), _RW) for entry in get_allowed_paths())
    with _lock:
        roots.extend((granted, _RW) for granted in _session_grants)
    return roots


def is_allowed(path: str | Path, *, mode: Mode = "read") -> bool:
    """Return True if `path` resolves inside any allowed root for `mode`."""
    resolved = _resolve(path)
    return any(resolved.is_relative_to(root) for root, modes in _allowed_roots() if mode in modes)


def resolve_and_check(path: str | Path, *, mode: Mode = "read", context: str = "") -> Path:
    """Resolve `path` and return it if allowed; raise SandboxViolationError if not.

    In interactive mode the denial is also queued for the TUI's post-turn
    consent popup — the raise still happens (the tool call fails this turn;
    after consent the user retries and it succeeds).
    """
    resolved = _resolve(path)
    if any(resolved.is_relative_to(root) for root, modes in _allowed_roots() if mode in modes):
        return resolved
    with _lock:
        if _interactive:
            _pending.append(ConsentRequest(resolved, mode, context))
    logger.warning("sandbox denial: cannot %s %s (%s)", _MODE_VERB[mode], resolved, context or "-")
    raise SandboxViolationError(resolved, mode, context)


def grant_session(path: str | Path) -> None:
    """Allow `path` (and everything under it) for the rest of this process."""
    resolved = _resolve(path)
    with _lock:
        _session_grants.add(resolved)
    logger.info("sandbox session grant: %s", resolved)


def clear_session_grants() -> None:
    with _lock:
        _session_grants.clear()


def set_interactive(flag: bool) -> None:
    """TUI/REPL flip this on so denials queue consent requests (default off)."""
    global _interactive
    with _lock:
        _interactive = flag


def pop_pending_denials() -> list[ConsentRequest]:
    """Drain the consent queue (deduplicated, order-preserving)."""
    with _lock:
        drained, _pending[:] = list(_pending), []
    seen: set[tuple[Path, str]] = set()
    unique = []
    for req in drained:
        key = (req.path, req.mode)
        if key not in seen:
            seen.add(key)
            unique.append(req)
    return unique
