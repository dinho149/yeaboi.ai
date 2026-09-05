"""The Front page (_screens_news.py) and its loop."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from rich.console import Console

from tests.unit.test_category_screen import FakeDesk, _Console, _keys, _Live, _paper, _plain
from yeaboi.news import edition
from yeaboi.news.paper import Paper, SourceStatus
from yeaboi.news.parse import NewsItem
from yeaboi.ui.mode_select.screens._screens_news import _build_front_page_screen, index_lines, picture_scale

NOW = datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc)
SUMMARY = "The lab says the model plans over hours rather than minutes, and hands work back with a written trace."


def _stories(n: int) -> tuple[NewsItem, ...]:
    return tuple(
        NewsItem(
            id=f"s{i}",
            title=f"Story {i} is a headline long enough to matter",
            url=f"https://news.example/{i}",
            source_name="Techmeme",
            column="ai",
            topic="models",
            persona="wizard",
            summary=SUMMARY,
            published=(NOW - timedelta(hours=2)).isoformat(timespec="seconds"),
        )
        for i in range(1, n + 1)
    )


def _fresh(**kw) -> Paper:
    return Paper(generated_at=(NOW - timedelta(minutes=8)).isoformat(timespec="seconds"), **kw)


def _lines(stories, *, current=0, paper=None, height=39, width=100, **kw) -> list[str]:
    paper = paper if paper is not None else _fresh()
    page = edition.page(stories, current, NOW) if stories else None
    console = Console(width=width, height=height, force_terminal=False)
    panel = _build_front_page_screen(
        page, stories=stories, paper=paper, current=current, width=width, height=height, now=NOW, version="3.41.1", **kw
    )
    rows = console.render_lines(panel, console.options.update(height=height), pad=True)
    return ["".join(seg.text for seg in row) for row in rows]


class TestRender:
    def test_exact_height_at_the_floor_and_above(self):
        assert len(_lines(_stories(3))) == 39
        assert len(_lines(_stories(3), height=52, width=190)) == 52
        assert len(_lines(_stories(3), height=45, width=120)) == 45

    def test_the_picture_doubles_when_the_rows_are_there(self):
        assert picture_scale(39, 190) == 1 and picture_scale(51, 190) == 1 and picture_scale(52, 190) == 2
        assert picture_scale(52, 100) == 1  # a narrow sheet keeps the small picture
        small = _lines(_stories(3), height=39)
        large = _lines(_stories(3), height=52, width=190)
        caption = "The wizard, under the stars."
        plate_small = [i for i, row in enumerate(small) if "✦" in row or "╱" in row]
        assert len([row for row in large if "▁▁▁▁" in row]) == 1
        # The plate spans 13 rows at the floor and 26 rows on a tall terminal.
        small_plate = next(i for i, row in enumerate(small) if caption in row) - min(plate_small)
        assert small_plate <= 13
        large_rows = [i for i, row in enumerate(large) if "██" in row or "▀" in row or "▄" in row]
        assert max(large_rows) - min(large_rows) > 20

    def test_the_sheet_sits_in_the_middle_of_a_tall_desk(self):
        rows = _lines(_stories(3), height=48, width=120)
        first = next(i for i, row in enumerate(rows) if "██" in row)
        assert first > 3  # not pressed against the top border

    def test_the_kicker_does_not_repeat_the_outlet(self):
        stories = (
            NewsItem(
                id="r", title="yeaboi 3.41.1: a release", url="u", source_name="yeaboi", column="yeaboi", kind="release"
            ),
        )
        text = "\n".join(_lines(stories))
        assert "From yeaboi" in text and "From yeaboi · yeaboi" not in text

    def test_the_nameplate_and_folio(self):
        text = "\n".join(_lines(_stories(3)))
        assert "██" in text and "░░" in text  # the block nameplate
        assert "Saturday, 5 September 2026" in text and "Vol. 3, No. 41" in text and "Refreshed 8 minutes ago." in text
        assert "═══════" in text

    def test_the_kicker_row_and_headline(self):
        text = "\n".join(_lines(_stories(8), current=1))
        assert "From the AI desk · Techmeme" in text and "‹ 2 of 8 ›" in text
        assert "Story 2 is a headline long enough to matter" in text

    def test_the_spread(self):
        rows = _lines(_stories(3))
        text = "\n".join(rows)
        assert "The wizard, under the stars." in text
        assert "Techmeme, 2 hours ago" in text and "o  Read more at Techmeme" in text
        assert "he lab says the model plans" in text  # the T is the drop cap
        assert "✦" in text  # the observatory's sky
        # The story column sits to the right of the plate.
        byline_row = next(row for row in rows if "Techmeme, 2 hours ago" in row)
        assert byline_row.index("Techmeme, 2 hours ago") > 32

    def test_the_picture_is_tinted_and_wears_the_costume(self):
        console = Console(width=100, height=39, force_terminal=True, color_system="truecolor")
        with console.capture() as cap:
            console.print(
                _build_front_page_screen(
                    edition.page(_stories(2), 0, NOW),
                    stories=_stories(2),
                    paper=_fresh(),
                    width=100,
                    height=39,
                    now=NOW,
                )
            )
        out = cap.get()
        assert "48;2;17;28;20" in out  # the Team tint, the default card
        assert "250;176;44" in out  # the bill and the wizard's stars
        assert "48;2;21;21;27" in out  # the sheet on the desk
        assert "48;2;16;16;20" in out  # the desk itself

    def test_the_agents_card_reads_the_paper_as_the_robo(self):
        card = {"color": "rgb(90,160,210)", "tint": "rgb(15,24,32)", "mascot": "robo"}
        console = Console(width=100, height=39, force_terminal=True, color_system="truecolor")
        with console.capture() as cap:
            console.print(
                _build_front_page_screen(
                    edition.page(_stories(1), 0, NOW),
                    stories=_stories(1),
                    paper=_fresh(),
                    card=card,
                    width=100,
                    height=39,
                    now=NOW,
                )
            )
        assert "140;160;178" in cap.get() and "48;2;15;24;32" in cap.get()

    def test_the_inside_line_folds_and_unfolds(self):
        closed = "\n".join(_lines(_stories(12)))
        assert "Inside this edition, 11 more stories  ▾" in closed
        opened = "\n".join(_lines(_stories(12), index_open=True))
        assert "Inside this edition  ▴" in opened
        assert " 1  Techmeme" not in opened and " 2  Techmeme" in opened and "12  Techmeme" in opened
        assert "▸" in opened and "enter turn to it" in opened
        assert "The wizard, under the stars." not in opened

    def test_two_stories_have_no_index(self):
        assert index_lines(_stories(2), 0) == []
        assert "Inside this edition" not in "\n".join(_lines(_stories(2)))

    def test_the_colophon(self):
        paper = _fresh(sources=tuple(SourceStatus(id=str(i), name=str(i), ok=i < 12) for i in range(14)))
        assert "Read from 12 outlets, two not answering." in "\n".join(_lines(_stories(1), paper=paper))

    def test_news_off_and_empty(self):
        text = "\n".join(_lines((), paper=Paper(stale=True), enabled=False))
        assert "News is off, showing yeaboi alone." in text and "Nothing to read yet." in text

    def test_hints(self):
        text = "\n".join(_lines(_stories(3)))
        assert "←/→ turn" in text and "o open" in text and "tab inside" in text and "r refresh" in text

    def test_fits_the_width_floor(self):
        rows = _lines(_stories(3), width=84)
        assert len(rows) == 39
        assert any("═══" in row and "…" not in row for row in rows)


def _run(*keys, desk=None, card=None):
    import yeaboi.ui.mode_select as ms

    live = _Live()
    ms._run_front_page_page(_Console(), live, _keys(*keys), 0.0, True, desk=desk or FakeDesk(), card=card)
    return live


class TestLoop:
    def test_esc_returns_and_prints_the_story_that_is_up(self):
        live = _run("esc")
        text = _plain(live.frames[-1])
        assert "Story one" in text and "‹ 1 of 2 ›" in text

    def test_arrows_turn_the_page(self):
        assert "Story two" in _plain(_run("right", "esc").frames[-1])
        assert "Story two" in _plain(_run("left", "esc").frames[-1])  # wraps
        assert "Story one" in _plain(_run("]", "[", "esc").frames[-1])

    def test_enter_and_o_open_the_story_that_is_up(self, monkeypatch):
        import yeaboi.ui.mode_select as ms

        opened = []
        monkeypatch.setattr(ms, "_open_story", lambda url: opened.append(url) or True)
        _run("enter", "right", "o", "q")
        assert opened == ["https://news.example/Story one", "https://news.example/Story two"]

    def test_the_index_picks_a_story(self):
        desk = FakeDesk((_paper("One", "Two", "Three", "Four"), False))
        live = _run("tab", "down", "down", "enter", "esc", desk=desk)
        text = _plain(live.frames[-1])
        assert "‹ 4 of 4 ›" in text and "▴" not in text

    def test_tab_folds_the_index_again(self):
        desk = FakeDesk((_paper("One", "Two", "Three"), False))
        live = _run("tab", "tab", "esc", desk=desk)
        assert "▾" in _plain(live.frames[-1])

    def test_the_clock_stops_while_the_index_is_open(self, monkeypatch):
        import time as _time

        clock = [0.0]

        def _tick():
            clock[0] += 5.0
            return clock[0]

        monkeypatch.setattr(_time, "monotonic", _tick)
        desk = FakeDesk((_paper("One", "Two", "Three"), False))
        live = _run("tab", "esc", "esc", "esc", "esc", desk=desk)
        assert "‹ 1 of 3 ›" in _plain(live.frames[-1]) or "Inside this edition  ▴" in _plain(live.frames[-1])

    def test_r_asks_for_a_fresh_paper_only_when_news_is_on(self):
        class Recording(FakeDesk):
            refreshes = 0

            def get_paper(self, *, refresh=False):
                self.refreshes += refresh
                return super().get_paper(refresh=refresh)

        desk = Recording()
        _run("r", "q", desk=desk)
        assert desk.refreshes == 1
        off = FakeDesk((_paper("Note"), False), enabled=False)
        _run("r", "q", desk=off)
        assert off.asked == 1

    @pytest.mark.parametrize("key", ["enter", "o", "tab", "right"])
    def test_an_empty_edition_takes_any_key(self, key):
        live = _run(key, "q", desk=FakeDesk((Paper(stale=True), True)))
        assert "Nothing to read yet." in _plain(live.frames[-1])
