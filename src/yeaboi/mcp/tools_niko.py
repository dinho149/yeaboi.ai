"""MCP tool: ask Niko, yeaboi's global assistant.

One tool, and the reason it is safe to have at all is in ``niko/tools.py``:
Niko's own tools call the engines directly rather than going back through this
server. Routing them through the dispatcher would re-enter
``runtime._ENGINE_LOCK`` from inside a call that already holds it, and this tool
would deadlock for the hour ``CALL_TIMEOUT_SECONDS`` allows.

An MCP host asking Niko is asking for a *summary across* yeaboi rather than one
mode's artifact — "what should I look at?", "what did my agents cost and is
anything waiting on me?". Every individual read is already its own tool here;
Niko is the one that reads several and writes the sentence.
"""

from __future__ import annotations

import logging

# Context must be importable from module globals — FastMCP evaluates the
# stringified type hints (PEP 563) of tool functions against this namespace.
from mcp.server.fastmcp import Context

from yeaboi.mcp.runtime import run_engine

logger = logging.getLogger(__name__)

#: Tighter than the engine's own ceiling. A host agent pays for every round and
#: is usually asking one question, not holding a conversation.
MAX_ROUNDS = 4


def _ask(question: str, conversation_id: str, route: str, max_rounds: int):
    if not (question or "").strip():
        raise ValueError("question is required — ask Niko something.")
    if max_rounds < 1 or max_rounds > MAX_ROUNDS:
        raise ValueError(f"max_rounds must be between 1 and {MAX_ROUNDS}.")
    from yeaboi.niko.engine import ask

    return ask(
        question,
        conversation_id=conversation_id,
        route=route,
        surface="terminal",
        max_rounds=max_rounds,
    )


def register(app) -> None:
    """Register the Niko tool on the FastMCP app."""

    @app.tool()
    async def niko_ask(
        ctx: Context,
        question: str,
        conversation_id: str = "",
        route: str = "",
        max_rounds: int = MAX_ROUNDS,
    ) -> dict:
        """Ask Niko, yeaboi's assistant, a question that spans modes — "what should I
        look at?", "what did my agents cost and is anything waiting on me?", "where
        does X live?". Niko reads across planning sessions, standups, retros, poker,
        performance, reporting, ship, the agentwatch family, ceremonies, provenance
        and LLM usage, and answers in prose.

        Read-only: Niko can look things up and name the screen that does a thing, but
        cannot start a run, change a setting or delete anything. Use the specific
        tool when you want one mode's artifact; use this when the answer spans
        several or you don't know which one holds it.

        `route` is where the user is (a desktop route like `/agents/usage`), which
        colours the answer. Pass `conversation_id` from a previous result to continue
        the thread; omit it to start a new one."""
        return await run_engine(ctx, _ask, question, conversation_id, route, max_rounds)
