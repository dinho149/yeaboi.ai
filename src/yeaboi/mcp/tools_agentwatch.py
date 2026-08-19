"""MCP tools: the Agents family (agentwatch) — usage/cost over local agent sessions."""

from __future__ import annotations

import logging

# Context must be importable from module globals — FastMCP evaluates the
# stringified type hints (PEP 563) of tool functions against this namespace.
from mcp.server.fastmcp import Context

from yeaboi.beta import AGENTWATCH_BETA_NOTICE
from yeaboi.mcp.runtime import run_engine, run_readonly

logger = logging.getLogger(__name__)

_VALID_SOURCES = ("claude_code",)


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


def _advisor_run(window_days: int):
    if window_days < 1 or window_days > 365:
        raise ValueError("window_days must be between 1 and 365.")
    from yeaboi.agentwatch.advisor import run_agent_advisor

    return run_agent_advisor(window_days=window_days)


def _advisor_history(limit: int):
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100.")
    from yeaboi.agentwatch.store import AgentWatchStore
    from yeaboi.paths import get_db_path

    with AgentWatchStore(get_db_path()) as store:
        return {"reports": store.list_reports("advisor", limit=limit)}


def _standup_run(
    days: int,
    tracker_sources: list | None,
    github_owners: list | None,
    azdo_projects: list | None,
    include_local_sessions: bool,
    deliver: bool,
):
    if days < 0 or days > 90:
        raise ValueError("days must be between 0 (previous working day) and 90.")
    if tracker_sources and (bad := set(tracker_sources) - {"github", "azdo"}):
        raise ValueError(f"tracker_sources entries must be github/azdo, got: {', '.join(sorted(bad))}.")
    from yeaboi.agentwatch.engine import run_agent_standup

    return run_agent_standup(
        days=days or None,
        tracker_sources=tracker_sources,
        github_owners=github_owners,
        azdo_projects=azdo_projects,
        include_local_sessions=include_local_sessions,
        deliver=deliver,
    )


def _standup_history(limit: int):
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100.")
    from yeaboi.agentwatch.store import AgentWatchStore
    from yeaboi.paths import get_db_path

    with AgentWatchStore(get_db_path()) as store:
        return {"digests": store.list_reports("standup", limit=limit)}


def _security_scan(deep: bool):
    from yeaboi.agentwatch.engine import run_agent_security

    return run_agent_security(deep=deep)


def _security_history(limit: int):
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100.")
    from yeaboi.agentwatch.store import AgentWatchStore
    from yeaboi.paths import get_db_path

    with AgentWatchStore(get_db_path()) as store:
        return {"reports": store.list_reports("security", limit=limit)}


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
        logs (Claude Code) priced at public rates. project filters by project directory
        name (substring); source by telemetry source (claude_code).

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

    @app.tool()
    async def agents_advisor_run(ctx: Context, window_days: int = 30) -> dict:
        """BETA — Audit the user's agent sessions for recoverable spend and prompt-cache
        health: how much of the window's cost came from mechanical Read waste (identical
        re-reads, subset re-reads, write read-backs, line-number scaffolding), plus
        context-residency stats, cache-death gaps, and volatile-shaped content in
        prompt-prefix files (CLAUDE.md). Computed locally from agent session logs;
        every dollar figure is an estimate (tokens ≈ bytes/4 at the window's blended
        input rate) and every count is a floor.

        The Agents modes are in beta — present recoverable figures as estimates of
        opportunity, never as promised savings."""
        return _with_beta(await run_engine(ctx, _advisor_run, window_days))

    @app.tool()
    async def agents_advisor_history(limit: int = 20) -> dict:
        """BETA — List previously generated agent advisor reports (newest first), so
        recoverable spend can be compared across runs without recomputing.

        The Agents modes are in beta — recoverable figures are estimates of
        opportunity, never promised savings."""
        return _with_beta(await run_readonly(_advisor_history, limit))

    @app.tool()
    async def agents_standup_run(
        ctx: Context,
        days: int = 0,
        tracker_sources: list[str] | None = None,
        github_owners: list[str] | None = None,
        azdo_projects: list[str] | None = None,
        include_local_sessions: bool = True,
        deliver: bool = False,
    ) -> dict:
        """BETA — Run the daily agent standup: what the user's AI coding agents did — local
        sessions worked (with cost) plus agent-authored commits/PRs found in GitHub/Azure DevOps.
        days=0 covers everything since the previous working day (a Monday run reaches Friday);
        tracker_sources=[] skips trackers for a local-only digest, and
        include_local_sessions=false is the mirror — a tracker-only digest. Session logs are read
        from the machine this runs on, so set it false anywhere that is not the user's own
        machine, or the digest reports whatever sessions that host happens to have. deliver=true
        posts to the configured Slack webhook — ask the user before enabling.

        The Agents modes are in beta — detection is a lower bound; never present absence of
        evidence as agent idleness."""
        return _with_beta(
            await run_engine(
                ctx,
                _standup_run,
                days,
                tracker_sources,
                github_owners,
                azdo_projects,
                include_local_sessions,
                deliver,
            )
        )

    @app.tool()
    async def agents_standup_history(limit: int = 20) -> dict:
        """BETA — List previously generated agent standup digests (newest first).

        The Agents modes are in beta — detection is a lower bound; never present absence of
        evidence as agent idleness."""
        return _with_beta(await run_readonly(_standup_history, limit))

    @app.tool()
    async def agents_security_scan(ctx: Context, deep: bool = False) -> dict:
        """BETA — Audit the local agent setup: permission-bypass settings, wildcard allow rules,
        risky hooks, MCP server inventory (plain-http, unpinned packages, inlined credentials),
        secret-shaped text and risky shell commands found in session transcripts. Findings carry
        pattern + file + line only — matched content is never stored or returned. deep=true
        re-scans every transcript instead of only new/changed ones.

        The Agents modes are in beta — deterministic pattern matches are an indicator, not a
        security audit; a clean report means no known pattern matched."""
        return _with_beta(await run_engine(ctx, _security_scan, deep))

    @app.tool()
    async def agents_security_history(limit: int = 20) -> dict:
        """BETA — List previously generated agent security reports (newest first).

        The Agents modes are in beta — deterministic pattern matches are an indicator, not a
        security audit."""
        return _with_beta(await run_readonly(_security_history, limit))
