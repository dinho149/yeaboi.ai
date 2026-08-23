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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from yeaboi.timeparse import parse_date

logger = logging.getLogger(__name__)

# Confidence buckets (percent of ideal burn achieved).
_ON_TRACK_MIN = 90
_AT_RISK_MIN = 70

# Trend thresholds over previous standups' recorded pcts.
_TREND_STEADY_BAND = 2  # |delta| ≤ this vs the last standup reads as "steady"
_DECLINE_STREAK_MIN = 3  # consecutive strict declines (ending today) before we dampen
_DECLINE_DAMPEN = 0.9  # sustained slide → today's pct is knocked down 10%

TREND_IMPROVING = "improving"
TREND_STEADY = "steady"
TREND_DECLINING = "declining"

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
    confidence_delta: int = 0  # final pct minus the previous standup's pct (0 without usable history)
    confidence_trend: str = ""  # TREND_* constant, or "" when there is no usable history


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
        return parse_date(value[:10])
    except (ValueError, TypeError):
        return None


def _trend_points(history: Sequence[Mapping], today: date) -> list[int]:
    """Usable previous-standup pcts, oldest→newest, from ``StandupStore.get_history`` rows.

    Filters: status success/partial only, ``standup_date`` strictly before
    ``today`` (a same-day earlier rerun is not "the previous standup"), and
    pct > 0 — "Insufficient data" runs record 0, and letting them into the
    trend would fabricate a collapse/recovery around a capacity-less day.
    Rows arrive newest-first; same-date reruns dedupe keeping the newest.
    """
    seen_dates: set[str] = set()
    newest_first: list[tuple[str, int]] = []
    for row in history:
        if str(row.get("status") or "") not in ("success", "partial"):
            continue
        day = str(row.get("standup_date") or "")
        parsed = _parse_date(day)
        if parsed is None or parsed >= today:
            continue
        try:
            pct = int(row.get("confidence_pct") or 0)
        except (TypeError, ValueError):
            continue
        if pct <= 0 or day in seen_dates:
            continue
        seen_dates.add(day)
        newest_first.append((day, pct))
    return [pct for _day, pct in reversed(newest_first)]


def _decline_streak(pcts: Sequence[int]) -> int:
    """Number of consecutive strict day-over-day drops ending at the last element."""
    streak = 0
    for i in range(len(pcts) - 1, 0, -1):
        if pcts[i] < pcts[i - 1]:
            streak += 1
        else:
            break
    return streak


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
    history: Sequence[Mapping] = (),
) -> SprintProgress:
    """Compute sprint day + confidence from sprint dates, burn-down, and prior standups.

    Args:
        start_date: sprint start (ISO). Empty → "insufficient data".
        sprint_length_weeks: sprint length; total working days = weeks * 5 (minus holidays).
        capacity_points: total points committed for the sprint.
        completed_points: points marked Done so far.
        activity_count: number of recent-activity items detected (drives the silence penalty).
        today: override for testing (defaults to date.today()).
        holidays: set of holiday dates to exclude from working-day counts.
        history: previous runs' metadata rows (``StandupStore.get_history`` shape,
            newest-first). Feeds the trend: today's number is still burn-down
            arithmetic, but a sustained slide across standups dampens it and the
            rationale explains the day-over-day movement.
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

    # Trend vs previous standups: today's pct stays burn-down arithmetic, but a
    # sustained slide (3+ strict drops in a row, counting today) dampens it —
    # momentum is signal the single-day snapshot can't see. Never boosts.
    trend = ""
    delta = 0
    trend_note = ""
    points = _trend_points(history, today)
    if points:
        streak = _decline_streak([*points, pct])
        if streak >= _DECLINE_STREAK_MIN:
            pct = max(0, int(round(pct * _DECLINE_DAMPEN)))
            trend_note = f" Confidence has declined {streak} standups in a row."
        # Delta uses the final (post-dampen) pct so the displayed movement
        # always matches the displayed number.
        delta = pct - points[-1]
        if abs(delta) <= _TREND_STEADY_BAND:
            trend = TREND_STEADY
        elif delta > 0:
            trend = TREND_IMPROVING
            trend_note = f" Up {delta} pts since the last standup."
        else:
            trend = TREND_DECLINING
            if not trend_note:  # the streak sentence already explains the slide
                trend_note = f" Down {abs(delta)} pts since the last standup."

    if pct >= _ON_TRACK_MIN:
        label = LABEL_ON_TRACK
    elif pct >= _AT_RISK_MIN:
        label = LABEL_AT_RISK
    else:
        label = LABEL_BEHIND

    rationale = (
        f"Day {sprint_day} of {total_days}: {completed_points:.0f} of ~{ideal_points:.0f} "
        f"ideal points burned ({pct}%).{silence_note}{trend_note}"
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
        confidence_delta=delta,
        confidence_trend=trend,
    )
