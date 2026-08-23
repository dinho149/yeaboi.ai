"""Plaintext rendering for Performance artifacts.

The one surface that needs an artifact as flat text: the 1:1 summary email body
(``performance/delivery.py``). ``coverage_lines`` is shared with the Markdown and
HTML exports so all three name a source the same way.

The styled rendering the TUI page and the CLI both draw lives in
``ui/shared/_performance_rows.py``, which renders the artifact object. It used to
be duplicated here as a second set of ``format_*_rich`` builders that hardcoded
the mode accent as a colour literal — with a comment conceding it had to be kept
in sync with the theme by hand.

# See docs: "Daily Standup" — delivery, TUI page
"""

from __future__ import annotations

import logging

from yeaboi.agent.state import OneOnOnePrep, OneOnOneRecord, SixMonthReview

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _bullets(items: tuple[str, ...] | list[str]) -> list[str]:
    return [f"  • {it}" for it in items if it]


def coverage_lines(artifact) -> tuple[str, ...]:
    """One readable row per evidence source: what was scanned, and what was not.

    Rendered on every artifact so a reader can tell a quiet period from an
    unscanned one. An artifact stored before evidence coverage existed has none,
    and simply shows nothing.
    """
    return tuple(
        f"{source} — {state.replace('_', ' ')}: {detail}"
        for source, state, detail in getattr(artifact, "evidence_coverage", ()) or ()
    )


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
    lines += _section_lines("Evidence coverage:", coverage_lines(prep))
    if prep.warnings:
        lines += _section_lines("⚠ Notices:", prep.warnings)
    return [ln for ln in lines]


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
    lines += _section_lines("Evidence coverage:", coverage_lines(review))
    if review.framework_used:
        lines += [f"(Framework: {review.framework_used})", ""]
    if review.warnings:
        lines += _section_lines("⚠ Notices:", review.warnings)
    return lines
