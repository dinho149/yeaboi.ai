"""Render tests for the Changelog page builder (_build_changelog_screen)."""

from __future__ import annotations

import io

from rich.console import Console
from rich.panel import Panel

from yeaboi.changelog import ChangelogEntry, ChangelogHighlight
from yeaboi.ui.mode_select.screens._screens_secondary import _build_changelog_screen


def _entries() -> list[ChangelogEntry]:
    return [
        ChangelogEntry(
            version="2.12.0",
            date="2026-07-18",
            summary="Analysis results redesigned.",
            highlights=(
                ChangelogHighlight(text="Overview plus section cards", areas=("analysis",)),
                ChangelogHighlight(text="Tagged two ways", areas=("planning", "general")),
            ),
        ),
        ChangelogEntry(
            version="2.11.0",
            date="2026-07-18",
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


class TestBuildChangelogScreen:
    def test_returns_panel(self):
        assert isinstance(_build_changelog_screen(_entries(), width=80, height=24), Panel)

    def test_respects_exact_height(self):
        panel = _build_changelog_screen(_entries(), width=80, height=24)
        out = _render(panel, width=80, height=24)
        assert len(out.splitlines()) == 24

    def test_shows_versions_dates_and_highlights(self):
        out = _render(_build_changelog_screen(_entries(), width=100, height=40))
        assert "v2.12.0" in out
        assert "2026-07-18" in out
        assert "Analysis results redesigned." in out
        assert "Overview plus section cards" in out

    def test_area_tags_rendered(self):
        out = _render(_build_changelog_screen(_entries(), width=100, height=40))
        assert "analysis" in out

    def test_copy_hint_and_message(self):
        # The Copy/Back button row was replaced by a keyboard hint (c copy · Esc back).
        out = _render(
            _build_changelog_screen(
                _entries(), width=100, height=40, actions=["Copy", "Back"], message="Copied to clipboard"
            )
        )
        assert "copy" in out
        assert "Copied to clipboard" in out
        assert "settings" in out

    def test_empty_entries_placeholder(self):
        out = _render(_build_changelog_screen([], width=80, height=24))
        assert "No changelog data available." in out

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

    def test_copy_hint_present(self):
        # The action row is a keyboard hint; 'Esc back' was dropped once the
        # app-wide back tab covered going back, leaving the copy hint. (Entry
        # text can contain "back", so only the copy hint is asserted here.)
        out = _render(_build_changelog_screen(_entries(), width=100, height=40, actions=["Copy", "Back"]))
        assert "copy" in out

    def test_scroll_meta_published(self):
        meta: dict = {}
        _build_changelog_screen(_entries(), scroll_meta=meta, width=80, height=24)
        assert "max_offset" in meta and "viewport_h" in meta  # geometry published for the scroll loop

    def test_long_highlight_wraps_without_crash(self):
        entries = [
            ChangelogEntry(
                version="1.0.0",
                date="2026-01-01",
                summary="s",
                highlights=(ChangelogHighlight(text="word " * 60, areas=("planning", "analysis", "general")),),
            )
        ]
        panel = _build_changelog_screen(entries, width=60, height=24)
        assert len(_render(panel, width=60, height=24).splitlines()) == 24
