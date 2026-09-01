"""Tests for the trello_sync batch creation module.

Same coverage stance as test_linear_sync.py, on Trello's shapes: the epic is a
label, sprints are lists named by the board's own numbering convention, and the
small-project landing modes leave cards in the Backlog list or move them into
one chosen open list.
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
from yeaboi.trello_sync import (
    is_trello_configured,
    sync_all_to_trello,
    sync_sprints_to_trello,
    sync_stories_to_trello,
    sync_tasks_to_trello,
)


def _make_story(id="story-1", title="Login endpoint"):
    return UserStory(
        id=id,
        feature_id="feat-1",
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


class FakeTrello:
    """Answers the REST paths trello_sync uses, counting creations."""

    def __init__(self):
        self.labels = 0
        self.cards: list[dict] = []
        self.lists: list[dict] = [{"id": "l-backlog", "name": "Backlog", "closed": False}]
        self.moves: list[tuple[str, str]] = []
        self.checklists: list[tuple[str, list[str]]] = []

    def request(self, method: str, path: str, params: dict | None = None):
        params = params or {}
        if path == "/labels":
            self.labels += 1
            return {"id": f"lbl-{self.labels}"}
        if path == "/cards":
            self.cards.append(params)
            return {"id": f"card-{len(self.cards)}"}
        raise AssertionError(f"unexpected request: {method} {path}")


@pytest.fixture
def fake(monkeypatch):
    fake = FakeTrello()
    monkeypatch.setenv("TRELLO_API_KEY", "k")
    monkeypatch.setenv("TRELLO_TOKEN", "t")
    monkeypatch.setattr("yeaboi.tools.trello._resolve_board", lambda hint="": {"id": "board-1", "name": "Platform"})
    monkeypatch.setattr("yeaboi.tools.trello._trello_request", fake.request)
    monkeypatch.setattr(
        "yeaboi.tools.trello.fetch_board_lists", lambda include_closed=False: [dict(row) for row in fake.lists]
    )

    def _create_list(name):
        created = {"id": f"l-{len(fake.lists)}", "name": name, "closed": False}
        fake.lists.append(created)
        return created

    monkeypatch.setattr("yeaboi.tools.trello.create_list", _create_list)
    monkeypatch.setattr(
        "yeaboi.tools.trello.move_card_to_list", lambda card_id, list_id: fake.moves.append((card_id, list_id))
    )

    def _checklist(card_id, name, items):
        fake.checklists.append((card_id, list(items)))
        return f"chk-{len(fake.checklists)}"

    monkeypatch.setattr("yeaboi.tools.trello.create_checklist_with_items", _checklist)
    return fake


def _state(**extra) -> dict:
    state = {
        "project_name": "Auth Platform",
        "features": [Feature(id="feat-1", title="Auth", description="d", priority=Priority.HIGH)],
        "stories": [_make_story()],
    }
    state.update(extra)
    return state


class TestConfigured:
    def test_needs_both_halves_of_the_pair(self, monkeypatch):
        monkeypatch.setenv("TRELLO_API_KEY", "k")
        monkeypatch.delenv("TRELLO_TOKEN", raising=False)
        assert is_trello_configured() is False
        monkeypatch.setenv("TRELLO_TOKEN", "t")
        assert is_trello_configured() is True


class TestStories:
    def test_creates_the_label_then_cards_in_the_backlog(self, fake):
        result, state = sync_stories_to_trello(_state())
        assert result.epic_label_id == "lbl-1"
        assert result.stories_created == {"story-1": "card-1"}
        assert state["trello_epic_label_id"] == "lbl-1"
        assert fake.cards[0]["idList"] == "l-backlog"
        assert fake.cards[0]["idLabels"] == "lbl-1"
        assert fake.cards[0]["desc"].startswith("**Points: 3**")

    def test_a_rerun_skips_what_exists(self, fake):
        _, state = sync_stories_to_trello(_state())
        result2, _ = sync_stories_to_trello(state)
        assert result2.stories_created == {}
        assert result2.skipped == 2  # label + card
        assert fake.labels == 1
        assert len(fake.cards) == 1

    def test_unconfigured_is_an_error_not_a_crash(self, monkeypatch):
        monkeypatch.delenv("TRELLO_API_KEY", raising=False)
        monkeypatch.delenv("TRELLO_TOKEN", raising=False)
        result, _ = sync_stories_to_trello(_state())
        assert result.errors and "TRELLO_API_KEY" in result.errors[0]


class TestTasks:
    def test_a_storys_tasks_share_one_checklist(self, fake):
        tasks = [_make_task(), _make_task(id="task-2", title="Write tests")]
        result, state = sync_tasks_to_trello(_state(tasks=tasks))
        assert fake.checklists == [("card-1", ["Implement login handler", "Write tests"])]
        assert result.tasks_created == {"task-1": "chk-1", "task-2": "chk-1"}
        assert state["trello_task_keys"]["task-1"] == "chk-1"


class TestSprints:
    def test_lists_follow_the_boards_numbering_convention(self, fake):
        # The board already runs "Sprint N" up to 2 (one archived) — the plan's
        # generic "Sprint 1" continues the sequence instead of colliding.
        fake.lists = [
            {"id": "l-backlog", "name": "Backlog", "closed": False},
            {"id": "l-a", "name": "Sprint 1", "closed": True},
            {"id": "l-b", "name": "Sprint 2", "closed": False},
        ]
        result, state = sync_sprints_to_trello(_state(sprints=[_make_sprint()]))
        names = {row["name"] for row in fake.lists}
        assert "Sprint 3" in names  # the sequence continues past the archived list
        assert list(result.lists_created) == ["sprint-1"]
        assert fake.moves and fake.moves[0][1] == state["trello_list_keys"]["sprint-1"]

    def test_backlog_mode_creates_no_list(self, fake):
        result, _ = sync_sprints_to_trello(_state(sprints=[_make_sprint()], sprint_target_mode="backlog"))
        assert result.lists_created == {}
        assert fake.moves == []

    def test_existing_mode_moves_cards_to_the_named_open_list(self, fake):
        fake.lists.append({"id": "l-s7", "name": "Sprint 7", "closed": False})
        state = _state(sprints=[_make_sprint()], sprint_target_mode="existing", target_sprint_name="Sprint 7")
        result, _ = sync_sprints_to_trello(state)
        assert result.lists_updated == {"sprint-1": "l-s7"}
        assert fake.moves == [("card-1", "l-s7")]

    def test_existing_mode_with_no_match_is_an_error(self, fake):
        state = _state(sprints=[_make_sprint()], sprint_target_mode="existing", target_sprint_name="Sprint 9")
        result, _ = sync_sprints_to_trello(state)
        assert any("Sprint 9" in e for e in result.errors)


class TestSyncAll:
    def test_aggregates_all_three_stages(self, fake):
        state = _state(tasks=[_make_task()], sprints=[_make_sprint()])
        result, _ = sync_all_to_trello(state)
        assert result.epic_label_id == "lbl-1"
        assert list(result.stories_created) == ["story-1"]
        assert list(result.tasks_created) == ["task-1"]
        assert list(result.lists_created) == ["sprint-1"]
        assert not result.errors

    def test_the_registry_summary_reads_this_result_shape(self, fake):
        from yeaboi import trackers

        result, _ = sync_all_to_trello(_state(sprints=[_make_sprint()]))
        summary = trackers.TRACKERS["trello"].result_summary(result)
        assert summary["epic"] == "lbl-1"
        assert list(summary["sprints_created"]) == ["sprint-1"]
