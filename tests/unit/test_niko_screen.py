"""Render tests for the Niko page (ui/mode_select/screens/_screens_niko.py).

The builder runs every frame, so what matters is that it never raises and never
overflows its declared height — a page that grows by a row pushes the buttons
off, which is exactly how the eleventh mode card broke three other screens.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console
from rich.panel import Panel

from yeaboi.ui.mode_select.screens._screens_niko import (
    NIKO_ACTIONS,
    _build_niko_screen,
    transcript_rows,
    wrap_rows,
)
from yeaboi.ui.session.chat._composer import ChatComposer

TURNS = [
    {"role": "user", "text": "what did my agents cost?", "tools": []},
    {
        "role": "assistant",
        "text": "About $41 this month, mostly Sonnet.",
        "tools": [{"name": "agents_usage_history", "ok": True}],
        "route": "/agents/usage",
    },
]


def state(**overrides) -> dict:
    base = {"composer": ChatComposer(), "turns": [], "chips": [], "busy": False}
    return {**base, **overrides}


def render(screen_state, width=100, height=36) -> str:
    panel = _build_niko_screen(screen_state, width=width, height=height, shimmer_tick=0.0)
    console = Console(file=io.StringIO(), width=width, height=height + 5, legacy_windows=False)
    console.print(panel)
    return console.file.getvalue()


class TestStructure:
    def test_it_returns_a_page_panel_not_a_raw_one(self):
        panel = _build_niko_screen(state(), width=100, height=36)
        assert isinstance(panel, Panel)
        # Every page paints its own background; a raw Panel would show the
        # user's terminal colour through (test_screen_backgrounds guards this).
        assert panel.style and str(panel.style) != "none"

    @pytest.mark.parametrize("height", [20, 24, 30, 36, 44, 60])
    def test_it_never_exceeds_its_declared_height(self, height):
        out = render(state(turns=TURNS), height=height)
        assert len(out.splitlines()) == height

    @pytest.mark.parametrize("width", [60, 80, 100, 140, 200])
    def test_it_renders_at_every_sane_width(self, width):
        assert render(state(turns=TURNS), width=width)

    def test_the_buttons_are_on_the_page(self):
        out = render(state())
        for label in NIKO_ACTIONS:
            assert label in out


class TestEmptyState:
    def test_it_introduces_niko_and_its_limits(self):
        out = render(state())
        assert "I'm Niko" in out
        assert "can't change anything" in out

    def test_chips_are_offered_when_there_are_any(self):
        out = render(state(chips=[{"label": "What did my agents cost?"}]))
        assert "TRY ASKING" in out
        assert "What did my agents cost?" in out

    def test_no_chips_is_a_shorter_screen_not_a_broken_one(self):
        assert "TRY ASKING" not in render(state(chips=[]))


class TestTranscript:
    def test_both_speakers_are_named(self):
        out = render(state(turns=TURNS))
        assert "You" in out and "Niko" in out

    def test_a_tool_call_shows_what_was_read(self):
        assert "read agents_usage_history" in render(state(turns=TURNS))

    def test_a_failed_tool_says_so_without_the_raw_error(self):
        turns = [{"role": "assistant", "text": "", "tools": [{"name": "ship_status", "ok": False}]}]
        out = render(state(turns=turns))
        assert "nothing to read" in out
        assert "ship_status" in out

    def test_a_navigation_suggestion_is_shown(self):
        assert "/agents/usage" in render(state(turns=TURNS))

    def test_a_turn_with_no_text_still_renders(self):
        assert render(state(turns=[{"role": "assistant", "text": "", "tools": []}]))

    def test_long_prose_wraps_rather_than_truncating(self):
        long = " ".join(["word"] * 200)
        out = render(state(turns=[{"role": "assistant", "text": long, "tools": []}]))
        assert out.count("word") > 20

    def test_rows_are_produced_for_every_turn(self):
        rows = transcript_rows(TURNS, 80)
        assert any("You" in r.plain for r in rows)
        assert any("Niko" in r.plain for r in rows)


class TestComposer:
    def test_the_placeholder_shows_on_an_empty_box(self):
        assert "Ask Niko anything" in render(state())

    def test_typed_text_shows_instead_of_the_placeholder(self):
        composer = ChatComposer()
        composer.set_text("what should I do next?")
        out = render(state(composer=composer))
        assert "what should I do next?" in out
        assert "Ask Niko anything" not in out

    def test_a_running_turn_says_so_rather_than_offering_the_box(self):
        out = render(state(busy=True, streaming="thinking about it"))
        assert "Thinking…" in out

    def test_the_partial_answer_streams_into_the_transcript(self):
        out = render(state(busy=True, streaming="About forty dollars"))
        assert "About forty dollars" in out

    def test_read_only_draws_no_input_box(self):
        out = render(state(turns=TURNS, read_only=True, actions=["Back"]))
        assert "Ask Niko anything" not in out
        assert "Back" in out
        assert "New" not in out


class TestScrolling:
    def test_a_long_conversation_scrolls_rather_than_overflowing(self):
        turns = [{"role": "user", "text": f"question {i}", "tools": []} for i in range(60)]
        panel = _build_niko_screen(state(turns=turns), scroll_offset=0, width=100, height=30)
        console = Console(file=io.StringIO(), width=100, height=35, legacy_windows=False)
        console.print(panel)
        assert len(console.file.getvalue().splitlines()) == 30

    def test_scrolling_past_the_end_clamps(self):
        # The page opens at the newest turn by asking for a huge offset.
        turns = [{"role": "user", "text": f"question {i}", "tools": []} for i in range(60)]
        out = render(state(turns=turns), height=30)
        last = render(state(turns=turns), height=30)
        assert out == last
        panel = _build_niko_screen(state(turns=turns), scroll_offset=10**6, width=100, height=30)
        console = Console(file=io.StringIO(), width=100, height=35, legacy_windows=False)
        console.print(panel)
        assert "question 59" in console.file.getvalue()

    def test_a_negative_offset_clamps_to_the_top(self):
        panel = _build_niko_screen(state(turns=TURNS), scroll_offset=-50, width=100, height=30)
        assert isinstance(panel, Panel)


class TestWrapRows:
    def test_it_keeps_the_texts_own_line_breaks(self):
        assert wrap_rows("a\nb", 40) == ["a", "b"]

    def test_an_empty_string_is_one_empty_row(self):
        assert wrap_rows("", 40) == [""]

    def test_a_silly_width_does_not_hang(self):
        assert wrap_rows("hello world", 0)
