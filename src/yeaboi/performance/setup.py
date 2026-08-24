"""Who a Performance surface can run its three workflows for.

The roster is not a rendering decision: it is the people who did work on the
board, with the saved plan's team as the fallback when no tracker is reachable,
plus a one-line status per person that only the store can answer. All of it was
spelled inline in the terminal page; both surfaces read it from here.

Deliberately ``setup.py`` and not ``engine.py``: the engine glob in the parity
test registers every public name in ``engine.py`` as a capability of its own.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: The workflows a picked engineer offers, in the order both surfaces show them.
ACTIONS = ("prep", "complete", "review", "notes", "history")

ACTION_LABELS = {
    "prep": "1:1 Prep",
    "complete": "1:1 Complete",
    "review": "6mo Review",
    "notes": "Notes",
    "history": "History",
}

GENERIC_HINT = "1:1 prep · completion · 6-month review"

NO_ROSTER_MESSAGE = "No engineers found — connect Jira or Azure DevOps, or plan a project with a team."


def _db(db_path):
    if db_path is not None:
        return db_path
    from yeaboi.paths import get_db_path

    return get_db_path()


def collect_roster(*, db_path=None) -> dict:
    """``{session_id, session_name, roster, hints}`` — everything a roster view needs.

    Best-effort throughout: the page still works with no session and no tracker.
    """
    data: dict = {"session_id": "", "session_name": "", "roster": [], "hints": []}
    path = _db(db_path)
    try:
        from yeaboi.sessions import SessionStore, make_display_name

        with SessionStore(path) as store:
            session_id = store.get_latest_session_id() or ""
            data["session_id"] = session_id
            if session_id:
                meta = store.get_session(session_id) or {}
                data["session_name"] = make_display_name(meta) if meta else session_id
    except Exception:  # noqa: BLE001 — session context is optional
        logger.warning("performance setup: failed to resolve the latest session", exc_info=True)
    try:
        from yeaboi.performance.roster import fetch_roster

        data["roster"] = [r.name for r in fetch_roster()]
    except Exception:  # noqa: BLE001 — a tracker outage falls back to the plan below
        logger.warning("performance setup: failed to fetch the roster", exc_info=True)
    if not data["roster"] and data["session_id"]:
        data["roster"] = session_team(data["session_id"], db_path=path)
        if data["roster"]:
            logger.info("performance setup: roster fell back to session team members")
    data["hints"] = roster_hints(data["roster"], db_path=path)
    logger.info("performance setup: %d engineer(s) in roster", len(data["roster"]))
    return data


def session_team(session_id: str, *, db_path=None) -> list[str]:
    """The session's team-member names — the fallback roster when no tracker answers.

    Reads ``selected_team_members`` from the saved plan state, the same
    board-derived roster the standup uses. De-duplicated, order-independent.
    """
    try:
        from yeaboi.sessions import SessionStore

        with SessionStore(_db(db_path)) as store:
            state = store.load_state(session_id) or {}
    except Exception:  # noqa: BLE001 — an unreadable store is an empty fallback
        logger.warning("performance setup: failed to load session team members", exc_info=True)
        return []
    names = [str(n).strip() for n in (state.get("selected_team_members") or ()) if str(n).strip()]
    return sorted(dict.fromkeys(names), key=str.lower)


def roster_hints(roster: list[str], *, db_path=None) -> list[str]:
    """One status line per engineer: open 1:1 actions, and whether a review is on file."""
    if not roster:
        return []
    try:
        from yeaboi.performance.store import PerformanceStore

        with PerformanceStore(_db(db_path)) as store:
            open_actions = store.get_all_open_action_items()
            hints: list[str] = []
            for name in roster:
                count = len(open_actions.get(name, ()))
                hint = f"{count} open 1:1 action{'s' if count != 1 else ''}" if count else "no open 1:1 actions"
                if store.get_latest_review(name) is not None:
                    hint += " · review on file"
                hints.append(hint)
            return hints
    except Exception:  # noqa: BLE001 — a store error still renders the page
        logger.warning("performance setup: failed to build roster hints", exc_info=True)
        return [GENERIC_HINT for _ in roster]
