"""Where ship finds a plan to ship — across BOTH stores yeaboi persists plans to.

yeaboi keeps completed plans in two places, by entry path:

- the **interactive planning chat** saves the whole graph state to
  ``persistence.py`` (``~/.yeaboi/data/states/<project-id>.json`` indexed by
  ``projects.json``);
- the **MCP ``plan_*`` tools and the headless pipeline** save to the SQLite
  ``SessionStore`` (``sessions.py``).

Ship was written against the SQLite store only, so a plan built in the chat — the
primary planning UX — is invisible to it: the picker shows "no stories" over a
plan that plainly exists. This module is the one place that reconciles the two,
used by both the picker (``_load_stories``) and the engine (``_load_story``) so
they can never disagree about where a plan lives.

Identifiers do not collide between the stores (project ids are UUIDs, session
ids are ``new-<hex>-<date>``), so :func:`load_plan_state` can try one then the
other and return the first that resolves.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SESSION_SCAN = 25  # bounded recent-session window; the chat store is the primary source anyway


def _has_stories(state: dict | None) -> bool:
    return bool(state and state.get("stories"))


def load_plan_state(identifier: str, db_path: Path | None = None) -> dict | None:
    """Load the full graph state for ``identifier`` from whichever store has it.

    Tries the interactive project store first (that is where the chat saves, and
    the common case), then the SQLite session store. Never raises — a broken or
    absent store yields ``None`` so the caller can report a plain reason.
    """
    if not identifier:
        return None
    try:
        from yeaboi.persistence import load_graph_state  # noqa: PLC0415 — lazy, avoids a heavy import

        project_state = load_graph_state(identifier)
    except Exception:  # noqa: BLE001 — an unreadable project store must not crash ship
        logger.debug("ship plans: project-store read failed for %s", identifier, exc_info=True)
        project_state = None
    if _has_stories(project_state):
        return project_state

    try:
        from yeaboi.paths import get_db_path  # noqa: PLC0415
        from yeaboi.sessions import SessionStore  # noqa: PLC0415

        with SessionStore(db_path or get_db_path()) as sessions:
            session_state = sessions.load_state(identifier)
    except Exception:  # noqa: BLE001
        logger.debug("ship plans: session-store read failed for %s", identifier, exc_info=True)
        session_state = None
    # Prefer whichever actually carries a plan; fall back to a bare project state
    # so a caller still gets a real reason ("has no stories") rather than "not found".
    return session_state or project_state


def latest_plan_with_stories(db_path: Path | None = None) -> tuple[list, str, str] | None:
    """The most recent plan that actually has stories: ``(stories, id, name)``.

    Prefers the interactive project store (where the planning chat saves), then
    falls back to a bounded scan of the newest SQLite sessions. Returns ``None``
    when neither store holds a plan with stories — the honest "generate a plan
    first" case. The returned id is what :func:`load_plan_state` reloads by, so
    the picker and the run agree on the source.
    """
    # 1) Interactive projects — load_projects() is sorted most-recent-first.
    try:
        from yeaboi.persistence import load_graph_state, load_projects  # noqa: PLC0415

        for project in load_projects():
            if getattr(project, "story_count", 0) <= 0:
                continue
            state = load_graph_state(project.id)
            if _has_stories(state):
                return list(state["stories"]), project.id, getattr(project, "name", "")
    except Exception:  # noqa: BLE001
        logger.debug("ship plans: scanning the project store failed", exc_info=True)

    # 2) SQLite sessions (MCP / headless) — bounded recent window.
    try:
        from yeaboi.paths import get_db_path  # noqa: PLC0415
        from yeaboi.sessions import SessionStore  # noqa: PLC0415

        with SessionStore(db_path or get_db_path()) as sessions:
            for sid in sessions.recent_session_ids(_SESSION_SCAN):
                state = sessions.load_state(sid)
                if _has_stories(state):
                    name = str(state.get("project_name") or "")
                    return list(state["stories"]), sid, name
    except Exception:  # noqa: BLE001
        logger.debug("ship plans: scanning the session store failed", exc_info=True)

    return None
