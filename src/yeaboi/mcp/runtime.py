"""Shared runtime for MCP tool handlers — result envelope + engine dispatch.

# See docs: "MCP Server" — result envelope and LLM modes

Every yeaboi MCP tool returns the same JSON envelope so host agents can
handle results uniformly:

    {"ok": true,  "llm_mode": "sampling|provider|fallback|n/a",
     "warnings": [...], "data": {...}}
    {"ok": false, "error": {"type": ..., "message": ...}, "hint": "..."}

``llm_mode`` tells the host how the content was produced: ``sampling`` =
the host's own model (MCP sampling), ``provider`` = the user's configured
LLM (~/.yeaboi/.env), ``fallback`` = no LLM was available so the engine's
deterministic fallback artifact was returned, ``n/a`` = no LLM involved.

``run_engine()`` is the single dispatch point for every engine call. It runs
the (synchronous) engine on a worker thread so the server's event loop stays
responsive, injects the sampling-backed model via ``llm_override()`` when the
client supports sampling, and converts any exception into a structured error
payload — a tool call must never crash the server.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import threading
from functools import partial

import anyio

logger = logging.getLogger(__name__)

LLM_HINT = (
    "No LLM available: set an API key in ~/.yeaboi/.env (run `yeaboi --setup`) "
    "or use an MCP client that supports sampling."
)

_AUTH_MARKERS = ("api key", "api_key", "credential", "authentication", "unauthorized", "401", "403", "billing")

# Engines were written for one-at-a-time TUI use (module-level caches,
# process-wide usage stats) — serialize them. A plain threading.Lock acquired
# *inside* the worker thread keeps this loop-agnostic (an anyio.Lock would be
# bound to whichever event loop first used it).
_ENGINE_LOCK = threading.Lock()


def to_jsonable(value):
    """Convert engine results (frozen dataclasses, tuples, enums, dates) to plain JSON types."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        value = dataclasses.asdict(value)
    # One round-trip through json flattens tuples/sets/enums; default=str
    # covers dates and anything exotic.
    return json.loads(json.dumps(value, default=str))


def envelope(data, *, llm_mode: str = "n/a", warnings=()) -> dict:
    """Build the success envelope every tool returns."""
    return {"ok": True, "llm_mode": llm_mode, "warnings": list(warnings), "data": data}


def error_envelope(exc: BaseException, *, llm_mode: str = "n/a") -> dict:
    """Build the failure envelope — structured error, never a raw traceback."""
    message = str(exc) or type(exc).__name__
    result: dict = {
        "ok": False,
        "llm_mode": llm_mode,
        "error": {"type": type(exc).__name__, "message": message},
    }
    if any(marker in message.lower() for marker in _AUTH_MARKERS):
        result["hint"] = LLM_HINT
    return result


def _result_warnings(result) -> list[str]:
    """Pull the engine artifact's own warnings field (StandupReport etc.) into the envelope."""
    warnings = getattr(result, "warnings", None)
    if warnings is None and isinstance(result, dict):
        warnings = result.get("warnings")
    return [str(w) for w in warnings] if warnings else []


async def run_engine(ctx, fn, /, *args, needs_llm: bool = True, **kwargs) -> dict:
    """Run a yeaboi engine function and return its envelope.

    Resolves the LLM mode (sampling → provider → fallback), runs ``fn`` on a
    worker thread under the engine lock — with the sampling model injected via
    ``llm_override()`` when the client supports it — and wraps result or
    exception in the envelope.
    """
    from yeaboi.mcp.sampling import SamplingChatModel, resolve_llm_mode

    mode = resolve_llm_mode(ctx) if needs_llm else "n/a"
    sampling_model = SamplingChatModel(session=ctx.session) if mode == "sampling" else None

    def _call():
        with _ENGINE_LOCK:
            if sampling_model is not None:
                from yeaboi.agent.llm import llm_override

                # The override is set here, in the worker thread, so the
                # SamplingChatModel's anyio.from_thread bridge back to the
                # event loop is always available when the engine invokes it.
                with llm_override(sampling_model):
                    return fn(*args, **kwargs)
            return fn(*args, **kwargs)

    logger.info("run_engine: fn=%s mode=%s", getattr(fn, "__name__", fn), mode)
    try:
        result = await anyio.to_thread.run_sync(_call)
    except Exception as exc:
        _log_tool_failure("run_engine", fn, exc)
        return error_envelope(exc, llm_mode=mode)

    warnings = _result_warnings(result)
    if mode == "fallback":
        warnings.append(LLM_HINT + " Content below is a deterministic fallback, not AI-generated.")
    if sampling_model is not None and sampling_model.truncated_calls:
        warnings.append(
            f"{sampling_model.truncated_calls} sampling response(s) hit the token cap and were truncated — "
            "parts of the content may be deterministic fallback. Raise YEABOI_MCP_MAX_TOKENS (default 8192)."
        )
    return envelope(to_jsonable(result), llm_mode=mode, warnings=warnings)


async def run_readonly(fn, /, *args, **kwargs) -> dict:
    """Run a deterministic (no-LLM) function on a worker thread and envelope it."""
    logger.info("run_readonly: fn=%s", getattr(fn, "__name__", fn))
    try:
        result = await anyio.to_thread.run_sync(partial(fn, *args, **kwargs))
    except Exception as exc:
        _log_tool_failure("run_readonly", fn, exc)
        return error_envelope(exc)
    return envelope(to_jsonable(result))


def _log_tool_failure(where: str, fn, exc: BaseException) -> None:
    """ValueError = expected user-facing condition (bad arg, no sessions yet) —
    warn without a traceback. Anything else is a real failure."""
    name = getattr(fn, "__name__", fn)
    if isinstance(exc, ValueError):
        logger.warning("%s rejected: fn=%s error=%s", where, name, exc)
    else:
        logger.error("%s failed: fn=%s error=%s", where, name, exc, exc_info=True)
