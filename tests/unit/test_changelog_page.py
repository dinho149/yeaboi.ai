"""Tests for the Changelog TUI page loop (_run_changelog_page).

Driven with a scripted ``read_key`` and a fake Live, the same shape the other
page-loop tests use — no terminal, no threads of our own.

The keys carry the weight here. Three of the page's four gestures are ones the
builder cannot test: Space arrives from ``read_key`` as ``" "`` and not as
``"space"``, Shift+Tab only exists because the shared input layer decodes the
back-tab escape, and the scroll keys must be able to move the viewport away from
the selection without the anchor dragging it back on the next repaint.
"""

from __future__ import annotations

import pytest

from yeaboi import changelog
from yeaboi.changelog import ChangelogEntry, ChangelogHighlight
from yeaboi.ui import mode_select


class _Live:
    """Captures whatever the loop renders; the last frame is the assertion."""

    def __init__(self):
        self.frames: list = []

    def update(self, renderable):
        self.frames.append(renderable)


class _Console:
    size = (100, 30)


def _keys(*sequence):
    """A read_key that plays a script; timeout=0.0 polls drain as empty."""
    remaining = list(sequence)

    def _read(timeout=None):
        if timeout == 0.0:
            return ""  # nothing buffered — ends a coalesced scroll burst
        return remaining.pop(0) if remaining else "esc"

    return _read


def _entries() -> list[ChangelogEntry]:
    areas = ("planning", "retro", "standup")
    return [
        ChangelogEntry(
            version=f"3.{n}.0",
            date="2026-08-20",
            headline=f"Release {n}",
            summary="Something changed. " * 4,
            highlights=tuple(ChangelogHighlight(text=f"highlight {n}-{i}", areas=(areas[n % 3],)) for i in range(3)),
        )
        for n in range(20, 0, -1)
    ]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A throwaway seen-marker file and a fixed ledger — no bundled data, no network."""
    monkeypatch.setattr("yeaboi.paths.DATA_DIR", tmp_path)
    monkeypatch.setattr("yeaboi.paths.CHANGELOG_SEEN_FILE", tmp_path / "changelog_seen.json")
    monkeypatch.setattr(changelog, "load_changelog", _entries)
    monkeypatch.setattr(
        "yeaboi.update_check.get_update_status",
        lambda: {"current": "3.20.0", "latest": "", "update_available": False, "upgrade_command": "", "is_dev": False},
    )
    return tmp_path


def _run(read_key) -> _Live:
    live = _Live()
    mode_select._run_changelog_page(_Console(), live, read_key, 0.05, True)
    return live


class TestChangelogPageLoop:
    def test_esc_closes_and_marks_the_newest_release_read(self, env):
        _run(_keys("esc"))
        assert changelog.read_seen_version() == "3.20.0"

    def test_opening_leads_with_the_catch_up_digest(self, env):
        """The anchor must not scroll past the block the page was redesigned to lead with."""
        from rich.console import Console as RichConsole

        changelog.write_seen_version("3.15.0")
        console = RichConsole(width=100, height=40, legacy_windows=False, record=True)
        console.print(_run(_keys("esc")).frames[0])
        assert "5 releases since v3.15.0" in console.export_text()

    def test_digest_is_absent_on_a_first_visit(self, env):
        from rich.console import Console as RichConsole

        console = RichConsole(width=100, height=40, legacy_windows=False, record=True)
        console.print(_run(_keys("esc")).frames[0])
        assert "since v" not in console.export_text()

    def test_space_toggles_the_expansion(self, env):
        """read_key emits a bare ' ' for Space, never the word 'space'."""
        from rich.console import Console as RichConsole

        def _text(frame) -> str:
            console = RichConsole(width=100, height=40, legacy_windows=False, record=True)
            console.print(frame)
            return console.export_text()

        collapsed = _run(_keys("esc")).frames[-1]
        expanded = _run(_keys(" ", "esc")).frames[-1]
        # The newest release starts expanded, so Space on it collapses it.
        assert "highlight 20-0" in _text(collapsed)
        assert "highlight 20-0" not in _text(expanded)

    def test_down_moves_the_selection(self, env):
        from rich.console import Console as RichConsole

        console = RichConsole(width=100, height=40, legacy_windows=False, record=True)
        console.print(_run(_keys("down", "esc")).frames[-1])
        selected = [line for line in console.export_text().splitlines() if "▸" in line]
        assert selected and "v3.19.0" in selected[0]

    def test_tab_cycles_the_area_filter_forward(self, env):
        from rich.console import Console as RichConsole

        console = RichConsole(width=100, height=40, legacy_windows=False, record=True)
        console.print(_run(_keys("tab", "esc")).frames[-1])
        out = console.export_text()
        # First chip past "all" is the mode grid's first area present in the ledger.
        assert "[planning]" in out

    def test_shift_tab_cycles_backwards(self, env):
        """Only reachable because the shared input layer decodes the back-tab CSI."""
        from rich.console import Console as RichConsole

        console = RichConsole(width=100, height=40, legacy_windows=False, record=True)
        console.print(_run(_keys("shift+tab", "esc")).frames[-1])
        out = console.export_text()
        assert "[retro]" in out  # wrapped to the last chip in the ring

    def test_filter_row_keeps_the_whole_ring_while_filtered(self, env):
        """Filtering must not shrink the chips to the ones that survived it."""
        from rich.console import Console as RichConsole

        console = RichConsole(width=140, height=40, legacy_windows=False, record=True)
        console.print(_run(_keys("tab", "esc")).frames[-1])
        out = console.export_text()
        for area in ("planning", "retro", "standup"):
            assert area in out

    def test_scrolling_hands_the_selection_to_what_is_on_screen(self, env):
        """Otherwise the next arrow press teleports back to wherever selection was."""
        from rich.console import Console as RichConsole

        console = RichConsole(width=100, height=40, legacy_windows=False, record=True)
        console.print(_run(_keys("pagedown", "pagedown", "down", "esc")).frames[-1])
        out = console.export_text()
        selected = [line for line in out.splitlines() if "▸" in line]
        assert selected, "a release stays selected after scrolling"
        assert "v3.20.0" not in selected[0], "selection followed the viewport instead of snapping back"

    def test_a_click_is_swallowed(self, env):
        live = _run(_keys("mouse:1,1", "esc"))
        assert live.frames  # no crash, no extra repaint demanded
