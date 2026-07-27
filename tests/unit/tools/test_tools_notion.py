"""Tests for Notion tools.

All Notion API calls are mocked via monkeypatch on _make_notion_client so no real
network requests are made. Tests cover happy paths, error cases, and edge cases for
each tool, plus helpers and registration in get_tools(). Mirrors
test_tools_confluence.py.
"""

from unittest.mock import MagicMock

import httpx

from yeaboi.tools import get_tools
from yeaboi.tools.notion import (
    _MISSING_CONFIG_MSG,
    _blocks_to_text,
    _notion_error_msg,
    _page_title,
    _text_to_blocks,
    notion_create_page,
    notion_read_database,
    notion_read_page,
    notion_recent_pages,
    notion_search_pages,
    notion_update_page,
)

# ---------------------------------------------------------------------------
# Helpers — build mock Notion API errors and objects
# ---------------------------------------------------------------------------


def _make_api_error(status: int, message: str = "error") -> Exception:
    """Build a real notion_client APIResponseError with the given status code."""
    from notion_client.errors import APIResponseError

    return APIResponseError(
        code=str(status),
        status=status,
        message=message,
        headers=httpx.Headers(),
        raw_body_text="{}",
    )


def _make_page(page_id: str = "abc123", title: str = "My Page", url: str = "https://notion.so/My-Page-abc123") -> dict:
    return {
        "id": page_id,
        "url": url,
        "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]}},
    }


def _para_block(text: str) -> dict:
    return {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": text}]}}


# ---------------------------------------------------------------------------
# _blocks_to_text
# ---------------------------------------------------------------------------


class TestBlocksToText:
    def test_paragraph_text(self):
        assert _blocks_to_text([_para_block("Hello world")]) == "Hello world"

    def test_heading_gets_blank_line_before(self):
        blocks = [
            _para_block("Intro"),
            {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Section"}]}},
        ]
        result = _blocks_to_text(blocks)
        assert "Intro\n\n## Section" in result

    def test_list_items_get_bullets(self):
        blocks = [{"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "one"}]}}]
        assert _blocks_to_text(blocks) == "- one"

    def test_heading_levels_get_markdown_prefixes(self):
        blocks = [
            {"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Top"}]}},
            {"type": "heading_3", "heading_3": {"rich_text": [{"plain_text": "Deep"}]}},
        ]
        result = _blocks_to_text(blocks)
        assert "# Top" in result
        assert "### Deep" in result

    def test_to_do_renders_checkbox_state(self):
        blocks = [
            {"type": "to_do", "to_do": {"rich_text": [{"plain_text": "done thing"}], "checked": True}},
            {"type": "to_do", "to_do": {"rich_text": [{"plain_text": "open thing"}], "checked": False}},
        ]
        result = _blocks_to_text(blocks)
        assert "- [x] done thing" in result
        assert "- [ ] open thing" in result

    def test_code_block_is_fenced(self):
        blocks = [{"type": "code", "code": {"rich_text": [{"plain_text": "print(1)"}], "language": "python"}}]
        result = _blocks_to_text(blocks)
        assert result == "```\nprint(1)\n```"

    def test_table_row_joins_cells(self):
        blocks = [
            {
                "type": "table_row",
                "table_row": {"cells": [[{"plain_text": "Owner"}], [{"plain_text": "Jane"}]]},
            }
        ]
        assert _blocks_to_text(blocks) == "Owner | Jane"

    def test_unknown_block_types_skipped(self):
        blocks = [{"type": "image", "image": {}}, _para_block("kept")]
        assert _blocks_to_text(blocks) == "kept"

    def test_empty_blocks(self):
        assert _blocks_to_text([]) == ""

    def test_non_dict_blocks_ignored(self):
        assert _blocks_to_text(["nope", None, _para_block("ok")]) == "ok"


# ---------------------------------------------------------------------------
# _text_to_blocks
# ---------------------------------------------------------------------------


class TestTextToBlocks:
    def test_single_paragraph(self):
        blocks = _text_to_blocks("Hello world")
        assert len(blocks) == 1
        assert blocks[0]["paragraph"]["rich_text"][0]["text"]["content"] == "Hello world"

    def test_double_newline_splits(self):
        blocks = _text_to_blocks("Para 1\n\nPara 2")
        assert len(blocks) == 2

    def test_empty_paragraphs_skipped(self):
        blocks = _text_to_blocks("Para 1\n\n\n\nPara 2")
        assert len(blocks) == 2

    def test_empty_string(self):
        assert _text_to_blocks("") == []

    def test_roundtrip_with_blocks_to_text(self):
        # A paragraph block produced by _text_to_blocks carries text under
        # ["text"]["content"], not ["plain_text"], so this documents that the two
        # helpers use the two distinct Notion shapes (write vs read).
        blocks = _text_to_blocks("Roundtrip")
        assert blocks[0]["type"] == "paragraph"


# ---------------------------------------------------------------------------
# _page_title
# ---------------------------------------------------------------------------


class TestPageTitle:
    def test_extracts_title_property(self):
        assert _page_title(_make_page(title="ADR-001")) == "ADR-001"

    def test_untitled_when_no_title_property(self):
        assert _page_title({"properties": {}}) == "Untitled"

    def test_untitled_when_not_a_dict(self):
        assert _page_title("nope") == "Untitled"


# ---------------------------------------------------------------------------
# _notion_error_msg
# ---------------------------------------------------------------------------


class TestNotionErrorMsg:
    def test_401_auth_error(self):
        assert "authentication failed" in _notion_error_msg(_make_api_error(401)).lower()

    def test_403_permission_error(self):
        assert "permission" in _notion_error_msg(_make_api_error(403)).lower()

    def test_404_not_found(self):
        assert "not found" in _notion_error_msg(_make_api_error(404)).lower()

    def test_429_rate_limit(self):
        assert "rate limit" in _notion_error_msg(_make_api_error(429)).lower()

    def test_unknown_code(self):
        assert "500" in _notion_error_msg(_make_api_error(500))


# ---------------------------------------------------------------------------
# notion_search_pages
# ---------------------------------------------------------------------------


class TestNotionSearchPages:
    def test_happy_path_returns_titles_and_urls(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.search.return_value = {"results": [_make_page("111", "Architecture Overview")]}
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        result = notion_search_pages.invoke({"query": "architecture"})

        assert "Architecture Overview" in result
        assert "111" in result
        assert "notion.so" in result

    def test_no_results_returns_message(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.search.return_value = {"results": []}
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        result = notion_search_pages.invoke({"query": "missing topic"})

        assert "No Notion pages found" in result

    def test_filters_to_pages(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.search.return_value = {"results": []}
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        notion_search_pages.invoke({"query": "test"})

        kwargs = mock_client.search.call_args.kwargs
        assert kwargs["filter"] == {"property": "object", "value": "page"}

    def test_http_error_401(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.search.side_effect = _make_api_error(401)
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        result = notion_search_pages.invoke({"query": "test"})

        assert "authentication failed" in result.lower()

    def test_missing_config_returns_message(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: None)

        result = notion_search_pages.invoke({"query": "test"})

        assert result == _MISSING_CONFIG_MSG


# ---------------------------------------------------------------------------
# notion_read_page
# ---------------------------------------------------------------------------


class TestNotionReadPage:
    def test_happy_path(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.pages.retrieve.return_value = _make_page("999", "ADR-001")
        mock_client.blocks.children.list.return_value = {"results": [_para_block("We chose PostgreSQL.")]}
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        result = notion_read_page.invoke({"page_id": "999"})

        assert "ADR-001" in result
        assert "PostgreSQL" in result

    def test_empty_page_id_returns_error(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: MagicMock())

        result = notion_read_page.invoke({"page_id": ""})

        assert "Error" in result

    def test_truncates_long_content(self, monkeypatch):
        long_text = "x" * 9000
        mock_client = MagicMock()
        mock_client.pages.retrieve.return_value = _make_page("1", "Big")
        mock_client.blocks.children.list.return_value = {"results": [_para_block(long_text)]}
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        result = notion_read_page.invoke({"page_id": "1"})

        assert "Truncated" in result

    def test_http_error_404(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.pages.retrieve.side_effect = _make_api_error(404)
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        result = notion_read_page.invoke({"page_id": "999"})

        assert "not found" in result.lower()

    def test_missing_config(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: None)
        assert notion_read_page.invoke({"page_id": "1"}) == _MISSING_CONFIG_MSG


# ---------------------------------------------------------------------------
# notion_read_database
# ---------------------------------------------------------------------------


class TestNotionReadDatabase:
    def test_happy_path_lists_entries(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.data_sources.query.return_value = {"results": [_make_page("1", "Runbook"), _make_page("2", "Spec")]}
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        result = notion_read_database.invoke({"database_id": "db1"})

        assert "Runbook" in result
        assert "Spec" in result

    def test_falls_back_to_database_data_source(self, monkeypatch):
        # First query (id as data source) 404s → resolve via databases.retrieve.
        mock_client = MagicMock()
        mock_client.data_sources.query.side_effect = [
            _make_api_error(404),
            {"results": [_make_page("1", "Runbook")]},
        ]
        mock_client.databases.retrieve.return_value = {"data_sources": [{"id": "ds1"}]}
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        result = notion_read_database.invoke({"database_id": "db1"})

        assert "Runbook" in result
        mock_client.databases.retrieve.assert_called_once()

    def test_empty_database(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.data_sources.query.return_value = {"results": []}
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        result = notion_read_database.invoke({"database_id": "db1"})

        assert "No entries" in result

    def test_empty_id_returns_error(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: MagicMock())
        assert "Error" in notion_read_database.invoke({"database_id": ""})

    def test_missing_config(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: None)
        assert notion_read_database.invoke({"database_id": "db1"}) == _MISSING_CONFIG_MSG


# ---------------------------------------------------------------------------
# notion_create_page
# ---------------------------------------------------------------------------


class TestNotionCreatePage:
    def test_happy_path_uses_explicit_parent(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.pages.create.return_value = _make_page("new1", "Sprint Plan")
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        result = notion_create_page.invoke({"title": "Sprint Plan", "body": "Goals", "parent_id": "parent99"})

        assert "Created Notion page" in result
        assert "Sprint Plan" in result
        assert mock_client.pages.create.call_args.kwargs["parent"] == {"page_id": "parent99"}

    def test_falls_back_to_env_root(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.pages.create.return_value = _make_page("new1", "Plan")
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)
        monkeypatch.setenv("NOTION_ROOT_PAGE_ID", "envroot")

        notion_create_page.invoke({"title": "Plan", "body": "x"})

        assert mock_client.pages.create.call_args.kwargs["parent"] == {"page_id": "envroot"}

    def test_no_parent_returns_error(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: MagicMock())
        monkeypatch.delenv("NOTION_ROOT_PAGE_ID", raising=False)

        result = notion_create_page.invoke({"title": "Plan", "body": "x"})

        assert "Error" in result and "NOTION_ROOT_PAGE_ID" in result

    def test_http_error_403(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.pages.create.side_effect = _make_api_error(403)
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        result = notion_create_page.invoke({"title": "P", "body": "x", "parent_id": "p"})

        assert "permission" in result.lower()

    def test_missing_config(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: None)
        assert notion_create_page.invoke({"title": "P", "body": "x"}) == _MISSING_CONFIG_MSG


# ---------------------------------------------------------------------------
# notion_update_page
# ---------------------------------------------------------------------------


class TestNotionUpdatePage:
    def test_appends_body_blocks(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.pages.retrieve.return_value = _make_page("p1", "Log")
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        result = notion_update_page.invoke({"page_id": "p1", "body": "New entry"})

        mock_client.blocks.children.append.assert_called_once()
        assert "Updated Notion page" in result

    def test_renames_when_title_given(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.pages.retrieve.return_value = _make_page("p1", "Renamed")
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        notion_update_page.invoke({"page_id": "p1", "body": "x", "title": "Renamed"})

        mock_client.pages.update.assert_called_once()

    def test_empty_page_id_returns_error(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: MagicMock())
        assert "Error" in notion_update_page.invoke({"page_id": "", "body": "x"})

    def test_http_error_404(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.blocks.children.append.side_effect = _make_api_error(404)
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        result = notion_update_page.invoke({"page_id": "p1", "body": "x"})

        assert "not found" in result.lower()

    def test_missing_config(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: None)
        assert notion_update_page.invoke({"page_id": "p1", "body": "x"}) == _MISSING_CONFIG_MSG


# ---------------------------------------------------------------------------
# notion_recent_pages (standup helper)
# ---------------------------------------------------------------------------


class TestNotionRecentPages:
    def test_returns_normalized_items(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.search.return_value = {
            "results": [
                {
                    "id": "p1",
                    "last_edited_time": "2999-01-01T10:00:00.000Z",
                    "last_edited_by": {"id": "u1"},
                    "properties": {"Name": {"type": "title", "title": [{"plain_text": "Recent Doc"}]}},
                }
            ]
        }
        mock_client.users.retrieve.return_value = {"name": "Alice"}
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        # Page is future-dated (2999), so any positive look-back keeps it.
        items = notion_recent_pages(days=1)

        assert len(items) == 1
        assert items[0]["kind"] == "page"
        assert items[0]["title"] == "Recent Doc"
        assert items[0]["author"] == "Alice"
        assert items[0]["key"] == "p1"

    def test_filters_out_old_pages(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.search.return_value = {
            "results": [{"id": "old", "last_edited_time": "2000-01-01T00:00:00.000Z", "properties": {}}]
        }
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        assert notion_recent_pages(days=1) == []

    def test_unconfigured_returns_empty(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: None)
        assert notion_recent_pages(days=1) == []

    def test_auth_error_raises_standup_source_error(self, monkeypatch):
        import pytest

        from yeaboi.standup.errors import StandupSourceError

        mock_client = MagicMock()
        mock_client.search.side_effect = _make_api_error(403)
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        with pytest.raises(StandupSourceError):
            notion_recent_pages(days=1)

    def test_other_error_returns_empty(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.search.side_effect = _make_api_error(500)
        monkeypatch.setattr("yeaboi.tools.notion._make_notion_client", lambda: mock_client)

        assert notion_recent_pages(days=1) == []


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_all_notion_tools_registered(self):
        names = {t.name for t in get_tools()}
        assert {
            "notion_search_pages",
            "notion_read_page",
            "notion_read_database",
            "notion_create_page",
            "notion_update_page",
        } <= names
