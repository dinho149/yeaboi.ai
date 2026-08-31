"""The TUI's active project — in-process controller state.

Set from the welcome screen's project switcher (``p``); read by mode launch
sites when they invoke an engine, so a standup or report started from the
menu runs scoped without every screen learning about projects. Deliberately
process-local and unpersisted: the active project is a *session* choice, and
a stale one silently rescoping next week's runs is worse than re-picking.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_active_project_id: str = ""


def get_active_project() -> str:
    """The active project id, '' when runs should stay team-wide."""
    return _active_project_id


def set_active_project(project_id: str) -> None:
    """Set (or clear, with '') the active project for this process."""
    global _active_project_id
    _active_project_id = project_id
    logger.info("active project set to %s", project_id or "(none)")


_context_deps: tuple[str, ...] | None = None

_solo_mode: bool = False


#: The Solo world's default context toggles: everything but the retro feed —
#: Solo has no retro mode, so a run should not go looking for retro history.
#: Computed from the token vocabulary so a new token is on for Solo by default.
def _solo_default_deps() -> tuple[str, ...]:
    from yeaboi.projects.scope import CONTEXT_DEP_TOKENS

    return tuple(t for t in CONTEXT_DEP_TOKENS if t != "retro")


def get_context_deps() -> tuple[str, ...] | None:
    """The session's context-source toggles; ``None`` inherits (all on).

    Same contract as the engines' ``context_deps``: an empty tuple is an
    incognito run. Unpersisted for the same reason as the active project.
    In the Solo world, "inherit" inherits the solo defaults (retro off)
    rather than everything — an explicit choice still wins.
    """
    if _context_deps is None and _solo_mode:
        deps = _solo_default_deps()
        logger.info("context deps defaulting for solo: %s", ", ".join(deps))
        return deps
    return _context_deps


def set_context_deps(deps: tuple[str, ...] | None) -> None:
    """Set the toggles for this process; ``()`` = incognito, ``None`` = inherit."""
    global _context_deps
    _context_deps = deps
    logger.info("context deps set to %s", "inherit" if deps is None else (", ".join(deps) or "incognito"))


def is_solo_mode() -> bool:
    """True while the session is in the Solo world (set by the landing split)."""
    return _solo_mode


def set_solo_mode(solo: bool) -> None:
    """Record which world the session is in; flips the inherit default above."""
    global _solo_mode
    if solo != _solo_mode:
        logger.info("solo mode %s", "on" if solo else "off")
    _solo_mode = solo
