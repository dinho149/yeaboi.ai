"""Export a StandupReport to Markdown and self-contained HTML.

Mirrors the plan exporters (``html_exporter.py`` / ``repl/_io.py``): readable
artifacts written under ``~/.scrum-agent/exports/standup/<project>/`` so a
standup's output persists as a shareable document, not just in the logs. Every
run (TUI, headless, or scheduled) auto-exports; the TUI **Export** button
re-writes the latest report on demand — same as the other pages.

The HTML reuses the plan report's stylesheet (``html_exporter._CSS``) so standup
pages look consistent with the rest of the app, plus a small supplementary style
for the confidence badge and notices.

# See docs: "Export Formats" — Markdown, HTML
# See docs: "Daily Standup" — exports
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from html import escape as _e
from pathlib import Path

from yeaboi.agent.state import StandupReport

logger = logging.getLogger(__name__)

# Confidence label → CSS colour + markdown marker (kept in one place).
_CONF_COLOR = {
    "On track": "#3fb950",
    "At risk": "#d29922",
    "Behind": "#f85149",
    "Insufficient data": "#8b949e",
}


def _slug(name: str) -> str:
    """Return a filesystem-safe slug for the export subdirectory."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40] or "standup"


def _sprint_line(report: StandupReport) -> str:
    if report.sprint_total_days:
        return f"{report.sprint_name or 'Sprint'} — day {report.sprint_day} of {report.sprint_total_days}"
    return report.sprint_name or "Sprint (dates unknown)"


def _confidence_text(report: StandupReport) -> str:
    label = report.confidence_label or "Unknown"
    if report.confidence_label and report.confidence_label != "Insufficient data":
        return f"{label} ({report.confidence_pct}%)"
    return label


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def build_standup_markdown(report: StandupReport) -> str:
    """Return the standup as a Markdown document."""
    lines: list[str] = [
        f"# Daily Standup — {report.date}",
        "",
        f"**Sprint:** {_sprint_line(report)}  ",
        f"**Confidence:** {_confidence_text(report)}",
    ]
    if report.confidence_rationale:
        lines.append("")
        lines.append(f"> {report.confidence_rationale}")

    if report.warnings:
        lines += ["", "## ⚠ Notices", ""]
        lines += [f"- {w}" for w in report.warnings]

    if report.team_summary:
        lines += ["", "## Team Summary", "", report.team_summary]

    lines += ["", "## Updates", ""]
    if report.member_updates:
        # A table reads better than a wall of sections — mirrors the HTML
        # export and renders as a native table on Notion/Confluence.
        from yeaboi.markdown_convert import md_table_cell as _cell

        lines += [
            "| Member | General Overview | Ticketing | Code | Documentation | Blocker |",
            "|--------|------------------|-----------|------|---------------|---------|",
        ]
        for m in report.member_updates:
            is_own = bool(m.self_report) or m.source == "self-reported"
            name = f"**{_cell(m.name)}** _(you)_" if is_own else f"**{_cell(m.name)}**"
            summary = _cell(m.summary) if m.summary else "_No activity detected._"

            def _category_cell(text: str, links) -> str:
                value = _cell(text) if text else "—"
                if links:
                    refs = " · ".join(f"[{_cell(label)}]({url})" for label, url in links)
                    value = f"{value}<br>{refs}"
                return value

            ticketing = _category_cell(m.ticketing_summary, getattr(m, "ticketing_links", ()))
            code = _category_cell(m.code_summary, getattr(m, "code_links", ()))
            documentation = _category_cell(m.documentation_summary, getattr(m, "documentation_links", ()))
            if not any(
                (
                    getattr(m, "ticketing_links", ()),
                    getattr(m, "code_links", ()),
                    getattr(m, "documentation_links", ()),
                )
            ) and getattr(m, "links", ()):
                refs = " · ".join(f"[{_cell(label)}]({url})" for label, url in m.links)
                summary = f"{summary}<br>**Links:** {refs}"
            blocker = _cell(m.blockers) if m.blockers else "—"
            lines.append(f"| {name} | {summary} | {ticketing} | {code} | {documentation} | {blocker} |")
        lines.append("")
    else:
        lines.append("_No individual updates._")
    if report.images:
        # Screenshots pasted into "My Update".
        lines += ["### Screenshots", ""]
        lines.extend(f"![Screenshot]({p})" for p in report.images)
        lines.append("")

    if report.activity_counts:
        counts = ", ".join(f"{src}: {n}" for src, n in report.activity_counts)
        window = f" ({report.activity_window})" if report.activity_window else ""
        lines += ["", "---", "", f"_Activity examined — {counts}{window}_"]
    if report.category_coverage:
        coverage = ", ".join(f"{category}: {status.replace('_', ' ')}" for category, status in report.category_coverage)
        lines += ["", f"_Coverage — {coverage}_"]
    if report.skipped_sources:
        skipped = ", ".join(f"{src} ({reason})" for src, reason in report.skipped_sources)
        lines += ["", f"_Sources skipped — {skipped}_"]

    lines += ["", f"🤙 _Generated by [yeaboi.ai](https://yeaboi.ai) · {datetime.now().strftime('%Y-%m-%d %H:%M')}_", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def build_standup_html(report: StandupReport) -> str:
    """Return the standup as a self-contained HTML document (reuses the plan CSS)."""
    from yeaboi.html_exporter import _CSS

    conf_color = _CONF_COLOR.get(report.confidence_label, "#8b949e")
    date_span = f"<span style='color:var(--text-muted);font-weight:400'>&mdash; {_e(report.date)}</span>"
    parts: list[str] = [
        '<div class="container">',
        f"<h1>Daily Standup {date_span}</h1>",
        f"<p><strong>Sprint:</strong> {_e(_sprint_line(report))}</p>",
        (
            f"<p><strong>Confidence:</strong> "
            f"<span style='color:{conf_color};font-weight:600'>{_e(_confidence_text(report))}</span></p>"
        ),
    ]
    if report.confidence_rationale:
        parts.append(f"<p style='color:var(--text-muted)'>{_e(report.confidence_rationale)}</p>")

    if report.warnings:
        items = "".join(f"<li>{_e(w)}</li>" for w in report.warnings)
        parts.append(
            "<div style='border-left:3px solid #d29922;background:rgba(210,153,34,.08);"
            "padding:.6rem 1rem;margin:1rem 0;border-radius:4px'>"
            "<strong style='color:#d29922'>⚠ Notices</strong>"
            f"<ul style='margin:.4rem 0 0'>{items}</ul></div>"
        )

    if report.team_summary:
        parts.append("<h2>Team Summary</h2>")
        parts.append(f"<p>{_e(report.team_summary)}</p>")

    parts.append("<h2>Updates</h2>")
    if report.member_updates:
        parts.append(
            "<table class='data-table'><thead><tr><th>Member</th><th>General Overview</th>"
            "<th>Ticketing</th><th>Code</th><th>Documentation</th><th>Blocker</th></tr></thead><tbody>"
        )
        for m in report.member_updates:
            tag = " <em style='color:var(--text-muted)'>(✍ own update)</em>" if m.self_report else ""
            blocker = _e(m.blockers) if m.blockers else "&mdash;"
            update_cell = _e(m.summary or "No activity detected.")
            if m.self_report:
                sr_html = _e(m.self_report).replace("\n", "<br>")
                update_cell += f"<br><em style='color:var(--text-muted)'>✍ In their words: {sr_html}</em>"
            category_links = (
                *getattr(m, "ticketing_links", ()),
                *getattr(m, "code_links", ()),
                *getattr(m, "documentation_links", ()),
            )
            if getattr(m, "links", ()) and not category_links:
                anchors = " · ".join(
                    f"<a href='{_e(url, quote=True)}' target='_blank'>{_e(label or url)}</a>" for label, url in m.links
                )
                update_cell += f"<br><span style='font-size:.85rem'>🔗 {anchors}</span>"

            def _category_html(text: str, links) -> str:
                value = _e(text or "Summary unavailable.")
                if links:
                    anchors = " · ".join(
                        f"<a href='{_e(url, quote=True)}' target='_blank'>{_e(label or url)}</a>"
                        for label, url in links
                    )
                    value += f"<br><span style='font-size:.85rem'>🔗 {anchors}</span>"
                return value

            ticketing = _category_html(m.ticketing_summary, getattr(m, "ticketing_links", ()))
            code = _category_html(m.code_summary, getattr(m, "code_links", ()))
            documentation = _category_html(m.documentation_summary, getattr(m, "documentation_links", ()))
            parts.append(
                f"<tr><td><strong>{_e(m.name)}</strong>{tag}</td><td>{update_cell}</td>"
                f"<td>{ticketing}</td><td>{code}</td><td>{documentation}</td><td>{blocker}</td></tr>"
            )
        parts.append("</tbody></table>")
    else:
        parts.append("<p style='color:var(--text-muted)'>No individual updates.</p>")

    if report.images:
        from yeaboi.html_exporter import img_b64_tag

        tags = "".join(img_b64_tag(p, "Screenshot") for p in report.images)
        if tags:
            parts.append(f"<h2>Screenshots</h2>{tags}")

    if report.activity_counts:
        counts = ", ".join(f"{_e(src)}: {n}" for src, n in report.activity_counts)
        parts.append(f"<p style='color:var(--text-muted);font-size:.85rem'>Activity examined — {counts}</p>")
    if report.category_coverage:
        coverage = ", ".join(
            f"{_e(category)}: {_e(status.replace('_', ' '))}" for category, status in report.category_coverage
        )
        parts.append(f"<p style='color:var(--text-muted);font-size:.85rem'>Coverage — {coverage}</p>")
    if report.skipped_sources:
        skipped = ", ".join(f"{_e(src)} ({_e(reason)})" for src, reason in report.skipped_sources)
        parts.append(f"<p style='color:var(--text-muted);font-size:.85rem'>Sources skipped — {skipped}</p>")

    parts.append("</div>")
    body = "".join(parts)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Daily Standup — {_e(report.date)}</title>
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


def export_standup(report: StandupReport, *, project_name: str = "") -> dict[str, Path]:
    """Write the standup as Markdown + HTML under the standup export dir.

    Returns ``{"markdown": Path, "html": Path}``. One file per day (dated
    filename) — a re-run the same day overwrites so the latest wins. Best-effort:
    on any I/O error the exception propagates to the caller, which logs it.
    """
    from yeaboi.paths import get_standup_export_dir

    key = _slug(project_name or report.session_id)
    out_dir = get_standup_export_dir(key)
    stem = f"standup-{report.date or 'latest'}"
    md_path = out_dir / f"{stem}.md"
    html_path = out_dir / f"{stem}.html"
    from yeaboi.export_targets import localize_images

    # Screenshots are copied next to the .md so the export folder is portable.
    md_path.write_text(localize_images(build_standup_markdown(report), out_dir), encoding="utf-8")
    html_path.write_text(build_standup_html(report), encoding="utf-8")
    logger.info("Standup exported: %s , %s", md_path, html_path)
    return {"markdown": md_path, "html": html_path}
