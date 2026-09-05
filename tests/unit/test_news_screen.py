"""The Front page reader (_screens_news.py) and its loop."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from rich.console import Console

from tests.unit.test_category_screen import FakeDesk, _Console, _keys, _Live, _paper, _plain
from yeaboi.news.paper import Paper, SourceStatus
from yeaboi.news.parse import NewsItem
from yeaboi.ui.mode_select.screens._screens_news import _build_front_page_screen

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _stories(n: int) -> tuple[NewsItem, ...]:
    return tuple(
        NewsItem(id=f"s{i}", title=f"Story {i}", url=f"https://news.example/{i}", source_name="Techmeme", column="ai")
        for i in range(1, n + 1)
    )


def _fresh(**kw) -> Paper:
    return Paper(generated_at=(NOW - timedelta(minutes=8)).isoformat(timespec="seconds"), **kw)


def _lines(stories, *, paper=None, height=40, width=100, **kw) -> list[str]:
    paper = paper if paper is not None else _fresh()
    console = Console(width=width, height=height, force_terminal=False)
    panel = _build_front_page_screen(stories, paper=paper, width=width, height=height, now=NOW, **kw)
    rows = console.render_lines(panel, console.options.update(height=height), pad=True)
    return ["".join(seg.text for seg in row) for row in rows]


class TestRender:
    def test_exact_height(self):
        assert len(_lines(_stories(3))) == 40
        assert len(_lines(_stories(3), height=24)) == 24

    def test_lists_every_story_in_edition_order_with_page_numerals(self):
        text = "\n".join(_lines(_stories(4)))
        assert "Inside this edition" in text
        for i in range(1, 5):
            assert f" {i}  Techmeme" in text and f"Story {i}" in text
        assert text.index("Story 1") < text.index("Story 2") < text.index("Story 4")

    def test_the_selected_row_carries_the_marker(self):
        rows = [row for row in _lines(_stories(3), selected=1) if "Story" in row]
        assert "▸" in rows[1] and "▸" not in rows[0]

    def test_folio_shows_the_edition_line_and_the_colophon(self):
        paper = _fresh(sources=tuple(SourceStatus(id=str(i), name=str(i), ok=i < 12) for i in range(14)))
        text = "\n".join(_lines(_stories(2), paper=paper))
        assert "Refreshed 8 minutes ago. Read from 12 outlets, two not answering." in text

    def test_news_off(self):
        text = "\n".join(_lines(_stories(1), enabled=False))
        assert "News is off, showing yeaboi alone." in text

    def test_empty_edition(self):
        text = "\n".join(_lines((), paper=Paper(stale=True)))
        assert "Nothing to read yet." in text and "Refreshing." in text

    def test_the_viewport_follows_the_selection_and_shows_a_scrollbar(self):
        rows = _lines(_stories(12), height=18, selected=11)
        text = "\n".join(rows)
        assert "Story 12" in text and "Story 1 " not in text
        assert "█" in text or "┃" in text or "│" in text  # the track

    def test_hints_and_background(self):
        from yeaboi.ui.shared._components import CHANGELOG_THEME

        text = "\n".join(_lines(_stories(2)))
        assert "enter open" in text and "r refresh" in text and "esc back" in text
        console = Console(width=100, height=40, force_terminal=True, color_system="truecolor")
        with console.capture() as cap:
            console.print(_build_front_page_screen(_stories(2), paper=_fresh(), width=100, height=40, now=NOW))
        r, g, b = CHANGELOG_THEME.bg.removeprefix("rgb(").removesuffix(")").split(",")
        assert f"48;2;{r};{g};{b}" in cap.get()


def _run(*keys, desk=None):
    import yeaboi.ui.mode_select as ms

    live = _Live()
    ms._run_front_page_page(_Console(), live, _keys(*keys), 0.0, True, desk=desk or FakeDesk())
    return live


class TestLoop:
    def test_esc_returns_and_prints_the_edition(self):
        live = _run("esc")
        assert "Story one" in _plain(live.frames[-1]) and "Story two" in _plain(live.frames[-1])

    def test_enter_opens_the_selected_story(self, monkeypatch):
        import yeaboi.ui.mode_select as ms

        opened = []
        monkeypatch.setattr(ms, "_open_story", lambda url: opened.append(url) or True)
        _run("down", "enter", "up", "o", "q")
        assert opened == ["https://news.example/Story two", "https://news.example/Story one"]

    def test_r_asks_for_a_fresh_paper(self):
        class Recording(FakeDesk):
            refreshes = 0

            def get_paper(self, *, refresh=False):
                self.refreshes += refresh
                return super().get_paper(refresh=refresh)

        desk = Recording()
        _run("r", "q", desk=desk)
        assert desk.refreshes == 1

    def test_r_does_nothing_when_news_is_off(self):
        desk = FakeDesk((_paper("Note"), False), enabled=False)
        _run("r", "q", desk=desk)
        assert desk.asked == 1

    @pytest.mark.parametrize("key", ["end", "pagedown"])
    def test_jumps_land_on_the_last_story(self, key, monkeypatch):
        import yeaboi.ui.mode_select as ms

        opened = []
        monkeypatch.setattr(ms, "_open_story", lambda url: opened.append(url) or True)
        _run(key, "enter", "q")
        assert opened == ["https://news.example/Story two"]

    def test_an_empty_edition_accepts_enter(self):
        live = _run("enter", "q", desk=FakeDesk((Paper(stale=True), True)))
        assert "Nothing to read yet." in _plain(live.frames[-1])
