"""Niko routes — the global assistant over HTTP.

One turn is a chunked NDJSON stream, the same shape ``routes_chat`` uses and for
the same reason: loopback has no proxy buffering, so a stream is simply the
right shape. The ambient SSE feed is never used for request-scoped data.

The turn runs on a worker thread and the generator drains its queue, because
``engine.ask`` calls back synchronously and a generator cannot yield from inside
a callback. The turn lock and the operation entry are released in the
generator's ``finally``, so a disconnected client frees them too — lifted from
``routes_chat._turn``, which solved exactly this.

Niko's tools call the engines directly rather than through the MCP dispatcher
(see ``yeaboi/niko/tools.py``), so a turn holds ``_ENGINE_LOCK`` exactly once.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Iterator

from yeaboi.app.router import HTTPError, Request, Response, json_response

logger = logging.getLogger(__name__)

#: The stream is over. Never reaches the wire.
_END = object()

#: Conversations the list route returns. The panel shows a short history; the
#: rest stay readable through the terminal's own hub.
LIST_LIMIT = 30


def create(app, request: Request) -> Response:
    """``POST /api/niko/conversations`` — open a thread."""
    from yeaboi.niko.store import NikoStore

    with NikoStore() as store:
        conversation = store.create()
    logger.info("Niko conversation opened: %s", conversation.id)
    return json_response(_view(conversation, []), code=201)


def conversations(app, request: Request) -> Response:
    """``GET /api/niko/conversations`` — the panel's history list."""
    from yeaboi.niko.store import NikoStore

    with NikoStore() as store:
        rows = store.conversations(limit=LIST_LIMIT)
    return json_response(
        {
            "conversations": [
                {
                    "id": row.id,
                    "title": row.title,
                    "messages": row.message_count,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
                for row in rows
            ]
        }
    )


def get(app, request: Request) -> Response:
    """``GET /api/niko/conversations/{conversation_id}`` — the whole thread, replayed."""
    from yeaboi.niko.store import NikoStore

    conversation_id = request.params.get("conversation_id", "")
    with NikoStore() as store:
        conversation = store.get(conversation_id)
        if conversation is None:
            raise HTTPError(404, f"no conversation {conversation_id!r}")
        messages = store.messages(conversation_id)
    return json_response(_view(conversation, messages))


def delete(app, request: Request) -> Response:
    """``POST /api/niko/conversations/{conversation_id}/delete`` — archive a thread.

    A POST rather than a DELETE because the router serves GET and POST only,
    and archived rather than purged because a conversation is a record of what
    the user was told. The terminal's hub does the permanent delete.
    """
    from yeaboi.niko.store import NikoStore

    conversation_id = request.params.get("conversation_id", "")
    with NikoStore() as store:
        if not store.archive(conversation_id):
            raise HTTPError(404, f"no conversation {conversation_id!r}")
    logger.info("Niko conversation archived: %s", conversation_id)
    return json_response({"archived": True, "id": conversation_id})


def send(app, request: Request) -> Response:
    """``POST /api/niko/conversations/{conversation_id}/send`` — one turn, streamed.

    The first line names the operation id, so the panel's Stop button can cancel
    the turn through ``POST /api/ops/{op_id}/cancel`` before the answer lands.
    """
    from yeaboi.niko.store import NikoStore

    conversation_id = request.params.get("conversation_id", "")
    payload = request.json()
    question = str(payload.get("question", "")).strip()
    if not question:
        raise HTTPError(400, "question is required")
    route = str(payload.get("route", ""))
    user_name = str(payload.get("user_name", ""))

    with NikoStore() as store:
        if store.get(conversation_id) is None:
            raise HTTPError(404, f"no conversation {conversation_id!r}")

    lock = app.niko_turns.setdefault(conversation_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPError(409, "a turn is already running for this conversation")
    try:
        op = app.ops.create()
    except Exception:
        lock.release()
        raise
    logger.info("Niko turn start: conversation=%s route=%s len=%d", conversation_id, route or "-", len(question))
    return Response(
        content_type="application/x-ndjson",
        stream=_lines(_turn(app, conversation_id, lock, op, question, route, user_name)),
        headers=(("X-Accel-Buffering", "no"),),
    )


def suggestions(app, request: Request) -> Response:
    """``GET /api/niko/suggestions?route=…`` — the chips the empty panel offers."""
    from yeaboi.niko.suggestions import for_route

    route = request.query.get("route", "")
    return json_response({"route": route, "suggestions": for_route(route)})


def _turn(app, conversation_id: str, lock, op, question: str, route: str, user_name: str) -> Iterator[dict]:
    from yeaboi.mcp.runtime import _ENGINE_LOCK
    from yeaboi.niko import engine

    events: queue.Queue = queue.Queue()
    failure: list[BaseException | None] = [None]
    answer: list[object] = [None]

    def worker() -> None:
        try:
            # Engines are one-at-a-time process-wide; a Niko turn is one of
            # them. Never fork this lock — a second one serialises nothing.
            with _ENGINE_LOCK:
                answer[0] = engine.ask(
                    question,
                    conversation_id=conversation_id,
                    route=route,
                    user_name=user_name,
                    surface="desktop",
                    on_event=events.put,
                    cancel=op.cancel,
                )
        except BaseException as exc:  # noqa: BLE001 — reported on the stream below
            failure[0] = exc
        finally:
            events.put(_END)

    thread = threading.Thread(target=worker, name="niko-turn", daemon=True)
    thread.start()
    try:
        yield {"type": "op", "op_id": op.op_id}
        while (event := events.get()) is not _END:
            wired = _wire(event)
            if wired is not None:
                yield wired
        thread.join()
        if failure[0] is not None:
            yield _error_line(failure[0])
        else:
            yield _done_line(answer[0], conversation_id, cancelled=op.cancel.is_set())
    finally:
        app.ops.remove(op.op_id)
        lock.release()


def _done_line(answer, conversation_id: str, *, cancelled: bool) -> dict:
    if cancelled:
        return {"type": "cancelled"}
    warnings = list(getattr(answer, "warnings", ()) or ())
    return {
        "type": "done",
        "conversation_id": getattr(answer, "conversation_id", conversation_id),
        "route": getattr(answer, "route", ""),
        "warnings": warnings,
    }


def _error_line(error: BaseException) -> dict:
    # The one place SDK exceptions become human text — never str(exc), which
    # for a JIRAError is its entire HTTP response.
    from yeaboi.ui.session._utils import _classify_api_error

    message = _classify_api_error(error) if isinstance(error, Exception) else "The turn stopped unexpectedly."
    logger.error("Niko turn failed: %s", message)
    return {"type": "error", "message": message}


def _lines(objects: Iterator[dict]) -> Iterator[bytes]:
    for obj in objects:
        yield (json.dumps(obj, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def _wire(event) -> dict | None:
    """One Niko event as its wire object. The type tag is the contract.

    ``Done`` returns None: the engine emits it when its own turn ends, but the
    stream's terminator carries the answer's route and warnings, which that
    event does not — two ``done`` lines would be one too many.
    """
    from yeaboi.niko import engine

    if isinstance(event, engine.Token):
        return {"type": "token", "text": event.text}
    if isinstance(event, engine.Assistant):
        return {"type": "assistant", "text": event.text}
    if isinstance(event, engine.ToolStarted):
        return {"type": "tool_call", "tool_name": event.name, "tool_input": event.arguments}
    if isinstance(event, engine.ToolFinished):
        return {
            "type": "tool_result",
            "tool_name": event.call.name,
            "ok": event.call.ok,
            "error": event.call.error,
        }
    if isinstance(event, engine.Navigate):
        return {"type": "navigate", "route": event.route}
    if isinstance(event, engine.Done):
        return None
    raise TypeError(f"no wire shape for Niko event {type(event).__name__}")


def _view(conversation, messages) -> dict:
    """The whole conversation as the panel draws it."""
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
        "messages": [
            {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "route": message.route,
                "created_at": message.created_at,
                "tool_calls": [
                    {"tool_name": call.name, "ok": call.ok, "error": call.error} for call in message.tool_calls
                ],
            }
            for message in messages
        ],
    }
