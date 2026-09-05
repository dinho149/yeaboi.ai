"""The Projects page loops: the list, one project, a new project's draft.

Reads are cheap and the pages are short, so each re-reads the store on every
action rather than caching. The list opens a project's page; the page's
Start makes it the active project (``projects/active.py``, the process-local
choice every mode launch site reads so its runs are scoped) and returns its
id, which the door (``pick=True``) and the menu's ``P`` keycap both read.
Plan does the same and names the card to run, so the menu opens on it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from rich.align import Align
from rich.console import Console, Group
from rich.text import Text

from yeaboi.projects.active import (
    get_active_project,
    get_context_deps,
    is_solo_mode,
    set_active_project,
    set_context_deps,
)
from yeaboi.ui.mode_select.screens._screens_projects import (
    CONTEXT_ACTIONS,
    CONTEXT_ROWS,
    DRAFT_ACTIONS,
    PROJECT_ACTIONS,
    SESSIONS_ACTIONS,
    _build_context_screen,
    _build_draft_screen,
    _build_project_screen,
    _build_project_sessions_screen,
    _build_projects_screen,
    list_actions,
    ordered_projects,
)
from yeaboi.ui.shared._components import PROJECTS_THEME, build_page_panel, build_popup, projects_title
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

#: A run's wire mode → the flow step it counts toward on the project page.
_STEP_FOR_MODE: dict[str, str] = {
    "analysis": "team-analysis",
    "standup": "daily-standup",
    "retro": "retro",
    "reporting": "reporting",
}

PLAN_CARD = "project-planning"

# How many runs the project page lists; the Runs page has them all.
_PAGE_RUNS = 8


def _load() -> list[dict]:
    from yeaboi.projects.engine import list_projects

    try:
        return ordered_projects(list_projects())
    except Exception:  # noqa: BLE001 — a broken store is an empty page, not a crash
        logger.error("projects page: list_projects failed", exc_info=True)
        return []


def _reload(project_id: str) -> dict | None:
    from yeaboi.projects.engine import get_project

    try:
        return get_project(project_id)
    except Exception:  # noqa: BLE001 — archived or gone: the page closes
        logger.warning("project page: could not re-read %s", project_id, exc_info=True)
        return None


def _load_sessions(project_id: str) -> list:
    from yeaboi.sessions_recent import recent_sessions

    try:
        return recent_sessions(project_id=project_id, limit=100)
    except Exception:  # noqa: BLE001 — a broken store is an empty page, not a crash
        logger.error("project sessions page: recent_sessions failed", exc_info=True)
        return []


def _plan_fact(project_id: str) -> str:
    """What planning has done inside the project, from projects.json.

    A full-screen planning run persists there (never to ``sessions_meta``),
    stamped with the engine project it ran inside.
    """
    from yeaboi.persistence import load_projects

    try:
        statuses = {p.status for p in load_projects() if p.engine_project_id == project_id}
    except Exception:  # noqa: BLE001 — a corrupt projects.json must not take the page down
        logger.warning("project page: could not read planning projects", exc_info=True)
        return "not yet"
    if "Complete" in statuses:
        return "done"
    if statuses:
        return "in progress"
    return "not yet"


def _inside(project_id: str, rows: list) -> dict[str, str]:
    """The per-step facts the project page's strip shows: what has run inside it."""
    counts: dict[str, int] = {}
    for row in rows:
        step = _STEP_FOR_MODE.get(row.mode)
        if step:
            counts[step] = counts.get(step, 0) + 1
    facts = {step: f"{n} run{'s' if n != 1 else ''}" for step, n in counts.items()}
    for step in _STEP_FOR_MODE.values():
        facts.setdefault(step, "not yet")
    facts[PLAN_CARD] = _plan_fact(project_id)
    # Poker boards keep no per-project record the sessions list can read.
    facts["poker"] = "untracked"
    return facts


def _confirm(console: Console, live, read_key, frame_time: float, supports_timeout: bool, question: str) -> bool:
    """A popup guard; Enter confirms, anything else keeps things as they are."""
    while True:
        w, h = console.size
        popup = build_popup(
            f"{question}\nEnter confirm  ·  Esc keep", width=min(w - 8, 56), border_style=PROJECTS_THEME.warn
        )
        content = Group(Text(""), projects_title(width=w), Text(""), Align.center(popup))
        live.update(build_page_panel(content, theme=PROJECTS_THEME, height=max(10, h - 1)))
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if not key:
            continue
        return key == "enter"


def _toggle_status(project: dict) -> str:
    from yeaboi.projects.engine import set_project_status

    status = "active" if project.get("status") == "done" else "done"
    set_project_status(project["project_id"], status)
    logger.info("Projects: %s is now %s", project["project_id"], status)
    return f"{project['name']} is {'done' if status == 'done' else 'in progress again'}."


def _archive(project: dict) -> str:
    from yeaboi.paths import get_db_path
    from yeaboi.projects.store import ProjectStore

    with ProjectStore(get_db_path()) as store:
        store.archive(project["project_id"])
    if get_active_project() == project["project_id"]:
        set_active_project("")
    logger.info("Projects: archived %s", project["project_id"])
    return f"Archived {project['name']}."


def run_projects_page(
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    *,
    pick: bool = False,
    open_hub: Callable[[str], None] | None = None,
    world: str = "team",
) -> str | tuple[str, str] | None:
    """The Projects list; returns what a project's page chose, or None when backed out.

    A project's Start returns its id; its Plan returns ``(card_key, id)`` so
    the menu opens on that card. ``pick`` marks the door's use (Esc returns
    to the door); the loop is the same either way. ``open_hub(card_key)``
    opens a mode's saved-runs hub — injected by ``select_mode``, which owns
    the hubs.
    """
    projects = _load()
    logger.info(
        "Projects page opened (pick=%s, world=%s): %d project(s), active=%s",
        pick,
        world,
        len(projects),
        get_active_project() or "(none)",
    )
    selected = action_sel = scroll = 0
    scroll_meta: dict = {}
    message = ""
    start = time.monotonic()

    def _open(project: dict) -> str | tuple[str, str] | None:
        return run_project_page(
            console, live, read_key, frame_time, supports_timeout, project=project, open_hub=open_hub, world=world
        )

    while True:
        w, h = console.size
        current = projects[selected] if projects else None
        actions = list_actions(current)
        live.update(
            _build_projects_screen(
                projects,
                world=world,
                selected=selected,
                active_project_id=get_active_project(),
                scroll_offset=scroll,
                scroll_meta=scroll_meta,
                width=w,
                height=h,
                action_sel=action_sel,
                actions=actions,
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
            action_sel = (action_sel - 1) % len(actions)
            continue
        if key == "right":
            action_sel = (action_sel + 1) % len(actions)
            continue

        choice = ""
        if key == "enter":
            choice = actions[action_sel]
        elif key == "d":
            choice = "Done"
        elif key == "a":
            choice = "Archive"
        if not choice:
            continue

        if choice == "Back":
            logger.info("Projects page closed from the buttons")
            return None
        if choice == "New":
            created = run_new_project_page(console, live, read_key, frame_time, supports_timeout)
            if created is not None:
                outcome = _open(created)
                if outcome is not None:
                    return outcome
            projects = _load()
            selected = next(
                (i for i, p in enumerate(projects) if created and p["project_id"] == created["project_id"]), 0
            )
            message = ""
            continue
        if current is None:
            message = "Nothing here yet — New starts one."
            continue
        if choice == "Open":
            outcome = _open(current)
            if outcome is not None:
                return outcome
            message = ""
        elif choice in ("Done", "Reopen"):
            message = _toggle_status(current)
        elif choice == "Archive":
            if not _confirm(console, live, read_key, frame_time, supports_timeout, f"Archive {current['name']}?"):
                continue
            message = _archive(current)
        projects = _load()
        selected = min(selected, max(0, len(projects) - 1))


def run_project_page(
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    *,
    project: dict,
    open_hub: Callable[[str], None] | None = None,
    world: str = "team",
) -> str | tuple[str, str] | None:
    """One project's page; Start returns its id, Plan ``(card_key, id)``, Back None."""
    project_id = project["project_id"]
    rows = _load_sessions(project_id)
    inside = _inside(project_id, rows)
    logger.info("Project page opened: %s (%d run(s))", project_id, len(rows))
    action_sel = 0
    message = ""
    start = time.monotonic()

    while True:
        w, h = console.size
        live.update(
            _build_project_screen(
                project,
                rows[:_PAGE_RUNS],
                inside=inside,
                world=world,
                width=w,
                height=h,
                action_sel=action_sel,
                shimmer_tick=time.monotonic() - start,
                message=message,
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()

        if key in ("esc", "q"):
            logger.info("Project page closed")
            return None
        if key == "left":
            action_sel = (action_sel - 1) % len(PROJECT_ACTIONS)
            continue
        if key == "right":
            action_sel = (action_sel + 1) % len(PROJECT_ACTIONS)
            continue
        if key == "d":
            message = _toggle_status(project)
            project = _reload(project_id) or project
            continue
        if key == "a":
            if _confirm(console, live, read_key, frame_time, supports_timeout, f"Archive {project['name']}?"):
                _archive(project)
                return None
            continue
        if key != "enter":
            continue

        choice = PROJECT_ACTIONS[action_sel]
        if choice == "Back":
            logger.info("Project page closed from the buttons")
            return None
        if choice == "Start":
            set_active_project(project_id)
            logger.info("Projects: started %s", project_id)
            return project_id
        if choice == "Plan":
            set_active_project(project_id)
            logger.info("Projects: planning inside %s", project_id)
            return (PLAN_CARD, project_id)
        if choice == "Runs":
            run_project_sessions_page(
                console, live, read_key, frame_time, supports_timeout, project=project, open_hub=open_hub
            )
        elif choice == "Context":
            run_context_page(console, live, read_key, frame_time, supports_timeout)
        rows = _load_sessions(project_id)
        inside = _inside(project_id, rows)
        message = ""


def _draft_locally(text: str) -> dict:
    from yeaboi.projects.engine import _fallback_name

    return {
        "name": _fallback_name(text),
        "description": text,
        "source": "original",
        "note": "Named from your first words.",
    }


def _rewrite(console: Console, live, read_key, frame_time: float, supports_timeout: bool, text: str) -> dict:
    """Run the AI rewrite on a worker thread behind the shared progress screen."""
    from yeaboi.projects.engine import draft_project_idea
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_progress_screen
    from yeaboi.ui.shared._music_bar import duck_working_thread

    out: list = []
    thread = duck_working_thread(lambda: out.append(draft_project_idea(text)), name="project-draft")
    thread.start()
    started = time.monotonic()
    while thread.is_alive():
        w, h = console.size
        elapsed = time.monotonic() - started
        live.update(
            _build_standup_progress_screen(
                [],
                width=w,
                height=h,
                elapsed=elapsed,
                anim_tick=elapsed,
                theme=PROJECTS_THEME,
                title=projects_title(width=w),
                label="Rewriting with AI",
            )
        )
        if supports_timeout:
            read_key(timeout=frame_time)  # keys are swallowed while the model works
        else:
            time.sleep(frame_time)
    thread.join()
    if not out:  # the engine never raises past a blank draft, so this is a thread failure
        logger.error("Projects: the rewrite thread produced nothing")
        return {**_draft_locally(text), "note": "AI request failed — your words, named from the first few."}
    return out[0]


def run_new_project_page(
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
) -> dict | None:
    """Describe what you're building, preview the draft, create. Returns the row, or None."""
    from yeaboi.projects.engine import create_project
    from yeaboi.ui.mode_select import _standup_read_line

    logger.info("New project page opened")
    text = ""
    while True:
        typed = _standup_read_line(
            console,
            live,
            read_key,
            frame_time,
            supports_timeout,
            prompt="Describe what you're building",
            step="New project",
            theme=PROJECTS_THEME,
            title=projects_title(width=console.size[0]),
            box_rows=4,
            initial=text,
            message="A few sentences is enough. yeaboi names it, and AI rewrite can sharpen the pitch.",
        )
        if typed is None:
            logger.info("New project cancelled at the description")
            return None
        text = " ".join(typed.split()).strip()
        if not text:
            continue
        draft = _draft_locally(text)
        action_sel = 0
        message = ""
        start = time.monotonic()
        edit = False
        while not edit:
            w, h = console.size
            live.update(
                _build_draft_screen(
                    draft,
                    width=w,
                    height=h,
                    action_sel=action_sel,
                    shimmer_tick=time.monotonic() - start,
                    sub_reveal=(time.monotonic() - start) * 6.0,
                    message=message,
                )
            )
            key = read_key(timeout=frame_time) if supports_timeout else read_key()
            if key in ("esc", "q"):
                logger.info("New project cancelled at the preview")
                return None
            if key == "left":
                action_sel = (action_sel - 1) % len(DRAFT_ACTIONS)
            elif key == "right":
                action_sel = (action_sel + 1) % len(DRAFT_ACTIONS)
            elif key == "enter":
                choice = DRAFT_ACTIONS[action_sel]
                if choice == "Cancel":
                    logger.info("New project cancelled from the buttons")
                    return None
                if choice == "Edit":
                    text = draft["description"]
                    edit = True
                elif choice == "AI rewrite":
                    logger.info("New project: AI rewrite requested (%d chars)", len(draft["description"]))
                    draft = _rewrite(console, live, read_key, frame_time, supports_timeout, draft["description"])
                    logger.info("New project: AI rewrite %s", draft["source"])
                    start = time.monotonic()
                elif choice == "Create":
                    try:
                        project = create_project(draft["name"], draft["description"])
                    except Exception as exc:  # noqa: BLE001 — say why, keep the draft on screen
                        logger.error("New project: create failed", exc_info=True)
                        message = f"Could not create it: {exc}"
                        continue
                    logger.info("New project created: %s (%s)", project["project_id"], project["name"])
                    return project


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
