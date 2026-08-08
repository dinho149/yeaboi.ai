"""Rich rendering for agentwatch artifacts (CLI output; the TUI reuses these).

Pure formatting — no IO, no LLM. One ``format_*_rich`` per artifact kind,
returning a Rich renderable the CLI prints and the TUI embeds in its page
panel.
"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from yeaboi.agent.state import AgentSecurityReport, AgentStandupDigest, AgentUsageReport

_ACCENT = "rgb(70,190,230)"  # AGENT_USAGE_THEME.accent
_MUTED = "rgb(120,120,140)"


def _tokens(n: int) -> str:
    """Compact token counts: 1234 → '1.2k', 5_600_000 → '5.6M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def format_usage_rich(report: AgentUsageReport) -> RenderableType:
    """The agent usage report as terminal output."""
    parts: list[RenderableType] = []

    header = Text()
    header.append("Agent Usage  ", style=f"bold {_ACCENT}")
    header.append(f"{report.period_start} → {report.period_end}", style=_MUTED)
    parts.append(header)

    totals = Text()
    totals.append(f"${report.total_cost_usd:,.2f}", style="bold white")
    totals.append(
        f" estimated across {report.session_count} session(s) — "
        f"{_tokens(report.total_input_tokens)} in / {_tokens(report.total_output_tokens)} out, "
        f"cache {_tokens(report.total_cache_read_tokens)} read / {_tokens(report.total_cache_write_tokens)} written",
        style=_MUTED,
    )
    parts.append(totals)
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


_STANDUP_ACCENT = "rgb(120,210,170)"  # AGENT_STANDUP_THEME.accent


def format_standup_rich(digest: AgentStandupDigest) -> RenderableType:
    """The agent standup digest as terminal output."""
    parts: list[RenderableType] = []
    header = Text()
    header.append("Agent Standup  ", style=f"bold {_STANDUP_ACCENT}")
    header.append(f"{digest.window_start} → {digest.window_end}", style=_MUTED)
    parts.append(header)

    totals = Text()
    totals.append(f"{digest.sessions_worked} session(s)", style="bold white")
    totals.append(f" — ${digest.total_cost_usd:,.2f} estimated", style=_MUTED)
    if digest.agents_seen:
        totals.append(f" · {', '.join(digest.agents_seen)}", style=_MUTED)
    parts.append(totals)

    if digest.narrative:
        parts.append(Text(""))
        parts.append(Text(digest.narrative, style="white"))

    for title, items in (
        ("Highlights", digest.highlights),
        ("In flight", digest.in_flight),
        ("Needs a human", digest.attention_items),
    ):
        if not items:
            continue
        parts.append(Text(title, style=f"bold {_STANDUP_ACCENT}"))
        parts.extend(Text(f"  • {item}") for item in items)

    if digest.session_summaries:
        table = Table(
            title="Local sessions",
            title_style=f"bold {_STANDUP_ACCENT}",
            header_style=_MUTED,
            border_style="rgb(50,60,80)",
        )
        table.add_column("project")
        table.add_column("source")
        table.add_column("models")
        table.add_column("turns", justify="right")
        table.add_column("cost", justify="right")
        for s in digest.session_summaries:
            table.add_row(s.project, s.source, ", ".join(s.models), str(s.turns), f"${s.cost_usd:,.2f}")
        parts.append(table)

    if digest.repo_activity:
        table = Table(
            title="Agent-authored tracker activity",
            title_style=f"bold {_STANDUP_ACCENT}",
            header_style=_MUTED,
            border_style="rgb(50,60,80)",
        )
        table.add_column("kind")
        table.add_column("title")
        table.add_column("repo")
        table.add_column("agent")
        for r in digest.repo_activity:
            kind = f"{r.kind} ({r.status})" if r.status else r.kind
            table.add_row(kind, r.title, r.repo, r.agent_marker)
        parts.append(table)

    for note in digest.coverage_notes:
        parts.append(Text(f"◦ {note}", style=_MUTED))
    for warning in digest.warnings:
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


def format_security_rich(report: AgentSecurityReport) -> RenderableType:
    """The agent security report as terminal output."""
    parts: list[RenderableType] = []
    header = Text()
    header.append("Agent Security  ", style=f"bold {_SECURITY_ACCENT}")
    header.append(f"scanned {report.scan_date}", style=_MUTED)
    parts.append(header)

    posture = Text()
    posture.append("Posture: ")
    posture.append(report.posture, style=_POSTURE_STYLE.get(report.posture, "bold white"))
    posture.append(
        f" — {report.sessions_scanned} session(s), {len(report.mcp_servers)} MCP server(s), "
        f"{report.secrets_found} secret signal(s)",
        style=_MUTED,
    )
    parts.append(posture)

    if report.summary:
        parts.append(Text(""))
        parts.append(Text(report.summary, style="white"))

    if report.findings:
        table = Table(
            title="Findings", title_style=f"bold {_SECURITY_ACCENT}", header_style=_MUTED, border_style="rgb(50,60,80)"
        )
        table.add_column("severity")
        table.add_column("finding")
        table.add_column("where")
        for f in report.findings:
            where = f"{f.location}:{f.line_no}" if f.line_no else f.location
            # The title is per-category, so every stored secret signal shares
            # one. The pattern is the detector that actually fired and is the
            # only part a reader can act on — show it, and it is a label, never
            # the matched text.
            what = Text(f.title)
            if f.pattern:
                what.append(f"  {f.pattern}", style=_MUTED)
            table.add_row(Text(f.severity, style=_SEVERITY_STYLE.get(f.severity, "")), what, where)
        parts.append(table)

    if report.mcp_servers:
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

    if report.recommendations:
        parts.append(Text("Recommendations", style=f"bold {_SECURITY_ACCENT}"))
        parts.extend(Text(f"  • {item}") for item in report.recommendations)

    for warning in report.warnings:
        parts.append(Text(f"⚠ {warning}", style="rgb(220,180,60)"))

    return Group(*parts)
