"""Markdown export for a WeeklyReview.

Markdown only: the review is a personal document, and the HTML/share path
would need a React export component in yeaboi-frontend (the agentwatch
precedent). Files land under the Solo export dir, one per ISO week, so a
re-run of the same week overwrites and the latest wins.
"""

from __future__ import annotations

import logging
from pathlib import Path

from yeaboi.agent.state import ReviewAction, WeeklyReview

logger = logging.getLogger(__name__)

_STATUS_GLYPH = {"pending": "[ ]", "done": "[x]", "dropped": "[-]", "carried": "[>]"}


def _title(review: WeeklyReview) -> str:
    who = review.project_name or "Solo"
    return f"Weekly Review — {who} — {review.week_label}"


def _action_line(action: ReviewAction) -> str:
    glyph = _STATUS_GLYPH.get(action.status, "[ ]")
    tag = f" _(from {action.week_label})_" if action.origin == "carryover" and action.week_label else ""
    return f"- {glyph} {action.text}{tag}"


def build_weekly_review_markdown(review: WeeklyReview) -> str:
    lines = [f"# {_title(review)}", "", f"{review.week_start} → {review.week_end}"]
    if review.my_name:
        lines.append(f"By {review.my_name}")
    lines += ["", f"**Against the plan:** {review.plan_line or 'no verdict'}", ""]
    if review.summary:
        lines += ["## Summary", "", review.summary, ""]
    if review.went_well:
        lines += ["## What went well", "", *(f"- {t}" for t in review.went_well), ""]
    if review.to_change:
        lines += ["## What to change", "", *(f"- {t}" for t in review.to_change), ""]
    if review.actions:
        lines += ["## Actions for next week", "", *(_action_line(a) for a in review.actions), ""]
    if review.carried_actions:
        lines += ["## Carried from last week", "", *(_action_line(a) for a in review.carried_actions), ""]
    if review.standup_lines:
        lines += ["## Standups", "", *(f"- {t}" for t in review.standup_lines), ""]
    if review.delivered_items:
        lines += ["## Delivered", "", *(f"- {i.key} {i.title}".rstrip() for i in review.delivered_items), ""]
    if review.warnings:
        lines += ["## Notices", "", *(f"- {w}" for w in review.warnings), ""]
    if review.generated_at:
        lines.append(f"_Generated {review.generated_at}_")
    return "\n".join(lines).rstrip() + "\n"


def export_weekly_review(review: WeeklyReview) -> dict[str, Path]:
    """Write the review as Markdown; returns ``{"markdown": path}``."""
    from yeaboi.paths import get_solo_export_dir

    out_dir = get_solo_export_dir(review.project_name or "solo")
    md_path = out_dir / f"weekly-review-{review.week_label or 'undated'}.md"
    md_path.write_text(build_weekly_review_markdown(review), encoding="utf-8")
    logger.info("Weekly review exported: %s", md_path)
    return {"markdown": md_path}
