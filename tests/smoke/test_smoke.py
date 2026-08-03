"""Smoke tests — real API calls to detect token expiry, API deprecations, and SDK drift.

These tests run against live APIs with real credentials. They are NOT part of
the regular test suite — they run on a weekly cron schedule or manually via
`make smoke-test`.

Each test creates minimal resources and cleans up after itself. Tests skip
gracefully when credentials are missing.

# See docs: "Testing — Smoke Tests" for background.
"""

from __future__ import annotations

import json
import os

import pytest

# ---------------------------------------------------------------------------
# Jira — create and delete a test epic in a sandbox project
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestJiraSmoke:
    def test_create_and_delete_epic(self, jira_creds):
        """Create an epic via the Jira API, verify it exists, then delete it."""
        from jira import JIRA

        client = JIRA(
            server=jira_creds["base_url"],
            basic_auth=(jira_creds["email"], jira_creds["token"]),
        )

        # Create
        epic = client.create_issue(
            project=jira_creds["project_key"],
            summary="[SMOKE TEST] Auto-delete — do not use",
            issuetype={"name": "Epic"},
        )

        try:
            assert epic.key is not None
            assert "SMOKE TEST" in epic.fields.summary

            # Verify it's readable
            fetched = client.issue(epic.key)
            assert fetched.key == epic.key
        finally:
            # Always clean up
            epic.delete()


# ---------------------------------------------------------------------------
# Confluence — create and delete a test page in a sandbox space
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestConfluenceSmoke:
    def test_create_and_delete_page(self, confluence_creds):
        """Create a page via the Confluence API, verify it exists, then delete it."""
        from atlassian import Confluence

        client = Confluence(
            url=confluence_creds["base_url"],
            username=confluence_creds["email"],
            password=confluence_creds["token"],
        )

        # Create
        result = client.create_page(
            space=confluence_creds["space_key"],
            title="[SMOKE TEST] Auto-delete — do not use",
            body="<p>This page was created by a smoke test and should be deleted automatically.</p>",
        )

        page_id = result["id"]
        try:
            assert page_id is not None
            assert "SMOKE TEST" in result["title"]

            # Verify it's readable
            fetched = client.get_page_by_id(page_id)
            assert fetched["id"] == page_id
        finally:
            # Always clean up
            client.remove_page(page_id)


# ---------------------------------------------------------------------------
# GitHub — read a known public repo
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestGithubSmoke:
    def test_read_public_repo(self, github_token):
        """Read the project's own repo to verify token + API access."""
        import github

        g = github.Github(auth=github.Auth.Token(github_token))
        try:
            repo = g.get_repo("YouLendPlatform/scrum-jira-agent")
        except github.GithubException as exc:
            if exc.status != 404:
                raise
            # SMOKE_GITHUB_TOKEN is set but lacks access to this repo (wrong
            # scope or fine-grained PAT not scoped here). Fall back to the
            # built-in workflow token which always has contents:read.
            builtin = os.environ.get("GITHUB_BUILTIN_TOKEN")
            if not builtin:
                pytest.skip("SMOKE_GITHUB_TOKEN lacks repo access and no builtin token available")
            g = github.Github(auth=github.Auth.Token(builtin))
            repo = g.get_repo("YouLendPlatform/scrum-jira-agent")

        assert repo.full_name == "YouLendPlatform/scrum-jira-agent"
        assert repo.default_branch is not None

        # Verify we can read the tree
        tree = repo.get_git_tree(sha="HEAD", recursive=False)
        assert len(tree.tree) > 0


# ---------------------------------------------------------------------------
# Azure DevOps — read a known project/repo
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestAzureDevOpsSmoke:
    def test_read_repo(self, azdo_creds):
        """Read a known AzDO repo to verify PAT + API access."""
        from azure.devops.connection import Connection
        from msrest.authentication import BasicAuthentication

        from yeaboi.tools.azure_devops import _parse_azdo_url

        org_url, project, repo = _parse_azdo_url(azdo_creds["repo_url"])
        creds = BasicAuthentication("", azdo_creds["token"])
        conn = Connection(base_url=org_url, creds=creds)
        git_client = conn.clients.get_git_client()

        items = git_client.get_items(
            repository_id=repo,
            project=project,
            recursion_level="full",
        )

        assert items is not None
        assert len(items) > 0


# ---------------------------------------------------------------------------
# Anthropic Claude — send a simple prompt, assert non-empty response
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestAnthropicSmoke:
    def test_simple_prompt(self, anthropic_api_key):
        """Send a minimal prompt to Claude and verify a non-empty response."""
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import HumanMessage

        llm = ChatAnthropic(
            model="claude-haiku-4-5-20251001",
            api_key=anthropic_api_key,
            temperature=0.0,
            max_tokens=50,
        )

        response = llm.invoke([HumanMessage(content="Reply with exactly: SMOKE_OK")])

        assert response.content is not None
        assert len(response.content.strip()) > 0
        assert "SMOKE_OK" in response.content


# ---------------------------------------------------------------------------
# OpenAI GPT-4o — send a simple prompt (if configured)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestOpenAISmoke:
    def test_simple_prompt(self, openai_api_key):
        """Send a minimal prompt to GPT-4o-mini and verify a non-empty response."""
        pytest.importorskip("langchain_openai", reason="langchain-openai not installed")
        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model="gpt-4o-mini",
            api_key=openai_api_key,
            temperature=0.0,
            max_tokens=50,
        )

        response = llm.invoke([HumanMessage(content="Reply with exactly: SMOKE_OK")])

        assert response.content is not None
        assert len(response.content.strip()) > 0
        assert "SMOKE_OK" in response.content


# ---------------------------------------------------------------------------
# Google Gemini — send a simple prompt (if configured)
# ---------------------------------------------------------------------------


@pytest.mark.smoke
class TestGeminiSmoke:
    def test_simple_prompt(self, google_api_key):
        """Send a minimal prompt to Gemini Flash and verify a non-empty response."""
        pytest.importorskip("langchain_google_genai", reason="langchain-google-genai not installed")
        from langchain_core.messages import HumanMessage
        from langchain_google_genai import ChatGoogleGenerativeAI

        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            google_api_key=google_api_key,
            temperature=0.0,
            max_output_tokens=50,
        )

        try:
            response = llm.invoke([HumanMessage(content="Reply with exactly: SMOKE_OK")])
        except Exception as exc:
            # Quota exhaustion (free-tier limit hit, or temporarily rate-limited)
            # is an infrastructure problem, not a code defect — skip rather than fail.
            msg = str(exc)
            if "RESOURCE_EXHAUSTED" in msg or "429" in msg or "quota" in msg.lower():
                pytest.skip(f"Gemini quota exhausted — top up billing or retry later: {exc}")
            raise

        assert response.content is not None
        assert len(response.content.strip()) > 0
        assert "SMOKE_OK" in response.content


@pytest.mark.smoke
class TestLinearSmoke:
    """Live Linear GraphQL calls — the read tools only.

    Linear has no Python SDK, so tools/linear.py hand-writes four GraphQL
    queries against a schema nobody in CI can typecheck. That makes these the
    highest-value smoke tests in the file: a field rename upstream would break
    the tools while every mocked unit test stayed green.

    Read-only on purpose. The write tool would leave an issue behind, and Linear
    has no hard delete via the API — archiving is the closest thing, which still
    leaves the issue identifier consumed in the user's real workspace.
    """

    def test_read_board_returns_a_team_summary(self, linear_creds, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", linear_creds["api_key"])
        monkeypatch.setenv("LINEAR_TEAM_KEY", linear_creds["team_key"])
        from yeaboi.tools.linear import linear_read_board

        result = linear_read_board.invoke({})
        assert not result.startswith("Error:"), result
        assert f"({linear_creds['team_key']})" in result
        assert "Backlog:" in result

    def test_fetch_active_cycle_returns_the_sprint_json_shape(self, linear_creds, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", linear_creds["api_key"])
        monkeypatch.setenv("LINEAR_TEAM_KEY", linear_creds["team_key"])
        from yeaboi.tools.linear import linear_fetch_active_cycle

        result = linear_fetch_active_cycle.invoke({})
        # A team with cycles disabled is a legitimate configuration, not a
        # failure — assert the shape only when a cycle actually came back.
        if result.startswith("Error: No active cycle"):
            pytest.skip("team has no active cycle")
        assert not result.startswith("Error:"), result
        data = json.loads(result)
        assert isinstance(data["sprint_number"], int)
        assert data["sprint_name"]

    def test_bad_key_is_reported_not_raised(self, linear_creds, monkeypatch):
        monkeypatch.setenv("LINEAR_API_KEY", "not-a-real-linear-key")
        monkeypatch.setenv("LINEAR_TEAM_KEY", linear_creds["team_key"])
        from yeaboi.tools.linear import linear_read_board

        result = linear_read_board.invoke({})
        assert result.startswith("Error:")
        assert "LINEAR_API_KEY" in result
