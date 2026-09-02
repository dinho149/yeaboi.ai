"""Rich rendering of a WeeklyReview for the CLI (mirrors reporting/render.py)."""

from __future__ import annotations

from rich.console import Group
from rich.text import Text

from yeaboi.agent.state import ReviewAction, WeeklyReview


def _accent() -> str:
    # The Solo theme's accent, read lazily: the UI package is heavier than this
    # module and the CLI already has it loaded by the time it renders.
    from yeaboi.ui.shared._components import SOLO_THEME

    return SOLO_THEME.accent


_STATUS_GLYPH = {"pending": "○", "done": "●", "dropped": "✕", "carried": "→"}


def _action(action: ReviewAction) -> str:
    glyph = _STATUS_GLYPH.get(action.status, "○")
    tag = f"  (from {action.week_label})" if action.origin == "carryover" and action.week_label else ""
    return f"{glyph} {action.text}{tag}"


def format_review_rich(review: WeeklyReview, *, accent: str = "") -> Group:
    """The review as a Rich renderable for the CLI."""
    accent = accent or _accent()
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
