"""Tests for Jira tools.

All Jira API calls are mocked via monkeypatch on _make_jira_client so no real
network requests are made. Tests cover happy paths, error cases, and edge cases
for each tool, plus registration in get_tools().
"""

from unittest.mock import MagicMock

from yeaboi.tools import get_tools  # noqa: E402 — stdlib/local separation handled by ruff
from yeaboi.tools.jira import (
    _MISSING_CONFIG_MSG,
    _create_issue_with_epic_link,
    _jira_error_msg,
    jira_create_epic,
    jira_create_sprint,
    jira_create_story,
    jira_fetch_velocity,
    jira_read_board,
)

# ---------------------------------------------------------------------------
# Helpers — build mock Jira objects
# ---------------------------------------------------------------------------


def _make_board(board_id: int = 1, name: str = "My Board") -> MagicMock:
    board = MagicMock()
    board.id = board_id
    board.name = name
    return board


def _make_sprint(sprint_id: int, name: str, start: str = "", end: str = "") -> MagicMock:
    sp = MagicMock()
    sp.id = sprint_id
    sp.name = name
    sp.startDate = start
    sp.endDate = end
    return sp


def _make_issue(key: str) -> MagicMock:
    issue = MagicMock()
    issue.key = key
    return issue


def _make_jira_error(status_code: int, text: str = "error") -> MagicMock:
    """Build a mock JIRAError with status_code and text attributes."""
    from jira import JIRAError

    err = JIRAError(text)
    err.status_code = status_code
    err.text = text
    return err


# ---------------------------------------------------------------------------
# _jira_error_msg
# ---------------------------------------------------------------------------


class TestJiraErrorMsg:
    def test_401_auth_error(self):
        err = _make_jira_error(401)
        result = _jira_error_msg(err)
        assert "authentication failed" in result.lower()

    def test_403_permission_error(self):
        err = _make_jira_error(403)
        result = _jira_error_msg(err)
        assert "permission" in result.lower()

    def test_404_not_found(self):
        err = _make_jira_error(404, "Issue does not exist")
        result = _jira_error_msg(err)
        assert "not found" in result.lower()

    def test_429_rate_limit(self):
        err = _make_jira_error(429)
        result = _jira_error_msg(err)
        assert "rate limit" in result.lower()

    def test_unknown_code(self):
        err = _make_jira_error(500, "internal server error")
        result = _jira_error_msg(err)
        assert "500" in result


# ---------------------------------------------------------------------------
# jira_read_board
# ---------------------------------------------------------------------------


class TestJiraReadBoard:
    def test_happy_path_returns_board_info(self, monkeypatch):
        board = _make_board(42, "Sprint Board")
        active = _make_sprint(10, "Sprint 1", "2024-01-01", "2024-01-14")

        mock_client = MagicMock()
        mock_client.boards.return_value = [board]
        mock_client.sprints.side_effect = lambda bid, state: [active] if state == "active" else []
        backlog_result = MagicMock()
        backlog_result.total = 12
        mock_client.search_issues.return_value = backlog_result

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)

        result = jira_read_board.invoke({"project_key": "PROJ"})

        assert "Sprint Board" in result
        assert "42" in result
        assert "Sprint 1" in result
        assert "12" in result

    def test_falls_back_to_env_var(self, monkeypatch):
        board = _make_board(1, "Env Board")
        mock_client = MagicMock()
        mock_client.boards.return_value = [board]
        mock_client.sprints.return_value = []
        backlog = MagicMock()
        backlog.total = 0
        mock_client.search_issues.return_value = backlog

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_PROJECT_KEY", "ENVPROJ")

        # Empty project_key → falls back to env var
        result = jira_read_board.invoke({"project_key": ""})

        # boards() called with the env var key
        mock_client.boards.assert_called_once_with(projectKeyOrID="ENVPROJ")
        assert "Env Board" in result

    def test_no_active_sprint_shows_none(self, monkeypatch):
        board = _make_board(1, "Board")
        mock_client = MagicMock()
        mock_client.boards.return_value = [board]
        mock_client.sprints.return_value = []
        backlog = MagicMock()
        backlog.total = 5
        mock_client.search_issues.return_value = backlog

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)

        result = jira_read_board.invoke({"project_key": "PROJ"})

        assert "None" in result or "no active sprint" in result.lower()

    def test_no_boards_found_returns_error(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.boards.return_value = []

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)

        result = jira_read_board.invoke({"project_key": "NOPE"})

        assert "Error" in result
        assert "NOPE" in result

    def test_jira_error_401(self, monkeypatch):
        from jira import JIRAError

        mock_client = MagicMock()
        err = JIRAError("auth error")
        err.status_code = 401
        err.text = "Unauthorized"
        mock_client.boards.side_effect = err

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)

        result = jira_read_board.invoke({"project_key": "PROJ"})

        assert "authentication failed" in result.lower()

    def test_missing_config_returns_message(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: None)

        result = jira_read_board.invoke({"project_key": "PROJ"})

        assert result == _MISSING_CONFIG_MSG

    def test_velocity_computed_from_closed_sprints(self, monkeypatch):
        board = _make_board(1, "Board")
        closed1 = _make_sprint(1, "S1")
        closed2 = _make_sprint(2, "S2")
        closed3 = _make_sprint(3, "S3")

        mock_client = MagicMock()
        mock_client.boards.return_value = [board]
        mock_client.sprints.side_effect = lambda bid, state: [] if state == "active" else [closed1, closed2, closed3]
        backlog = MagicMock()
        backlog.total = 0
        mock_client.search_issues.return_value = backlog
        mock_client.sprint_info.side_effect = lambda bid, sid: {"completedPoints": 20}

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)

        result = jira_read_board.invoke({"project_key": "PROJ"})

        assert "20.0" in result


# ---------------------------------------------------------------------------
# jira_create_epic
# ---------------------------------------------------------------------------


class TestJiraCreateEpic:
    def test_happy_path_returns_key_and_url(self, monkeypatch):
        issue = _make_issue("PROJ-1")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://myorg.atlassian.net")

        result = jira_create_epic.invoke({"title": "Auth Epic", "project_key": "PROJ"})

        assert "PROJ-1" in result
        assert "Auth Epic" in result
        assert "https://myorg.atlassian.net/browse/PROJ-1" in result

    def test_custom_issue_type_respected(self, monkeypatch):
        issue = _make_issue("PROJ-2")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://myorg.atlassian.net")

        jira_create_epic.invoke({"title": "Feature X", "project_key": "PROJ", "issue_type": "Feature"})

        fields_used = mock_client.create_issue.call_args[1]["fields"]
        assert fields_used["issuetype"]["name"] == "Feature"

    def test_falls_back_to_env_project_key(self, monkeypatch):
        issue = _make_issue("ENV-1")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_PROJECT_KEY", "ENV")
        monkeypatch.setenv("JIRA_BASE_URL", "https://x.atlassian.net")

        result = jira_create_epic.invoke({"title": "Epic", "project_key": ""})

        fields_used = mock_client.create_issue.call_args[1]["fields"]
        assert fields_used["project"]["key"] == "ENV"
        assert "ENV-1" in result

    def test_jira_error_403(self, monkeypatch):
        from jira import JIRAError

        mock_client = MagicMock()
        err = JIRAError("forbidden")
        err.status_code = 403
        err.text = "Forbidden"
        mock_client.create_issue.side_effect = err

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)

        result = jira_create_epic.invoke({"title": "Epic", "project_key": "PROJ"})

        assert "permission" in result.lower()

    def test_internal_id_included_in_response(self, monkeypatch):
        issue = _make_issue("PROJ-3")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://myorg.atlassian.net")

        result = jira_create_epic.invoke({"title": "Epic", "project_key": "PROJ", "internal_id": "epic-1"})

        assert "Mapping: epic-1 → PROJ-3" in result

    def test_no_mapping_line_when_internal_id_omitted(self, monkeypatch):
        issue = _make_issue("PROJ-4")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://myorg.atlassian.net")

        result = jira_create_epic.invoke({"title": "Epic", "project_key": "PROJ"})

        assert "Mapping:" not in result

    def test_missing_config_returns_message(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: None)

        result = jira_create_epic.invoke({"title": "Epic", "project_key": "PROJ"})

        assert result == _MISSING_CONFIG_MSG


# ---------------------------------------------------------------------------
# jira_create_story
# ---------------------------------------------------------------------------


class TestJiraCreateStory:
    def test_happy_path_returns_key_epic_url(self, monkeypatch):
        issue = _make_issue("PROJ-10")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        result = jira_create_story.invoke({"summary": "Login story", "epic_key": "PROJ-1", "project_key": "PROJ"})

        assert "PROJ-10" in result
        assert "PROJ-1" in result
        assert "https://org.atlassian.net/browse/PROJ-10" in result

    def test_story_points_mapped_to_customfield_10016(self, monkeypatch):
        issue = _make_issue("PROJ-11")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        jira_create_story.invoke({"summary": "S", "epic_key": "PROJ-1", "story_points": 5, "project_key": "PROJ"})

        fields = mock_client.create_issue.call_args[1]["fields"]
        assert fields["customfield_10016"] == 5

    def test_zero_story_points_not_set(self, monkeypatch):
        issue = _make_issue("PROJ-12")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        jira_create_story.invoke({"summary": "S", "epic_key": "PROJ-1", "story_points": 0, "project_key": "PROJ"})

        fields = mock_client.create_issue.call_args[1]["fields"]
        assert "customfield_10016" not in fields

    def test_epic_key_mapped_to_customfield_10014(self, monkeypatch):
        issue = _make_issue("PROJ-13")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        jira_create_story.invoke({"summary": "S", "epic_key": "PROJ-5", "project_key": "PROJ"})

        fields = mock_client.create_issue.call_args[1]["fields"]
        assert fields["customfield_10014"] == "PROJ-5"

    def test_priority_mapped_to_name_dict(self, monkeypatch):
        issue = _make_issue("PROJ-14")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        jira_create_story.invoke({"summary": "S", "epic_key": "PROJ-1", "priority": "High", "project_key": "PROJ"})

        fields = mock_client.create_issue.call_args[1]["fields"]
        assert fields["priority"] == {"name": "High"}

    def test_internal_id_included_in_response(self, monkeypatch):
        issue = _make_issue("PROJ-20")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        result = jira_create_story.invoke(
            {"summary": "S", "epic_key": "PROJ-1", "project_key": "PROJ", "internal_id": "story-3"}
        )

        assert "Mapping: story-3 → PROJ-20" in result

    def test_no_mapping_line_when_internal_id_omitted(self, monkeypatch):
        issue = _make_issue("PROJ-21")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        result = jira_create_story.invoke({"summary": "S", "epic_key": "PROJ-1", "project_key": "PROJ"})

        assert "Mapping:" not in result

    def test_labels_included_in_fields(self, monkeypatch):
        issue = _make_issue("PROJ-30")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        jira_create_story.invoke(
            {"summary": "S", "epic_key": "PROJ-1", "project_key": "PROJ", "labels": ["Code", "Auth"]}
        )

        fields = mock_client.create_issue.call_args[1]["fields"]
        assert fields["labels"] == ["Code", "Auth"]

    def test_labels_omitted_when_empty(self, monkeypatch):
        issue = _make_issue("PROJ-31")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        jira_create_story.invoke({"summary": "S", "epic_key": "PROJ-1", "project_key": "PROJ"})

        fields = mock_client.create_issue.call_args[1]["fields"]
        assert "labels" not in fields

    def test_labels_shown_in_response(self, monkeypatch):
        issue = _make_issue("PROJ-32")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        result = jira_create_story.invoke(
            {"summary": "S", "epic_key": "PROJ-1", "project_key": "PROJ", "labels": ["Code"]}
        )

        assert "Labels: Code" in result

    def test_link_method_epic_link_uses_customfield(self, monkeypatch):
        issue = _make_issue("PROJ-33")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        result = jira_create_story.invoke(
            {"summary": "S", "epic_key": "PROJ-1", "project_key": "PROJ", "link_method": "epic_link"}
        )

        fields = mock_client.create_issue.call_args[1]["fields"]
        assert fields["customfield_10014"] == "PROJ-1"
        assert "customfield_10014" in result

    def test_link_method_parent_uses_parent_field(self, monkeypatch):
        issue = _make_issue("PROJ-34")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        result = jira_create_story.invoke(
            {"summary": "S", "epic_key": "PROJ-1", "project_key": "PROJ", "link_method": "parent"}
        )

        fields = mock_client.create_issue.call_args[1]["fields"]
        assert fields["parent"] == {"key": "PROJ-1"}
        assert "parent" in result

    def test_link_method_auto_fallback_to_parent_on_400(self, monkeypatch):
        from jira import JIRAError

        issue = _make_issue("PROJ-35")
        mock_client = MagicMock()

        # First call raises 400 (customfield_10014 not on screen — next-gen project).
        # Second call succeeds with parent field.
        err = JIRAError("field not on screen")
        err.status_code = 400
        err.text = "Field 'customfield_10014' cannot be set."
        mock_client.create_issue.side_effect = [err, issue]

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        result = jira_create_story.invoke(
            {"summary": "S", "epic_key": "PROJ-1", "project_key": "PROJ", "link_method": "auto"}
        )

        # Second call should use parent field
        assert mock_client.create_issue.call_count == 2
        second_call_fields = mock_client.create_issue.call_args_list[1][1]["fields"]
        assert second_call_fields["parent"] == {"key": "PROJ-1"}
        assert "parent" in result
        assert "PROJ-35" in result

    def test_missing_config_returns_message(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: None)

        result = jira_create_story.invoke({"summary": "S", "epic_key": "PROJ-1", "project_key": "PROJ"})

        assert result == _MISSING_CONFIG_MSG


# ---------------------------------------------------------------------------
# _create_issue_with_epic_link
# ---------------------------------------------------------------------------


class TestCreateIssueWithEpicLink:
    def test_epic_link_method_sets_customfield(self):
        issue = _make_issue("PROJ-1")
        mock_jira = MagicMock()
        mock_jira.create_issue.return_value = issue

        fields: dict = {}
        result_issue, link_field = _create_issue_with_epic_link(mock_jira, fields, "PROJ-5", "epic_link")

        assert fields["customfield_10014"] == "PROJ-5"
        assert link_field == "customfield_10014"
        assert result_issue is issue

    def test_parent_method_sets_parent_field(self):
        issue = _make_issue("PROJ-2")
        mock_jira = MagicMock()
        mock_jira.create_issue.return_value = issue

        fields: dict = {}
        result_issue, link_field = _create_issue_with_epic_link(mock_jira, fields, "PROJ-5", "parent")

        assert fields["parent"] == {"key": "PROJ-5"}
        assert link_field == "parent"
        assert result_issue is issue

    def test_auto_succeeds_with_customfield_on_first_try(self):
        issue = _make_issue("PROJ-3")
        mock_jira = MagicMock()
        mock_jira.create_issue.return_value = issue

        fields: dict = {}
        result_issue, link_field = _create_issue_with_epic_link(mock_jira, fields, "PROJ-5", "auto")

        assert link_field == "customfield_10014"
        assert mock_jira.create_issue.call_count == 1

    def test_auto_falls_back_to_parent_on_400(self):
        from jira import JIRAError

        issue = _make_issue("PROJ-4")
        mock_jira = MagicMock()
        err = JIRAError("field error")
        err.status_code = 400
        err.text = "Field not on screen"
        mock_jira.create_issue.side_effect = [err, issue]

        fields: dict = {}
        result_issue, link_field = _create_issue_with_epic_link(mock_jira, fields, "PROJ-5", "auto")

        assert link_field == "parent"
        assert fields["parent"] == {"key": "PROJ-5"}
        assert mock_jira.create_issue.call_count == 2
        assert result_issue is issue

    def test_auto_does_not_swallow_non_400_errors(self):
        from jira import JIRAError

        mock_jira = MagicMock()
        err = JIRAError("auth error")
        err.status_code = 401
        err.text = "Unauthorized"
        mock_jira.create_issue.side_effect = err

        fields: dict = {}
        try:
            _create_issue_with_epic_link(mock_jira, fields, "PROJ-5", "auto")
            assert False, "Expected JIRAError to propagate"
        except JIRAError:
            pass  # Expected — 401 should not be swallowed


# ---------------------------------------------------------------------------
# jira_create_sprint
# ---------------------------------------------------------------------------


class TestJiraCreateSprint:
    def test_happy_path_returns_sprint_id_and_name(self, monkeypatch):
        sprint = MagicMock()
        sprint.id = 99
        sprint.name = "Sprint Alpha"

        mock_client = MagicMock()
        mock_client.create_sprint.return_value = sprint

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)

        result = jira_create_sprint.invoke({"sprint_name": "Sprint Alpha", "board_id": 42})

        assert "99" in result
        assert "Sprint Alpha" in result
        assert "42" in result

    def test_optional_fields_passed_when_provided(self, monkeypatch):
        sprint = MagicMock()
        sprint.id = 1
        sprint.name = "Sprint 1"

        mock_client = MagicMock()
        mock_client.create_sprint.return_value = sprint

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)

        jira_create_sprint.invoke(
            {
                "sprint_name": "Sprint 1",
                "board_id": 5,
                "goal": "Ship auth",
                "start_date": "2024-01-01",
                "end_date": "2024-01-14",
            }
        )

        call_kwargs = mock_client.create_sprint.call_args[1]
        assert call_kwargs["goal"] == "Ship auth"
        assert call_kwargs["startDate"] == "2024-01-01"
        assert call_kwargs["endDate"] == "2024-01-14"

    def test_optional_fields_omitted_when_empty(self, monkeypatch):
        sprint = MagicMock()
        sprint.id = 2
        sprint.name = "Sprint 2"

        mock_client = MagicMock()
        mock_client.create_sprint.return_value = sprint

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)

        jira_create_sprint.invoke({"sprint_name": "Sprint 2", "board_id": 5})

        call_kwargs = mock_client.create_sprint.call_args[1]
        assert "goal" not in call_kwargs
        assert "startDate" not in call_kwargs
        assert "endDate" not in call_kwargs

    def test_missing_config_returns_message(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: None)

        result = jira_create_sprint.invoke({"sprint_name": "S1", "board_id": 1})

        assert result == _MISSING_CONFIG_MSG


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Input validation edge cases
# ---------------------------------------------------------------------------


class TestJiraInputValidation:
    """Test tool input validation — empty/missing params, edge cases."""

    def test_read_board_empty_key_no_env_returns_error(self, monkeypatch):
        """Empty project_key with no JIRA_PROJECT_KEY env var should return a clear error."""
        mock_client = MagicMock()
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.delenv("JIRA_PROJECT_KEY", raising=False)

        result = jira_read_board.invoke({"project_key": ""})

        assert "Error" in result
        assert "JIRA_PROJECT_KEY" in result

    def test_create_epic_empty_key_no_env_returns_error(self, monkeypatch):
        """create_epic with no project_key and no env var should return an error."""
        mock_client = MagicMock()
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.delenv("JIRA_PROJECT_KEY", raising=False)

        result = jira_create_epic.invoke({"title": "Epic", "project_key": ""})

        assert "Error" in result
        assert "JIRA_PROJECT_KEY" in result

    def test_create_story_empty_key_no_env_returns_error(self, monkeypatch):
        """create_story with no project_key and no env var should return an error."""
        mock_client = MagicMock()
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.delenv("JIRA_PROJECT_KEY", raising=False)

        result = jira_create_story.invoke({"summary": "Story", "epic_key": "PROJ-1", "project_key": ""})

        assert "Error" in result
        assert "JIRA_PROJECT_KEY" in result

    def test_create_sprint_jira_error_returns_message(self, monkeypatch):
        """JIRAError during sprint creation should return a user-friendly message."""
        from jira import JIRAError

        mock_client = MagicMock()
        err = JIRAError("sprint creation failed")
        err.status_code = 500
        err.text = "Internal server error"
        mock_client.create_sprint.side_effect = err

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)

        result = jira_create_sprint.invoke({"sprint_name": "Sprint 1", "board_id": 42})

        assert "Error" in result
        assert "500" in result

    def test_create_epic_whitespace_project_key_falls_back(self, monkeypatch):
        """Whitespace-only project_key should be treated as empty and fall back to env var."""
        issue = _make_issue("ENV-1")
        mock_client = MagicMock()
        mock_client.create_issue.return_value = issue

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_PROJECT_KEY", "ENV")
        monkeypatch.setenv("JIRA_BASE_URL", "https://x.atlassian.net")

        jira_create_epic.invoke({"title": "Epic", "project_key": "   "})

        fields_used = mock_client.create_issue.call_args[1]["fields"]
        assert fields_used["project"]["key"] == "ENV"

    def test_create_epic_generic_exception_returns_error(self, monkeypatch):
        """Non-JIRAError exceptions should be caught and returned as error strings."""
        mock_client = MagicMock()
        mock_client.create_issue.side_effect = ConnectionError("network down")

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)

        result = jira_create_epic.invoke({"title": "Epic", "project_key": "PROJ"})

        assert "Error" in result
        assert "network down" in result

    def test_read_board_generic_exception_returns_error(self, monkeypatch):
        """Non-JIRAError exceptions in read_board should return error strings."""
        mock_client = MagicMock()
        mock_client.boards.side_effect = RuntimeError("unexpected")

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)

        result = jira_read_board.invoke({"project_key": "PROJ"})

        assert "Error" in result
        assert "unexpected" in result


# ---------------------------------------------------------------------------
# jira_fetch_velocity — zero-velocity edge case
# ---------------------------------------------------------------------------


class TestJiraFetchVelocityZeroVelocity:
    """When all closed sprints have zero completed points, team size should still be returned."""

    def test_zero_velocity_returns_team_size_in_json(self, monkeypatch):
        """Zero velocity should return JSON with velocity_error AND jira_team_size."""
        import json

        board = _make_board(1, "Board")
        closed1 = _make_sprint(1, "Sprint 1")

        mock_client = MagicMock()
        mock_client.boards.return_value = [board]
        mock_client.sprints.return_value = [closed1]
        # Zero completed points from sprint report
        mock_client.sprint_info.return_value = {"completedPoints": 0}

        # 2 unique sub-task assignees (no story_points on any issue)
        assignee1, assignee2 = MagicMock(), MagicMock()
        assignee1.accountId = "user-1"
        assignee2.accountId = "user-2"
        sub1 = MagicMock(spec=["fields", "key"])
        sub1.fields = MagicMock(spec=["assignee"])
        sub1.fields.assignee = assignee1
        sub2 = MagicMock(spec=["fields", "key"])
        sub2.fields = MagicMock(spec=["assignee"])
        sub2.fields.assignee = assignee2

        # First search_issues call = JQL fallback (Done issues, no story_points);
        # second call = Sub-task assignees
        done_issue = MagicMock(spec=["fields", "key"])
        done_issue.fields = MagicMock(spec=["assignee"])
        done_issue.fields.assignee = None
        mock_client.search_issues.side_effect = [
            [done_issue],  # JQL fallback — no story_points attr
            [sub1, sub2],  # Sub-task assignees
        ]

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_URL", "https://test.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "test@test.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "tok")
        monkeypatch.setenv("JIRA_PROJECT_KEY", "PROJ")

        result = jira_fetch_velocity.invoke({})
        data = json.loads(result)

        assert data["team_velocity"] == 0
        assert data["jira_team_size"] == 2
        assert "velocity_error" in data

    def test_jql_fallback_computes_velocity_from_customfield(self, monkeypatch):
        """When completedPoints is zero, velocity is computed from Done issues' customfield_10016."""
        import json

        board = _make_board(1, "Board")
        closed1 = _make_sprint(1, "Sprint 1")

        mock_client = MagicMock()
        mock_client.boards.return_value = [board]
        mock_client.sprints.return_value = [closed1]
        mock_client.sprint_info.return_value = {"completedPoints": 0}

        # JQL fallback returns Done issues with customfield_10016 (Jira Cloud story points)
        issue1 = MagicMock(spec=["fields", "key"])
        issue1.fields = MagicMock(spec=["customfield_10016", "story_points", "assignee"])
        issue1.fields.customfield_10016 = 5
        issue1.fields.assignee = None
        issue2 = MagicMock(spec=["fields", "key"])
        issue2.fields = MagicMock(spec=["customfield_10016", "story_points", "assignee"])
        issue2.fields.customfield_10016 = 8
        issue2.fields.assignee = None

        # Sub-task assignees
        sub = MagicMock(spec=["fields", "key"])
        sub.fields = MagicMock(spec=["assignee"])
        assignee = MagicMock()
        assignee.accountId = "user-1"
        sub.fields.assignee = assignee

        mock_client.search_issues.side_effect = [
            [issue1, issue2],  # JQL fallback — 5+8=13 pts
            [sub],  # Sub-task assignees
        ]

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_URL", "https://test.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "test@test.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "tok")
        monkeypatch.setenv("JIRA_PROJECT_KEY", "PROJ")

        result = jira_fetch_velocity.invoke({})
        data = json.loads(result)

        assert data["team_velocity"] == 13
        assert data["jira_team_size"] == 1
        assert data["per_dev_velocity"] == 13.0
        assert "velocity_error" not in data


class TestJiraListSprints:
    def test_lists_and_normalizes_sprints(self, monkeypatch):
        from yeaboi.tools.jira import jira_list_sprints

        board = _make_board(7, "Board")
        closed = _make_sprint(1, "Sprint 1", "2026-06-01T00:00:00.000Z", "2026-06-14T00:00:00.000Z")
        active = _make_sprint(2, "Sprint 2", "2026-06-15T00:00:00.000Z", "2026-06-28T00:00:00.000Z")
        mock_client = MagicMock()
        mock_client.boards.return_value = [board]
        mock_client.sprints.side_effect = lambda bid, state: {
            "closed": [closed],
            "active": [active],
            "future": [],
        }[state]
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)

        out = jira_list_sprints("PROJ")
        assert [s["name"] for s in out] == ["Sprint 1", "Sprint 2"]  # sorted by start, newest last
        assert out[0] == {
            "id": 1,
            "name": "Sprint 1",
            "start_date": "2026-06-01",
            "end_date": "2026-06-14",
            "state": "closed",
        }
        assert out[1]["state"] == "active"

    def test_empty_when_unconfigured(self, monkeypatch):
        from yeaboi.tools.jira import jira_list_sprints

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: None)
        assert jira_list_sprints("PROJ") == []

    def test_empty_when_no_boards(self, monkeypatch):
        from yeaboi.tools.jira import jira_list_sprints

        mock_client = MagicMock()
        mock_client.boards.return_value = []
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        assert jira_list_sprints("PROJ") == []


class TestJiraToolsRegistered:
    def test_all_four_jira_tools_in_get_tools(self):
        tools = get_tools()
        names = {t.name for t in tools}
        expected = {"jira_read_board", "jira_create_epic", "jira_create_story", "jira_create_sprint"}
        assert expected.issubset(names), f"Missing Jira tools: {expected - names}"


# ---------------------------------------------------------------------------
# Poker helpers — per-issue fetch + field update
# ---------------------------------------------------------------------------


def _make_poker_issue(
    key: str,
    summary: str = "Do the thing",
    description: str = "Details",
    points=None,
    status_name: str = "To Do",
    assignee_name: str = "",
    type_name: str = "Story",
    acceptance_field: str = "",
    acceptance: str = "",
):
    issue = MagicMock(spec=["fields", "key"])
    issue.key = key
    spec = ["summary", "description", "status", "assignee", "customfield_10016", "story_points", "issuetype"]
    if acceptance_field:
        spec.append(acceptance_field)
    issue.fields = MagicMock(spec=spec)
    issue.fields.summary = summary
    issue.fields.description = description
    issue.fields.customfield_10016 = points
    issue.fields.story_points = None
    itype = MagicMock()
    itype.name = type_name  # post-creation assignment — the name= ctor kwarg means something else on mocks
    issue.fields.issuetype = itype
    if acceptance_field:
        setattr(issue.fields, acceptance_field, acceptance)
    status = MagicMock()
    status.name = status_name
    issue.fields.status = status
    if assignee_name:
        assignee = MagicMock(spec=["displayName", "emailAddress", "accountType"])
        assignee.displayName = assignee_name
        assignee.emailAddress = ""
        assignee.accountType = "atlassian"
        issue.fields.assignee = assignee
    else:
        issue.fields.assignee = None
    return issue


class TestJiraSprintIssues:
    def test_normalizes_rows(self, monkeypatch):
        from yeaboi.tools.jira import jira_sprint_issues

        mock_client = MagicMock()
        mock_client.search_issues.return_value = [
            _make_poker_issue("PROJ-1", points=5, assignee_name="Alex"),
            _make_poker_issue("PROJ-2"),
        ]
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_base_url", lambda: "https://x.atlassian.net")

        out = jira_sprint_issues(42, "PROJ")
        assert len(out) == 2
        assert out[0] == {
            "source": "jira",
            "key": "PROJ-1",
            "summary": "Do the thing",
            "description": "Details",
            "story_points": 5.0,
            "state": "To Do",
            "assignee": "Alex",
            "url": "https://x.atlassian.net/browse/PROJ-1",
            "type": "Story",
            "acceptance": "",
        }
        assert out[1]["story_points"] is None
        assert out[1]["assignee"] == ""
        # The sprint id is interpolated as an int and the project scopes the JQL.
        jql = mock_client.search_issues.call_args[0][0]
        assert "sprint = 42" in jql and 'project = "PROJ"' in jql
        # Sub-tasks are excluded server-side, before the ORDER BY clause.
        assert "issuetype NOT IN subTaskIssueTypes()" in jql
        assert jql.index("subTaskIssueTypes()") < jql.index("ORDER BY")

    def test_include_types_filters_by_name_unknown_kept(self, monkeypatch):
        from yeaboi.tools.jira import jira_sprint_issues

        mock_client = MagicMock()
        mock_client.search_issues.return_value = [
            _make_poker_issue("P-1", type_name="Story"),
            _make_poker_issue("P-2", type_name="Bug"),
            _make_poker_issue("P-3", type_name="Task"),
            _make_poker_issue("P-4", type_name="Spike"),  # custom type — must survive any filter
        ]
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setattr("yeaboi.tools.jira._acceptance_field_cache", "")

        out = jira_sprint_issues(42, "PROJ", include_types=("story",))
        assert [r["key"] for r in out] == ["P-1", "P-4"]
        out = jira_sprint_issues(42, "PROJ", include_types=("story", "bug", "task"))
        assert [r["key"] for r in out] == ["P-1", "P-2", "P-3", "P-4"]

    def test_acceptance_field_discovered_and_requested(self, monkeypatch):
        from yeaboi.tools import jira as jira_mod

        mock_client = MagicMock()
        mock_client.fields.return_value = [
            {"id": "customfield_10500", "name": "Sprint"},
            {"id": "customfield_10101", "name": "Acceptance Criteria"},
        ]
        mock_client.search_issues.return_value = [
            _make_poker_issue("P-1", acceptance_field="customfield_10101", acceptance="AC1: works offline")
        ]
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setattr("yeaboi.tools.jira._acceptance_field_cache", None)

        out = jira_mod.jira_sprint_issues(42, "PROJ")
        assert out[0]["acceptance"] == "AC1: works offline"
        fields_param = mock_client.search_issues.call_args.kwargs["fields"]
        assert fields_param.endswith(",customfield_10101")
        # Successful scan is cached — a second fetch must not re-list fields.
        assert jira_mod._acceptance_field_cache == "customfield_10101"

    def test_no_acceptance_field_caches_empty(self, monkeypatch):
        from yeaboi.tools import jira as jira_mod

        mock_client = MagicMock()
        mock_client.fields.return_value = [{"id": "customfield_10500", "name": "Sprint"}]
        mock_client.search_issues.return_value = [_make_poker_issue("P-1")]
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setattr("yeaboi.tools.jira._acceptance_field_cache", None)

        out = jira_mod.jira_sprint_issues(42, "PROJ")
        assert out[0]["acceptance"] == ""
        assert mock_client.search_issues.call_args.kwargs["fields"] == jira_mod._POKER_ISSUE_FIELDS
        assert jira_mod._acceptance_field_cache == ""  # "no such field" is a stable answer

    def test_discovery_failure_not_cached(self, monkeypatch):
        from yeaboi.tools import jira as jira_mod

        mock_client = MagicMock()
        mock_client.fields.side_effect = RuntimeError("api down")
        mock_client.search_issues.return_value = [_make_poker_issue("P-1")]
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setattr("yeaboi.tools.jira._acceptance_field_cache", None)

        out = jira_mod.jira_sprint_issues(42, "PROJ")
        assert out[0]["acceptance"] == ""
        assert jira_mod._acceptance_field_cache is None  # transient failure → retry next fetch

    def test_empty_when_unconfigured(self, monkeypatch):
        from yeaboi.tools.jira import jira_sprint_issues

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: None)
        assert jira_sprint_issues(42) == []

    def test_empty_on_bad_sprint_id(self, monkeypatch):
        from yeaboi.tools.jira import jira_sprint_issues

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: MagicMock())
        assert jira_sprint_issues("not-a-number") == []

    def test_empty_on_api_error(self, monkeypatch):
        from yeaboi.tools.jira import jira_sprint_issues

        mock_client = MagicMock()
        mock_client.search_issues.side_effect = _make_jira_error(500, "boom")
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        assert jira_sprint_issues(42) == []


class TestJiraBacklogIssues:
    def test_uses_backlog_jql(self, monkeypatch):
        from yeaboi.tools.jira import jira_backlog_issues

        mock_client = MagicMock()
        mock_client.search_issues.return_value = [_make_poker_issue("PROJ-9")]
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_base_url", lambda: "https://x.atlassian.net")

        out = jira_backlog_issues("PROJ")
        assert [r["key"] for r in out] == ["PROJ-9"]
        jql = mock_client.search_issues.call_args[0][0]
        assert "sprint IS EMPTY" in jql and "statusCategory != Done" in jql
        assert "issuetype NOT IN subTaskIssueTypes()" in jql
        assert jql.index("subTaskIssueTypes()") < jql.index("ORDER BY")

    def test_empty_without_project_key(self, monkeypatch):
        from yeaboi.tools.jira import jira_backlog_issues

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: MagicMock())
        monkeypatch.setattr("yeaboi.tools.jira.get_jira_project_key", lambda: None)
        assert jira_backlog_issues("") == []


class TestJiraUpdateIssueFields:
    def _client_with_issue(self, monkeypatch):
        mock_client = MagicMock()
        mock_issue = MagicMock()
        mock_client.issue.return_value = mock_issue
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)
        return mock_client, mock_issue

    def test_updates_all_fields(self, monkeypatch):
        from yeaboi.tools.jira import jira_update_issue_fields

        _client, issue = self._client_with_issue(monkeypatch)
        ok, err = jira_update_issue_fields("PROJ-1", summary="New", description="Desc", story_points=8)
        assert (ok, err) == (True, "")
        fields = issue.update.call_args.kwargs["fields"]
        assert fields == {"summary": "New", "description": "Desc", "customfield_10016": 8.0}

    def test_noop_when_nothing_to_update(self, monkeypatch):
        from yeaboi.tools.jira import jira_update_issue_fields

        _client, issue = self._client_with_issue(monkeypatch)
        assert jira_update_issue_fields("PROJ-1") == (True, "")
        issue.update.assert_not_called()

    def test_unconfigured_returns_error_tuple(self, monkeypatch):
        from yeaboi.tools.jira import jira_update_issue_fields

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: None)
        ok, err = jira_update_issue_fields("PROJ-1", story_points=5)
        assert ok is False
        assert "not configured" in err

    def test_auth_error_folded_into_tuple(self, monkeypatch):
        from yeaboi.tools.jira import jira_update_issue_fields

        _client, issue = self._client_with_issue(monkeypatch)
        issue.update.side_effect = _make_jira_error(401)
        ok, err = jira_update_issue_fields("PROJ-1", story_points=5)
        assert ok is False
        assert "authentication" in err.lower()

    def test_discovers_story_points_field_and_retries(self, monkeypatch):
        import yeaboi.tools.jira as jira_mod
        from yeaboi.tools.jira import jira_update_issue_fields

        monkeypatch.setattr(jira_mod, "_story_points_field_cache", "")
        mock_client, issue = self._client_with_issue(monkeypatch)
        # First update rejects the default field id; discovery finds the real one.
        issue.update.side_effect = [_make_jira_error(400, "Field 'customfield_10016' cannot be set"), None]
        mock_client.fields.return_value = [
            {"id": "customfield_10999", "name": "Story point estimate"},
            {"id": "customfield_10014", "name": "Epic Link"},
        ]

        ok, err = jira_update_issue_fields("PROJ-1", story_points=5)
        assert (ok, err) == (True, "")
        retry_fields = issue.update.call_args_list[1].kwargs["fields"]
        assert retry_fields == {"customfield_10999": 5.0}
        # The discovered id is cached for subsequent updates.
        assert jira_mod._story_points_field_cache == "customfield_10999"
