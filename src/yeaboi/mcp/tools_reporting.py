"""MCP tools: Reporting mode (business-friendly delivery reports)."""

from __future__ import annotations

import logging

# Context must be importable from module globals — FastMCP evaluates the
# stringified type hints (PEP 563) of tool functions against this namespace.
from mcp.server.fastmcp import Context

from yeaboi.mcp.runtime import run_engine, run_readonly, to_jsonable

logger = logging.getLogger(__name__)

_PERIODS = ("last_sprint", "last_month", "quarter")


def _reporting_history(session_id: str, limit: int) -> dict:
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.paths import get_db_path
    from yeaboi.reporting.store import ReportingStore

    resolved = resolve_session_id(session_id)
    with ReportingStore(get_db_path()) as store:
        history = store.get_history(resolved, limit=limit)
        latest = store.get_latest_report(resolved)
    # to_jsonable only unpacks a top-level dataclass; convert the nested report
    # here so latest_report is a structured dict rather than its str() repr.
    return {
        "session_id": resolved,
        "history": history,
        "latest_report": to_jsonable(latest) if latest is not None else None,
    }


def _reporting_export(session_id: str) -> dict:
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.paths import get_db_path
    from yeaboi.reporting.export import export_report
    from yeaboi.reporting.store import ReportingStore

    resolved = resolve_session_id(session_id)
    with ReportingStore(get_db_path()) as store:
        report = store.get_latest_report(resolved)
        run_history = store.get_history(resolved, limit=30)
    if report is None:
        raise ValueError(
            f"No delivery report recorded for session {resolved!r} — generate one from the yeaboi TUI first."
        )
    paths = export_report(report, history=run_history)
    logger.info("Delivery report exported via MCP: session=%s period=%s", resolved, report.period_label)
    return {
        "session_id": resolved,
        "period": report.period_label,
        "markdown": str(paths["markdown"]),
        "html": str(paths["html"]),
    }


def _report_delivery(
    period: str,
    session_id: str,
    jira_project: str,
    azdo_project: str,
    window_start: str,
    window_end: str,
    sprint_names: list | None,
    period_label_override: str,
):
    if period not in _PERIODS:
        raise ValueError(f"period must be one of {', '.join(_PERIODS)} — got {period!r}")
    from yeaboi.reporting.engine import run_delivery_report

    return run_delivery_report(
        period,
        session_id=session_id,
        jira_project=jira_project,
        azdo_project=azdo_project,
        window_start=window_start,
        window_end=window_end,
        sprint_names=tuple(sprint_names or ()),
        period_label_override=period_label_override,
    )


def register(app) -> None:
    """Attach the reporting tools to the FastMCP app."""

    @app.tool()
    async def report_delivery(
        ctx: Context,
        period: str = "last_month",
        session_id: str = "",
        jira_project: str = "",
        azdo_project: str = "",
        window_start: str = "",
        window_end: str = "",
        sprint_names: list[str] | None = None,
        period_label_override: str = "",
    ) -> dict:
        """Generate a stakeholder-friendly delivery report of completed work from the team's
        tracker (Jira/Azure DevOps): executive summary, outcome themes, metrics, highlights.
        period: 'last_sprint', 'last_month', or 'quarter'. For 'quarter', optionally frame the
        window explicitly: window_start/window_end (YYYY-MM-DD) bound the reporting span,
        sprint_names lists the sprints it covers, and period_label_override renames the period
        (e.g. 'Q3 2026'). Blank session_id = most recent session (sprint length/project name)."""
        return await run_engine(
            ctx,
            _report_delivery,
            period,
            session_id,
            jira_project,
            azdo_project,
            window_start,
            window_end,
            sprint_names,
            period_label_override,
        )

    @app.tool()
    async def reporting_history(session_id: str = "", limit: int = 30) -> dict:
        """Get past delivery reports (executive summary, themes, metrics, delivered items) for a
        session. Blank session_id = most recent session. Generating a new report uses report_delivery."""
        return await run_readonly(_reporting_history, session_id, limit)

    @app.tool()
    async def reporting_export(session_id: str = "") -> dict:
        """Export the most recent delivery report as Markdown + HTML files (under
        ~/.yeaboi/exports/reporting/) and return their paths. Blank session_id = most recent session."""
        return await run_readonly(_reporting_export, session_id)
