"""The Projects page loop: list, open, browse a project's sessions, archive.

Reads are cheap and the page is short, so it re-reads the store on every
action rather than caching. Creating a project is a terminal command
(``yeaboi project create <name>``) and the page says so rather than offering
a form — same argument as the Ceremonies page: the surface that installs a
thing should be the one the resulting command is visible in.

The one piece of state the page *writes* beyond the store is the active
project (``projects/active.py``): the process-local choice every mode launch
site reads so its runs are scoped. Open sets it and returns the id — the door
(``pick=True``) and the menu's ``P`` shortcut both read that.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

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
    SESSIONS_ACTIONS,
    _build_context_screen,
    _build_project_sessions_screen,
    _build_projects_screen,
)
from yeaboi.ui.shared._scroll import SCROLL_KEYS, coalesce_scroll

logger = logging.getLogger(__name__)

#: The saved-runs hub a sessions row opens, by the row's wire mode. Planning
#: and analysis have no hub by registry design — their rows say so.
_HUB_FOR_MODE: dict[str, str] = {
    "standup": "daily-standup",
    "retro": "retro",
    "reporting": "reporting",
    "ship": "ship",
    "review": "weekly-review",
}

_MODE_LABELS: dict[str, str] = {
    "planning": "Planning",
    "analysis": "Analysis",
    "standup": "Standup",
    "retro": "Retro",
    "reporting": "Reporting",
    "ship": "Ship",
    "review": "Weekly Review",
}


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
    *,
    pick: bool = False,
    open_hub: Callable[[str], None] | None = None,
) -> str | None:
    """The Projects page; returns the id Open chose, or None when backed out.

    ``pick`` marks the door's use (Esc returns to the door); the loop is the
    same either way. ``open_hub(card_key)`` opens a mode's saved-runs hub from
    the sessions sub-page — injected by ``select_mode``, which owns the hubs.
    """
    projects = _load()
    logger.info(
        "Projects page opened (pick=%s): %d project(s), active=%s",
        pick,
        len(projects),
        get_active_project() or "(none)",
    )
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

        # ↑/↓ move the row selection; the wheel and page keys scroll the viewport.
        if key in ("up", "down") and projects:
            step = -1 if key == "up" else 1
            selected = (selected + step) % len(projects)
            message = ""
            continue
        if key in SCROLL_KEYS:
            scroll = coalesce_scroll(scroll, key, scroll_meta, read_key)
            continue
        if key in ("esc", "q"):
            logger.info("Projects page closed")
            return None
        if key == "left":
            action_sel = (action_sel - 1) % len(ACTIONS)
        elif key == "right":
            action_sel = (action_sel + 1) % len(ACTIONS)
        elif key == "enter":
            choice = ACTIONS[action_sel]
            if choice == "Back":
                logger.info("Projects page closed from the buttons")
                return None
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
            if choice == "Open":
                set_active_project(project["project_id"])
                logger.info("Projects: opened %s", project["project_id"])
                return project["project_id"]
            if choice == "Sessions":
                run_project_sessions_page(
                    console, live, read_key, frame_time, supports_timeout, project=project, open_hub=open_hub
                )
                message = ""
                continue
            if choice == "Archive":
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


def _load_sessions(project_id: str) -> list:
    from yeaboi.sessions_recent import recent_sessions

    try:
        return recent_sessions(project_id=project_id, limit=100)
    except Exception:  # noqa: BLE001 — a broken store is an empty page, not a crash
        logger.error("project sessions page: recent_sessions failed", exc_info=True)
        return []


def run_project_sessions_page(
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    *,
    project: dict,
    open_hub: Callable[[str], None] | None = None,
) -> None:
    """Every run inside one project, newest first; Enter opens its mode's hub.

    The hub is opened through ``open_hub`` with the active project set to this
    one for the duration, so the hub lists the project's runs; the previous
    active project is restored afterwards.
    """
    project_id = project["project_id"]
    rows = _load_sessions(project_id)
    logger.info("Project sessions page opened: %s (%d row(s))", project_id, len(rows))
    selected = action_sel = scroll = 0
    scroll_meta: dict = {}
    message = ""
    start = time.monotonic()

    while True:
        w, h = console.size
        live.update(
            _build_project_sessions_screen(
                rows,
                project_name=project.get("name", ""),
                selected=selected,
                scroll_offset=scroll,
                scroll_meta=scroll_meta,
                width=w,
                height=h,
                action_sel=action_sel,
                actions=list(SESSIONS_ACTIONS),
                shimmer_tick=time.monotonic() - start,
                sub_reveal=(time.monotonic() - start) * 6.0,
                message=message,
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()

        if key in ("up", "down") and rows:
            step = -1 if key == "up" else 1
            selected = (selected + step) % len(rows)
            message = ""
            continue
        if key in SCROLL_KEYS:
            scroll = coalesce_scroll(scroll, key, scroll_meta, read_key)
            continue
        if key in ("esc", "q"):
            logger.info("Project sessions page closed")
            return
        if key == "left":
            action_sel = (action_sel - 1) % len(SESSIONS_ACTIONS)
        elif key == "right":
            action_sel = (action_sel + 1) % len(SESSIONS_ACTIONS)
        elif key == "enter":
            if SESSIONS_ACTIONS[action_sel] == "Back":
                logger.info("Project sessions page closed from the buttons")
                return
            if not rows:
                message = "Nothing has run inside this project yet."
                continue
            row = rows[selected]
            card_key = _HUB_FOR_MODE.get(row.mode)
            label = _MODE_LABELS.get(row.mode, row.mode)
            if card_key is None or open_hub is None:
                message = f"Open it from the {label} card."
                continue
            logger.info("Project sessions: opening the %s hub for %s", card_key, project_id)
            previous = get_active_project()
            set_active_project(project_id)
            try:
                open_hub(card_key)
            finally:
                set_active_project(previous)
            rows = _load_sessions(project_id)
            selected = min(selected, max(0, len(rows) - 1))
            message = ""


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
