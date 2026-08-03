"""Tests for capacity planning helpers — net velocity computation and bank holiday detection.

Sprint selection and capacity planning were consolidated into the intake
questionnaire (Phase 6). These tests cover the extracted helpers:
- _compute_net_velocity — math for net velocity after deductions
- _extract_capacity_deductions — parsing Q27-Q30 answers
- _detect_bank_holidays_for_window — bank holiday detection
- _build_velocity_breakdown — transparent velocity breakdown
- _fetch_jira_velocity — thin wrapper around jira_fetch_velocity @tool
- _parse_velocity_override — velocity override parsing
- _parse_date_dmy — DD/MM/YYYY date parsing
- _count_working_days — weekday counting
- _assign_leave_to_sprints — PTO mapping to sprint windows
"""

import json
from unittest.mock import patch

from yeaboi.agent.nodes import (
    _assign_holidays_to_sprints,
    _assign_leave_to_sprints,
    _build_velocity_breakdown,
    _compute_net_velocity,
    _compute_per_sprint_velocities,
    _count_working_days,
    _detect_bank_holidays_for_window,
    _extract_capacity_deductions,
    _fetch_active_sprint_number,
    _fetch_jira_velocity,
    _fetch_linear_active_cycle,
    _fetch_linear_velocity,
    _fetch_tracker_velocity,
    _is_linear_configured,
    _is_tracker_configured,
    _parse_date_dmy,
    _parse_velocity_override,
    _prepare_bank_holiday_choices,
)
from yeaboi.agent.state import QuestionnaireState


class TestComputeNetVelocity:
    """Tests for _compute_net_velocity math."""

    def test_no_deductions_returns_gross_velocity(self):
        """With zero deductions and zero discovery, net velocity equals gross velocity."""
        result = _compute_net_velocity(
            team_size=3,
            velocity_per_sprint=15,
            sprint_length_weeks=2,
            target_sprints=4,
            bank_holiday_days=0,
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
            discovery_pct=0,
        )
        assert result == 15

    def test_known_inputs(self):
        """Verify the net velocity math with known inputs.

        team_size=3, sprint_length_weeks=2 (10 working days), target_sprints=4
        velocity_per_sprint=15, unplanned=10%, discovery=5% (default)
        bank_holidays=2, planned_leave=5, onboarding=1
        gross_days = 3 * 10 * 4 = 120, ktlo_days = 0
        available_days = 120
        unplanned = 120 * 0.10 = 12, onboarding = 1 * 10 = 10
        total_leave = 2 + 5 + 12 + 10 = 29
        after_deductions = max(120 - 29, 0) = 91
        discovery_days = 91 * 0.05 = 4.55
        net_days = 91 - 4.55 = 86.45
        net_ratio = 86.45 / 120 ≈ 0.72042
        net_velocity = round(0.72042 * 15) = round(10.806) = 11
        """
        result = _compute_net_velocity(
            team_size=3,
            velocity_per_sprint=15,
            sprint_length_weeks=2,
            target_sprints=4,
            bank_holiday_days=2,
            planned_leave_days=5,
            unplanned_leave_pct=10,
            onboarding_engineer_sprints=1,
        )
        assert result == 11

    def test_never_below_one(self):
        """Net velocity should never go below 1 even with huge deductions."""
        result = _compute_net_velocity(
            team_size=1,
            velocity_per_sprint=5,
            sprint_length_weeks=2,
            target_sprints=1,
            bank_holiday_days=100,
            planned_leave_days=500,
            unplanned_leave_pct=50,
            onboarding_engineer_sprints=10,
        )
        assert result >= 1

    def test_zero_team_size_returns_gross(self):
        """Zero team size (gross_days=0) returns gross velocity."""
        result = _compute_net_velocity(
            team_size=0,
            velocity_per_sprint=10,
            sprint_length_weeks=2,
            target_sprints=4,
            bank_holiday_days=0,
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
        )
        assert result == 10

    def test_ktlo_engineers_reduce_velocity(self):
        """KTLO engineers should reduce available capacity."""
        # With 1 KTLO engineer out of 3, ~33% of capacity is removed before deductions
        result_with_ktlo = _compute_net_velocity(
            team_size=3,
            velocity_per_sprint=15,
            sprint_length_weeks=2,
            target_sprints=4,
            bank_holiday_days=0,
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
            ktlo_engineers=1,
            discovery_pct=0,
        )
        result_without = _compute_net_velocity(
            team_size=3,
            velocity_per_sprint=15,
            sprint_length_weeks=2,
            target_sprints=4,
            bank_holiday_days=0,
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
            ktlo_engineers=0,
            discovery_pct=0,
        )
        assert result_with_ktlo < result_without
        assert result_with_ktlo == 10  # 2/3 * 15 = 10

    def test_discovery_pct_reduces_velocity(self):
        """Discovery percentage should reduce net velocity."""
        result_with_discovery = _compute_net_velocity(
            team_size=3,
            velocity_per_sprint=20,
            sprint_length_weeks=2,
            target_sprints=4,
            bank_holiday_days=0,
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
            ktlo_engineers=0,
            discovery_pct=10,
        )
        result_without = _compute_net_velocity(
            team_size=3,
            velocity_per_sprint=20,
            sprint_length_weeks=2,
            target_sprints=4,
            bank_holiday_days=0,
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
            ktlo_engineers=0,
            discovery_pct=0,
        )
        assert result_with_discovery < result_without
        assert result_with_discovery == 18  # 90% of 20 = 18

    def test_backward_compat_defaults(self):
        """Default ktlo=0 and discovery=5 should be backward compatible."""
        # With discovery_pct=5 (default), a small reduction is applied
        result = _compute_net_velocity(
            team_size=3,
            velocity_per_sprint=15,
            sprint_length_weeks=2,
            target_sprints=4,
            bank_holiday_days=0,
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
        )
        # 95% of 15 = 14.25 → rounds to 14
        assert result == 14


class TestBuildVelocityBreakdown:
    """Tests for _build_velocity_breakdown output format."""

    def test_returns_net_velocity_and_text(self):
        """Should return a tuple of (int, str)."""
        net_vel, breakdown = _build_velocity_breakdown(
            velocity_per_sprint=20,
            velocity_source="estimated",
            team_size=3,
            sprint_length_weeks=2,
            target_sprints=4,
            bank_holiday_days=0,
            planned_leave_days=0,
            unplanned_leave_pct=10,
            onboarding_engineer_sprints=0,
        )
        assert isinstance(net_vel, int)
        assert isinstance(breakdown, str)
        assert "Recommended Velocity" in breakdown
        assert "Net velocity:" in breakdown

    def test_shows_gross_velocity_source(self):
        """Breakdown should indicate where the gross velocity came from."""
        _, breakdown = _build_velocity_breakdown(
            velocity_per_sprint=18,
            velocity_source="jira",
            team_size=3,
            sprint_length_weeks=2,
            target_sprints=4,
            bank_holiday_days=0,
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
            discovery_pct=0,
        )
        assert "from Jira" in breakdown

    def test_shows_deduction_lines(self):
        """Each non-zero deduction should appear in the breakdown."""
        _, breakdown = _build_velocity_breakdown(
            velocity_per_sprint=20,
            velocity_source="manual",
            team_size=3,
            sprint_length_weeks=2,
            target_sprints=4,
            bank_holiday_days=2,
            planned_leave_days=3,
            unplanned_leave_pct=10,
            onboarding_engineer_sprints=1,
            ktlo_engineers=1,
            discovery_pct=5,
        )
        assert "Bank holidays" in breakdown
        assert "Planned leave" in breakdown
        assert "Unplanned absence" in breakdown
        assert "Onboarding" in breakdown
        assert "KTLO" in breakdown
        assert "Discovery" in breakdown

    def test_zero_deductions_no_lines(self):
        """Zero-value deductions should not appear in the breakdown."""
        _, breakdown = _build_velocity_breakdown(
            velocity_per_sprint=20,
            velocity_source="estimated",
            team_size=3,
            sprint_length_weeks=2,
            target_sprints=4,
            bank_holiday_days=0,
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
            ktlo_engineers=0,
            discovery_pct=0,
        )
        assert "Bank holidays" not in breakdown
        assert "Unplanned" not in breakdown

    def test_zero_team_size_returns_simple(self):
        """With zero team size, breakdown is minimal."""
        net_vel, breakdown = _build_velocity_breakdown(
            velocity_per_sprint=10,
            velocity_source="estimated",
            team_size=0,
            sprint_length_weeks=2,
            target_sprints=4,
            bank_holiday_days=0,
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
        )
        assert net_vel == 10
        assert "Net velocity: 10" in breakdown


class TestFetchJiraVelocity:
    """Tests for _fetch_jira_velocity wrapper — calls jira_fetch_velocity @tool."""

    @patch("yeaboi.tools.jira.jira_fetch_velocity")
    def test_returns_per_dev_velocity(self, mock_tool):
        """Should parse the tool's JSON response into a dict."""
        mock_tool.invoke.return_value = json.dumps({"team_velocity": 25, "jira_team_size": 5, "per_dev_velocity": 5.0})
        result = _fetch_jira_velocity()
        assert result is not None
        assert result["team_velocity"] == 25
        assert result["jira_team_size"] == 5
        assert result["per_dev_velocity"] == 5.0

    @patch("yeaboi.tools.jira.jira_fetch_velocity")
    def test_returns_none_when_not_configured(self, mock_tool):
        """Should return None when tool returns an error string."""
        mock_tool.invoke.return_value = "Error: Jira is not configured."
        result = _fetch_jira_velocity()
        assert result is None

    @patch("yeaboi.tools.jira.jira_fetch_velocity")
    def test_returns_none_on_exception(self, mock_tool):
        """Should return None when tool invocation fails."""
        mock_tool.invoke.side_effect = Exception("fail")
        result = _fetch_jira_velocity()
        assert result is None


class TestParseVelocityOverride:
    """Tests for _parse_velocity_override intent detection."""

    def test_bare_number(self):
        assert _parse_velocity_override("14") == 14

    def test_number_with_pts(self):
        assert _parse_velocity_override("12 pts") == 12

    def test_number_with_points(self):
        assert _parse_velocity_override("15 points") == 15

    def test_number_with_pts_per_sprint(self):
        assert _parse_velocity_override("18 pts/sprint") == 18

    def test_sentence_with_number(self):
        assert _parse_velocity_override("use 20 points per sprint") == 20

    def test_zero_returns_none(self):
        assert _parse_velocity_override("0") is None

    def test_no_number_returns_none(self):
        assert _parse_velocity_override("fast") is None

    def test_whitespace_handling(self):
        assert _parse_velocity_override("  12  ") == 12


class TestExtractCapacityDeductions:
    """Tests for _extract_capacity_deductions parsing Q28-Q30 answers."""

    def test_parses_all_defaults(self):
        """Default answers should produce sensible deduction values."""
        qs = QuestionnaireState(
            answers={
                27: "Fresh start (today)",
                28: "No bank holidays detected",
                29: "10%",
                30: "No engineers onboarding",
            }
        )
        result = _extract_capacity_deductions(qs)
        assert result["capacity_bank_holiday_days"] == 0
        assert result["capacity_planned_leave_days"] == 0
        assert result["capacity_unplanned_leave_pct"] == 10
        assert result["capacity_onboarding_engineer_sprints"] == 0
        assert result["capacity_ktlo_engineers"] == 0
        assert result["capacity_discovery_pct"] == 5

    def test_parses_numeric_answers(self):
        """Numeric answers should be parsed correctly."""
        qs = QuestionnaireState(
            answers={
                27: "Sprint 105",
                28: "2 bank holiday(s)",
                29: "15%",
                30: "2 engineers onboarding",
            },
            _detected_bank_holiday_days=2,
        )
        result = _extract_capacity_deductions(qs)
        assert result["capacity_bank_holiday_days"] == 2
        assert result["capacity_planned_leave_days"] == 0
        assert result["capacity_unplanned_leave_pct"] == 15
        assert result["capacity_onboarding_engineer_sprints"] == 2

    def test_falls_back_to_q28_text_when_no_transient(self):
        """When _detected_bank_holiday_days is 0, parse Q28 answer text."""
        qs = QuestionnaireState(
            answers={28: "3 bank holiday(s)"},
            _detected_bank_holiday_days=0,
        )
        result = _extract_capacity_deductions(qs)
        assert result["capacity_bank_holiday_days"] == 3

    def test_defaults_on_missing_answers(self):
        """Missing answers should use sensible defaults."""
        qs = QuestionnaireState(answers={})
        result = _extract_capacity_deductions(qs)
        assert result["capacity_bank_holiday_days"] == 0
        assert result["capacity_planned_leave_days"] == 0
        assert result["capacity_unplanned_leave_pct"] == 10
        assert result["capacity_onboarding_engineer_sprints"] == 0


class TestDetectBankHolidaysForWindow:
    """Tests for _detect_bank_holidays_for_window helper."""

    @patch("yeaboi.tools.calendar_tools._detect_country_from_locale", return_value=None)
    def test_no_locale_returns_zero(self, mock_locale):
        """When locale can't be detected, return 0 holidays."""
        count, summary = _detect_bank_holidays_for_window(None, 2, 4)
        assert count == 0
        assert "not detected" in summary.lower()

    @patch("yeaboi.tools.calendar_tools._detect_country_from_locale", return_value="GB")
    @patch(
        "yeaboi.tools.calendar_tools.get_bank_holidays_structured",
        return_value=[
            {"date": __import__("datetime").date(2026, 4, 3), "name": "Good Friday", "weekday": "Friday"},
            {"date": __import__("datetime").date(2026, 4, 6), "name": "Easter Monday", "weekday": "Monday"},
        ],
    )
    def test_detects_holidays(self, mock_holidays, mock_locale):
        """Detected holidays should be counted and summarized."""
        count, summary = _detect_bank_holidays_for_window("2026-03-16", 2, 4)
        assert count == 2
        assert "Good Friday" in summary
        assert "Easter Monday" in summary

    @patch("yeaboi.tools.calendar_tools._detect_country_from_locale", return_value="GB")
    @patch("yeaboi.tools.calendar_tools.get_bank_holidays_structured", return_value=[])
    def test_no_holidays_in_window(self, mock_holidays, mock_locale):
        """Zero holidays returns count=0 with appropriate message."""
        count, summary = _detect_bank_holidays_for_window("2026-03-16", 2, 4)
        assert count == 0
        assert "no bank holidays" in summary.lower()


class TestPrepareBankHolidayChoices:
    """Tests for _prepare_bank_holiday_choices using the @tool."""

    @patch("yeaboi.tools.calendar_tools.detect_bank_holidays")
    def test_calls_tool_and_populates_choices_with_holidays(self, mock_tool):
        """When holidays are detected, choices include Accept with summary."""
        mock_tool.func.return_value = (
            "Bank holidays in United Kingdom (GB)\n"
            "Planning window: 2026-03-16 to 2026-05-11\n\n"
            "**2 bank holiday(s) on weekdays:**\n"
            "  - 2026-04-03 (Friday): Good Friday\n"
            "  - 2026-04-06 (Monday): Easter Monday\n\n"
            "Total working days lost to bank holidays: **2**"
        )
        qs = QuestionnaireState(answers={8: "2 weeks", 10: "4 sprints"})
        _prepare_bank_holiday_choices(qs)
        assert qs._detected_bank_holiday_days == 2
        assert len(qs._follow_up_choices[28]) == 3
        assert "Accept" in qs._follow_up_choices[28][0]
        assert "Good Friday" in qs._follow_up_choices[28][0]

    @patch("yeaboi.tools.calendar_tools.detect_bank_holidays")
    def test_calls_tool_and_populates_choices_no_holidays(self, mock_tool):
        """When no holidays detected, choices show 'No bank holidays'."""
        mock_tool.func.return_value = (
            "Bank holidays in United Kingdom (GB)\n"
            "Planning window: 2026-03-16 to 2026-05-11\n\n"
            "No bank holidays fall on weekdays in this planning window.\n\n"
            "Total working days lost to bank holidays: **0**"
        )
        qs = QuestionnaireState(answers={8: "2 weeks", 10: "4 sprints"})
        _prepare_bank_holiday_choices(qs)
        assert qs._detected_bank_holiday_days == 0
        assert len(qs._follow_up_choices[28]) == 2
        assert "No bank holidays detected" in qs._follow_up_choices[28][0]

    @patch("yeaboi.tools.calendar_tools.detect_bank_holidays")
    def test_tool_failure_defaults_to_zero(self, mock_tool):
        """When the tool raises, fall back to 0 holidays."""
        mock_tool.func.side_effect = Exception("holidays lib missing")
        qs = QuestionnaireState(answers={8: "2 weeks", 10: "4 sprints"})
        _prepare_bank_holiday_choices(qs)
        assert qs._detected_bank_holiday_days == 0
        assert 28 in qs._follow_up_choices

    @patch("yeaboi.tools.calendar_tools.detect_bank_holidays")
    def test_recalculates_with_updated_sprint_count(self, mock_tool):
        """Calling again with different Q10 should recalculate bank holidays."""
        mock_tool.func.return_value = (
            "Bank holidays in United Kingdom (GB)\n"
            "Planning window: 2026-03-16 to 2026-03-30\n\n"
            "**1 bank holiday(s) on weekdays:**\n"
            "  - 2026-04-03 (Friday): Good Friday\n\n"
            "Total working days lost to bank holidays: **1**"
        )
        qs = QuestionnaireState(
            answers={8: "2 weeks", 10: "2 sprints"},
            _detected_bank_holiday_days=4,  # stale from a previous call with Q10=6
        )
        _prepare_bank_holiday_choices(qs)
        # Tool should be called — recalculate with updated Q10
        mock_tool.func.assert_called_once()
        assert qs._detected_bank_holiday_days == 1


class TestAssignHolidaysToSprints:
    """Tests for _assign_holidays_to_sprints mapping holidays to sprint indices."""

    def test_maps_holidays_to_correct_sprints(self):
        """Holidays should land in the sprint window they fall in."""
        holidays = [
            {"date": "2026-04-03", "name": "Good Friday", "weekday": "Friday"},
            {"date": "2026-04-06", "name": "Easter Monday", "weekday": "Monday"},
            {"date": "2026-05-04", "name": "May Day", "weekday": "Monday"},
        ]
        # 2-week sprints starting 2026-03-15
        # Sprint 0: Mar 15 – Mar 28, Sprint 1: Mar 29 – Apr 11, Sprint 2: Apr 12 – Apr 25
        # Sprint 3: Apr 26 – May 9
        result = _assign_holidays_to_sprints(holidays, "2026-03-15", 2, 4)
        assert 1 in result  # Good Friday (Apr 3) and Easter Monday (Apr 6) in sprint 1
        assert len(result[1]) == 2
        assert result[1][0]["name"] == "Good Friday"
        assert result[1][1]["name"] == "Easter Monday"
        assert 3 in result  # May Day (May 4) in sprint 3
        assert len(result[3]) == 1
        assert 0 not in result  # No holidays in sprint 0
        assert 2 not in result  # No holidays in sprint 2

    def test_empty_holidays(self):
        """Empty holiday list returns empty dict."""
        result = _assign_holidays_to_sprints([], "2026-03-15", 2, 4)
        assert result == {}

    def test_holiday_before_start_ignored(self):
        """Holidays before the planning window start are skipped."""
        holidays = [{"date": "2026-03-01", "name": "Early", "weekday": "Sunday"}]
        result = _assign_holidays_to_sprints(holidays, "2026-03-15", 2, 4)
        assert result == {}

    def test_holiday_beyond_target_sprints_ignored(self):
        """Holidays after the last sprint window are skipped."""
        holidays = [{"date": "2026-05-04", "name": "May Day", "weekday": "Monday"}]
        # Only 2 sprints × 2 weeks = 4 weeks (Mar 15 – Apr 12)
        result = _assign_holidays_to_sprints(holidays, "2026-03-15", 2, 2)
        assert result == {}


class TestComputePerSprintVelocities:
    """Tests for _compute_per_sprint_velocities per-sprint bank holiday deductions."""

    def test_sprint_with_holidays_gets_lower_velocity(self):
        """Only the sprint containing bank holidays should be reduced."""
        holidays_by_sprint = {
            1: [
                {"date": "2026-04-03", "name": "Good Friday", "weekday": "Friday"},
                {"date": "2026-04-06", "name": "Easter Monday", "weekday": "Monday"},
            ],
        }
        result = _compute_per_sprint_velocities(
            team_size=1,
            velocity_per_sprint=10,
            sprint_length_weeks=2,
            target_sprints=3,
            holidays_by_sprint=holidays_by_sprint,
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
            ktlo_engineers=0,
            discovery_pct=0,
        )
        assert len(result) == 3
        # Sprint 0 and 2: no holidays, full velocity
        assert result[0]["net_velocity"] == 10
        assert result[2]["net_velocity"] == 10
        # Sprint 1: 2 holidays out of 10 working days → 80% → 8 pts
        assert result[1]["net_velocity"] == 8
        assert result[1]["bank_holiday_days"] == 2
        assert "Good Friday" in result[1]["bank_holiday_names"]
        assert "Easter Monday" in result[1]["bank_holiday_names"]

    def test_no_holidays_gives_uniform_velocity(self):
        """With no bank holidays, all sprints get the same velocity."""
        result = _compute_per_sprint_velocities(
            team_size=1,
            velocity_per_sprint=10,
            sprint_length_weeks=2,
            target_sprints=3,
            holidays_by_sprint={},
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
            ktlo_engineers=0,
            discovery_pct=0,
        )
        velocities = [sc["net_velocity"] for sc in result]
        assert velocities == [10, 10, 10]

    def test_team_size_multiplies_holiday_impact(self):
        """Each holiday costs team_size person-days, not just 1."""
        holidays_by_sprint = {
            0: [{"date": "2026-03-20", "name": "Holiday", "weekday": "Friday"}],
        }
        result_1dev = _compute_per_sprint_velocities(
            team_size=1,
            velocity_per_sprint=10,
            sprint_length_weeks=2,
            target_sprints=1,
            holidays_by_sprint=holidays_by_sprint,
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
            discovery_pct=0,
        )
        result_3dev = _compute_per_sprint_velocities(
            team_size=3,
            velocity_per_sprint=10,
            sprint_length_weeks=2,
            target_sprints=1,
            holidays_by_sprint=holidays_by_sprint,
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
            discovery_pct=0,
        )
        # 1 dev: 1/10 days lost → 9 pts. 3 devs: 3/30 days lost → same ratio → 9 pts
        assert result_1dev[0]["net_velocity"] == result_3dev[0]["net_velocity"]

    def test_velocity_never_below_one(self):
        """Even with extreme deductions, velocity stays >= 1."""
        holidays_by_sprint = {
            0: [
                {"date": f"2026-03-{16 + i}", "name": f"H{i}", "weekday": "Mon"} for i in range(10)
            ],  # 10 holidays in a 2-week sprint
        }
        result = _compute_per_sprint_velocities(
            team_size=1,
            velocity_per_sprint=10,
            sprint_length_weeks=2,
            target_sprints=1,
            holidays_by_sprint=holidays_by_sprint,
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
            discovery_pct=0,
        )
        assert result[0]["net_velocity"] >= 1


class TestParseDateDmy:
    """Tests for _parse_date_dmy — DD/MM/YYYY date parsing."""

    def test_dd_mm_yyyy_slash(self):
        from datetime import date

        assert _parse_date_dmy("06/04/2026") == date(2026, 4, 6)

    def test_dd_mm_yy_slash(self):
        from datetime import date

        assert _parse_date_dmy("06/04/26") == date(2026, 4, 6)

    def test_dd_mm_yyyy_dash(self):
        from datetime import date

        assert _parse_date_dmy("06-04-2026") == date(2026, 4, 6)

    def test_dd_mm_yy_dash(self):
        from datetime import date

        assert _parse_date_dmy("06-04-26") == date(2026, 4, 6)

    def test_single_digit_day_month(self):
        from datetime import date

        assert _parse_date_dmy("6/4/2026") == date(2026, 4, 6)

    def test_invalid_format_returns_none(self):
        assert _parse_date_dmy("2026-04-06") is None  # ISO format not accepted
        assert _parse_date_dmy("not a date") is None
        assert _parse_date_dmy("") is None

    def test_invalid_date_returns_none(self):
        assert _parse_date_dmy("31/02/2026") is None  # Feb 31 doesn't exist

    def test_whitespace_stripped(self):
        from datetime import date

        assert _parse_date_dmy("  06/04/2026  ") == date(2026, 4, 6)


class TestCountWorkingDays:
    """Tests for _count_working_days — weekday counting."""

    def test_single_weekday(self):
        from datetime import date

        assert _count_working_days(date(2026, 3, 16), date(2026, 3, 16)) == 1  # Monday

    def test_weekend_excluded(self):
        from datetime import date

        # Fri to Mon = Fri + Mon = 2 working days (Sat+Sun excluded)
        assert _count_working_days(date(2026, 3, 20), date(2026, 3, 23)) == 2

    def test_full_work_week(self):
        from datetime import date

        # Mon to Fri = 5 working days
        assert _count_working_days(date(2026, 3, 16), date(2026, 3, 20)) == 5

    def test_two_weeks(self):
        from datetime import date

        # Mon to next Fri = 10 working days
        assert _count_working_days(date(2026, 3, 16), date(2026, 3, 27)) == 10

    def test_end_before_start_returns_zero(self):
        from datetime import date

        assert _count_working_days(date(2026, 3, 20), date(2026, 3, 16)) == 0

    def test_saturday_only(self):
        from datetime import date

        assert _count_working_days(date(2026, 3, 21), date(2026, 3, 21)) == 0  # Saturday


class TestAssignLeaveToSprints:
    """Tests for _assign_leave_to_sprints — PTO mapping to sprint windows."""

    def test_single_entry_in_one_sprint(self):
        entries = [{"person": "Alice", "start_date": "2026-03-23", "end_date": "2026-03-27", "working_days": 5}]
        # 2-week sprints starting 2026-03-16, Sprint 0: Mar 16–29
        result = _assign_leave_to_sprints(entries, "2026-03-16", 2, 3)
        assert 0 in result
        assert result[0][0]["person"] == "Alice"
        assert result[0][0]["days"] == 5

    def test_entry_spanning_two_sprints(self):
        # Leave spans sprint boundary: Mar 27 (sprint 0) to Apr 1 (sprint 1)
        entries = [{"person": "Bob", "start_date": "2026-03-27", "end_date": "2026-04-01", "working_days": 4}]
        result = _assign_leave_to_sprints(entries, "2026-03-16", 2, 3)
        # Sprint 0: Mar 16–29, Sprint 1: Mar 30–Apr 12
        assert 0 in result  # Mar 27 is in sprint 0
        assert 1 in result  # Apr 1 is in sprint 1
        total_days = sum(e["days"] for entries in result.values() for e in entries)
        assert total_days == 4  # Total should match working days

    def test_entry_outside_window_ignored(self):
        entries = [{"person": "Carol", "start_date": "2026-06-01", "end_date": "2026-06-05", "working_days": 5}]
        # 2 sprints × 2 weeks = 4 weeks from Mar 16 → Apr 12
        result = _assign_leave_to_sprints(entries, "2026-03-16", 2, 2)
        assert result == {}

    def test_multiple_people_same_sprint(self):
        entries = [
            {"person": "Alice", "start_date": "2026-03-23", "end_date": "2026-03-27", "working_days": 5},
            {"person": "Bob", "start_date": "2026-03-25", "end_date": "2026-03-26", "working_days": 2},
        ]
        result = _assign_leave_to_sprints(entries, "2026-03-16", 2, 3)
        assert len(result[0]) == 2
        people = {e["person"] for e in result[0]}
        assert people == {"Alice", "Bob"}

    def test_empty_entries(self):
        assert _assign_leave_to_sprints([], "2026-03-16", 2, 3) == {}

    def test_no_team_size_multiplier(self):
        """PTO is per-person — no team_size multiplication unlike bank holidays."""
        entries = [{"person": "Alice", "start_date": "2026-03-23", "end_date": "2026-03-27", "working_days": 5}]
        result = _assign_leave_to_sprints(entries, "2026-03-16", 2, 3)
        # Should be 5 days total, not 5 × team_size
        assert result[0][0]["days"] == 5


class TestComputePerSprintVelocitiesWithPTO:
    """Tests for _compute_per_sprint_velocities with leave_by_sprint parameter."""

    def test_pto_reduces_sprint_velocity(self):
        """Sprint with PTO should have lower velocity than sprint without."""
        leave_by_sprint = {
            1: [{"person": "Alice", "days": 5}],
        }
        result = _compute_per_sprint_velocities(
            team_size=1,
            velocity_per_sprint=10,
            sprint_length_weeks=2,
            target_sprints=3,
            holidays_by_sprint={},
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
            ktlo_engineers=0,
            discovery_pct=0,
            leave_by_sprint=leave_by_sprint,
        )
        assert result[0]["net_velocity"] == 10  # No PTO
        assert result[1]["net_velocity"] < 10  # PTO reduces velocity
        assert result[1]["pto_days"] == 5
        assert result[1]["pto_entries"] == [{"person": "Alice", "days": 5}]
        assert result[2]["net_velocity"] == 10  # No PTO

    def test_pto_and_holidays_combined(self):
        """Sprint with both bank holidays and PTO should have both deductions."""
        holidays_by_sprint = {
            0: [{"date": "2026-03-20", "name": "Holiday", "weekday": "Friday"}],
        }
        leave_by_sprint = {
            0: [{"person": "Bob", "days": 2}],
        }
        result = _compute_per_sprint_velocities(
            team_size=1,
            velocity_per_sprint=10,
            sprint_length_weeks=2,
            target_sprints=1,
            holidays_by_sprint=holidays_by_sprint,
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
            discovery_pct=0,
            leave_by_sprint=leave_by_sprint,
        )
        assert result[0]["bank_holiday_days"] == 1
        assert result[0]["pto_days"] == 2
        # 3 days lost out of 10 → 7/10 = 70% → 7 pts
        assert result[0]["net_velocity"] == 7

    def test_no_double_counting_when_leave_by_sprint_provided(self):
        """When leave_by_sprint is provided, planned_leave_days should be ignored to avoid double-counting."""
        leave_by_sprint = {
            0: [{"person": "Alice", "days": 5}],
        }
        # Pass planned_leave_days=5 (same as PTO) — should NOT reduce further
        result = _compute_per_sprint_velocities(
            team_size=1,
            velocity_per_sprint=10,
            sprint_length_weeks=2,
            target_sprints=2,
            holidays_by_sprint={},
            planned_leave_days=5,  # This should be ignored when leave_by_sprint is provided
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
            ktlo_engineers=0,
            discovery_pct=0,
            leave_by_sprint=leave_by_sprint,
        )
        # Sprint 0: 5 PTO days out of 10 working days = 50% → 5 pts
        assert result[0]["net_velocity"] == 5
        assert result[0]["pto_days"] == 5
        # Sprint 1: no PTO → full velocity
        assert result[1]["net_velocity"] == 10

    def test_none_leave_by_sprint_backward_compat(self):
        """leave_by_sprint=None should work (backward compatible)."""
        result = _compute_per_sprint_velocities(
            team_size=1,
            velocity_per_sprint=10,
            sprint_length_weeks=2,
            target_sprints=2,
            holidays_by_sprint={},
            planned_leave_days=0,
            unplanned_leave_pct=0,
            onboarding_engineer_sprints=0,
            discovery_pct=0,
            leave_by_sprint=None,
        )
        assert result[0]["pto_days"] == 0
        assert result[0]["pto_entries"] == []
        assert result[0]["net_velocity"] == 10


class TestExtractCapacityDeductionsWithLeave:
    """Tests for _extract_capacity_deductions with leave entries populated."""

    def test_leave_entries_sum_working_days(self):
        qs = QuestionnaireState(
            answers={28: "No bank holidays", 29: "10%", 30: "No engineers onboarding"},
            _planned_leave_entries=[
                {"person": "Alice", "start_date": "2026-04-06", "end_date": "2026-04-10", "working_days": 5},
                {"person": "Bob", "start_date": "2026-04-13", "end_date": "2026-04-15", "working_days": 3},
            ],
        )
        result = _extract_capacity_deductions(qs)
        assert result["capacity_planned_leave_days"] == 8

    def test_no_leave_entries_defaults_to_zero(self):
        qs = QuestionnaireState(answers={28: "No bank holidays", 29: "10%", 30: "No engineers onboarding"})
        result = _extract_capacity_deductions(qs)
        assert result["capacity_planned_leave_days"] == 0


class TestLinearTrackerSeam:
    """Tests for the Linear branch of the intake node's tracker seam.

    Linear is the third tracker alongside Jira and Azure DevOps. Because
    linear_fetch_velocity and linear_fetch_active_cycle deliberately emit the
    same JSON keys their Jira twins do, these wrappers stay as thin as
    _fetch_jira_velocity — which is exactly what these tests pin.
    """

    def test_is_configured_needs_both_vars(self, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", "fake-linear-key-for-tests")
        monkeypatch.setenv("LINEAR_TEAM_KEY", "YEA")
        assert _is_linear_configured() is True

    def test_is_not_configured_without_a_team_key(self, monkeypatch):
        # A bare API key cannot address a board, so it must not count as configured.
        monkeypatch.setenv("LINEAR_API_KEY", "fake-linear-key-for-tests")
        monkeypatch.delenv("LINEAR_TEAM_KEY", raising=False)
        assert _is_linear_configured() is False

    def test_is_not_configured_without_a_key(self, monkeypatch):
        monkeypatch.delenv("LINEAR_API_KEY", raising=False)
        monkeypatch.setenv("LINEAR_TEAM_KEY", "YEA")
        assert _is_linear_configured() is False

    @patch("yeaboi.tools.linear.linear_fetch_velocity")
    def test_velocity_wrapper_parses_the_tool_json(self, mock_tool):
        mock_tool.invoke.return_value = json.dumps({"team_velocity": 15, "jira_team_size": 2, "per_dev_velocity": 7.5})
        result = _fetch_linear_velocity()
        assert result == {"team_velocity": 15, "jira_team_size": 2, "per_dev_velocity": 7.5}

    @patch("yeaboi.tools.linear.linear_fetch_velocity")
    def test_velocity_wrapper_returns_none_on_error_string(self, mock_tool):
        mock_tool.invoke.return_value = "Error: Linear is not configured."
        assert _fetch_linear_velocity() is None

    @patch("yeaboi.tools.linear.linear_fetch_velocity")
    def test_velocity_wrapper_passes_through_the_zero_velocity_case(self, mock_tool):
        mock_tool.invoke.return_value = json.dumps(
            {"team_velocity": 0, "jira_team_size": 3, "per_dev_velocity": 0, "velocity_error": "no points"}
        )
        result = _fetch_linear_velocity()
        assert result is not None
        assert result["jira_team_size"] == 3

    @patch("yeaboi.tools.linear.linear_fetch_active_cycle")
    def test_active_cycle_wrapper_returns_number_date_and_message(self, mock_tool):
        mock_tool.invoke.return_value = json.dumps(
            {"sprint_number": 42, "sprint_name": "Hardening", "start_date": "2026-08-01"}
        )
        number, start, message = _fetch_linear_active_cycle()
        assert number == 42
        assert start == "2026-08-01"
        assert message == "Active cycle: Hardening"

    @patch("yeaboi.tools.linear.linear_fetch_active_cycle")
    def test_active_cycle_wrapper_surfaces_the_error_text(self, mock_tool):
        mock_tool.invoke.return_value = "Error: No active cycle on team 'YEA'"
        number, start, message = _fetch_linear_active_cycle()
        assert number is None
        assert message == "No active cycle on team 'YEA'"

    def test_tracker_velocity_uses_linear_when_it_is_the_only_tracker(self, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", "fake-linear-key-for-tests")
        monkeypatch.setenv("LINEAR_TEAM_KEY", "YEA")
        for var in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT_KEY"):
            monkeypatch.delenv(var, raising=False)
        with (
            patch("yeaboi.agent.nodes._is_azdevops_configured", return_value=False),
            patch("yeaboi.agent.nodes._fetch_linear_velocity", return_value={"team_velocity": 15}) as mock_linear,
        ):
            assert _fetch_tracker_velocity() == {"team_velocity": 15}
        mock_linear.assert_called_once()

    def test_tracker_velocity_prefers_jira_over_linear_by_default(self, monkeypatch):
        # Ordering is load-bearing: adding Linear must not change what an
        # existing Jira user gets.
        monkeypatch.setenv("LINEAR_API_KEY", "fake-linear-key-for-tests")
        monkeypatch.setenv("LINEAR_TEAM_KEY", "YEA")
        with (
            patch("yeaboi.agent.nodes._is_jira_configured", return_value=True),
            patch("yeaboi.agent.nodes._fetch_jira_velocity", return_value={"team_velocity": 25}),
            patch("yeaboi.agent.nodes._fetch_linear_velocity") as mock_linear,
        ):
            assert _fetch_tracker_velocity() == {"team_velocity": 25}
        mock_linear.assert_not_called()

    def test_tracker_velocity_honours_an_explicit_linear_preference(self, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", "fake-linear-key-for-tests")
        monkeypatch.setenv("LINEAR_TEAM_KEY", "YEA")
        with (
            patch("yeaboi.agent.nodes._is_jira_configured", return_value=True),
            patch("yeaboi.agent.nodes._fetch_jira_velocity") as mock_jira,
            patch("yeaboi.agent.nodes._fetch_linear_velocity", return_value={"team_velocity": 15}),
        ):
            assert _fetch_tracker_velocity("linear") == {"team_velocity": 15}
        mock_jira.assert_not_called()

    def test_tracker_configured_is_true_with_only_linear(self, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", "fake-linear-key-for-tests")
        monkeypatch.setenv("LINEAR_TEAM_KEY", "YEA")
        for var in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT_KEY"):
            monkeypatch.delenv(var, raising=False)
        with patch("yeaboi.agent.nodes._is_azdevops_configured", return_value=False):
            assert _is_tracker_configured() is True

    def test_active_sprint_number_falls_back_to_linear(self, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", "fake-linear-key-for-tests")
        monkeypatch.setenv("LINEAR_TEAM_KEY", "YEA")
        for var in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT_KEY"):
            monkeypatch.delenv(var, raising=False)
        with (
            patch("yeaboi.agent.nodes._is_azdevops_configured", return_value=False),
            patch(
                "yeaboi.agent.nodes._fetch_linear_active_cycle",
                return_value=(42, "2026-08-01", "Active cycle: Hardening"),
            ),
        ):
            assert _fetch_active_sprint_number() == (42, "2026-08-01", "Active cycle: Hardening")

    def test_active_sprint_number_honours_an_explicit_linear_preference(self, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", "fake-linear-key-for-tests")
        monkeypatch.setenv("LINEAR_TEAM_KEY", "YEA")
        with (
            patch("yeaboi.agent.nodes._is_jira_configured", return_value=True),
            patch(
                "yeaboi.agent.nodes._fetch_linear_active_cycle",
                return_value=(42, "2026-08-01", "Active cycle: Hardening"),
            ),
        ):
            assert _fetch_active_sprint_number("linear")[0] == 42
