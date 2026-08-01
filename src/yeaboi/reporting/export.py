"""Export a DeliveryReport to Markdown, self-contained HTML, and a slide deck.

Mirrors the standup / retro exporters (standup/export.py, retro/export.py):
readable artifacts written under ``~/.scrum-agent/exports/reporting/<project>/`` so a
delivery report persists as a shareable document, not just in the logs. Three files
per run: a Markdown summary, a self-contained HTML report (using the shared
design system ``html_theme``), and a self-contained HTML *slide deck*
(reporting/presentation.py) for presenting to the business.

Every ticket ``title`` / ``assignee`` is external data (it came from the
tracker). The Markdown builder escapes it by hand; the HTML no longer needs to —
it carries the report as a JSON payload that ``frontend/src/export`` draws, and
a React text child cannot become markup however it is spelled. The TUI
**Export** button re-writes on demand.

# See docs: "Export Formats" — Markdown, HTML
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from yeaboi.agent.state import DeliveryReport
from yeaboi.artifacts.render import annotations_markdown, with_annotations
from yeaboi.reporting.style import DeckStyle

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


def _stem(report: DeliveryReport) -> str:
    """The filename stem every format of this report shares.

    Depends only on the report, not on the project name — that decides the
    *directory*. Shared with :func:`build_report_html` so the page's
    ``<noscript>`` note can name the Markdown file written beside it.
    """
    return f"report-{_slug(report.period_label) or 'period'}-{report.period_end or 'latest'}"


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
    if report.supporting_signals:
        from yeaboi.reporting.context import SIGNAL_KIND_LABELS, SIGNAL_SOURCE_LABELS, signals_sentence

        lines += ["### Supporting signals", ""]
        sentence = signals_sentence(report.supporting_signals)
        if sentence:
            lines += [f"_{sentence} from the same period (reference only)._", ""]
        for sig in report.supporting_signals:
            kind = SIGNAL_KIND_LABELS.get(sig.kind, sig.kind)
            source = SIGNAL_SOURCE_LABELS.get(sig.source, sig.source)
            lines.append(f"- **{kind} · {source}:** {sig.count}")
            lines += [f"  - {s}" for s in sig.samples[:3]]
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
    lines += annotations_markdown(report.annotations)
    lines += ["---", ""]
    lines += [f"🤙 _Generated by [yeaboi.ai](https://yeaboi.ai) · {datetime.now().strftime('%Y-%m-%d %H:%M')}_", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def _item_payload(item) -> dict:
    """One delivered ticket as data."""
    out = {"key": item.key, "title": item.title, "status": item.status}
    if item.assignee:
        out["assignee"] = item.assignee
    return out


def reporting_export_args(report: DeliveryReport, *, history: Sequence[dict] = ()) -> dict[str, object]:
    """Return the chrome + payload keyword arguments for one delivery report.

    See :func:`yeaboi.standup.export.standup_export_args` for why this is split
    out: an editable shared document rebuilds the payload without a page.
    """
    from yeaboi.html_theme import trend

    # Reporting history can be cross-session; keep the trend to this project.
    if report.project_name:
        history = [r for r in history if r.get("project_name") in ("", report.project_name)]

    args = dict(
        mode="reporting",
        title=f"{_emoji(report, 'headline')}{_title(report)}",
        wordmark="report",
        facts=[
            ("PERIOD", report.period_label or ""),
            ("DATES", f"{report.period_start} → {report.period_end}" if report.period_start else ""),
            ("DELIVERED", str(len(report.delivered_items))),
        ],
        report={
            "kind": "reporting",
            "headline": report.headline,
            "metrics": [[label, value] for label, value in report.metrics],
            "summary": report.executive_summary,
            "themes": [{"title": title, "outcomes": list(outcomes)} for title, outcomes in report.themes],
            "highlights": list(report.highlights),
            "items": [_item_payload(it) for it in report.delivered_items],
            "breakdown": [[label, count] for label, count in _delivered_counts(report)],
            # The chosen decoration per slot, not the vocabulary — the emoji a
            # host may pick from is server-validated and codegen'd; which one
            # they picked for this report is data.
            "emoji": {slot: emoji for slot, emoji in report.emoji_theme if emoji},
            "trend": trend(
                history,
                date_key="period_end",
                value_key="item_count",
                title="Delivery volume trend",
                label="Delivered items",
                cutoff_date=report.period_end,
                current=(report.period_end, len(report.delivered_items)),
            ),
            "warnings": list(report.warnings or []),
        },
        footer=f"Generated by yeaboi.ai • {datetime.now().strftime('%Y-%m-%d')}",
    )
    return with_annotations(args, report)


def build_report_html(report: DeliveryReport, *, history: Sequence[dict] = (), document_title: str = "") -> str:
    """Return the delivery report as a self-contained HTML document.

    The delivered-work breakdown renders as a theme-aware inline segment bar —
    no matplotlib PNG in the HTML path, because this page can draw one that
    recolours with the theme and prints. Only the Markdown/Notion/Confluence
    path embeds the chart image, since those destinations cannot.

    ``history`` is optional ``ReportingStore.get_history`` rows (newest-first);
    with two or more reports it powers the volume trend.
    """
    from yeaboi.html_theme import export_page

    return export_page(
        **reporting_export_args(report, history=history),  # type: ignore[arg-type]
        markdown_name=f"{_stem(report)}.md",
        document_title=document_title,
    )


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def _export_stem(report: DeliveryReport, project_name: str) -> tuple[Path, str]:
    """The export directory + filename stem shared by every reporting format."""
    from yeaboi.paths import get_reporting_export_dir

    key = _slug(project_name or report.project_name or "report")
    return get_reporting_export_dir(key), _stem(report)


def export_pptx_only(
    report: DeliveryReport, *, project_name: str = "", theme: str = "midnight", style: DeckStyle | None = None
) -> Path | None:
    """Write just the .pptx deck (the export picker's PowerPoint option).

    Returns the written path, or None when python-pptx isn't installed.
    """
    from yeaboi.reporting.pptx_export import build_report_pptx

    out_dir, stem = _export_stem(report, project_name)
    return build_report_pptx(report, out_dir / f"{stem}.pptx", theme=theme, style=style)


def export_report(
    report: DeliveryReport,
    *,
    project_name: str = "",
    theme: str = "midnight",
    history: Sequence[dict] = (),
    style: DeckStyle | None = None,
) -> dict[str, Path]:
    """Write the report as Markdown + HTML + a slide deck under the reporting export dir.

    Returns ``{"markdown": Path, "html": Path, "slides": Path}`` plus a ``"pptx"`` entry
    when python-pptx is installed (the optional ``docs`` extra). Filenames carry the
    period + end date — a re-run for the same period/day overwrites so the latest wins.
    ``style`` customizes only the presentation outputs (slide deck + .pptx); the
    Markdown/HTML report is a document, not a presentation, and stays unstyled.
    """
    from yeaboi.reporting.presentation import build_presentation_html

    out_dir, stem = _export_stem(report, project_name)
    md_path = out_dir / f"{stem}.md"
    html_path = out_dir / f"{stem}.html"
    slides_path = out_dir / f"{stem}-slides.html"
    from yeaboi.export_targets import localize_images

    md = build_report_markdown(report, charts_dir=out_dir)
    md_path.write_text(localize_images(md, out_dir), encoding="utf-8")
    # The HTML path draws its breakdown as an inline theme-aware segment bar;
    # only the Markdown/Notion/Confluence path embeds the matplotlib PNG.
    html_path.write_text(build_report_html(report, history=history), encoding="utf-8")
    slides_path.write_text(build_presentation_html(report, theme=theme, style=style), encoding="utf-8")
    paths = {"markdown": md_path, "html": html_path, "slides": slides_path}
    try:
        from yeaboi.reporting.pptx_export import build_report_pptx

        pptx_path = build_report_pptx(report, out_dir / f"{stem}.pptx", theme=theme, style=style)
        if pptx_path is not None:
            paths["pptx"] = pptx_path
    except Exception as e:  # noqa: BLE001 — the .pptx is a best-effort extra format
        logger.warning("reporting pptx export failed: %s", e)
    logger.info("Reporting exported: %s", " , ".join(str(p) for p in paths.values()))
    return paths
