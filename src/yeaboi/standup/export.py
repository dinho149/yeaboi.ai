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
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from yeaboi.agent.state import MemberUpdate, StandupReport
from yeaboi.html_theme import prose_bullets as _summary_bullets
from yeaboi.html_theme import safe_url
from yeaboi.html_theme import split_sentences as _split_sentences

logger = logging.getLogger(__name__)

# The three colour maps that used to live here — confidence label → CSS token,
# confidence label → chip kind, coverage status → dot token — moved to
# `frontend/src/export/reports/Standup.tsx`. They were the same fact written
# three ways for three different markup helpers; there is one renderer now.
#
# Both vocabularies still travel as their own strings (`confidence.LABEL_*`,
# the coverage statuses), because neither is validated against untrusted input —
# they are *produced* by the engine. So the bundle maps them with a fallback
# rather than a codegen'd union, which is what this file did too, and an
# unrecognised label degrades to the muted tone instead of failing a build.

_SPARKLINE_MAX_POINTS = 14

# Jira-style ticket keys ("PSOT-12"). AzDO work items ("#1234" / "AB#1234")
# deliberately don't match — their URLs only ever arrive via the *_links tuples.
_TICKET_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")

# The same alternation `_runs` builds, for the no-member-names case. Named so
# the two branches read out of one `match.lastgroup` either way.
_KEY_ONLY_RE = re.compile(f"(?P<key>{_TICKET_KEY_RE.pattern})")


def _slug(name: str) -> str:
    """Return a filesystem-safe slug for the export subdirectory."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40] or "standup"


def _stem(report: StandupReport) -> str:
    """The filename stem both artifacts share.

    Shared so the HTML's ``<noscript>`` note can name the Markdown file written
    beside it; guessing that name twice is how the note ends up pointing at a
    file nobody wrote.
    """
    return f"standup-{report.date or 'latest'}"


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


def _runs(text: str, key_map: Mapping[str, str], names: re.Pattern[str] | None = None) -> list[dict]:
    """Split prose into ``{s, href?, strong?}`` runs: ticket keys link, member names bold.

    **This is what replaced the escape-then-substitute pair.** There used to be
    a `_linkify` that HTML-escaped the text and then regex-substituted raw `<a>`
    markup back into it, a `_linkify_md` that did the same job in Markdown, and
    a `_name_pattern(html=…)` that had to build one alternation against the raw
    text and another against the escaped text so bolding worked on both. Four
    functions arranging one fact: *which spans of this sentence are special*.

    That fact is now the return value. `RichText` renders it with text children
    and `safeUrl`, so no escaping is involved on the HTML side at all; the
    Markdown builder renders the same list with `[KEY](url)` and `**Name**`.
    Neither can drift, and neither is one regex bug away from an injection.

    Names and keys share a single alternation so a match can only be one of
    them — with names first, since a member could in principle be called
    something the key regex likes.
    """
    if not text:
        return []
    pattern = re.compile(f"(?P<name>{names.pattern})|(?P<key>{_TICKET_KEY_RE.pattern})") if names else _KEY_ONLY_RE

    runs: list[dict] = []
    pos = 0
    for match in pattern.finditer(text):
        if match.start() > pos:
            runs.append({"s": text[pos : match.start()]})
        span = match.group(0)
        if names and match.lastgroup == "name":
            runs.append({"s": span, "strong": True})
        # safe_url even here: the fallback branch of `_ticket_key_map` builds a
        # URL from configured Jira base, and a hostile one must not become a link.
        elif url := safe_url(key_map.get(span) or ""):
            runs.append({"s": span, "href": url})
        else:
            runs.append({"s": span})
        pos = match.end()
    if pos < len(text):
        runs.append({"s": text[pos:]})
    return runs


def _md_runs(runs: Sequence[Mapping]) -> str:
    """Render runs as Markdown — the other consumer of the same structure."""
    out: list[str] = []
    for run in runs:
        s = str(run.get("s", ""))
        if run.get("href"):
            s = f"[{s}]({run['href']})"
        if run.get("strong"):
            s = f"**{s}**"
        out.append(s)
    return "".join(out)


def _md_link(label: str, url: str) -> str:
    """A leftover evidence link as Markdown; an unsafe scheme degrades to text.

    A Markdown link becomes an `<a href>` on Notion/Confluence/GitHub, so it
    needs the same allowlist the HTML path gets from `safeUrl`.
    """
    return f"[{label or url}]({safe})" if (safe := safe_url(url)) else (label or url)


def _links_payload(pairs: Sequence[tuple[str, str]]) -> list[list[str]]:
    """Leftover evidence links as ``[label, url]``, unsafe schemes dropped to "".

    The empty URL is deliberate rather than dropping the row: the label is what
    the link was *evidence of*, and losing it entirely would silently shrink the
    evidence a reader is being shown.
    """
    return [[label or url, safe_url(url)] for label, url in pairs or ()]


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


def _name_pattern(variants: Sequence[str]) -> re.Pattern[str] | None:
    """One alternation over the member-name variants.

    No `html=` flag any more: it existed because the HTML path matched against
    *escaped* text and so needed `re.escape(_e(v))` while Markdown needed
    `re.escape(v)`. Nothing matches against escaped text now — see `_runs`.
    """
    if not variants:
        return None
    alts = []
    for v in variants:
        pre = r"\b" if v[:1].isalnum() else ""
        post = r"\b" if v[-1:].isalnum() else ""
        alts.append(f"{pre}{re.escape(v)}{post}")
    return re.compile("|".join(alts))


def _team_summary_runs(text: str, key_map: Mapping[str, str], member_names: Sequence[str]) -> list[list[dict]]:
    """The team summary as one run-list per sentence: names bold, ticket keys linked.

    Deterministic post-processing only — no prompt or schema change, so it also
    improves historical reports and the no-LLM fallback path.
    """
    pattern = _name_pattern(_name_variants(member_names))
    return [_runs(sentence, key_map, pattern) for sentence in _split_sentences(text)]


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
        sentences = [_md_runs(runs) for runs in _team_summary_runs(report.team_summary, key_map, member_names)]
        # A single sentence is a paragraph; several are a scannable list.
        lines += sentences if len(sentences) == 1 else [f"- {s}" for s in sentences]

    lines += ["", "## Updates", ""]
    if report.member_updates:
        # One section per member — labeled bullet lists read far better than the
        # old six-column table, which wrapped into tall rows on Notion/Confluence.
        for m in report.member_updates:
            is_own = bool(m.self_report) or m.source == "self-reported"
            lines.append(f"### {m.name} (you)" if is_own else f"### {m.name}")
            lines.append("")
            overview = _md_runs(_runs(m.summary, key_map)) if m.summary else "_No activity detected._"
            lines.append(overview)
            lines.append("")
            if getattr(m, "progress_note", ""):
                lines.append(f"- **Since last standup:** {_md_runs(_runs(m.progress_note, key_map))}")

            def _refs(pairs: Sequence[tuple[str, str]]) -> str:
                return " · ".join(_md_link(label, url) for label, url in pairs)

            bullets: list[str] = []
            for label, text, links in (
                ("Ticketing", getattr(m, "ticketing_summary", ""), getattr(m, "ticketing_links", ())),
                ("Code", getattr(m, "code_summary", ""), getattr(m, "code_links", ())),
                ("Docs", getattr(m, "documentation_summary", ""), getattr(m, "documentation_links", ())),
            ):
                if not text and not links:
                    continue
                value = _md_runs(_runs(text, key_map)) if text else ""
                leftovers = _leftover_links(text, links)
                if leftovers:
                    value = f"{value} — {_refs(leftovers)}" if value else _refs(leftovers)
                bullets.append(f"- **{label}:** {value}")
            if getattr(m, "outlook", ""):
                bullets.append(f"- **Outlook:** {_md_runs(_runs(m.outlook, key_map))}")
            if m.blockers:
                bullets.append(f"- **Blocker:** {_md_runs(_runs(m.blockers, key_map))}")
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


def _category_payload(label: str, summary: str, links: Sequence[tuple[str, str]], key_map: Mapping[str, str]) -> dict:
    """One labelled list inside a member card: bullet runs plus leftover evidence."""
    return {
        "label": label,
        "items": [_runs(fragment, key_map) for fragment in _summary_bullets(summary)],
        "links": _links_payload(_leftover_links(summary, links)),
    }


def _member_payload(m: MemberUpdate, key_map: Mapping[str, str]) -> dict:
    """One member as data: their prose, their evidence, and what they are stuck on."""
    out: dict = {
        "name": m.name,
        "summary": _runs(m.summary, key_map),
        # Order matters: ticketing, code, docs — the same order as the chips and
        # the team-activity bars, so a reader compares like with like.
        "counts": [
            getattr(m, "ticketing_activity_count", 0),
            getattr(m, "code_activity_count", 0),
            getattr(m, "documentation_activity_count", 0),
        ],
        "categories": [],
        "footnotes": [],
        "links": [],
    }
    if bool(m.self_report) or m.source == "self-reported":
        out["own"] = True
    if getattr(m, "progress_note", ""):
        out["progressNote"] = _runs(m.progress_note, key_map)
    if getattr(m, "outlook", ""):
        out["outlook"] = _runs(m.outlook, key_map)
    if m.blockers:
        out["blockers"] = _runs(m.blockers, key_map)
    if m.self_report:
        # The verbatim quote gets linkified too — people type bare ticket keys.
        out["selfReport"] = _runs(m.self_report, key_map)

    # A category earns a column when it has real activity or evidence links; one
    # with prose but neither becomes a footnote, so a quiet category never
    # squeezes the busy ones into narrow strips. The wording is preserved
    # either way — "source not configured" must stay distinguishable from
    # "no activity detected".
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
        block = _category_payload(label, summary, links, key_map) if (count or links) else None
        if block and (block["items"] or block["links"]):
            out["categories"].append(block)
        elif summary:
            out["footnotes"].append({"label": label, "runs": _runs(summary, key_map)})

    # Legacy reports carry only the general links tuple — surface what is not
    # already an inline anchor in the prose.
    category_links = (
        *(getattr(m, "ticketing_links", ()) or ()),
        *(getattr(m, "code_links", ()) or ()),
        *(getattr(m, "documentation_links", ()) or ()),
    )
    if getattr(m, "links", ()) and not category_links:
        out["links"] = _links_payload(_leftover_links(m.summary, m.links))
    return out


def build_standup_html(report: StandupReport, *, history: Sequence[dict] = (), document_title: str = "") -> str:
    """Return the standup as a self-contained HTML document.

    ``history`` is optional ``StandupStore.get_history`` rows (newest-first);
    with two or more usable points it powers the confidence-trend sparkline.
    """
    from yeaboi.html_theme import export_page, image_data_uri, trend

    key_map = _ticket_key_map(report)
    members = [_member_payload(m, key_map) for m in report.member_updates]
    # Screenshots pasted into "My Update". Embedded rather than referenced: the
    # files live under ~/.yeaboi and get pruned, so a path would go stale.
    images = [uri for p in report.images if (uri := image_data_uri(p))]

    nav: list[tuple[str, str]] = [("overview", "Overview")]
    if report.team_summary:
        nav.append(("summary", "Team Summary"))
    nav.append(("updates", "Updates"))
    if images:
        nav.append(("screenshots", "Screenshots"))
    has_details = bool(report.activity_counts or report.category_coverage or report.skipped_sources)
    if has_details:
        nav.append(("details", "Details"))

    return export_page(
        mode="standup",
        title="Daily Standup",
        wordmark="standup",
        subtitle=report.date,
        facts=[
            ("SPRINT", _sprint_line(report)),
            ("CONFIDENCE", _confidence_text(report)),
            ("MEMBERS", str(len(report.member_updates))),
            ("WINDOW", report.activity_window or ""),
        ],
        nav=nav,
        report={
            "kind": "standup",
            "sprint": {
                "name": report.sprint_name,
                "day": report.sprint_day,
                "total": report.sprint_total_days,
            },
            # The label is a produced value, not a validated one, so it travels
            # as itself and the bundle maps it to a tone with a fallback —
            # exactly what `_CONF_COLOR.get(label, "var(--low)")` did here.
            "confidence": {
                "label": report.confidence_label,
                "pct": report.confidence_pct,
                "text": _confidence_text(report),
                "trend": getattr(report, "confidence_trend", ""),
                "trendText": _trend_text(report),
                "rationale": report.confidence_rationale,
            },
            "summary": _team_summary_runs(report.team_summary, key_map, [m.name for m in report.member_updates]),
            "members": members,
            "activityCounts": [[source, count] for source, count in report.activity_counts],
            "activityWindow": report.activity_window,
            "coverage": [[category, status] for category, status in report.category_coverage],
            "skipped": [[source, reason] for source, reason in report.skipped_sources],
            "images": images,
            "trend": trend(
                history,
                date_key="standup_date",
                value_key="confidence_pct",
                status_key="status",
                title="Confidence trend",
                label="Confidence",
                cutoff_date=report.date,
                current=(report.date, report.confidence_pct),
                max_points=_SPARKLINE_MAX_POINTS,
                floor=0,
                ceiling=100,
            ),
            "warnings": list(report.warnings or []),
        },
        footer=f"Generated by yeaboi.ai • {datetime.now().strftime('%Y-%m-%d')}",
        markdown_name=f"{_stem(report)}.md",
        document_title=document_title,
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
    stem = _stem(report)
    md_path = out_dir / f"{stem}.md"
    html_path = out_dir / f"{stem}.html"
    from yeaboi.export_targets import localize_images

    # Screenshots are copied next to the .md so the export folder is portable.
    md_path.write_text(localize_images(build_standup_markdown(report), out_dir), encoding="utf-8")
    html_path.write_text(build_standup_html(report, history=history), encoding="utf-8")
    logger.info("Standup exported: %s , %s", md_path, html_path)
    return {"markdown": md_path, "html": html_path}
