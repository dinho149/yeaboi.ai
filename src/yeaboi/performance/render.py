"""Rendering for Performance artifacts — one source of truth for every surface.

Plaintext is used by the email delivery; the Rich form is used by the TUI
Performance page. Keeping both here means the artifacts look consistent everywhere
and no surface re-implements the layout (mirrors standup/render.py).

# See docs: "Daily Standup" — delivery, TUI page
"""

from __future__ import annotations

import logging

from rich.console import Group
from rich.text import Text

from yeaboi.agent.state import OneOnOnePrep, OneOnOneRecord, SixMonthReview

logger = logging.getLogger(__name__)

_ACCENT = "rgb(220,110,90)"  # Performance theme coral — keep in sync with the TUI theme


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _bullets(items: tuple[str, ...] | list[str]) -> list[str]:
    return [f"  • {it}" for it in items if it]


def _section_lines(title: str, items: tuple[str, ...] | list[str]) -> list[str]:
    if not items:
        return []
    return [title] + _bullets(items) + [""]


# ---------------------------------------------------------------------------
# 1:1 Prep
# ---------------------------------------------------------------------------


def format_prep_lines(prep: OneOnOnePrep) -> list[str]:
    """Return a 1:1 prep as plain-text lines (no ANSI)."""
    logger.info("performance render: 1:1 prep (plaintext) — engineer=%s", prep.engineer)
    lines = [f"1:1 Prep — {prep.engineer}", f"Prepared: {prep.date}", ""]
    if prep.activity_summary:
        lines += ["Sprint work:", f"  {prep.activity_summary}", ""]
    if prep.carried_action_items:
        lines += _section_lines("Carried-over action items (from last 1:1):", prep.carried_action_items)
    lines += _section_lines("Talking points:", prep.talking_points)
    lines += _section_lines("Feedback to give:", prep.feedback)
    lines += _section_lines("Goals to align on:", prep.goals)
    lines += _section_lines("Gaps observed:", prep.gaps)
    lines += _section_lines("Areas to improve:", prep.improvements)
    if prep.warnings:
        lines += _section_lines("⚠ Notices:", prep.warnings)
    return [ln for ln in lines]


def format_prep_rich(prep: OneOnOnePrep, *, accent: str = _ACCENT) -> Group:
    """Return a Rich renderable for the 1:1 prep."""
    logger.info("performance render: 1:1 prep (rich) — engineer=%s", prep.engineer)
    body: list[Text] = [
        Text(f"1:1 Prep — {prep.engineer}", style=f"bold {accent}"),
        Text(f"Prepared: {prep.date}", style="dim"),
        Text(""),
    ]
    if prep.activity_summary:
        body.append(Text("Sprint work", style=f"bold {accent}"))
        body.append(Text(f"  {prep.activity_summary}"))
        body.append(Text(""))
    _rich_section(body, "Carried-over action items", prep.carried_action_items, accent, marker="↺")
    _rich_section(body, "Talking points", prep.talking_points, accent)
    _rich_section(body, "Feedback to give", prep.feedback, accent)
    _rich_section(body, "Goals to align on", prep.goals, accent)
    _rich_section(body, "Gaps observed", prep.gaps, accent)
    _rich_section(body, "Areas to improve", prep.improvements, accent)
    _rich_notices(body, prep.warnings)
    return Group(*body)


# ---------------------------------------------------------------------------
# 1:1 Completion
# ---------------------------------------------------------------------------


def format_completion_lines(record: OneOnOneRecord) -> list[str]:
    """Return a completed 1:1 as plain-text lines (the email body + actions)."""
    logger.info("performance render: 1:1 completion (plaintext) — engineer=%s", record.engineer)
    lines = [f"1:1 Completed — {record.engineer}", f"Date: {record.date}", ""]
    if record.email_subject:
        lines += [f"Subject: {record.email_subject}", ""]
    if record.email_summary:
        lines += ["Summary email:", record.email_summary, ""]
    lines += _section_lines("Action items:", record.action_items)
    lines += _section_lines("Highlights:", record.highlights)
    if record.warnings:
        lines += _section_lines("⚠ Notices:", record.warnings)
    return lines


def format_completion_rich(record: OneOnOneRecord, *, accent: str = _ACCENT) -> Group:
    """Return a Rich renderable for the completed 1:1."""
    logger.info("performance render: 1:1 completion (rich) — engineer=%s", record.engineer)
    body: list[Text] = [
        Text(f"1:1 Completed — {record.engineer}", style=f"bold {accent}"),
        Text(f"Date: {record.date}", style="dim"),
        Text(""),
    ]
    if record.email_subject:
        body.append(Text(f"Subject: {record.email_subject}", style="bold"))
        body.append(Text(""))
    if record.email_summary:
        body.append(Text("Summary email", style=f"bold {accent}"))
        for para in record.email_summary.split("\n"):
            body.append(Text(f"  {para}"))
        body.append(Text(""))
    _rich_section(body, "Action items", record.action_items, accent, marker="☐")
    _rich_section(body, "Highlights", record.highlights, accent)
    _rich_notices(body, record.warnings)
    return Group(*body)


# ---------------------------------------------------------------------------
# 6-month Review
# ---------------------------------------------------------------------------


def format_review_lines(review: SixMonthReview) -> list[str]:
    """Return a 6-month review as plain-text lines."""
    logger.info("performance render: 6-month review (plaintext) — engineer=%s", review.engineer)
    lines = [
        f"6-Month Performance Review — {review.engineer}",
        f"Period: {review.period_start or '?'} to {review.period_end or '?'}",
        "",
    ]
    if review.overall:
        lines += ["Overall:", f"  {review.overall}", ""]
    lines += _section_lines("Strengths:", review.strengths)
    lines += _section_lines("Achievements:", review.achievements)
    lines += _section_lines("Areas for improvement:", review.areas_for_improvement)
    lines += _section_lines("Goals for next period:", review.goals)
    if review.framework_used:
        lines += [f"(Framework: {review.framework_used})", ""]
    if review.warnings:
        lines += _section_lines("⚠ Notices:", review.warnings)
    return lines


def format_review_rich(review: SixMonthReview, *, accent: str = _ACCENT) -> Group:
    """Return a Rich renderable for the 6-month review."""
    logger.info("performance render: 6-month review (rich) — engineer=%s", review.engineer)
    body: list[Text] = [
        Text(f"6-Month Review — {review.engineer}", style=f"bold {accent}"),
        Text(f"Period: {review.period_start or '?'} to {review.period_end or '?'}", style="dim"),
        Text(""),
    ]
    if review.overall:
        body.append(Text("Overall", style=f"bold {accent}"))
        body.append(Text(f"  {review.overall}"))
        body.append(Text(""))
    _rich_section(body, "Strengths", review.strengths, accent)
    _rich_section(body, "Achievements", review.achievements, accent)
    _rich_section(body, "Areas for improvement", review.areas_for_improvement, accent)
    _rich_section(body, "Goals for next period", review.goals, accent)
    _rich_notices(body, review.warnings)
    return Group(*body)


# ---------------------------------------------------------------------------
# Rich section builders (shared)
# ---------------------------------------------------------------------------


def _rich_section(
    body: list[Text], title: str, items: tuple[str, ...] | list[str], accent: str, *, marker: str = "•"
) -> None:
    if not items:
        return
    body.append(Text(title, style=f"bold {accent}"))
    for it in items:
        if not it:
            continue
        row = Text()
        row.append(f"  {marker} ", style="dim")
        row.append(it)
        body.append(row)
    body.append(Text(""))


def _rich_notices(body: list[Text], warnings: tuple[str, ...]) -> None:
    if not warnings:
        return
    body.append(Text("⚠ Notices", style="bold rgb(220,180,60)"))
    for w in warnings:
        body.append(Text(f"  - {w}", style="rgb(220,180,60)"))
    body.append(Text(""))
