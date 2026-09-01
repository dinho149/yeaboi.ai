"""Tests for the linear_sync batch creation module.

Mirrors test_jira_sync.py's coverage on the Linear shapes: idempotency through
the linear_*_keys dicts, error accumulation, the small-project landing modes
(backlog / existing cycle), and the full sync_all aggregation.
"""

from __future__ import annotations

import pytest

from yeaboi.agent.state import (
    AcceptanceCriterion,
    Discipline,
    Feature,
    Priority,
    Sprint,
    StoryPointValue,
    Task,
    TaskLabel,
    UserStory,
)
from yeaboi.linear_sync import (
    is_linear_configured,
    sync_all_to_linear,
    sync_cycles_to_linear,
    sync_stories_to_linear,
    sync_tasks_to_linear,
)


def _make_feature(id="feat-1", title="User Authentication"):
    return Feature(id=id, title=title, description="Auth feature", priority=Priority.HIGH)


def _make_story(id="story-1", feature_id="feat-1", title="Login endpoint"):
    return UserStory(
        id=id,
        feature_id=feature_id,
        persona="developer",
        goal="log in via API",
        benefit="access protected resources",
        acceptance_criteria=(AcceptanceCriterion(given="valid credentials", when="POST /login", then="return 200"),),
        story_points=StoryPointValue.THREE,
        priority=Priority.HIGH,
        title=title,
        discipline=Discipline.BACKEND,
    )


def _make_task(id="task-1", story_id="story-1", title="Implement login handler"):
    return Task(
        id=id,
        story_id=story_id,
        title=title,
        description="Implement the login endpoint",
        label=TaskLabel.CODE,
        test_plan="Test with valid and invalid credentials",
    )


def _make_sprint(id="sprint-1", story_ids=("story-1",), name="Sprint 1"):
    return Sprint(id=id, name=name, goal="Auth foundation", capacity_points=13, story_ids=list(story_ids))


class FakeLinear:
    """Answers the GraphQL documents linear_sync sends, counting creations."""

    def __init__(self, fail_issue_titles: set[str] | None = None):
        self.fail_issue_titles = fail_issue_titles or set()
        self.projects = 0
        self.issues: list[dict] = []
        self.cycles: list[dict] = []
        self.assignments: list[tuple[str, list[str]]] = []

    def request(self, query: str, variables: dict | None = None):
        variables = variables or {}
        if "projectCreate" in query:
            self.projects += 1
            return {"projectCreate": {"success": True, "project": {"id": f"proj-{self.projects}"}}}
        if "issueCreate" in query:
            payload = variables["input"]
            if payload["title"] in self.fail_issue_titles:
                raise RuntimeError("boom")
            self.issues.append(payload)
            n = len(self.issues)
            return {"issueCreate": {"success": True, "issue": {"id": f"uuid-{n}", "identifier": f"ENG-{n}"}}}
        if "cycleCreate" in query:
            self.cycles.append(variables["input"])
            return {"cycleCreate": {"success": True, "cycle": {"id": f"cyc-{len(self.cycles)}"}}}
        raise AssertionError(f"unexpected query: {query}")


@pytest.fixture
def fake(monkeypatch):
    fake = FakeLinear()
    monkeypatch.setenv("LINEAR_API_KEY", "lin_key")
    monkeypatch.setattr("yeaboi.tools.linear._resolve_team", lambda team_key="": {"id": "team-1", "key": "ENG"})
    monkeypatch.setattr("yeaboi.tools.linear._linear_request", fake.request)
    monkeypatch.setattr("yeaboi.tools.linear._label_ids", lambda team_id, names: [])
    monkeypatch.setattr(
        "yeaboi.tools.linear.create_sub_issue",
        lambda parent_id, title, description="": {"id": f"sub-{title}", "identifier": f"ENG-T-{title[:6]}"},
    )
    monkeypatch.setattr(
        "yeaboi.tools.linear.add_issues_to_cycle",
        lambda cycle_id, issue_ids: fake.assignments.append((cycle_id, list(issue_ids))),
    )
    monkeypatch.setattr("yeaboi.tools.linear.fetch_team_cycles", lambda states=("active", "future"): [])
    return fake


def _state(**extra) -> dict:
    state = {
        "project_name": "Auth Platform",
        "features": [_make_feature()],
        "stories": [_make_story()],
    }
    state.update(extra)
    return state


class TestConfigured:
    def test_reads_the_env(self, monkeypatch):
        monkeypatch.delenv("LINEAR_API_KEY", raising=False)
        assert is_linear_configured() is False
        monkeypatch.setenv("LINEAR_API_KEY", "x")
        assert is_linear_configured() is True


class TestStories:
    def test_creates_the_project_then_the_issues(self, fake):
        result, state = sync_stories_to_linear(_state())
        assert result.project_id == "proj-1"
        assert result.stories_created == {"story-1": "ENG-1"}
        assert state["linear_project_id"] == "proj-1"
        assert state["linear_story_keys"] == {"story-1": "ENG-1"}
        assert state["linear_story_ids"] == {"story-1": "uuid-1"}
        assert fake.issues[0]["estimate"] == 3

    def test_a_rerun_skips_what_exists(self, fake):
        _, state = sync_stories_to_linear(_state())
        result2, state2 = sync_stories_to_linear(state)
        assert result2.stories_created == {}
        assert result2.skipped == 2  # the project and the story
        assert fake.projects == 1
        assert len(fake.issues) == 1

    def test_one_failure_does_not_stop_the_others(self, monkeypatch, fake):
        fake.fail_issue_titles = {"Login endpoint"}
        stories = [_make_story(), _make_story(id="story-2", title="Logout endpoint")]
        result, state = sync_stories_to_linear(_state(stories=stories))
        assert list(result.stories_created) == ["story-2"]
        assert any("Login endpoint" in e for e in result.errors)

    def test_unconfigured_is_an_error_not_a_crash(self, monkeypatch):
        monkeypatch.delenv("LINEAR_API_KEY", raising=False)
        result, _ = sync_stories_to_linear(_state())
        assert result.errors and "LINEAR_API_KEY" in result.errors[0]


class TestTasks:
    def test_cascades_stories_then_creates_sub_issues(self, fake):
        result, state = sync_tasks_to_linear(_state(tasks=[_make_task()]))
        assert state["linear_story_keys"] == {"story-1": "ENG-1"}
        assert list(result.tasks_created) == ["task-1"]
        assert state["linear_task_keys"]["task-1"].startswith("ENG-T-")

    def test_a_task_whose_story_never_synced_is_an_error(self, fake):
        state = _state(tasks=[_make_task(story_id="story-404")])
        result, _ = sync_tasks_to_linear(state)
        assert any("story-404" in e for e in result.errors)


class TestCycles:
    def test_creates_cycles_and_assigns_issues(self, fake):
        result, state = sync_cycles_to_linear(_state(sprints=[_make_sprint()], sprint_start_date="2026-09-01"))
        assert result.cycles_created == {"sprint-1": "cyc-1"}
        assert fake.assignments == [("cyc-1", ["uuid-1"])]
        assert fake.cycles[0]["startsAt"].startswith("2026-09-01")

    def test_backlog_mode_creates_no_cycle(self, fake):
        result, _ = sync_cycles_to_linear(_state(sprints=[_make_sprint()], sprint_target_mode="backlog"))
        assert result.cycles_created == {}
        assert fake.cycles == []
        assert fake.assignments == []

    def test_existing_mode_assigns_to_the_named_open_cycle(self, monkeypatch, fake):
        monkeypatch.setattr(
            "yeaboi.tools.linear.fetch_team_cycles",
            lambda states=("active", "future", "closed"): [
                {"id": "cyc-77", "name": "Cycle 7", "state": "active", "start_date": "2026-08-24"}
            ],
        )
        state = _state(sprints=[_make_sprint()], sprint_target_mode="existing", target_sprint_name="Cycle 7")
        result, updated = sync_cycles_to_linear(state)
        assert result.cycles_updated == {"sprint-1": "cyc-77"}
        assert fake.assignments == [("cyc-77", ["uuid-1"])]
        assert fake.cycles == []  # nothing created

    def test_existing_mode_with_no_match_is_an_error(self, fake):
        state = _state(sprints=[_make_sprint()], sprint_target_mode="existing", target_sprint_name="Cycle 9")
        result, _ = sync_cycles_to_linear(state)
        assert any("Cycle 9" in e for e in result.errors)


class TestSyncAll:
    def test_aggregates_all_three_stages(self, fake):
        state = _state(tasks=[_make_task()], sprints=[_make_sprint()], sprint_start_date="2026-09-01")
        result, updated = sync_all_to_linear(state)
        assert result.project_id == "proj-1"
        assert list(result.stories_created) == ["story-1"]
        assert list(result.tasks_created) == ["task-1"]
        assert result.cycles_created == {"sprint-1": "cyc-1"}
        assert not result.errors

    def test_the_registry_summary_reads_this_result_shape(self, fake):
        from yeaboi import trackers

        state = _state(sprints=[_make_sprint()], sprint_start_date="2026-09-01")
        result, _ = sync_all_to_linear(state)
        summary = trackers.TRACKERS["linear"].result_summary(result)
        assert summary["epic"] == "proj-1"
        assert summary["sprints_created"] == {"sprint-1": "cyc-1"}
