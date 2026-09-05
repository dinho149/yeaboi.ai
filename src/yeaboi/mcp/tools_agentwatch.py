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


def _usage(window_days: int, project: str, source: str, project_path: str = ""):
    if window_days < 1 or window_days > 365:
        raise ValueError("window_days must be between 1 and 365.")
    if source and source not in _VALID_SOURCES:
        raise ValueError(f"source must be one of {', '.join(_VALID_SOURCES)} (or empty for all).")
    from yeaboi.agentwatch.engine import run_agent_usage

    return run_agent_usage(window_days=window_days, project=project, source=source, project_path=project_path)


def _usage_history(limit: int):
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100.")
    from yeaboi.agentwatch.store import AgentWatchStore
    from yeaboi.paths import get_db_path

    with AgentWatchStore(get_db_path()) as store:
        return {"reports": store.list_reports("usage", limit=limit)}


def _advisor_run(window_days: int, project_path: str = ""):
    if window_days < 1 or window_days > 365:
        raise ValueError("window_days must be between 1 and 365.")
    from yeaboi.agentwatch.advisor import run_agent_advisor

    return run_agent_advisor(window_days=window_days, project_path=project_path)


def _advisor_history(limit: int):
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100.")
    from yeaboi.agentwatch.store import AgentWatchStore
    from yeaboi.paths import get_db_path

    with AgentWatchStore(get_db_path()) as store:
        return {"reports": store.list_reports("advisor", limit=limit)}


def _security_scan(deep: bool, include_info: bool = False):
    from yeaboi.agentwatch.engine import run_agent_security

    return run_agent_security(deep=deep, include_info=include_info)


def _security_dismiss(key: str, reason: str, expires: str = ""):
    from yeaboi.agentwatch import dismissals

    entry = dismissals.dismiss(key, reason=reason, expires=expires)
    return {"dismissed": entry.__dict__, "on_file": len(dismissals.load())}


def _security_undismiss(key: str):
    from yeaboi.agentwatch import dismissals

    if not dismissals.undismiss(key):
        raise ValueError(f"no dismissal on file for {key!r}.")
    return {"restored": key, "on_file": len(dismissals.load())}


def _security_replay(key: str, line: int = 0):
    from yeaboi.agentwatch import replay as replay_mod
    from yeaboi.agentwatch.engine import rebuild_security_report

    report = rebuild_security_report(include_info=True, record=False)
    finding = next((f for f in report.findings if f.key == key), None)
    if finding is None:
        raise ValueError(f"no finding {key!r} in the latest scan — run agents_security_scan first.")
    if finding.category not in ("secret", "risky_tool"):
        raise ValueError("only transcript findings have a replay.")
    try:
        return replay_mod.replay(finding.location, line or finding.line_no, pattern=finding.pattern)
    except replay_mod.ReplayError as exc:
        raise ValueError(str(exc)) from exc


def _security_signals(key: str):
    from yeaboi.agentwatch.engine import rebuild_security_report
    from yeaboi.agentwatch.store import AgentWatchStore
    from yeaboi.paths import get_db_path

    report = rebuild_security_report(include_info=True, record=False)
    finding = next((f for f in report.findings if f.key == key), None)
    if finding is None:
        raise ValueError(f"no finding {key!r} in the latest scan — run agents_security_scan first.")
    with AgentWatchStore(get_db_path()) as store:
        rows = store.list_findings_for_key(
            category=finding.category, pattern=finding.pattern, source_path=finding.location, context=finding.context
        )
    return {"key": key, "signals": rows}


def _security_fix(key: str, fix_id: str, reason: str = "", repo: str = ""):
    from yeaboi.agentwatch import security_fixes

    outcome = security_fixes.apply_fix(key, fix_id, reason=reason, repo=repo)
    if not outcome.ok:
        raise ValueError(outcome.detail)
    return outcome


def _security_verdict(keys: list[str], verdict: str, reason: str = ""):
    from yeaboi.agentwatch import dismissals

    if verdict == "undo":
        return {"restored": [k for k in keys if dismissals.undismiss(k)]}
    if verdict == "test-data":
        reason = reason or "test data: fixture or example text"
    elif verdict != "dismiss":
        raise ValueError("verdict must be one of test-data, dismiss, undo.")
    return {"handled": [dismissals.dismiss(k, reason=reason).key for k in keys]}


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
        project_path: str = "",
    ) -> dict:
        """BETA — Report what the user's AI coding agents cost: per-model, per-project and
        per-source token/cost breakdowns plus a daily trend, computed from local agent session
        logs (Claude Code) priced at public rates. project filters by project directory
        name (substring); source by telemetry source (claude_code); project_path keeps only
        sessions whose directory is that absolute path or under it (a repo and its worktrees).

        The Agents modes are in beta — costs are estimates from local session logs and public
        rate tables, not the provider's bill. Present totals as estimates."""
        return _with_beta(await run_engine(ctx, _usage, window_days, project, source, project_path))

    @app.tool()
    async def agents_usage_history(limit: int = 20) -> dict:
        """BETA — List previously generated agent usage reports (newest first), so spend can be
        compared across runs without recomputing.

        The Agents modes are in beta — costs are estimates from local session logs and public
        rate tables, not the provider's bill."""
        return _with_beta(await run_readonly(_usage_history, limit))

    @app.tool()
    async def agents_advisor_run(ctx: Context, window_days: int = 30, project_path: str = "") -> dict:
        """BETA — Audit the user's agent sessions for recoverable spend and prompt-cache
        health: how much of the window's cost came from mechanical Read waste (identical
        re-reads, subset re-reads, write read-backs, line-number scaffolding), plus
        context-residency stats, cache-death gaps, and volatile-shaped content in
        prompt-prefix files (CLAUDE.md). Computed locally from agent session logs;
        every dollar figure is an estimate (tokens ≈ bytes/4 at the window's blended
        input rate) and every count is a floor. project_path keeps only sessions whose
        directory is that absolute path or under it (a repo and its worktrees).

        The Agents modes are in beta — present recoverable figures as estimates of
        opportunity, never as promised savings."""
        return _with_beta(await run_engine(ctx, _advisor_run, window_days, project_path))

    @app.tool()
    async def agents_advisor_history(limit: int = 20) -> dict:
        """BETA — List previously generated agent advisor reports (newest first), so
        recoverable spend can be compared across runs without recomputing.

        The Agents modes are in beta — recoverable figures are estimates of
        opportunity, never promised savings."""
        return _with_beta(await run_readonly(_advisor_history, limit))

    @app.tool()
    async def agents_security_scan(ctx: Context, deep: bool = False, include_info: bool = False) -> dict:
        """BETA — Audit the local agent setup: permission-bypass settings, wildcard allow rules,
        risky hooks, MCP server inventory (plain-http, unpinned packages, inlined credentials),
        secret-shaped text and risky shell commands found in session transcripts. The report
        opens with ``verdict_line`` and lists ``issues`` (one per pattern) each with a verdict —
        needs-decision (the command ran / a live-looking key in a command or prompt), unsure,
        test-data (written into or read from a test/fixture/docs file — not a risk), handled,
        info — the ``why`` it matters and its ``fixes`` (apply with agents_security_fix).
        Findings carry pattern + file + line + the context the match sat in + a redacted snippet;
        the matched secret is never stored. deep=true re-scans every transcript; include_info=true
        lists informational findings that are otherwise only counted.

        The Agents modes are in beta — deterministic pattern matches are an indicator, not a
        security audit; a clean report means no known pattern matched."""
        return _with_beta(await run_engine(ctx, _security_scan, deep, include_info))

    @app.tool()
    async def agents_security_dismiss(key: str, reason: str, expires: str = "") -> dict:
        """BETA — Dismiss one security finding by its key (the ``key`` field on a finding,
        category:pattern:location) with a mandatory reason, optionally until an ISO date.
        Dismissed findings leave the report and the posture but stay counted as dismissed;
        pass an empty reason and the call is refused. Use undo by passing reason="undo" with
        the same key to restore it."""
        if reason.strip().lower() == "undo":
            return _with_beta(await run_readonly(_security_undismiss, key))
        return _with_beta(await run_readonly(_security_dismiss, key, reason, expires))

    @app.tool()
    async def agents_security_replay(key: str, line: int = 0) -> dict:
        """BETA — Replay the transcript turns around one security finding (its ``key``): who
        spoke, when, which tool ran what, with the flagged turn marked. Every string is redacted
        and the matched span is masked. ``line`` picks one of the finding's matching lines (see
        agents_security_signals); the default is the first. Use this to tell whether a signal
        was a command that ran or text written into a file."""
        return _with_beta(await run_readonly(_security_replay, key, line))

    @app.tool()
    async def agents_security_signals(key: str) -> dict:
        """BETA — Every matching line behind one grouped security finding (its ``key``): line
        number, timestamp, session, the context the match sat in and a redacted snippet."""
        return _with_beta(await run_readonly(_security_signals, key))

    @app.tool()
    async def agents_security_fix(ctx: Context, key: str, fix_id: str, reason: str = "", repo: str = "") -> dict:
        """BETA — Apply one of a finding's ``fixes`` by id: ``guard-hook`` writes a Claude Code
        PreToolUse guard into ~/.claude (asks for sandbox consent first), ``guard-hook-pr`` and
        ``mcp-edit-pr`` open a PR against ``repo`` (or the session's repository), ``settings-edit``
        removes one settings key or rule after a backup, ``mark-rotated`` / ``mark-test-data`` /
        ``dismiss`` (needs ``reason``) set the finding aside, ``undo`` brings it back. An applied
        fix reads as handled on the next report."""
        return _with_beta(await run_engine(ctx, _security_fix, key, fix_id, reason, repo))

    @app.tool()
    async def agents_security_verdict(keys: list[str], verdict: str, reason: str = "") -> dict:
        """BETA — Set many findings aside at once: ``verdict`` is ``test-data`` (reason filled
        in), ``dismiss`` (needs ``reason``) or ``undo``."""
        return _with_beta(await run_readonly(_security_verdict, keys, verdict, reason))

    @app.tool()
    async def agents_security_history(limit: int = 20) -> dict:
        """BETA — List previously generated agent security reports (newest first).

        The Agents modes are in beta — deterministic pattern matches are an indicator, not a
        security audit."""
        return _with_beta(await run_readonly(_security_history, limit))
