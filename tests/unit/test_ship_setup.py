"""What a Ship launcher decides before the engine is called."""

from __future__ import annotations

from dataclasses import dataclass, field

from yeaboi.ship import setup


@dataclass
class _Story:
    id: str
    title: str = ""
    goal: str = ""
    story_points: int | None = 3
    acceptance_criteria: tuple = field(default_factory=tuple)


class TestLoadPlan:
    def test_the_latest_plan_with_work_wins(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.ship.plans.latest_plan_with_work",
            lambda: ({"stories": [_Story("US-1")]}, "plan-9", "Apollo"),
        )
        state, plan_id, name, problem = setup.load_plan()
        assert [s.id for s in state["stories"]] == ["US-1"]
        assert (plan_id, name, problem) == ("plan-9", "Apollo", "")

    def test_no_plan_is_not_a_problem(self, monkeypatch):
        monkeypatch.setattr("yeaboi.ship.plans.latest_plan_with_work", lambda: None)
        assert setup.load_plan() == ({}, "", "", "")

    def test_an_unreadable_store_reports_a_plain_reason(self, monkeypatch):
        def _boom():
            raise RuntimeError("db is locked")

        monkeypatch.setattr("yeaboi.ship.plans.latest_plan_with_work", _boom)
        _state, _plan, _name, problem = setup.load_plan()
        assert "Could not read saved plans" in problem


class TestLoadStories:
    def test_narrows_the_plan_to_its_stories(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.ship.plans.latest_plan_with_work",
            lambda: ({"features": [object()], "stories": [_Story("US-1")]}, "plan-9", "Apollo"),
        )
        stories, plan_id, name, problem = setup.load_stories()
        assert [s.id for s in stories] == ["US-1"]
        assert (plan_id, name, problem) == ("plan-9", "Apollo", "")

    def test_a_plan_with_no_stories_yields_no_rows(self, monkeypatch):
        monkeypatch.setattr("yeaboi.ship.plans.latest_plan_with_work", lambda: ({"tasks": [1]}, "p", "P"))
        assert setup.load_stories()[0] == []


class TestStoryOptions:
    def test_carries_the_three_facts_a_picker_shows(self):
        rows = setup.story_options([_Story("US-1", title="Add search", story_points=5, acceptance_criteria=(1, 2))])
        assert rows == [{"id": "US-1", "title": "Add search", "points": 5, "criteria": 2}]

    def test_an_untitled_story_falls_back_to_its_goal(self):
        # Stories saved before titles existed carry only the persona template.
        rows = setup.story_options([_Story("US-2", goal="search the catalogue")])
        assert rows[0]["title"] == "search the catalogue"

    def test_an_unestimated_story_reads_as_zero(self):
        assert setup.story_options([_Story("US-3", story_points=None)])[0]["points"] == 0

    def test_no_stories_is_no_rows(self):
        assert setup.story_options([]) == []


class TestGateResolutions:
    def test_only_the_two_the_store_arbitrates(self):
        # A surface offering a third verb would be lying about what the CAS
        # in ShipStore.resolve_gate can record.
        assert setup.GATE_RESOLUTIONS == ("approved", "rejected")
