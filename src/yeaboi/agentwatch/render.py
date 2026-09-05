"""Rich rendering for agentwatch artifacts (CLI output; the TUI reuses these).

Pure formatting — no IO, no LLM. One ``format_*_rich`` per artifact kind,
returning a Rich renderable the CLI prints and the TUI embeds in its page
panel.
"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from yeaboi.agent.state import AgentAdvisorReport, AgentSecurityReport, AgentUsageReport

_ACCENT = "rgb(70,190,230)"  # AGENT_USAGE_THEME.accent
_MUTED = "rgb(120,120,140)"


def _tokens(n: int) -> str:
    """Compact token counts: 1234 → '1.2k', 5_600_000 → '5.6M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


_BARS = " ▁▂▃▄▅▆▇█"


def sparkline(points, *, accent: str = _ACCENT, width: int = 30) -> Text:
    """One-row cost-per-day sparkline over the trend, newest on the right.

    Block characters scaled to the busiest day; the caption names the peak so
    the bar height means something without an axis.
    """
    tail = list(points)[-width:]
    peak = max((p.cost_usd for p in tail), default=0.0)
    line = Text()
    line.append("trend ", style=_MUTED)
    for point in tail:
        level = 0 if peak <= 0 else min(8, int(round(point.cost_usd / peak * 8)))
        line.append(_BARS[level], style=accent)
    if tail:
        first, last = tail[0].date[5:], tail[-1].date[5:]
        line.append(f"  {first} → {last} · peak ${peak:,.2f}", style=_MUTED)
    return line


def format_usage_rich(report: AgentUsageReport) -> RenderableType:
    """The agent usage report as terminal output."""
    parts: list[RenderableType] = []

    header = Text()
    header.append("Agent Usage  ", style=f"bold {_ACCENT}")
    header.append(f"{report.period_start} → {report.period_end}", style=_MUTED)
    parts.append(header)

    from yeaboi.agentwatch.billing import label_for

    totals = Text()
    totals.append(f"≈ ${report.total_cost_usd:,.2f}", style="bold white")
    totals.append(f" {label_for(report.billing_kind)}", style=_MUTED)
    parts.append(totals)
    volume = Text(
        f"{report.session_count} session(s) — "
        f"{_tokens(report.total_input_tokens)} in / {_tokens(report.total_output_tokens)} out, "
        f"cache {_tokens(report.total_cache_read_tokens)} read / {_tokens(report.total_cache_write_tokens)} written",
        style=_MUTED,
    )
    if report.cache_cost_share > 0:
        volume.append(f" · {report.cache_cost_share:.0%} of the estimate is cache traffic", style=_MUTED)
    parts.append(volume)
    if report.daily_trend:
        parts.append(sparkline(report.daily_trend, accent=_ACCENT))
    if report.unknown_model_cost_share > 0:
        parts.append(
            Text(
                f"⚠ {report.unknown_model_cost_share:.0%} of the total is priced at a fallback tier "
                "(unknown model rates)",
                style="rgb(220,180,60)",
            )
        )
    parts.append(Text(f"rates as of {report.pricing_as_of}", style=_MUTED))

    if report.by_model:
        table = Table(
            title="By model", title_style=f"bold {_ACCENT}", header_style=_MUTED, border_style="rgb(50,60,80)"
        )
        table.add_column("model")
        table.add_column("cost", justify="right")
        table.add_column("in", justify="right")
        table.add_column("out", justify="right")
        table.add_column("cache r/w", justify="right")
        table.add_column("calls", justify="right")
        for row in report.by_model:
            model = row.model if row.known_pricing else f"{row.model} *"
            table.add_row(
                model,
                f"${row.cost_usd:,.2f}",
                _tokens(row.input_tokens),
                _tokens(row.output_tokens),
                f"{_tokens(row.cache_read_tokens)}/{_tokens(row.cache_write_tokens)}",
                str(row.calls),
            )
        parts.append(table)

    for title, rows in (("By project", report.by_project), ("By source", report.by_source)):
        if not rows:
            continue
        table = Table(title=title, title_style=f"bold {_ACCENT}", header_style=_MUTED, border_style="rgb(50,60,80)")
        table.add_column("")
        table.add_column("cost", justify="right")
        table.add_column("sessions", justify="right")
        table.add_column("in", justify="right")
        table.add_column("out", justify="right")
        for row in rows:
            table.add_row(
                row.key,
                f"${row.cost_usd:,.2f}",
                str(row.sessions),
                _tokens(row.input_tokens),
                _tokens(row.output_tokens),
            )
        parts.append(table)

    for title, items in (("Insights", report.insights), ("Recommendations", report.recommendations)):
        if not items:
            continue
        parts.append(Text(title, style=f"bold {_ACCENT}"))
        parts.extend(Text(f"  • {item}") for item in items)

    for warning in report.warnings:
        parts.append(Text(f"⚠ {warning}", style="rgb(220,180,60)"))

    return Group(*parts)


_ADVISOR_ACCENT = "rgb(240,180,70)"  # AGENT_ADVISOR_THEME.accent


def format_advisor_rich(report: AgentAdvisorReport) -> RenderableType:
    """The agent advisor report as terminal output."""
    parts: list[RenderableType] = []

    header = Text()
    header.append("Agent Advisor  ", style=f"bold {_ADVISOR_ACCENT}")
    header.append(f"{report.period_start} → {report.period_end}", style=_MUTED)
    parts.append(header)

    headline = Text()
    headline.append(f"~${report.recoverable_usd:,.2f} recoverable", style="bold white")
    headline.append(
        f" of ${report.total_cost_usd:,.2f} estimated spend"
        + (f" ({report.recoverable_share:.0%})" if report.total_cost_usd > 0 else "")
        + f" — {report.files_audited} transcript(s) audited",
        style=_MUTED,
    )
    parts.append(headline)
    if report.unknown_rate_share > 0:
        parts.append(
            Text(
                f"⚠ {report.unknown_rate_share:.0%} of input tokens priced at a fallback tier (unknown model rates)",
                style="rgb(220,180,60)",
            )
        )
    parts.append(
        Text(
            f"estimates: tokens ≈ bytes/4, priced at ~${report.effective_input_rate_per_mtok:,.2f}/Mtok input "
            f"(rates as of {report.pricing_as_of})",
            style=_MUTED,
        )
    )

    items = [i for i in report.line_items if i.content_bytes]
    if items:
        table = Table(
            title="Waste by mechanism",
            title_style=f"bold {_ADVISOR_ACCENT}",
            header_style=_MUTED,
            border_style="rgb(50,60,80)",
        )
        table.add_column("mechanism")
        table.add_column("calls", justify="right")
        table.add_column("size", justify="right")
        table.add_column("est. cost", justify="right")
        table.add_column("of Read bytes", justify="right")
        for item in items:
            label = item.label if item.recoverable else f"{item.label} *"
            table.add_row(
                label,
                str(item.calls) if item.calls else "—",
                _tokens(item.est_tokens) + " tok",
                f"${item.est_usd:,.2f}",
                f"{item.share_of_read_bytes:.0%}",
            )
        parts.append(table)
        if any(not i.recoverable for i in items):
            parts.append(Text("* sized as context, not counted in the recoverable total", style=_MUTED))

    health = Text()
    health.append("Cache health  ", style=f"bold {_ADVISOR_ACCENT}")
    health.append(
        f"alignment {report.alignment_score}/100 · a Read stays in context ~{report.residency_median} turn(s) "
        f"(p90 {report.residency_p90}) · {report.gaps_over_5m} cache-death gap(s) "
        f"across {report.sessions_with_gap} session(s)",
        style=_MUTED,
    )
    parts.append(health)

    if report.volatile_signals:
        table = Table(
            title="Volatile content in prompt-prefix files",
            title_style=f"bold {_ADVISOR_ACCENT}",
            header_style=_MUTED,
            border_style="rgb(50,60,80)",
        )
        table.add_column("file")
        table.add_column("findings", justify="right")
        table.add_column("kinds")
        for signal in report.volatile_signals:
            kinds = ", ".join(f"{label}×{count}" for label, count in signal.counts)
            table.add_row(signal.location, str(signal.total), kinds)
        parts.append(table)

    for title, prose in (("Insights", report.insights), ("Recommendations", report.recommendations)):
        if not prose:
            continue
        parts.append(Text(title, style=f"bold {_ADVISOR_ACCENT}"))
        parts.extend(Text(f"  • {item}") for item in prose)

    for warning in report.warnings:
        parts.append(Text(f"⚠ {warning}", style="rgb(220,180,60)"))

    return Group(*parts)


_SECURITY_ACCENT = "rgb(230,90,120)"  # AGENT_SECURITY_THEME.accent
_SEVERITY_STYLE = {
    "critical": "bold rgb(255,90,90)",
    "high": "rgb(230,120,80)",
    "medium": "rgb(220,180,60)",
    "info": _MUTED,
}
_POSTURE_STYLE = {
    "good": "bold rgb(80,220,120)",
    "needs-attention": "bold rgb(220,180,60)",
    "at-risk": "bold rgb(255,90,90)",
}


_WARN = "rgb(230,150,60)"  # the one warning tone on the page: a decision, and the turn that matched
_VERDICT_STYLE = {
    "needs-decision": f"bold {_WARN}",
    "unsure": "rgb(220,180,60)",
    "test-data": _MUTED,
    "handled": "rgb(120,210,170)",
    "info": _MUTED,
}
_VERDICT_LABEL = {
    "needs-decision": "Needs a decision",
    "unsure": "Worth a look",
    "test-data": "Looks like test data",
    "handled": "Handled",
    "info": "Informational",
}
_VERDICT_HINT = {
    "test-data": "written into or read from test, fixture or docs files — not run",
    "handled": "dismissed, fixed or rotated",
}


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def short_date(iso: str) -> str:
    """``2026-08-23…`` → ``Aug 23``; anything else comes back as given."""
    if len(iso) >= 10 and iso[4] == "-" and iso[7] == "-" and iso[5:7].isdigit() and iso[8:10].isdigit():
        return f"{_MONTHS[int(iso[5:7]) - 1]} {int(iso[8:10])}"
    return iso


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _issue_meta(issue) -> str:
    parts = []
    if issue.category in ("secret", "risky_tool"):
        parts.append(_plural(issue.sessions, "session"))
    else:
        parts.append("MCP config" if issue.category == "mcp" else "settings")
    if issue.signals > 1:
        parts.append(f"{issue.signals} signals")
    if issue.last_seen:
        parts.append(f"last {short_date(issue.last_seen)}")
    parts.append(issue.severity)
    return " · ".join(parts)


def format_security_rich(
    report: AgentSecurityReport,
    *,
    focus: int | None = None,
    expanded: tuple[str, ...] = ("needs-decision", "unsure"),
    mcp_table: bool = True,
) -> RenderableType:
    """The agent security report as terminal output: issues grouped by verdict.

    ``focus`` marks one issue row (the TUI's selection); the CLI passes
    nothing. ``expanded`` names the verdict groups listed in full — the rest
    fold to one line with a count. The TUI turns ``mcp_table`` off and says
    how many servers there are in one line instead.
    """
    parts: list[RenderableType] = []
    header = Text()
    header.append("Agent Security  ", style=f"bold {_SECURITY_ACCENT}")
    header.append(f"scanned {report.scan_date}", style=_MUTED)
    parts.append(header)

    if report.verdict_line:
        parts.append(Text(report.verdict_line, style="bold white"))
    posture = Text()
    posture.append("Posture: ")
    posture.append(report.posture, style=_POSTURE_STYLE.get(report.posture, "bold white"))
    posture.append(
        f" — {_plural(report.sessions_scanned, 'session')}, {_plural(len(report.mcp_servers), 'MCP server')}",
        style=_MUTED,
    )
    if report.posture_reason and not report.verdict_line:
        posture.append(f" · {report.posture_reason}", style=_MUTED)
    parts.append(posture)
    if report.new_findings or report.resolved_findings:
        delta = Text()
        delta.append(f"+{len(report.new_findings)} new", style="rgb(220,120,120)" if report.new_findings else _MUTED)
        delta.append(" / ", style=_MUTED)
        delta.append(
            f"−{len(report.resolved_findings)} resolved",
            style="rgb(120,210,170)" if report.resolved_findings else _MUTED,
        )
        delta.append(" since the last scan", style=_MUTED)
        parts.append(delta)
    elif report.finding_keys:
        parts.append(Text("no change since the last scan", style=_MUTED))

    counts = dict(report.verdict_counts)
    issues = list(report.issues)
    index = 0
    for verdict in ("needs-decision", "unsure", "test-data", "handled", "info"):
        rows = [i for i in issues if i.verdict == verdict]
        count = counts.get(verdict, 0)
        if not rows and not count:
            continue
        parts.append(Text(""))
        title = Text()
        title.append(_VERDICT_LABEL[verdict], style=_VERDICT_STYLE[verdict])
        title.append(f"  {count}", style=_MUTED)
        if verdict == "info" and report.hidden_info_count:
            title.append(
                f"  {report.hidden_info_count} finding(s) folded — `--show-info` or i to list them", style=_MUTED
            )
        elif verdict in _VERDICT_HINT:
            title.append(f"  {_VERDICT_HINT[verdict]}", style=_MUTED)
        parts.append(title)
        if verdict not in expanded:
            index += len(rows)
            continue
        for issue in rows:
            marker = "▶ " if focus is not None and index == focus else "  "
            line = Text(no_wrap=True, overflow="ellipsis")
            line.append(marker, style=_SECURITY_ACCENT)
            line.append(issue.title, style="bold white" if verdict == "needs-decision" else "white")
            line.append(f"  {_issue_meta(issue)}", style=_MUTED)
            if issue.fixes:
                line.append(f"  → {issue.fixes[0].label}", style=_VERDICT_STYLE[verdict])
            parts.append(line)
            index += 1

    if report.mcp_servers and mcp_table:
        parts.append(Text(""))
        table = Table(
            title="MCP servers",
            title_style=f"bold {_SECURITY_ACCENT}",
            header_style=_MUTED,
            border_style="rgb(50,60,80)",
        )
        table.add_column("name")
        table.add_column("scope")
        table.add_column("transport")
        table.add_column("flags")
        for record in report.mcp_servers:
            table.add_row(record.name, record.scope, record.transport, ", ".join(record.flags) or "—")
        parts.append(table)

    if report.summary and report.summary != report.verdict_line:
        parts.append(Text(""))
        parts.append(Text("Full write-up", style=f"bold {_SECURITY_ACCENT}"))
        parts.append(Text(report.summary, style="white"))
    if report.recommendations:
        parts.extend(Text(f"  • {item}") for item in report.recommendations)

    for warning in report.warnings:
        parts.append(Text(f"⚠ {warning}", style="rgb(220,180,60)"))

    return Group(*parts)


def format_issue_rich(
    report: AgentSecurityReport, issue, *, fix_sel: int | None = None, signals: bool = True
) -> RenderableType:
    """One issue in detail: the why, its fixes, and (unless ``signals`` is off) every signal behind it."""
    parts: list[RenderableType] = []
    head = Text()
    head.append(issue.title, style="bold white")
    head.append(
        f"  {_VERDICT_LABEL.get(issue.verdict, issue.verdict).lower()}", style=_VERDICT_STYLE.get(issue.verdict, "")
    )
    parts.append(head)
    parts.append(Text(_issue_meta(issue), style=_MUTED))
    if issue.why:
        parts.append(Text(""))
        parts.append(Text("Why it matters", style=f"bold {_SECURITY_ACCENT}"))
        parts.append(Text(issue.why))
    if issue.fixes:
        parts.append(Text(""))
        parts.append(Text("Fix", style=f"bold {_SECURITY_ACCENT}"))
        for index, fix in enumerate(issue.fixes):
            line = Text()
            line.append("▶ " if fix_sel is not None and index == fix_sel else "  ", style=_SECURITY_ACCENT)
            line.append(fix.label, style="bold white" if index == 0 else "white")
            if fix.detail:
                line.append(f"  {fix.detail}", style=_MUTED)
            parts.append(line)
    if signals:
        parts.extend(_signal_rows(report, issue))
    return Group(*parts)


def _signal_rows(report: AgentSecurityReport, issue, *, focus: int | None = None) -> list[RenderableType]:
    rows = [f for f in report.findings if f.key in issue.finding_keys]
    if not rows:
        return []
    parts: list[RenderableType] = [Text(""), Text(f"Every signal ({len(rows)})", style=f"bold {_SECURITY_ACCENT}")]
    for index, f in enumerate(rows):
        line = Text()
        line.append("▶ " if focus is not None and index == focus else "  ", style=_SECURITY_ACCENT)
        line.append(f.project_label or f.location, style="white")
        if f.session_id:
            line.append(f" · {f.session_id[:8]}", style=_MUTED)
        if f.at:
            line.append(f" · {short_date(f.at)}", style=_MUTED)
        if f.occurrences > 1:
            line.append(f" · {f.occurrences} lines", style=_MUTED)
        if f.verdict_reason:
            line.append(f" — {f.verdict_reason}", style=_VERDICT_STYLE.get(f.verdict, _MUTED))
        parts.append(line)
        if f.snippet:
            parts.append(Text(f"      {f.snippet}", style=_MUTED, no_wrap=True, overflow="ellipsis"))
    return parts


def format_signals_rich(report: AgentSecurityReport, issue, *, focus: int | None = None) -> RenderableType:
    """Every signal behind one issue, the focused one marked."""
    return Group(*_signal_rows(report, issue, focus=focus))


def format_replay_rich(replay) -> RenderableType:
    """A replay as a turn list, the flagged turn in the warning tone."""
    parts: list[RenderableType] = []
    head = Text()
    head.append("Replay  ", style=f"bold {_SECURITY_ACCENT}")
    head.append(f"session {replay.session_id[:8]} · line {replay.line_no}", style=_MUTED)
    if replay.started_at:
        head.append(f" · {short_date(replay.started_at)}", style=_MUTED)
    parts.append(head)
    for turn in replay.turns:
        line = Text()
        style = _WARN if turn.flagged else _MUTED
        line.append(f"{turn.at or '        '}  ", style=_MUTED)
        who = turn.tool if turn.kind == "tool_use" else turn.role
        line.append(f"{who:<10}", style=f"bold {style}" if turn.flagged else style)
        body = turn.text.replace("\n", " ⏎ ")
        line.append(body, style="bold white" if turn.flagged else "white")
        if turn.flagged:
            line.append("   ← this matched", style=_WARN)
        parts.append(line)
    for warning in replay.warnings:
        parts.append(Text(f"⚠ {warning}", style="rgb(220,180,60)"))
    return Group(*parts)
