"""Tests for the bundled changelog loader (src/yeaboi/changelog.py)."""

from __future__ import annotations

import re

import pytest

from yeaboi import changelog
from yeaboi.changelog import (
    ALL_SURFACES,
    AREA_COLORS,
    VALID_AREAS,
    VALID_SURFACES,
    ChangelogEntry,
    ChangelogHighlight,
    filter_for_surface,
    load_changelog,
)


class _FakeTraversable:
    def __init__(self, text: str | None):
        self._text = text

    def __truediv__(self, name: str):
        return self

    def read_text(self, encoding: str = "utf-8") -> str:
        if self._text is None:
            raise FileNotFoundError("changelog_data.json")
        return self._text


def _patch_data(monkeypatch, text: str | None):
    monkeypatch.setattr(changelog.resources, "files", lambda pkg: _FakeTraversable(text))


class TestBundledData:
    """Integrity checks against the real shipped changelog_data.json."""

    def test_loads_real_file(self):
        entries = load_changelog()
        assert entries, "bundled changelog should not be empty"
        assert all(isinstance(e, ChangelogEntry) for e in entries)

    def test_newest_first(self):
        versions = [tuple(int(p) for p in e.version.split(".")) for e in load_changelog()]
        assert versions == sorted(versions, reverse=True)

    def test_all_areas_valid(self):
        for entry in load_changelog():
            for hl in entry.highlights:
                assert hl.areas, f"{entry.version}: highlight without areas"
                assert set(hl.areas) <= VALID_AREAS

    def test_all_surfaces_valid(self):
        for entry in load_changelog():
            for hl in entry.highlights:
                assert hl.surfaces, f"{entry.version}: highlight without surfaces"
                assert set(hl.surfaces) <= VALID_SURFACES

    def test_dates_iso(self):
        for entry in load_changelog():
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry.date), entry.version

    def test_every_entry_has_summary_and_highlights(self):
        for entry in load_changelog():
            assert entry.summary, entry.version
            assert entry.highlights, entry.version


class TestGracefulLoading:
    def test_missing_file_returns_empty(self, monkeypatch):
        _patch_data(monkeypatch, None)
        assert load_changelog() == []

    def test_corrupt_json_returns_empty(self, monkeypatch):
        _patch_data(monkeypatch, "{not json")
        assert load_changelog() == []

    def test_non_dict_root_returns_empty(self, monkeypatch):
        _patch_data(monkeypatch, "[1, 2, 3]")
        assert load_changelog() == []

    def test_malformed_entries_skipped(self, monkeypatch):
        _patch_data(
            monkeypatch,
            '{"entries": [{"version": "1.0.0", "summary": "ok", "highlights": []},'
            ' {"no_version": true}, "just-a-string", {"version": ""}]}',
        )
        entries = load_changelog()
        assert [e.version for e in entries] == ["1.0.0"]

    def test_unknown_area_coerced_to_general(self, monkeypatch):
        _patch_data(
            monkeypatch,
            '{"entries": [{"version": "1.0.0", "highlights": [{"text": "x", "areas": ["bogus", "planning"]}]}]}',
        )
        entries = load_changelog()
        assert entries[0].highlights[0].areas == ("general", "planning")

    def test_missing_areas_defaults_to_general(self, monkeypatch):
        _patch_data(monkeypatch, '{"entries": [{"version": "1.0.0", "highlights": [{"text": "x"}]}]}')
        assert load_changelog()[0].highlights[0].areas == ("general",)

    def test_missing_surfaces_defaults_to_all(self, monkeypatch):
        _patch_data(monkeypatch, '{"entries": [{"version": "1.0.0", "highlights": [{"text": "x"}]}]}')
        assert load_changelog()[0].highlights[0].surfaces == ALL_SURFACES

    def test_unknown_surfaces_dropped(self, monkeypatch):
        _patch_data(
            monkeypatch,
            '{"entries": [{"version": "1.0.0",'
            ' "highlights": [{"text": "x", "surfaces": ["bogus", "tui", "tui", "desktop"]}]}]}',
        )
        assert load_changelog()[0].highlights[0].surfaces == ("tui", "desktop")

    def test_all_unknown_surfaces_falls_back_to_all(self, monkeypatch):
        _patch_data(
            monkeypatch,
            '{"entries": [{"version": "1.0.0", "highlights": [{"text": "x", "surfaces": ["bogus", 3]}]}]}',
        )
        assert load_changelog()[0].highlights[0].surfaces == ALL_SURFACES

    def test_non_list_surfaces_falls_back_to_all(self, monkeypatch):
        _patch_data(
            monkeypatch,
            '{"entries": [{"version": "1.0.0", "highlights": [{"text": "x", "surfaces": "tui"}]}]}',
        )
        assert load_changelog()[0].highlights[0].surfaces == ALL_SURFACES

    def test_highlight_without_text_skipped(self, monkeypatch):
        _patch_data(
            monkeypatch,
            '{"entries": [{"version": "1.0.0", "highlights": [{"areas": ["planning"]}, {"text": "kept"}]}]}',
        )
        highlights = load_changelog()[0].highlights
        assert [h.text for h in highlights] == ["kept"]


class TestAreaColors:
    def test_covers_all_valid_areas(self):
        assert set(AREA_COLORS) == set(VALID_AREAS)

    def test_all_rgb_strings(self):
        for color in AREA_COLORS.values():
            assert re.fullmatch(r"rgb\(\d{1,3},\d{1,3},\d{1,3}\)", color)


class TestFilterForSurface:
    def _entries(self):
        return [
            ChangelogEntry(
                version="2.0.0",
                highlights=(
                    ChangelogHighlight(text="everywhere"),
                    ChangelogHighlight(text="terminal only", surfaces=("tui",)),
                    ChangelogHighlight(text="web only", surfaces=("web",)),
                ),
            ),
            ChangelogEntry(
                version="1.0.0",
                highlights=(ChangelogHighlight(text="desktop only", surfaces=("desktop",)),),
            ),
        ]

    def test_keeps_matching_highlights_only(self):
        filtered = filter_for_surface(self._entries(), "tui")
        assert [e.version for e in filtered] == ["2.0.0"]
        assert [h.text for h in filtered[0].highlights] == ["everywhere", "terminal only"]

    def test_untagged_highlight_matches_every_surface(self):
        filtered = filter_for_surface(self._entries(), "desktop")
        assert [e.version for e in filtered] == ["2.0.0", "1.0.0"]
        assert [h.text for h in filtered[1].highlights] == ["desktop only"]

    def test_drops_entries_left_empty(self):
        # 1.0.0's only highlight is desktop-tagged, so a web filter drops it.
        filtered = filter_for_surface(self._entries(), "web")
        assert [e.version for e in filtered] == ["2.0.0"]

    def test_keeps_summary_only_entries(self):
        entry = ChangelogEntry(version="3.0.0", summary="just words")
        assert filter_for_surface([entry], "tui") == [entry]

    def test_preserves_order_and_frozen(self):
        filtered = filter_for_surface(self._entries(), "web")
        assert [e.version for e in filtered] == ["2.0.0"]
        with pytest.raises(AttributeError):
            filtered[0].version = "9.9.9"  # type: ignore[misc]

    def test_empty_input(self):
        assert filter_for_surface([], "tui") == []


class TestDataclasses:
    def test_defaults_for_backward_compat(self):
        assert ChangelogEntry().version == ""
        assert ChangelogHighlight().areas == ()
        assert ChangelogHighlight().surfaces == ALL_SURFACES

    def test_frozen(self):
        entry = ChangelogEntry(version="1.0.0")
        with pytest.raises(AttributeError):
            entry.version = "2.0.0"  # type: ignore[misc]
