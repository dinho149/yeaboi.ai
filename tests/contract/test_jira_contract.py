"""Contract tests for Jira tools using recorded API responses (VCR.py).

These tests replay hand-crafted cassettes containing realistic Jira Cloud REST
API responses. They verify that our tool functions correctly parse the response
shapes returned by the real API — catching SDK upgrades, schema changes, and
field-mapping regressions without requiring live Jira credentials.

# See docs: "Testing — Contract Tests" for background on VCR.py replay.

Each test is marked with @pytest.mark.vcr so pytest-recording loads the
matching cassette from tests/contract/cassettes/test_jira_contract/.
Cassette names follow the pattern: ClassName.test_method_name.yaml

To re-record cassettes against a real Jira instance: make record
"""

from __future__ import annotations

import pytest
from jira import JIRA

from yeaboi.tools.jira import (
    jira_create_epic,
    jira_create_sprint,
    jira_create_story,
    jira_read_board,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_TEST_BASE_URL = "https://test.atlassian.net"


def _make_test_client() -> JIRA:
    """Create a JIRA client that skips the serverInfo init call.

    get_server_info=False prevents the constructor from calling
    GET /rest/api/2/serverInfo, so cassettes only need the actual
    API calls we care about. This also sets _is_cloud=False, so
    search_issues uses the classic /rest/api/2/search endpoint.
    """
    return JIRA(
        server=_TEST_BASE_URL,
        basic_auth=("test@example.com", "fake-token"),
        get_server_info=False,
    )


@pytest.fixture(autouse=True)
def _jira_env(monkeypatch):
    """Patch _make_jira_client to return a test client + set env vars."""
    monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", _make_test_client)
    monkeypatch.setenv("JIRA_BASE_URL", _TEST_BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "test@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "fake-token")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "PROJ")


# ---------------------------------------------------------------------------
# jira_read_board — board info, active sprint, backlog, velocity
# ---------------------------------------------------------------------------


class TestJiraReadBoardContract:
    """Contract: jira_read_board parses board, sprint, backlog, and velocity from real API shapes."""

    @pytest.mark.vcr
    def test_read_board_happy_path(self):
        """Full board read: board name, active sprint, backlog count, velocity."""
        result = jira_read_board.invoke({"project_key": "PROJ"})

        # Board info from /rest/agile/1.0/board response
        assert "PROJ board" in result
        assert "42" in result

        # Active sprint from /rest/agile/1.0/board/42/sprint?state=active
        assert "Sprint 1" in result
        assert "2024-01-15" in result
        assert "2024-01-29" in result

        # Backlog count from /rest/api/2/search
        assert "7" in result

        # Velocity from sprint_info — avg of 18, 22, 20 = 20.0
        assert "20.0" in result


# ---------------------------------------------------------------------------
# jira_create_epic — epic creation with summary, description, priority
# ---------------------------------------------------------------------------


class TestJiraCreateEpicContract:
    """Contract: jira_create_epic parses the issue creation response correctly."""

    @pytest.mark.vcr
    def test_create_epic_happy_path(self):
        """Create epic and verify key + URL extraction from response."""
        result = jira_create_epic.invoke(
            {
                "title": "Authentication Epic",
                "description": "Implement user authentication with OAuth",
                "project_key": "PROJ",
                "internal_id": "epic-1",
            }
        )

        # Issue key from POST /rest/api/2/issue response
        assert "PROJ-100" in result
        assert "Authentication Epic" in result
        assert f"{_TEST_BASE_URL}/browse/PROJ-100" in result
        # Internal ID mapping
        assert "Mapping: epic-1" in result


# ---------------------------------------------------------------------------
# jira_create_story — story with AC, points, epic link, labels
# ---------------------------------------------------------------------------


class TestJiraCreateStoryContract:
    """Contract: jira_create_story sends correct fields and parses response."""

    @pytest.mark.vcr
    def test_create_story_with_points_and_epic_link(self):
        """Create story with story points, priority, labels, and epic link."""
        result = jira_create_story.invoke(
            {
                "summary": "User login flow",
                "description": "As a user I want to log in so I can access my account",
                "epic_key": "PROJ-100",
                "story_points": 5,
                "priority": "High",
                "project_key": "PROJ",
                "labels": ["Code", "Auth"],
                "internal_id": "story-1",
                "link_method": "epic_link",
            }
        )

        # Issue key from POST /rest/api/2/issue response
        assert "PROJ-200" in result
        # Epic link confirmation
        assert "PROJ-100" in result
        assert "customfield_10014" in result
        # Labels reported back
        assert "Code" in result
        assert "Auth" in result
        # URL constructed correctly
        assert f"{_TEST_BASE_URL}/browse/PROJ-200" in result
        # Internal ID mapping
        assert "Mapping: story-1" in result


# ---------------------------------------------------------------------------
# jira_create_sprint — sprint with name, dates, goal
# ---------------------------------------------------------------------------


class TestJiraCreateSprintContract:
    """Contract: jira_create_sprint parses the sprint creation response correctly."""

    @pytest.mark.vcr
    def test_create_sprint_with_dates_and_goal(self):
        """Create sprint with start/end dates and goal."""
        result = jira_create_sprint.invoke(
            {
                "sprint_name": "Sprint 1",
                "board_id": 42,
                "start_date": "2024-01-15",
                "end_date": "2024-01-29",
                "goal": "Ship authentication module",
            }
        )

        # Sprint ID and name from POST /rest/agile/1.0/sprint response
        assert "15" in result
        assert "Sprint 1" in result
        assert "42" in result


# ---------------------------------------------------------------------------
# Error responses — 401, 404, 429
# ---------------------------------------------------------------------------


class TestJiraErrorResponsesContract:
    """Contract: Jira error responses are caught and returned as user-friendly messages."""

    @pytest.mark.vcr
    def test_401_bad_token(self):
        """401 Unauthorized → authentication error message."""
        result = jira_read_board.invoke({"project_key": "PROJ"})

        assert "authentication failed" in result.lower()

    @pytest.mark.vcr
    def test_404_missing_project(self):
        """404 Not Found → resource not found message."""
        result = jira_create_epic.invoke({"title": "Test", "project_key": "NOPE"})

        assert "not found" in result.lower()

    def test_429_rate_limit(self, monkeypatch):
        """429 Too Many Requests → rate limit message.

        Not VCR-based: PyJira's ResilientSession auto-retries 429 responses
        with exponential backoff, making VCR replay impractical. Instead we
        verify the error message formatting via a mock JIRAError.
        """
        from unittest.mock import MagicMock

        from jira import JIRAError

        mock_client = MagicMock()
        err = JIRAError("rate limit")
        err.status_code = 429
        err.text = "Rate limit exceeded"
        mock_client.boards.side_effect = err

        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: mock_client)

        result = jira_read_board.invoke({"project_key": "PROJ"})

        assert "rate limit" in result.lower()
