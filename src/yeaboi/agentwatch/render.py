"""Rich rendering for agentwatch artifacts (CLI output; the TUI reuses these).

Pure formatting — no IO, no LLM. One ``format_*_rich`` per artifact kind,
returning a Rich renderable the CLI prints and the TUI embeds in its page
panel.
"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from yeaboi.agent.state import AgentUsageReport

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
