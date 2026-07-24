"""Export a DeliveryReport to Markdown, self-contained HTML, and a slide deck.

Mirrors the standup / retro exporters (standup/export.py, retro/export.py):
readable artifacts written under ``~/.scrum-agent/exports/reporting/<project>/`` so a
delivery report persists as a shareable document, not just in the logs. Three files
per run: a Markdown summary, a self-contained HTML report (reusing the plan
stylesheet ``html_exporter._CSS``), and a self-contained HTML *slide deck*
(reporting/presentation.py) for presenting to the business.

Every ticket ``title`` / ``assignee`` is external data (it came from the tracker),
so the HTML escapes every field with ``html.escape`` — the same defense the other
exporters use. The TUI **Export** button re-writes on demand.

# See docs: "Export Formats" — Markdown, HTML
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from html import escape as _e
from pathlib import Path

from yeaboi.agent.state import DeliveryReport

logger = logging.getLogger(__name__)


def _slug(name: str) -> str:
    """Return a filesystem-safe slug for the export subdirectory."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40] or "report"


def _emoji(report: DeliveryReport, slot: str) -> str:
    """Return the emoji chosen for ``slot`` (with trailing space), or ''."""
    for s, e in report.emoji_theme:
        if s == slot and e:
            return f"{e} "
    return ""


def _title(report: DeliveryReport) -> str:
    proj = f" — {report.project_name}" if report.project_name else ""
    return f"Delivery Report{proj}"


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _delivered_counts(report: DeliveryReport) -> list[tuple[str, int]]:
    """Delivered-item counts for the chart: by person, else by status."""
    by_person: dict[str, int] = {}
    for it in report.delivered_items:
        by_person[it.assignee or "Unassigned"] = by_person.get(it.assignee or "Unassigned", 0) + 1
    if len(by_person) > 1 or (by_person and "Unassigned" not in by_person):
        return sorted(by_person.items(), key=lambda kv: -kv[1])
    by_status: dict[str, int] = {}
    for it in report.delivered_items:
        by_status[it.status or "Done"] = by_status.get(it.status or "Done", 0) + 1
    return sorted(by_status.items(), key=lambda kv: -kv[1])


def _delivered_chart(report: DeliveryReport, charts_dir: Path | None) -> Path | None:
    """Render the delivered-work chart PNG (optional charts extra), or None."""
    if charts_dir is None or not report.delivered_items:
        return None
    from yeaboi.charts import delivered_chart

    return delivered_chart(_delivered_counts(report), charts_dir / "delivered.png", title="Delivered items")


def build_report_markdown(report: DeliveryReport, *, charts_dir: Path | None = None) -> str:
    """Return the delivery report as a Markdown document.

    When ``charts_dir`` is set (and matplotlib is installed), a delivered-work
    chart PNG is rendered there and embedded above the item list — the
    ``![alt](path)`` line flows through file/Notion/Confluence exports via
    export_targets.
    """
    lines: list[str] = [
        f"# {_emoji(report, 'headline')}{_title(report)}",
        "",
        f"**Period:** {report.period_label}  ",
        f"**Dates:** {report.period_start} to {report.period_end}",
        "",
    ]
    if report.sprint_names:
        lines += [f"**Sprint(s):** {', '.join(report.sprint_names)}", ""]
    if report.headline:
        lines += [f"> {report.headline}", ""]
    if report.metrics:
        from yeaboi.markdown_convert import md_table_cell as _cell

        lines += [f"## {_emoji(report, 'metrics')}By the numbers", ""]
        lines += ["| Metric | Value |", "|--------|-------|"]
        lines += [f"| {_cell(label)} | **{_cell(value)}** |" for label, value in report.metrics]
        lines += [""]
    if report.executive_summary:
        lines += [f"## {_emoji(report, 'summary')}Executive summary", "", report.executive_summary, ""]
    for ttitle, outcomes in report.themes:
        lines += [f"## {_emoji(report, 'themes')}{ttitle}", ""]
        lines += [f"- {o}" for o in outcomes]
        lines += [""]
    if report.highlights:
        lines += [f"## {_emoji(report, 'highlights')}Highlights", ""]
        lines += [f"- {h}" for h in report.highlights]
        lines += [""]
    if report.delivered_items:
        from yeaboi.markdown_convert import md_table_cell as _cell

        lines += ["## Delivered items", ""]
        chart = _delivered_chart(report, charts_dir)
        if chart is not None:
            lines += [f"![Delivered items]({chart})", ""]
        lines += ["| Key | Title | Status | By |", "|-----|-------|--------|----|"]
        for it in report.delivered_items:
            who = _cell(it.assignee) if it.assignee else "—"
            lines.append(f"| `{_cell(it.key)}` | {_cell(it.title)} | {_cell(it.status)} | {who} |")
        lines += [""]
    if report.warnings:
        lines += ["## ⚠ Notices", ""]
        lines += [f"- {w}" for w in report.warnings]
        lines += [""]
    lines += ["---", ""]
    lines += [f"🤙 _Generated by [yeaboi.ai](https://yeaboi.ai) · {datetime.now().strftime('%Y-%m-%d %H:%M')}_", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def _html_list(items: tuple[str, ...]) -> str:
    return "<ul>" + "".join(f"<li>{_e(it)}</li>" for it in items) + "</ul>"


def build_report_html(report: DeliveryReport, *, chart_path: Path | None = None) -> str:
    """Return the delivery report as a self-contained HTML document (reuses plan CSS).

    ``chart_path`` (a PNG rendered by the markdown builder) is base64-embedded
    so the HTML stays offline-openable.
    """
    from yeaboi.html_exporter import _CSS, img_b64_tag

    parts: list[str] = [
        '<div class="container">',
        f"<h1>{_e(_emoji(report, 'headline'))}{_e(_title(report))}</h1>",
        (
            "<p style='color:var(--text-muted)'>"
            f"{_e(report.period_label)} &bull; {_e(report.period_start)} to {_e(report.period_end)}</p>"
        ),
    ]
    if report.headline:
        parts.append(f"<blockquote><strong>{_e(report.headline)}</strong></blockquote>")
    if report.metrics:
        cards = "".join(
            "<div style='border:1px solid var(--border,#30363d);border-radius:8px;padding:.6rem 1rem;"
            "min-width:120px'>"
            f"<div style='font-size:1.6rem;font-weight:700'>{_e(value)}</div>"
            f"<div style='color:var(--text-muted);font-size:.85rem'>{_e(label)}</div></div>"
            for label, value in report.metrics
        )
        parts.append(f"<h2>{_e(_emoji(report, 'metrics'))}By the numbers</h2>")
        parts.append(f"<div style='display:flex;flex-wrap:wrap;gap:.8rem'>{cards}</div>")
    if report.executive_summary:
        body = _e(report.executive_summary).replace("\n", "<br>")
        parts.append(f"<h2>{_e(_emoji(report, 'summary'))}Executive summary</h2><p>{body}</p>")
    for ttitle, outcomes in report.themes:
        parts.append(f"<h2>{_e(_emoji(report, 'themes'))}{_e(ttitle)}</h2>{_html_list(outcomes)}")
    if report.highlights:
        parts.append(f"<h2>{_e(_emoji(report, 'highlights'))}Highlights</h2>{_html_list(report.highlights)}")
    if report.delivered_items:
        rows = "".join(
            f"<tr><td><code>{_e(it.key)}</code></td><td>{_e(it.title)}</td>"
            f"<td>{_e(it.status)}</td><td>{_e(it.assignee)}</td></tr>"
            for it in report.delivered_items
        )
        chart_tag = img_b64_tag(chart_path, "Delivered items") if chart_path else ""
        parts.append(
            "<h2>Delivered items</h2>"
            + chart_tag
            + "<table><thead><tr><th>Key</th><th>Title</th><th>Status</th><th>Delivered by</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    if report.warnings:
        parts.append(f"<h2>⚠ Notices</h2>{_html_list(report.warnings)}")
    parts.append("</div>")
    body = "".join(parts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_e(_title(report))} — {_e(report.period_end)}</title>
  <style>{_CSS}</style>
</head>
<body>
{body}
<footer class="site-footer">
  Generated by yeaboi.ai &bull; {_e(datetime.now().strftime("%Y-%m-%d"))}
</footer>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def export_report(report: DeliveryReport, *, project_name: str = "", theme: str = "midnight") -> dict[str, Path]:
    """Write the report as Markdown + HTML + a slide deck under the reporting export dir.

    Returns ``{"markdown": Path, "html": Path, "slides": Path}``. Filenames carry the
    period + end date — a re-run for the same period/day overwrites so the latest wins.
    """
    from yeaboi.paths import get_reporting_export_dir
    from yeaboi.reporting.presentation import build_presentation_html

    key = _slug(project_name or report.project_name or "report")
    out_dir = get_reporting_export_dir(key)
    period_slug = _slug(report.period_label) or "period"
    stem = f"report-{period_slug}-{report.period_end or 'latest'}"
    md_path = out_dir / f"{stem}.md"
    html_path = out_dir / f"{stem}.html"
    slides_path = out_dir / f"{stem}-slides.html"
    from yeaboi.export_targets import localize_images

    md = build_report_markdown(report, charts_dir=out_dir)
    md_path.write_text(localize_images(md, out_dir), encoding="utf-8")
    chart_path = out_dir / "delivered.png"
    html_path.write_text(
        build_report_html(report, chart_path=chart_path if chart_path.is_file() else None), encoding="utf-8"
    )
    slides_path.write_text(build_presentation_html(report, theme=theme), encoding="utf-8")
    logger.info("Reporting exported: %s , %s , %s", md_path, html_path, slides_path)
    return {"markdown": md_path, "html": html_path, "slides": slides_path}
