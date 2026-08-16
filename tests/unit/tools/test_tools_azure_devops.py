"""Tests for Azure DevOps read-only tools.

All Azure DevOps API calls are mocked via unittest.mock.patch on _make_connection
so no real network requests are made. Tests cover happy paths, error cases, and
edge cases for each tool and the _parse_azdo_url helper.
"""

from datetime import UTC
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from azure.devops.exceptions import AzureDevOpsServiceError

from yeaboi.tools import get_tools
from yeaboi.tools.azure_devops import (
    _azdo_error_msg,
    _parse_azdo_url,
    azdevops_changed_files,
    azdevops_list_projects,
    azdevops_list_work_items,
    azdevops_read_file,
    azdevops_read_repo,
    azdevops_recent_commits,
)


class _FakeAzdoError(AzureDevOpsServiceError):
    """Test-only subclass that bypasses the complex __init__.

    AzureDevOpsServiceError normally requires a wrapped SDK exception object.
    This subclass lets us instantiate with a plain string for testing.
    """

    def __init__(self, message: str):
        Exception.__init__(self, message)
        self.inner_exception = None
        self.message = message
        self.exception_id = None
        self.type_name = None
        self.type_key = None
        self.error_code = None
        self.event_id = None
        self.custom_properties = {}

    def __str__(self) -> str:
        return self.message


_VALID_URL = "https://dev.azure.com/myorg/MyProject/_git/my-repo"

# ---------------------------------------------------------------------------
# _parse_azdo_url
# ---------------------------------------------------------------------------


class TestParseAzdoUrl:
    def test_valid_url(self):
        org_url, project, repo = _parse_azdo_url(_VALID_URL)
        assert org_url == "https://dev.azure.com/myorg"
        assert project == "MyProject"
        assert repo == "my-repo"

    def test_trailing_slash(self):
        org_url, project, repo = _parse_azdo_url(_VALID_URL + "/")
        assert org_url == "https://dev.azure.com/myorg"
        assert project == "MyProject"
        assert repo == "my-repo"

    def test_git_suffix(self):
        org_url, project, repo = _parse_azdo_url(_VALID_URL + ".git")
        assert org_url == "https://dev.azure.com/myorg"
        assert project == "MyProject"
        assert repo == "my-repo"

    def test_invalid_url_raises(self):
        import pytest

        with pytest.raises(ValueError, match="dev.azure.com"):
            _parse_azdo_url("https://github.com/owner/repo")

    def test_missing_git_segment_raises(self):
        import pytest

        with pytest.raises(ValueError):
            _parse_azdo_url("https://dev.azure.com/myorg/MyProject/my-repo")


class TestAnalysisScopeHelpers:
    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_list_projects_is_sorted_and_paginated(self, mock_connection):
        first = []
        for idx in range(100):
            project = MagicMock()
            project.name = f"P{idx:03}"
            first.append(project)
        another = MagicMock()
        another.name = "Another"
        second = [another]
        core = mock_connection.return_value.clients.get_core_client.return_value
        core.get_projects.side_effect = [first, second]

        result = azdevops_list_projects()

        assert len(result) == 101
        assert result[0] == "Another"
        assert core.get_projects.call_count == 2

    @patch("yeaboi.tools.azure_devops._make_git_client")
    def test_recent_commits_reuses_analysis_inventory(self, mock_git):
        client = mock_git.return_value
        commit = MagicMock()
        commit.commit_id = "abc123"
        commit.comment = "Selected change"
        commit.author.name = "Alice"
        commit.author.email = "alice@example.com"
        commit.author.date = "2026-07-25"
        client.get_commits.return_value = [commit]

        items = azdevops_recent_commits(
            "Project",
            days=30,
            include_repository=True,
            repositories=[
                {
                    "provider": "azdo",
                    "container": "Project",
                    "name": "api",
                    "repo_id": "repo-id",
                    "url": "https://example.test/api",
                }
            ],
        )

        client.get_repositories.assert_not_called()
        client.get_commits.assert_called_once()
        assert items[0]["repository"] == "api"
        assert items[0]["commit_id"] == "abc123"

    @patch("yeaboi.tools.azure_devops._make_git_client")
    def test_changed_files_labels_commit_and_pr_attribution(self, mock_git):
        client = mock_git.return_value
        commit_change = MagicMock()
        commit_change.item.path = "/src/a.py"
        commit_change.change_type = "edit"
        pr_change = MagicMock()
        pr_change.item.path = "/src/b.py"
        pr_change.change_type = "add"
        client.get_changes.return_value = [commit_change]
        iteration = MagicMock()
        iteration.id = 2
        client.get_pull_request_iterations.return_value = [iteration]
        client.get_pull_request_iteration_changes.return_value = [pr_change]

        files = azdevops_changed_files(
            "Project",
            "repo",
            [
                {"kind": "commit", "commit_id": "abc", "author": "Alice"},
                {"kind": "pr", "pr_id": 7, "author": "Alice"},
            ],
        )

        assert [(f["path"], f["attribution"], f["confidence"]) for f in files] == [
            ("src/a.py", "authored_commit", "high"),
            ("src/b.py", "authored_pr", "medium"),
        ]

    @patch("yeaboi.tools.azure_devops._make_git_client")
    def test_changed_files_unwraps_real_sdk_response_models(self, mock_git):
        from azure.devops.v7_1.git.models import GitCommitChanges, GitPullRequestIterationChanges

        client = mock_git.return_value
        commit_change = MagicMock()
        commit_change.item.path = "/src/a.py"
        commit_change.change_type = "edit"
        pr_change = MagicMock()
        pr_change.item.path = "/src/b.py"
        pr_change.change_type = "add"
        client.get_changes.return_value = GitCommitChanges(changes=[commit_change])
        iteration = MagicMock()
        iteration.id = 2
        client.get_pull_request_iterations.return_value = [iteration]
        client.get_pull_request_iteration_changes.return_value = GitPullRequestIterationChanges(
            change_entries=[pr_change],
            next_skip=1,
            next_top=0,
        )

        files = azdevops_changed_files(
            "Project",
            "repo",
            [
                {"kind": "commit", "commit_id": "abc", "author": "Alice"},
                {"kind": "pr", "pr_id": 7, "author": "Alice"},
            ],
        )

        assert [item["path"] for item in files] == ["src/a.py", "src/b.py"]
        assert not any(item.get("status") == "failed" for item in files)


# ---------------------------------------------------------------------------
# Helpers — build mock AzDO objects
# ---------------------------------------------------------------------------


def _make_item(path: str, obj_type: str = "blob") -> MagicMock:
    item = MagicMock()
    item.path = path
    item.git_object_type = obj_type
    return item


def _make_work_item(wi_id: int, wi_type: str, title: str, state: str, assignee: str | None = None) -> MagicMock:
    wi = MagicMock()
    wi.id = wi_id
    assigned_value = {"displayName": assignee} if assignee else None
    wi.fields = {
        "System.Id": wi_id,
        "System.WorkItemType": wi_type,
        "System.Title": title,
        "System.State": state,
        "System.AssignedTo": assigned_value,
    }
    return wi


# ---------------------------------------------------------------------------
# azdevops_read_repo
# ---------------------------------------------------------------------------


class TestAzdevopsReadRepo:
    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_normal_tree_returned(self, mock_make_conn):
        items = [
            _make_item("/src", "tree"),
            _make_item("/src/main.py", "blob"),
            _make_item("/pyproject.toml", "blob"),
            _make_item("/README.md", "blob"),
        ]
        mock_make_conn.return_value.clients.get_git_client.return_value.get_items.return_value = items

        result = azdevops_read_repo.invoke({"repo_url": _VALID_URL})

        assert "MyProject/my-repo" in result
        assert "pyproject.toml" in result
        assert "README.md" in result

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_empty_repo(self, mock_make_conn):
        mock_make_conn.return_value.clients.get_git_client.return_value.get_items.return_value = []

        result = azdevops_read_repo.invoke({"repo_url": _VALID_URL})

        assert "MyProject/my-repo" in result
        assert "Key files" not in result

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_service_error(self, mock_make_conn):
        mock_make_conn.return_value.clients.get_git_client.return_value.get_items.side_effect = RuntimeError(
            "TF401019: The Git repository was not found"
        )

        result = azdevops_read_repo.invoke({"repo_url": _VALID_URL})

        assert "Error" in result

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_generic_error(self, mock_make_conn):
        mock_make_conn.side_effect = RuntimeError("connection refused")

        result = azdevops_read_repo.invoke({"repo_url": _VALID_URL})

        assert "Error" in result

    def test_invalid_url_returns_error(self):
        result = azdevops_read_repo.invoke({"repo_url": "https://github.com/owner/repo"})

        assert "Error" in result


# ---------------------------------------------------------------------------
# azdevops_read_file
# ---------------------------------------------------------------------------


class TestAzdevopsReadFile:
    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_file_found_and_decoded(self, mock_make_conn):
        content = b"name = 'my-project'\nversion = '1.0'\n"
        mock_make_conn.return_value.clients.get_git_client.return_value.get_item_content.return_value = iter([content])

        result = azdevops_read_file.invoke({"repo_url": _VALID_URL, "file_path": "/pyproject.toml"})

        assert "pyproject.toml" in result
        assert "name = 'my-project'" in result
        assert "[Truncated" not in result

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_file_not_found(self, mock_make_conn):
        mock_make_conn.return_value.clients.get_git_client.return_value.get_item_content.side_effect = RuntimeError(
            "TF401019: File not found"
        )

        result = azdevops_read_file.invoke({"repo_url": _VALID_URL, "file_path": "/missing.py"})

        assert "Error" in result

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_truncation_at_8000_chars(self, mock_make_conn):
        long_content = ("x" * 10_000).encode()
        mock_make_conn.return_value.clients.get_git_client.return_value.get_item_content.return_value = iter(
            [long_content]
        )

        result = azdevops_read_file.invoke({"repo_url": _VALID_URL, "file_path": "/big.py"})

        assert "[Truncated at 8000 characters]" in result
        assert "x" * 8000 in result
        assert "x" * 8001 not in result

    def test_invalid_url_returns_error(self):
        result = azdevops_read_file.invoke({"repo_url": "not-a-url", "file_path": "/any.py"})

        assert "Error" in result


# ---------------------------------------------------------------------------
# azdevops_list_work_items
# ---------------------------------------------------------------------------


class TestAzdevopsListWorkItems:
    def _setup_wit_client(self, mock_make_conn, work_items: list) -> MagicMock:
        wit_client = MagicMock()
        mock_make_conn.return_value.clients.get_work_item_tracking_client.return_value = wit_client

        # query_by_wiql returns a result with .work_items = list of lightweight refs (id only)
        query_result = MagicMock()
        query_result.work_items = [MagicMock(id=wi.id) for wi in work_items]
        wit_client.query_by_wiql.return_value = query_result

        # get_work_items returns the full work item objects
        wit_client.get_work_items.return_value = work_items
        return wit_client

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_items_returned(self, mock_make_conn):
        work_items = [
            _make_work_item(1, "Bug", "Fix login crash", "Active", "Jane Smith"),
            _make_work_item(2, "Task", "Update docs", "Active"),
        ]
        self._setup_wit_client(mock_make_conn, work_items)

        result = azdevops_list_work_items.invoke({"repo_url": _VALID_URL})

        assert "#1" in result
        assert "Bug" in result
        assert "Fix login crash" in result
        assert "Jane Smith" in result
        assert "#2" in result
        assert "Unassigned" in result

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_empty_list(self, mock_make_conn):
        wit_client = MagicMock()
        mock_make_conn.return_value.clients.get_work_item_tracking_client.return_value = wit_client
        query_result = MagicMock()
        query_result.work_items = []
        wit_client.query_by_wiql.return_value = query_result

        result = azdevops_list_work_items.invoke({"repo_url": _VALID_URL})

        assert "No work items found" in result

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_state_all_skips_filter(self, mock_make_conn):
        """state='All' must omit the state clause from the WIQL query."""
        work_items = [_make_work_item(1, "Story", "Add feature", "Closed")]
        wit_client = self._setup_wit_client(mock_make_conn, work_items)

        azdevops_list_work_items.invoke({"repo_url": _VALID_URL, "state": "All"})

        # Inspect the Wiql object passed to query_by_wiql
        wiql_obj = wit_client.query_by_wiql.call_args[0][0]
        assert "System.State" not in wiql_obj.query

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_service_error(self, mock_make_conn):
        mock_make_conn.return_value.clients.get_work_item_tracking_client.side_effect = RuntimeError(
            "TF401001: Access denied"
        )

        result = azdevops_list_work_items.invoke({"repo_url": _VALID_URL})

        assert "Error" in result

    def test_invalid_url_returns_error(self):
        result = azdevops_list_work_items.invoke({"repo_url": "bad-url", "state": "Active"})

        assert "Error" in result


# ---------------------------------------------------------------------------
# get_tools() — now returns 7 tools
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _azdo_error_msg — user-friendly HTTP error messages
# ---------------------------------------------------------------------------


class TestAzdoErrorMessages:
    def test_401_authentication_failed(self):
        e = _FakeAzdoError("401 Unauthorized")
        assert "Authentication failed" in _azdo_error_msg(e)
        assert "AZURE_DEVOPS_TOKEN" in _azdo_error_msg(e)

    def test_403_access_denied(self):
        e = _FakeAzdoError("403 Forbidden")
        assert "Access denied" in _azdo_error_msg(e)
        assert "PAT" in _azdo_error_msg(e)

    def test_403_access_denied_text(self):
        e = _FakeAzdoError("access denied to resource")
        assert "Access denied" in _azdo_error_msg(e)

    def test_429_throttling(self):
        e = _FakeAzdoError("429 Too Many Requests")
        assert "throttling" in _azdo_error_msg(e)

    def test_503_throttling(self):
        e = _FakeAzdoError("503 Service Unavailable")
        assert "throttling" in _azdo_error_msg(e)

    def test_404_not_found(self):
        e = _FakeAzdoError("404 Not Found")
        assert "not found" in _azdo_error_msg(e).lower()

    def test_unknown_error_returns_str(self):
        e = _FakeAzdoError("something weird happened")
        result = _azdo_error_msg(e)
        assert result.startswith("Error:")

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_azdo_service_error_401_in_read_repo(self, mock_make_conn):
        mock_make_conn.return_value.clients.get_git_client.return_value.get_items.side_effect = _FakeAzdoError(
            "401 Unauthorized"
        )

        result = azdevops_read_repo.invoke({"repo_url": _VALID_URL})

        assert "Authentication failed" in result

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_azdo_service_error_403_in_read_file(self, mock_make_conn):
        mock_make_conn.return_value.clients.get_git_client.return_value.get_item_content.side_effect = _FakeAzdoError(
            "403 Forbidden"
        )

        result = azdevops_read_file.invoke({"repo_url": _VALID_URL, "file_path": "/any.py"})

        assert "Access denied" in result

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_azdo_service_error_429_in_work_items(self, mock_make_conn):
        mock_make_conn.return_value.clients.get_work_item_tracking_client.return_value.query_by_wiql.side_effect = (
            _FakeAzdoError("429 Too Many Requests")
        )

        result = azdevops_list_work_items.invoke({"repo_url": _VALID_URL})

        assert "throttling" in result


# ---------------------------------------------------------------------------
# azdevops_list_work_items — truncation note
# ---------------------------------------------------------------------------


class TestWorkItemsTruncationNote:
    def _setup_wit_client(self, mock_make_conn, work_items: list) -> MagicMock:
        wit_client = MagicMock()
        mock_make_conn.return_value.clients.get_work_item_tracking_client.return_value = wit_client
        query_result = MagicMock()
        query_result.work_items = [MagicMock(id=wi.id) for wi in work_items]
        wit_client.query_by_wiql.return_value = query_result
        wit_client.get_work_items.return_value = work_items
        return wit_client

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_truncation_note_when_at_cap(self, mock_make_conn):
        # Exactly max_items returned — note should appear
        work_items = [_make_work_item(i, "Task", f"Task {i}", "Active") for i in range(1, 6)]
        self._setup_wit_client(mock_make_conn, work_items)

        result = azdevops_list_work_items.invoke({"repo_url": _VALID_URL, "max_items": 5})

        assert "increase max_items to see more" in result

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_no_truncation_note_when_under_cap(self, mock_make_conn):
        # Fewer items than max_items — no note expected
        work_items = [_make_work_item(i, "Task", f"Task {i}", "Active") for i in range(1, 4)]
        self._setup_wit_client(mock_make_conn, work_items)

        result = azdevops_list_work_items.invoke({"repo_url": _VALID_URL, "max_items": 10})

        assert "increase max_items" not in result


# ---------------------------------------------------------------------------
# Write tools: azdevops_create_epic, azdevops_create_story
# ---------------------------------------------------------------------------


class TestAzdevopsCreateEpic:
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_creates_epic(self, mock_clients, _):
        mock_wit = MagicMock()
        mock_clients.return_value = (mock_wit, MagicMock())
        wi = MagicMock()
        wi.id = 42
        mock_wit.create_work_item.return_value = wi

        from yeaboi.tools.azure_devops import azdevops_create_epic

        result = azdevops_create_epic.invoke({"title": "My Epic", "description": "desc"})
        assert "42" in result
        assert "My Epic" in result
        mock_wit.create_work_item.assert_called_once()

    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="")
    def test_missing_project(self, _):
        from yeaboi.tools.azure_devops import azdevops_create_epic

        result = azdevops_create_epic.invoke({"title": "Epic"})
        assert "Error" in result
        assert "project" in result.lower()


class TestAzdevopsCreateStory:
    @patch("yeaboi.tools.azure_devops.get_azure_devops_org_url", return_value="https://dev.azure.com/org")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_creates_story_with_epic_link(self, mock_clients, *_):
        mock_wit = MagicMock()
        mock_clients.return_value = (mock_wit, MagicMock())
        wi = MagicMock()
        wi.id = 101
        mock_wit.create_work_item.return_value = wi

        from yeaboi.tools.azure_devops import azdevops_create_story

        result = azdevops_create_story.invoke(
            {
                "summary": "Login feature",
                "epic_id": "42",
                "story_points": 5,
                "priority": 2,
            }
        )
        assert "101" in result
        assert "Login feature" in result
        # Verify parent link was included in the document
        call_args = mock_wit.create_work_item.call_args
        document = call_args.kwargs.get("document") or call_args[1].get("document") or call_args[0][0]
        has_parent_link = any(getattr(op, "path", "") == "/relations/-" for op in document)
        assert has_parent_link, "Expected parent link in document"


# ---------------------------------------------------------------------------
# Read tools: azdevops_read_board, azdevops_fetch_velocity, azdevops_fetch_active_iteration
# ---------------------------------------------------------------------------


class TestAzdevopsReadBoard:
    @patch("yeaboi.tools.azure_devops.get_azure_devops_team", return_value="MyTeam")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_returns_board_info(self, mock_clients, *_):
        from datetime import datetime, timedelta

        mock_wit = MagicMock()
        mock_work = MagicMock()
        mock_clients.return_value = (mock_wit, mock_work)

        # Mock current iteration with dates that bracket "now"
        now = datetime.now(UTC)
        cur_iter = MagicMock()
        cur_iter.name = "Sprint 42"
        cur_iter.attributes.start_date = now - timedelta(days=7)
        cur_iter.attributes.finish_date = now + timedelta(days=7)
        mock_work.get_team_iterations.return_value = [cur_iter]

        from yeaboi.tools.azure_devops import azdevops_read_board

        result = azdevops_read_board.invoke({})
        assert "MyProject" in result
        assert "Sprint 42" in result

    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="")
    def test_missing_project(self, _):
        from yeaboi.tools.azure_devops import azdevops_read_board

        result = azdevops_read_board.invoke({})
        assert "Error" in result


class TestAzdevopsListSprints:
    @patch("yeaboi.tools.azure_devops.get_azure_devops_team", return_value="MyTeam")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_lists_and_classifies_iterations(self, mock_clients, *_):
        from datetime import datetime, timedelta

        mock_work = MagicMock()
        mock_clients.return_value = (MagicMock(), mock_work)
        now = datetime.now(UTC)
        past = MagicMock()
        past.name = "Sprint 1"
        past.attributes.start_date = now - timedelta(days=28)
        past.attributes.finish_date = now - timedelta(days=14)
        cur = MagicMock()
        cur.name = "Sprint 2"
        cur.attributes.start_date = now - timedelta(days=7)
        cur.attributes.finish_date = now + timedelta(days=7)
        mock_work.get_team_iterations.return_value = [cur, past]  # unsorted input

        from yeaboi.tools.azure_devops import azdevops_list_sprints

        out = azdevops_list_sprints("MyProject")
        assert [s["name"] for s in out] == ["Sprint 1", "Sprint 2"]  # sorted by start, newest last
        assert out[0]["state"] == "closed"
        assert out[1]["state"] == "active"
        assert out[0]["start_date"] == (now - timedelta(days=28)).strftime("%Y-%m-%d")

    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="")
    def test_missing_project_returns_empty(self, _):
        from yeaboi.tools.azure_devops import azdevops_list_sprints

        assert azdevops_list_sprints() == []


class TestAzdevopsFetchActiveIteration:
    @patch("yeaboi.tools.azure_devops.get_azure_devops_team", return_value="MyTeam")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_returns_active_iteration(self, mock_clients, *_):
        from datetime import datetime, timedelta

        mock_work = MagicMock()
        mock_clients.return_value = (MagicMock(), mock_work)

        now = datetime.now(UTC)
        cur_iter = MagicMock()
        cur_iter.name = "Sprint 42"
        cur_iter.attributes.start_date = now - timedelta(days=7)
        cur_iter.attributes.finish_date = now + timedelta(days=7)
        mock_work.get_team_iterations.return_value = [cur_iter]

        from yeaboi.tools.azure_devops import azdevops_fetch_active_iteration

        result = azdevops_fetch_active_iteration.invoke({})
        assert "Sprint 42" in result
        assert "42" in result

    @patch("yeaboi.tools.azure_devops.get_azure_devops_team", return_value="MyTeam")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_no_active_iteration(self, mock_clients, *_):
        mock_work = MagicMock()
        mock_clients.return_value = (MagicMock(), mock_work)
        mock_work.get_team_iterations.return_value = []

        from yeaboi.tools.azure_devops import azdevops_fetch_active_iteration

        result = azdevops_fetch_active_iteration.invoke({})
        assert "No active iteration" in result


class TestFetchTeamIterationsMeta:
    """Time-frame derivation for the plain iterations helper (not a @tool)."""

    @staticmethod
    def _iteration(name: str, start, finish, path: str = ""):
        return SimpleNamespace(
            name=name,
            path=path or f"\\MyProject\\{name}",
            attributes=SimpleNamespace(start_date=start, finish_date=finish),
        )

    @patch("yeaboi.tools.azure_devops.get_azure_devops_team", return_value="MyTeam")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_time_frames_derived_from_aware_dates(self, mock_clients, *_):
        from datetime import datetime, timedelta

        from yeaboi.tools.azure_devops import fetch_team_iterations_meta

        mock_work = MagicMock()
        mock_clients.return_value = (MagicMock(), mock_work)
        now = datetime.now(UTC)
        mock_work.get_team_iterations.return_value = [
            self._iteration("Sprint 1", now - timedelta(days=21), now - timedelta(days=8)),
            self._iteration("Sprint 2", now - timedelta(days=7), now + timedelta(days=7)),
            self._iteration("Sprint 3", now + timedelta(days=8), now + timedelta(days=21)),
        ]

        items = fetch_team_iterations_meta()
        assert [it["time_frame"] for it in items] == ["past", "current", "future"]
        assert items[1]["name"] == "Sprint 2"
        assert items[1]["path"] == "\\MyProject\\Sprint 2"
        # Dates are plain "YYYY-MM-DD" strings.
        assert items[1]["start_date"] == (now - timedelta(days=7)).strftime("%Y-%m-%d")
        assert items[1]["finish_date"] == (now + timedelta(days=7)).strftime("%Y-%m-%d")

    @patch("yeaboi.tools.azure_devops.get_azure_devops_team", return_value="MyTeam")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_undated_iteration_counts_as_future(self, mock_clients, *_):
        from yeaboi.tools.azure_devops import fetch_team_iterations_meta

        mock_work = MagicMock()
        mock_clients.return_value = (MagicMock(), mock_work)
        mock_work.get_team_iterations.return_value = [self._iteration("Backlog", None, None)]

        items = fetch_team_iterations_meta()
        assert items == [
            {
                "name": "Backlog",
                "path": "\\MyProject\\Backlog",
                "time_frame": "future",
                "start_date": "",
                "finish_date": "",
            }
        ]

    @patch("yeaboi.tools.azure_devops.get_azure_devops_team", return_value="MyTeam")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_naive_sdk_datetimes_do_not_raise(self, mock_clients, *_):
        # Regression: the SDK can return tzinfo-less datetimes; comparing one
        # against the aware `now` used to raise TypeError. _aware coerces them.
        from datetime import datetime, timedelta

        from yeaboi.tools.azure_devops import fetch_team_iterations_meta

        mock_work = MagicMock()
        mock_clients.return_value = (MagicMock(), mock_work)
        naive_now = datetime.now(UTC).replace(tzinfo=None)
        mock_work.get_team_iterations.return_value = [
            self._iteration("Sprint 1", naive_now - timedelta(days=21), naive_now - timedelta(days=8)),
            self._iteration("Sprint 2", naive_now - timedelta(days=7), naive_now + timedelta(days=7)),
        ]

        items = fetch_team_iterations_meta()
        assert [it["time_frame"] for it in items] == ["past", "current"]
        assert items[0]["finish_date"] == (naive_now - timedelta(days=8)).strftime("%Y-%m-%d")

    @patch("yeaboi.tools.azure_devops.get_azure_devops_team", return_value="MyTeam")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_config_error_propagates_to_caller(self, mock_clients, *_):
        # The helper raises (callers degrade) — no error-string swallowing.
        import pytest

        from yeaboi.tools.azure_devops import fetch_team_iterations_meta

        mock_clients.side_effect = ValueError("AZURE_DEVOPS_ORG_URL is not set")
        with pytest.raises(ValueError, match="AZURE_DEVOPS_ORG_URL"):
            fetch_team_iterations_meta()


class TestGetTools:
    def test_returns_thirty_tools(self):
        tools = get_tools()
        assert len(tools) == 37

    def test_all_are_base_tools(self):
        from langchain_core.tools import BaseTool

        tools = get_tools()
        for t in tools:
            assert isinstance(t, BaseTool), f"{t} is not a BaseTool"

    def test_correct_names(self):
        tools = get_tools()
        names = {t.name for t in tools}
        assert names == {
            "github_read_repo",
            "github_read_file",
            "github_list_issues",
            "github_read_readme",
            "azdevops_read_repo",
            "azdevops_read_file",
            "azdevops_list_work_items",
            "azdevops_read_board",
            "azdevops_fetch_velocity",
            "azdevops_fetch_active_iteration",
            "azdevops_create_epic",
            "azdevops_create_story",
            "azdevops_create_iteration",
            "read_codebase",
            "read_local_file",
            "detect_bank_holidays",
            "estimate_complexity",
            "generate_acceptance_criteria",
            "jira_read_board",
            "jira_create_epic",
            "jira_create_story",
            "jira_create_sprint",
            "confluence_search_docs",
            "confluence_read_page",
            "confluence_read_space",
            "confluence_create_page",
            "confluence_update_page",
            "notion_search_pages",
            "notion_read_page",
            "notion_read_database",
            "notion_create_page",
            "notion_update_page",
            "jira_fetch_velocity",
            "jira_fetch_active_sprint",
            "load_project_context",
            "analyze_team_history",
            "compare_plan_to_actuals",
        }


# ---------------------------------------------------------------------------
# Poker helpers — per-work-item fetch + field update
# ---------------------------------------------------------------------------


def _make_poker_work_item(
    wi_id: int,
    title: str = "Do the thing",
    points=None,
    assignee=None,
    type_name: str = "User Story",
    acceptance: str = "",
):
    item = MagicMock()
    item.id = wi_id
    item.fields = {
        "System.Id": wi_id,
        "System.Title": title,
        "System.Description": "<div>Details<br>here</div>",
        "System.State": "New",
        "System.AssignedTo": assignee,
        "System.WorkItemType": type_name,
        "Microsoft.VSTS.Scheduling.StoryPoints": points,
        "Microsoft.VSTS.Common.AcceptanceCriteria": acceptance,
    }
    return item


class TestAzdevopsSprintIssues:
    @patch("yeaboi.tools.azure_devops.get_azure_devops_org_url", return_value="https://dev.azure.com/org")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_team", return_value="MyTeam")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_normalizes_rows(self, mock_clients, *_):
        from yeaboi.tools.azure_devops import azdevops_sprint_issues

        mock_wit, mock_work = MagicMock(), MagicMock()
        mock_clients.return_value = (mock_wit, mock_work)
        rel1, rel2 = MagicMock(), MagicMock()
        rel1.target.id = 101
        rel2.target.id = 102
        mock_work.get_iteration_work_items.return_value = MagicMock(work_item_relations=[rel1, rel2])
        mock_wit.get_work_items.return_value = [
            _make_poker_work_item(101, points=5, assignee={"displayName": "Alex"}),
            _make_poker_work_item(102),
        ]

        out = azdevops_sprint_issues("iter-guid", "MyProject")
        assert len(out) == 2
        assert out[0] == {
            "source": "azdevops",
            "key": "101",
            "summary": "Do the thing",
            "description": "<div>Details<br>here</div>",
            "story_points": 5.0,
            "state": "New",
            "assignee": "Alex",
            "url": "https://dev.azure.com/org/MyProject/_workitems/edit/101",
            "type": "User Story",
            "acceptance": "",
        }
        assert out[1]["story_points"] is None
        assert out[1]["assignee"] == ""
        mock_wit.get_work_items.assert_called_once()
        # The acceptance-criteria field rides in the batch fetch.
        assert "Microsoft.VSTS.Common.AcceptanceCriteria" in mock_wit.get_work_items.call_args.kwargs["fields"]

    @patch("yeaboi.tools.azure_devops.get_azure_devops_org_url", return_value="https://dev.azure.com/org")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_team", return_value="MyTeam")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_include_types_drops_tasks_keeps_unknown(self, mock_clients, *_):
        from yeaboi.tools.azure_devops import azdevops_sprint_issues

        mock_wit, mock_work = MagicMock(), MagicMock()
        mock_clients.return_value = (mock_wit, mock_work)
        rels = []
        for wid in (101, 102, 103, 104):
            rel = MagicMock()
            rel.target.id = wid
            rels.append(rel)
        mock_work.get_iteration_work_items.return_value = MagicMock(work_item_relations=rels)
        mock_wit.get_work_items.return_value = [
            _make_poker_work_item(101, type_name="User Story"),
            _make_poker_work_item(102, type_name="Task"),  # child work item — the reported leak
            _make_poker_work_item(103, type_name="Bug"),
            _make_poker_work_item(104, type_name="Impediment"),  # unknown type — kept
        ]

        out = azdevops_sprint_issues("iter-guid", "MyProject", include_types=("story", "bug"))
        assert [r["key"] for r in out] == ["101", "103", "104"]

    @patch("yeaboi.tools.azure_devops.get_azure_devops_org_url", return_value="https://dev.azure.com/org")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_team", return_value="MyTeam")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_acceptance_carried_on_row(self, mock_clients, *_):
        from yeaboi.tools.azure_devops import azdevops_sprint_issues

        mock_wit, mock_work = MagicMock(), MagicMock()
        mock_clients.return_value = (mock_wit, mock_work)
        rel = MagicMock()
        rel.target.id = 101
        mock_work.get_iteration_work_items.return_value = MagicMock(work_item_relations=[rel])
        mock_wit.get_work_items.return_value = [
            _make_poker_work_item(101, acceptance="<div>Given a user<br>Then it works</div>")
        ]

        out = azdevops_sprint_issues("iter-guid", "MyProject")
        assert out[0]["acceptance"] == "<div>Given a user<br>Then it works</div>"

    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="")
    def test_missing_project_returns_empty(self, _):
        from yeaboi.tools.azure_devops import azdevops_sprint_issues

        assert azdevops_sprint_issues("iter-guid") == []

    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    def test_missing_iteration_id_returns_empty(self, _):
        from yeaboi.tools.azure_devops import azdevops_sprint_issues

        assert azdevops_sprint_issues("") == []

    @patch("yeaboi.tools.azure_devops.get_azure_devops_team", return_value="MyTeam")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_empty_on_api_error(self, mock_clients, *_):
        from yeaboi.tools.azure_devops import azdevops_sprint_issues

        mock_wit, mock_work = MagicMock(), MagicMock()
        mock_clients.return_value = (mock_wit, mock_work)
        mock_work.get_iteration_work_items.side_effect = _FakeAzdoError("boom")
        assert azdevops_sprint_issues("iter-guid") == []


class TestAzdevopsBacklogIssues:
    @patch("yeaboi.tools.azure_devops.get_azure_devops_org_url", return_value="https://dev.azure.com/org")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_backlog_wiql_targets_root_iteration(self, mock_clients, *_):
        from yeaboi.tools.azure_devops import azdevops_backlog_issues

        mock_wit = MagicMock()
        mock_clients.return_value = (mock_wit, MagicMock())
        ref = MagicMock()
        ref.id = 201
        mock_wit.query_by_wiql.return_value = MagicMock(work_items=[ref])
        mock_wit.get_work_items.return_value = [_make_poker_work_item(201)]

        out = azdevops_backlog_issues("MyProject")
        assert [r["key"] for r in out] == ["201"]
        query = mock_wit.query_by_wiql.call_args[0][0].query
        assert "[System.IterationPath] = 'MyProject'" in query
        assert "'User Story'" in query
        assert "NOT IN" in query

    @patch("yeaboi.tools.azure_devops.get_azure_devops_org_url", return_value="https://dev.azure.com/org")
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_wiql_types_from_selection(self, mock_clients, *_):
        from yeaboi.tools.azure_devops import azdevops_backlog_issues

        mock_wit = MagicMock()
        mock_clients.return_value = (mock_wit, MagicMock())
        mock_wit.query_by_wiql.return_value = MagicMock(work_items=[])

        azdevops_backlog_issues("MyProject", include_types=("story",))
        query = mock_wit.query_by_wiql.call_args[0][0].query
        assert "'User Story'" in query and "'Product Backlog Item'" in query
        assert "'Bug'" not in query and "'Task'" not in query

    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="")
    def test_missing_project_returns_empty(self, _):
        from yeaboi.tools.azure_devops import azdevops_backlog_issues

        assert azdevops_backlog_issues() == []


class TestAzdevopsUpdateWorkItemFields:
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_updates_all_fields_with_add_ops(self, mock_clients, _):
        from yeaboi.tools.azure_devops import azdevops_update_work_item_fields

        mock_wit = MagicMock()
        mock_clients.return_value = (mock_wit, MagicMock())

        ok, err = azdevops_update_work_item_fields(101, summary="New", description="<div>D</div>", story_points=8)
        assert (ok, err) == (True, "")
        kwargs = mock_wit.update_work_item.call_args.kwargs
        assert kwargs["id"] == 101
        assert kwargs["project"] == "MyProject"
        ops = {op.path: (op.op, op.value) for op in kwargs["document"]}
        # op="add" is AzDO's add-or-replace — "replace" fails on unset StoryPoints.
        assert ops == {
            "/fields/System.Title": ("add", "New"),
            "/fields/System.Description": ("add", "<div>D</div>"),
            "/fields/Microsoft.VSTS.Scheduling.StoryPoints": ("add", 8.0),
        }

    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_noop_when_nothing_to_update(self, mock_clients, _):
        from yeaboi.tools.azure_devops import azdevops_update_work_item_fields

        mock_wit = MagicMock()
        mock_clients.return_value = (mock_wit, MagicMock())
        assert azdevops_update_work_item_fields(101) == (True, "")
        mock_wit.update_work_item.assert_not_called()

    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    @patch("yeaboi.tools.azure_devops._make_azdo_clients")
    def test_api_error_folded_into_tuple(self, mock_clients, _):
        from yeaboi.tools.azure_devops import azdevops_update_work_item_fields

        mock_wit = MagicMock()
        mock_clients.return_value = (mock_wit, MagicMock())
        mock_wit.update_work_item.side_effect = _FakeAzdoError("denied")
        ok, err = azdevops_update_work_item_fields(101, story_points=5)
        assert ok is False
        assert err.startswith("Error")

    @patch("yeaboi.tools.azure_devops.get_azure_devops_org_url", return_value=None)
    @patch("yeaboi.tools.azure_devops.get_azure_devops_project", return_value="MyProject")
    def test_unconfigured_returns_error_tuple(self, *_):
        from yeaboi.tools.azure_devops import azdevops_update_work_item_fields

        ok, err = azdevops_update_work_item_fields(101, story_points=5)
        assert ok is False
        assert "AZURE_DEVOPS_ORG_URL" in err


class TestPinClientBaseUrl:
    """SDK resource-area discovery can swap in the legacy {org}.visualstudio.com
    alias; clients must be pinned back to the configured org URL."""

    @staticmethod
    def _client(base_url):
        from types import SimpleNamespace

        return SimpleNamespace(config=SimpleNamespace(base_url=base_url))

    def test_legacy_alias_is_pinned_to_configured_url(self):
        from yeaboi.tools.azure_devops import _pin_client_base_url

        client = self._client("https://youlend.visualstudio.com/")
        out = _pin_client_base_url(client, "https://dev.azure.com/youlend")
        assert out is client
        assert client.config.base_url == "https://dev.azure.com/youlend"

    def test_matching_url_untouched(self):
        from yeaboi.tools.azure_devops import _pin_client_base_url

        client = self._client("https://dev.azure.com/youlend/")
        _pin_client_base_url(client, "https://dev.azure.com/youlend")
        assert client.config.base_url == "https://dev.azure.com/youlend/"

    def test_none_org_url_is_a_no_op(self):
        from yeaboi.tools.azure_devops import _pin_client_base_url

        client = self._client("https://youlend.visualstudio.com/")
        _pin_client_base_url(client, None)
        assert client.config.base_url == "https://youlend.visualstudio.com/"

    def test_broken_client_never_raises(self):
        from yeaboi.tools.azure_devops import _pin_client_base_url

        assert _pin_client_base_url(object(), "https://dev.azure.com/youlend") is not None


class TestTruncatedCommentRefetch:
    """get_commits truncates long comments, stripping end-of-message AI trailers."""

    def _git_client(self, monkeypatch, repos):
        git = MagicMock()
        git.get_repositories.return_value = repos
        monkeypatch.setattr("yeaboi.tools.azure_devops._make_git_client", lambda: git)
        monkeypatch.setattr("yeaboi.tools.azure_devops.get_azure_devops_project", lambda: "Proj")
        return git

    @staticmethod
    def _commit(sha, comment, truncated=False):
        return SimpleNamespace(
            commit_id=sha,
            comment=comment,
            comment_truncated=truncated,
            author=SimpleNamespace(name="Gina", email="g@corp.com", date="2026-07-17T08:00:00Z"),
        )

    def test_truncated_comment_is_refetched_in_full(self, monkeypatch):
        repo = SimpleNamespace(id="r1", name="api")
        git = self._git_client(monkeypatch, [repo])
        git.get_commits.return_value = [self._commit("a" * 16, "fix login\n\nlong bo...", truncated=True)]
        git.get_commit.return_value = SimpleNamespace(
            comment="fix login\n\nlong body\n\nCo-Authored-By: Claude <noreply@anthropic.com>"
        )
        items = azdevops_recent_commits("Proj", days=1, include_repository=True)
        git.get_commit.assert_called_once_with("a" * 16, "r1", project="Proj")
        assert "Co-Authored-By: Claude" in items[0]["body"]

    def test_refetch_failure_falls_back_to_truncated_text(self, monkeypatch):
        repo = SimpleNamespace(id="r1", name="api")
        git = self._git_client(monkeypatch, [repo])
        git.get_commits.return_value = [self._commit("b" * 16, "fix\n\ntruncated bo...", truncated=True)]
        git.get_commit.side_effect = RuntimeError("boom")
        items = azdevops_recent_commits("Proj", days=1, include_repository=True)
        assert items[0]["body"] == "truncated bo..."

    def test_untruncated_comment_never_refetches(self, monkeypatch):
        repo = SimpleNamespace(id="r1", name="api")
        git = self._git_client(monkeypatch, [repo])
        git.get_commits.return_value = [self._commit("c" * 16, "fix\n\nfull body")]
        azdevops_recent_commits("Proj", days=1, include_repository=True)
        git.get_commit.assert_not_called()

    def test_refetches_capped_per_repo(self, monkeypatch):
        from yeaboi.tools.azure_devops import _TRUNCATED_COMMENT_REFETCH_CAP

        repo = SimpleNamespace(id="r1", name="api")
        git = self._git_client(monkeypatch, [repo])
        git.get_commits.return_value = [
            self._commit(f"{i:016d}", f"c{i}\n\nbo...", truncated=True)
            for i in range(_TRUNCATED_COMMENT_REFETCH_CAP + 5)
        ]
        git.get_commit.return_value = SimpleNamespace(comment="c\n\nfull body")
        items = azdevops_recent_commits("Proj", days=1, include_repository=True)
        assert git.get_commit.call_count == _TRUNCATED_COMMENT_REFETCH_CAP
        assert len(items) == _TRUNCATED_COMMENT_REFETCH_CAP + 5


class TestStandupPathBounds:
    """The standup path (include_repository=False) keeps its legacy API-call
    bounds; only the exhaustive analysis path walks the whole window."""

    def _git_client(self, monkeypatch, repos):
        git = MagicMock()
        git.get_repositories.return_value = repos
        monkeypatch.setattr("yeaboi.tools.azure_devops._make_git_client", lambda: git)
        monkeypatch.setattr("yeaboi.tools.azure_devops.get_azure_devops_project", lambda: "Proj")
        return git

    @staticmethod
    def _commit(index):
        return SimpleNamespace(
            commit_id=f"{index:016d}",
            comment=f"change {index}",
            comment_truncated=False,
            author=SimpleNamespace(name="Gina", email="g@corp.com", date="2026-07-17T08:00:00Z"),
        )

    def test_standup_commit_scan_and_change_lookups_capped(self, monkeypatch):
        from yeaboi.tools.azure_devops import _MAX_CHANGED_FILE_LOOKUPS, _MAX_REPO_COMMITS

        repo = SimpleNamespace(id="r1", name="api")
        git = self._git_client(monkeypatch, [repo])
        batches = [[self._commit(index) for index in range(page * 100, page * 100 + 100)] for page in range(3)]
        git.get_commits.side_effect = lambda **kwargs: batches[kwargs.get("skip", 0) // 100]
        lookups = {"count": 0}

        def _fake_changed_files(*args, **kwargs):
            lookups["count"] += 1
            return []

        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_commit_changed_files", _fake_changed_files)

        items = azdevops_recent_commits("Proj", days=30)

        assert len(items) == _MAX_REPO_COMMITS
        assert lookups["count"] == _MAX_CHANGED_FILE_LOOKUPS

    def test_standup_pr_change_lookups_capped(self, monkeypatch):
        from datetime import UTC, datetime

        from yeaboi.tools.azure_devops import _MAX_CHANGED_FILE_LOOKUPS, azdevops_recent_prs

        repo = SimpleNamespace(id="r1", name="api")
        self._git_client(monkeypatch, [repo])
        now = datetime.now(UTC)
        prs = [
            SimpleNamespace(
                pull_request_id=index,
                title=f"PR {index}",
                description="",
                status="active",
                creation_date=now,
                closed_date=None,
                created_by=SimpleNamespace(display_name="Gina", unique_name="g@corp.com"),
                source_ref_name="refs/heads/feature/x",
                reviewers=(),
            )
            for index in range(_MAX_CHANGED_FILE_LOOKUPS + 10)
        ]
        monkeypatch.setattr(
            "yeaboi.tools.azure_devops._activity_pull_requests",
            lambda *args, **kwargs: (("Proj", repo, pr) for pr in prs),
        )
        lookups = {"count": 0}

        def _fake_changed_files(*args, **kwargs):
            lookups["count"] += 1
            return []

        monkeypatch.setattr("yeaboi.tools.azure_devops._azdo_pr_changed_files", _fake_changed_files)

        items = azdevops_recent_prs("Proj", days=30)

        assert len(items) == _MAX_CHANGED_FILE_LOOKUPS + 10
        assert lookups["count"] == _MAX_CHANGED_FILE_LOOKUPS
