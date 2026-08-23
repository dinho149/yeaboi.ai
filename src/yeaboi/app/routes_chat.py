"""Native chat routes — the planning conversation over HTTP.

One turn is a chunked NDJSON stream: each line is a typed event from
:mod:`yeaboi.agent.chat_session`, terminated by ``done`` or ``error``.
Loopback has no proxy buffering, so a stream is simply the right shape — the
long-poll rationale that shaped the board servers does not apply here.

The turn runs on a worker thread and the generator drains its queue, because
``ChatSession.send`` calls back synchronously and a generator cannot yield
from inside a callback. The turn lock and the operation entry are released in
the generator's ``finally``, so a disconnected client frees them too.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Iterator

from yeaboi.agent.chat_session import (
    AskQuestion,
    Assistant,
    AwaitConfirm,
    Done,
    ShowArtifact,
    Token,
    UserSaid,
    replay,
)
from yeaboi.app.chats import LiveChat, UnknownChatError
from yeaboi.app.router import HTTPError, Request, Response, json_response
from yeaboi.mcp.runtime import to_jsonable

logger = logging.getLogger(__name__)

#: The stream is over. Never reaches the wire.
_END = object()


def create(app, request: Request) -> Response:
    """``POST /api/chat/sessions`` — open a conversation on a description."""
    payload = request.json()
    description = str(payload.get("description", "")).strip()
    if not description:
        raise HTTPError(400, "description is required")
    intake_mode = str(payload.get("intake_mode", ""))
    if intake_mode not in ("", "small_project", "smart"):
        raise HTTPError(400, "intake_mode must be 'small_project' or 'smart'")
    chat = app.chats.create(description, intake_mode=intake_mode)
    app.chats.save(chat)
    return json_response(_view(chat), code=201)


def get(app, request: Request) -> Response:
    """``GET /api/chat/sessions/{project_id}`` — the whole conversation, replayed."""
    return json_response(_view(_chat(app, request)))


def send(app, request: Request) -> Response:
    """``POST /api/chat/sessions/{project_id}/send`` — one turn, streamed as NDJSON.

    The first line names the operation id, so the client can cancel the turn
    through ``POST /api/ops/{op_id}/cancel`` before the reply lands.
    """
    payload = request.json()
    text = str(payload.get("text", ""))
    images = [str(name) for name in payload.get("images") or []]
    chat = _chat(app, request)
    if not chat.turn.acquire(blocking=False):
        raise HTTPError(409, "a turn is already running for this conversation")
    try:
        op = app.ops.create()
    except Exception:
        chat.turn.release()
        raise
    logger.info("Chat turn start: project=%s len=%d images=%d", chat.project_id, len(text), len(images))
    return Response(
        content_type="application/x-ndjson",
        stream=_lines(_turn(app, chat, op, text, images)),
        headers=(("X-Accel-Buffering", "no"),),
    )


def _turn(app, chat: LiveChat, op, text: str, images: list[str]) -> Iterator[dict]:
    from yeaboi.mcp.runtime import _ENGINE_LOCK

    events: queue.Queue = queue.Queue()
    failure: list[BaseException | None] = [None]

    def worker() -> None:
        try:
            # Engines are one-at-a-time process-wide; a chat turn is one of
            # them. Never fork this lock — a second one serialises nothing.
            with _ENGINE_LOCK:
                chat.session.send(text, events.put, images=images, cancel=op.cancel)
        except BaseException as exc:  # noqa: BLE001 — reported on the stream below
            failure[0] = exc
        finally:
            events.put(_END)

    thread = threading.Thread(target=worker, name="chat-turn", daemon=True)
    thread.start()
    try:
        yield {"type": "op", "op_id": op.op_id}
        while (event := events.get()) is not _END:
            yield _wire(event, chat)
        thread.join()
        if failure[0] is not None:
            yield _error_line(failure[0])
        else:
            app.chats.save(chat)
    finally:
        app.ops.remove(op.op_id)
        chat.turn.release()


def _error_line(error: BaseException) -> dict:
    from yeaboi.agent.streaming import ChatStreamCancelledError

    if isinstance(error, ChatStreamCancelledError):
        return {"type": "cancelled"}
    # The one place SDK exceptions become human text — never str(exc), which
    # for a JIRAError is its entire HTTP response.
    from yeaboi.ui.session._utils import _classify_api_error

    message = _classify_api_error(error) if isinstance(error, Exception) else "The turn stopped unexpectedly."
    logger.error("Chat turn failed: %s", message)
    return {"type": "error", "message": message}


def _lines(objects: Iterator[dict]) -> Iterator[bytes]:
    for obj in objects:
        yield (json.dumps(obj, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def _wire(event, chat: LiveChat) -> dict:
    """One chat event as its wire object. The type tag is the contract."""
    if isinstance(event, Token):
        return {"type": "token", "text": event.text}
    if isinstance(event, Assistant):
        return {"type": "assistant", "text": event.text}
    if isinstance(event, UserSaid):
        return {"type": "user", "text": event.text}
    if isinstance(event, AskQuestion):
        return {"type": "question", "text": event.text, "number": event.number}
    if isinstance(event, AwaitConfirm):
        return {"type": "await_confirm", "kind": event.kind, "prompt": event.prompt}
    if isinstance(event, ShowArtifact):
        return {"type": "artifact", "kind": event.kind}
    if isinstance(event, Done):
        return {"type": "done", "stage": chat.session.awaiting}
    raise TypeError(f"no wire shape for chat event {type(event).__name__}")


def _view(chat: LiveChat) -> dict:
    """The whole conversation as the renderer draws it."""
    from yeaboi.ui.session.chat._question_view import derive_question_view

    state = chat.session.state
    return {
        "project_id": chat.project_id,
        "stage": chat.session.awaiting,
        "transcript": [_wire(item, chat) for item in replay(state)],
        "question": to_jsonable(derive_question_view(state)),
    }


def _chat(app, request: Request) -> LiveChat:
    project_id = request.params.get("project_id", "")
    try:
        return app.chats.open(project_id)
    except UnknownChatError:
        raise HTTPError(404, f"no conversation {project_id!r}") from None
