"""Rendering for a StandupReport — one source of truth for every surface.

Plaintext is used by Slack/email/desktop delivery; the Rich form is used by the
terminal delivery channel and the TUI standup page. Keeping both here means the
report looks consistent everywhere and no surface re-implements the layout.

# See docs: "Daily Standup" — delivery, TUI page
"""

from __future__ import annotations

import logging

from rich.console import Group
from rich.text import Text

from yeaboi.agent.state import StandupReport
from yeaboi.standup import collector


def summary_heading(report: StandupReport) -> str:
    """The narrative's heading: a solo report has no team to summarise."""
    return "Summary" if getattr(report, "solo", False) else "Team summary"


logger = logging.getLogger(__name__)

# Emoji markers per confidence label — used in plaintext (Slack/email) output.
_CONFIDENCE_EMOJI = {
    "On track": "🟢",
    "At risk": "🟡",
    "Behind": "🔴",
    "Insufficient data": "⚪",
}


def _sprint_line(report: StandupReport) -> str:
    if report.sprint_total_days:
        return f"{report.sprint_name or 'Sprint'} — day {report.sprint_day} of {report.sprint_total_days}"
    return report.sprint_name or "Sprint (dates unknown)"


def _confidence_line(report: StandupReport) -> str:
    emoji = _CONFIDENCE_EMOJI.get(report.confidence_label, "")
    label = report.confidence_label or "Unknown"
    pct = f" ({report.confidence_pct}%)" if report.confidence_label not in ("", "Insufficient data") else ""
    return f"{emoji} {label}{pct}{_trend_fragment(report)}".strip()


def _trend_fragment(report: StandupReport) -> str:
    """Day-over-day movement suffix, e.g. " ▲ +6 vs last" — empty when steady/no history."""
    trend = getattr(report, "confidence_trend", "")
    delta = getattr(report, "confidence_delta", 0)
    if trend == "improving":
        return f" ▲ +{delta} vs last"
    if trend == "declining":
        return f" ▼ {abs(delta)} vs last"
    return ""


def _practice_rollup_line(report: StandupReport) -> str:
    """Team-level practice summary, or "" — counts members, never signals."""
    from yeaboi.standup.habits import RULE_TITLES

    parts = [f"{RULE_TITLES.get(rule, rule)} ×{count}" for rule, count in getattr(report, "practice_rollup", ()) or ()]
    return " · ".join(parts)


def _denoise(report: StandupReport):
    """The shared de-noise pass for the plaintext and rich renderers.

    Returns ``(quiet, active, overview_of, category_lines_of)`` — the member
    partition plus two per-member helpers: the deduped one-line overview, and
    the (label, text) category lines with canonical empty states dropped. The
    FAILED sentence survives by design ("we could not look" is per-member
    news); it is `categories.is_empty_state` that draws that line.
    """
    from yeaboi.standup import categories
    from yeaboi.standup.export import _is_quiet, _member_summary_bullets, _ticket_key_map

    quiet = [m for m in report.member_updates if _is_quiet(m)]
    active = [m for m in report.member_updates if not _is_quiet(m)]
    key_map = _ticket_key_map(report)

    def overview_of(m) -> str:
        return "; ".join(_member_summary_bullets(m.summary, key_map)) or "No activity detected."

    def category_lines_of(m) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for label, summary in (
            ("Ticketing", m.ticketing_summary),
            ("Code", m.code_summary),
            ("Documentation", m.documentation_summary),
        ):
            if summary and categories.is_empty_state(summary):
                continue
            out.append((label, summary or f"{label} summary unavailable."))
        return out

    return quiet, active, overview_of, category_lines_of


def _production_lines(report: StandupReport) -> list[str]:
    """The Production block, or nothing at all.

    Nothing at all is the point: with no ops vendor connected there are no
    signals, so this appends no heading, no line and no blank — a broadcast
    surface must not carry a section announcing that a feature exists.
    """
    from yeaboi.standup import ops as standup_ops

    signals = getattr(report, "ops_signals", ()) or ()
    if not signals:
        return []
    window = signals[0].window_start[:10]
    out = ["", f"Production (since {window}):" if window else "Production:"]
    for signal in signals:
        out.append(f"  - {standup_ops.signal_line(signal)}")
        for sample in signal.samples[:3]:
            out.append(f"      · {sample}")
    return out


def format_standup_lines(report: StandupReport) -> list[str]:
    """Return the standup as a list of plain-text lines (no ANSI)."""
    lines: list[str] = [
        f"Daily Standup — {report.date}",
        _sprint_line(report),
        f"Confidence: {_confidence_line(report)}",
    ]
    if report.confidence_rationale:
        lines.append(f"  {report.confidence_rationale}")
    if rollup := _practice_rollup_line(report):
        lines.append(f"  Practices: {rollup}")
    # Surface problems up top so they're never missed (missing key, source 401/403).
    if report.warnings:
        lines.append("")
        lines.append("⚠ Notices:")
        for w in report.warnings:
            lines.append(f"  - {w}")
    lines.append("")

    # The same de-noising the markdown/HTML exporters apply — export.py owns
    # the helpers so the surfaces cannot drift: summary bullets deduped per
    # ticket, canonical empty-state category lines dropped (coverage is stated
    # once in the footer), and zero-activity members compressed to one shared
    # line. The team summary renders verbatim: the rationale-echo strip is
    # generation-time only, so a host-edited sentence can never be deleted.
    quiet, active, overview_of, category_lines_of = _denoise(report)

    if report.team_summary:
        lines.append(f"{summary_heading(report)}:")
        lines.append(f"  {report.team_summary}")
        lines.append("")

    # Team-level state, so it reads before the per-person sections — and after
    # the summary, which is the one place it may already have been mentioned.
    if production := _production_lines(report):
        lines.extend(production[1:])
        lines.append("")

    if active or quiet:
        lines.append("Updates:")
        for m in active:
            tag = "✍️" if m.self_report else "•"
            lines.append(f"  {tag} {m.name}")
            lines.append(f"      General overview: {overview_of(m)}")
            if getattr(m, "progress_note", ""):
                lines.append(f"      ↺ Since last standup: {m.progress_note}")
            for label, summary in category_lines_of(m):
                lines.append(f"      {label}: {summary}")
            if getattr(m, "outlook", ""):
                lines.append(f"      → Outlook: {m.outlook}")
            # Their own typed words ride alongside the activity analysis, never replace it.
            for i, sr_line in enumerate(m.self_report.splitlines()):
                prefix = "✍ In their words: " if i == 0 else "  "
                lines.append(f"      {prefix}{sr_line}")
            if m.blockers:
                lines.append(f"      ⚠ Blocker: {m.blockers}")
            # Practices sit after the blocker: the blocker is what to act on
            # today, these are the coaching note. Capped at three per member by
            # habits.py, which is what keeps a ten-person Slack post readable.
            for signal in getattr(m, "practices", ()) or ():
                again = " (again today)" if getattr(signal, "repeat", False) else ""
                lines.append(f"      ◇ {signal.title}{again}: {signal.detail}")
            # Raw URLs — Slack/email clients auto-link them.
            category_links = (
                *getattr(m, "ticketing_links", ()),
                *getattr(m, "code_links", ()),
                *getattr(m, "documentation_links", ()),
            )
            for label, url in () if category_links else getattr(m, "links", ()):
                lines.append(f"      🔗 {label}: {url}")
            for label, url in getattr(m, "ticketing_links", ()):
                lines.append(f"      🔗 Ticket {label}: {url}")
            for label, url in getattr(m, "code_links", ()):
                lines.append(f"      🔗 Code {label}: {url}")
            for label, url in getattr(m, "documentation_links", ()):
                lines.append(f"      🔗 Documentation {label}: {url}")
        if quiet:
            lines.append(f"  • No activity detected: {', '.join(m.name for m in quiet)}")
    else:
        lines.append("No individual updates.")

    if report.activity_counts:
        counts = ", ".join(f"{src}: {n}" for src, n in report.activity_counts)
        window = f"  ({report.activity_window})" if report.activity_window else ""
        lines.append("")
        lines.append(f"Activity examined — {counts}{window}")
    if report.category_coverage:
        coverage = ", ".join(f"{category}: {status.replace('_', ' ')}" for category, status in report.category_coverage)
        lines.append(f"Coverage — {coverage}")
    skipped = broadcast_skipped(report)
    if skipped:
        lines.append(f"Sources skipped — {skipped}")
    return lines


def broadcast_skipped(report: StandupReport) -> str:
    """The skipped-source line for a surface that goes OUT — Slack, email, Markdown.

    Deliberately not the full ``skipped_sources`` list. Diagnostic surfaces (the
    TUI "Not scanned" panel, the HTML details) show every skip, because someone is
    looking at them to answer "where is my GitHub?". A broadcast has no such reader:
    listing five sources a Jira-only team never selected would append the same
    apology to every standup it ever posts. Only ``unmet_sources`` — asked for and
    not delivered — is news, and it stops once the user acts on it.
    """
    unmet = set(report.unmet_sources)
    return ", ".join(
        f"{collector.source_label(src)} ({reason})" for src, reason in report.skipped_sources if src in unmet
    )


def format_standup_plaintext(report: StandupReport) -> str:
    """Return the standup as a single plain-text string (for Slack/email/desktop)."""
    logger.info(
        "standup render: plaintext report — %d member update(s), %d warning(s)",
        len(report.member_updates),
        len(report.warnings),
    )
    return "\n".join(format_standup_lines(report))


def format_standup_rich(report: StandupReport, *, accent: str = "rgb(200,100,180)") -> Group:
    """Return a Rich renderable for terminal / TUI display."""
    logger.info(
        "standup render: rich report — %d member update(s), %d warning(s)",
        len(report.member_updates),
        len(report.warnings),
    )
    body: list[Text] = []
    header = Text(justify="left")
    header.append(f"Daily Standup — {report.date}", style=f"bold {accent}")
    body.append(header)
    body.append(Text(_sprint_line(report), style="dim"))

    conf = Text()
    conf.append("Confidence: ", style="dim")
    conf.append(_confidence_line(report), style="bold")
    body.append(conf)
    if report.confidence_rationale:
        body.append(Text(f"  {report.confidence_rationale}", style="dim"))
    if rollup := _practice_rollup_line(report):
        body.append(Text(f"  Practices: {rollup}", style="dim"))
    body.append(Text(""))

    # Notices up top — auth/API-key problems must be seen, never silently empty.
    if report.warnings:
        body.append(Text("⚠ Notices", style="bold rgb(220,180,60)"))
        for w in report.warnings:
            body.append(Text(f"  - {w}", style="rgb(220,180,60)"))
        body.append(Text(""))

    if report.team_summary:
        body.append(Text(summary_heading(report), style=f"bold {accent}"))
        body.append(Text(f"  {report.team_summary}"))
        body.append(Text(""))

    production = _production_lines(report)
    if production:
        body.append(Text(production[1].rstrip(":"), style=f"bold {accent}"))
        for line in production[2:]:
            body.append(Text(line, style="dim" if line.lstrip().startswith("·") else ""))
        body.append(Text(""))

    # Same de-noise pass as the plaintext — the terminal an operator watches
    # must not be noisier than the Slack post the same run just delivered.
    quiet, active, overview_of, category_lines_of = _denoise(report)
    category_styles = {"Ticketing": "", "Code": "rgb(120,190,220)", "Documentation": "rgb(170,160,220)"}

    if active or quiet:
        body.append(Text("Updates", style=f"bold {accent}"))
        for m in active:
            row = Text()
            tag = "✍" if m.self_report else "•"
            row.append(f"  {tag} ", style="dim")
            row.append(m.name, style="bold")
            body.append(row)
            body.append(Text(f"      General overview: {overview_of(m)}"))
            if getattr(m, "progress_note", ""):
                body.append(Text(f"      ↺ Since last standup: {m.progress_note}", style="italic"))
            for label, summary in category_lines_of(m):
                body.append(Text(f"      {label}: {summary}", style=category_styles.get(label, "")))
            if getattr(m, "outlook", ""):
                body.append(Text(f"      → Outlook: {m.outlook}", style="italic dim"))
            # Their own typed words ride alongside the activity analysis, never replace it.
            for i, sr_line in enumerate(m.self_report.splitlines()):
                prefix = "✍ In their words: " if i == 0 else "  "
                body.append(Text(f"      {prefix}{sr_line}", style="italic dim"))
            if m.blockers:
                body.append(Text(f"      ⚠ Blocker: {m.blockers}", style="rgb(220,180,60)"))
            # Deliberately dimmer than the blocker's warn colour: a practice
            # note is a nudge, not something to drop the sprint for.
            for signal in getattr(m, "practices", ()) or ():
                again = " (again today)" if getattr(signal, "repeat", False) else ""
                body.append(Text(f"      ◇ {signal.title}{again}: {signal.detail}", style="rgb(150,145,175)"))
            category_links = (
                *getattr(m, "ticketing_links", ()),
                *getattr(m, "code_links", ()),
                *getattr(m, "documentation_links", ()),
            )
            for label, url in () if category_links else getattr(m, "links", ()):
                link = Text("      ↗ ", style="dim")
                # OSC-8 hyperlink — clickable in supporting terminals, plain elsewhere.
                link.append(label, style=f"underline {accent} link {url}")
                body.append(link)
            for label, url in getattr(m, "ticketing_links", ()):
                link = Text("      ↗ Ticket ", style="dim")
                link.append(label, style=f"underline {accent} link {url}")
                body.append(link)
            for label, url in getattr(m, "code_links", ()):
                link = Text("      ↗ Code ", style="dim")
                link.append(label, style=f"underline {accent} link {url}")
                body.append(link)
            for label, url in getattr(m, "documentation_links", ()):
                link = Text("      ↗ Documentation ", style="dim")
                link.append(label, style=f"underline {accent} link {url}")
                body.append(link)
        if quiet:
            body.append(Text(f"  • No activity detected: {', '.join(m.name for m in quiet)}", style="dim"))
    else:
        body.append(Text("No individual updates.", style="dim"))

    if report.category_coverage:
        coverage = ", ".join(f"{category}: {status.replace('_', ' ')}" for category, status in report.category_coverage)
        body.append(Text(""))
        body.append(Text(f"Coverage — {coverage}", style="dim"))

    return Group(*body)
