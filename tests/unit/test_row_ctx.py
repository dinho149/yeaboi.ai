"""The shared row accumulator and the height-aware viewport packer.

These two hold the invariant every sectioned page depends on: scroll offsets
index items, ``item_heights`` maps them to terminal rows, and the visible window
fills the viewport exactly. When they drift, a scrollbar thumb lies and a
report's tail becomes unreachable — silently, in both cases.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.shared._components import PERFORMANCE_THEME
from yeaboi.ui.shared._row_ctx import RowCtx, max_scroll_for, pack_viewport


def _rendered_height(renderable, width: int) -> int:
    console = Console(width=width, file=io.StringIO(), legacy_windows=False)
    return len(console.render_lines(renderable, pad=False))


class TestRowCtx:
    def test_plain_rows_are_one_item_one_row(self):
        ctx = RowCtx(PERFORMANCE_THEME, 80)
        ctx.line("hello")
        ctx.blank()
        assert ctx.item_heights == [1, 1]
        assert len(ctx.lines) == 2

    def test_heading_emits_a_blank_a_title_and_a_rule(self):
        ctx = RowCtx(PERFORMANCE_THEME, 80)
        ctx.heading("Talking points")
        assert ctx.item_heights == [1, 1, 1]
        assert "─" in ctx.lines[2].plain

    def test_wrapped_prose_becomes_one_item_per_terminal_row(self):
        ctx = RowCtx(PERFORMANCE_THEME, 60)
        ctx.wrapped("word " * 60, PERFORMANCE_THEME.value)
        assert len(ctx.lines) > 1
        assert ctx.item_heights == [1] * len(ctx.lines)

    def test_wrapped_preserves_the_authors_own_paragraph_breaks(self):
        ctx = RowCtx(PERFORMANCE_THEME, 80)
        ctx.wrapped("first\nsecond", PERFORMANCE_THEME.value, preserve_newlines=True)
        assert [ln.plain.strip() for ln in ctx.lines] == ["first", "second"]

    def test_wrapped_row_never_exceeds_one_terminal_row(self):
        # The -7 chrome budget is the whole point: one char over and Rich wraps
        # the row, which silently eats a viewport row the packer already spent.
        for width in range(50, 121, 7):
            ctx = RowCtx(PERFORMANCE_THEME, width)
            ctx.wrapped("supercalifragilistic " * 20, PERFORMANCE_THEME.value)
            for line in ctx.lines:
                assert _rendered_height(line, width - 7) == 1, width

    def test_add_renderable_records_its_measured_height(self):
        ctx = RowCtx(PERFORMANCE_THEME, 80)
        panel = Panel(Text("a\nb\nc"))
        ctx.add_renderable(panel)
        assert ctx.item_heights == [_rendered_height(ctx.lines[0], 80 - 7)]
        assert ctx.item_heights[0] >= 5  # three content rows plus two borders


class TestMaxScrollFor:
    def test_no_items_cannot_scroll(self):
        assert max_scroll_for([], 10) == 0

    def test_content_that_fits_cannot_scroll(self):
        assert max_scroll_for([1, 1, 1], 10) == 0

    def test_stops_where_the_tail_still_fills_the_viewport(self):
        # 10 one-row items in a 4-row viewport: offset 6 shows items 6..9.
        assert max_scroll_for([1] * 10, 4) == 6

    def test_a_tall_item_costs_its_real_rows(self):
        # The 2-row item at index 2 costs two of the four rows, so the tail run
        # reaches index 1 rather than index 0.
        assert max_scroll_for([1, 1, 2, 1], 4) == 1

    def test_an_item_too_tall_to_share_the_viewport_ends_the_tail_run(self):
        # Nothing can be shown above the 5-row item in a 4-row viewport, so the
        # last offset that shows a full tail is the item after it.
        assert max_scroll_for([1, 1, 5, 1], 4) == 3


class TestPackViewport:
    def test_fills_the_viewport_exactly(self):
        items = [Text(str(i)) for i in range(10)]
        visible, visible_h = pack_viewport(items, [1] * 10, 3, 4)
        assert [t.plain for t in visible] == ["3", "4", "5", "6"]
        assert visible_h == 4

    def test_stops_before_an_item_that_would_overflow(self):
        items = [Text("a"), Text("b"), Text("c")]
        visible, visible_h = pack_viewport(items, [1, 5, 1], 0, 4)
        assert [t.plain for t in visible] == ["a"]
        assert visible_h == 1

    def test_an_oversized_first_item_is_shown_and_clipped(self):
        # Dropping it would render an empty viewport with no key to escape from.
        items = [Text("tall"), Text("b")]
        visible, visible_h = pack_viewport(items, [99, 1], 0, 4)
        assert [t.plain for t in visible] == ["tall"]
        assert visible_h == 4

    def test_offset_past_the_end_yields_nothing(self):
        assert pack_viewport([Text("a")], [1], 5, 4) == ([], 0)

    @pytest.mark.parametrize("viewport_h", [3, 5, 8, 13])
    def test_the_tail_is_always_reachable(self, viewport_h):
        heights = [1, 1, 3, 1, 1, 2, 1, 1, 1, 4, 1, 1]
        items = [Text(str(i)) for i in range(len(heights))]
        visible, _ = pack_viewport(items, heights, max_scroll_for(heights, viewport_h), viewport_h)
        assert visible[-1].plain == str(len(heights) - 1)
