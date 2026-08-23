"""Native ship routes — pick a story, watch the run, answer the gate.

``ship_history`` and ``ship_status`` are MCP tools, so a headless caller can
already read what happened. Launching is deliberately not one: a run holds a
coding-agent subprocess for many minutes behind the server's engine lock, and
the gate is a human decision. A human-owned desktop app satisfies that, which
is why these routes exist and the MCP surface stays read-only.

The run does **not** stream. It lives in :mod:`yeaboi.app.ships`, in the process
that outlives every window, and a surface polls ``GET /api/ship/runs/{key}`` —
a renderer reload must not be able to abandon a coding agent mid-diff.
"""

from __future__ import annotations

import logging

from yeaboi.app.router import HTTPError, Request, Response, json_response

logger = logging.getLogger(__name__)


def stories(app, request: Request) -> Response:
    """``GET /api/ship/stories`` — the latest saved plan's stories, and where to run them."""
    from pathlib import Path

    from yeaboi.ship import setup

    found, plan_id, project_name, problem = setup.load_stories()
    return json_response(
        {
            "stories": setup.story_options(found),
            "session_id": plan_id,
            "project_name": project_name,
            "problem": problem,
            "empty_message": setup.NO_PLAN_MESSAGE,
            # The picker's default, so both surfaces open on the same repo.
            "default_repo": str(Path.cwd()),
        }
    )


def target(app, request: Request) -> Response:
    """``POST /api/ship/target`` — resolve a typed path to the repo a run will touch.

    The toplevel, not the typed path, is what every write lands on, and it is
    what the sandbox must have granted — so the check answers about the resolved
    path, never the one that was typed.
    """
    from yeaboi import fs_policy
    from yeaboi.ship import setup

    typed = str(request.json().get("repo", ""))
    resolved, problem = setup.resolve_target(typed)
    allowed = bool(resolved) and fs_policy.is_allowed(resolved, mode="write")
    return json_response(
        {
            "repo": resolved,
            "problem": problem,
            "allowed": allowed,
            "consent_hint": (
                "" if allowed or not resolved else f"Add {resolved} under Settings → Paths before launching a run."
            ),
        }
    )


def runs(app, request: Request) -> Response:
    """``GET /api/ship/runs`` — every run this app session has launched."""
    return json_response({"runs": _ships(app).runs()})


def run(app, request: Request) -> Response:
    """``GET /api/ship/runs/{key}`` — one run's phases, gate and result."""
    snapshot = _ships(app).snapshot(request.params.get("key", ""))
    if snapshot is None:
        raise HTTPError(404, "no such ship run")
    return json_response(snapshot)


def launch(app, request: Request) -> Response:
    """``POST /api/ship/runs`` — start one supervised run."""
    from yeaboi import fs_policy
    from yeaboi.ship import setup

    payload = request.json()
    story_id = str(payload.get("story_id", ""))
    if not story_id:
        raise HTTPError(400, "story_id is required")
    resolved, problem = setup.resolve_target(str(payload.get("repo", "")))
    if problem:
        raise HTTPError(400, problem)
    if not fs_policy.is_allowed(resolved, mode="write"):
        # Stated up front rather than discovered by the coding agent: the run
        # would fail deep inside a worktree write, after spending real money.
        raise HTTPError(403, f"{resolved} is outside the allowed paths — add it in Settings → Paths")
    snapshot = _ships(app).start(
        story_id=story_id,
        story_title=str(payload.get("story_title", "")),
        repo=resolved,
        session_id=str(payload.get("session_id", "")),
        check_command=str(payload.get("check_command", "")),
    )
    return json_response(snapshot)


def gate(app, request: Request) -> Response:
    """``POST /api/ship/runs/{key}/gate`` — approve or reject the diff.

    ``taken: false`` is not an error: the store's compare-and-swap means another
    surface answered first, and the honest answer is to say so and re-read.
    """
    from yeaboi.ship import setup

    payload = request.json()
    resolution = str(payload.get("resolution", ""))
    if resolution not in setup.GATE_RESOLUTIONS:
        raise HTTPError(400, f"resolution must be one of {', '.join(setup.GATE_RESOLUTIONS)}")
    ships = _ships(app)
    if ships.run(request.params.get("key", "")) is None:
        raise HTTPError(404, "no such ship run")
    taken = ships.resolve_gate(request.params["key"], resolution, str(payload.get("comment", "")).strip())
    return json_response({"taken": taken, "resolution": resolution})


def cancel(app, request: Request) -> Response:
    """``POST /api/ship/runs/{key}/cancel`` — wind the run down cooperatively."""
    session = _ships(app).run(request.params.get("key", ""))
    if session is None:
        raise HTTPError(404, "no such ship run")
    session.stop()
    return json_response({"cancelling": True})


def _ships(app):
    ships = getattr(app, "ships", None)
    if ships is None:
        raise HTTPError(503, "ship runs are not available in this server")
    return ships
