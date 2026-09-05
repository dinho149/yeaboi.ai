"""Screen builders for the Agents family pages.

Same shared-component structure as every other mode page (tui-standards):
pinned wordmark title + subtitle + content, wrapped in ``build_page_panel``
with the mode's theme. Lists are CAPPED, not scrolled — the dashboard shows
the top rows and says how many more exist (the repo's capped-viewport
convention), so the page renders correctly at the minimum terminal size.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import PurePath

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from yeaboi.agent.state import AgentAdvisorReport, AgentSecurityReport, AgentUsageReport
from yeaboi.agentwatch.render import format_usage_rich
from yeaboi.analysis.progress import is_component_progress
from yeaboi.timeparse import parse_datetime
from yeaboi.ui.shared._components import (
    AGENT_USAGE_THEME,
    agent_usage_title,
    build_action_buttons,
    build_meter,
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

# Static phase checklists, one per page — the screen owns what "pending" looks
# like; the engines only emit lifecycle events keyed on these component ids
# (see agentwatch/engine.py). Unknown ids still render, after the checklist.
_USAGE_PHASES: tuple[tuple[str, str], ...] = (
    ("scan", "Scan agent sessions"),
    ("price", "Price usage"),
    ("insights", "Write insights"),
)
_SECURITY_PHASES: tuple[tuple[str, str], ...] = (
    ("scan", "Scan transcripts"),
    ("settings", "Audit settings"),
    ("mcp", "Inventory MCP servers"),
    ("summary", "Write the summary"),
)
_ADVISOR_PHASES: tuple[tuple[str, str], ...] = (
    ("scan", "Scan agent sessions"),
    ("audit", "Audit Read waste"),
    ("signals", "Check cache health"),
    ("insights", "Write advice"),
)

# Marker per terminal status — same vocabulary as the analysis activity rows
# (✓ done, ~ fallback, ! partial, ✗ failed, ○ nothing/no data).
_STATUS_MARKS = {"completed": "✓", "fallback": "~", "partial": "!", "failed": "✗", "no_data": "○"}


def _fmt_elapsed(seconds: float) -> str:
    """0:07-style elapsed stamp for the progress header."""
    total = max(0, int(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _relative_age(iso: str, *, now: datetime | None = None) -> str:
    """A human age for a stored report's timestamp: "5m ago", "2h ago", "3d ago".

    Unparseable input returns "" — no stamp rather than a wrong one.
    """
    try:
        then = parse_datetime(iso)
    except (TypeError, ValueError):
        return ""
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    resolved_now = now or datetime.now(timezone.utc)
    seconds = (resolved_now - then).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _phase_row(label: str, event: dict | None, *, frame: str, theme) -> Text:
    """One checklist row: pending ○, running spinner (+ meter), or its terminal mark."""
    row = Text(justify="center")
    if event is None:
        row.append("○ ", style="rgb(90,90,105)")
        row.append(label, style="rgb(110,110,125)")
        return row
    status = event.get("status", "")
    detail = str(event.get("detail", "") or "")
    if status == "running":
        row.append(f"{frame} ", style=theme.accent_bright)
        row.append(label, style="rgb(200,200,210)")
        current, total = event.get("current"), event.get("total")
        if current is not None and total:
            row.append("  ")
            row.append_text(build_meter(int(current), int(total), width=12, theme=theme))
            unit = str(event.get("unit", "") or "")
            row.append(f"  {current}/{total}{f' {unit}' if unit else ''}", style="rgb(160,160,175)")
            secondary = event.get("secondary_count")
            secondary_unit = str(event.get("secondary_unit", "") or "")
            if secondary is not None and secondary_unit:
                row.append(f" · {secondary} {secondary_unit}", style="rgb(110,110,125)")
        elif detail:
            row.append(f" · {detail}", style="rgb(160,160,175)")
        return row
    mark = _STATUS_MARKS.get(status, "○")
    mark_style = {"✓": theme.accent, "~": theme.warn, "!": theme.warn, "✗": theme.bad}.get(mark, "rgb(90,90,105)")
    row.append(f"{mark} ", style=mark_style)
    row.append(label, style="rgb(160,160,175)")
    if detail:
        row.append(f" · {detail}", style="rgb(110,110,125)")
    return row


def _build_agent_progress_body(
    phases: tuple[tuple[str, str], ...],
    progress: list,
    *,
    tick: float,
    theme,
    status: str = "",
) -> list:
    """The in-progress body: header spinner + elapsed, then the phase checklist.

    ``progress`` is a list of analysis_component lifecycle events (latest wins
    per component id — folded here defensively even though the page loop
    pre-folds). Stateless by design: elapsed derives from ``tick`` rather than
    module clocks, so renders are pure and testable.
    """
    latest: dict[str, dict] = {}
    for item in progress:
        if is_component_progress(item):
            latest[item["component_id"]] = item
    frame = _SPINNER[int(tick * 10) % len(_SPINNER)]

    header = Text(justify="center")
    header.append(f"{frame} ", style=theme.accent_bright)
    header.append("Working", style="rgb(200,200,210)")
    header.append(f" · {_fmt_elapsed(tick)}", style="rgb(110,110,125)")
    rows: list = [Text(""), header, Text("")]

    known = {pid for pid, _ in phases}
    for pid, label in phases:
        rows.append(_phase_row(label, latest.get(pid), frame=frame, theme=theme))
    for pid, event in latest.items():
        if pid not in known:
            rows.append(_phase_row(str(event.get("label", pid)), event, frame=frame, theme=theme))
    if status:
        rows.append(Text(""))
        rows.append(Text(status, style="rgb(110,110,125)", justify="center"))
    return rows


def _refreshing_line(as_of: str, *, tick: float, theme, progress: list | None = None) -> Text:
    """The one-line banner under a shown report while a fresh run replaces it.

    After the first-ever run the page always opens on a saved report, so this
    banner — not the full checklist — is where refresh progress is seen: it
    names the running phase and, while scanning, the files meter counts.
    """
    frame = _SPINNER[int(tick * 10) % len(_SPINNER)]
    line = Text(justify="center")
    line.append(f"{frame} ", style=theme.accent_bright)
    line.append("Refreshing", style="rgb(160,160,175)")
    running = next(
        (e for e in progress or [] if is_component_progress(e) and e.get("status") == "running"),
        None,
    )
    if running is not None:
        line.append(f" — {running['label']}", style="rgb(160,160,175)")
        current, total = running.get("current"), running.get("total")
        if current is not None and total:
            unit = str(running.get("unit", "") or "")
            line.append(f" {current}/{total}{f' {unit}' if unit else ''}", style="rgb(110,110,125)")
    else:
        line.append("…", style="rgb(160,160,175)")
    age = _relative_age(as_of)
    if age:
        line.append(f" · showing report from {age}", style="rgb(110,110,125)")
    return line


def _repo_tail(path: str) -> str:
    """A path by its last two components, prefixed with … when that drops any."""
    pure = PurePath(path)
    names = pure.parts[1:] if pure.anchor else pure.parts
    if len(names) <= 2:
        return path
    return "…/" + "/".join(names[-2:])


def _subtitle(default: str, scope: str) -> str:
    """The page subtitle, with the repository a scoped run is narrowed to."""
    return f"{default} · {_repo_tail(scope)}" if scope else default


def _subtitle_row(default: str, scope: str) -> Text:
    """The subtitle pinned to one row, so a long scope never shifts the layout."""
    row = build_reveal_subtitle(_subtitle(default, scope), None, justify="center")
    row.no_wrap = True
    row.overflow = "ellipsis"
    return row


def _result_footer(action_sel: int, notice: str, theme, *, hints: str = "") -> list:
    """The shared footer under a finished report: notice line + action buttons.

    One helper for all three pages, and it goes through ``build_action_buttons``
    rather than painting its own key strip — tui-standards rule 1. The notice
    row is always present so the page height does not jump when an export
    reports back. ``hints`` names the page's extra keys (window, info, dismiss).
    """
    rows: list = [Text("")]
    rows.append(Text(notice, style=theme.accent if notice else "", justify="center"))
    rows.append(Text(hints, style="rgb(110,110,125)", justify="center") if hints else Text(""))
    rows.extend(build_action_buttons(AGENT_RESULT_ACTIONS, action_sel))
    return rows


def _window_hint(options: dict | None) -> str:
    """The key hint for a windowed page, naming the window in force."""
    days = (options or {}).get("window_days")
    return f"w  window · {days} days" if days else ""


def _dismiss_prompt(dismiss_edit: str | None, theme) -> list:
    """The one-line reason prompt shown while a dismissal is being typed."""
    if dismiss_edit is None:
        return []
    prompt = Text(justify="center")
    prompt.append("Why is this finding expected?  ", style=theme.accent_bright)
    prompt.append(dismiss_edit or "", style="bold white")
    prompt.append("▏", style=theme.accent)
    prompt.append("   enter to dismiss · esc to keep", style="rgb(110,110,125)")
    return [Text(""), prompt]


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
    progress: list | None = None,
    refreshing: bool = False,
    as_of: str = "",
    scope: str = "",
    options: dict | None = None,
    finding_sel: int = 0,
    dismiss_edit: str | None = None,
) -> Panel:
    """The Agent Usage dashboard page.

    ``report=None`` renders the in-progress state — the phase checklist when
    structured ``progress`` events are given, else a one-line spinner over the
    ``status`` string; a report renders the capped dashboard, with a
    "Refreshing…" banner when a background re-run is replacing it.
    """
    theme = AGENT_USAGE_THEME
    parts: list = [
        Text(""),
        agent_usage_title(shimmer_tick, width=width),
        _subtitle_row("What your agents cost", scope),
        Text(""),
    ]

    if report is None:
        if progress is not None:
            parts += _build_agent_progress_body(
                _USAGE_PHASES, progress, tick=shimmer_tick or 0.0, theme=theme, status=status
            )
        else:
            frame = _SPINNER[int((shimmer_tick or 0.0) * 10) % len(_SPINNER)]
            working = Text(justify="center")
            working.append(f"{frame} ", style=theme.accent_bright)
            working.append(status or "Collecting local agent sessions…", style="rgb(160,160,175)")
            parts += [Text(""), working]
    else:
        if refreshing:
            parts.append(_refreshing_line(as_of, tick=shimmer_tick or 0.0, theme=theme, progress=progress))
        capped, notes = _capped(report)
        parts.append(format_usage_rich(capped))
        for note in notes:
            parts.append(Text(note, style="rgb(110,110,125)"))
        parts.extend(_result_footer(action_sel, notice, theme, hints=_window_hint(options)))

    panel = build_page_panel(Group(*parts), theme=theme, height=height)
    # The chrome's corner companion and entrance read this stamp — Agents pages
    # get the robo, not the duck (see MusicLive.get_renderable).
    panel._duck_mascot = "robo"
    return panel


def _capped_advisor(report: AgentAdvisorReport) -> tuple[AgentAdvisorReport, list[str]]:
    """Cap the advisor report's list fields for on-screen rendering."""
    notes: list[str] = []
    if len(report.volatile_signals) > _MAX_BREAKDOWN_ROWS:
        notes.append(f"… and {len(report.volatile_signals) - _MAX_BREAKDOWN_ROWS} more file(s) in the export")
    capped = replace(
        report,
        # Line items are a fixed five-row taxonomy — they always fit; only the
        # volatile-file table and the prose grow with the corpus.
        volatile_signals=report.volatile_signals[:_MAX_BREAKDOWN_ROWS],
        insights=report.insights[:_MAX_PROSE],
        recommendations=report.recommendations[:_MAX_PROSE],
    )
    return capped, notes


def _build_agent_advisor_screen(
    report: AgentAdvisorReport | None = None,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float | None = None,
    status: str = "",
    action_sel: int = 0,
    notice: str = "",
    progress: list | None = None,
    refreshing: bool = False,
    as_of: str = "",
    scope: str = "",
    options: dict | None = None,
    finding_sel: int = 0,
    dismiss_edit: str | None = None,
) -> Panel:
    """The Agent Advisor page: phase checklist while auditing, capped report when done."""
    from yeaboi.agentwatch.render import format_advisor_rich
    from yeaboi.ui.shared._components import AGENT_ADVISOR_THEME, agent_advisor_title

    theme = AGENT_ADVISOR_THEME
    parts: list = [
        Text(""),
        agent_advisor_title(shimmer_tick, width=width),
        _subtitle_row("How much of your agent spend is recoverable", scope),
        Text(""),
    ]
    if report is None:
        if progress is not None:
            parts += _build_agent_progress_body(
                _ADVISOR_PHASES, progress, tick=shimmer_tick or 0.0, theme=theme, status=status
            )
        else:
            frame = _SPINNER[int((shimmer_tick or 0.0) * 10) % len(_SPINNER)]
            working = Text(justify="center")
            working.append(f"{frame} ", style=theme.accent_bright)
            working.append(status or "Auditing agent sessions…", style="rgb(160,160,175)")
            parts += [Text(""), working]
    else:
        if refreshing:
            parts.append(_refreshing_line(as_of, tick=shimmer_tick or 0.0, theme=theme, progress=progress))
        capped, notes = _capped_advisor(report)
        parts.append(format_advisor_rich(capped))
        for note in notes:
            parts.append(Text(note, style="rgb(110,110,125)"))
        parts.extend(_result_footer(action_sel, notice, theme, hints=_window_hint(options)))
    panel = build_page_panel(Group(*parts), theme=theme, height=height)
    # The chrome's corner companion and entrance read this stamp — Agents pages
    # get the robo, not the duck (see MusicLive.get_renderable).
    panel._duck_mascot = "robo"
    return panel


_MAX_FINDING_ROWS = 12


def _capped_security(report: AgentSecurityReport, *, focus: int = 0) -> tuple[AgentSecurityReport, list[str]]:
    """Cap the security report's list fields for on-screen rendering.

    Findings are already grouped per (pattern, file), so a dozen rows is the
    whole story on most machines; the window slides so the focused row is
    always on screen.
    """
    notes: list[str] = []
    total = len(report.findings)
    start = 0
    if total > _MAX_FINDING_ROWS:
        start = min(max(0, focus - _MAX_FINDING_ROWS + 1), total - _MAX_FINDING_ROWS)
        notes.append(f"… {total - _MAX_FINDING_ROWS} more finding(s) in the export (↑↓ to scroll)")
    if len(report.mcp_servers) > _MAX_BREAKDOWN_ROWS:
        notes.append(f"… and {len(report.mcp_servers) - _MAX_BREAKDOWN_ROWS} more MCP server(s) in the export")
    capped = replace(
        report,
        findings=report.findings[start : start + _MAX_FINDING_ROWS],
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
    progress: list | None = None,
    refreshing: bool = False,
    as_of: str = "",
    scope: str = "",
    options: dict | None = None,
    finding_sel: int = 0,
    dismiss_edit: str | None = None,
) -> Panel:
    """The Agent Security page: phase checklist while scanning, capped report when done."""
    from yeaboi.agentwatch.render import format_security_rich
    from yeaboi.ui.shared._components import AGENT_SECURITY_THEME, agent_security_title

    theme = AGENT_SECURITY_THEME
    parts: list = [
        Text(""),
        agent_security_title(shimmer_tick, width=width),
        _subtitle_row("Your agent setup, audited", scope),
        Text(""),
    ]
    if report is None:
        if progress is not None:
            parts += _build_agent_progress_body(
                _SECURITY_PHASES, progress, tick=shimmer_tick or 0.0, theme=theme, status=status
            )
        else:
            frame = _SPINNER[int((shimmer_tick or 0.0) * 10) % len(_SPINNER)]
            working = Text(justify="center")
            working.append(f"{frame} ", style=theme.accent_bright)
            working.append(status or "Scanning agent configuration…", style="rgb(160,160,175)")
            parts += [Text(""), working]
    else:
        if refreshing:
            parts.append(_refreshing_line(as_of, tick=shimmer_tick or 0.0, theme=theme, progress=progress))
        capped, notes = _capped_security(report, focus=finding_sel)
        parts.append(format_security_rich(capped, focus=min(finding_sel, max(0, len(capped.findings) - 1))))
        for note in notes:
            parts.append(Text(note, style="rgb(110,110,125)"))
        parts.extend(_dismiss_prompt(dismiss_edit, theme))
        info = "showing info" if (options or {}).get("include_info") else "info hidden"
        parts.extend(_result_footer(action_sel, notice, theme, hints=f"↑↓ finding · d  dismiss · i  {info}"))
    panel = build_page_panel(Group(*parts), theme=theme, height=height)
    # The chrome's corner companion and entrance read this stamp — Agents pages
    # get the robo, not the duck (see MusicLive.get_renderable).
    panel._duck_mascot = "robo"
    return panel
