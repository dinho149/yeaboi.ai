"""Contract tests for Azure DevOps tools using real SDK model objects.

Unlike the Jira/Confluence/GitHub contract tests (which use VCR.py cassettes to
replay HTTP responses), the AzDO contract tests construct real SDK model objects
directly. This is because the azure-devops SDK has complex internal HTTP
orchestration — resource area discovery, route template discovery, and msrest
pipeline — that makes VCR cassettes impractical.

These tests still have contract-test value:
- They use real GitItem, WorkItem, WorkItemQueryResult objects (not MagicMock)
- They catch SDK attribute renames, type changes, and model restructuring
- They verify our tool code handles the actual SDK types correctly

# See docs: "Testing — Contract Tests" for background.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from azure.devops.exceptions import AzureDevOpsServiceError
from azure.devops.v7_1.git.models import GitItem
from azure.devops.v7_1.work_item_tracking.models import (
    WorkItem,
    WorkItemQueryResult,
    WorkItemReference,
)

from yeaboi.tools.azure_devops import (
    azdevops_list_work_items,
    azdevops_read_file,
    azdevops_read_repo,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_URL = "https://dev.azure.com/testorg/MyProject/_git/my-repo"


# ---------------------------------------------------------------------------
# Helpers — real SDK model objects (not MagicMock)
# ---------------------------------------------------------------------------


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


def _make_git_item(path: str, obj_type: str = "blob") -> GitItem:
    """Create a real GitItem from the azure-devops SDK."""
    return GitItem(path=path, git_object_type=obj_type)


def _make_work_item(wi_id: int, wi_type: str, title: str, state: str, assignee: str | None = None) -> WorkItem:
    """Create a real WorkItem from the azure-devops SDK."""
    assigned_value = {"displayName": assignee} if assignee else None
    return WorkItem(
        id=wi_id,
        fields={
            "System.Id": wi_id,
            "System.WorkItemType": wi_type,
            "System.Title": title,
            "System.State": state,
            "System.AssignedTo": assigned_value,
        },
    )


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _azdo_env(monkeypatch):
    """Set required env vars for all Azure DevOps contract tests."""
    monkeypatch.setenv("AZURE_DEVOPS_TOKEN", "fake-token-for-contract-tests")


# ---------------------------------------------------------------------------
# azdevops_read_repo — repo file tree listing
# ---------------------------------------------------------------------------


class TestAzdevopsReadRepoContract:
    """Contract: azdevops_read_repo parses GitItem objects from the SDK."""

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_read_repo_tree_and_key_files(self, mock_make_conn):
        """Repo tree returns top-level entries and detected key files."""
        items = [
            _make_git_item("/", "tree"),
            _make_git_item("/src", "tree"),
            _make_git_item("/src/main.py"),
            _make_git_item("/src/utils.py"),
            _make_git_item("/pyproject.toml"),
            _make_git_item("/README.md"),
            _make_git_item("/Dockerfile"),
            _make_git_item("/Makefile"),
        ]
        mock_make_conn.return_value.clients.get_git_client.return_value.get_items.return_value = items

        result = azdevops_read_repo.invoke({"repo_url": _VALID_URL})

        # Repo metadata
        assert "MyProject/my-repo" in result
        assert "https://dev.azure.com/testorg" in result
        # Top-level entries
        assert "src" in result
        assert "pyproject.toml" in result
        assert "README.md" in result
        # Key files detected
        assert "Key files detected" in result
        assert "Dockerfile" in result
        assert "Makefile" in result
        # File count (6 blobs: main.py, utils.py, pyproject.toml, README.md, Dockerfile, Makefile)
        assert "Total files: 6" in result


# ---------------------------------------------------------------------------
# azdevops_read_file — file content retrieval
# ---------------------------------------------------------------------------


class TestAzdevopsReadFileContract:
    """Contract: azdevops_read_file reads and decodes byte chunks from the SDK."""

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_read_file_decodes_content(self, mock_make_conn):
        """Read file joins byte chunks and decodes to UTF-8."""
        content = b'[project]\nname = "test-repo"\nversion = "1.0.0"\n'
        mock_make_conn.return_value.clients.get_git_client.return_value.get_item_content.return_value = iter([content])

        result = azdevops_read_file.invoke({"repo_url": _VALID_URL, "file_path": "/pyproject.toml"})

        # File path in header
        assert "pyproject.toml" in result
        # Decoded content
        assert "[project]" in result
        assert 'name = "test-repo"' in result
        assert 'version = "1.0.0"' in result
        # File size
        assert f"{len(content)} bytes" in result
        # Should NOT be truncated
        assert "Truncated" not in result


# ---------------------------------------------------------------------------
# azdevops_list_work_items — work items with types, states, assignees
# ---------------------------------------------------------------------------


class TestAzdevopsListWorkItemsContract:
    """Contract: azdevops_list_work_items parses WorkItem SDK objects."""

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_list_work_items_with_types_and_assignees(self, mock_make_conn):
        """List work items returns IDs, types, titles, states, and assignees."""
        work_items = [
            _make_work_item(101, "Bug", "Fix login crash", "Active", "Jane Smith"),
            _make_work_item(102, "User Story", "Add dark mode", "Active", "John Doe"),
            _make_work_item(103, "Task", "Update documentation", "Active"),
        ]

        # Set up WIT client mock
        wit_client = MagicMock()
        mock_make_conn.return_value.clients.get_work_item_tracking_client.return_value = wit_client

        # query_by_wiql returns real WorkItemQueryResult with WorkItemReference objects
        refs = [
            WorkItemReference(id=wi.id, url=f"https://dev.azure.com/testorg/_apis/wit/workitems/{wi.id}")
            for wi in work_items
        ]
        wit_client.query_by_wiql.return_value = WorkItemQueryResult(work_items=refs)

        # get_work_items returns real WorkItem objects
        wit_client.get_work_items.return_value = work_items

        result = azdevops_list_work_items.invoke({"repo_url": _VALID_URL})

        # Work item #101 — Bug, assigned
        assert "#101" in result
        assert "Bug" in result
        assert "Fix login crash" in result
        assert "Jane Smith" in result
        # Work item #102 — User Story, assigned
        assert "#102" in result
        assert "User Story" in result
        assert "Add dark mode" in result
        assert "John Doe" in result
        # Work item #103 — Task, unassigned
        assert "#103" in result
        assert "Task" in result
        assert "Unassigned" in result
        # Summary
        assert "3 work items shown" in result


# ---------------------------------------------------------------------------
# Error responses — 401, 404
# ---------------------------------------------------------------------------


class TestAzdevopsErrorResponsesContract:
    """Contract: AzDO errors are caught and returned as user-friendly messages."""

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_401_bad_pat(self, mock_make_conn):
        """401 Unauthorized → authentication error with token hint."""
        mock_make_conn.return_value.clients.get_git_client.return_value.get_items.side_effect = _FakeAzdoError(
            "401 Unauthorized"
        )

        result = azdevops_read_repo.invoke({"repo_url": _VALID_URL})

        assert "Authentication failed" in result
        assert "AZURE_DEVOPS_TOKEN" in result

    @patch("yeaboi.tools.azure_devops._make_connection")
    def test_404_missing_project(self, mock_make_conn):
        """404 Not Found → resource not found with URL verification hint."""
        mock_make_conn.return_value.clients.get_git_client.return_value.get_items.side_effect = _FakeAzdoError(
            "TF404000: The Git repository with name or identifier 'nonexistent' does not exist. 404 Not Found"
        )

        result = azdevops_read_repo.invoke({"repo_url": _VALID_URL})

        assert "not found" in result.lower()
        assert "verify the repo URL" in result
