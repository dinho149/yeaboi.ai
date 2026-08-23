"""Native performance routes — the roster, and one engineer's file.

The three workflows are MCP tools (``perf_one_on_one_prep``,
``perf_one_on_one_complete``, ``perf_six_month_review``) and a note is
``perf_note_add``, so a headless caller already has all four; each is a single
LLM call with no progress or cancel seam, which is exactly what the dispatcher
serves well. What lives here is what MCP has no shape for: the roster with the
status line under each name, and everything already on file for one engineer.
"""

from __future__ import annotations

import logging

from yeaboi.app.router import HTTPError, Request, Response, json_response
from yeaboi.mcp.runtime import to_jsonable

logger = logging.getLogger(__name__)


def roster(app, request: Request) -> Response:
    """``GET /api/performance/roster`` — who can be reviewed, and where they stand."""
    from yeaboi.performance import setup

    data = setup.collect_roster()
    return json_response(
        {
            "session_id": data["session_id"],
            "session_name": data["session_name"],
            "engineers": [
                {"name": name, "hint": hint}
                for name, hint in zip(data["roster"], data["hints"] or [""] * len(data["roster"]))
            ],
            "actions": [{"key": key, "label": setup.ACTION_LABELS[key]} for key in setup.ACTIONS],
            "empty_message": setup.NO_ROSTER_MESSAGE,
        }
    )


def engineer(app, request: Request) -> Response:
    """``GET /api/performance/engineer/{name}`` — the artifacts already on file.

    ``latest`` is the one a result screen opens: review beats completion beats
    prep, the same usefulness order every surface uses.
    """
    from yeaboi.paths import get_db_path
    from yeaboi.performance.store import PerformanceStore
    from yeaboi.sharing import resolve

    name = request.params.get("name", "")
    if not name:
        raise HTTPError(400, "an engineer name is required")
    db_path = get_db_path()
    if not db_path.exists():
        raise HTTPError(404, f"nothing on file for {name!r}")
    with PerformanceStore(db_path) as store:
        prep = store.get_latest_prep(name)
        review = store.get_latest_review(name)
        completions = store.get_recent_completions(name, limit=12)
        open_actions = store.get_open_action_items(name)
        notes = store.get_notes(name, limit=50)
        history = store.get_engineer_history(name, limit=100)
    found = resolve.load("performance", session_id=name, db_path=db_path)
    return json_response(
        {
            "engineer": name,
            "prep": to_jsonable(prep) if prep is not None else None,
            "review": to_jsonable(review) if review is not None else None,
            "completions": [to_jsonable(record) for record in completions],
            "open_actions": list(open_actions),
            "notes": to_jsonable(notes),
            "history": to_jsonable(history),
            # The reference a result screen hands to export/share/anonymize, and
            # which of the three artifacts it addresses.
            "latest": (
                None
                if found is None
                else {
                    "title": found.title,
                    "artifact_kind": (found.extras or {}).get("artifact_kind", ""),
                    "artifact": to_jsonable(found.artifact),
                }
            ),
        }
    )
