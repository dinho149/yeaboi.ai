"""Export a StandupReport to Markdown and self-contained HTML.

Mirrors the plan exporters (``html_exporter.py`` / ``repl/_io.py``): readable
artifacts written under ``~/.scrum-agent/exports/standup/<project>/`` so a
standup's output persists as a shareable document, not just in the logs. Every
run (TUI, headless, or scheduled) auto-exports; the TUI **Export** button
re-writes the latest report on demand — same as the other pages.

Layout: an Overview stat strip (sprint day, confidence, members, activity),
the team summary as a scannable bullet list (member names bolded), and one
card per member with Ticketing / Code / Documentation lists. Ticket keys
mentioned in prose (e.g. "PSOT-12") become inline links — the key text is the
anchor — instead of separate link lines; see ``_ticket_key_map``.

# See docs: "Export Formats" — Markdown, HTML
# See docs: "Daily Standup" — exports
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from yeaboi.agent.state import MemberUpdate, StandupReport
from yeaboi.html_theme import escape as _e
from yeaboi.html_theme import prose_bullets as _summary_bullets
from yeaboi.html_theme import split_sentences as _split_sentences

logger = logging.getLogger(__name__)

# Confidence label → semantic CSS token (resolves per theme in the shared stylesheet).
_CONF_COLOR = {
    "On track": "var(--ok)",
    "At risk": "var(--warn)",
    "Behind": "var(--danger)",
    "Insufficient data": "var(--low)",
}

# Confidence label → chip kind (shared .badge-* classes).
_CONF_CHIP_KIND = {
    "On track": "ok",
    "At risk": "warn",
    "Behind": "danger",
    "Insufficient data": "low",
}

# category_coverage status → status-dot token (status word always kept as text
# beside the dot — never color-alone).
_COVERAGE_DOT = {
    "covered": "--ok",
    "partial": "--warn",
    "failed": "--danger",
    "not_configured": "--low",
}

_SPARKLINE_MAX_POINTS = 14

# Jira-style ticket keys ("PSOT-12"). AzDO work items ("#1234" / "AB#1234")
# deliberately don't match — their URLs only ever arrive via the *_links tuples.
_TICKET_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")


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


def _trend_text(report: StandupReport) -> str:
    """Day-over-day movement, e.g. "▲ +6 vs last standup" — empty when steady/no history."""
    trend = getattr(report, "confidence_trend", "")
    delta = getattr(report, "confidence_delta", 0)
    if trend == "improving":
        return f"▲ +{delta} vs last standup"
    if trend == "declining":
        return f"▼ {abs(delta)} vs last standup"
    return ""


# ---------------------------------------------------------------------------
# Ticket linkification
# ---------------------------------------------------------------------------


def _member_prose(m: MemberUpdate) -> tuple[str, ...]:
    return (
        m.summary or "",
        getattr(m, "ticketing_summary", "") or "",
        getattr(m, "code_summary", "") or "",
        getattr(m, "documentation_summary", "") or "",
        m.blockers or "",
        getattr(m, "progress_note", "") or "",
        getattr(m, "outlook", "") or "",
        m.self_report or "",
    )


def _member_link_groups(m: MemberUpdate) -> tuple[tuple[tuple[str, str], ...], ...]:
    return (
        getattr(m, "links", ()) or (),
        getattr(m, "ticketing_links", ()) or (),
        getattr(m, "code_links", ()) or (),
        getattr(m, "documentation_links", ()) or (),
    )


def _ticket_key_map(report: StandupReport) -> dict[str, str]:
    """Map ticket keys ("PSOT-12") to URLs so prose mentions can become inline links.

    Two passes:
    1. Evidence: link labels that *are* ticket keys (built from real activity
       URLs in ``engine._member_links``) — always trusted.
    2. Prefix-gated fallback: bare keys found in prose but absent from pass 1
       get a ``<jira base>/browse/<key>`` URL, but only when a Jira base URL is
       configured AND the key's project prefix was seen in pass 1. The gate
       stops false positives like UTF-8 / SHA-256 / ISO-8601 (which match the
       key regex) from turning into dead links.
    """
    key_map: dict[str, str] = {}
    for m in report.member_updates:
        for links in _member_link_groups(m):
            for label, url in links:
                key = (label or "").strip()
                if url and key not in key_map and _TICKET_KEY_RE.fullmatch(key):
                    key_map[key] = url
    if not key_map:
        return key_map  # no evidence → no guessing

    from yeaboi.config import get_jira_base_url

    base = get_jira_base_url()
    if not base:
        return key_map
    known_prefixes = {k.split("-")[0] for k in key_map}
    prose: list[str] = [report.team_summary or "", report.confidence_rationale or ""]
    for m in report.member_updates:
        prose.extend(_member_prose(m))
    for text in prose:
        for key in _TICKET_KEY_RE.findall(text):
            if key not in key_map and key.split("-")[0] in known_prefixes:
                key_map[key] = f"{base.rstrip('/')}/browse/{key}"
    return key_map


def _anchor(label: str, url: str) -> str:
    return f"<a href='{_e(url, quote=True)}' target='_blank' rel='noopener'>{_e(label or url)}</a>"


def _linkify_escaped(escaped: str, key_map: dict[str, str]) -> str:
    """Substitute mapped ticket keys in *already HTML-escaped* text with anchors."""

    def repl(match: re.Match[str]) -> str:
        url = key_map.get(match.group(0))
        if not url:
            return match.group(0)
        return f"<a href='{_e(url, quote=True)}' target='_blank' rel='noopener'>{match.group(0)}</a>"

    return _TICKET_KEY_RE.sub(repl, escaped)


def _linkify(text: str, key_map: dict[str, str]) -> str:
    """HTML-escape ``text`` then turn mapped ticket keys into inline anchors.

    Safe by construction: escaping happens exactly once, *before* substitution;
    the entities escaping produces (&lt; &amp; …) contain no UPPERCASE-digits
    run, so the key regex can never match inside them, and matched keys are
    ``[A-Z0-9-]`` only — safe to embed verbatim as anchor text.
    """
    return _linkify_escaped(_e(text), key_map)


def _linkify_md(text: str, key_map: dict[str, str]) -> str:
    """Markdown flavor: mapped ticket keys become ``[KEY](url)``."""

    def repl(match: re.Match[str]) -> str:
        url = key_map.get(match.group(0))
        return f"[{match.group(0)}]({url})" if url else match.group(0)

    return _TICKET_KEY_RE.sub(repl, text)


def _leftover_links(text: str, links: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    """Links that still need explicit anchors: label not ticket-shaped, or key absent from the prose."""
    out: list[tuple[str, str]] = []
    for label, url in links or ():
        key = (label or "").strip()
        if _TICKET_KEY_RE.fullmatch(key) and re.search(rf"\b{re.escape(key)}\b", text or ""):
            continue  # already an inline anchor in the prose
        out.append((label, url))
    return out


# ---------------------------------------------------------------------------
# Team summary highlighting
# ---------------------------------------------------------------------------


# Sentence/bullet splitting moved to the shared design system —
# yeaboi.html_theme.split_sentences / prose_bullets (imported above) so every
# mode's export fragments prose the same way.


def _name_variants(member_names: Sequence[str]) -> list[str]:
    """Full names plus first names (LLM prose says "Alice" for "Alice Smith"), longest first."""
    variants: list[str] = []
    for name in member_names:
        name = (name or "").strip()
        if not name:
            continue
        variants.append(name)
        first = name.split()[0]
        if len(first) >= 3 and first != name:
            variants.append(first)
    # Longest first + single-pass alternation → "Alice Smith" wins over "Alice", no nested bolding.
    return sorted(set(variants), key=len, reverse=True)


def _name_pattern(variants: Sequence[str], *, html: bool) -> re.Pattern[str] | None:
    if not variants:
        return None
    alts = []
    for v in variants:
        literal = re.escape(_e(v) if html else v)
        pre = r"\b" if v[:1].isalnum() else ""
        post = r"\b" if v[-1:].isalnum() else ""
        alts.append(f"{pre}{literal}{post}")
    return re.compile("|".join(alts))


def _team_summary_html(text: str, key_map: dict[str, str], member_names: Sequence[str]) -> str:
    """Render the LLM team summary as a scannable bullet list with bolded names + inline ticket links.

    Deterministic post-processing only — no prompt/schema change, so it also
    improves historical reports and the no-LLM fallback path. (A structured
    ``highlights`` field in the summary prompt is a possible future upgrade.)
    Names are bolded *before* anchors are inserted, so a name can never match
    inside an href.
    """
    pattern = _name_pattern(_name_variants(member_names), html=True)
    items: list[str] = []
    for sentence in _split_sentences(text):
        escaped = _e(sentence)
        if pattern:
            escaped = pattern.sub(lambda m: f"<strong>{m.group(0)}</strong>", escaped)
        items.append(_linkify_escaped(escaped, key_map))
    if not items:
        return ""
    if len(items) == 1:
        return f"<div class='card'><p>{items[0]}</p></div>"
    lis = "".join(f"<li>{item}</li>" for item in items)
    return f"<div class='card'><ul>{lis}</ul></div>"


def _team_summary_md_lines(text: str, key_map: dict[str, str], member_names: Sequence[str]) -> list[str]:
    """Markdown flavor of the summary bullets: ``**Name**`` bolding + ``[KEY](url)`` links."""
    pattern = _name_pattern(_name_variants(member_names), html=False)
    items: list[str] = []
    for sentence in _split_sentences(text):
        if pattern:
            sentence = pattern.sub(lambda m: f"**{m.group(0)}**", sentence)
        items.append(_linkify_md(sentence, key_map))
    if len(items) == 1:
        return items
    return [f"- {item}" for item in items]


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def build_standup_markdown(report: StandupReport) -> str:
    """Return the standup as a Markdown document (per-member sections, inline ticket links)."""
    key_map = _ticket_key_map(report)
    member_names = [m.name for m in report.member_updates]
    lines: list[str] = [
        f"# Daily Standup — {report.date}",
        "",
        f"**Sprint:** {_sprint_line(report)}  ",
        f"**Confidence:** {_confidence_text(report)}" + (f" ({_trend_text(report)})" if _trend_text(report) else ""),
    ]
    if report.confidence_rationale:
        lines.append("")
        lines.append(f"> {report.confidence_rationale}")

    if report.warnings:
        lines += ["", "## ⚠ Notices", ""]
        lines += [f"- {w}" for w in report.warnings]

    if report.team_summary:
        lines += ["", "## Team Summary", ""]
        lines += _team_summary_md_lines(report.team_summary, key_map, member_names)

    lines += ["", "## Updates", ""]
    if report.member_updates:
        # One section per member — labeled bullet lists read far better than the
        # old six-column table, which wrapped into tall rows on Notion/Confluence.
        for m in report.member_updates:
            is_own = bool(m.self_report) or m.source == "self-reported"
            lines.append(f"### {m.name} (you)" if is_own else f"### {m.name}")
            lines.append("")
            overview = _linkify_md(m.summary, key_map) if m.summary else "_No activity detected._"
            lines.append(overview)
            lines.append("")
            if getattr(m, "progress_note", ""):
                lines.append(f"- **Since last standup:** {_linkify_md(m.progress_note, key_map)}")

            def _refs(pairs: Sequence[tuple[str, str]]) -> str:
                return " · ".join(f"[{label or url}]({url})" for label, url in pairs)

            bullets: list[str] = []
            for label, text, links in (
                ("Ticketing", getattr(m, "ticketing_summary", ""), getattr(m, "ticketing_links", ())),
                ("Code", getattr(m, "code_summary", ""), getattr(m, "code_links", ())),
                ("Docs", getattr(m, "documentation_summary", ""), getattr(m, "documentation_links", ())),
            ):
                if not text and not links:
                    continue
                value = _linkify_md(text, key_map) if text else ""
                leftovers = _leftover_links(text, links)
                if leftovers:
                    value = f"{value} — {_refs(leftovers)}" if value else _refs(leftovers)
                bullets.append(f"- **{label}:** {value}")
            if getattr(m, "outlook", ""):
                bullets.append(f"- **Outlook:** {_linkify_md(m.outlook, key_map)}")
            if m.blockers:
                bullets.append(f"- **Blocker:** {_linkify_md(m.blockers, key_map)}")
            # Legacy reports carry only the general links tuple.
            category_links = (
                *(getattr(m, "ticketing_links", ()) or ()),
                *(getattr(m, "code_links", ()) or ()),
                *(getattr(m, "documentation_links", ()) or ()),
            )
            if getattr(m, "links", ()) and not category_links:
                leftovers = _leftover_links(m.summary, m.links)
                if leftovers:
                    bullets.append(f"- **Links:** {_refs(leftovers)}")
            lines += bullets
            if m.self_report:
                if bullets:
                    lines.append("")
                quoted = m.self_report.replace("\n", "\n> ")
                lines.append(f"> ✍ {quoted}")
            lines.append("")
    else:
        lines.append("_No individual updates._")
    if report.images:
        # Screenshots pasted into "My Update".
        lines += ["## Screenshots", ""]
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


def _history_points(report: StandupReport, history: Sequence[dict]) -> list[tuple[str, int]]:
    """Normalize store history rows into (standup_date, confidence_pct), oldest → newest.

    Thin wrapper over the shared ``history_series`` with standup's field names,
    clipped to the report's own date so re-exporting an old run never shows its
    future.
    """
    from yeaboi.html_theme import history_series

    points = history_series(
        history,
        date_key="standup_date",
        value_key="confidence_pct",
        status_key="status",
        cutoff_date=report.date,
        current=(report.date, report.confidence_pct),
        max_points=_SPARKLINE_MAX_POINTS,
    )
    return [(day, int(value)) for day, value in points]


def _confidence_sparkline(report: StandupReport, history: Sequence[dict]) -> str:
    """Confidence-over-time trend card, or "" when there is no trend to show."""
    from yeaboi.html_theme import sparkline_card

    points = _history_points(report, history)
    # _CONF_COLOR stores full "var(--ok)" strings; the SVG helper wants bare tokens.
    end_token = _CONF_COLOR.get(report.confidence_label, "var(--low)")[4:-1]
    return sparkline_card(
        points,
        title="Confidence trend",
        end_color_var=end_token,
        floor=0,
        ceiling=100,
        svg_title=f"Confidence trend — last {len(points)} standups",
    )


def _team_activity_block(report: StandupReport) -> str:
    """Comparable per-member stacked activity bars (tickets/code/docs), or ""."""
    from yeaboi.html_theme import legend, segment_bar

    rows: list[tuple[str, int, int, int, int]] = []
    for m in report.member_updates:
        t = getattr(m, "ticketing_activity_count", 0)
        c = getattr(m, "code_activity_count", 0)
        d = getattr(m, "documentation_activity_count", 0)
        total = t + c + d
        if total > 0:
            rows.append((m.name, t, c, d, total))
    if not rows:
        return ""
    team_max = max(total for *_, total in rows)
    bars = "".join(
        "<div class='bar-row'>"
        f"<span class='bar-name'>{_e(name)}</span>"
        + segment_bar(
            [(t, "--accent"), (c, "--accent2"), (d, "--info")],
            title=f"{name}: {total} activity item(s)",
            width_pct=total / team_max * 100,
        )
        + f"<span class='bar-total'>{total}</span></div>"
        for name, t, c, d, total in rows
    )
    key = legend([("Tickets", "--accent"), ("Code", "--accent2"), ("Docs", "--info")])
    return f"<div class='card'><div class='card-title' style='margin-bottom:.3rem'>Team activity</div>{key}{bars}</div>"


def _source_bar(report: StandupReport) -> str:
    """Activity-by-source segmented bar with a counted legend, or ""."""
    from yeaboi.html_theme import counted_segment_bar

    block = counted_segment_bar(report.activity_counts, title="Activity by source")
    return f"<div style='margin-bottom:.6rem'>{block}</div>" if block else ""


def _category_block(title: str, summary: str, links: Sequence[tuple[str, str]], key_map: dict[str, str]) -> str:
    """One labeled list inside the member card's Ticketing / Code / Documentation grid.

    Returns "" when there is nothing to show — the caller collapses empty
    categories into a footnote so the grid redistributes the freed width.
    """
    items = [f"<li>{_linkify(fragment, key_map)}</li>" for fragment in _summary_bullets(summary)]
    leftovers = _leftover_links(summary, links)
    chips = ""
    if leftovers:
        chips = (
            "<div class='chip-row'>"
            + "".join(
                f"<a class='badge' href='{_e(url, quote=True)}' target='_blank' rel='noopener'>{_e(label or url)}</a>"
                for label, url in leftovers
            )
            + "</div>"
        )
    if not items and not chips:
        return ""
    return f"<div class='analysis-section'><h3>{_e(title)}</h3><ul>{''.join(items)}</ul>{chips}</div>"


def _member_card(m: MemberUpdate, key_map: dict[str, str]) -> str:
    """One card per member: avatar + overview paragraph, category lists, blocker callout, self-report quote."""
    from yeaboi.html_theme import avatar, chip

    is_own = bool(m.self_report) or m.source == "self-reported"
    title = f"<strong>{_e(m.name)}</strong>"
    if is_own:
        title += f" {chip('you', 'accent')}"
    title = (
        f"<span style='display:inline-flex;align-items:center;gap:.55rem'>{avatar(m.name)}<span>{title}</span></span>"
    )

    meta_chips: list[str] = []
    for count, singular, plural in (
        (getattr(m, "ticketing_activity_count", 0), "ticket", "tickets"),
        (getattr(m, "code_activity_count", 0), "code", "code"),
        (getattr(m, "documentation_activity_count", 0), "doc", "docs"),
    ):
        if count:
            meta_chips.append(chip(f"{count} {singular if count == 1 else plural}"))
    if m.blockers:
        meta_chips.append(chip("blocked", "danger"))
    meta_html = f"<div class='card-meta'>{''.join(meta_chips)}</div>" if meta_chips else ""

    parts = [
        f"<div class='card-header'><div class='card-title'>{title}</div>{meta_html}</div>",
        f"<p>{_linkify(m.summary or 'No activity detected.', key_map)}</p>",
    ]
    if getattr(m, "progress_note", ""):
        parts.append(
            f"<p style='color:var(--muted);font-size:.875rem'>↺ <em>Since last standup:</em> "
            f"{_linkify(m.progress_note, key_map)}</p>"
        )
    # Legacy reports carry only the general links tuple — surface what isn't already inline.
    category_links = (
        *(getattr(m, "ticketing_links", ()) or ()),
        *(getattr(m, "code_links", ()) or ()),
        *(getattr(m, "documentation_links", ()) or ()),
    )
    if getattr(m, "links", ()) and not category_links:
        leftovers = _leftover_links(m.summary, m.links)
        if leftovers:
            anchors = " · ".join(_anchor(label, url) for label, url in leftovers)
            parts.append(f"<p style='font-size:.85rem'>{anchors}</p>")
    # Adaptive grid: only categories with real activity get a column (count > 0
    # or evidence links), so a quiet category never squeezes the busy ones into
    # narrow strips. Empty categories collapse into muted footnote lines that
    # KEEP the coverage-aware wording — "source not configured" must stay
    # distinguishable from "no activity detected".
    sections: list[str] = []
    footnotes: list[str] = []
    for label, summary, links, count in (
        (
            "Ticketing",
            getattr(m, "ticketing_summary", ""),
            getattr(m, "ticketing_links", ()),
            getattr(m, "ticketing_activity_count", 0),
        ),
        ("Code", getattr(m, "code_summary", ""), getattr(m, "code_links", ()), getattr(m, "code_activity_count", 0)),
        (
            "Documentation",
            getattr(m, "documentation_summary", ""),
            getattr(m, "documentation_links", ()),
            getattr(m, "documentation_activity_count", 0),
        ),
    ):
        block = _category_block(label, summary, links, key_map) if (count or links) else ""
        if block:
            sections.append(block)
        elif summary:
            footnotes.append(f"<p class='card-footnote'>{_e(label)} — {_linkify(summary, key_map)}</p>")
    if sections:
        parts.append(f"<div class='analysis-grid member-grid' style='margin-top:.6rem'>{''.join(sections)}</div>")
    parts.extend(footnotes)
    if getattr(m, "outlook", ""):
        parts.append(
            f"<p style='margin-top:.6rem;font-size:.875rem'><span class='badge'>Outlook</span> "
            f"{_linkify(m.outlook, key_map)}</p>"
        )
    if m.blockers:
        parts.append(
            f"<p style='margin-top:.6rem'><span class='badge badge-danger'>Blocker</span> "
            f"{_linkify(m.blockers, key_map)}</p>"
        )
    if m.self_report:
        # Linkify the verbatim quote too — people often type bare ticket keys.
        sr_html = _linkify(m.self_report, key_map).replace("\n", "<br>")
        parts.append(f"<p class='quote'>✍ {sr_html}</p>")

    classes = "card story-card critical" if m.blockers else "card story-card"
    return f"<div class='{classes}'>{''.join(parts)}</div>"


def build_standup_html(report: StandupReport, *, history: Sequence[dict] = ()) -> str:
    """Return the standup as a self-contained HTML document (shared design system).

    ``history`` is optional ``StandupStore.get_history`` rows (newest-first);
    with two or more usable points it powers the confidence-trend sparkline.
    All visuals are inline SVG/CSS on theme tokens — no external resources.
    """
    from yeaboi.html_theme import chip, html_page, notice_block, section, stat_bar, stat_tile

    key_map = _ticket_key_map(report)
    conf_color = _CONF_COLOR.get(report.confidence_label, "var(--low)")

    # Overview — stat tiles + confidence chip + trend + team activity + notices.
    if report.confidence_label and report.confidence_label != "Insufficient data":
        conf_num = f"{report.confidence_pct}%"
    else:
        conf_num = "—"
    if report.sprint_total_days:
        sprint_pct = report.sprint_day / report.sprint_total_days * 100
        sprint_tile = (
            f"<div class='stat'><div class='num'>{report.sprint_day} / {report.sprint_total_days}</div>"
            f"<div class='lbl'>Sprint day</div>{stat_bar(sprint_pct)}</div>"
        )
    else:
        sprint_tile = stat_tile(report.sprint_name or "—", "Sprint")
    tiles = [
        sprint_tile,
        # Hand-built tile: stat_tile can't color the number by confidence.
        f"<div class='stat'><div class='num' style='color:{conf_color}'>{_e(conf_num)}</div>"
        f"<div class='lbl'>Confidence</div></div>",
        stat_tile(str(len(report.member_updates)), "Members"),
    ]
    if report.activity_counts:
        tiles.append(stat_tile(str(sum(n for _, n in report.activity_counts)), "Activity items"))
    overview = [f"<div class='stat-grid'>{''.join(tiles)}</div>"]
    conf_line = f"<p>{chip(_confidence_text(report), _CONF_CHIP_KIND.get(report.confidence_label, 'low'))}"
    trend_text = _trend_text(report)
    if trend_text:
        trend_kind = "ok" if report.confidence_trend == "improving" else "danger"
        conf_line += f" {chip(trend_text, trend_kind)}"
    if report.confidence_rationale:
        conf_line += f" <span style='color:var(--muted);font-size:.875rem'>{_e(report.confidence_rationale)}</span>"
    overview.append(conf_line + "</p>")
    overview.append(_confidence_sparkline(report, history))
    overview.append(_team_activity_block(report))
    overview.append(notice_block("Notices", report.warnings or []))
    parts: list[str] = [section("overview", "Overview", "".join(overview))]

    if report.team_summary:
        names = [m.name for m in report.member_updates]
        parts.append(section("summary", "Team Summary", _team_summary_html(report.team_summary, key_map, names)))

    if report.member_updates:
        cards = "".join(_member_card(m, key_map) for m in report.member_updates)
    else:
        cards = "<p style='color:var(--muted)'>No individual updates.</p>"
    parts.append(section("updates", "Updates", cards))

    has_screenshots = False
    if report.images:
        from yeaboi.html_exporter import img_b64_tag

        tags = "".join(img_b64_tag(p, "Screenshot") for p in report.images)
        if tags:
            parts.append(section("screenshots", "Screenshots", tags))
            has_screenshots = True

    detail_items: list[str] = []
    if report.activity_counts:
        counts = ", ".join(f"{_e(src)}: {n}" for src, n in report.activity_counts)
        window = f" ({_e(report.activity_window)})" if report.activity_window else ""
        detail_items.append(f"<li>Activity examined — {counts}{window}</li>")
    if report.category_coverage:
        coverage = " &nbsp; ".join(
            f"<span class='dot' style='background:var({_COVERAGE_DOT.get(status, '--low')})'></span>"
            f"{_e(category)} <span style='color:var(--muted)'>{_e(status.replace('_', ' '))}</span>"
            for category, status in report.category_coverage
        )
        detail_items.append(f"<li>Coverage — {coverage}</li>")
    if report.skipped_sources:
        skipped = ", ".join(f"{_e(src)} ({_e(reason)})" for src, reason in report.skipped_sources)
        detail_items.append(f"<li>Sources skipped — {skipped}</li>")
    source_bar = _source_bar(report)
    if detail_items or source_bar:
        details = source_bar + f"<ul class='ac-list'>{''.join(detail_items)}</ul>"
        parts.append(section("details", "Details", details))

    nav: list[tuple[str, str]] = [("overview", "Overview")]
    if report.team_summary:
        nav.append(("summary", "Team Summary"))
    nav.append(("updates", "Updates"))
    if has_screenshots:
        nav.append(("screenshots", "Screenshots"))
    if detail_items or source_bar:
        nav.append(("details", "Details"))

    meta = [_sprint_line(report)]
    if report.activity_window:
        meta.append(report.activity_window)
    n = len(report.member_updates)
    meta.append(f"{n} member{'s' if n != 1 else ''}")

    return html_page(
        title=f"Daily Standup — {report.date}",
        heading="Daily Standup",
        subtitle=report.date,
        meta=meta,
        nav=nav,
        body="".join(parts),
        footer_note=f"Generated by yeaboi.ai • {datetime.now().strftime('%Y-%m-%d')}",
    )


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------


def export_standup(report: StandupReport, *, project_name: str = "", history: Sequence[dict] = ()) -> dict[str, Path]:
    """Write the standup as Markdown + HTML under the standup export dir.

    Returns ``{"markdown": Path, "html": Path}``. One file per day (dated
    filename) — a re-run the same day overwrites so the latest wins. Best-effort:
    on any I/O error the exception propagates to the caller, which logs it.
    ``history`` (StandupStore.get_history rows) feeds the HTML confidence trend.
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
    html_path.write_text(build_standup_html(report, history=history), encoding="utf-8")
    logger.info("Standup exported: %s , %s", md_path, html_path)
    return {"markdown": md_path, "html": html_path}
