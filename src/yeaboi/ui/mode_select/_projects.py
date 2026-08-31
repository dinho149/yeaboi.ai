"""The Projects page loop: list, set active, archive.

Reads are cheap and the page is short, so it re-reads the store on every
action rather than caching. Creating a project is a terminal command
(``yeaboi project create <name>``) and the page says so rather than offering
a form — same argument as the Ceremonies page: the surface that installs a
thing should be the one the resulting command is visible in.

The one piece of state the page *writes* beyond the store is the active
project (``projects/active.py``): the process-local choice every mode launch
site reads so its runs are scoped.
"""

from __future__ import annotations

import logging
import time

from rich.console import Console

from yeaboi.projects.active import (
    get_active_project,
    get_context_deps,
    is_solo_mode,
    set_active_project,
    set_context_deps,
)
from yeaboi.ui.mode_select.screens._screens_projects import (
    ACTIONS,
    CONTEXT_ACTIONS,
    CONTEXT_ROWS,
    _build_context_screen,
    _build_projects_screen,
)
from yeaboi.ui.shared._scroll import SCROLL_KEYS, coalesce_scroll

logger = logging.getLogger(__name__)


def _load() -> list[dict]:
    from yeaboi.projects.engine import list_projects

    try:
        return list_projects()
    except Exception:  # noqa: BLE001 — a broken store is an empty page, not a crash
        logger.error("projects page: list_projects failed", exc_info=True)
        return []


def run_projects_page(
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
) -> None:
    """Enter Projects from the menu; returns when the user backs out."""
    projects = _load()
    logger.info("Projects page opened: %d project(s), active=%s", len(projects), get_active_project() or "(none)")
    selected = action_sel = scroll = 0
    scroll_meta: dict = {}
    message = ""
    start = time.monotonic()

    while True:
        w, h = console.size
        live.update(
            _build_projects_screen(
                projects,
                selected=selected,
                active_project_id=get_active_project(),
                scroll_offset=scroll,
                scroll_meta=scroll_meta,
                width=w,
                height=h,
                action_sel=action_sel,
                actions=list(ACTIONS),
                shimmer_tick=time.monotonic() - start,
                sub_reveal=(time.monotonic() - start) * 6.0,
                message=message,
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()

        if key in SCROLL_KEYS:
            scroll = coalesce_scroll(scroll, key, scroll_meta, read_key)
            continue
        if key in ("esc", "q"):
            logger.info("Projects page closed")
            return
        if key == "left":
            action_sel = (action_sel - 1) % len(ACTIONS)
        elif key == "right":
            action_sel = (action_sel + 1) % len(ACTIONS)
        elif key in ("up", "down") and projects:
            step = -1 if key == "up" else 1
            selected = (selected + step) % len(projects)
            message = ""
        elif key == "enter":
            choice = ACTIONS[action_sel]
            if choice == "Back":
                logger.info("Projects page closed from the buttons")
                return
            if choice == "Context":
                # Orthogonal to the project list — incognito works with no
                # projects at all, so this sits before the empty-list guard.
                run_context_page(console, live, read_key, frame_time, supports_timeout)
                message = ""
                continue
            if not projects:
                message = "No projects yet — yeaboi project create <name>"
                continue
            project = projects[selected]
            if choice == "Set active":
                if get_active_project() == project["project_id"]:
                    set_active_project("")
                    message = f"{project['name']} is no longer active — runs are team-wide again."
                else:
                    set_active_project(project["project_id"])
                    message = f"{project['name']} is the active project — runs from the menu are scoped to it."
            elif choice == "Archive":
                from yeaboi.paths import get_db_path
                from yeaboi.projects.store import ProjectStore

                with ProjectStore(get_db_path()) as store:
                    store.archive(project["project_id"])
                if get_active_project() == project["project_id"]:
                    set_active_project("")
                logger.info("Projects: archived %s", project["project_id"])
                message = f"Archived {project['name']}."
            projects = _load()
            selected = min(selected, max(0, len(projects) - 1))


def run_context_page(
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
) -> None:
    """The context-toggles sub-page: Space flips a source, buttons batch it.

    Writes only ``projects/active.py`` state — the process-local toggles every
    launch site passes as ``context_deps``. ``None`` = inherit, ``()`` =
    incognito, same contract as the engines.
    """
    logger.info("Context page opened: deps=%s", get_context_deps())
    selected = action_sel = 0
    message = ""
    start = time.monotonic()

    while True:
        w, h = console.size
        live.update(
            _build_context_screen(
                get_context_deps(),
                selected=selected,
                action_sel=action_sel,
                width=w,
                height=h,
                shimmer_tick=time.monotonic() - start,
                sub_reveal=(time.monotonic() - start) * 6.0,
                message=message,
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()

        if key in ("esc", "q"):
            logger.info("Context page closed: deps=%s", get_context_deps())
            return
        if key == "left":
            action_sel = (action_sel - 1) % len(CONTEXT_ACTIONS)
        elif key == "right":
            action_sel = (action_sel + 1) % len(CONTEXT_ACTIONS)
        elif key in ("up", "down"):
            step = -1 if key == "up" else 1
            selected = (selected + step) % len(CONTEXT_ROWS)
            message = ""
        elif key == " ":
            token = CONTEXT_ROWS[selected][0]
            deps = get_context_deps()
            # Inherit materialises to the full set on the first toggle so
            # switching one source off leaves the other four explicitly on.
            current = set(token for token, _label, _hint in CONTEXT_ROWS) if deps is None else set(deps)
            current.symmetric_difference_update({token})
            ordered = tuple(t for t, _label, _hint in CONTEXT_ROWS if t in current)
            set_context_deps(ordered)
            logger.info("Context page: toggled %s -> %s", token, ordered or "incognito")
            message = ""
        elif key == "enter":
            choice = CONTEXT_ACTIONS[action_sel]
            if choice == "Back":
                logger.info("Context page closed from the buttons: deps=%s", get_context_deps())
                return
            if choice == "All on":
                set_context_deps(None)
                # Solo inherits the solo defaults, not everything — it has no
                # retro mode, so saying "every source" there would be a lie.
                message = (
                    "Back to the Solo defaults — retro stays off."
                    if is_solo_mode()
                    else "Every source on — runs inherit the project default when one is set."
                )
            elif choice == "Incognito":
                set_context_deps(())
                message = "Incognito — runs read no cross-mode context until this changes."
