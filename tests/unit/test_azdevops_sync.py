"""Tests for the azdevops_sync batch creation module.

Tests cover:
- _feature_title_to_tag sanitisation edge cases
- _format_story_description_html / _format_task_description_html formatting
- _map_priority_to_azdo mapping
- Idempotency: pre-populated azdevops_*_keys are skipped
- Error accumulation: one failure doesn't stop others
- sync_stories_to_azdevops with mock WIT client
- sync_tasks_to_azdevops cascades to create stories first
- sync_all_to_azdevops full pipeline
- is_azdevops_board_configured detection
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
from yeaboi.azdevops_sync import (
    AzDevOpsSyncResult,
    _feature_title_to_tag,
    _format_story_description_html,
    _format_task_description_html,
    _map_priority_to_azdo,
    is_azdevops_board_configured,
    sync_all_to_azdevops,
    sync_iterations_to_azdevops,
    sync_stories_to_azdevops,
    sync_tasks_to_azdevops,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


def _make_sprint(id="sprint-1", story_ids=("story-1",)):
    return Sprint(
        id=id,
        name="Sprint 1",
        goal="Auth foundation",
        capacity_points=13,
        story_ids=story_ids,
    )


def _make_graph_state(**overrides):
    """Build a minimal graph state with defaults for testing."""
    state = {
        "messages": [],
        "features": [_make_feature()],
        "stories": [_make_story()],
        "tasks": [_make_task()],
        "sprints": [_make_sprint()],
        "project_name": "Test Project",
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Tests: helpers
# ---------------------------------------------------------------------------


class TestFeatureTitleToTag:
    def test_basic(self):
        assert _feature_title_to_tag("User Authentication") == "User Authentication"

    def test_strips_semicolons(self):
        assert ";" not in _feature_title_to_tag("Auth; Login")

    def test_strips_commas(self):
        assert "," not in _feature_title_to_tag("Auth, Login")

    def test_empty_returns_feature(self):
        assert _feature_title_to_tag("") == "Feature"

    def test_truncates_long_titles(self):
        assert len(_feature_title_to_tag("x" * 100)) <= 80


class TestPriorityMapping:
    def test_critical(self):
        assert _map_priority_to_azdo("critical") == 1

    def test_high(self):
        assert _map_priority_to_azdo("high") == 2

    def test_medium(self):
        assert _map_priority_to_azdo("medium") == 3

    def test_low(self):
        assert _map_priority_to_azdo("low") == 4

    def test_unknown_defaults_to_medium(self):
        assert _map_priority_to_azdo("unknown") == 3


class TestFormatStoryDescriptionHtml:
    def test_contains_user_story_text(self):
        story = _make_story()
        desc = _format_story_description_html(story)
        assert "<strong>As a</strong>" in desc
        assert "developer" in desc
        assert "log in via API" in desc

    def test_contains_acceptance_criteria(self):
        story = _make_story()
        desc = _format_story_description_html(story)
        assert "<h3>Acceptance Criteria</h3>" in desc
        assert "valid credentials" in desc

    def test_contains_feature_context(self):
        story = _make_story()
        feature = _make_feature()
        desc = _format_story_description_html(story, feature)
        assert "User Authentication" in desc


class TestFormatTaskDescriptionHtml:
    def test_contains_description(self):
        task = _make_task()
        desc = _format_task_description_html(task)
        assert "Implement the login endpoint" in desc

    def test_contains_test_plan(self):
        task = _make_task()
        desc = _format_task_description_html(task)
        assert "<h3>Test Plan</h3>" in desc
        assert "valid and invalid credentials" in desc


# ---------------------------------------------------------------------------
# Tests: is_azdevops_board_configured
# ---------------------------------------------------------------------------


class TestIsAzdevopsBoardConfigured:
    @patch("yeaboi.azdevops_sync.get_azure_devops_token", return_value="token")
    @patch("yeaboi.azdevops_sync.get_azure_devops_org_url", return_value="https://dev.azure.com/org")
    @patch("yeaboi.azdevops_sync.get_azure_devops_project", return_value="MyProject")
    def test_all_set(self, *_):
        assert is_azdevops_board_configured() is True

    @patch("yeaboi.azdevops_sync.get_azure_devops_token", return_value=None)
    @patch("yeaboi.azdevops_sync.get_azure_devops_org_url", return_value="https://dev.azure.com/org")
    @patch("yeaboi.azdevops_sync.get_azure_devops_project", return_value="MyProject")
    def test_missing_token(self, *_):
        assert is_azdevops_board_configured() is False

    @patch("yeaboi.azdevops_sync.get_azure_devops_token", return_value="token")
    @patch("yeaboi.azdevops_sync.get_azure_devops_org_url", return_value=None)
    @patch("yeaboi.azdevops_sync.get_azure_devops_project", return_value="MyProject")
    def test_missing_org_url(self, *_):
        assert is_azdevops_board_configured() is False


# ---------------------------------------------------------------------------
# Tests: sync_stories_to_azdevops
# ---------------------------------------------------------------------------


class TestSyncStoriesToAzdevops:
    @patch("yeaboi.azdevops_sync.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.azdevops_sync.get_azure_devops_org_url", return_value="https://dev.azure.com/org")
    @patch("yeaboi.azdevops_sync.get_azure_devops_token", return_value="token")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_creates_epic_and_story(self, mock_clients, *_):
        mock_wit = MagicMock()
        mock_clients.return_value = (mock_wit, MagicMock())

        # First create_work_item call = Epic (returns ID 100)
        # Second call = Story (returns ID 101)
        epic_wi = MagicMock()
        epic_wi.id = 100
        story_wi = MagicMock()
        story_wi.id = 101
        mock_wit.create_work_item.side_effect = [epic_wi, story_wi]

        state = _make_graph_state()
        result, new_state = sync_stories_to_azdevops(state)

        assert result.epic_id == "100"
        assert result.stories_created == {"story-1": "101"}
        assert new_state["azdevops_epic_id"] == "100"
        assert new_state["azdevops_story_keys"] == {"story-1": "101"}
        assert mock_wit.create_work_item.call_count == 2

    @patch("yeaboi.azdevops_sync.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.azdevops_sync.get_azure_devops_org_url", return_value="https://dev.azure.com/org")
    @patch("yeaboi.azdevops_sync.get_azure_devops_token", return_value="token")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_idempotency_skips_existing(self, mock_clients, *_):
        """Already-created stories are skipped."""
        mock_wit = MagicMock()
        mock_clients.return_value = (mock_wit, MagicMock())

        state = _make_graph_state(
            azdevops_epic_id="100",
            azdevops_story_keys={"story-1": "101"},
        )
        result, _ = sync_stories_to_azdevops(state)

        assert result.skipped == 2  # epic + story
        assert result.stories_created == {}
        mock_wit.create_work_item.assert_not_called()

    @patch("yeaboi.azdevops_sync.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.azdevops_sync.get_azure_devops_org_url", return_value="https://dev.azure.com/org")
    @patch("yeaboi.azdevops_sync.get_azure_devops_token", return_value="token")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_error_accumulation(self, mock_clients, *_):
        """Story creation errors are accumulated, not raised."""
        mock_wit = MagicMock()
        mock_clients.return_value = (mock_wit, MagicMock())

        epic_wi = MagicMock()
        epic_wi.id = 100
        mock_wit.create_work_item.side_effect = [epic_wi, RuntimeError("API error")]

        state = _make_graph_state()
        result, _ = sync_stories_to_azdevops(state)

        assert result.epic_id == "100"
        assert len(result.errors) == 1
        assert "API error" in result.errors[0]

    @patch("yeaboi.azdevops_sync.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.azdevops_sync.get_azure_devops_org_url", return_value="https://dev.azure.com/org")
    @patch("yeaboi.azdevops_sync.get_azure_devops_token", return_value="token")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_progress_callback(self, mock_clients, *_):
        """Progress callback is called with correct counts."""
        mock_wit = MagicMock()
        mock_clients.return_value = (mock_wit, MagicMock())

        epic_wi = MagicMock()
        epic_wi.id = 100
        story_wi = MagicMock()
        story_wi.id = 101
        mock_wit.create_work_item.side_effect = [epic_wi, story_wi]

        call_count = [0]

        def on_progress(current, total, desc):
            call_count[0] += 1

        state = _make_graph_state()
        sync_stories_to_azdevops(state, on_progress=on_progress)

        assert call_count[0] == 2  # 1 epic + 1 story

    @patch("yeaboi.azdevops_sync.get_azure_devops_project", return_value="")
    def test_missing_project(self, *_):
        """Returns error when project is not configured."""
        state = _make_graph_state()
        result, _ = sync_stories_to_azdevops(state)
        assert len(result.errors) == 1
        assert "AZURE_DEVOPS_PROJECT" in result.errors[0]


# ---------------------------------------------------------------------------
# Tests: sync_tasks_to_azdevops
# ---------------------------------------------------------------------------


class TestSyncTasksToAzdevops:
    @patch("yeaboi.azdevops_sync.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.azdevops_sync.get_azure_devops_org_url", return_value="https://dev.azure.com/org")
    @patch("yeaboi.azdevops_sync.get_azure_devops_token", return_value="token")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_creates_task_under_story(self, mock_clients, *_):
        mock_wit = MagicMock()
        mock_clients.return_value = (mock_wit, MagicMock())

        task_wi = MagicMock()
        task_wi.id = 200
        mock_wit.create_work_item.return_value = task_wi

        state = _make_graph_state(
            azdevops_epic_id="100",
            azdevops_story_keys={"story-1": "101"},
        )
        result, new_state = sync_tasks_to_azdevops(state)

        assert result.tasks_created == {"task-1": "200"}
        assert new_state["azdevops_task_keys"] == {"task-1": "200"}

    @patch("yeaboi.azdevops_sync.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.azdevops_sync.get_azure_devops_org_url", return_value="https://dev.azure.com/org")
    @patch("yeaboi.azdevops_sync.get_azure_devops_token", return_value="token")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_skips_task_without_parent(self, mock_clients, *_):
        """Tasks whose parent story hasn't been created get an error."""
        mock_wit = MagicMock()
        mock_clients.return_value = (mock_wit, MagicMock())

        state = _make_graph_state(
            azdevops_epic_id="100",
            azdevops_story_keys={},  # no stories created
        )
        # Remove stories to prevent cascade
        state["stories"] = []
        result, _ = sync_tasks_to_azdevops(state)

        assert len(result.errors) == 1
        assert "parent story" in result.errors[0]


# ---------------------------------------------------------------------------
# Tests: sync_all_to_azdevops
# ---------------------------------------------------------------------------


class TestSyncAllToAzdevops:
    @patch("yeaboi.azdevops_sync.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.azdevops_sync.get_azure_devops_org_url", return_value="https://dev.azure.com/org")
    @patch("yeaboi.azdevops_sync.get_azure_devops_token", return_value="token")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    @patch("yeaboi.azdevops_sync._create_iteration_node", return_value="MyProject\\Sprint 1")
    @patch("yeaboi.tools.azure_devops.add_work_items_to_iteration")
    def test_full_pipeline(self, mock_assign, mock_iter, mock_clients, *_):
        mock_wit = MagicMock()
        mock_clients.return_value = (mock_wit, MagicMock())

        # Epic, Story, Task
        epic_wi = MagicMock()
        epic_wi.id = 100
        story_wi = MagicMock()
        story_wi.id = 101
        task_wi = MagicMock()
        task_wi.id = 200
        mock_wit.create_work_item.side_effect = [epic_wi, story_wi, task_wi]

        state = _make_graph_state()
        result, new_state = sync_all_to_azdevops(state)

        assert result.epic_id == "100"
        assert "story-1" in result.stories_created
        assert "task-1" in result.tasks_created
        assert "sprint-1" in result.iterations_created


# ---------------------------------------------------------------------------
# Tests: sync_iterations_to_azdevops — numbering, past-iteration guard, reuse
# ---------------------------------------------------------------------------


def _iter_meta(name, time_frame, path=""):
    return {
        "name": name,
        "path": path or f"MyProject\\{name}",
        "time_frame": time_frame,
        "start_date": "",
        "finish_date": "",
    }


@patch("yeaboi.azdevops_sync.get_azure_devops_project", return_value="MyProject")
@patch("yeaboi.azdevops_sync.get_azure_devops_org_url", return_value="https://dev.azure.com/org")
@patch("yeaboi.azdevops_sync.get_azure_devops_token", return_value="token")
class TestSyncIterationsToAzdevops:
    def _state(self, **overrides):
        base = _make_graph_state(
            azdevops_story_keys={"story-1": "101"},
            sprint_length_weeks=2,
            sprint_start_date="2026-03-16",
        )
        base.update(overrides)
        return base

    def test_minus_one_sentinel_continues_sequence(self, *_):
        meta = [_iter_meta("Sprint 5", "past"), _iter_meta("Sprint 6", "current")]
        with patch("yeaboi.tools.azure_devops.fetch_team_iterations_meta", return_value=meta):
            with patch("yeaboi.azdevops_sync._create_iteration_node", return_value="MyProject\\Sprint 7") as mock_iter:
                with patch("yeaboi.tools.azure_devops.add_work_items_to_iteration"):
                    result, _ = sync_iterations_to_azdevops(self._state(starting_sprint_number=-1))

        assert not result.errors
        assert mock_iter.call_args.args[3] == "Sprint 7"

    def test_past_iteration_never_reused(self, *_):
        """A configured start landing on a past iteration's name renumbers past the max."""
        meta = [_iter_meta("Sprint 5", "past"), _iter_meta("Sprint 6", "past")]
        with patch("yeaboi.tools.azure_devops.fetch_team_iterations_meta", return_value=meta):
            with patch("yeaboi.azdevops_sync._create_iteration_node", return_value="MyProject\\Sprint 7") as mock_iter:
                with patch("yeaboi.tools.azure_devops.add_work_items_to_iteration"):
                    result, _ = sync_iterations_to_azdevops(self._state(starting_sprint_number=5))

        assert mock_iter.call_args.args[3] == "Sprint 7"
        assert result.iterations_updated == {}

    def test_dates_do_not_overlap(self, *_):
        with patch("yeaboi.tools.azure_devops.fetch_team_iterations_meta", return_value=[]):
            with patch("yeaboi.azdevops_sync._create_iteration_node", return_value="MyProject\\Sprint 1") as mock_iter:
                with patch("yeaboi.tools.azure_devops.add_work_items_to_iteration"):
                    sync_iterations_to_azdevops(self._state())

        kwargs = mock_iter.call_args.kwargs
        assert kwargs["start_date"] == "2026-03-16"
        assert kwargs["finish_date"] == "2026-03-29"  # start + 2 weeks − 1 day

    def test_existing_target_adds_items_never_creates(self, *_):
        """sprint_target_mode='existing' assigns items to the target iteration, no creation."""
        meta = [_iter_meta("Sprint 6", "current")]
        state = self._state(
            sprint_target_mode="existing",
            target_sprint_name="Sprint 6",
            target_sprint_external_id="",
        )
        with patch("yeaboi.tools.azure_devops.fetch_team_iterations_meta", return_value=meta):
            with patch("yeaboi.azdevops_sync._create_iteration_node") as mock_iter:
                with patch("yeaboi.tools.azure_devops.add_work_items_to_iteration") as mock_add:
                    result, new_state = sync_iterations_to_azdevops(state)

        assert not result.errors
        assert not mock_iter.called
        assert result.iterations_updated == {"sprint-1": "MyProject\\Sprint 6"}
        assert new_state["azdevops_iteration_keys"]["sprint-1"] == "MyProject\\Sprint 6"
        mock_add.assert_called_once()

    def test_existing_target_past_iteration_errors_loudly(self, *_):
        meta = [_iter_meta("Sprint 5", "past")]
        state = self._state(
            sprint_target_mode="existing",
            target_sprint_name="Sprint 5",
            target_sprint_external_id="",
        )
        with patch("yeaboi.tools.azure_devops.fetch_team_iterations_meta", return_value=meta):
            with patch("yeaboi.azdevops_sync._create_iteration_node") as mock_iter:
                with patch("yeaboi.tools.azure_devops.add_work_items_to_iteration") as mock_add:
                    result, _ = sync_iterations_to_azdevops(state)

        assert result.errors and "not found among current/future" in result.errors[0]
        assert not mock_iter.called
        assert not mock_add.called

    def test_current_same_named_iteration_reused_not_created(self, *_):
        meta = [_iter_meta("Sprint 6", "current")]
        with patch("yeaboi.tools.azure_devops.fetch_team_iterations_meta", return_value=meta):
            with patch("yeaboi.azdevops_sync._create_iteration_node") as mock_iter:
                with patch("yeaboi.tools.azure_devops.add_work_items_to_iteration") as mock_add:
                    result, new_state = sync_iterations_to_azdevops(self._state(starting_sprint_number=6))

        assert not mock_iter.called
        assert result.iterations_created == {}
        assert result.iterations_updated == {"sprint-1": "MyProject\\Sprint 6"}
        assert new_state["azdevops_iteration_keys"]["sprint-1"] == "MyProject\\Sprint 6"
        mock_add.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: AzDevOpsSyncResult
# ---------------------------------------------------------------------------


class TestAzDevOpsSyncResult:
    def test_defaults(self):
        result = AzDevOpsSyncResult()
        assert result.epic_id is None
        assert result.stories_created == {}
        assert result.tasks_created == {}
        assert result.iterations_created == {}
        assert result.errors == []
        assert result.skipped == 0


class TestTeamStyleDescriptionsHtml:
    """AzDO mirror: team AC style, DoD list, and section headings."""

    def test_free_text_acs_render_as_list(self):
        story = _make_story()
        story = story.__class__(
            **{**story.__dict__, "acceptance_criteria": (AcceptanceCriterion(text="Login works end to end."),)}
        )
        desc = _format_story_description_html(story, _make_feature())
        assert "<li>Login works end to end.</li>" in desc
        assert "<strong>Given</strong>" not in desc

    def test_custom_dod_list_renders_with_its_own_labels(self):
        story = _make_story()
        custom = ("Tests green", "Deployed", "Announced")
        story = story.__class__(**{**story.__dict__, "dod_applicable": (True, False, True)})
        desc = _format_story_description_html(story, None, dod_items=custom)
        assert "Tests green" in desc
        assert "<s>Deployed</s>" in desc

    def test_team_headings_adopted(self):
        story = _make_story()
        headings = {"acceptance_criteria": "ACs", "dod": "Done looks like"}
        desc = _format_story_description_html(story, None, headings=headings)
        assert "<h3>ACs</h3>" in desc
        assert "<h3>Done looks like</h3>" in desc
