"""Rendering for a WeeklyReview — plaintext lines for the TUI, Rich for the CLI.

One layout, two forms, so no surface re-implements it (mirrors reporting/render.py).
"""

from __future__ import annotations

import logging

from rich.console import Group
from rich.text import Text

from yeaboi.agent.state import ReviewAction, WeeklyReview

logger = logging.getLogger(__name__)

_ACCENT = "rgb(210,168,80)"  # the Solo accent — keep in sync with SOLO_THEME

_STATUS_GLYPH = {"pending": "○", "done": "●", "dropped": "✕", "carried": "→"}


def _action(action: ReviewAction) -> str:
    glyph = _STATUS_GLYPH.get(action.status, "○")
    tag = f"  (from {action.week_label})" if action.origin == "carryover" and action.week_label else ""
    return f"{glyph} {action.text}{tag}"


def format_review_lines(review: WeeklyReview) -> list[str]:
    """The review as plain-text lines (no ANSI)."""
    logger.info("weekly review render: week=%s %d action(s)", review.week_label, len(review.actions))
    lines = [
        f"Weekly Review — {review.project_name or 'Solo'} — {review.week_label}",
        f"{review.week_start} to {review.week_end}",
        "",
        f"Against the plan: {review.plan_line or 'no verdict'}",
        "",
    ]
    if review.summary:
        lines += ["Summary:", f"  {review.summary}", ""]
    for title, items in (("What went well:", review.went_well), ("What to change:", review.to_change)):
        if items:
            lines += [title, *(f"  • {t}" for t in items), ""]
    if review.actions:
        lines += ["Actions for next week:", *(f"  {_action(a)}" for a in review.actions), ""]
    if review.carried_actions:
        lines += ["Carried from last week:", *(f"  {_action(a)}" for a in review.carried_actions), ""]
    if review.warnings:
        lines += ["Notices:", *(f"  ! {w}" for w in review.warnings), ""]
    return lines[:-1] if lines and lines[-1] == "" else lines


def format_review_rich(review: WeeklyReview, *, accent: str = _ACCENT) -> Group:
    """The review as a Rich renderable for the CLI."""
    body: list[Text] = [
        Text(f"Weekly Review — {review.project_name or 'Solo'} — {review.week_label}", style=f"bold {accent}"),
        Text(f"{review.week_start} to {review.week_end}", style="dim"),
        Text(""),
        Text("Against the plan: ", style="bold") + Text(review.plan_line or "no verdict"),
        Text(""),
    ]
    if review.summary:
        body += [Text("Summary", style=f"bold {accent}"), Text(review.summary), Text("")]
    for title, items in (("What went well", review.went_well), ("What to change", review.to_change)):
        if items:
            body.append(Text(title, style=f"bold {accent}"))
            body += [Text(f"  • {t}") for t in items]
            body.append(Text(""))
    for title, actions in (
        ("Actions for next week", review.actions),
        ("Carried from last week", review.carried_actions),
    ):
        if actions:
            body.append(Text(title, style=f"bold {accent}"))
            body += [Text(f"  {_action(a)}") for a in actions]
            body.append(Text(""))
    if review.warnings:
        body.append(Text("Notices", style="bold yellow"))
        body += [Text(f"  ! {w}", style="yellow") for w in review.warnings]
    return Group(*body)
