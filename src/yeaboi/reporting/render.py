"""Rendering for the DeliveryReport — one source of truth for the TUI + plaintext.

The plaintext lines feed the TUI Reporting detail view (styled there); the Rich
form is available for any console surface. Keeping both here means the report looks
consistent everywhere and no surface re-implements the layout (mirrors
performance/render.py).

# See docs: "Reporting Mode" — TUI page
"""

from __future__ import annotations

import logging

from rich.console import Group
from rich.text import Text

from yeaboi.agent.state import DeliveryReport

logger = logging.getLogger(__name__)

_ACCENT = "rgb(140,120,230)"  # Reporting theme indigo — keep in sync with the TUI theme


def _emoji(report: DeliveryReport, slot: str) -> str:
    """Return the emoji chosen for ``slot`` (with a trailing space), or ''."""
    for s, e in report.emoji_theme:
        if s == slot and e:
            return f"{e} "
    return ""


def _metrics_line(report: DeliveryReport) -> str:
    return "   ".join(f"{label}: {value}" for label, value in report.metrics)


# ---------------------------------------------------------------------------
# Plaintext
# ---------------------------------------------------------------------------


def format_report_lines(report: DeliveryReport) -> list[str]:
    """Return the delivery report as plain-text lines (no ANSI)."""
    logger.info(
        "reporting render: plaintext report — period=%s, %d item(s)",
        report.period_label,
        len(report.delivered_items),
    )
    title = report.project_name or "Delivery Report"
    lines = [
        f"{_emoji(report, 'headline')}Delivery Report — {title}",
        f"{report.period_label}  ·  {report.period_start} to {report.period_end}",
        "",
    ]
    if report.headline:
        lines += [report.headline, ""]
    if report.metrics:
        lines += [f"{_emoji(report, 'metrics')}By the numbers:", f"  {_metrics_line(report)}", ""]
    if report.executive_summary:
        lines += [f"{_emoji(report, 'summary')}Executive summary:"]
        lines += [f"  {report.executive_summary}", ""]
    for ttitle, outcomes in report.themes:
        lines += [f"{_emoji(report, 'themes')}{ttitle}:"]
        lines += [f"  • {o}" for o in outcomes]
        lines += [""]
    if report.highlights:
        lines += [f"{_emoji(report, 'highlights')}Highlights:"]
        lines += [f"  • {h}" for h in report.highlights]
        lines += [""]
    if report.warnings:
        lines += ["⚠ Notices:"]
        lines += [f"  • {w}" for w in report.warnings]
        lines += [""]
    return lines


# ---------------------------------------------------------------------------
# Rich
# ---------------------------------------------------------------------------


def format_report_rich(report: DeliveryReport, *, accent: str = _ACCENT) -> Group:
    """Return a Rich renderable for the delivery report."""
    logger.info(
        "reporting render: rich report — period=%s, %d item(s)",
        report.period_label,
        len(report.delivered_items),
    )
    title = report.project_name or "Delivery Report"
    body: list[Text] = [
        Text(f"{_emoji(report, 'headline')}Delivery Report — {title}", style=f"bold {accent}"),
        Text(f"{report.period_label} · {report.period_start} to {report.period_end}", style="dim"),
        Text(""),
    ]
    if report.headline:
        body.append(Text(report.headline, style="bold"))
        body.append(Text(""))
    if report.metrics:
        body.append(Text(f"{_emoji(report, 'metrics')}By the numbers", style=f"bold {accent}"))
        body.append(Text(f"  {_metrics_line(report)}"))
        body.append(Text(""))
    if report.executive_summary:
        body.append(Text(f"{_emoji(report, 'summary')}Executive summary", style=f"bold {accent}"))
        body.append(Text(f"  {report.executive_summary}"))
        body.append(Text(""))
    for ttitle, outcomes in report.themes:
        _rich_section(body, f"{_emoji(report, 'themes')}{ttitle}", outcomes, accent)
    _rich_section(body, f"{_emoji(report, 'highlights')}Highlights", report.highlights, accent)
    if report.warnings:
        body.append(Text("⚠ Notices", style="bold rgb(220,180,60)"))
        for w in report.warnings:
            body.append(Text(f"  - {w}", style="rgb(220,180,60)"))
        body.append(Text(""))
    return Group(*body)


def _rich_section(body: list[Text], title: str, items: tuple[str, ...], accent: str) -> None:
    if not items:
        return
    body.append(Text(title, style=f"bold {accent}"))
    for it in items:
        if not it:
            continue
        row = Text()
        row.append("  • ", style="dim")
        row.append(it)
        body.append(row)
    body.append(Text(""))
