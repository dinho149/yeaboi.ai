"""Contract tests for Confluence tools using recorded API responses (VCR.py).

These tests replay hand-crafted cassettes containing realistic Confluence Cloud
REST API responses. They verify that our tool functions correctly parse the
response shapes — catching SDK upgrades, schema changes, and HTML-stripping
regressions without requiring live Confluence credentials.

# See docs: "Testing — Contract Tests" for background on VCR.py replay.

Each test is marked with @pytest.mark.vcr so pytest-recording loads the
matching cassette from tests/contract/cassettes/test_confluence_contract/.

To re-record cassettes against a real Confluence instance: make record
"""

from __future__ import annotations

import pytest

from yeaboi.tools.confluence import (
    confluence_create_page,
    confluence_read_page,
    confluence_read_space,
    confluence_search_docs,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_TEST_BASE_URL = "https://test.atlassian.net"


@pytest.fixture(autouse=True)
def _confluence_env(monkeypatch):
    """Set required env vars for all Confluence contract tests.

    The atlassian-python-api Confluence client auto-appends /wiki to
    atlassian.net URLs, so cassette URIs use /wiki/rest/api/...
    """
    monkeypatch.setenv("JIRA_BASE_URL", _TEST_BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "test@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "fake-token-for-vcr")
    monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "DOCS")


# ---------------------------------------------------------------------------
# confluence_search_docs — CQL search returning titles and URLs
# ---------------------------------------------------------------------------


class TestConfluenceSearchDocsContract:
    """Contract: confluence_search_docs parses CQL search results correctly."""

    @pytest.mark.vcr
    def test_search_returns_titles_and_urls(self):
        """CQL search returns page titles, IDs, excerpts, and URLs."""
        result = confluence_search_docs.invoke({"query": "architecture", "space_key": "DOCS"})

        # Two results from the cassette
        assert "System Architecture" in result
        assert "12345" in result
        assert "Architecture Decision Records" in result
        assert "12346" in result
        # URLs constructed from _links.webui
        assert "/spaces/DOCS/pages/12345/" in result
        assert "/spaces/DOCS/pages/12346/" in result
        # Excerpts are HTML-stripped
        assert "<p>" not in result
        assert "<b>" not in result
        assert "2 results shown" in result


# ---------------------------------------------------------------------------
# confluence_read_page — page content with HTML → plain text conversion
# ---------------------------------------------------------------------------


class TestConfluenceReadPageContract:
    """Contract: confluence_read_page fetches and strips HTML from storage format."""

    @pytest.mark.vcr
    def test_read_page_by_id_strips_html(self):
        """Read page by ID, verify HTML tags are stripped to plain text."""
        result = confluence_read_page.invoke({"page_id": "12345"})

        # Page title in header
        assert "=== System Architecture ===" in result
        # URL from _links.webui
        assert "/spaces/DOCS/pages/12345/" in result
        # Content extracted from storage format — HTML stripped
        assert "microservices" in result
        assert "API Gateway" in result
        assert "RabbitMQ" in result
        assert "Kubernetes" in result
        # HTML tags should be stripped
        assert "<h1>" not in result
        assert "<b>" not in result
        assert "<em>" not in result
        assert "<ul>" not in result
        assert "<li>" not in result


# ---------------------------------------------------------------------------
# confluence_read_space — space page listing
# ---------------------------------------------------------------------------


class TestConfluenceReadSpaceContract:
    """Contract: confluence_read_space lists pages from a space."""

    @pytest.mark.vcr
    def test_read_space_lists_pages(self):
        """List pages in a space with titles, IDs, and URLs."""
        result = confluence_read_space.invoke({"space_key": "DOCS"})

        # Three pages from the cassette
        assert "System Architecture" in result
        assert "12345" in result
        assert "Architecture Decision Records" in result
        assert "12346" in result
        assert "API Reference" in result
        assert "12347" in result
        # URLs from _links.webui
        assert "/spaces/DOCS/pages/12345/" in result
        assert "3 pages shown" in result


# ---------------------------------------------------------------------------
# confluence_create_page — page creation with storage format body
# ---------------------------------------------------------------------------


class TestConfluenceCreatePageContract:
    """Contract: confluence_create_page parses the creation response correctly."""

    @pytest.mark.vcr
    def test_create_page_returns_id_and_url(self):
        """Create page and verify ID + URL extraction from response."""
        result = confluence_create_page.invoke(
            {
                "title": "Sprint 1 Plan",
                "body": "<p>Sprint goal: Ship authentication module</p><p>Stories: 5 stories, 23 points total</p>",
                "space_key": "DOCS",
            }
        )

        # Page ID from POST response
        assert "99001" in result
        assert "Sprint 1 Plan" in result
        # URL from _links.webui
        assert "/spaces/DOCS/pages/99001/" in result


# ---------------------------------------------------------------------------
# Error responses — 401, 404
# ---------------------------------------------------------------------------


class TestConfluenceErrorResponsesContract:
    """Contract: Confluence error responses are caught and returned as user-friendly messages."""

    @pytest.mark.vcr
    def test_401_bad_token(self):
        """401 Unauthorized → authentication error message."""
        result = confluence_search_docs.invoke({"query": "test", "space_key": "DOCS"})

        assert "authentication failed" in result.lower()

    @pytest.mark.vcr
    def test_404_missing_space(self):
        """404 Not Found for a non-existent page → error message.

        The atlassian-python-api Confluence client raises ApiNotFoundError
        (a subclass of Exception, not HTTPError) for 404s, which our tool
        catches via the generic Exception handler and returns the API message.
        """
        result = confluence_read_page.invoke({"page_id": "99999"})

        assert result.startswith("Error:")
        assert "content" in result.lower() or "not found" in result.lower()
