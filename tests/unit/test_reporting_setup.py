"""The surface-neutral half of the Reporting page: periods, sources, windows."""

from __future__ import annotations

from datetime import date

import pytest

from yeaboi.reporting import setup
from yeaboi.reporting.activity import PERIOD_LAST_WEEK, PERIOD_QUARTER, PERIOD_WINDOW
from yeaboi.reporting.sprints import SprintRef
from yeaboi.reporting.style import DEFAULT_STYLE


class TestPeriodOptions:
    def test_five_periods_in_picker_order(self):
        keys = [option["key"] for option in setup.period_options(today=date(2026, 8, 23))]
        assert keys == ["last_week", "last_sprint", "last_month", "quarter", "window"]

    def test_the_quarter_is_labelled_for_the_day_it_is_asked_about(self):
        options = {o["key"]: o for o in setup.period_options(today=date(2026, 8, 23))}
        assert options[PERIOD_QUARTER]["label"] == "Whole quarter (Q3 2026)"

    def test_every_option_carries_a_description(self):
        assert all(o["description"] for o in setup.period_options(today=date(2026, 1, 5)))


class TestExtraSteps:
    def test_only_the_quarter_earns_the_sprint_multi_select(self):
        assert setup.needs_sprints(PERIOD_QUARTER)
        assert not setup.needs_sprints(PERIOD_LAST_WEEK)

    def test_only_a_custom_range_earns_the_two_dates(self):
        assert setup.needs_window(PERIOD_WINDOW)
        assert not setup.needs_window(PERIOD_QUARTER)


class TestValidateWindow:
    def test_canonicalises_a_compact_iso_date(self):
        # Everything downstream compares these as strings — 20260818 would
        # order wrongly against the 2026-08-18 it equals.
        assert setup.validate_window("20260818", "2026-08-20") == ("2026-08-18", "2026-08-20")

    def test_a_reversed_range_is_refused_by_name(self):
        with pytest.raises(ValueError, match="before window_start"):
            setup.validate_window("2026-08-20", "2026-08-01")

    def test_a_non_date_names_the_field_and_the_value(self):
        with pytest.raises(ValueError, match="window_start must be an ISO date"):
            setup.validate_window("last tuesday", "")

    def test_blank_halves_pass_through(self):
        assert setup.validate_window("", "") == ("", "")

    def test_default_window_is_four_weeks_back_from_today(self):
        assert setup.default_window(today=date(2026, 8, 29)) == ("2026-08-01", "2026-08-29")


class TestSources:
    grid = {"delivery": ["jira"], "code": ["github"], "docs": []}

    def test_offerable_drops_the_empty_components(self):
        assert setup.offerable_grid(self.grid) == {"delivery": ["jira"], "code": ["github"]}

    def test_one_configured_source_is_not_a_question(self):
        assert not setup.sources_step_applies({"delivery": ["jira"], "code": [], "docs": []})
        assert setup.sources_step_applies(self.grid)

    def test_nothing_configured_is_not_a_question_either(self):
        assert not setup.sources_step_applies({"delivery": [], "code": [], "docs": []})

    def test_an_unchecked_component_comes_back_explicitly_empty(self):
        # Absent means "auto" downstream, which would re-enable exactly what
        # the user just deselected.
        assert setup.normalize_selection({"delivery": ["jira"]}, self.grid) == {"delivery": ["jira"], "code": []}

    def test_summary_names_the_sources_a_generate_will_consult(self):
        line = setup.sources_summary({"delivery": ["jira", "azuredevops"], "code": [], "docs": []}, self.grid)
        assert line == "Sources: Jira + Azure DevOps  ·  Code: —  ·  Docs: —"

    def test_summary_falls_back_to_the_whole_grid_before_a_choice_is_made(self):
        assert setup.sources_summary(None, self.grid) == "Sources: Jira  ·  Code: GitHub  ·  Docs: —"


def _sprint(name: str, start: str, end: str, *, in_quarter: bool) -> SprintRef:
    return SprintRef(name=name, start_date=start, end_date=end, source="plan", in_quarter=in_quarter)


class TestWindowFromSprints:
    sprints = [
        _sprint("Sprint 9", "2026-06-01", "2026-06-14", in_quarter=False),
        _sprint("Sprint 10", "2026-07-01", "2026-07-14", in_quarter=True),
        _sprint("Sprint 11", "2026-07-15", "2026-07-28", in_quarter=True),
    ]

    def test_default_checked_is_the_detected_quarter(self):
        assert setup.default_checked(self.sprints) == [1, 2]

    def test_the_window_spans_the_checked_sprints(self):
        window = setup.window_from_sprints(self.sprints, [1, 2], today=date(2026, 8, 23))
        assert window["window_start"] == "2026-07-01"
        assert window["window_end"] == "2026-07-28"
        assert window["sprint_names"] == ("Sprint 10", "Sprint 11")

    def test_the_detected_selection_keeps_the_plain_quarter_label(self):
        window = setup.window_from_sprints(self.sprints, [1, 2], today=date(2026, 8, 23))
        assert window["period_label_override"] == "Q3 2026"

    def test_any_other_selection_is_labelled_custom(self):
        window = setup.window_from_sprints(self.sprints, [0, 1, 2], today=date(2026, 8, 23))
        assert window["period_label_override"] == "Q3 2026 (custom)"

    def test_the_end_never_runs_past_today(self):
        # A quarter still in progress must not claim days that have not happened.
        window = setup.window_from_sprints(self.sprints, [1, 2], today=date(2026, 7, 20))
        assert window["window_end"] == "2026-07-20"

    def test_out_of_range_indices_are_ignored_without_making_it_custom(self):
        window = setup.window_from_sprints(self.sprints, [1, 2, 99], today=date(2026, 8, 23))
        assert window["period_label_override"] == "Q3 2026"

    def test_nothing_checked_is_no_window(self):
        assert setup.window_from_sprints(self.sprints, []) == {}

    def test_calendar_quarter_is_the_fallback_when_no_sprints_exist(self):
        window = setup.calendar_quarter_window(today=date(2026, 8, 23))
        assert window["window_start"] == "2026-07-01"
        assert window["window_end"] == "2026-08-23"
        assert window["sprint_names"] == ()
        assert window["period_label_override"] == "Q3 2026"


class TestResolveFit:
    def test_a_decided_style_is_returned_unasked(self):
        style = setup.apply_fit(DEFAULT_STYLE, expand=False)
        resolved, extra = setup.resolve_fit(object(), style)
        assert extra == 0
        assert resolved.content_fit == "tight"

    def test_no_report_is_nothing_to_ask_about(self):
        resolved, extra = setup.resolve_fit(None, DEFAULT_STYLE)
        assert (resolved, extra) == (DEFAULT_STYLE, 0)

    def test_asking_only_happens_when_expanding_costs_slides(self, monkeypatch):
        monkeypatch.setattr("yeaboi.reporting.layout.count_fit_slides", lambda report, style: (7, 7))
        resolved, extra = setup.resolve_fit(object(), DEFAULT_STYLE)
        assert extra == 0
        assert resolved.content_fit == "expand"  # decided for the caller, not asked

    def test_the_extra_slide_count_is_the_difference(self, monkeypatch):
        monkeypatch.setattr("yeaboi.reporting.layout.count_fit_slides", lambda report, style: (7, 10))
        resolved, extra = setup.resolve_fit(object(), DEFAULT_STYLE)
        assert extra == 3
        assert resolved.content_fit == "ask"  # untouched until the answer arrives

    def test_the_answer_applies_to_this_export_only(self):
        assert setup.apply_fit(DEFAULT_STYLE, expand=True).content_fit == "expand"
        assert DEFAULT_STYLE.content_fit == "ask"  # the saved preference is unchanged
