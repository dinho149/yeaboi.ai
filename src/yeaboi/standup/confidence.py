"""Deterministic sprint-day and confidence scoring for Daily Standup mode.

No LLM is involved here — confidence is pure arithmetic over the sprint's ideal
burn-down, so it's cheap, fast, and unit-testable. The engine calls compute()
and drops the result straight onto the StandupReport.

Model:
- Sprint day = working days elapsed since the sprint start (Mon-Fri, minus
  bank holidays), 1-indexed, capped at the sprint's total working days.
- Confidence = actual completed points vs the *ideal linear burn* for the day.
  On day D of a T-day sprint with capacity C, you'd ideally have burned
  C * D / T points. completed / ideal → a ratio, bucketed into On track /
  At risk / Behind. A dead-quiet sprint (no recent activity past day 1) is
  nudged down because silence usually means stalled work.

# See docs: "Daily Standup" — sprint-day & confidence
# See docs: "Scrum Standards" — capacity planning, velocity
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# Confidence buckets (percent of ideal burn achieved).
_ON_TRACK_MIN = 90
_AT_RISK_MIN = 70

LABEL_ON_TRACK = "On track"
LABEL_AT_RISK = "At risk"
LABEL_BEHIND = "Behind"
LABEL_INSUFFICIENT = "Insufficient data"


@dataclass(frozen=True)
class SprintProgress:
    """Result of a confidence computation — mirrors the StandupReport fields."""

    sprint_day: int = 0
    sprint_total_days: int = 0
    confidence_pct: int = 0
    confidence_label: str = LABEL_INSUFFICIENT
    confidence_rationale: str = ""


def working_days_between(start: date, end: date, holidays: set[date] | None = None) -> int:
    """Count Mon-Fri days in [start, end] inclusive, excluding ``holidays``.

    Returns 0 when end < start.
    """
    if end < start:
        return 0
    holidays = holidays or set()
    count = 0
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in holidays:  # Mon=0 .. Fri=4
            count += 1
        d += timedelta(days=1)
    return count


def _parse_date(value: str) -> date | None:
    """Parse a YYYY-MM-DD (or ISO datetime) string to a date, or None."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def compute(
    *,
    sprint_name: str = "",
    start_date: str = "",
    sprint_length_weeks: int = 2,
    capacity_points: float = 0.0,
    completed_points: float = 0.0,
    activity_count: int = 0,
    today: date | None = None,
    holidays: set[date] | None = None,
) -> SprintProgress:
    """Compute sprint day + confidence from sprint dates and burn-down.

    Args:
        start_date: sprint start (ISO). Empty → "insufficient data".
        sprint_length_weeks: sprint length; total working days = weeks * 5 (minus holidays).
        capacity_points: total points committed for the sprint.
        completed_points: points marked Done so far.
        activity_count: number of recent-activity items detected (drives the silence penalty).
        today: override for testing (defaults to date.today()).
        holidays: set of holiday dates to exclude from working-day counts.
    """
    today = today or date.today()
    holidays = holidays or set()

    start = _parse_date(start_date)
    if start is None:
        return SprintProgress(
            confidence_rationale="No active sprint start date available — cannot estimate progress.",
        )

    # Total working days across the whole sprint (weeks * 5, minus holidays in range).
    sprint_end = start + timedelta(days=sprint_length_weeks * 7 - 1)
    total_days = working_days_between(start, sprint_end, holidays)
    if total_days <= 0:
        return SprintProgress(
            confidence_rationale="Sprint length is zero — cannot estimate progress.",
        )

    # Working days elapsed through today, clamped into [1, total_days].
    elapsed = working_days_between(start, min(today, sprint_end), holidays)
    sprint_day = max(1, min(elapsed, total_days))

    # Without a committed capacity we can still report the sprint day, but not a
    # burn-based confidence — say so rather than inventing a number.
    if capacity_points <= 0:
        return SprintProgress(
            sprint_day=sprint_day,
            sprint_total_days=total_days,
            confidence_label=LABEL_INSUFFICIENT,
            confidence_rationale=(
                f"Day {sprint_day} of {total_days}. No committed sprint capacity on record, "
                "so burn-down confidence can't be computed."
            ),
        )

    ideal_points = capacity_points * sprint_day / total_days
    # Ratio of achieved to ideal; being ahead is capped at 1.0 (100%).
    ratio = 1.0 if ideal_points <= 0 else completed_points / ideal_points
    pct = int(round(min(ratio, 1.0) * 100))

    # Silence penalty: past the first day, zero recent activity usually means
    # stalled work — knock confidence down and note it.
    silence_note = ""
    if sprint_day > 1 and activity_count == 0:
        pct = int(round(pct * 0.7))
        silence_note = " No recent activity detected — work may be stalled."

    if pct >= _ON_TRACK_MIN:
        label = LABEL_ON_TRACK
    elif pct >= _AT_RISK_MIN:
        label = LABEL_AT_RISK
    else:
        label = LABEL_BEHIND

    rationale = (
        f"Day {sprint_day} of {total_days}: {completed_points:.0f} of ~{ideal_points:.0f} "
        f"ideal points burned ({pct}%).{silence_note}"
    )
    logger.info(
        "confidence: sprint=%r day=%d/%d completed=%.1f ideal=%.1f pct=%d label=%s",
        sprint_name,
        sprint_day,
        total_days,
        completed_points,
        ideal_points,
        pct,
        label,
    )
    return SprintProgress(
        sprint_day=sprint_day,
        sprint_total_days=total_days,
        confidence_pct=pct,
        confidence_label=label,
        confidence_rationale=rationale,
    )
