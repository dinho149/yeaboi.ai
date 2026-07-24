"""Contract tests for Notion tools using recorded API responses (VCR.py).

These tests replay hand-crafted cassettes containing realistic Notion REST API
responses. They verify that our tool functions correctly parse the response shapes
— catching SDK upgrades, schema changes, and block→text regressions without
requiring live Notion credentials. Mirrors test_confluence_contract.py.

# See docs: "Testing — Contract Tests" for background on VCR.py replay.

Each test is marked with @pytest.mark.vcr so pytest-recording loads the matching
cassette from tests/contract/cassettes/test_notion_contract/.

To re-record cassettes against a real Notion workspace: make record
"""

from __future__ import annotations

import pytest

from yeaboi.tools.notion import (
    notion_create_page,
    notion_read_database,
    notion_read_page,
    notion_search_pages,
)


@pytest.fixture(autouse=True)
def _notion_env(monkeypatch):
    """Set required env vars for all Notion contract tests."""
    monkeypatch.setenv("NOTION_TOKEN", "fake-token-for-vcr")
    monkeypatch.setenv("NOTION_ROOT_PAGE_ID", "root-page-id")


class TestNotionSearchContract:
    """Contract: notion_search_pages parses search results correctly."""

    @pytest.mark.vcr
    def test_search_returns_titles_and_urls(self):
        result = notion_search_pages.invoke({"query": "architecture"})

        assert "System Architecture" in result
        assert "11111111111111111111111111111111" in result
        assert "Architecture Decision Records" in result
        assert "notion.so" in result
        assert "2 results shown" in result


class TestNotionReadPageContract:
    """Contract: notion_read_page fetches a page + its blocks and flattens to text."""

    @pytest.mark.vcr
    def test_read_page_flattens_blocks(self):
        result = notion_read_page.invoke({"page_id": "11111111111111111111111111111111"})

        assert "=== System Architecture ===" in result
        assert "microservices" in result
        assert "API Gateway" in result
        # Block JSON should be flattened — no raw structure leaks through.
        assert "rich_text" not in result
        assert "plain_text" not in result


class TestNotionReadDatabaseContract:
    """Contract: notion_read_database lists database entries."""

    @pytest.mark.vcr
    def test_read_database_lists_entries(self):
        result = notion_read_database.invoke({"database_id": "22222222222222222222222222222222"})

        assert "System Architecture" in result
        assert "API Reference" in result
        assert "2 entries shown" in result


class TestNotionCreatePageContract:
    """Contract: notion_create_page parses the creation response."""

    @pytest.mark.vcr
    def test_create_page_returns_id_and_url(self):
        result = notion_create_page.invoke(
            {"title": "Sprint 1 Plan", "body": "Sprint goal: ship auth", "parent_id": "root-page-id"}
        )

        assert "Sprint 1 Plan" in result
        assert "99999999999999999999999999999999" in result
        assert "notion.so" in result


class TestNotionErrorResponsesContract:
    """Contract: Notion error responses become user-friendly messages."""

    @pytest.mark.vcr
    def test_401_bad_token(self):
        result = notion_search_pages.invoke({"query": "test"})

        assert "authentication failed" in result.lower()
