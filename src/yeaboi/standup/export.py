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
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from pathlib import Path

from yeaboi.agent.state import MemberUpdate, StandupReport
from yeaboi.artifacts.render import (
    annotations_markdown,
    edit_map,
    ev_children,
    ev_field,
    evidence_payload,
    row_anchor,
    with_annotations,
)
from yeaboi.html_theme import prose_bullets as _summary_bullets
from yeaboi.html_theme import safe_url
from yeaboi.html_theme import split_sentences as _split_sentences
from yeaboi.standup import categories, references
from yeaboi.standup.render import broadcast_skipped

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
# Aliased rather than re-declared: `references` owns the pattern (habits.py has
# to agree with it exactly), and the local name keeps this module's five other
# consumers — _KEY_ONLY_RE, _ticket_title_map, _runs, _leftover_links — as they
# were.
_TICKET_KEY_RE = references.TICKET_KEY_RE

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
        # Practice details name ticket keys ("PSOT-12 shipped but the board…"),
        # so they belong in the prefix-gated key map like any other prose.
        *(s.detail or "" for s in getattr(m, "practices", ()) or ()),
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
    known_prefixes = references.prefixes_of(key_map)
    prose: list[str] = [report.team_summary or "", report.confidence_rationale or ""]
    for m in report.member_updates:
        prose.extend(_member_prose(m))
    for text in prose:
        for key in references.gated_ticket_keys(text, prefixes=known_prefixes):
            if key not in key_map:
                key_map[key] = f"{base.rstrip('/')}/browse/{key}"
    return key_map


_TICKET_TITLE_MAX = 60


def _ticket_title_map(report: StandupReport) -> dict[str, str]:
    """Map ticket keys to their collected titles for first-mention enrichment.

    Harvested from the structured evidence rows — deterministic collector data,
    never LLM output. The first non-empty title per key wins, trimmed so an
    enriched link stays a phrase rather than a paragraph.
    """
    titles: dict[str, str] = {}
    for m in report.member_updates:
        for field in ("ticketing_evidence", "code_evidence", "documentation_evidence"):
            for e in getattr(m, field, ()) or ():
                key = ev_field(e, "key")
                title = ev_field(e, "title")
                if not title or key in titles or not _TICKET_KEY_RE.fullmatch(key):
                    continue
                if len(title) > _TICKET_TITLE_MAX:
                    title = title[: _TICKET_TITLE_MAX - 1].rstrip() + "…"
                titles[key] = title
    return titles


def _runs(
    text: str,
    key_map: Mapping[str, str],
    names: re.Pattern[str] | None = None,
    titles: Mapping[str, str] | None = None,
    seen: set[str] | None = None,
) -> list[dict]:
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
            if titles is not None and seen is not None and span in titles and span not in seen:
                # The first mention of a ticket in this document scope (one
                # member card) carries its title inline — "PSOT-14 Fix login" —
                # so a reader never meets a bare id cold; later mentions stay
                # bare keys so enumerations don't balloon.
                seen.add(span)
                runs.append({"s": f"{span} {titles[span]}", "href": url})
            else:
                runs.append({"s": span, "href": url})
        else:
            runs.append({"s": span})
        pos = match.end()
    if pos < len(text):
        runs.append({"s": text[pos:]})
    return runs


def _md_label(text: str) -> str:
    """Neutralise link-label syntax — tracker titles now travel inside ``[…]``,
    and an unescaped bracket or newline would corrupt the link."""
    from yeaboi.markdown_convert import md_label

    return md_label(text)


def _md_runs(runs: Sequence[Mapping]) -> str:
    """Render runs as Markdown — the other consumer of the same structure."""
    out: list[str] = []
    for run in runs:
        s = str(run.get("s", ""))
        if run.get("href"):
            s = f"[{_md_label(s)}]({run['href']})"
        if run.get("strong"):
            s = f"**{s}**"
        out.append(s)
    return "".join(out)


def _md_link(label: str, url: str) -> str:
    """A leftover evidence link as Markdown; an unsafe scheme degrades to text.

    A Markdown link becomes an `<a href>` on Notion/Confluence/GitHub, so it
    needs the same allowlist the HTML path gets from `safeUrl`.
    """
    return f"[{_md_label(label or url)}]({safe})" if (safe := safe_url(url)) else (label or url)


# Markdown shows fewer evidence rows than the HTML (which folds its overflow
# behind a toggle) — a static document has no fold, so it stays shorter.
_MD_EVIDENCE_CAP = 4


# Commit breakdowns under a PR bullet stay short — the PR line is the unit of
# work; its commits are supporting detail.
_MD_CHILD_CAP = 3

# Kinds whose key is a machine id (Confluence page id, Notion UUID) — the
# title is the human handle, so it becomes the link text and the id is dropped.
_DOC_KINDS = frozenset({"page", "page-created"})


def _md_evidence_lines(evidence: Sequence[object]) -> list[str]:
    """Evidence as nested Markdown sub-bullets: linked key, title, repo, status."""
    lines: list[str] = []
    # Same historical-report dedupe as _evidence_payload: one PR merge, one row.
    seen_merges: set[str] = set()
    deduped = []
    for e in evidence:
        merge_key = _pr_merge_dedupe_key(e)
        if merge_key:
            if merge_key in seen_merges:
                continue
            seen_merges.add(merge_key)
        deduped.append(e)
    evidence = deduped
    for e in evidence[:_MD_EVIDENCE_CAP]:
        key = ev_field(e, "key")
        title = ev_field(e, "title")
        url = ev_field(e, "url")
        if ev_field(e, "kind") in _DOC_KINDS:
            label = title or key
            line = _md_link(label, url) if url else label
        else:
            head = _md_link(key or title, url) if url else (key or title)
            line = f"{head} {title}" if title and key else head
        if repo := ev_field(e, "repository"):
            line += f" · {repo}"
        if status := ev_field(e, "status"):
            line += f" — {status}"
        children = ev_children(e)
        if children:
            line += f" — {len(children)} commit{'s' if len(children) != 1 else ''}"
        if line.strip():
            lines.append(f"  - {line.strip()}")
        for c in children[:_MD_CHILD_CAP]:
            c_key = ev_field(c, "key")
            c_title = ev_field(c, "title")
            c_url = ev_field(c, "url")
            c_head = _md_link(c_key or c_title, c_url) if c_url else (c_key or c_title)
            c_line = f"{c_head} {c_title}" if c_title and c_key else c_head
            if c_line.strip():
                lines.append(f"    - {c_line.strip()}")
        if (c_extra := len(children) - _MD_CHILD_CAP) > 0:
            lines.append(f"    - …and {c_extra} more")
    if (extra := len(evidence) - _MD_EVIDENCE_CAP) > 0:
        lines.append(f"  - …and {extra} more")
    return lines


def _links_payload(pairs: Sequence[tuple[str, str]]) -> list[list[str]]:
    """Leftover evidence links as ``[label, url]``, unsafe schemes dropped to "".

    The empty URL is deliberate rather than dropping the row: the label is what
    the link was *evidence of*, and losing it entirely would silently shrink the
    evidence a reader is being shown.
    """
    return [[label or url, safe_url(url)] for label, url in pairs or ()]


def _pr_merge_dedupe_key(e: object) -> str:
    """The ``pr-merge:{repo}:{number}`` identity of a merge-commit row, or "".

    The engine dedupes these at collection time now, but reports stored before
    it did carry the branch-side and target-side merge commits as two rows —
    same subject, different SHAs. Re-exports of history deserve the same one
    merge, one row.
    """
    if ev_field(e, "kind") != "commit":
        return ""
    title = ev_field(e, "title")
    if not references.is_merge_subject(title):
        return ""
    number = references.pr_reference(title)
    return f"pr-merge:{ev_field(e, 'repository')}:{number}" if number else ""


def _evidence_payload(evidence: Sequence[object]) -> list[dict]:
    """Structured evidence rows for the browser, with merge commits deduped.

    The projection itself is shared (``artifacts.render.evidence_payload``); what
    is standup's own is the dedupe it is handed, which depends on this mode's
    reference grammar. Passing it in rather than filtering first is what keeps it
    applying to a PR's nested commits, as it always has.
    """
    return evidence_payload(evidence, dedupe_key=_pr_merge_dedupe_key)


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
    titles = _ticket_title_map(report)
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

    # Rendered verbatim: the rationale-echo strip happens once, at generation
    # time in the engine. A fuzzy strip here could silently delete a sentence a
    # host hand-edited onto the share — a human's words outrank de-noising.
    if report.team_summary:
        lines += ["", "## Team Summary", ""]
        sentences = [_md_runs(runs) for runs in _team_summary_runs(report.team_summary, key_map, member_names)]
        # A single sentence is a paragraph; several are a scannable list.
        lines += sentences if len(sentences) == 1 else [f"- {s}" for s in sentences]

    # Cross-source disagreements before the member sections: a board/code
    # mismatch is team-level state, and both claims render with their links so
    # the reader can settle it without hunting.
    conflict_cards = getattr(report, "conflicts", ()) or ()
    if conflict_cards:
        lines += ["", "## Conflicts", ""]
        for card in conflict_cards:
            # Everything here embeds tracker/PR text an outsider can author
            # (a PR title travels inside card.detail), and this Markdown lands
            # on Notion/Confluence/Slack where `[x](url)` becomes a live link.
            # _md_label neutralises the bracket syntax on every raw string —
            # only _md_link may mint a link, behind its scheme allowlist.
            lines.append(f"- **{_md_label(card.title)}**")
            lines.append(f"  {_md_label(card.detail)}")
            claim_bits = " vs ".join(
                f"{_md_link(label, url)} ({_md_label(source)}: {_md_label(value)})"
                if url
                else f"{_md_label(label)} ({_md_label(source)}: {_md_label(value)})"
                for source, value, label, url in card.claims
            )
            if claim_bits:
                lines.append(f"  - {claim_bits}")
            if card.recommended_action:
                lines.append(f"  - _{_md_label(card.recommended_action)}_")

    # Production after the conflicts, before the people: team-level state, and
    # over a wider window than the rest of this document — which the heading
    # says, so nobody reads a fortnight's incidents as today's.
    ops_signals = getattr(report, "ops_signals", ()) or ()
    if ops_signals:
        from yeaboi.standup import ops as standup_ops

        window = ops_signals[0].window_start[:10]
        lines += ["", f"## Production (since {window})" if window else "## Production", ""]
        for signal in ops_signals:
            # Every string here is vendor text an outsider can author (a monitor
            # name travels in `samples`), and this Markdown lands on
            # Notion/Confluence/Slack where `[x](url)` becomes a live link.
            lines.append(f"- {_md_label(standup_ops.signal_line(signal))}")
            for sample in signal.samples[:3]:
                lines.append(f"  - {_md_label(sample)}")
        lines.append("")
        lines.append("_Team-wide; not attributed to anyone._")

    # Zero-activity members compress to one shared line after the sections: a
    # full section per quiet member said "No activity detected" three ways each.
    quiet = [m for m in report.member_updates if _is_quiet(m)]
    active = [m for m in report.member_updates if not _is_quiet(m)]

    lines += ["", "## Updates", ""]
    if active:
        # One section per member — labeled bullet lists read far better than the
        # old six-column table, which wrapped into tall rows on Notion/Confluence.
        for m in active:
            is_own = bool(m.self_report) or m.source == "self-reported"
            # First mention of a ticket within this member's section carries
            # its title inline — same rule as the HTML card.
            seen: set[str] = set()
            lines.append(f"### {m.name} (you)" if is_own else f"### {m.name}")
            lines.append("")
            fragments = _member_summary_bullets(m.summary, key_map)
            if not fragments:
                lines.append("_No activity detected._")
            elif len(fragments) == 1:
                lines.append(_md_runs(_runs(fragments[0], key_map, titles=titles, seen=seen)))
            else:
                lines += [f"- {_md_runs(_runs(f, key_map, titles=titles, seen=seen))}" for f in fragments]
            lines.append("")
            if getattr(m, "progress_note", ""):
                note = _md_runs(_runs(m.progress_note, key_map, titles=titles, seen=seen))
                lines.append(f"- **Since last standup:** {note}")

            def _refs(pairs: Sequence[tuple[str, str]]) -> str:
                return " · ".join(_md_link(label, url) for label, url in pairs)

            bullets: list[str] = []
            for label, text, links, evidence in (
                (
                    "Ticketing",
                    getattr(m, "ticketing_summary", ""),
                    getattr(m, "ticketing_links", ()),
                    getattr(m, "ticketing_evidence", ()),
                ),
                ("Code", getattr(m, "code_summary", ""), getattr(m, "code_links", ()), getattr(m, "code_evidence", ())),
                (
                    "Docs",
                    getattr(m, "documentation_summary", ""),
                    getattr(m, "documentation_links", ()),
                    getattr(m, "documentation_evidence", ()),
                ),
            ):
                if not text and not links and not evidence:
                    continue
                # A canonical empty-state sentence is a report-wide coverage
                # fact; the Coverage footer states it once for everyone.
                if not links and not evidence and categories.is_empty_state(text):
                    continue
                if evidence:
                    # Structured evidence renders as nested sub-bullets below.
                    # The prose is dropped, not joined: the category one-liner
                    # is an LLM restatement of the same rows, and the old
                    # "— refs" join would repeat the same URLs inline too.
                    bullets.append(f"- **{label}:**")
                    bullets += _md_evidence_lines(evidence)
                    continue
                value = _md_runs(_runs(text, key_map, titles=titles, seen=seen)) if text else ""
                leftovers = _leftover_links(text, links)
                if leftovers:
                    value = f"{value} — {_refs(leftovers)}" if value else _refs(leftovers)
                bullets.append(f"- **{label}:** {value}")
            if getattr(m, "outlook", ""):
                bullets.append(f"- **Outlook:** {_md_runs(_runs(m.outlook, key_map, titles=titles, seen=seen))}")
            if m.blockers:
                bullets.append(f"- **Blocker:** {_md_runs(_runs(m.blockers, key_map, titles=titles, seen=seen))}")
            for signal in getattr(m, "practices", ()) or ():
                again = " (again today)" if getattr(signal, "repeat", False) else ""
                detail = _md_runs(_runs(signal.detail, key_map, titles=titles, seen=seen))
                refs = _refs(signal.evidence) if signal.evidence else ""
                bullets.append(f"- **{signal.title}{again}:** {detail}{f' — {refs}' if refs else ''}")
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
    elif not quiet:
        lines.append("_No individual updates._")
    if quiet:
        lines += [f"_No activity detected: {', '.join(m.name for m in quiet)}._", ""]
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
    # Only the sources the user asked for and did not get — see render.broadcast_skipped.
    skipped = broadcast_skipped(report)
    if skipped:
        lines += ["", f"_Sources skipped — {skipped}_"]

    lines += annotations_markdown(report.annotations)
    lines += ["", f"🤙 _Generated by [yeaboi.ai](https://yeaboi.ai) · {datetime.now().strftime('%Y-%m-%d %H:%M')}_", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


def _dedupe_summary_fragments(fragments: Sequence[str], known_keys: Collection[str]) -> list[str]:
    """One summary bullet per ticket: drop a fragment that re-mentions only keys
    an earlier fragment already covered ("Edited PSOT-14" then "continuing
    PSOT-14 in progress" is the same fact twice). A fragment naming any new key,
    or no key at all, always survives — this can only remove restatements.

    Keys are gated on ``known_keys`` (the report's ticket-key map): the bare
    regex also matches UTF-8 / SHA-256 / ISO-8601, and "Fixed the UTF-8
    encoder; added UTF-8 round-trip tests" is two facts, not one.
    """
    seen: set[str] = set()
    kept: list[str] = []
    for fragment in fragments:
        keys = {key for key in references.find_ticket_keys(fragment) if key in known_keys}
        if keys and keys <= seen:
            continue
        seen |= keys
        kept.append(fragment)
    return kept


def _member_summary_bullets(summary: str, known_keys: Collection[str]) -> list[str]:
    """The member's headline bullets: fragmented like all prose, deduped by ticket."""
    return _dedupe_summary_fragments(_summary_bullets(summary), known_keys)


def _is_quiet(m: MemberUpdate) -> bool:
    """A member whose card would say nothing but "No activity detected".

    Deliberately strict: any count, evidence row, link, blocker, practice
    signal, self-report, outlook, progress note — or a summary (headline or
    per-category) that says anything of its own — keeps the full card. A
    votable practice signal or a blocker must never disappear into a one-line
    strip, and neither may a category summary a host hand-edited on a share:
    the downloaded copy renders through this same predicate.
    """
    if any(
        getattr(m, f"{category}_activity_count", 0)
        or getattr(m, f"{category}_evidence", ())
        or getattr(m, f"{category}_links", ())
        for category in ("ticketing", "code", "documentation")
    ):
        return False
    if m.blockers or m.self_report or getattr(m, "practices", ()) or getattr(m, "links", ()):
        return False
    if getattr(m, "outlook", "") or getattr(m, "progress_note", ""):
        return False
    if any(
        summary and not categories.is_empty_state(summary)
        for summary in (
            getattr(m, "ticketing_summary", ""),
            getattr(m, "code_summary", ""),
            getattr(m, "documentation_summary", ""),
        )
    ):
        return False
    return (m.summary or "").strip() in ("", "No activity detected.")


def _category_payload(
    label: str,
    summary: str,
    links: Sequence[tuple[str, str]],
    key_map: Mapping[str, str],
    evidence: Sequence[object] = (),
    titles: Mapping[str, str] | None = None,
    seen: set[str] | None = None,
) -> dict:
    """One labelled list inside a member card: bullet runs plus its evidence.

    ``evidence`` is the structured form (kind/key/title/repo); the renderer
    prefers it and falls back to the bare ``links`` chips only for legacy
    reports that predate it. When evidence exists the prose is dropped rather
    than sent: the category one-liner is an LLM restatement of the same rows
    ("Edited PSOT-14; two tickets remain active" above rows for PSOT-14 and the
    two tickets), so shipping both rendered every fact twice. The prose still
    travels for evidence-less/legacy categories, where it is the only content.
    """
    rows = _evidence_payload(evidence)
    return {
        "label": label,
        "items": (
            []
            if rows
            else [_runs(fragment, key_map, titles=titles, seen=seen) for fragment in _summary_bullets(summary)]
        ),
        "links": _links_payload(_leftover_links(summary, links)),
        "evidence": rows,
    }


def _practice_title(rule: str) -> str:
    """Human label for a rule id — sent with the rollup so the bundle needn't
    carry a second copy of a vocabulary the engine owns."""
    from yeaboi.standup.habits import RULE_TITLES

    return RULE_TITLES.get(rule, rule.replace("-", " ").capitalize())


def _member_payload(
    m: MemberUpdate,
    key_map: Mapping[str, str],
    titles: Mapping[str, str] | None = None,
    *,
    editable: bool = False,
) -> dict:
    """One member as data: their prose, their evidence, and what they are stuck on."""
    # First mention of a ticket in THIS card carries its title inline; the set
    # is consumed in the card's visual order (the blocker note leads the card),
    # so "first" means first thing the reader meets.
    seen: set[str] = set()

    def runs(text: str) -> list[dict]:
        return _runs(text, key_map, titles=titles, seen=seen)

    out: dict = {
        "name": m.name,
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
    if editable:
        anchor = row_anchor("member_updates", "name", m.name)
        out["anchor"] = anchor
        out["edit"] = edit_map(
            anchor,
            m,
            (
                "summary",
                "blockers",
                "progress_note",
                "outlook",
                "ticketing_summary",
                "code_summary",
                "documentation_summary",
            ),
        )
    if bool(m.self_report) or m.source == "self-reported":
        out["own"] = True
    if m.blockers:
        out["blockers"] = runs(m.blockers)
    # Terse clauses, one bullet each — the same fragmenting the team summary
    # and the category items get, deduped so one ticket is one bullet.
    out["summary"] = [runs(fragment) for fragment in _member_summary_bullets(m.summary, key_map)]
    if getattr(m, "progress_note", ""):
        out["progressNote"] = runs(m.progress_note)

    # A category earns a column when it has real activity or evidence links; one
    # with prose but neither becomes a footnote — so a quiet category never
    # squeezes the busy ones into narrow strips — and one whose prose is a
    # canonical empty-state sentence renders nothing at all: coverage (including
    # "sources not configured") is a report-wide fact the Details section
    # states once. Only bespoke prose earns a footnote.
    for label, summary, links, count, evidence in (
        (
            "Ticketing",
            getattr(m, "ticketing_summary", ""),
            getattr(m, "ticketing_links", ()),
            getattr(m, "ticketing_activity_count", 0),
            getattr(m, "ticketing_evidence", ()),
        ),
        (
            "Code",
            getattr(m, "code_summary", ""),
            getattr(m, "code_links", ()),
            getattr(m, "code_activity_count", 0),
            getattr(m, "code_evidence", ()),
        ),
        (
            "Documentation",
            getattr(m, "documentation_summary", ""),
            getattr(m, "documentation_links", ()),
            getattr(m, "documentation_activity_count", 0),
            getattr(m, "documentation_evidence", ()),
        ),
    ):
        block = (
            _category_payload(label, summary, links, key_map, evidence, titles=titles, seen=seen)
            if (count or links or evidence)
            else None
        )
        if block and (block["items"] or block["links"] or block["evidence"]):
            out["categories"].append(block)
        elif summary and not categories.is_empty_state(summary):
            # Canonical empty-state sentences are report-wide coverage facts the
            # Details section states once; only bespoke prose earns a footnote.
            out["footnotes"].append({"label": label, "runs": runs(summary)})

    # Practices sit after the categories: the reader has just seen what shipped,
    # which is the context that makes "no ticket behind it" mean anything. The
    # rule id travels as the word — the component maps it to a tone, and an id
    # it doesn't know renders muted rather than failing a build.
    #
    # ``handles`` is deliberately absent: it is internal identity for the
    # feedback ledger, and an export is a file with nothing to vote from.
    if practices := getattr(m, "practices", ()) or ():
        out["practices"] = [
            {
                "rule": s.rule,
                "title": s.title,
                "detail": runs(s.detail),
                "evidence": _links_payload(s.evidence),
                **({"repeat": True} if getattr(s, "repeat", False) else {}),
            }
            for s in practices
        ]

    if getattr(m, "outlook", ""):
        out["outlook"] = runs(m.outlook)
    if m.self_report:
        # The verbatim quote gets linkified too — people type bare ticket keys.
        out["selfReport"] = runs(m.self_report)

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


def standup_export_args(
    report: StandupReport, *, history: Sequence[dict] = (), editable: bool = False, correctable: bool = False
) -> dict[str, object]:
    """Return the chrome + payload keyword arguments for one standup document.

    Split out of :func:`build_standup_html` so the same payload can be built
    without a page around it. A shared *editable* standup re-derives this dict on
    every change and sends it down the long poll; the file export wraps it in a
    document once. Both go through ``html_theme``, so the two cannot drift.

    Page-level arguments — ``markdown_name``, ``document_title`` — deliberately
    stay with the caller: a served document has no sibling Markdown file, so
    naming one would point a reader at something nobody wrote.

    ``correctable`` says a live share server is behind this page and will accept
    a verdict on a practice signal. False for every written file: an export on
    disk has nowhere to send one, and rendering the controls anyway would offer
    the reader a button that silently does nothing. Independent of ``editable``:
    a reader may answer a signal on a document whose prose they cannot rewrite.
    """
    from yeaboi.html_theme import image_data_uri, trend

    key_map = _ticket_key_map(report)
    titles = _ticket_title_map(report)
    # Zero-activity members compress into one strip below the cards. Except on
    # an editable share: the strip has no per-member edit anchors, and a host
    # correcting the record ("Alexandru actually did X") needs the card.
    quiet = [] if editable else [m for m in report.member_updates if _is_quiet(m)]
    quiet_names = {m.name for m in quiet}
    if quiet:
        # The log is where "where did that member's card go?" gets answered.
        logger.info("standup export: %d quiet member(s) collapsed to the strip: %s", len(quiet), sorted(quiet_names))
    members = [
        _member_payload(m, key_map, titles, editable=editable)
        for m in report.member_updates
        if m.name not in quiet_names
    ]
    # Screenshots pasted into "My Update". Embedded rather than referenced: the
    # files live under ~/.yeaboi and get pruned, so a path would go stale.
    images = [uri for p in report.images if (uri := image_data_uri(p))]

    nav: list[tuple[str, str]] = [("overview", "Overview")]
    if report.team_summary:
        nav.append(("summary", "Team Summary"))
    if getattr(report, "conflicts", ()):
        nav.append(("conflicts", "Conflicts"))
    if getattr(report, "ops_signals", ()):
        nav.append(("production", "Production"))
    nav.append(("updates", "Updates"))
    if images:
        nav.append(("screenshots", "Screenshots"))
    has_details = bool(report.activity_counts or report.category_coverage or report.skipped_sources)
    if has_details:
        nav.append(("details", "Details"))

    args = dict(
        mode="standup",
        title="Daily Standup",
        wordmark="standup",
        subtitle=report.date,
        # No MEMBERS row: a head-count is not something a standup reader acts
        # on, and the member strip plus the quiet strip below name everyone —
        # names beat a number.
        facts=[
            ("SPRINT", _sprint_line(report)),
            ("CONFIDENCE", _confidence_text(report)),
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
            # Verbatim — the rationale-echo strip is generation-time only; a
            # fuzzy strip here could delete a host-edited sentence.
            "summary": _team_summary_runs(report.team_summary, key_map, [m.name for m in report.member_updates]),
            "members": members,
            "quietMembers": [m.name for m in quiet],
            "activityCounts": [[source, count] for source, count in report.activity_counts],
            "activityWindow": report.activity_window,
            # Machine-readable bounds for the timeline axis. Always present;
            # both empty on a report stored before the timeline existed, and
            # the page then derives the axis from the event times instead.
            "window": {
                "start": getattr(report, "activity_window_start", ""),
                "end": getattr(report, "activity_window_end", ""),
            },
            "coverage": [[category, status] for category, status in report.category_coverage],
            "skipped": [[source, reason] for source, reason in report.skipped_sources],
            # Overview rollup. `count` is MEMBERS, so "2" beside untracked-work
            # means two people, not two PRs. An object rather than the pair-array
            # its neighbours use, because three heterogeneous fields read badly
            # as a positional tuple.
            "practices": [
                {"rule": rule, "count": count, "title": _practice_title(rule)}
                for rule, count in getattr(report, "practice_rollup", ()) or ()
            ],
            # Cross-source disagreements, one card each: both claims travel
            # with their source and evidence url, severity as a word (the
            # bundle maps it to a tone — no colour crosses the wire).
            "conflicts": [
                {
                    "fingerprint": card.fingerprint,
                    "title": card.title,
                    "detail": card.detail,
                    "severity": card.severity,
                    "action": card.recommended_action,
                    "claims": [
                        {"source": source, "value": value, "label": label, "url": url}
                        for source, value, label, url in card.claims
                    ],
                    "members": list(card.members),
                }
                for card in getattr(report, "conflicts", ()) or ()
            ],
            # What production did, over its own wider window. Counts, words and
            # bounded titles — no body, no metric series, and no person: an
            # OpsSignal has no field one could ride in. `window` is carried per
            # signal so the page can say what the numbers measured.
            "production": [
                {
                    "kind": signal.kind,
                    "source": signal.source,
                    "family": signal.family,
                    "count": signal.count,
                    "resolved": signal.resolved,
                    "severity": signal.severity,
                    "services": list(signal.services),
                    "samples": list(signal.samples),
                    "window": {"start": signal.window_start, "end": signal.window_end},
                }
                for signal in getattr(report, "ops_signals", ()) or ()
            ],
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
            **({"edit": edit_map("", report, ("team_summary", "confidence_rationale"))} if editable else {}),
            # A capability, not a style: whether this page has a server that will
            # take a verdict. Only sent when true, so every written export keeps
            # exactly the payload it had.
            **({"correctable": True} if correctable else {}),
        },
        footer=f"Generated by yeaboi.ai • {datetime.now().strftime('%Y-%m-%d')}",
    )
    return with_annotations(args, report)


def build_standup_html(
    report: StandupReport,
    *,
    history: Sequence[dict] = (),
    document_title: str = "",
    correctable: bool = False,
) -> str:
    """Return the standup as a self-contained HTML document.

    ``history`` is optional ``StandupStore.get_history`` rows (newest-first);
    with two or more usable points it powers the confidence-trend sparkline.

    ``correctable`` is passed through to :func:`standup_export_args`, and is what
    separates a share whose reader may answer a practice signal from a file on
    disk, which has nowhere to send one.
    """
    from yeaboi.html_theme import export_page

    return export_page(
        **standup_export_args(report, history=history, correctable=correctable),  # type: ignore[arg-type]
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
