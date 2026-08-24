"""Native meta routes — what the shell needs before it can do anything.

Each handler takes ``(app, request)`` where ``app`` is the
:class:`~yeaboi.app.server.AppServer`; ``registry.build_router`` binds the
first argument. Everything here is read-only and serves data that already has
a single owner elsewhere (cards, tips, changelog, version) — these handlers
serialize, they never define.
"""

from __future__ import annotations

import logging
import os
import platform
import sys

from yeaboi.app.dispatch import DispatcherUnavailableError
from yeaboi.app.router import HTTPError, Request, Response, json_response
from yeaboi.mcp.runtime import to_jsonable

logger = logging.getLogger(__name__)


def health(app, request: Request) -> Response:
    """Liveness + identity. Unauthenticated by design (loopback-only bind):

    the instance lock's probe must be able to tell "our recorded pid still
    answers here" apart from "some other server recycled the port", and it
    carries nothing a local process could not already learn from `ps`.
    """
    from yeaboi import __version__
    from yeaboi.sessions import CURRENT_SCHEMA_VERSION

    return json_response({"ok": True, "pid": os.getpid(), "version": __version__, "schema": CURRENT_SCHEMA_VERSION})


def version(app, request: Request) -> Response:
    from yeaboi import __version__
    from yeaboi.sessions import CURRENT_SCHEMA_VERSION

    return json_response(
        {
            "version": __version__,
            "schema_version": CURRENT_SCHEMA_VERSION,
            "python": sys.version.split()[0],
            "platform": platform.system().lower(),
        }
    )


def capabilities(app, request: Request) -> Response:
    """The card inventory, verbatim from the TUI's single source of truth.

    Served rather than re-declared so the desktop home screen can never drift
    from ``_MODE_CARDS`` — the same dicts the welcome screen renders.
    """
    from yeaboi.ui.mode_select.screens._screens import _AGENT_CARDS, _INTAKE_CARDS, _MODE_CARDS
    from yeaboi.ui.mode_select.screens._screens_category import _CATEGORY_CARDS

    return json_response(
        to_jsonable(
            {
                "categories": _CATEGORY_CARDS,
                "modes": _MODE_CARDS,
                "agents": _AGENT_CARDS,
                "intake": _INTAKE_CARDS,
            }
        )
    )


def tips(app, request: Request) -> Response:
    """The rotating discoverability tips (voice/music availability resolved)."""
    from yeaboi.ui.shared._tips import get_tips

    # to_jsonable flattens one dataclass, not a list of them — convert per item.
    return json_response({"tips": [to_jsonable(tip) for tip in get_tips()]})


def changelog(app, request: Request) -> Response:
    """The bundled release notes, newest-first — feeds the What's New page."""
    from yeaboi.changelog import load_changelog

    return json_response({"entries": [to_jsonable(entry) for entry in load_changelog()]})


def tools(app, request: Request) -> Response:
    """The MCP tool inventory the dispatcher serves (empty when unavailable)."""
    dispatcher = app.dispatcher
    names = sorted(dispatcher.tool_names()) if dispatcher is not None else []
    available = dispatcher is not None and dispatcher.available
    return json_response({"available": available, "tools": names})


def call_tool(app, request: Request) -> Response:
    """``POST /api/tool/{name}`` — envelope passthrough to the MCP dispatcher."""
    name = request.params["name"]
    dispatcher = app.dispatcher
    if dispatcher is None or not dispatcher.available:
        raise HTTPError(503, "tool dispatch unavailable — " + _dispatch_reason(app))
    if name not in dispatcher.tool_names():
        raise HTTPError(404, f"unknown tool: {name}")
    payload = request.json()
    arguments = payload.get("arguments", {})
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be a JSON object")
    op_id = payload.get("op_id")
    if op_id is not None and not isinstance(op_id, str):
        raise ValueError("op_id must be a string")
    logger.info("tool call: %s (op_id=%s)", name, op_id)
    try:
        return json_response(dispatcher.call_tool(name, arguments, op_id=op_id))
    except DispatcherUnavailableError as exc:  # pragma: no cover - post-start crash path
        raise HTTPError(503, str(exc)) from exc


def cancel_op(app, request: Request) -> Response:
    """``POST /api/ops/{op_id}/cancel`` — set the operation's cancel event."""
    op_id = request.params["op_id"]
    if not app.ops.cancel(op_id):
        raise HTTPError(404, f"unknown operation: {op_id}")
    return json_response({"cancelled": True, "op_id": op_id})


def events(app, request: Request) -> Response:
    """The ambient SSE feed. One long-lived response per subscriber."""
    try:
        stream = app.bus.sse_stream()
    except RuntimeError as exc:  # the subscriber cap — say so instead of streaming nothing
        raise HTTPError(503, str(exc)) from None
    return Response(content_type="text/event-stream", stream=stream, headers=(("X-Accel-Buffering", "no"),))


def shutdown(app, request: Request) -> Response:
    """Ask the backend to exit. The response is sent before the stop lands."""
    logger.info("shutdown requested over the API")
    app.request_shutdown()
    return json_response({"ok": True})


def _dispatch_reason(app) -> str:
    from yeaboi.app.dispatch import MISSING_MCP_HINT

    return MISSING_MCP_HINT
