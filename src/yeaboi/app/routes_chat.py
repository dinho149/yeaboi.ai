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

#: What the window may paste. Mirrors ``_attachments._EXT_FOR_MIME``, which is
#: keyed the same way but private to the terminal's clipboard path.
_EXT_FOR_IMAGE = {"image/png": ".png", "image/jpeg": ".jpg"}


def create(app, request: Request) -> Response:
    """``POST /api/chat/sessions`` — open a conversation on a description."""
    payload = request.json()
    description = str(payload.get("description", "")).strip()
    if not description:
        raise HTTPError(400, "description is required")
    intake_mode = str(payload.get("intake_mode", ""))
    if intake_mode not in ("", "small_project", "smart"):
        raise HTTPError(400, "intake_mode must be 'small_project' or 'smart'")
    solo = bool(payload.get("solo", False))
    if solo:
        logger.info("Chat create: solo intake")
    chat = app.chats.create(description, intake_mode=intake_mode, solo=solo)
    app.chats.save(chat)
    return json_response(_view(chat), code=201)


def get(app, request: Request) -> Response:
    """``GET /api/chat/sessions/{project_id}`` — the whole conversation, replayed."""
    return json_response(_view(_chat(app, request)))


def send(app, request: Request) -> Response:
    """``POST /api/chat/sessions/{project_id}/send`` — one turn, streamed as NDJSON.

    The first line names the operation id, so the client can cancel the turn
    through ``POST /api/ops/{op_id}/cancel`` before the reply lands.

    ``images`` is the composer's attachment list, in order. Which of them
    actually travel is decided here by :func:`referenced_images`, so deleting
    an ``[image #N]`` chip detaches its image on this surface exactly as it
    does in the terminal — one implementation of the rule, not two.
    """
    from yeaboi.ui.shared._attachments import referenced_images

    payload = request.json()
    text = str(payload.get("text", ""))
    attachments = [str(name) for name in payload.get("images") or []]
    images = referenced_images(text, attachments) if attachments else []
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


def questions(app, request: Request) -> Response:
    """``GET /api/chat/sessions/{project_id}/questions`` — this run's question plan.

    Every question the run touches: the essential gaps still to ask, plus
    anything already answered. Not the 30-question bank — extraction, SCRUM.md
    and defaults answer most of it silently, and listing all thirty would
    misdescribe the conversation the person is actually having.

    One payload serves three affordances the terminal keeps apart: the
    ``/questions`` checklist, the ``/form`` questionnaire, and the answer
    browser behind a bare ``/edit``.
    """
    from yeaboi.agent.state import TOTAL_QUESTIONS, QuestionnaireState
    from yeaboi.prompts.intake import QUESTION_SHORT_LABELS
    from yeaboi.ui.session.chat._question_view import planned_question_sets

    chat = _chat(app, request)
    qs = chat.session.state.get("questionnaire")
    if not isinstance(qs, QuestionnaireState):
        # Pre-graph: the questionnaire only exists after the first invoke.
        return json_response({"questions": [], "total": TOTAL_QUESTIONS, "completed": False, "derived": False})

    sets = planned_question_sets(qs)
    remaining = set(sets[0]) if sets else set()
    answered = {number for number, answer in qs.answers.items() if answer}
    rows = [
        {
            "number": number,
            "label": QUESTION_SHORT_LABELS.get(number, f"Question {number}"),
            "answer": qs.answers.get(number, ""),
            "remaining": number in remaining,
            "skipped": number in qs.skipped_questions,
        }
        for number in sorted(remaining | answered)
    ]
    return json_response(
        {
            "questions": rows,
            "total": TOTAL_QUESTIONS,
            "completed": bool(qs.completed),
            # False when the gap derivation failed — the client then says so
            # rather than presenting a short list as the whole plan.
            "derived": sets is not None,
        }
    )


def size(app, request: Request) -> Response:
    """``POST /api/chat/sessions/{project_id}/size`` — switch the plan size mid-run.

    The answers survive the switch; the pipeline artifacts do not, because
    they were generated for the other mode. Returns ``changed: false`` when
    the conversation is already that size, so the caller says so rather than
    running a pointless turn.
    """
    from yeaboi.agent.nodes import apply_size_switch

    payload = request.json()
    mode = str(payload.get("mode", ""))
    if mode not in ("small_project", "smart"):
        raise HTTPError(400, "mode must be 'small_project' or 'smart'")
    chat = _chat(app, request)
    if chat.session.dry_run:
        raise HTTPError(409, "Size switching is not available in dry-run")
    state = chat.session.state
    if state.get("_intake_mode") == mode:
        return json_response({"changed": False, "mode": mode})
    if state.get("questionnaire") is None:
        # Pre-intake there is nothing to reset — record the preference and the
        # size exchange honours it.
        state["_intake_mode"] = mode
        app.chats.save(chat)
        return json_response({"changed": True, "mode": mode, "reopened": False})
    apply_size_switch(state, mode)
    # The prior-art step re-runs under the new mode, so its old card has no
    # data left to render from.
    state.pop("_prior_art_preview", None)
    app.chats.save(chat)
    logger.info("Chat size switched: project=%s mode=%s", chat.project_id, mode)
    return json_response({"changed": True, "mode": mode, "reopened": True})


def attach(app, request: Request) -> Response:
    """``POST /api/chat/sessions/{project_id}/attachments`` — keep one pasted image.

    The window reads the clipboard itself (the terminal cannot), so what
    arrives here is bytes rather than a paste event. Everything downstream is
    the terminal's: the same size ceiling, the same attachments directory, and
    the same ``[image #N]`` chip, which is what makes the image detachable by
    deleting text.
    """
    import base64
    import binascii
    import uuid

    from yeaboi.paths import get_attachments_dir
    from yeaboi.ui.shared._attachments import MAX_IMAGE_BYTES, chip_text

    chat = _chat(app, request)
    payload = request.json()
    mime = str(payload.get("mime", "image/png"))
    if mime not in _EXT_FOR_IMAGE:
        raise HTTPError(400, f"unsupported image type {mime!r} — paste a PNG or a JPEG")
    try:
        data = base64.b64decode(str(payload.get("image", "")), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPError(400, "image must be base64") from None
    if not data:
        raise HTTPError(400, "no image was sent")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPError(413, f"Image too large ({len(data) / (1024 * 1024):.1f} MB, max 4.5 MB)")

    index = int(payload.get("index", 1))
    path = get_attachments_dir(chat.project_id) / f"img-{uuid.uuid4().hex[:8]}{_EXT_FOR_IMAGE[mime]}"
    try:
        path.write_bytes(data)
    except OSError as exc:
        logger.error("failed to save pasted image to %s: %s", path, exc)
        raise HTTPError(500, "Could not save pasted image") from None
    logger.info("image pasted: project=%s bytes=%d mime=%s", chat.project_id, len(data), mime)
    return json_response({"path": str(path), "chip": chip_text(index)})


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
        # Non-empty only until the description has been sent as the first turn.
        "opening": state.get("_chat_opening", ""),
        "question": to_jsonable(derive_question_view(state)),
    }


def _chat(app, request: Request) -> LiveChat:
    project_id = request.params.get("project_id", "")
    try:
        return app.chats.open(project_id)
    except UnknownChatError:
        raise HTTPError(404, f"no conversation {project_id!r}") from None
