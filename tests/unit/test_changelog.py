"""Tests for the bundled changelog loader (src/yeaboi/changelog.py)."""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path

import pytest

from yeaboi import changelog
from yeaboi.changelog import (
    ALL_SURFACES,
    AREA_COLORS,
    VALID_AREAS,
    VALID_SURFACES,
    ChangelogEntry,
    ChangelogHighlight,
    changelog_areas,
    entries_since,
    filter_by_area,
    filter_for_surface,
    load_changelog,
    read_seen_version,
    write_seen_version,
)


def _raw_entries() -> list[dict]:
    """The shipped JSON as written, before the loader launders anything.

    The copy guards below must see what the release bot actually committed:
    ``_coerce_areas`` silently turns an invalid tag into "general", so asserting
    on loaded entries would pass on data that is wrong on disk.
    """
    raw = (resources.files("yeaboi") / "changelog_data.json").read_text(encoding="utf-8")
    return json.loads(raw)["entries"]


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

    def test_versions_unique(self):
        versions = [e.version for e in load_changelog()]
        assert len(versions) == len(set(versions))

    def test_current_version_has_an_entry(self):
        """A release always ships its notes — the bot writes the entry with the bump."""
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        if not pyproject.exists():  # installed-package test run
            pytest.skip("source tree not available")
        match = re.search(r'^version = "([^"]+)"', pyproject.read_text(encoding="utf-8"), re.M)
        assert match, "pyproject.toml has no version line"
        assert match.group(1) in {e.version for e in load_changelog()}


class TestCopyContract:
    """The shipped notes are product copy, not pull-request descriptions.

    Every rule here is also stated in the release bot's prompt
    (``.github/workflows/auto-version.yml``), so an entry that breaks one reds the
    PR that wrote it. Fix the entry, never the guard.
    """

    HEADLINE_MAX = 60
    SUMMARY_MAX = 240
    SENTENCE_MAX = 2
    HIGHLIGHT_MAX = 90
    HIGHLIGHTS_MAX = 4

    # CamelCase-shaped names that are products, not internals.
    PRODUCT_NAMES = frozenset(
        {"GitHub", "DevOps", "PyPI", "JetBrains", "OpenAI", "JavaScript", "TypeScript", "DeepSeek"}
    )

    BANNED = (
        ("backtick", r"`"),
        ("command-line flag", r"(?<![\w-])--[a-z][a-z-]{2,}"),
        ("function call", r"\b\w+\(\)"),
        ("file name", r"\b[\w-]+\.(?:py|json|ts|tsx|md|yml|yaml|toml|sh|html|css)\b"),
        ("source path", r"\b(?:src|tests)/"),
        ("snake_case identifier", r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b"),
        ("SHOUTING_CASE identifier", r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b"),
    )

    def _strings(self, entry: dict):
        yield "headline", entry.get("headline", "")
        yield "summary", entry.get("summary", "")
        for hl in entry.get("highlights") or []:
            yield "highlight", hl.get("text", "")

    def test_headlines(self):
        for entry in _raw_entries():
            headline = entry.get("headline", "")
            assert headline, f"{entry['version']}: no headline"
            assert len(headline) <= self.HEADLINE_MAX, f"{entry['version']}: headline is {len(headline)} chars"
            assert not headline.endswith("."), f"{entry['version']}: headline ends with a full stop"

    def test_summaries(self):
        for entry in _raw_entries():
            summary = entry.get("summary", "")
            assert summary, f"{entry['version']}: no summary"
            assert len(summary) <= self.SUMMARY_MAX, f"{entry['version']}: summary is {len(summary)} chars"

    def test_summaries_are_at_most_two_sentences(self):
        for entry in _raw_entries():
            sentences = [part for part in re.split(r"(?<=[.!?])\s+", entry.get("summary", "")) if part]
            assert len(sentences) <= self.SENTENCE_MAX, f"{entry['version']}: {len(sentences)} sentences"

    def test_highlights(self):
        for entry in _raw_entries():
            highlights = entry.get("highlights") or []
            assert 1 <= len(highlights) <= self.HIGHLIGHTS_MAX, f"{entry['version']}: {len(highlights)} highlights"
            for hl in highlights:
                text = hl.get("text", "")
                assert text, f"{entry['version']}: highlight with no text"
                assert len(text) <= self.HIGHLIGHT_MAX, f"{entry['version']}: highlight is {len(text)} chars"
                assert not text.endswith("."), f"{entry['version']}: highlight ends with a full stop"

    def test_no_internal_identifiers(self):
        for entry in _raw_entries():
            for field, text in self._strings(entry):
                for label, pattern in self.BANNED:
                    hit = re.search(pattern, text)
                    assert hit is None, f"{entry['version']} {field}: {label} {hit.group(0)!r} in {text!r}"

    def test_no_camelcase_internals(self):
        """CamelCase reads as a class name unless it is a product the user knows."""
        for entry in _raw_entries():
            for field, text in self._strings(entry):
                for token in re.findall(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b", text):
                    assert token in self.PRODUCT_NAMES, f"{entry['version']} {field}: {token!r} looks internal"

    def test_areas_valid_on_disk(self):
        """A surface name in `areas` is a bot bug the loader would otherwise hide."""
        for entry in _raw_entries():
            for hl in entry.get("highlights") or []:
                areas = hl.get("areas") or []
                assert areas, f"{entry['version']}: highlight without areas"
                assert set(areas) <= VALID_AREAS, f"{entry['version']}: bad areas {areas}"
                assert not set(areas) & VALID_SURFACES, f"{entry['version']}: surface used as an area"

    def test_surfaces_valid_on_disk(self):
        for entry in _raw_entries():
            for hl in entry.get("highlights") or []:
                surfaces = hl.get("surfaces")
                if surfaces is None:
                    continue
                assert surfaces, f"{entry['version']}: empty surfaces list — omit the key instead"
                assert set(surfaces) <= VALID_SURFACES, f"{entry['version']}: bad surfaces {surfaces}"


class TestHeadlineFallback:
    def test_missing_headline_falls_back_to_first_sentence(self, monkeypatch):
        _patch_data(
            monkeypatch,
            '{"entries": [{"version": "1.0.0", "summary": "Plans build themselves. And more.",'
            ' "highlights": [{"text": "x", "areas": ["planning"]}]}]}',
        )
        assert load_changelog()[0].headline == "Plans build themselves"

    def test_explicit_headline_wins(self, monkeypatch):
        _patch_data(
            monkeypatch,
            '{"entries": [{"version": "1.0.0", "headline": "Real headline", "summary": "Something else.",'
            ' "highlights": [{"text": "x", "areas": ["planning"]}]}]}',
        )
        assert load_changelog()[0].headline == "Real headline"

    def test_no_summary_leaves_headline_empty(self, monkeypatch):
        _patch_data(monkeypatch, '{"entries": [{"version": "1.0.0", "highlights": []}]}')
        assert load_changelog()[0].headline == ""


class TestSeenVersion:
    def test_absent_file_reads_empty(self, tmp_path, monkeypatch):
        from yeaboi import paths

        monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
        monkeypatch.setattr(paths, "CHANGELOG_SEEN_FILE", tmp_path / "changelog_seen.json")
        assert read_seen_version() == ""

    def test_round_trip(self, tmp_path, monkeypatch):
        from yeaboi import paths

        monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
        monkeypatch.setattr(paths, "CHANGELOG_SEEN_FILE", tmp_path / "changelog_seen.json")
        write_seen_version("3.30.0")
        assert read_seen_version() == "3.30.0"

    def test_empty_version_is_not_written(self, tmp_path, monkeypatch):
        from yeaboi import paths

        monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
        monkeypatch.setattr(paths, "CHANGELOG_SEEN_FILE", tmp_path / "changelog_seen.json")
        write_seen_version("")
        assert not (tmp_path / "changelog_seen.json").exists()

    def test_corrupt_file_reads_empty(self, tmp_path, monkeypatch):
        from yeaboi import paths

        monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
        monkeypatch.setattr(paths, "CHANGELOG_SEEN_FILE", tmp_path / "changelog_seen.json")
        (tmp_path / "changelog_seen.json").write_text("{nope", encoding="utf-8")
        assert read_seen_version() == ""


class TestEntriesSince:
    ENTRIES = [
        ChangelogEntry(version="3.2.0"),
        ChangelogEntry(version="3.1.0"),
        ChangelogEntry(version="3.0.0"),
    ]

    def test_returns_newer_only(self):
        assert [e.version for e in entries_since(self.ENTRIES, "3.0.0")] == ["3.2.0", "3.1.0"]

    def test_nothing_newer(self):
        assert entries_since(self.ENTRIES, "3.2.0") == []

    def test_unknown_version_claims_nothing(self):
        assert entries_since(self.ENTRIES, "") == []
        assert entries_since(self.ENTRIES, "not-a-version") == []


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


class TestAreaHelpers:
    ENTRIES = [
        ChangelogEntry(
            version="2.0.0",
            highlights=(
                ChangelogHighlight(text="a", areas=("retro",)),
                ChangelogHighlight(text="b", areas=("planning",)),
            ),
        ),
        ChangelogEntry(version="1.0.0", highlights=(ChangelogHighlight(text="c", areas=("planning",)),)),
    ]

    def test_areas_come_back_in_mode_grid_order(self):
        assert changelog_areas(self.ENTRIES) == ["planning", "retro"]

    def test_filter_keeps_only_matching_highlights(self):
        filtered = filter_by_area(self.ENTRIES, "retro")
        assert [e.version for e in filtered] == ["2.0.0"]
        assert [hl.text for hl in filtered[0].highlights] == ["a"]

    def test_empty_filter_is_a_no_op(self):
        assert filter_by_area(self.ENTRIES, "") is self.ENTRIES

    def test_unknown_area_matches_nothing(self):
        assert filter_by_area(self.ENTRIES, "nope") == []
