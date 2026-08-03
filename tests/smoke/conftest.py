"""Shared fixtures for smoke tests.

Smoke tests run against real APIs with real credentials. They are NOT run on
every push — only on a weekly cron schedule or manually via `make smoke-test`.

Each test is marked with @pytest.mark.smoke so pytest can select them.
Tests that require a specific credential skip gracefully if it's missing.
"""

from __future__ import annotations

import os

import pytest


def _env_or_skip(var: str) -> str:
    """Return the env var value or skip the test if missing."""
    val = os.environ.get(var)
    if not val:
        pytest.skip(f"{var} not set — skipping smoke test")
    return val


@pytest.fixture
def anthropic_api_key() -> str:
    return _env_or_skip("ANTHROPIC_API_KEY")


@pytest.fixture
def openai_api_key() -> str:
    return _env_or_skip("OPENAI_API_KEY")


@pytest.fixture
def google_api_key() -> str:
    return _env_or_skip("GOOGLE_API_KEY")


@pytest.fixture
def github_token() -> str:
    # Prefer a custom PAT (SMOKE_GITHUB_TOKEN, set via repo secret) for
    # broader GitHub access. If it is missing or not set, fall back to the
    # built-in workflow token which always has contents:read for this repo.
    val = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_BUILTIN_TOKEN")
    if not val:
        pytest.skip("No GitHub token available — skipping smoke test")
    return val


@pytest.fixture
def jira_creds() -> dict[str, str]:
    """Return Jira credentials or skip if any are missing."""
    return {
        "base_url": _env_or_skip("JIRA_BASE_URL"),
        "email": _env_or_skip("JIRA_EMAIL"),
        "token": _env_or_skip("JIRA_API_TOKEN"),
        "project_key": _env_or_skip("JIRA_PROJECT_KEY"),
    }


@pytest.fixture
def confluence_creds() -> dict[str, str]:
    """Return Confluence credentials or skip if any are missing."""
    return {
        "base_url": _env_or_skip("JIRA_BASE_URL"),
        "email": _env_or_skip("JIRA_EMAIL"),
        "token": _env_or_skip("JIRA_API_TOKEN"),
        "space_key": _env_or_skip("CONFLUENCE_SPACE_KEY"),
    }


@pytest.fixture
def azdo_creds() -> dict[str, str]:
    """Return Azure DevOps credentials or skip if any are missing."""
    return {
        "token": _env_or_skip("AZURE_DEVOPS_TOKEN"),
        "repo_url": _env_or_skip("AZURE_DEVOPS_SMOKE_REPO_URL"),
    }


@pytest.fixture
def linear_creds() -> dict[str, str]:
    """Return Linear credentials or skip if any are missing."""
    return {
        "api_key": _env_or_skip("LINEAR_API_KEY"),
        "team_key": _env_or_skip("LINEAR_TEAM_KEY"),
    }
