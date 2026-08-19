"""Tests for the jira_sync batch creation module.

Tests cover:
- _feature_title_to_label sanitisation edge cases
- _format_story_description / _format_task_description formatting
- Idempotency: pre-populated jira_*_keys are skipped
- Error accumulation: one failure doesn't stop others
- sync_stories_to_jira with mock JIRA client
- sync_tasks_to_jira cascades to create stories first
- sync_all_to_jira full pipeline
"""

from __future__ import annotations

from types import SimpleNamespace
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
from yeaboi.jira_sync import (
    JiraSyncResult,
    _feature_title_to_label,
    _format_story_description,
    _format_task_description,
    is_jira_configured,
    sync_all_to_jira,
    sync_sprints_to_jira,
    sync_stories_to_jira,
    sync_tasks_to_jira,
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
        "sprint_length_weeks": 2,
        "sprint_start_date": "2026-03-16",
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# _feature_title_to_label tests
# ---------------------------------------------------------------------------


class TestFeatureTitleToLabel:
    def test_basic(self):
        assert _feature_title_to_label("User Authentication") == "User-Authentication"

    def test_special_chars(self):
        assert _feature_title_to_label("Feature #1 (beta)") == "Feature-1-beta"

    def test_empty(self):
        assert _feature_title_to_label("") == "Feature"

    def test_only_special_chars(self):
        assert _feature_title_to_label("@#$%") == "Feature"

    def test_multiple_spaces(self):
        assert _feature_title_to_label("  User   Auth  ") == "User-Auth"

    def test_long_title_truncated(self):
        label = _feature_title_to_label("A" * 100)
        assert len(label) <= 50


# ---------------------------------------------------------------------------
# _format_story_description tests
# ---------------------------------------------------------------------------


class TestFormatStoryDescription:
    def test_includes_user_story_text(self):
        story = _make_story()
        desc = _format_story_description(story)
        assert "developer" in desc
        assert "log in via API" in desc

    def test_includes_acceptance_criteria(self):
        story = _make_story()
        desc = _format_story_description(story)
        assert "Acceptance Criteria" in desc
        assert "valid credentials" in desc

    def test_includes_feature_context(self):
        story = _make_story()
        feature = _make_feature()
        desc = _format_story_description(story, feature)
        assert "User Authentication" in desc

    def test_no_feature(self):
        story = _make_story()
        desc = _format_story_description(story, None)
        assert "Feature:" not in desc


# ---------------------------------------------------------------------------
# _format_task_description tests
# ---------------------------------------------------------------------------


class TestFormatTaskDescription:
    def test_includes_description(self):
        task = _make_task()
        desc = _format_task_description(task)
        assert "Implement the login endpoint" in desc

    def test_includes_test_plan(self):
        task = _make_task()
        desc = _format_task_description(task)
        assert "Test Plan" in desc
        assert "valid and invalid credentials" in desc

    def test_no_test_plan(self):
        task = Task(id="t1", story_id="s1", title="Doc task", description="Write docs", label=TaskLabel.DOCUMENTATION)
        desc = _format_task_description(task)
        assert "Test Plan" not in desc


# ---------------------------------------------------------------------------
# is_jira_configured tests
# ---------------------------------------------------------------------------


class TestIsJiraConfigured:
    def test_returns_true_when_token_present(self, monkeypatch):
        monkeypatch.setenv("JIRA_API_TOKEN", "test-token")
        assert is_jira_configured() is True

    def test_returns_false_when_token_missing(self, monkeypatch):
        monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
        assert is_jira_configured() is False


# ---------------------------------------------------------------------------
# sync_stories_to_jira tests
# ---------------------------------------------------------------------------


class TestSyncStoriesToJira:
    def test_returns_error_when_jira_not_configured(self, monkeypatch):
        monkeypatch.setattr("yeaboi.jira_sync.get_jira_token", lambda: None)
        result, state = sync_stories_to_jira(_make_graph_state())
        assert result.errors
        assert "not configured" in result.errors[0].lower() or "missing" in result.errors[0].lower()

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_creates_epic_and_stories(self, mock_key):
        mock_jira = MagicMock()
        mock_epic = MagicMock()
        mock_epic.key = "PROJ-1"
        mock_story_issue = MagicMock()
        mock_story_issue.key = "PROJ-2"

        mock_jira.create_issue.return_value = mock_epic

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            with patch(
                "yeaboi.tools.jira._create_issue_with_epic_link",
                return_value=(mock_story_issue, "parent"),
            ):
                result, state = sync_stories_to_jira(_make_graph_state())

        assert result.epic_key == "PROJ-1"
        assert state["jira_epic_key"] == "PROJ-1"
        assert "story-1" in result.stories_created
        assert result.stories_created["story-1"] == "PROJ-2"
        assert state["jira_story_keys"]["story-1"] == "PROJ-2"

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_skips_existing_stories(self, mock_key):
        """Stories already in jira_story_keys should be skipped."""
        mock_jira = MagicMock()
        mock_epic = MagicMock()
        mock_epic.key = "PROJ-1"
        mock_jira.create_issue.return_value = mock_epic

        state = _make_graph_state(
            jira_epic_key="PROJ-1",
            jira_story_keys={"story-1": "PROJ-2"},
        )

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            result, new_state = sync_stories_to_jira(state)

        # Epic was skipped (already exists)
        assert result.skipped >= 1
        # Story was skipped (already exists)
        assert "story-1" not in result.stories_created
        # No Jira API calls for story creation
        assert not mock_jira.create_issue.called  # epic also skipped

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_error_accumulation(self, mock_key):
        """One failing story shouldn't prevent others from being created."""
        from jira import JIRAError

        mock_jira = MagicMock()
        mock_epic = MagicMock()
        mock_epic.key = "PROJ-1"
        mock_jira.create_issue.return_value = mock_epic

        # Two stories — first fails, second succeeds
        story1 = _make_story(id="s1", title="Failing story")
        story2 = _make_story(id="s2", title="Good story")
        state = _make_graph_state(stories=[story1, story2])

        mock_good_issue = MagicMock()
        mock_good_issue.key = "PROJ-3"

        call_count = [0]

        def mock_create_with_epic(jira, fields, epic_key, method):
            call_count[0] += 1
            if call_count[0] == 1:
                raise JIRAError(status_code=500, text="Server error")
            return mock_good_issue, "parent"

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            with patch("yeaboi.tools.jira._create_issue_with_epic_link", side_effect=mock_create_with_epic):
                result, new_state = sync_stories_to_jira(state)

        assert len(result.errors) == 1
        assert "s2" in result.stories_created
        assert "s1" not in result.stories_created

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_progress_callback_called(self, mock_key):
        mock_jira = MagicMock()
        mock_epic = MagicMock()
        mock_epic.key = "PROJ-1"
        mock_jira.create_issue.return_value = mock_epic

        mock_story_issue = MagicMock()
        mock_story_issue.key = "PROJ-2"

        progress_calls = []

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            with patch(
                "yeaboi.tools.jira._create_issue_with_epic_link",
                return_value=(mock_story_issue, "parent"),
            ):
                result, state = sync_stories_to_jira(
                    _make_graph_state(),
                    on_progress=lambda cur, tot, desc: progress_calls.append((cur, tot, desc)),
                )

        assert len(progress_calls) >= 2  # epic + at least 1 story


# ---------------------------------------------------------------------------
# sync_tasks_to_jira tests
# ---------------------------------------------------------------------------


class TestSyncTasksToJira:
    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_cascades_to_create_stories_first(self, mock_key):
        """When no stories exist in Jira, tasks sync should create stories first."""
        mock_jira = MagicMock()
        mock_epic = MagicMock()
        mock_epic.key = "PROJ-1"
        mock_jira.create_issue.side_effect = [mock_epic]  # epic creation

        mock_story_issue = MagicMock()
        mock_story_issue.key = "PROJ-2"
        mock_task_issue = MagicMock()
        mock_task_issue.key = "PROJ-3"

        # Mock create_subtask separately from the module
        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            with patch(
                "yeaboi.tools.jira._create_issue_with_epic_link",
                return_value=(mock_story_issue, "parent"),
            ):
                with patch("yeaboi.tools.jira.create_subtask", return_value="PROJ-3"):
                    result, state = sync_tasks_to_jira(_make_graph_state())

        # Stories should have been created via cascade
        assert "story-1" in state.get("jira_story_keys", {})
        # Tasks should be created
        assert "task-1" in state.get("jira_task_keys", {})

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_skips_existing_tasks(self, mock_key):
        mock_jira = MagicMock()
        state = _make_graph_state(
            jira_epic_key="PROJ-1",
            jira_story_keys={"story-1": "PROJ-2"},
            jira_task_keys={"task-1": "PROJ-3"},
        )

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            result, new_state = sync_tasks_to_jira(state)

        assert result.skipped >= 1
        assert "task-1" not in result.tasks_created


# ---------------------------------------------------------------------------
# sync_all_to_jira tests
# ---------------------------------------------------------------------------


class TestSyncAllToJira:
    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_full_pipeline(self, mock_key):
        mock_jira = MagicMock()
        mock_epic = MagicMock()
        mock_epic.key = "PROJ-1"
        mock_jira.create_issue.return_value = mock_epic

        mock_story_issue = MagicMock()
        mock_story_issue.key = "PROJ-2"

        mock_sprint = MagicMock()
        mock_sprint.id = 42
        mock_jira.create_sprint.return_value = mock_sprint

        mock_boards = [MagicMock(id=10)]
        mock_jira.boards.return_value = mock_boards

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            with patch(
                "yeaboi.tools.jira._create_issue_with_epic_link",
                return_value=(mock_story_issue, "parent"),
            ):
                with patch("yeaboi.tools.jira.create_subtask", return_value="PROJ-3"):
                    result, state = sync_all_to_jira(_make_graph_state())

        assert result.epic_key == "PROJ-1"
        assert len(result.stories_created) == 1
        assert len(result.tasks_created) == 1
        assert len(result.sprints_created) == 1


# ---------------------------------------------------------------------------
# sync_sprints_to_jira — board numbering, renaming, and reuse
# ---------------------------------------------------------------------------


def _board_sprint(id, name):
    """A real-shaped board sprint object (SimpleNamespace: MagicMock treats name= specially)."""
    return SimpleNamespace(id=id, name=name)


def _sprint_capable_jira(board_sprints_by_state, boards=None):
    """A mock JIRA client whose boards()/sprints() return real-shaped data."""
    mock_jira = MagicMock()
    mock_jira.boards.return_value = (
        boards if boards is not None else [SimpleNamespace(id=10, name="Board", type="scrum")]
    )

    def _sprints(board_id, state=None, **kwargs):
        assert kwargs.get("maxResults") is False, "board sprints must be fetched fully paginated (maxResults=False)"
        return board_sprints_by_state.get(state, [])

    mock_jira.sprints.side_effect = _sprints
    created = []

    def _create_sprint(**kwargs):
        created.append(kwargs)
        return SimpleNamespace(id=100 + len(created), name=kwargs.get("name", ""))

    mock_jira.create_sprint.side_effect = _create_sprint
    mock_jira._created_sprints = created
    return mock_jira


class TestSyncSprintsToJira:
    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_minus_one_sentinel_continues_board_sequence(self, mock_key):
        """starting_sprint_number=-1 (no tracker pick) must yield max+1, never 'Sprint -1'."""
        mock_jira = _sprint_capable_jira(
            {
                "closed": [_board_sprint(1, "PSOT Sprint 105"), _board_sprint(2, "PSOT Sprint 106")],
                "active": [_board_sprint(3, "PSOT Sprint 107")],
                "future": [],
            }
        )
        state = _make_graph_state(starting_sprint_number=-1, jira_story_keys={"story-1": "PROJ-2"})

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            result, new_state = sync_sprints_to_jira(state)

        assert not result.errors
        names = [kw["name"] for kw in mock_jira._created_sprints]
        assert names == ["PSOT Sprint 108"]
        assert "sprint-1" in result.sprints_created

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_outlier_name_does_not_hijack_prefix(self, mock_key):
        """A stray 'Hardening 2024' must not beat the consensus 'PSOT Sprint N' convention."""
        closed = [_board_sprint(n, f"PSOT Sprint {n}") for n in (104, 105, 106)]
        closed.append(_board_sprint(999, "Hardening 2024"))
        mock_jira = _sprint_capable_jira({"closed": closed, "active": [], "future": []})
        state = _make_graph_state(starting_sprint_number=-1, jira_story_keys={"story-1": "PROJ-2"})

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            sync_sprints_to_jira(state)

        names = [kw["name"] for kw in mock_jira._created_sprints]
        assert names == ["PSOT Sprint 107"]

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_scrum_board_preferred_over_kanban(self, mock_key):
        """The type='scrum' board query wins; a kanban board listed first must not."""
        mock_jira = _sprint_capable_jira({"closed": [], "active": [], "future": []})
        kanban = SimpleNamespace(id=1, name="Kanban", type="kanban")
        scrum = SimpleNamespace(id=2, name="Scrum", type="scrum")

        def _boards(**kwargs):
            return [scrum] if kwargs.get("type") == "scrum" else [kanban, scrum]

        mock_jira.boards.side_effect = _boards
        state = _make_graph_state(jira_story_keys={"story-1": "PROJ-2"})

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            sync_sprints_to_jira(state)

        assert mock_jira._created_sprints[0]["board_id"] == 2

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_user_pick_landing_on_closed_sprints_renumbers(self, mock_key):
        """A configured start whose names are closed sprints shifts the batch past the max."""
        mock_jira = _sprint_capable_jira(
            {
                "closed": [
                    _board_sprint(1, "Sprint 105"),
                    _board_sprint(2, "Sprint 106"),
                    _board_sprint(3, "Sprint 107"),
                ],
                "active": [],
                "future": [],
            }
        )
        state = _make_graph_state(starting_sprint_number=105, jira_story_keys={"story-1": "PROJ-2"})

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            result, _ = sync_sprints_to_jira(state)

        names = [kw["name"] for kw in mock_jira._created_sprints]
        assert names == ["Sprint 108"]
        # The closed sprint was never targeted for reuse.
        assert not mock_jira.add_issues_to_sprint.called or all(
            call.args[0] not in (1, 2, 3) for call in mock_jira.add_issues_to_sprint.call_args_list
        )

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_active_same_named_sprint_reused_not_created(self, mock_key):
        """A same-named active sprint is reused (sprints_updated), never re-created."""
        mock_jira = _sprint_capable_jira(
            {
                "closed": [_board_sprint(1, "Sprint 103")],
                "active": [_board_sprint(2, "Sprint 104")],
                "future": [],
            }
        )
        state = _make_graph_state(starting_sprint_number=104, jira_story_keys={"story-1": "PROJ-2"})

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            with patch("yeaboi.tools.jira.add_issues_to_sprint") as mock_add:
                result, new_state = sync_sprints_to_jira(state)

        assert not mock_jira.create_sprint.called
        assert result.sprints_created == {}
        assert result.sprints_updated == {"sprint-1": "2"}
        assert new_state["jira_sprint_keys"]["sprint-1"] == "2"
        mock_add.assert_called_once()
        assert mock_add.call_args.args[1] == 2

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_created_sprint_dates_are_iso_datetimes_without_overlap(self, mock_key):
        """Dates go to Jira as ISO-8601 datetimes; end is inclusive (start + length − 1 day)."""
        sprint2 = _make_sprint(id="sprint-2", story_ids=())
        mock_jira = _sprint_capable_jira({"closed": [], "active": [], "future": []})
        state = _make_graph_state(
            sprints=[_make_sprint(), sprint2],
            starting_sprint_number=1,
            jira_story_keys={"story-1": "PROJ-2"},
        )

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            sync_sprints_to_jira(state)

        first, second = mock_jira._created_sprints
        assert first["startDate"] == "2026-03-16T00:00:00.000+00:00"
        assert first["endDate"] == "2026-03-29T00:00:00.000+00:00"  # start + 2 weeks − 1 day
        assert second["startDate"] == "2026-03-30T00:00:00.000+00:00"  # no overlap with first end

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_existing_target_adds_stories_never_creates(self, mock_key):
        """sprint_target_mode='existing' assigns stories to the target and never creates."""
        mock_jira = _sprint_capable_jira({"closed": [], "active": [_board_sprint(7, "PSOT Sprint 104")], "future": []})
        state = _make_graph_state(
            jira_story_keys={"story-1": "PROJ-2"},
            sprint_target_mode="existing",
            target_sprint_name="PSOT Sprint 104",
            target_sprint_external_id="",
        )

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            with patch("yeaboi.tools.jira.add_issues_to_sprint") as mock_add:
                result, new_state = sync_sprints_to_jira(state)

        assert not result.errors
        assert not mock_jira.create_sprint.called
        assert result.sprints_created == {}
        assert result.sprints_updated == {"sprint-1": "7"}
        assert new_state["jira_sprint_keys"]["sprint-1"] == "7"
        mock_add.assert_called_once()
        assert mock_add.call_args.args[1] == 7
        assert mock_add.call_args.args[2] == ["PROJ-2"]

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_existing_target_resolved_by_external_id(self, mock_key):
        mock_jira = _sprint_capable_jira({"closed": [], "active": [], "future": []})
        state = _make_graph_state(
            jira_story_keys={"story-1": "PROJ-2"},
            sprint_target_mode="existing",
            target_sprint_name="",
            target_sprint_external_id="42",
        )

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            with patch("yeaboi.tools.jira.add_issues_to_sprint") as mock_add:
                result, _ = sync_sprints_to_jira(state)

        assert not result.errors
        assert mock_add.call_args.args[1] == 42

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_existing_target_closed_or_missing_errors_loudly(self, mock_key):
        """A closed or vanished target sprint is an error — never a silent create."""
        mock_jira = _sprint_capable_jira({"closed": [_board_sprint(7, "PSOT Sprint 104")], "active": [], "future": []})
        state = _make_graph_state(
            jira_story_keys={"story-1": "PROJ-2"},
            sprint_target_mode="existing",
            target_sprint_name="PSOT Sprint 104",
            target_sprint_external_id="",
        )

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            with patch("yeaboi.tools.jira.add_issues_to_sprint") as mock_add:
                result, _ = sync_sprints_to_jira(state)

        assert result.errors and "not found among active/future" in result.errors[0]
        assert not mock_jira.create_sprint.called
        assert not mock_add.called

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_existing_target_closed_external_id_errors_loudly(self, mock_key):
        """A closed sprint's external id must not slip past the active/future filter."""
        mock_jira = _sprint_capable_jira({"closed": [_board_sprint(42, "PSOT Sprint 99")], "active": [], "future": []})
        state = _make_graph_state(
            jira_story_keys={"story-1": "PROJ-2"},
            sprint_target_mode="existing",
            target_sprint_name="",
            target_sprint_external_id="42",
        )

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            with patch("yeaboi.tools.jira.add_issues_to_sprint") as mock_add:
                result, _ = sync_sprints_to_jira(state)

        assert result.errors and "not found among active/future" in result.errors[0]
        assert not mock_add.called
        assert not mock_jira.create_sprint.called

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_backlog_target_creates_and_assigns_nothing(self, mock_key):
        """sprint_target_mode='backlog' — stories only; no sprint, no assignment."""
        mock_jira = _sprint_capable_jira({"closed": [], "active": [], "future": []})
        state = _make_graph_state(jira_story_keys={"story-1": "PROJ-2"}, sprint_target_mode="backlog")

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            with patch("yeaboi.tools.jira.add_issues_to_sprint") as mock_add:
                result, new_state = sync_sprints_to_jira(state)

        assert not result.errors
        assert not mock_jira.create_sprint.called
        assert not mock_add.called
        assert result.sprints_created == {}
        assert result.sprints_updated == {}
        assert "jira_sprint_keys" not in new_state or not new_state["jira_sprint_keys"]

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_duplicate_name_open_sprint_wins_over_closed(self, mock_key):
        """A closed sprint must not shadow a same-named open one — reuse, don't re-create."""
        mock_jira = _sprint_capable_jira(
            {
                "closed": [_board_sprint(1, "Sprint 104")],
                "active": [],
                "future": [_board_sprint(9, "Sprint 104")],
            }
        )
        state = _make_graph_state(starting_sprint_number=104, jira_story_keys={"story-1": "PROJ-2"})

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            with patch("yeaboi.tools.jira.add_issues_to_sprint") as mock_add:
                result, _ = sync_sprints_to_jira(state)

        assert not mock_jira.create_sprint.called
        assert result.sprints_updated == {"sprint-1": "9"}
        mock_add.assert_called_once()
        assert mock_add.call_args.args[1] == 9

    @patch("yeaboi.jira_sync.get_jira_project_key", return_value="PROJ")
    def test_unnumbered_board_keeps_plan_names(self, mock_key):
        """No numbered sprints on the board → the plan's own names are used as-is."""
        mock_jira = _sprint_capable_jira({"closed": [_board_sprint(1, "Kickoff")], "active": [], "future": []})
        state = _make_graph_state(starting_sprint_number=-1, jira_story_keys={"story-1": "PROJ-2"})

        with patch("yeaboi.tools.jira._make_jira_client", return_value=mock_jira):
            sync_sprints_to_jira(state)

        assert [kw["name"] for kw in mock_jira._created_sprints] == ["Sprint 1"]


# ---------------------------------------------------------------------------
# JiraSyncResult tests
# ---------------------------------------------------------------------------


class TestJiraSyncResult:
    def test_defaults(self):
        r = JiraSyncResult()
        assert r.epic_key is None
        assert r.stories_created == {}
        assert r.tasks_created == {}
        assert r.sprints_created == {}
        assert r.errors == []
        assert r.skipped == 0


class TestTeamStyleDescriptions:
    """Descriptions follow the team's AC style, DoD list, and section headings."""

    def test_free_text_acs_render_verbatim(self):
        story = _make_story()
        story = story.__class__(
            **{**story.__dict__, "acceptance_criteria": (AcceptanceCriterion(text="Login works end to end."),)}
        )
        desc = _format_story_description(story, _make_feature())
        assert "Login works end to end." in desc
        assert "*Given*" not in desc

    def test_custom_dod_list_renders_with_its_own_labels(self):
        # The regression this pins: the gate compared against the DEFAULT
        # DOD_ITEMS, silently dropping custom DoD sections of another length.
        story = _make_story()
        custom = ("Tests green", "Deployed", "Announced")
        story = story.__class__(**{**story.__dict__, "dod_applicable": (True, False, True)})
        desc = _format_story_description(story, None, dod_items=custom)
        assert "* [x] Tests green" in desc
        assert "* [ ] ~Deployed~" in desc
        assert "* [x] Announced" in desc

    def test_default_length_flags_with_custom_dod_keep_the_section(self):
        # The real pipeline shape: stories carry flags sized to the default
        # 7-item list; a shorter custom DoD list must not drop the whole
        # block — positions carry over, extra flags are ignored.
        story = _make_story()
        story = story.__class__(**{**story.__dict__, "dod_applicable": (True, False, True, True, True, True, True)})
        desc = _format_story_description(story, None, dod_items=("Tests green", "Deployed", "Announced"))
        assert "Definition of Done" in desc
        assert "* [x] Tests green" in desc
        assert "* [ ] ~Deployed~" in desc
        assert "* [x] Announced" in desc

    def test_mixed_ac_list_numbers_gwt_triples_consecutively(self):
        story = _make_story()
        acs = (
            AcceptanceCriterion(text="Free text first."),
            AcceptanceCriterion(given="g", when="w", then="t"),
        )
        story = story.__class__(**{**story.__dict__, "acceptance_criteria": acs})
        desc = _format_story_description(story, None)
        assert "*AC1*" in desc  # the counter skips the free-text criterion
        assert "AC2" not in desc

    def test_short_flags_pad_as_applicable(self):
        story = _make_story()
        story = story.__class__(**{**story.__dict__, "dod_applicable": (True,)})
        desc = _format_story_description(story, None, dod_items=("Tests green", "Deployed"))
        assert "* [x] Tests green" in desc
        assert "* [x] Deployed" in desc

    def test_team_headings_adopted(self):
        story = _make_story()
        headings = {"summary": "What is this about?", "acceptance_criteria": "ACs", "dod": "Done looks like"}
        desc = _format_story_description(story, None, headings=headings)
        assert "h3. What is this about?" in desc
        assert "h3. ACs" in desc
        assert "h3. Done looks like" in desc
        assert "h3. Acceptance Criteria" not in desc
