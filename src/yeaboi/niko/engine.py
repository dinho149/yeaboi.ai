"""Niko engine — the global assistant's tool loop.

# See docs: "The ReAct Loop" — Thought → Action → Observation
# See docs: "Architecture" — the four layers; engines are headless
# See docs: "Agentic Blueprint Reference" — bind_tools, streaming

A standalone pipeline, not a LangGraph node: Niko has no state machine to
advance and no artifact to build, so a plain bind_tools loop is the whole of it.
``bind_tools`` is the LangChain method that hands a chat model the tools'
schemas — without it the model has no idea any exist and can only write prose.

The convention every other engine follows holds here too: an LLM failure is
never re-raised, it becomes a *warning* plus a deterministic answer, so every
surface always renders something useful. What is deterministic here is not a
report but a signpost — with no model available Niko can still say what yeaboi
does and where to go, because the card registry and the route manifest are both
local data.

:func:`ask` is the one entry point. ``on_event`` is the streaming seam: the
desktop's NDJSON route, the TUI driver and the MCP tool all pass a callback and
render the same typed events. It is called from the calling thread, in order.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yeaboi.agent.state import NikoAnswer, NikoToolCall

logger = logging.getLogger(__name__)

#: How many times the model may call tools before it must answer. Five is the
#: platform this was ported from; in practice Niko settles in one or two, and
#: the ceiling exists so a confused turn ends in an answer rather than a bill.
MAX_ROUNDS = 5

#: Turns of history replayed into the prompt. A Niko thread is a series of
#: short lookups, not one long document, so the window is generous but finite.
HISTORY_TURNS = 20

#: The answer when there is no model. Not an error: the question may well be
#: answerable from the registries, and "set an API key" is not a reply to
#: "what can yeaboi do?".
NO_LLM_TEXT = (
    "I can't think without a model — set an API key in `~/.yeaboi/.env` "
    "(or run `yeaboi --setup`) and ask me again. In the meantime, here's what yeaboi does:"
)


# ---------------------------------------------------------------------------
# The events every surface renders
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Token:
    """One slice of the answer, as it is generated."""

    text: str


@dataclass(frozen=True)
class Assistant:
    """The finished answer. Always sent, even when tokens streamed."""

    text: str


@dataclass(frozen=True)
class ToolStarted:
    name: str
    arguments: dict


@dataclass(frozen=True)
class ToolFinished:
    call: NikoToolCall


@dataclass(frozen=True)
class Navigate:
    """Niko's suggestion of where to go. The surface decides whether to obey."""

    route: str


@dataclass(frozen=True)
class Done:
    conversation_id: str


def _emit(on_event: Callable | None, event) -> None:
    """Hand one event to the caller. None-safe, and never fatal."""
    if on_event is None:
        return
    try:
        on_event(event)
    except Exception:  # noqa: BLE001 — a broken renderer must not kill the turn
        logger.warning("niko: on_event callback raised", exc_info=True)


# ---------------------------------------------------------------------------
# The turn
# ---------------------------------------------------------------------------


def ask(
    question: str,
    *,
    conversation_id: str = "",
    route: str = "",
    user_name: str = "",
    surface: str = "desktop",
    max_rounds: int = MAX_ROUNDS,
    db_path: Path | None = None,
    on_event: Callable | None = None,
    cancel=None,
) -> NikoAnswer:
    """Answer one question, using Niko's read-only tools.

    Args:
        question: What the user asked.
        conversation_id: Continue this thread; blank opens a new one.
        route: Where the user is — a desktop route, or a TUI mode key.
        user_name: What to call them.
        surface: "desktop" | "terminal" — changes how `navigate` is described.
        max_rounds: Tool rounds before the model must answer.
        db_path: Injection seam for the conversation store.
        on_event: Called with each typed event as it happens.
        cancel: A ``threading.Event``; set it to stop the turn between rounds.
    """
    from yeaboi.niko.store import NikoStore

    asked = (question or "").strip()
    if not asked:
        raise ValueError("Ask Niko something — the question was empty.")

    with NikoStore(db_path) as store:
        conversation = store.get(conversation_id) if conversation_id else None
        if conversation is None:
            conversation = store.create()
        history = store.messages(conversation.id)[-HISTORY_TURNS:]
        store.add_message(conversation.id, role="user", content=asked, route=route)

        answer = _run_turn(
            asked,
            conversation_id=conversation.id,
            history=history,
            route=route,
            user_name=user_name,
            surface=surface,
            max_rounds=max_rounds,
            on_event=on_event,
            cancel=cancel,
        )

        store.add_message(
            conversation.id,
            role="assistant",
            content=answer.text,
            tool_calls=answer.tool_calls,
            route=route,
        )
        if not conversation.title:
            store.set_title(conversation.id, _title_for(asked))

    _emit(on_event, Done(conversation.id))
    return answer


def _run_turn(
    question: str,
    *,
    conversation_id: str,
    history: list,
    route: str,
    user_name: str,
    surface: str,
    max_rounds: int,
    on_event: Callable | None,
    cancel,
) -> NikoAnswer:
    """The loop. Never raises: a failure is a warning plus a usable answer."""
    from yeaboi.config import is_llm_configured

    configured, why = is_llm_configured()
    if not configured:
        logger.warning("niko: LLM not configured (%s)", why)
        return _fallback_answer(conversation_id, route, warning=f"AI answers unavailable — {why}.", on_event=on_event)

    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    from yeaboi.niko import tools as niko_tools
    from yeaboi.prompts.niko import get_niko_system_prompt

    messages = [
        SystemMessage(
            content=get_niko_system_prompt(
                route=route,
                capability=_capability_for(route),
                screen_title=_title_of(route),
                user_name=user_name,
                surface=surface,
                facts=_facts(),
            )
        ),
        *_replay(history),
        HumanMessage(content=question),
    ]

    calls: list[NikoToolCall] = []
    warnings: list[str] = []
    suggested_route = ""
    # Every round's prose, not just the last: a turn that says "let me check",
    # calls a tool, then answers has written two paragraphs, and keeping only
    # the second stores an answer the user never saw.
    said: list[str] = []

    for round_index in range(max(1, max_rounds)):
        if cancel is not None and cancel.is_set():
            logger.info("niko: turn cancelled before round %d", round_index)
            break
        reply, streamed, failure = _generate(messages, on_event=on_event)
        if failure:
            warnings.append(failure)
            break
        spoken = _text_of(reply)
        if spoken:
            if not streamed:
                _emit(on_event, Token(spoken))
            said.append(spoken)
        tool_calls = list(getattr(reply, "tool_calls", None) or [])
        if not tool_calls:
            break

        messages.append(reply)
        for requested in tool_calls:
            name = str(requested.get("name", ""))
            arguments = requested.get("args") or {}
            _emit(on_event, ToolStarted(name, arguments))
            payload = niko_tools.call(name, arguments)
            record = _record(name, arguments, payload)
            calls.append(record)
            _emit(on_event, ToolFinished(record))
            if name == niko_tools.NAVIGATE_TOOL and record.ok:
                suggested_route = str(payload.get("route", "")) or suggested_route
            messages.append(
                ToolMessage(
                    content=json.dumps(payload, default=str)[:20000],
                    tool_call_id=str(requested.get("id", "")),
                )
            )
    else:
        # The loop ran its full budget and the model still wanted tools.
        warnings.append("Niko stopped after the tool-round limit — the answer may be partial.")

    text = "\n\n".join(said).strip()
    if not text:
        text = "I looked, but I don't have an answer for that. Try asking a different way."

    _emit(on_event, Assistant(text))
    if suggested_route:
        _emit(on_event, Navigate(suggested_route))
    logger.info(
        "niko: turn done conversation=%s tools=%d route=%s chars=%d",
        conversation_id,
        len(calls),
        suggested_route or "-",
        len(text),
    )
    return NikoAnswer(
        conversation_id=conversation_id,
        text=text,
        tool_calls=tuple(calls),
        route=suggested_route,
        warnings=tuple(warnings),
    )


def _generate(messages: list, *, on_event: Callable | None) -> tuple[object, bool, str]:
    """One model call. Returns (reply, streamed, failure_message).

    Streams when the provider supports it, so the panel fills as the answer is
    written; falls back to a single invoke when streaming raises, because a
    provider that cannot stream is not a reason to have no answer.
    """
    from yeaboi.agent.llm import get_llm, track_usage
    from yeaboi.agent.nodes import _is_llm_auth_or_billing_error, _local_llm_hint
    from yeaboi.niko.tools import NIKO_TOOLS

    try:
        model = get_llm(temperature=0.2).bind_tools(NIKO_TOOLS)
    except Exception as exc:  # noqa: BLE001 — a missing provider package is a warning
        logger.warning("niko: could not build the model: %s", exc)
        return None, False, "AI answers unavailable — the LLM provider could not be created."

    try:
        reply, streamed = _stream(model, messages, on_event=on_event)
        track_usage(reply)
        return reply, streamed, ""
    except Exception as exc:  # noqa: BLE001 — every LLM failure becomes a warning
        if _is_llm_auth_or_billing_error(exc):
            logger.warning("niko: LLM auth/billing error: %s", exc)
            return None, False, "AI answers unavailable — API key invalid or billing issue."
        local_hint = _local_llm_hint(exc)
        if local_hint:
            logger.warning("niko: local Ollama failure: %s", exc)
            return None, False, f"AI answers unavailable — {local_hint}"
        logger.warning("niko: LLM request failed: %s", exc)
        return None, False, "AI answers unavailable — the LLM request failed (see logs)."


def _stream(model, messages: list, *, on_event: Callable | None):
    """Stream one reply, accumulating chunks into a single message.

    AIMessageChunk defines ``+``, which is what merges partial tool-call
    arguments back into whole ones — accumulating by hand would have to
    reimplement that.
    """
    if on_event is None:
        return model.invoke(messages), False

    merged = None
    emitted = False
    try:
        for chunk in model.stream(messages):
            merged = chunk if merged is None else merged + chunk
            piece = _text_of(chunk)
            if piece:
                _emit(on_event, Token(piece))
                emitted = True
    except NotImplementedError:
        logger.info("niko: provider does not stream — falling back to invoke")
        return model.invoke(messages), False
    if merged is None:
        return model.invoke(messages), False
    return merged, emitted


def _text_of(message) -> str:
    """The plain text of a reply — content is a string or a list of blocks."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _record(name: str, arguments: dict, payload: dict) -> NikoToolCall:
    error = str(payload.get("error", "")) if isinstance(payload, dict) else ""
    return NikoToolCall(
        name=name,
        arguments=dict(arguments or {}),
        ok=not error,
        result=None if error else payload,
        error=error,
    )


def _replay(history: list) -> list:
    """Stored turns as chat messages.

    Tool calls come back as a compact note on the assistant turn rather than as
    tool_use/tool_result pairs: the pairs would have to survive a provider swap
    and a model change to stay valid, and what the next turn actually needs is
    "you already looked this up", which one line says.
    """
    from langchain_core.messages import AIMessage, HumanMessage

    messages = []
    for stored in history:
        if stored.role == "user":
            messages.append(HumanMessage(content=stored.content))
            continue
        text = stored.content
        if stored.tool_calls:
            looked = ", ".join(sorted({call.name for call in stored.tool_calls}))
            text = f"{text}\n\n[Earlier you read: {looked}]".strip()
        messages.append(AIMessage(content=text or "(no answer)"))
    return messages


def _capability_for(route: str) -> str:
    from yeaboi.niko.suggestions import screen_for

    return screen_for(route).get("capability", "") if route else ""


def _title_of(route: str) -> str:
    from yeaboi.niko.suggestions import screen_for

    return screen_for(route).get("title", "") if route else ""


def _facts() -> tuple[str, ...]:
    """Cheap, deterministic one-liners about the user's own data.

    Deliberately thin: everything else is a tool call, and a fact in the prompt
    is a number the model may repeat later without having read it.
    """
    try:
        from yeaboi.paths import get_db_path
        from yeaboi.sessions import SessionStore

        with SessionStore(get_db_path()) as store:
            count = len(store.list_sessions())
    except Exception:  # noqa: BLE001 — context is a nicety, never a failure
        logger.debug("niko: could not count sessions for the prompt", exc_info=True)
        return ()
    if not count:
        return ("They have no saved planning sessions yet.",)
    return (f"They have {count} saved planning session(s).",)


def _fallback_answer(conversation_id: str, route: str, *, warning: str, on_event: Callable | None) -> NikoAnswer:
    """What Niko says with no model: the signpost, from local registries only."""
    from yeaboi.niko.suggestions import for_route

    lines = [NO_LLM_TEXT, ""]
    lines += [f"- {chip['label']} — {chip['prompt']}" for chip in for_route(route)]
    text = "\n".join(lines)
    _emit(on_event, Assistant(text))
    return NikoAnswer(conversation_id=conversation_id, text=text, warnings=(warning,))


def _title_for(question: str) -> str:
    """Name the thread from its opening question.

    One fast-model call, and a plain truncation when it fails — a conversation
    with no title is a row the saved-conversations hub cannot label, and that is
    not worth failing a turn over.
    """
    from yeaboi.niko.store import MAX_TITLE_CHARS

    fallback = question[:60].rstrip() + ("…" if len(question) > 60 else "")
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from yeaboi.agent.llm import get_analysis_fast_model, get_llm
        from yeaboi.prompts.niko import get_niko_title_prompt

        model = get_llm(model=get_analysis_fast_model(), temperature=0.0)
        reply = model.invoke([SystemMessage(content=get_niko_title_prompt()), HumanMessage(content=question)])
        title = _text_of(reply).strip().strip("\"'").strip()
        return (title or fallback)[:MAX_TITLE_CHARS]
    except Exception:  # noqa: BLE001 — a missing title is never worth a failed turn
        logger.debug("niko: could not auto-title the conversation", exc_info=True)
        return fallback[:MAX_TITLE_CHARS]
