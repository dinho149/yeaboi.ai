"""Tests for roadmap/ingest.py — locator parsing, local-file extraction, dispatch."""

import sys
import types

from yeaboi.roadmap.ingest import (
    _MAX_ROADMAP_CHARS,
    RoadmapSource,
    _read_local_file,
    ingest_source,
    parse_confluence_locator,
    parse_notion_locator,
)


class TestConfluenceLocator:
    def test_pretty_url(self):
        url = "https://acme.atlassian.net/wiki/spaces/ENG/pages/123456/Q3-2026-Roadmap"
        assert parse_confluence_locator(url) == "123456"

    def test_viewpage_url(self):
        url = "https://acme.atlassian.net/pages/viewpage.action?pageId=98765"
        assert parse_confluence_locator(url) == "98765"

    def test_bare_id(self):
        assert parse_confluence_locator("  123456 ") == "123456"

    def test_title_passthrough(self):
        assert parse_confluence_locator("Q3 2026 Roadmap") == "Q3 2026 Roadmap"


class TestNotionLocator:
    def test_slug_url(self):
        url = "https://www.notion.so/acme/Q3-Roadmap-0123456789abcdef0123456789abcdef"
        assert parse_notion_locator(url) == "0123456789abcdef0123456789abcdef"

    def test_dashed_id(self):
        raw = "01234567-89ab-cdef-0123-456789abcdef"
        assert parse_notion_locator(raw) == raw

    def test_query_string_stripped(self):
        url = "https://www.notion.so/Q3-0123456789abcdef0123456789abcdef?pvs=4"
        assert parse_notion_locator(url) == "0123456789abcdef0123456789abcdef"

    def test_passthrough(self):
        assert parse_notion_locator("not-an-id") == "not-an-id"


class TestLocalFile:
    def test_markdown_read(self, tmp_path):
        f = tmp_path / "roadmap.md"
        f.write_text("# Q3 Roadmap\n\n- SSO\n- Checkout revamp", encoding="utf-8")
        text, label, warnings = _read_local_file(str(f))
        assert "Checkout revamp" in text
        assert label == "roadmap.md"
        assert warnings == []

    def test_missing_file_warns(self, tmp_path):
        text, label, warnings = _read_local_file(str(tmp_path / "nope.md"))
        assert text == ""
        assert warnings and "not found" in warnings[0]

    def test_unsupported_extension_warns(self, tmp_path):
        f = tmp_path / "roadmap.xlsx"
        f.write_bytes(b"\x00")
        text, _label, warnings = _read_local_file(str(f))
        assert text == ""
        assert warnings and "Unsupported roadmap file type" in warnings[0]

    def test_docx_missing_dep_warns(self, tmp_path, monkeypatch):
        f = tmp_path / "roadmap.docx"
        f.write_bytes(b"\x00")
        monkeypatch.setitem(sys.modules, "docx", None)  # import docx → ImportError
        text, _label, warnings = _read_local_file(str(f))
        assert text == ""
        assert warnings and "uv sync --extra docs" in warnings[0]

    def test_pptx_missing_dep_warns(self, tmp_path, monkeypatch):
        f = tmp_path / "roadmap.pptx"
        f.write_bytes(b"\x00")
        monkeypatch.setitem(sys.modules, "pptx", None)
        text, _label, warnings = _read_local_file(str(f))
        assert text == ""
        assert warnings and "uv sync --extra docs" in warnings[0]

    def test_docx_extraction_with_fake_module(self, tmp_path, monkeypatch):
        """Paragraphs + table cells are extracted (python-docx faked, no hard dep)."""

        class _Para:
            def __init__(self, text):
                self.text = text

        class _Cell:
            def __init__(self, text):
                self.text = text

        class _Row:
            def __init__(self, cells):
                self.cells = [_Cell(c) for c in cells]

        class _Table:
            def __init__(self, rows):
                self.rows = [_Row(r) for r in rows]

        class _Doc:
            paragraphs = [_Para("Q3 goals"), _Para("")]
            tables = [_Table([["Project", "Size"], ["SSO", "Large"]])]

        fake = types.ModuleType("docx")
        fake.Document = lambda _path: _Doc()
        monkeypatch.setitem(sys.modules, "docx", fake)

        f = tmp_path / "roadmap.docx"
        f.write_bytes(b"\x00")
        text, _label, warnings = _read_local_file(str(f))
        assert "Q3 goals" in text
        assert "SSO\tLarge" in text
        assert warnings == []

    def test_pptx_extraction_with_fake_module(self, tmp_path, monkeypatch):
        """Slide markers, text frames, and speaker notes are extracted."""

        class _Frame:
            def __init__(self, text):
                self.text = text

        class _Shape:
            has_text_frame = True

            def __init__(self, text):
                self.text_frame = _Frame(text)

        class _Notes:
            notes_text_frame = _Frame("Ship by August")

        class _Slide:
            shapes = [_Shape("Checkout revamp")]
            has_notes_slide = True
            notes_slide = _Notes()

        class _Pres:
            slides = [_Slide()]

        fake = types.ModuleType("pptx")
        fake.Presentation = lambda _path: _Pres()
        monkeypatch.setitem(sys.modules, "pptx", fake)

        f = tmp_path / "roadmap.pptx"
        f.write_bytes(b"\x00")
        text, _label, warnings = _read_local_file(str(f))
        assert "--- Slide 1 ---" in text
        assert "Checkout revamp" in text
        assert "Notes: Ship by August" in text
        assert warnings == []


class TestIngestSource:
    def test_local_dispatch(self, tmp_path):
        f = tmp_path / "roadmap.md"
        f.write_text("Q3 plans", encoding="utf-8")
        text, label, warnings = ingest_source(RoadmapSource(source_type="local", locator=str(f)))
        assert text == "Q3 plans"
        assert label == "roadmap.md"
        assert warnings == []

    def test_unknown_source_type_warns(self):
        text, _label, warnings = ingest_source(RoadmapSource(source_type="gdrive", locator="x"))
        assert text == ""
        assert warnings and "Unknown roadmap source" in warnings[0]

    def test_truncation_warning(self, tmp_path):
        f = tmp_path / "big.md"
        f.write_text("x" * (_MAX_ROADMAP_CHARS + 100), encoding="utf-8")
        text, _label, warnings = ingest_source(RoadmapSource(source_type="local", locator=str(f)))
        assert len(text) == _MAX_ROADMAP_CHARS
        assert any("truncated" in w.lower() for w in warnings)

    def test_confluence_dispatch_mocked(self, monkeypatch):
        import yeaboi.tools.confluence as conf_mod

        monkeypatch.setattr(
            conf_mod,
            "confluence_read_page_text",
            lambda **kw: {"title": "Q3 Roadmap", "text": "The plan", "truncated": False, "error": ""},
        )
        source = RoadmapSource(source_type="confluence", locator="https://a.atlassian.net/wiki/x/pages/42/Q3")
        text, label, warnings = ingest_source(source)
        assert text == "The plan"
        assert label == "Q3 Roadmap"
        assert warnings == []

    def test_confluence_error_becomes_warning(self, monkeypatch):
        import yeaboi.tools.confluence as conf_mod

        monkeypatch.setattr(
            conf_mod,
            "confluence_read_page_text",
            lambda **kw: {"title": "", "text": "", "truncated": False, "error": "authentication failed"},
        )
        text, _label, warnings = ingest_source(RoadmapSource(source_type="confluence", locator="42"))
        assert text == ""
        assert warnings == ["Confluence: authentication failed"]

    def test_notion_dispatch_mocked(self, monkeypatch):
        import yeaboi.tools.notion as notion_mod

        monkeypatch.setattr(
            notion_mod,
            "notion_read_page_text",
            lambda pid, **kw: {"title": "Roadmap", "text": "Notion plan", "truncated": False, "error": ""},
        )
        source = RoadmapSource(source_type="notion", locator="0123456789abcdef0123456789abcdef")
        text, label, warnings = ingest_source(source)
        assert text == "Notion plan"
        assert label == "Roadmap"
        assert warnings == []

    def test_never_raises_on_reader_exception(self, tmp_path, monkeypatch):
        """A reader blowing up unexpectedly must not escape ingest_source's contract."""
        # Unreadable local path (a directory posing as a file target).
        text, _label, warnings = _read_local_file(str(tmp_path))
        assert text == ""
        assert warnings


class TestSandboxGating:
    def test_file_outside_whitelist_returns_warning(self):
        """Denied roadmap files surface as warnings, not crashes."""
        text, label, warnings = _read_local_file("/denied-sandbox-dir/roadmap.md")
        assert text == ""
        assert label == "roadmap.md"
        assert any("YEABOI_ALLOWED_PATHS" in w for w in warnings)
