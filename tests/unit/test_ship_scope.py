"""Tests for the plan-hierarchy walker (ship/scope.py).

Ship can be pointed at an epic, a story or a task, so the two things that must
hold are: the outline shows every item the user can see in Planning, and an id
resolves to the same work whichever level it lives at.
"""

from __future__ import annotations

import pytest

from yeaboi.agent.state import (
    AcceptanceCriterion,
    Feature,
    Priority,
    StoryPointValue,
    Task,
    TaskLabel,
    UserStory,
)
from yeaboi.ship import scope


def _epic(fid="F1", title="Core Functionality"):
    return Feature(id=fid, title=title, description=f"{title} description.", priority=Priority.HIGH)


def _story(sid="US-F1-001", fid="F1", title="Create endpoint"):
    return UserStory(
        id=sid,
        feature_id=fid,
        persona="developer",
        goal="ship faster",
        benefit="less toil",
        acceptance_criteria=(AcceptanceCriterion(given="a plan", when="I ship", then="a PR opens"),),
        story_points=StoryPointValue.THREE,
        priority=Priority.HIGH,
        title=title,
    )


def _task(tid="T-US-F1-001-01", sid="US-F1-001", title="Add the route"):
    return Task(id=tid, story_id=sid, title=title, description="do it", label=TaskLabel.CODE, ai_prompt="Add X.")


def _plan():
    return {
        "features": [_epic(), _epic("F2", "Infrastructure")],
        "stories": [_story(), _story("US-F1-002", "F1", "List things"), _story("US-F2-001", "F2", "Set up CI")],
        "tasks": [_task(), _task("T-US-F1-001-02", "US-F1-001", "Add tests")],
    }


class TestOutline:
    def test_rows_come_out_in_tree_order_with_depths(self):
        rows = scope.outline(_plan())
        assert [(r.level, r.id, r.depth) for r in rows[:4]] == [
            ("epic", "F1", 0),
            ("story", "US-F1-001", 1),
            ("task", "T-US-F1-001-01", 2),
            ("task", "T-US-F1-001-02", 2),
        ]

    def test_every_child_names_its_parent_key(self):
        rows = {r.id: r for r in scope.outline(_plan())}
        assert rows["US-F1-001"].parent_key == "epic:F1"
        assert rows["T-US-F1-001-01"].parent_key == "story:US-F1-001"

    def test_an_epic_counts_its_stories_and_their_tasks(self):
        row = next(r for r in scope.outline(_plan()) if r.id == "F1")
        assert row.detail == "2 stories · 2 tasks"

    def test_a_lone_story_is_counted_in_the_singular(self):
        state = {"features": [_epic()], "stories": [_story()], "tasks": [_task()]}
        row = next(r for r in scope.outline(state) if r.id == "F1")
        assert row.detail == "1 story · 1 task"

    def test_an_orphan_story_is_grouped_not_dropped(self):
        # The planner's own nodes skip a story whose feature_id names nothing, so
        # a saved plan can carry one — and work the user sees in Planning must
        # not vanish from the picker.
        state = {"features": [_epic()], "stories": [_story("US-X", "F-GONE")], "tasks": []}
        rows = scope.outline(state)
        assert any(r.id == scope.UNGROUPED_ID for r in rows)
        assert any(r.id == "US-X" for r in rows)

    def test_an_orphan_task_is_grouped_not_dropped(self):
        state = {"features": [], "stories": [], "tasks": [_task("T-X", "US-GONE")]}
        rows = scope.outline(state)
        assert [r.id for r in rows] == [scope.UNGROUPED_ID, "T-X"]

    def test_an_empty_plan_is_no_rows_not_a_crash(self):
        assert scope.outline({}) == []
        assert scope.outline(None) == []


class TestFindTarget:
    def test_a_story_carries_its_own_tasks_and_its_epic(self):
        target = scope.find_target(_plan(), "US-F1-001")
        assert target.level == "story"
        assert [t.id for t in target.tasks] == ["T-US-F1-001-01", "T-US-F1-001-02"]
        assert target.parent_title == "Core Functionality"
        assert target.summary.startswith("As a developer")

    def test_an_epic_carries_every_story_beneath_it_and_all_their_tasks(self):
        target = scope.find_target(_plan(), "F1")
        assert target.level == "epic"
        assert [s.id for s in target.stories] == ["US-F1-001", "US-F1-002"]
        assert len(target.tasks) == 2
        assert target.summary == "Core Functionality description."

    def test_a_task_carries_the_story_it_serves(self):
        target = scope.find_target(_plan(), "T-US-F1-001-01")
        assert target.level == "task"
        assert [t.id for t in target.tasks] == ["T-US-F1-001-01"]
        assert target.parent_title == "Create endpoint"
        assert target.parent_summary.startswith("As a developer")

    def test_an_explicit_level_wins_over_the_search_order(self):
        # Ids are LLM-supplied, so two levels can collide; --level is the escape.
        state = {"features": [_epic("DUP", "The epic")], "stories": [_story("DUP", "F1", "The story")], "tasks": []}
        assert scope.find_target(state, "DUP").level == "epic"
        assert scope.find_target(state, "DUP", level="story").level == "story"

    def test_a_missing_id_names_what_was_available_at_every_level(self):
        with pytest.raises(ValueError) as caught:
            scope.find_target(_plan(), "NOPE")
        message = str(caught.value)
        assert "F1" in message
        assert "US-F1-001" in message
        assert "T-US-F1-001-01" in message

    def test_a_missing_id_at_a_named_level_names_only_that_level(self):
        with pytest.raises(ValueError) as caught:
            scope.find_target(_plan(), "NOPE", level="task")
        message = str(caught.value)
        assert "T-US-F1-001-01" in message
        assert "US-F1-002" not in message

    def test_an_unknown_level_is_refused_by_name(self):
        with pytest.raises(ValueError, match="sprint"):
            scope.find_target(_plan(), "F1", level="sprint")

    def test_parents_are_resolved_by_field_never_by_parsing_the_id(self):
        # The task decomposer prompt still teaches the pre-rename T-US-E1-… shape
        # while the story writer emits US-F1-…, so an id says nothing reliable
        # about its parent.
        state = {
            "features": [_epic("F1")],
            "stories": [_story("US-E1-001", "F1")],
            "tasks": [_task("T-completely-unrelated", "US-E1-001")],
        }
        assert scope.find_target(state, "T-completely-unrelated").parent_title == "Create endpoint"
        assert [t.id for t in scope.find_target(state, "US-E1-001").tasks] == ["T-completely-unrelated"]


class TestSplitStoryIds:
    def test_an_epic_splits_over_its_stories_in_plan_order(self):
        assert scope.split_story_ids(scope.find_target(_plan(), "F1")) == ("US-F1-001", "US-F1-002")

    @pytest.mark.parametrize("item_id", ["US-F1-001", "T-US-F1-001-01"])
    def test_nothing_below_an_epic_splits(self, item_id):
        assert scope.split_story_ids(scope.find_target(_plan(), item_id)) == ()

    def test_an_epic_with_no_stories_splits_over_nothing(self):
        assert scope.split_story_ids(scope.find_target({"features": [_epic()]}, "F1")) == ()


class TestUngrouped:
    """The synthetic bucket is on screen, so Enter on it has to mean something."""

    def test_it_resolves_to_a_target_rather_than_an_unknown_id(self):
        state = {"features": [_epic()], "stories": [_story("US-X", "F-GONE")], "tasks": [_task("T-X", "US-X")]}
        target = scope.find_target(state, scope.UNGROUPED_ID)
        assert target.level == "epic"
        assert target.title == scope.UNGROUPED_TITLE
        assert [s.id for s in target.stories] == ["US-X"]
        assert [t.id for t in target.tasks] == ["T-X"]

    def test_it_can_be_split_over_its_orphan_stories(self):
        state = {"features": [], "stories": [_story("US-X", "F-GONE"), _story("US-Y", "F-GONE")], "tasks": []}
        assert scope.split_story_ids(scope.find_target(state, scope.UNGROUPED_ID)) == ("US-X", "US-Y")

    def test_orphan_tasks_are_counted_in_the_bucket_s_detail(self):
        state = {"features": [], "stories": [], "tasks": [_task("T-X", "US-GONE"), _task("T-Y", "US-GONE")]}
        row = next(r for r in scope.outline(state) if r.id == scope.UNGROUPED_ID)
        assert row.detail == "0 stories · 2 tasks"

    def test_a_task_only_bucket_carries_the_tasks(self):
        state = {"features": [], "stories": [], "tasks": [_task("T-X", "US-GONE")]}
        target = scope.find_target(state, scope.UNGROUPED_ID)
        assert [t.id for t in target.tasks] == ["T-X"]
        assert scope.split_story_ids(target) == ()
