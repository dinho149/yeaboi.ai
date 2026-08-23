"""Native board routes — starting a retro or poker board, and hosting it.

The board itself is served by the mode's own loopback server on its own port;
these routes are the *host controls* around it. A surface starts a board, polls
its snapshot while the ceremony runs, retries the secure link if it failed, and
closes it — which is also what flushes the ceremony to its mode's store.

``host_url`` is the one private field in a snapshot: it carries the admin token
that makes its holder the host. It is returned because opening the board window
needs it, and it must never be handed out as an invite.

Poker setup is two calls, not one: ``GET /api/poker/options`` answers what this
machine can offer, ``POST /api/poker/tickets`` fetches a scope, and the ticket
list is passed to ``POST /api/boards/poker``. Splitting it that way is what lets
a surface show the tickets before committing the table to them.
"""

from __future__ import annotations

import logging

from yeaboi.app.router import HTTPError, Request, Response, json_response

logger = logging.getLogger(__name__)


def boards(app, request: Request) -> Response:
    """``GET /api/boards`` — every board this process is hosting."""
    return json_response({"boards": [session.snapshot() for session in app.boards.boards()]})


def board(app, request: Request) -> Response:
    """``GET /api/boards/{board_id}`` — one board's host controls and contents."""
    return json_response(_require(app, request).snapshot())


def start_retro(app, request: Request) -> Response:
    """``POST /api/boards/retro`` — open a retro board for the latest session."""
    from yeaboi.retro.setup import NO_SESSION_MESSAGE

    try:
        session = app.boards.start_retro()
    except ValueError:
        raise HTTPError(409, NO_SESSION_MESSAGE) from None
    except OSError as exc:
        raise HTTPError(503, f"could not start the retro server: {exc}") from None
    return json_response(session.snapshot())


def start_poker(app, request: Request) -> Response:
    """``POST /api/boards/poker`` — open a poker table over a fetched scope."""
    payload = request.json()
    tickets = payload.get("tickets") or []
    if not isinstance(tickets, list) or not tickets:
        raise HTTPError(400, "tickets must be a non-empty list — fetch them with /api/poker/tickets first")
    try:
        session = app.boards.start_poker(
            source=str(payload.get("source", "demo")),
            scope_label=str(payload.get("scope_label", "")),
            tickets=tickets,
        )
    except OSError as exc:
        raise HTTPError(503, f"could not start the poker server: {exc}") from None
    return json_response(session.snapshot())


def retry_link(app, request: Request) -> Response:
    """``POST /api/boards/{board_id}/link`` — try the secure link again.

    A no-op while one attempt is already running, which is what the button on
    every host surface is gated on.
    """
    session = _require(app, request)
    session.link.start()
    return json_response({"link": session.link.snapshot()})


def generate_actions(app, request: Request) -> Response:
    """``POST /api/boards/{board_id}/actions`` — draft this retro's action items.

    One LLM call that never raises: an empty board comes back as a message, not
    an error, exactly as it does in the terminal.
    """
    session = _require(app, request)
    if session.kind != "retro":
        raise HTTPError(400, "action items are a retro board's control")
    from yeaboi.mcp.runtime import _ENGINE_LOCK
    from yeaboi.retro.engine import generate_action_items

    # Engines are one-at-a-time process-wide. Never fork this lock.
    with _ENGINE_LOCK:
        message = generate_action_items(session.board)
    logger.info("retro board: generate action items → %s", message)
    return json_response({"message": message, "state": session.snapshot()["state"]})


def invite(app, request: Request) -> Response:
    """``GET /api/boards/{board_id}/invite`` — the one link a teammate gets.

    Empty until the tunnel lands: before then there is no address that works for
    a reader, and an invite carrying the code alone sends the host into a chat
    window with nothing to click.
    """
    from yeaboi.sharing.access import invite_url

    session = _require(app, request)
    return json_response(
        {
            "invite": invite_url(session.server.share_url, session.server.display_code),
            "display_code": session.server.display_code,
        }
    )


def close_board(app, request: Request) -> Response:
    """``POST /api/boards/{board_id}/close`` — end the board and record it."""
    board_id = request.params.get("board_id", "")
    run_id = app.boards.stop_board(board_id)
    if run_id is None:
        raise HTTPError(404, f"no live board {board_id!r}")
    return json_response({"closed": True, "board_id": board_id, "run_id": run_id})


def poker_options(app, request: Request) -> Response:
    """``GET /api/poker/options`` — what a poker setup wizard may offer here."""
    from yeaboi.poker import setup

    return json_response(
        {
            "steps": list(setup.STEPS),
            "titles": dict(setup.STEP_TITLES),
            "sources": setup.source_options(),
            "source_hint": setup.source_hint(),
            "scopes": setup.scope_options(),
        }
    )


def poker_sprints(app, request: Request) -> Response:
    """``GET /api/poker/sprints`` — one source's sprint list, cursor included."""
    from yeaboi.poker import setup
    from yeaboi.poker.tickets import list_sprints

    source = str(request.query.get("source", ""))
    if not source:
        raise HTTPError(400, "source is required")
    sprints = list_sprints(source)
    return json_response(
        {
            "sprints": sprints,
            "options": setup.sprint_options(sprints),
            "default_index": setup.default_sprint_index(sprints),
        }
    )


def poker_types(app, request: Request) -> Response:
    """``GET /api/poker/types`` — the ticket-type toggles for one source."""
    from yeaboi.poker import setup

    source = str(request.query.get("source", ""))
    if not source:
        raise HTTPError(400, "source is required")
    return json_response({"types": setup.type_options(source), "hint": setup.type_hint(source)})


def poker_tickets(app, request: Request) -> Response:
    """``POST /api/poker/tickets`` — fetch the tickets one scope would estimate."""
    from yeaboi.poker import setup
    from yeaboi.poker.tickets import fetch_tickets

    payload = request.json()
    source = str(payload.get("source", ""))
    if not source:
        raise HTTPError(400, "source is required")
    sprint = payload.get("sprint") or None
    scope = str(payload.get("scope", ""))
    scope_label = setup.scope_label_for(source=source, scope=scope, sprint=sprint)
    include_types = setup.include_types_for(source, payload.get("include_types"))
    tickets = fetch_tickets(source, sprint=sprint, include_types=include_types)
    logger.info("poker tickets: source=%s scope=%s → %d", source, scope_label, len(tickets))
    return json_response(
        {
            "tickets": tickets,
            "scope_label": scope_label,
            "source": source,
            # An empty answer is a configuration story, not an error — the same
            # sentence the terminal shows on its own dead end.
            "message": "" if tickets else setup.empty_result_message(source, scope_label),
        }
    )


def _require(app, request: Request):
    board_id = request.params.get("board_id", "")
    session = app.boards.board(board_id)
    if session is None:
        raise HTTPError(404, f"no live board {board_id!r}")
    return session
