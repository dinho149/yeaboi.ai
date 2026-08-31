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


def get_context_deps() -> tuple[str, ...] | None:
    """The session's context-source toggles; ``None`` inherits (all on).

    Same contract as the engines' ``context_deps``: an empty tuple is an
    incognito run. Unpersisted for the same reason as the active project.
    """
    return _context_deps


def set_context_deps(deps: tuple[str, ...] | None) -> None:
    """Set the toggles for this process; ``()`` = incognito, ``None`` = inherit."""
    global _context_deps
    _context_deps = deps
    logger.info("context deps set to %s", "inherit" if deps is None else (", ".join(deps) or "incognito"))
