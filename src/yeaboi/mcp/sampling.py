"""MCP sampling bridge — a LangChain chat model backed by the client's LLM.

# See docs: "MCP Server" — sampling (host-model) mode

MCP "sampling" lets a server ask the *client* to run an LLM completion with
the client's own model and billing (`ctx.session.create_message`). This
module wraps that request in a LangChain ``BaseChatModel`` so yeaboi's
engines — which all call ``get_llm().invoke(...)`` — can run on the host
agent's model with zero code changes, injected via
``yeaboi.agent.llm.llm_override()``.

Threading contract: engines are synchronous, so ``run_engine()`` executes
them on an ``anyio.to_thread`` worker while the MCP session lives on the
server's event loop. ``_generate()`` therefore bridges back to the loop with
``anyio.from_thread.run(...)`` — which only works from a thread that anyio
itself spawned. ``run_engine()`` is the single place that guarantees this
by construction; never call ``SamplingChatModel.invoke()`` from the event
loop thread or a hand-rolled thread.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import anyio
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult

logger = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 8192


def _max_tokens_from_env() -> int:
    """Sampling max_tokens — env-tunable because hosts cap sampling responses."""
    raw = os.getenv("YEABOI_MCP_MAX_TOKENS", "").strip()
    try:
        return int(raw) if raw else DEFAULT_MAX_TOKENS
    except ValueError:
        logger.warning("Invalid YEABOI_MCP_MAX_TOKENS=%r — using %d", raw, DEFAULT_MAX_TOKENS)
        return DEFAULT_MAX_TOKENS


def _message_text(message: BaseMessage) -> str:
    """Flatten LangChain message content to plain text.

    Content is either a string or a list of typed blocks (multimodal). MCP
    sampling is text-only in our use, so image blocks are dropped with a
    warning — mirroring invoke_with_images()'s text-only retry philosophy.
    """
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, str):
            parts.append(block)
        else:
            logger.warning("Dropping non-text content block in sampling request: %s", type(block).__name__)
    return "\n".join(parts)


def convert_messages(messages: list[BaseMessage]) -> tuple[list, str | None]:
    """LangChain messages → (MCP SamplingMessages, system_prompt).

    SystemMessages become the sampling ``system_prompt`` (concatenated when
    there are several); AIMessages map to role "assistant", everything else
    to role "user".
    """
    from mcp import types

    system_parts: list[str] = []
    sampling: list = []
    for message in messages:
        text = _message_text(message)
        if isinstance(message, SystemMessage):
            system_parts.append(text)
            continue
        role = "assistant" if isinstance(message, AIMessage) else "user"
        sampling.append(types.SamplingMessage(role=role, content=types.TextContent(type="text", text=text)))
    return sampling, ("\n\n".join(system_parts) or None)


class SamplingChatModel(BaseChatModel):
    """LangChain chat model that fulfils invoke() via MCP sampling.

    ``track_usage()`` finds no token counts in the response metadata (MCP
    sampling results carry none) and logs its existing "no token data"
    warning — intentional: the host pays for these tokens, so yeaboi's
    usage ledger skips them.
    """

    session: Any = None  # mcp ServerSession — Any keeps the mcp import lazy
    max_tokens: int = 0  # 0 → resolve from env at first use
    truncated_calls: int = 0  # responses that hit the max_tokens cap (read by run_engine)

    @property
    def _llm_type(self) -> str:
        return "mcp-sampling"

    def _resolved_max_tokens(self) -> int:
        return self.max_tokens or _max_tokens_from_env()

    async def _acreate(self, messages: list[BaseMessage]) -> ChatResult:
        sampling_messages, system_prompt = convert_messages(messages)
        result = await self.session.create_message(
            sampling_messages,
            max_tokens=self._resolved_max_tokens(),
            system_prompt=system_prompt,
        )
        stop_reason = str(getattr(result, "stopReason", "") or "")
        if stop_reason.replace("_", "").lower() == "maxtokens":
            # A truncated response usually means broken JSON downstream — the
            # engine's parse-fallback then fires while llm_mode still says
            # "sampling". Count it so run_engine can surface a warning.
            self.truncated_calls += 1
            logger.warning(
                "Sampling response truncated at %d tokens (stopReason=%s)", self._resolved_max_tokens(), stop_reason
            )
        content = getattr(result, "content", None)
        text = getattr(content, "text", "") or ""
        ai_message = AIMessage(content=text, response_metadata={"model": getattr(result, "model", "")})
        return ChatResult(generations=[ChatGeneration(message=ai_message)])

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        # Sync path — called from run_engine()'s anyio worker thread; bridge
        # the async sampling request back to the server's event loop.
        return anyio.from_thread.run(self._acreate, messages)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return await self._acreate(messages)


def resolve_llm_mode(ctx) -> str:
    """Pick how LLM calls will be fulfilled: sampling → provider → fallback.

    1. ``sampling`` — the client advertised the sampling capability (unless
       forced off with ``YEABOI_MCP_LLM=provider``).
    2. ``provider`` — yeaboi's own configured LLM (~/.yeaboi/.env), exactly
       as the TUI uses it.
    3. ``fallback`` — neither available; engines still run and return their
       deterministic fallback artifacts, flagged in the envelope.
    """
    forced = os.getenv("YEABOI_MCP_LLM", "").strip().lower()
    if forced != "provider":
        try:
            from mcp import types

            if ctx.session.check_client_capability(types.ClientCapabilities(sampling=types.SamplingCapability())):
                return "sampling"
        except Exception:
            logger.debug("Sampling capability check failed — falling through", exc_info=True)

    from yeaboi.config import is_llm_configured

    configured, _message = is_llm_configured()
    return "provider" if configured else "fallback"
