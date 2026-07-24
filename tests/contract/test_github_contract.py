"""Contract tests for GitHub tools using recorded API responses (VCR.py).

These tests replay hand-crafted cassettes containing realistic GitHub REST API
responses. They verify that our tool functions correctly parse the response
shapes — catching PyGithub SDK upgrades, schema changes, and content-decoding
regressions without requiring a live GitHub token.

# See docs: "Testing — Contract Tests" for background on VCR.py replay.

Each test is marked with @pytest.mark.vcr so pytest-recording loads the
matching cassette from tests/contract/cassettes/test_github_contract/.

To re-record cassettes against a real GitHub instance: make record
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from yeaboi.tools.github import (
    github_list_issues,
    github_read_file,
    github_read_readme,
    github_read_repo,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _github_env(monkeypatch):
    """Set GITHUB_TOKEN so _get_github_client() creates an authenticated client.

    PyGithub's Github() constructor does NOT make HTTP calls on init (unlike
    PyJira), so no extra cassette interactions are needed for construction.
    """
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token-for-vcr")


# ---------------------------------------------------------------------------
# github_read_repo — repo tree listing with file types and languages
# ---------------------------------------------------------------------------


class TestGithubReadRepoContract:
    """Contract: github_read_repo parses repo metadata, tree, and languages."""

    @pytest.mark.vcr
    def test_read_repo_tree_and_languages(self):
        """Read repo returns tree structure, key files, and language breakdown."""
        result = github_read_repo.invoke({"repo_url": "test-org/test-repo"})

        # Repo metadata
        assert "test-org/test-repo" in result
        assert "main" in result  # default branch
        # File tree entries
        assert "src" in result
        # Key files detected
        assert "pyproject.toml" in result
        assert "README.md" in result
        assert "Dockerfile" in result
        assert "Makefile" in result
        # Language breakdown
        assert "Python" in result
        # Stats
        assert "42" in result  # stars
        assert "7" in result  # forks


# ---------------------------------------------------------------------------
# github_read_file — file content retrieval with base64 decoding
# ---------------------------------------------------------------------------


class TestGithubReadFileContract:
    """Contract: github_read_file fetches and decodes base64 file content."""

    @pytest.mark.vcr
    def test_read_file_decodes_content(self):
        """Read file decodes base64 content and shows file metadata."""
        result = github_read_file.invoke({"repo_url": "test-org/test-repo", "file_path": "pyproject.toml"})

        # File path in header
        assert "pyproject.toml" in result
        # Decoded content from base64
        assert "[project]" in result
        assert 'name = "test-repo"' in result
        assert 'version = "1.0.0"' in result
        assert "line-length = 120" in result
        # Should NOT be truncated (small file)
        assert "Truncated" not in result


# ---------------------------------------------------------------------------
# github_list_issues — issues with labels and PR tags
# ---------------------------------------------------------------------------


class TestGithubListIssuesContract:
    """Contract: github_list_issues parses issues with labels and PR detection."""

    @pytest.mark.vcr
    def test_list_issues_with_labels(self):
        """List issues returns numbers, titles, labels, and PR tags."""
        result = github_list_issues.invoke({"repo_url": "test-org/test-repo"})

        # Issue #1 — bug
        assert "#1" in result
        assert "Fix login bug" in result
        assert "bug" in result
        # Issue #2 — enhancement
        assert "#2" in result
        assert "Add dark mode" in result
        assert "enhancement" in result
        # Issue #3 — pull request
        assert "#3" in result
        assert "Refactor auth module" in result
        assert "[PR]" in result
        # Body previews
        assert "SSO" in result
        # Summary
        assert "3 issues shown" in result


# ---------------------------------------------------------------------------
# github_read_readme — README.md + CONTRIBUTING.md content
# ---------------------------------------------------------------------------


class TestGithubReadReadmeContract:
    """Contract: github_read_readme fetches README and CONTRIBUTING docs."""

    @pytest.mark.vcr
    def test_read_readme_with_contributing(self):
        """Read readme returns decoded README and CONTRIBUTING content."""
        result = github_read_readme.invoke({"repo_url": "test-org/test-repo"})

        # README section
        assert "README" in result
        assert "Test Repo" in result
        assert "contract testing" in result
        assert "make run" in result
        # CONTRIBUTING section
        assert "CONTRIBUTING.md" in result
        assert "How to Contribute" in result
        assert "Fork the repository" in result
        # Should NOT be truncated (small files)
        assert "Truncated" not in result


# ---------------------------------------------------------------------------
# Error responses — 401, 404, 403 (rate limit)
# ---------------------------------------------------------------------------


class TestGithubErrorResponsesContract:
    """Contract: GitHub error responses are caught and returned as user-friendly messages."""

    @pytest.mark.vcr
    def test_401_bad_token(self):
        """401 Unauthorized → error message with 'Bad credentials'."""
        result = github_read_repo.invoke({"repo_url": "test-org/test-repo"})

        assert "Bad credentials" in result

    @pytest.mark.vcr
    def test_404_missing_repo(self):
        """404 Not Found for a non-existent repo → error message."""
        result = github_read_repo.invoke({"repo_url": "test-org/nonexistent"})

        assert "Not Found" in result

    @patch("yeaboi.tools.github._get_github_client")
    def test_403_rate_limit(self, mock_get_client):
        """403 rate limit → user-friendly message with upgrade hint.

        PyGithub raises RateLimitExceededException only when specific
        X-RateLimit headers are present in the response. Rather than craft
        fragile cassette headers, we mock the exception directly (same
        approach as Jira's 429 test).
        """
        import github as gh_module

        mock_repo = mock_get_client.return_value.get_repo
        mock_repo.side_effect = gh_module.RateLimitExceededException(403, {"message": "API rate limit exceeded"}, {})

        result = github_read_repo.invoke({"repo_url": "test-org/test-repo"})

        assert "rate limit" in result.lower()
        assert "5 000 req/hr" in result
