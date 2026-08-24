"""Tests for the poker setup rules (poker/setup.py)."""

from __future__ import annotations

import pytest

from yeaboi.poker import setup


@pytest.fixture(autouse=True)
def _no_trackers(monkeypatch):
    for var in (
        "JIRA_BASE_URL",
        "JIRA_API_TOKEN",
        "AZURE_DEVOPS_ORG_URL",
        "AZURE_DEVOPS_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


def _jira(monkeypatch):
    monkeypatch.setenv("JIRA_BASE_URL", "https://x.atlassian.net")
    monkeypatch.setenv("JIRA_API_TOKEN", "t")


def _azdo(monkeypatch):
    monkeypatch.setenv("AZURE_DEVOPS_ORG_URL", "https://dev.azure.com/x")
    monkeypatch.setenv("AZURE_DEVOPS_TOKEN", "t")


class TestSourceOptions:
    def test_demo_is_always_offered_last(self):
        options = setup.source_options()
        assert [o["key"] for o in options] == ["demo"]
        assert options[-1]["sub"]

    def test_configured_trackers_come_first(self, monkeypatch):
        _jira(monkeypatch)
        _azdo(monkeypatch)
        assert [o["key"] for o in setup.source_options()] == ["jira", "azdevops", "demo"]

    def test_hint_names_the_choice_when_both_are_configured(self, monkeypatch):
        _jira(monkeypatch)
        _azdo(monkeypatch)
        assert "pick which one" in setup.source_hint()

    def test_hint_points_at_settings_with_nothing_configured(self):
        assert "Settings" in setup.source_hint()

    def test_hint_is_silent_with_exactly_one(self, monkeypatch):
        _jira(monkeypatch)
        assert setup.source_hint() == ""


class TestStepApplies:
    def test_demo_answers_only_the_source(self):
        assert setup.steps_for(source="demo") == ("source",)

    def test_backlog_skips_the_sprint_list(self):
        assert setup.steps_for(source="jira", scope="backlog") == ("source", "scope", "types")

    def test_a_sprint_earns_the_sprint_list(self):
        assert setup.steps_for(source="jira", scope="sprint") == ("source", "scope", "sprint", "types")

    def test_sprint_is_not_offered_before_the_scope_is_known(self):
        assert not setup.step_applies("sprint", source="jira")

    def test_source_always_applies(self):
        assert setup.step_applies("source", source="demo")


class TestSprintOptions:
    SPRINTS = [
        {"name": "Sprint 1", "start_date": "2026-01-01", "end_date": "2026-01-14", "state": "closed"},
        {"name": "Sprint 2", "state": "active"},
        {"name": "Sprint 3"},
    ]

    def test_subtitle_joins_only_what_is_present(self):
        options = setup.sprint_options(self.SPRINTS)
        assert options[0]["sub"] == "2026-01-01 · 2026-01-14 · closed"
        assert options[1]["sub"] == "active"
        assert options[2]["sub"] == ""

    def test_cursor_lands_on_the_active_sprint(self):
        assert setup.default_sprint_index(self.SPRINTS) == 1

    def test_cursor_falls_back_to_the_last(self):
        assert setup.default_sprint_index([{"name": "a"}, {"name": "b"}]) == 1

    def test_no_sprints_is_index_zero(self):
        assert setup.default_sprint_index([]) == 0


class TestTypeOptions:
    def test_jira_checks_all_three(self):
        assert [o["checked"] for o in setup.type_options("jira")] == [True, True, True]

    def test_azdo_leaves_child_tasks_off(self):
        options = {o["key"]: o["checked"] for o in setup.type_options("azdevops")}
        assert options == {"story": True, "bug": True, "task": False}

    def test_sublabels_differ_by_source(self):
        jira = {o["key"]: o["sub"] for o in setup.type_options("jira")}
        azdo = {o["key"]: o["sub"] for o in setup.type_options("azdevops")}
        assert jira["story"] == "issuetype Story"
        assert azdo["story"] == "User Story / Product Backlog Item"

    def test_jira_hint_names_the_subtask_rule(self):
        assert "Sub-tasks are never included" in setup.type_hint("jira")

    def test_other_sources_get_the_generic_hint(self):
        assert "work-item types" in setup.type_hint("azdevops")


class TestIncludeTypes:
    def test_demo_never_filters(self):
        assert setup.include_types_for("demo", ["story"]) is None

    def test_unanswered_means_the_source_default(self):
        assert setup.include_types_for("jira", None) is None

    def test_selection_keeps_canonical_order(self):
        assert setup.include_types_for("jira", ["task", "story"]) == ("story", "task")

    def test_an_empty_selection_is_no_filter_rather_than_no_tickets(self):
        assert setup.include_types_for("jira", []) is None


class TestScopeLabel:
    def test_demo(self):
        assert setup.scope_label_for(source="demo") == "Demo"

    def test_backlog(self):
        assert setup.scope_label_for(source="jira", scope="backlog") == "Backlog"

    def test_named_sprint(self):
        assert setup.scope_label_for(source="jira", scope="sprint", sprint={"name": "Sprint 7"}) == "Sprint 7"

    def test_unnamed_sprint_still_reads_as_one(self):
        assert setup.scope_label_for(source="jira", scope="sprint", sprint={}) == "Sprint"


class TestEmptyResult:
    def test_message_names_the_source_and_the_scope(self):
        message = setup.empty_result_message("azdevops", "Sprint 4")
        assert "Azure DevOps" in message and "Sprint 4" in message
