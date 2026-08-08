"""Screen builders for the Agents family pages.

Same shared-component structure as every other mode page (tui-standards):
pinned wordmark title + subtitle + content, wrapped in ``build_page_panel``
with the mode's theme. Lists are CAPPED, not scrolled — the dashboard shows
the top rows and says how many more exist (the repo's capped-viewport
convention), so the page renders correctly at the minimum terminal size.
"""

from __future__ import annotations

from dataclasses import replace

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from yeaboi.agent.state import AgentSecurityReport, AgentStandupDigest, AgentUsageReport
from yeaboi.agentwatch.render import format_usage_rich
from yeaboi.ui.shared._components import (
    AGENT_USAGE_THEME,
    agent_usage_title,
    build_action_buttons,
    build_page_panel,
    build_reveal_subtitle,
)

# The result-screen actions, shared by all three pages. Export writes the
# Markdown artifact and Copy puts the same Markdown on the clipboard — there is
# no destination picker because agentwatch has exactly one export format (HTML
# is deliberately absent until an export component exists; see the beta notice).
AGENT_RESULT_ACTIONS = ["Export", "Copy", "Re-run", "Back"]

# Row caps so the dashboard fits the minimum supported terminal (40 rows)
# without a scroll model. The markdown export carries the full tables; the
# by-source table is export-only for the same budget reason.
_MAX_MODEL_ROWS = 5
_MAX_BREAKDOWN_ROWS = 3
_MAX_PROSE = 2

_SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def _result_footer(action_sel: int, notice: str, theme) -> list:
    """The shared footer under a finished report: notice line + action buttons.

    One helper for all three pages, and it goes through ``build_action_buttons``
    rather than painting its own key strip — tui-standards rule 1. The notice
    row is always present so the page height does not jump when an export
    reports back.
    """
    rows: list = [Text("")]
    rows.append(Text(notice, style=theme.accent if notice else "", justify="center"))
    rows.append(Text(""))
    rows.extend(build_action_buttons(AGENT_RESULT_ACTIONS, action_sel))
    return rows


def _capped(report: AgentUsageReport) -> tuple[AgentUsageReport, list[str]]:
    """Cap the report's list fields for on-screen rendering.

    Returns the capped copy plus "… and N more" notes for anything trimmed.
    """
    notes: list[str] = []
    if len(report.by_model) > _MAX_MODEL_ROWS:
        notes.append(f"… and {len(report.by_model) - _MAX_MODEL_ROWS} more model(s) in the export")
    if len(report.by_project) > _MAX_BREAKDOWN_ROWS:
        notes.append(f"… and {len(report.by_project) - _MAX_BREAKDOWN_ROWS} more project(s) in the export")
    capped = replace(
        report,
        by_model=report.by_model[:_MAX_MODEL_ROWS],
        by_project=report.by_project[:_MAX_BREAKDOWN_ROWS],
        by_source=(),  # export-only: the screen budget goes to models + projects
        daily_trend=(),  # the trend table is export-only; the screen stays compact
        insights=report.insights[:_MAX_PROSE],
        recommendations=report.recommendations[:_MAX_PROSE],
    )
    return capped, notes


def _build_agent_usage_screen(
    report: AgentUsageReport | None,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float | None = None,
    status: str = "",
    action_sel: int = 0,
    notice: str = "",
) -> Panel:
    """The Agent Usage dashboard page.

    ``report=None`` renders the in-progress state (spinner + the engine's
    current ``status`` string); a report renders the capped dashboard.
    """
    theme = AGENT_USAGE_THEME
    parts: list = [
        Text(""),
        agent_usage_title(shimmer_tick, width=width),
        build_reveal_subtitle("What your agents cost", None, justify="center"),
        Text(""),
    ]

    if report is None:
        frame = _SPINNER[int((shimmer_tick or 0.0) * 10) % len(_SPINNER)]
        working = Text(justify="center")
        working.append(f"{frame} ", style=theme.accent_bright)
        working.append(status or "Collecting local agent sessions…", style="rgb(160,160,175)")
        parts += [Text(""), working]
    else:
        capped, notes = _capped(report)
        parts.append(format_usage_rich(capped))
        for note in notes:
            parts.append(Text(note, style="rgb(110,110,125)"))
        parts.extend(_result_footer(action_sel, notice, theme))

    panel = build_page_panel(Group(*parts), theme=theme, height=height)
    # The chrome's corner companion and entrance read this stamp — Agents pages
    # get the robo, not the duck (see MusicLive.get_renderable).
    panel._duck_mascot = "robo"
    return panel


def _capped_standup(digest: AgentStandupDigest) -> tuple[AgentStandupDigest, list[str]]:
    """Cap the digest's list fields for on-screen rendering (export keeps all)."""
    notes: list[str] = []
    if len(digest.session_summaries) > _MAX_BREAKDOWN_ROWS:
        notes.append(f"… and {len(digest.session_summaries) - _MAX_BREAKDOWN_ROWS} more session(s) in the export")
    if len(digest.repo_activity) > _MAX_BREAKDOWN_ROWS:
        notes.append(f"… and {len(digest.repo_activity) - _MAX_BREAKDOWN_ROWS} more tracker item(s) in the export")
    capped = replace(
        digest,
        session_summaries=digest.session_summaries[:_MAX_BREAKDOWN_ROWS],
        repo_activity=digest.repo_activity[:_MAX_BREAKDOWN_ROWS],
        highlights=digest.highlights[:_MAX_PROSE],
        in_flight=digest.in_flight[:_MAX_PROSE],
        attention_items=digest.attention_items[:_MAX_PROSE],
        coverage_notes=digest.coverage_notes[:1],
    )
    return capped, notes


def _build_agent_standup_screen(
    digest=None,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float | None = None,
    status: str = "",
    action_sel: int = 0,
    notice: str = "",
) -> Panel:
    """The Agent Standup page: spinner while running, capped digest when done."""
    from yeaboi.agentwatch.render import format_standup_rich
    from yeaboi.ui.shared._components import AGENT_STANDUP_THEME, agent_standup_title

    theme = AGENT_STANDUP_THEME
    parts: list = [
        Text(""),
        agent_standup_title(shimmer_tick, width=width),
        build_reveal_subtitle("What your agents did", None, justify="center"),
        Text(""),
    ]
    if digest is None:
        frame = _SPINNER[int((shimmer_tick or 0.0) * 10) % len(_SPINNER)]
        working = Text(justify="center")
        working.append(f"{frame} ", style=theme.accent_bright)
        working.append(status or "Collecting agent activity…", style="rgb(160,160,175)")
        parts += [Text(""), working]
    else:
        capped, notes = _capped_standup(digest)
        parts.append(format_standup_rich(capped))
        for note in notes:
            parts.append(Text(note, style="rgb(110,110,125)"))
        parts.extend(_result_footer(action_sel, notice, theme))
    panel = build_page_panel(Group(*parts), theme=theme, height=height)
    # The chrome's corner companion and entrance read this stamp — Agents pages
    # get the robo, not the duck (see MusicLive.get_renderable).
    panel._duck_mascot = "robo"
    return panel


def _capped_security(report: AgentSecurityReport) -> tuple[AgentSecurityReport, list[str]]:
    """Cap the security report's list fields for on-screen rendering."""
    notes: list[str] = []
    if len(report.findings) > _MAX_MODEL_ROWS:
        notes.append(f"… and {len(report.findings) - _MAX_MODEL_ROWS} more finding(s) in the export")
    if len(report.mcp_servers) > _MAX_BREAKDOWN_ROWS:
        notes.append(f"… and {len(report.mcp_servers) - _MAX_BREAKDOWN_ROWS} more MCP server(s) in the export")
    capped = replace(
        report,
        findings=report.findings[:_MAX_MODEL_ROWS],
        mcp_servers=report.mcp_servers[:_MAX_BREAKDOWN_ROWS],
        recommendations=report.recommendations[:_MAX_PROSE],
    )
    return capped, notes


def _build_agent_security_screen(
    report: AgentSecurityReport | None = None,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float | None = None,
    status: str = "",
    action_sel: int = 0,
    notice: str = "",
) -> Panel:
    """The Agent Security page: spinner while scanning, capped report when done."""
    from yeaboi.agentwatch.render import format_security_rich
    from yeaboi.ui.shared._components import AGENT_SECURITY_THEME, agent_security_title

    theme = AGENT_SECURITY_THEME
    parts: list = [
        Text(""),
        agent_security_title(shimmer_tick, width=width),
        build_reveal_subtitle("Your agent setup, audited", None, justify="center"),
        Text(""),
    ]
    if report is None:
        frame = _SPINNER[int((shimmer_tick or 0.0) * 10) % len(_SPINNER)]
        working = Text(justify="center")
        working.append(f"{frame} ", style=theme.accent_bright)
        working.append(status or "Scanning agent configuration…", style="rgb(160,160,175)")
        parts += [Text(""), working]
    else:
        capped, notes = _capped_security(report)
        parts.append(format_security_rich(capped))
        for note in notes:
            parts.append(Text(note, style="rgb(110,110,125)"))
        parts.extend(_result_footer(action_sel, notice, theme))
    panel = build_page_panel(Group(*parts), theme=theme, height=height)
    # The chrome's corner companion and entrance read this stamp — Agents pages
    # get the robo, not the duck (see MusicLive.get_renderable).
    panel._duck_mascot = "robo"
    return panel
