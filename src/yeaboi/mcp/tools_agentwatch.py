"""MCP tools: the Agents family (agentwatch) — usage/cost over local agent sessions."""

from __future__ import annotations

import logging

# Context must be importable from module globals — FastMCP evaluates the
# stringified type hints (PEP 563) of tool functions against this namespace.
from mcp.server.fastmcp import Context

from yeaboi.beta import AGENTWATCH_BETA_NOTICE
from yeaboi.mcp.runtime import run_engine, run_readonly

logger = logging.getLogger(__name__)

_VALID_SOURCES = ("claude_code", "openclaw")


def _usage(window_days: int, project: str, source: str):
    if window_days < 1 or window_days > 365:
        raise ValueError("window_days must be between 1 and 365.")
    if source and source not in _VALID_SOURCES:
        raise ValueError(f"source must be one of {', '.join(_VALID_SOURCES)} (or empty for all).")
    from yeaboi.agentwatch.engine import run_agent_usage

    return run_agent_usage(window_days=window_days, project=project, source=source)


def _usage_history(limit: int):
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100.")
    from yeaboi.agentwatch.store import AgentWatchStore
    from yeaboi.paths import get_db_path

    with AgentWatchStore(get_db_path()) as store:
        return {"reports": store.list_reports("usage", limit=limit)}


def _with_beta(payload: dict) -> dict:
    """Prepend the beta caveat to a success envelope's warnings.

    Same adapter-level placement as tools_performance._with_beta, for the same
    reason: cli._strict_exit maps engine warnings to exit 3, so the caveat must
    never live in the artifact itself (see src/yeaboi/beta.py).
    """
    if payload.get("ok"):
        payload["warnings"] = [AGENTWATCH_BETA_NOTICE, *payload.get("warnings", [])]
    return payload


def register(app) -> None:
    """Attach the agentwatch tools to the FastMCP app."""

    # NOTE: the "BETA — " prefixes below are hand-written literals, not f-strings.
    # FastMCP captures each tool's description from ``fn.__doc__`` at decoration
    # time, so an f-string docstring is a syntax error and reassigning __doc__
    # afterwards is a no-op.

    @app.tool()
    async def agents_usage(
        ctx: Context,
        window_days: int = 30,
        project: str = "",
        source: str = "",
    ) -> dict:
        """BETA — Report what the user's AI coding agents cost: per-model, per-project and
        per-source token/cost breakdowns plus a daily trend, computed from local agent session
        logs (Claude Code, OpenClaw) priced at public rates. project filters by project directory
        name (substring); source by telemetry source (claude_code, openclaw).

        The Agents modes are in beta — costs are estimates from local session logs and public
        rate tables, not the provider's bill. Present totals as estimates."""
        return _with_beta(await run_engine(ctx, _usage, window_days, project, source))

    @app.tool()
    async def agents_usage_history(limit: int = 20) -> dict:
        """BETA — List previously generated agent usage reports (newest first), so spend can be
        compared across runs without recomputing.

        The Agents modes are in beta — costs are estimates from local session logs and public
        rate tables, not the provider's bill."""
        return _with_beta(await run_readonly(_usage_history, limit))
