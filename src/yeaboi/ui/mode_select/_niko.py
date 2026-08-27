"""The Niko page loop — one conversation with the global assistant.

The turn runs on a daemon thread and streams into a buffer the render loop
joins each frame: a turn makes LLM and network calls for tens of seconds, and a
frozen terminal is indistinguishable from a crashed one. Nothing here touches
Rich from the worker (``on_event`` only appends), which is the same thread
contract ``agent/streaming.py`` states for the planning chat.

Imports from :mod:`yeaboi.ui.mode_select` happen lazily inside function bodies —
the package imports this module's callers, so a top-level import is a cycle.
"""

from __future__ import annotations

import logging
import threading
import time

from rich.console import Console

from yeaboi.ui.session.chat._composer import (
    ChatComposer,
    Cleared,
    Restored,
    Submit,
    Truncated,
    Voice,
    clear_notice,
    paste_notice,
)
from yeaboi.ui.shared._scroll import SCROLL_KEYS, coalesce_scroll

logger = logging.getLogger(__name__)


def _turns_from(messages) -> list[dict]:
    """A stored conversation as the rows the screen builder draws."""
    return [
        {
            "role": message.role,
            "text": message.content,
            "tools": [{"name": call.name, "ok": call.ok} for call in message.tool_calls],
            "route": next(
                (
                    call.result.get("route", "")
                    for call in message.tool_calls
                    if call.name == "navigate" and call.ok and isinstance(call.result, dict)
                ),
                "",
            ),
        }
        for message in messages
    ]


class _Turn:
    """One in-flight question. The worker writes; the render loop reads."""

    def __init__(self) -> None:
        self.text: list[str] = []
        self.tools: list[dict] = []
        self.route = ""
        self.answer = None
        self.done = threading.Event()

    def on_event(self, event) -> None:
        from yeaboi.niko import engine

        if isinstance(event, engine.Token):
            self.text.append(event.text)
        elif isinstance(event, engine.ToolStarted):
            self.tools.append({"name": event.name, "ok": True})
        elif isinstance(event, engine.ToolFinished):
            for row in reversed(self.tools):
                if row["name"] == event.call.name:
                    row["ok"] = event.call.ok
                    break
        elif isinstance(event, engine.Assistant):
            # The finished text replaces the streamed pieces: a provider that
            # cannot stream sends the whole thing here and nothing before it.
            self.text = [event.text]
        elif isinstance(event, engine.Navigate):
            self.route = event.route


def run_niko_page(
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    *,
    conversation_id: str = "",
    read_only: bool = False,
) -> str:
    """Run one Niko conversation. Returns the conversation id (may be new).

    ``read_only`` opens a saved conversation for reading — the hub's snapshot
    view, where the composer is hidden and only scrolling and Back are live.
    """
    from yeaboi.niko import engine, suggestions
    from yeaboi.niko.store import NikoStore
    from yeaboi.ui.mode_select.screens._screens_niko import NIKO_ACTIONS, _build_niko_screen
    from yeaboi.ui.shared._click import button_click, parse_click

    composer = ChatComposer()
    with NikoStore() as store:
        turns = _turns_from(store.messages(conversation_id)) if conversation_id else []
    chips = suggestions.for_route("niko")
    scroll_offset = 10**6 if turns else 0  # open at the newest turn
    scroll_meta: dict = {}
    action_sel = 0
    message = "Saved conversation — read-only" if read_only else ""
    turn: _Turn | None = None
    started = 0.0
    actions = ["Back"] if read_only else NIKO_ACTIONS

    def render():
        width, height = console.size
        return _build_niko_screen(
            {
                "composer": composer,
                "turns": turns,
                "chips": chips,
                "busy": turn is not None,
                "streaming": "".join(turn.text) if turn else "",
                "streaming_tools": turn.tools if turn else [],
                "message": message,
                "read_only": read_only,
                "actions": actions,
            },
            scroll_offset=scroll_offset,
            action_sel=action_sel,
            width=width,
            height=height,
            shimmer_tick=time.monotonic(),
        )

    def ask(question: str) -> None:
        nonlocal turn, started, message
        turn = _Turn()
        started = time.monotonic()
        message = ""
        pending = turn

        def worker() -> None:
            try:
                pending.answer = engine.ask(
                    question,
                    conversation_id=conversation_id,
                    route="niko",
                    surface="terminal",
                    on_event=pending.on_event,
                )
            except Exception as exc:  # noqa: BLE001 — a failed turn is a notice, not a crash
                logger.warning("niko page: turn failed: %s", exc)
            finally:
                pending.done.set()

        turns.append({"role": "user", "text": question, "tools": []})
        threading.Thread(target=worker, name="niko-turn", daemon=True).start()

    def settle() -> None:
        """Fold a finished turn into the transcript."""
        nonlocal turn, conversation_id, message
        finished, turn = turn, None
        answer = finished.answer
        if answer is None:
            message = "That didn't work — see ~/.yeaboi/logs/niko/."
            turns.append({"role": "assistant", "text": "Something went wrong. Try again?", "tools": []})
            return
        conversation_id = answer.conversation_id
        turns.append(
            {
                "role": "assistant",
                "text": answer.text,
                "tools": [{"name": call.name, "ok": call.ok} for call in answer.tool_calls],
                "route": answer.route,
            }
        )
        message = answer.warnings[0] if answer.warnings else ""
        logger.info("niko page: turn settled in %.1fs (tools=%d)", time.monotonic() - started, len(answer.tool_calls))

    logger.info("niko page opened (conversation=%s read_only=%s)", conversation_id or "new", read_only)
    while True:
        live.update(render(), refresh=True)
        key = read_key(timeout=frame_time) if supports_timeout else read_key()

        if turn is not None and turn.done.is_set():
            settle()
            scroll_offset = 10**6
            continue
        if key is None:
            continue

        click = parse_click(key)
        if click is not None:
            hit = button_click(click, actions, console.size[1])
            if hit is None:
                continue
            action_sel = hit
            key = "enter"

        if key in ("escape", "esc"):
            break
        if key in SCROLL_KEYS and (composer.is_empty() or key not in ("up", "down")):
            scroll_offset = coalesce_scroll(scroll_offset, key, scroll_meta, read_key)
            continue
        if key in ("left", "right"):
            action_sel = (action_sel + (1 if key == "right" else -1)) % len(actions)
            continue
        if turn is not None:
            continue  # a turn is running: only scrolling and Esc are live
        if read_only:
            if key == "enter":
                break
            continue

        if key == "enter" and action_sel != 0 and composer.is_empty():
            chosen = actions[action_sel]
            if chosen == "Back":
                break
            if chosen == "New":
                conversation_id, turns[:] = "", []
                scroll_offset, message = 0, "New conversation."
                logger.info("niko page: new conversation")
            continue

        event = composer.handle_key(key)
        if isinstance(event, Submit):
            composer.reset()
            ask(event.text)
            scroll_offset = 10**6
        elif isinstance(event, Voice):
            spoken = _dictate(live, console, read_key)
            if spoken:
                composer.insert_text(spoken)
        elif isinstance(event, Truncated):
            message = paste_notice(event)
        elif isinstance(event, Cleared | Restored):
            message = clear_notice(event)

    logger.info("niko page closed (conversation=%s)", conversation_id or "none")
    return conversation_id


def _dictate(live, console, read_key) -> str:
    """Double-tap Space → dictation. A missing mic is a no-op, never a crash."""
    from yeaboi.ui.shared._voice_input import record_voice_input

    try:
        return record_voice_input(live, console, read_key) or ""
    except Exception as exc:  # noqa: BLE001 — a missing mic must not close the page
        logger.warning("niko page: dictation failed: %s", exc)
        return ""
