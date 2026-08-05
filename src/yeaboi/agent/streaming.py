"""Streaming transport for the planning live chat — one turn, token by token.

# See docs: "Architecture" — The ReAct Loop (the agent node is the only
# node whose reply is free prose; every other node emits template-built text
# or structured JSON)
# See docs: "Agentic Blueprint Reference" — streaming

The chat TUI calls stream_chat_turn() on a worker thread and paints tokens as
they arrive. Two internal paths give every turn the same feel through one
callback:

- **Real streaming** (the ``agent`` ReAct node only): ``graph.stream()`` with
  ``stream_mode="messages"`` attaches a LangGraph callback handler that makes
  the ``BaseChatModel.invoke()`` call *inside* the node transparently upgrade
  to provider streaming and surface each ``AIMessageChunk`` here — the node
  code is unchanged. This is the design the "streaming is handled at the REPL
  layer by iterating graph.stream()" comment in nodes.py always anticipated.
  Config propagates to the node via contextvars (Python 3.11+), so no plumbing
  is needed.
- **Typewriter** (everything else — intake questions, review summaries): the
  text is template-built by the node, not LLM prose, so there is nothing to
  stream. We run a plain ``graph.invoke()`` and then replay the finished text
  through the same on_token callback at a fixed characters-per-second pace.

Pipeline JSON nodes (analyzer/features/stories/tasks/sprints) never take the
real-streaming path: forcing provider streaming onto JSON-mode calls is
provider-fragile (e.g. Ollama ``format="json"``), and their output is parsed,
not displayed. They run under the typewriter path, where graph.invoke() is
byte-identical to today's behaviour — planning results are unaffected.

Thread contract: stream_chat_turn() runs on a worker thread. ``on_token`` is
called from that thread and must be cheap and thread-safe — appending to a
plain list is the intended use (list.append is atomic under the GIL; the
render loop joins the buffer each frame). It must NEVER touch Rich/Live
objects; the main thread owns rendering.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from langchain_core.messages import AIMessage, AIMessageChunk

from yeaboi.agent.headless import _predict_next_node

logger = logging.getLogger(__name__)

# Re-exported under a public name: the chat driver and this module must agree
# with route_entry()'s decision, and headless.py already maintains the one
# hand-synced copy — a third copy would drift.
predict_next_node = _predict_next_node

_TYPEWRITER_CHUNK = 8
"""Characters per on_token call on the typewriter path — small enough to look
smooth at 30fps, large enough to keep callback overhead negligible."""


class ChatStreamCancelledError(Exception):
    """A real-streaming turn was aborted mid-generation.

    Raised only from the real-streaming path: the node never completed, no
    state was merged, so the caller keeps its previous graph_state untouched.
    (The typewriter path never raises this — by the time text is replaying,
    the graph has already run and the answer is recorded; cancel there just
    fast-forwards the remaining text.)
    """


def _text_of(content) -> str:
    """Extract display text from a chunk's content.

    Anthropic/Bedrock stream content as a LIST of blocks (text / tool-use /
    thinking); OpenAI-style providers stream a plain string. Only text should
    reach the chat — tool-call fragments and thinking blocks are not prose.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return ""


def _last_ai_text(state: dict) -> str:
    """The content of the last AIMessage in a finished graph state ("" if none)."""
    for message in reversed(state.get("messages", [])):
        if isinstance(message, AIMessage):
            return _text_of(message.content)
    return ""


def stream_chat_turn(
    graph,
    invoke_state: dict,
    on_token: Callable[[str], None],
    *,
    cancel: threading.Event | None = None,
    typewriter_cps: int = 400,
) -> dict:
    """Run one chat turn, emitting display text through on_token as it forms.

    Args:
        graph: The compiled planning graph (create_graph()).
        invoke_state: Full state for this turn — ``{**graph_state, "messages":
            [..., HumanMessage(user_text)]}`` exactly as graph.invoke() takes.
        on_token: Called with each text piece, from THIS (worker) thread.
            Must be cheap, thread-safe, and never touch Rich/Live.
        cancel: Optional event. During real streaming, setting it aborts the
            provider call and raises ChatStreamCancelledError (state unchanged).
            During typewriter replay it fast-forwards: the remaining text is
            emitted in one piece and the completed state is returned — the
            answer is already recorded, discarding it would lose the turn.
        typewriter_cps: Pace for replaying deterministic text. 0 disables
            pacing (tests).

    Returns:
        The final graph state for this turn.

    Raises:
        ChatStreamCancelledError: cancel was set mid-stream on the real path.
        Exception: provider/graph errors propagate; the caller classifies
            them (the TUI keeps any partial text and appends the error).
    """
    node = predict_next_node(invoke_state)
    if node == "agent":
        return _stream_agent_turn(graph, invoke_state, on_token, cancel)
    return _typewriter_turn(graph, invoke_state, on_token, cancel, typewriter_cps)


def _stream_agent_turn(
    graph,
    invoke_state: dict,
    on_token: Callable[[str], None],
    cancel: threading.Event | None,
) -> dict:
    """Real token streaming for the agent (ReAct) node.

    # See docs: "The ReAct Loop" — Thought → Action → Observation
    #
    # stream_mode=["messages", "values"] multiplexes two feeds:
    # - "messages": (AIMessageChunk, metadata) pairs as the LLM generates —
    #   metadata["langgraph_node"] names the node the chunk came from, which
    #   is the filter that keeps tool results and any non-agent LLM call out
    #   of the visible chat.
    # - "values": the full state after each super-step — the LAST one is the
    #   same dict graph.invoke() would have returned.
    #
    # Tool-loop turns need no special casing: chunks that only carry
    # tool_call_chunks have empty text and fall out of the filter, and the
    # post-tool answer streams on the next agent super-step within this same
    # generator.
    """
    final_state: dict | None = None
    emitted = False
    stream = graph.stream(invoke_state, stream_mode=["messages", "values"])
    try:
        for mode, payload in stream:
            if cancel is not None and cancel.is_set():
                logger.info("Chat stream cancelled (emitted=%s)", emitted)
                raise ChatStreamCancelledError()
            if mode == "messages":
                chunk, metadata = payload
                if metadata.get("langgraph_node") != "agent" or not isinstance(chunk, AIMessageChunk):
                    continue
                text = _text_of(chunk.content)
                if text:
                    on_token(text)
                    emitted = True
            elif mode == "values":
                final_state = payload
    finally:
        # Close the generator either way — on cancel/error this closes the
        # provider's HTTP stream instead of leaving it half-consumed.
        stream.close()

    if final_state is None:
        raise RuntimeError("graph.stream() ended without a final state value")
    return final_state


def _typewriter_turn(
    graph,
    invoke_state: dict,
    on_token: Callable[[str], None],
    cancel: threading.Event | None,
    typewriter_cps: int,
) -> dict:
    """Deterministic-text path: invoke normally, then replay the reply paced."""
    result = graph.invoke(invoke_state)
    text = _last_ai_text(result)

    for start in range(0, len(text), _TYPEWRITER_CHUNK):
        piece = text[start : start + _TYPEWRITER_CHUNK]
        if cancel is not None and cancel.is_set():
            # Fast-forward: the graph already ran; the rest arrives at once.
            on_token(text[start:])
            break
        on_token(piece)
        if typewriter_cps > 0:
            time.sleep(len(piece) / typewriter_cps)
    return result
