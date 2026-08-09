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
    # the helpers so the surfaces cannot drift: rationale echoes stripped from
    # stored reports, summary bullets deduped per ticket, canonical empty-state
    # category lines dropped (coverage is stated once in the footer), and
    # zero-activity members compressed to one shared line.
    from yeaboi.standup import categories
    from yeaboi.standup.export import _is_quiet, _member_summary_bullets, _ticket_key_map, strip_rationale_echo

    team_summary = strip_rationale_echo(report.team_summary, report.confidence_rationale)
    if team_summary:
        lines.append("Team summary:")
        lines.append(f"  {team_summary}")
        lines.append("")

    quiet = [m for m in report.member_updates if _is_quiet(m)]
    active = [m for m in report.member_updates if not _is_quiet(m)]
    key_map = _ticket_key_map(report)

    if active or quiet:
        lines.append("Updates:")
        for m in active:
            tag = "✍️" if m.self_report else "•"
            lines.append(f"  {tag} {m.name}")
            overview = "; ".join(_member_summary_bullets(m.summary, key_map)) or "No activity detected."
            lines.append(f"      General overview: {overview}")
            if getattr(m, "progress_note", ""):
                lines.append(f"      ↺ Since last standup: {m.progress_note}")
            for label, summary in (
                ("Ticketing", m.ticketing_summary),
                ("Code", m.code_summary),
                ("Documentation", m.documentation_summary),
            ):
                if summary and categories.is_empty_state(summary):
                    continue
                lines.append(f"      {label}: {summary or f'{label} summary unavailable.'}")
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
    if report.skipped_sources:
        skipped = ", ".join(f"{src} ({reason})" for src, reason in report.skipped_sources)
        lines.append(f"Sources skipped — {skipped}")
    return lines


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
        body.append(Text("Team summary", style=f"bold {accent}"))
        body.append(Text(f"  {report.team_summary}"))
        body.append(Text(""))

    if report.member_updates:
        body.append(Text("Updates", style=f"bold {accent}"))
        for m in report.member_updates:
            row = Text()
            tag = "✍" if m.self_report else "•"
            row.append(f"  {tag} ", style="dim")
            row.append(m.name, style="bold")
            body.append(row)
            body.append(Text(f"      General overview: {m.summary or 'No activity detected.'}"))
            if getattr(m, "progress_note", ""):
                body.append(Text(f"      ↺ Since last standup: {m.progress_note}", style="italic"))
            body.append(Text(f"      Ticketing: {m.ticketing_summary or 'Ticketing summary unavailable.'}"))
            body.append(Text(f"      Code: {m.code_summary or 'Code summary unavailable.'}", style="rgb(120,190,220)"))
            body.append(
                Text(
                    f"      Documentation: {m.documentation_summary or 'Documentation summary unavailable.'}",
                    style="rgb(170,160,220)",
                )
            )
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
    else:
        body.append(Text("No individual updates.", style="dim"))

    if report.category_coverage:
        coverage = ", ".join(f"{category}: {status.replace('_', ' ')}" for category, status in report.category_coverage)
        body.append(Text(""))
        body.append(Text(f"Coverage — {coverage}", style="dim"))

    return Group(*body)
