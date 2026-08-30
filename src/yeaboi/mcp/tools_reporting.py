"""MCP tools: Reporting mode (business-friendly delivery reports)."""

from __future__ import annotations

import logging

# Context must be importable from module globals — FastMCP evaluates the
# stringified type hints (PEP 563) of tool functions against this namespace.
from mcp.server.fastmcp import Context

from yeaboi.mcp.runtime import run_engine, run_readonly, to_jsonable

logger = logging.getLogger(__name__)

_PERIODS = ("last_sprint", "last_week", "last_month", "quarter", "window")


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


def _reporting_export(session_id: str, theme: str, style: dict | None = None) -> dict:
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.paths import get_db_path
    from yeaboi.reporting.export import export_report
    from yeaboi.reporting.store import ReportingStore
    from yeaboi.reporting.style import load_deck_style, style_from_dict, style_to_dict

    resolved = resolve_session_id(session_id)
    with ReportingStore(get_db_path()) as store:
        report = store.get_latest_report(resolved)
        run_history = store.get_history(resolved, limit=30)
    if report is None:
        raise ValueError(
            f"No delivery report recorded for session {resolved!r} — generate one from the yeaboi TUI first."
        )
    # Per-call style keys override the saved prefs; omitted keys keep them.
    # style_from_dict tolerates any malformed value, so an untrusted dict is safe.
    deck_style = load_deck_style()
    if style:
        deck_style = style_from_dict({**style_to_dict(deck_style), **style})
    paths = export_report(report, history=run_history, theme=theme or "midnight", style=deck_style)
    logger.info("Delivery report exported via MCP: session=%s period=%s", resolved, report.period_label)
    result = {
        "session_id": resolved,
        "period": report.period_label,
        "markdown": str(paths["markdown"]),
        "html": str(paths["html"]),
        "slides": str(paths["slides"]),
    }
    if "pptx" in paths:
        result["pptx"] = str(paths["pptx"])
    return result


def _report_delivery(
    period: str,
    session_id: str,
    jira_project: str,
    azdo_project: str,
    window_start: str,
    window_end: str,
    sprint_names: list | None,
    period_label_override: str,
    theme: str,
    sources: dict | None,
    project_id: str,
):
    if period not in _PERIODS:
        raise ValueError(f"period must be one of {', '.join(_PERIODS)} — got {period!r}")
    from yeaboi.reporting.engine import run_delivery_report

    return run_delivery_report(
        period,
        session_id=session_id,
        jira_project=jira_project,
        azdo_project=azdo_project,
        project_id=project_id,
        window_start=window_start,
        window_end=window_end,
        sprint_names=tuple(sprint_names or ()),
        period_label_override=period_label_override,
        theme=theme or "midnight",
        sources=sources,
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
        theme: str = "midnight",
        sources: dict[str, list[str]] | None = None,
        project_id: str = "",
    ) -> dict:
        """Generate a stakeholder-friendly delivery report of completed work from the team's
        tracker (Jira/Azure DevOps): executive summary, outcome themes, metrics, highlights.
        period: 'last_week', 'last_sprint', 'last_month', 'quarter', or 'window'. For 'quarter'
        or 'window', frame the span explicitly: window_start/window_end (YYYY-MM-DD) bound the
        reporting span, sprint_names lists the sprints it covers, and period_label_override
        renames the period (e.g. 'Q3 2026'). theme names the export palette — built-ins
        midnight/aurora/sunset/mono or a custom palette from reporting_themes.json. sources
        restricts inputs, e.g. {'delivery': ['jira','azuredevops'], 'code': ['github',
        'azuredevops'], 'docs': ['confluence','notion']} — delivery picks the tracker(s)
        tickets come from, code/docs add supporting PR/commit and doc-update context
        (azdevops/azure_devops accepted as aliases); omit for all configured. Blank
        session_id = most recent session (sprint length/project name). project_id scopes the
        sprint framing to a project's latest plan; blank inherits the session's own link."""
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
            theme,
            sources,
            project_id,
        )

    @app.tool()
    async def reporting_history(session_id: str = "", limit: int = 30) -> dict:
        """Get past delivery reports (executive summary, themes, metrics, delivered items) for a
        session. Blank session_id = most recent session. Generating a new report uses report_delivery."""
        return await run_readonly(_reporting_history, session_id, limit)

    @app.tool()
    async def reporting_export(session_id: str = "", theme: str = "midnight", style: dict | None = None) -> dict:
        """Export the most recent delivery report as Markdown + HTML + slide deck (and .pptx when
        python-pptx is installed) under ~/.yeaboi/exports/reporting/ and return their paths.
        theme picks the deck palette (built-in or custom). style customizes the deck/.pptx —
        keys: title_color/heading_color (palette role or #RRGGBB), font_family (modern|classic|
        mono|rounded), font_scale (compact|normal|large), layout (detailed|compact), content_fit
        (ask|expand = add slides so nothing is trimmed; tight = fixed grid, may trim), max_bullets,
        include_items_table/include_signals/include_highlights/include_thanks, slide_numbers,
        footer_text; omitted keys keep the saved preferences (reporting_prefs.json). Blank
        session_id = most recent session."""
        return await run_readonly(_reporting_export, session_id, theme, style)
