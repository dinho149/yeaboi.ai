"""Render tests for the Changelog page builder (_build_changelog_screen)."""

from __future__ import annotations

import io

from rich.console import Console
from rich.panel import Panel

from yeaboi.changelog import ChangelogEntry, ChangelogHighlight
from yeaboi.ui.mode_select.screens._screens_secondary import (
    _build_changelog_screen,
    _changelog_short_date,
)


def _entries() -> list[ChangelogEntry]:
    return [
        ChangelogEntry(
            version="2.12.0",
            date="2026-07-18",
            headline="Analysis reads as one page",
            summary="Analysis results redesigned.",
            highlights=(
                ChangelogHighlight(text="Overview plus section cards", areas=("analysis",)),
                ChangelogHighlight(text="Tagged two ways", areas=("planning", "general")),
            ),
        ),
        ChangelogEntry(
            version="2.11.0",
            date="2026-07-18",
            headline="Every mode keeps its own log",
            summary="Unified logging.",
            highlights=(ChangelogHighlight(text="Per-mode log files", areas=("settings",)),),
        ),
    ]


def _update(available: bool) -> dict:
    return {
        "current": "2.11.0",
        "latest": "2.12.0" if available else "",
        "update_available": available,
        "upgrade_command": "uv tool upgrade yeaboi",
        "is_dev": False,
    }


def _render(panel: Panel, width: int = 100, height: int = 40) -> str:
    console = Console(file=io.StringIO(), width=width, height=height + 5, legacy_windows=False)
    console.print(panel)
    return console.file.getvalue()


def _open(entries: list[ChangelogEntry]) -> set[str]:
    """Every release expanded — what the render assertions below need to see."""
    return {e.version for e in entries}


class TestBuildChangelogScreen:
    def test_returns_panel(self):
        assert isinstance(_build_changelog_screen(_entries(), width=80, height=24), Panel)

    def test_respects_exact_height(self):
        panel = _build_changelog_screen(_entries(), width=80, height=24)
        out = _render(panel, width=80, height=24)
        assert len(out.splitlines()) == 24

    def test_respects_exact_height_when_expanded(self):
        entries = _entries()
        panel = _build_changelog_screen(entries, width=80, height=24, expanded=_open(entries))
        assert len(_render(panel, width=80, height=24).splitlines()) == 24

    def test_collapsed_row_shows_version_date_and_headline(self):
        out = _render(_build_changelog_screen(_entries(), width=100, height=40))
        assert "v2.12.0" in out
        assert "Jul 18" in out  # the long ISO date is a machine detail, not a reading aid
        assert "Analysis reads as one page" in out
        # A collapsed release shows its headline only — the detail waits for Enter.
        assert "Overview plus section cards" not in out

    def test_expanded_entry_shows_summary_and_highlights(self):
        entries = _entries()
        out = _render(_build_changelog_screen(entries, width=100, height=40, expanded=_open(entries)))
        assert "Analysis results redesigned." in out
        assert "Overview plus section cards" in out

    def test_area_tags_rendered_when_expanded(self):
        entries = _entries()
        out = _render(_build_changelog_screen(entries, width=100, height=40, expanded=_open(entries)))
        assert "analysis" in out

    def test_selected_row_is_marked(self):
        out = _render(_build_changelog_screen(_entries(), width=100, height=40, selected=1))
        selected_line = next(line for line in out.splitlines() if "v2.11.0" in line)
        assert "▸" in selected_line

    def test_status_message_rendered(self):
        out = _render(_build_changelog_screen(_entries(), width=100, height=40, message="Nothing to see here"))
        assert "Nothing to see here" in out

    def test_empty_entries_placeholder(self):
        out = _render(_build_changelog_screen([], width=80, height=24))
        assert "No changelog data available." in out

    def test_empty_filter_result_says_so(self):
        out = _render(_build_changelog_screen([], width=80, height=24, area_filter="retro"))
        assert "Nothing tagged that yet." in out

    def test_upgrade_banner_when_update_available(self):
        out = _render(_build_changelog_screen(_entries(), update_status=_update(True), width=100, height=40))
        assert "v2.12.0 is available" in out
        assert "uv tool upgrade yeaboi" in out

    def test_no_banner_when_current(self):
        out = _render(_build_changelog_screen(_entries(), update_status=_update(False), width=100, height=40))
        assert "is available" not in out

    def test_scroll_clamps_past_end(self):
        panel = _build_changelog_screen(_entries(), scroll_offset=9999, width=80, height=24)
        assert isinstance(panel, Panel)
        assert len(_render(panel, width=80, height=24).splitlines()) == 24

    def test_no_copy_action(self):
        # Copy went when the page became read-only; the footer is keyboard hints.
        out = _render(_build_changelog_screen(_entries(), width=100, height=40))
        assert "copy" not in out.lower()

    def test_key_hints_rendered(self):
        out = _render(_build_changelog_screen(_entries(), width=100, height=40))
        assert "select" in out and "expand" in out

    def test_scroll_meta_published(self):
        meta: dict = {}
        _build_changelog_screen(_entries(), scroll_meta=meta, width=80, height=24)
        # Geometry for the scroll loop, plus the offset the builder actually used.
        assert "max_offset" in meta and "viewport_h" in meta and "offset" in meta

    def test_long_highlight_wraps_without_crash(self):
        entries = [
            ChangelogEntry(
                version="1.0.0",
                date="2026-01-01",
                headline="h",
                summary="s",
                highlights=(ChangelogHighlight(text="word " * 60, areas=("planning", "analysis", "general")),),
            )
        ]
        panel = _build_changelog_screen(entries, width=60, height=24, expanded={"1.0.0"})
        assert len(_render(panel, width=60, height=24).splitlines()) == 24


class TestSinceBlock:
    def test_digest_lists_new_headlines(self):
        entries = _entries()
        out = _render(_build_changelog_screen(entries, width=100, height=40, since=entries[:1], seen_version="2.11.0"))
        assert "1 release since v2.11.0" in out
        assert "Analysis reads as one page" in out

    def test_plural_release_count(self):
        entries = _entries()
        out = _render(_build_changelog_screen(entries, width=100, height=40, since=entries, seen_version="2.10.0"))
        assert "2 releases since v2.10.0" in out

    def test_absent_on_a_first_visit(self):
        out = _render(_build_changelog_screen(_entries(), width=100, height=40, since=[], seen_version=""))
        assert "since v" not in out

    def test_caps_the_list_and_counts_the_rest(self):
        many = [
            ChangelogEntry(version=f"3.{i}.0", date="2026-08-01", headline=f"Headline {i}") for i in range(9, 0, -1)
        ]
        out = _render(_build_changelog_screen(many, width=100, height=40, since=many, seen_version="2.0.0"))
        assert "+4 more" in out


class TestSelectionAnchoring:
    def _tall(self) -> list[ChangelogEntry]:
        return [
            ChangelogEntry(
                version=f"1.{i}.0",
                date="2026-01-01",
                headline=f"Release {i}",
                summary="s",
                highlights=(ChangelogHighlight(text="t", areas=("general",)),),
            )
            for i in range(40, 0, -1)
        ]

    def test_selection_below_the_fold_scrolls_into_view(self):
        entries = self._tall()
        meta: dict = {}
        out = _render(
            _build_changelog_screen(entries, width=100, height=24, selected=30, scroll_meta=meta),
            width=100,
            height=24,
        )
        assert meta["offset"] > 0
        assert "Release 10" in out  # entries[30] is version 1.10.0

    def test_selection_at_the_top_does_not_scroll(self):
        entries = self._tall()
        meta: dict = {}
        _build_changelog_screen(entries, width=100, height=24, selected=0, scroll_offset=0, scroll_meta=meta)
        assert meta["offset"] == 0

    def test_scrolling_away_is_not_dragged_back(self):
        """Without this the anchor would fight every scroll key and pin the page."""
        entries = self._tall()
        meta: dict = {}
        _build_changelog_screen(
            entries, width=100, height=24, selected=0, scroll_offset=20, scroll_meta=meta, anchor=False
        )
        assert meta["offset"] == 20

    def test_selection_move_re_anchors_after_a_scroll(self):
        entries = self._tall()
        meta: dict = {}
        _build_changelog_screen(
            entries, width=100, height=24, selected=0, scroll_offset=20, scroll_meta=meta, anchor=True
        )
        assert meta["offset"] == 0

    def test_expanded_entry_taller_than_viewport_keeps_its_heading(self):
        entries = self._tall()
        long_one = ChangelogEntry(
            version="1.41.0",
            date="2026-01-01",
            headline="The tall one",
            summary="word " * 200,
            highlights=tuple(ChangelogHighlight(text="word " * 15, areas=("general",)) for _ in range(4)),
        )
        out = _render(
            _build_changelog_screen([long_one, *entries], width=100, height=24, selected=0, expanded={"1.41.0"}),
            width=100,
            height=24,
        )
        assert "The tall one" in out


class TestFilterRow:
    def test_shows_the_whole_ring_even_while_filtered(self):
        """Deriving the chips from the filtered entries collapsed the row to two."""
        ring = ["", "planning", "retro", "standup"]
        out = _render(
            _build_changelog_screen(_entries(), width=120, height=24, area_filter="retro", areas=ring),
            width=120,
        )
        assert "[retro]" in out
        for area in ("planning", "standup"):
            assert area in out

    def test_windows_to_fit_and_keeps_the_active_chip(self):
        """Ten areas do not fit at 80 columns; the active chip always survives."""
        ring = [
            "",
            *sorted(
                {
                    "analysis",
                    "planning",
                    "standup",
                    "retro",
                    "performance",
                    "reporting",
                    "usage",
                    "settings",
                    "agents",
                    "general",
                }
            ),
        ]
        out = _render(
            _build_changelog_screen(_entries(), width=80, height=24, area_filter="usage", areas=ring), width=80
        )
        assert "[usage]" in out
        assert "…" in out  # something was clipped, and says so

    def test_falls_back_to_all_with_no_ring(self):
        out = _render(_build_changelog_screen(_entries(), width=80, height=24), width=80)
        assert "[all]" in out


class TestShortDate:
    def test_formats_an_iso_date(self):
        assert _changelog_short_date("2026-08-31") == "Aug 31"

    def test_single_digit_day_has_no_padding_zero(self):
        assert _changelog_short_date("2026-08-05") == "Aug 5"

    def test_unparseable_never_outgrows_the_column(self):
        # It feeds a 6-wide column; a full ISO string would shove the headline across.
        assert len(_changelog_short_date("not-a-date-at-all")) <= 6
        assert _changelog_short_date("") == ""


class TestCollapsedDots:
    def test_general_is_dropped_beside_a_real_area(self):
        """It is on almost every release, so next to a real tag it says nothing."""
        entry = ChangelogEntry(
            version="1.0.0",
            date="2026-01-01",
            headline="Two areas",
            highlights=(
                ChangelogHighlight(text="a", areas=("planning",)),
                ChangelogHighlight(text="b", areas=("general",)),
            ),
        )
        out = _render(_build_changelog_screen([entry], width=100, height=24))
        row = next(line for line in out.splitlines() if "Two areas" in line)
        assert row.count("●") == 1

    def test_general_alone_still_shows(self):
        entry = ChangelogEntry(
            version="1.0.0",
            date="2026-01-01",
            headline="Only general",
            highlights=(ChangelogHighlight(text="a", areas=("general",)),),
        )
        out = _render(_build_changelog_screen([entry], width=100, height=24))
        row = next(line for line in out.splitlines() if "Only general" in line)
        assert row.count("●") == 1
