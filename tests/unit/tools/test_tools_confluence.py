"""Tests for Confluence tools.

All Confluence API calls are mocked via monkeypatch on _make_confluence_client
so no real network requests are made. Tests cover happy paths, error cases, and
edge cases for each tool, plus helpers and registration in get_tools().
"""

from unittest.mock import MagicMock

from yeaboi.tools import get_tools
from yeaboi.tools.confluence import (
    _MISSING_CONFIG_MSG,
    _confluence_error_msg,
    _strip_html_tags,
    _text_to_storage,
    confluence_create_page,
    confluence_read_page,
    confluence_read_page_text,
    confluence_read_space,
    confluence_search_docs,
    confluence_update_page,
)

# ---------------------------------------------------------------------------
# Helpers — build mock HTTP errors
# ---------------------------------------------------------------------------


def _make_http_error(status_code: int, text: str = "error") -> Exception:
    """Build a requests HTTPError with a mock response."""
    from requests.exceptions import HTTPError

    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = text
    err = HTTPError(text)
    err.response = mock_response
    return err


def _make_page(page_id: str = "123456", title: str = "My Page", body: str = "") -> dict:
    return {
        "id": page_id,
        "title": title,
        "body": {"storage": {"value": body}},
        "_links": {"webui": f"/wiki/spaces/MYSPACE/pages/{page_id}"},
    }


# ---------------------------------------------------------------------------
# _strip_html_tags
# ---------------------------------------------------------------------------


class TestStripHtmlTags:
    def test_removes_simple_tags(self):
        assert _strip_html_tags("<p>Hello world</p>") == "Hello world"

    def test_br_becomes_newline(self):
        result = _strip_html_tags("Line 1<br/>Line 2")
        assert "Line 1" in result
        assert "Line 2" in result

    def test_closing_p_becomes_newline(self):
        result = _strip_html_tags("<p>Para 1</p><p>Para 2</p>")
        assert "Para 1" in result
        assert "Para 2" in result

    def test_html_entities_expanded(self):
        assert "&amp;" not in _strip_html_tags("foo &amp; bar")
        assert "&lt;" not in _strip_html_tags("&lt;tag&gt;")

    def test_empty_string(self):
        assert _strip_html_tags("") == ""

    def test_no_tags_passthrough(self):
        assert _strip_html_tags("plain text") == "plain text"


# ---------------------------------------------------------------------------
# _text_to_storage
# ---------------------------------------------------------------------------


class TestStripHtmlTagsStructure:
    """Structure markers must survive extraction — the doc-quality heuristics
    look for markdown headings/lists/owner lines."""

    def test_headings_become_markdown(self):
        result = _strip_html_tags("<h1>Title</h1><p>Body</p><h3>Sub</h3>")
        assert "# Title" in result
        assert "### Sub" in result

    def test_list_items_become_bullets(self):
        result = _strip_html_tags("<ul><li>one</li><li>two</li></ul>")
        assert "- one" in result
        assert "- two" in result

    def test_table_row_joins_cells_on_one_line(self):
        result = _strip_html_tags("<table><tr><th>Owner</th><td>Jane</td></tr></table>")
        assert any(line.startswith("Owner | Jane") for line in result.splitlines())

    def test_code_macro_becomes_fenced_block(self):
        html = (
            '<ac:structured-macro ac:name="code" ac:schema-version="1">'
            "<ac:plain-text-body><![CDATA[def f():\n    return 1]]></ac:plain-text-body>"
            "</ac:structured-macro>"
        )
        result = _strip_html_tags(html)
        assert result.count("```") == 2
        assert "def f():" in result

    def test_pre_block_becomes_fenced(self):
        result = _strip_html_tags("<p>Run this:</p><pre>make test</pre>")
        assert "```\nmake test\n```" in result


class TestTextToStorage:
    def test_single_paragraph_wrapped(self):
        result = _text_to_storage("Hello world")
        assert result == "<p>Hello world</p>"

    def test_double_newline_splits_paragraphs(self):
        result = _text_to_storage("Para 1\n\nPara 2")
        assert "<p>Para 1</p>" in result
        assert "<p>Para 2</p>" in result

    def test_empty_paragraphs_skipped(self):
        result = _text_to_storage("Para 1\n\n\n\nPara 2")
        assert result.count("<p>") == 2

    def test_empty_string(self):
        assert _text_to_storage("") == ""


# ---------------------------------------------------------------------------
# _confluence_error_msg
# ---------------------------------------------------------------------------


class TestConfluenceErrorMsg:
    def test_401_auth_error(self):
        err = _make_http_error(401)
        assert "authentication failed" in _confluence_error_msg(err).lower()

    def test_403_permission_error(self):
        err = _make_http_error(403)
        assert "permission" in _confluence_error_msg(err).lower()

    def test_404_not_found(self):
        err = _make_http_error(404)
        assert "not found" in _confluence_error_msg(err).lower()

    def test_429_rate_limit(self):
        err = _make_http_error(429)
        assert "rate limit" in _confluence_error_msg(err).lower()

    def test_unknown_code(self):
        err = _make_http_error(500)
        assert "500" in _confluence_error_msg(err)


# ---------------------------------------------------------------------------
# _make_confluence_client — credential resolution (standalone + Jira fallback)
# ---------------------------------------------------------------------------


class TestMakeConfluenceClient:
    _CREDS = (
        "CONFLUENCE_BASE_URL",
        "CONFLUENCE_EMAIL",
        "CONFLUENCE_API_TOKEN",
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
    )

    def _clear(self, monkeypatch):
        for k in self._CREDS:
            monkeypatch.delenv(k, raising=False)

    def _spy_confluence(self, monkeypatch):
        """Replace the Confluence class with a spy so no client is really built."""
        calls = {}

        def _fake(**kwargs):
            calls.update(kwargs)
            return MagicMock()

        monkeypatch.setattr("yeaboi.tools.confluence.Confluence", _fake)
        return calls

    def test_builds_from_standalone_confluence_vars(self, monkeypatch):
        from yeaboi.tools.confluence import _make_confluence_client

        self._clear(monkeypatch)
        calls = self._spy_confluence(monkeypatch)
        monkeypatch.setenv("CONFLUENCE_BASE_URL", "https://conf.atlassian.net")
        monkeypatch.setenv("CONFLUENCE_EMAIL", "conf@x.com")
        monkeypatch.setenv("CONFLUENCE_API_TOKEN", "conf-tok")
        client = _make_confluence_client()
        assert client is not None
        assert calls["url"] == "https://conf.atlassian.net"
        assert calls["username"] == "conf@x.com"
        assert calls["password"] == "conf-tok"

    def test_builds_from_jira_fallback(self, monkeypatch):
        from yeaboi.tools.confluence import _make_confluence_client

        self._clear(monkeypatch)
        calls = self._spy_confluence(monkeypatch)
        monkeypatch.setenv("JIRA_BASE_URL", "https://jira.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "jira@x.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "jira-tok")
        client = _make_confluence_client()
        assert client is not None
        assert calls["url"] == "https://jira.atlassian.net"
        assert calls["username"] == "jira@x.com"

    def test_none_when_no_creds(self, monkeypatch):
        from yeaboi.tools.confluence import _make_confluence_client

        self._clear(monkeypatch)
        self._spy_confluence(monkeypatch)
        assert _make_confluence_client() is None


# ---------------------------------------------------------------------------
# confluence_search_docs
# ---------------------------------------------------------------------------


class TestConfluenceSearchDocs:
    def test_happy_path_returns_titles_and_urls(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.cql.return_value = {
            "results": [
                {
                    "id": "111",
                    "title": "Architecture Overview",
                    "excerpt": "<b>Key</b> design decisions",
                    "_links": {"webui": "/wiki/spaces/ENG/pages/111"},
                }
            ]
        }
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://myorg.atlassian.net")

        result = confluence_search_docs.invoke({"query": "architecture", "space_key": "ENG"})

        assert "Architecture Overview" in result
        assert "111" in result
        assert "https://myorg.atlassian.net/wiki/spaces/ENG/pages/111" in result

    def test_no_results_returns_message(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.cql.return_value = {"results": []}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)

        result = confluence_search_docs.invoke({"query": "missing topic", "space_key": "ENG"})

        assert "No Confluence pages found" in result

    def test_falls_back_to_env_space_key(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.cql.return_value = {"results": []}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)
        monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "ENVSPACE")

        confluence_search_docs.invoke({"query": "test", "space_key": ""})

        cql_used = mock_client.cql.call_args[0][0]
        assert "ENVSPACE" in cql_used

    def test_no_space_key_searches_globally(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.cql.return_value = {"results": []}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)
        monkeypatch.delenv("CONFLUENCE_SPACE_KEY", raising=False)

        confluence_search_docs.invoke({"query": "test", "space_key": ""})

        cql_used = mock_client.cql.call_args[0][0]
        assert "space" not in cql_used.lower()

    def test_http_error_401(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.cql.side_effect = _make_http_error(401)
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)

        result = confluence_search_docs.invoke({"query": "test"})

        assert "authentication failed" in result.lower()

    def test_missing_config_returns_message(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: None)

        result = confluence_search_docs.invoke({"query": "test"})

        assert result == _MISSING_CONFIG_MSG


# ---------------------------------------------------------------------------
# confluence_read_page
# ---------------------------------------------------------------------------


class TestConfluenceReadPage:
    def test_happy_path_by_id(self, monkeypatch):
        page = _make_page("999", "ADR-001", "<p>We chose PostgreSQL.</p>")
        mock_client = MagicMock()
        mock_client.get_page_by_id.return_value = page
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://myorg.atlassian.net")

        result = confluence_read_page.invoke({"page_id": "999"})

        assert "ADR-001" in result
        assert "PostgreSQL" in result
        assert "999" in result

    def test_happy_path_by_title(self, monkeypatch):
        page = _make_page("888", "Runbook", "<p>Restart instructions.</p>")
        mock_client = MagicMock()
        mock_client.get_page_by_title.return_value = page
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://myorg.atlassian.net")
        monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "OPS")

        result = confluence_read_page.invoke({"page_title": "Runbook"})

        mock_client.get_page_by_title.assert_called_once()
        assert "Runbook" in result
        assert "Restart instructions" in result

    def test_no_page_id_or_title_returns_error(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: MagicMock())

        result = confluence_read_page.invoke({"page_id": "", "page_title": ""})

        assert "Error" in result

    def test_page_not_found_returns_error(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.get_page_by_id.return_value = None
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)

        result = confluence_read_page.invoke({"page_id": "000"})

        assert "not found" in result.lower()

    def test_content_truncated_at_8000_chars(self, monkeypatch):
        long_body = "<p>" + "x" * 10_000 + "</p>"
        page = _make_page("777", "Big Page", long_body)
        mock_client = MagicMock()
        mock_client.get_page_by_id.return_value = page
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        result = confluence_read_page.invoke({"page_id": "777"})

        assert "[Truncated at 8000 characters]" in result
        assert "x" * 8000 in result
        assert "x" * 8001 not in result

    def test_title_required_with_page_title_when_no_env(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: MagicMock())
        monkeypatch.delenv("CONFLUENCE_SPACE_KEY", raising=False)

        result = confluence_read_page.invoke({"page_title": "Some Page", "space_key": ""})

        assert "Error" in result
        assert "space_key" in result or "CONFLUENCE_SPACE_KEY" in result

    def test_missing_config_returns_message(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: None)

        result = confluence_read_page.invoke({"page_id": "123"})

        assert result == _MISSING_CONFIG_MSG

    def test_page_text_uses_rendered_view_when_storage_is_empty(self):
        storage_page = _make_page("999", "Macro page", "<ac:structured-macro />")
        rendered_page = {
            "id": "999",
            "title": "Macro page",
            "body": {"view": {"value": "<p>Rendered macro content</p>"}},
        }
        mock_client = MagicMock()
        mock_client.get_page_by_id.side_effect = [storage_page, rendered_page]

        result = confluence_read_page_text(page_id="999", _client=mock_client)

        assert result["text"] == "Rendered macro content"
        assert result["error"] == ""
        assert mock_client.get_page_by_id.call_args_list[1].kwargs["expand"] == "body.view"


# ---------------------------------------------------------------------------
# confluence_read_space
# ---------------------------------------------------------------------------


class TestConfluenceReadSpace:
    def test_happy_path_lists_pages(self, monkeypatch):
        pages = [
            {"id": "1", "title": "Architecture", "_links": {"webui": "/wiki/pages/1"}},
            {"id": "2", "title": "Runbooks", "_links": {"webui": "/wiki/pages/2"}},
        ]
        mock_client = MagicMock()
        mock_client.get_all_pages_from_space.return_value = pages
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        result = confluence_read_space.invoke({"space_key": "ENG"})

        assert "Architecture" in result
        assert "Runbooks" in result
        assert "ENG" in result

    def test_empty_space_returns_message(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.get_all_pages_from_space.return_value = []
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)

        result = confluence_read_space.invoke({"space_key": "EMPTY"})

        assert "No pages found" in result

    def test_falls_back_to_env_space_key(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.get_all_pages_from_space.return_value = []
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)
        monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "FROMENV")

        confluence_read_space.invoke({"space_key": ""})

        mock_client.get_all_pages_from_space.assert_called_once_with(space="FROMENV", limit=25)

    def test_no_space_key_returns_error(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: MagicMock())
        monkeypatch.delenv("CONFLUENCE_SPACE_KEY", raising=False)

        result = confluence_read_space.invoke({"space_key": ""})

        assert "Error" in result
        assert "CONFLUENCE_SPACE_KEY" in result

    def test_truncation_note_when_at_cap(self, monkeypatch):
        pages = [{"id": str(i), "title": f"Page {i}", "_links": {}} for i in range(1, 6)]
        mock_client = MagicMock()
        mock_client.get_all_pages_from_space.return_value = pages
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        result = confluence_read_space.invoke({"space_key": "ENG", "limit": 5})

        assert "increase limit to see more" in result

    def test_missing_config_returns_message(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: None)

        result = confluence_read_space.invoke({"space_key": "ENG"})

        assert result == _MISSING_CONFIG_MSG


# ---------------------------------------------------------------------------
# confluence_create_page
# ---------------------------------------------------------------------------


class TestConfluenceCreatePage:
    def test_happy_path_returns_id_and_url(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.create_page.return_value = {
            "id": "555",
            "_links": {"webui": "/wiki/spaces/ENG/pages/555"},
        }
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        result = confluence_create_page.invoke(
            {"title": "Sprint 1 Plan", "body": "We will deliver auth.", "space_key": "ENG"}
        )

        assert "Sprint 1 Plan" in result
        assert "555" in result
        assert "https://org.atlassian.net/wiki/spaces/ENG/pages/555" in result

    def test_plain_text_body_converted_to_storage_format(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.create_page.return_value = {"id": "556", "_links": {}}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        confluence_create_page.invoke({"title": "T", "body": "Plain text", "space_key": "ENG"})

        body_passed = mock_client.create_page.call_args[1]["body"]
        assert body_passed.startswith("<p>")

    def test_html_body_passed_through_unchanged(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.create_page.return_value = {"id": "557", "_links": {}}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        html_body = "<p>Already <strong>formatted</strong></p>"
        confluence_create_page.invoke({"title": "T", "body": html_body, "space_key": "ENG"})

        body_passed = mock_client.create_page.call_args[1]["body"]
        assert body_passed == html_body

    def test_falls_back_to_env_space_key(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.create_page.return_value = {"id": "558", "_links": {}}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")
        monkeypatch.setenv("CONFLUENCE_SPACE_KEY", "ENVSPACE")

        confluence_create_page.invoke({"title": "T", "body": "body", "space_key": ""})

        assert mock_client.create_page.call_args[1]["space"] == "ENVSPACE"

    def test_http_error_403(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.create_page.side_effect = _make_http_error(403)
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)

        result = confluence_create_page.invoke({"title": "T", "body": "B", "space_key": "ENG"})

        assert "permission" in result.lower()

    def test_missing_config_returns_message(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: None)

        result = confluence_create_page.invoke({"title": "T", "body": "B", "space_key": "ENG"})

        assert result == _MISSING_CONFIG_MSG


# ---------------------------------------------------------------------------
# confluence_update_page
# ---------------------------------------------------------------------------


class TestConfluenceUpdatePage:
    def test_happy_path_returns_title_and_url(self, monkeypatch):
        existing = {
            "id": "111",
            "title": "Old Sprint Plan",
            "_links": {"webui": "/wiki/pages/111"},
        }
        mock_client = MagicMock()
        mock_client.get_page_by_id.return_value = existing
        mock_client.update_page.return_value = {"id": "111"}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        result = confluence_update_page.invoke({"page_id": "111", "body": "Updated content", "title": "Sprint 2 Plan"})

        assert "Sprint 2 Plan" in result
        assert "111" in result
        assert "https://org.atlassian.net/wiki/pages/111" in result

    def test_preserves_existing_title_when_omitted(self, monkeypatch):
        existing = {"id": "222", "title": "Existing Title", "_links": {}}
        mock_client = MagicMock()
        mock_client.get_page_by_id.return_value = existing
        mock_client.update_page.return_value = {}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        result = confluence_update_page.invoke({"page_id": "222", "body": "New content"})

        # update_page should be called with the existing title
        update_kwargs = mock_client.update_page.call_args[1]
        assert update_kwargs["title"] == "Existing Title"
        assert "Existing Title" in result

    def test_page_not_found_returns_error(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.get_page_by_id.return_value = None
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)

        result = confluence_update_page.invoke({"page_id": "000", "body": "content"})

        assert "not found" in result.lower()

    def test_version_comment_passed_to_update(self, monkeypatch):
        existing = {"id": "333", "title": "Page", "_links": {}}
        mock_client = MagicMock()
        mock_client.get_page_by_id.return_value = existing
        mock_client.update_page.return_value = {}
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: mock_client)
        monkeypatch.setenv("JIRA_BASE_URL", "https://org.atlassian.net")

        confluence_update_page.invoke({"page_id": "333", "body": "content", "version_comment": "Added Sprint 3"})

        update_kwargs = mock_client.update_page.call_args[1]
        assert update_kwargs["version_comment"] == "Added Sprint 3"

    def test_missing_config_returns_message(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.confluence._make_confluence_client", lambda: None)

        result = confluence_update_page.invoke({"page_id": "111", "body": "content"})

        assert result == _MISSING_CONFIG_MSG


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


class TestConfluenceToolsRegistered:
    def test_all_five_confluence_tools_in_get_tools(self):
        tools = get_tools()
        names = {t.name for t in tools}
        expected = {
            "confluence_search_docs",
            "confluence_read_page",
            "confluence_read_space",
            "confluence_create_page",
            "confluence_update_page",
        }
        assert expected.issubset(names), f"Missing Confluence tools: {expected - names}"

    def test_total_tool_count_is_thirty(self):
        assert len(get_tools()) == 43
