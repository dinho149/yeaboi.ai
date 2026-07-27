"""Unit tests for deterministic sprint-day + confidence scoring."""

from datetime import date

from yeaboi.standup import confidence
from yeaboi.standup.confidence import (
    LABEL_AT_RISK,
    LABEL_BEHIND,
    LABEL_INSUFFICIENT,
    LABEL_ON_TRACK,
    working_days_between,
)


class TestWorkingDaysBetween:
    def test_full_week(self):
        # Mon 2026-07-06 .. Fri 2026-07-10 = 5 working days
        assert working_days_between(date(2026, 7, 6), date(2026, 7, 10)) == 5

    def test_excludes_weekend(self):
        # Mon .. Sun spans a weekend → still 5
        assert working_days_between(date(2026, 7, 6), date(2026, 7, 12)) == 5

    def test_excludes_holidays(self):
        holidays = {date(2026, 7, 8)}  # Wednesday off
        assert working_days_between(date(2026, 7, 6), date(2026, 7, 10), holidays) == 4

    def test_end_before_start(self):
        assert working_days_between(date(2026, 7, 10), date(2026, 7, 6)) == 0


class TestCompute:
    def test_no_start_date_is_insufficient(self):
        r = confidence.compute(start_date="", capacity_points=20)
        assert r.confidence_label == LABEL_INSUFFICIENT
        assert r.sprint_day == 0

    def test_no_capacity_reports_day_but_not_confidence(self):
        # Sprint started Mon; today is Wed of the same week → day 3 of 10.
        r = confidence.compute(
            start_date="2026-07-06",
            sprint_length_weeks=2,
            capacity_points=0,
            today=date(2026, 7, 8),
        )
        assert r.sprint_day == 3
        assert r.sprint_total_days == 10
        assert r.confidence_label == LABEL_INSUFFICIENT

    def test_on_track(self):
        # Day 5 of 10, capacity 20 → ideal = 10; completed 10 → 100% On track.
        r = confidence.compute(
            start_date="2026-07-06",
            sprint_length_weeks=2,
            capacity_points=20,
            completed_points=10,
            activity_count=5,
            today=date(2026, 7, 10),
        )
        assert r.sprint_day == 5
        assert r.confidence_pct == 100
        assert r.confidence_label == LABEL_ON_TRACK

    def test_at_risk(self):
        # Day 5 of 10, ideal 10, completed 8 → 80% At risk.
        r = confidence.compute(
            start_date="2026-07-06",
            sprint_length_weeks=2,
            capacity_points=20,
            completed_points=8,
            activity_count=3,
            today=date(2026, 7, 10),
        )
        assert r.confidence_pct == 80
        assert r.confidence_label == LABEL_AT_RISK

    def test_behind(self):
        # Day 5 of 10, ideal 10, completed 4 → 40% Behind.
        r = confidence.compute(
            start_date="2026-07-06",
            sprint_length_weeks=2,
            capacity_points=20,
            completed_points=4,
            activity_count=2,
            today=date(2026, 7, 10),
        )
        assert r.confidence_pct == 40
        assert r.confidence_label == LABEL_BEHIND

    def test_ahead_is_capped_at_100(self):
        r = confidence.compute(
            start_date="2026-07-06",
            sprint_length_weeks=2,
            capacity_points=20,
            completed_points=18,
            activity_count=5,
            today=date(2026, 7, 8),
        )
        assert r.confidence_pct == 100
        assert r.confidence_label == LABEL_ON_TRACK

    def test_silence_penalty_past_day_one(self):
        # Day 5, would be 100% on track, but zero activity → *0.7 = 70.
        r = confidence.compute(
            start_date="2026-07-06",
            sprint_length_weeks=2,
            capacity_points=20,
            completed_points=10,
            activity_count=0,
            today=date(2026, 7, 10),
        )
        assert r.confidence_pct == 70
        assert "No recent activity" in r.confidence_rationale

    def test_holidays_reduce_total_days(self):
        holidays = {date(2026, 7, 8)}
        r = confidence.compute(
            start_date="2026-07-06",
            sprint_length_weeks=2,
            capacity_points=20,
            completed_points=5,
            activity_count=1,
            today=date(2026, 7, 7),
            holidays=holidays,
        )
        # 2 sprint weeks = 10 weekdays, minus 1 holiday in range = 9 total.
        assert r.sprint_total_days == 9


def _hist(*rows):
    """History rows newest-first, mirroring StandupStore.get_history output."""
    return [
        {"standup_date": d, "confidence_pct": p, "status": s, "sprint_day": i, "run_at": f"{d}T09:00:00", "id": i}
        for i, (d, p, s) in enumerate(rows)
    ]


def _compute(history, *, completed_points=10.0, activity_count=5, today=date(2026, 7, 10)):
    # start 2026-07-06 → day 5 of 10 on 07-10; 10/10 ideal → base pct 100.
    return confidence.compute(
        start_date="2026-07-06",
        sprint_length_weeks=2,
        capacity_points=20,
        completed_points=completed_points,
        activity_count=activity_count,
        today=today,
        history=history,
    )


class TestTrend:
    def test_no_history_unchanged(self):
        r = _compute(())
        assert r.confidence_trend == ""
        assert r.confidence_delta == 0
        assert "since the last standup" not in r.confidence_rationale

    def test_steady_band(self):
        r = _compute(_hist(("2026-07-09", 99, "success")))
        assert r.confidence_trend == "steady"
        assert r.confidence_delta == 1
        assert "since the last standup" not in r.confidence_rationale

    def test_improving_adds_rationale(self):
        r = _compute(_hist(("2026-07-09", 80, "success")))
        assert r.confidence_trend == "improving"
        assert r.confidence_delta == 20
        assert "Up 20 pts since the last standup." in r.confidence_rationale

    def test_single_decline_no_dampen(self):
        # Base pct 75 (15/20 ideal … completed 7.5? use completed to get 75): 7.5/10 → 75.
        r = _compute(_hist(("2026-07-09", 90, "success")), completed_points=7.5)
        assert r.confidence_pct == 75  # no damping on a single drop
        assert r.confidence_trend == "declining"
        assert r.confidence_delta == -15
        assert "Down 15 pts since the last standup." in r.confidence_rationale

    def test_three_drop_streak_dampens(self):
        history = _hist(
            ("2026-07-09", 80, "success"),
            ("2026-07-08", 85, "success"),
            ("2026-07-07", 90, "success"),
        )
        r = _compute(history, completed_points=7.5)  # base 75 < 80 → third straight drop
        assert r.confidence_pct == 68  # 75 * 0.9 = 67.5 → 68
        assert "Confidence has declined 3 standups in a row." in r.confidence_rationale
        # Delta reflects the displayed (post-dampen) number, one trend sentence only.
        assert r.confidence_delta == 68 - 80
        assert r.confidence_trend == "declining"
        assert "Down " not in r.confidence_rationale

    def test_streak_broken_by_rise_no_dampen(self):
        history = _hist(
            ("2026-07-09", 80, "success"),
            ("2026-07-08", 78, "success"),  # rose 78→80: streak resets
            ("2026-07-07", 90, "success"),
        )
        r = _compute(history, completed_points=7.5)
        assert r.confidence_pct == 75

    def test_trend_points_filters(self):
        history = _hist(
            ("2026-07-10", 40, "success"),  # today — excluded (same-day rerun)
            ("2026-07-09", 95, "failed"),  # failed run — excluded
            ("2026-07-09", 80, "success"),  # kept for 07-09
            ("2026-07-08", 0, "success"),  # pct 0 (insufficient data) — excluded
            ("2026-07-07", 90, "success"),
        )
        points = confidence._trend_points(history, date(2026, 7, 10))
        assert points == [90, 80]

    def test_same_date_rerun_dedupes_newest_wins(self):
        history = _hist(
            ("2026-07-09", 85, "success"),  # newest rerun for 07-09
            ("2026-07-09", 60, "success"),
        )
        assert confidence._trend_points(history, date(2026, 7, 10)) == [85]

    def test_insufficient_data_paths_untouched(self):
        r = confidence.compute(start_date="", history=_hist(("2026-07-09", 80, "success")))
        assert r.confidence_label == LABEL_INSUFFICIENT
        assert r.confidence_trend == ""
        assert r.confidence_delta == 0
