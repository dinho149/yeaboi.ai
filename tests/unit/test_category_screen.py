"""Tests for the Humans/Agents landing split (_screens_category.py)."""

from rich.console import Console

from yeaboi.ui.mode_select.screens._screens_category import (
    _CATEGORY_CARDS,
    _build_category_screen,
    category_at_pos,
)


def _render(width=110, height=32, selected=0, **kwargs) -> str:
    console = Console(width=width, height=height, force_terminal=True)
    with console.capture() as cap:
        console.print(_build_category_screen(selected, width=width, height=height, **kwargs))
    return cap.get()


class TestCards:
    def test_two_categories_in_order(self):
        assert [c["key"] for c in _CATEGORY_CARDS] == ["humans", "agents"]

    def test_core_keys_present(self):
        for card in _CATEGORY_CARDS:
            assert {"key", "title", "description", "color", "mascot"} <= set(card)


class TestRender:
    def test_both_mascots_render(self):
        out = _render()
        assert "34;158;122" in out  # duck green (left half)
        assert "140;160;178" in out  # robo steel (right half)

    def test_exact_height(self):
        out = _render(width=100, height=30)
        assert out.count("\n") == 30

    def test_selected_half_carries_full_accent(self):
        left = _render(selected=0)
        right = _render(selected=1)
        assert left != right
        # The agents accent at full strength appears only when agents is selected.
        assert "90;160;210" in right

    def test_headline_and_hints(self):
        out = _render()
        # The heading is letter-spaced ("W h o   a r e …") between two accent
        # rules — assert the spaced words and the flanking rule glyphs.
        assert "w o r k i n g   w i t h" in out
        assert "─────" in out
        assert "choose" in out and "switch" in out and "quit" in out

    def test_intro_hides_heads_early(self):
        early = _render(intro=0.0)
        assert "34;158;122" not in early  # no duck yet
        assert "w o r k i n g   w i t h" in early  # copy is already there

    def test_small_terminal_still_renders(self):
        out = _render(width=84, height=24)
        assert out.count("\n") == 24


class TestHitTest:
    def test_left_half_is_humans(self):
        assert category_at_pos(100, 30, row=15, col=10) == 0

    def test_right_half_is_agents(self):
        assert category_at_pos(100, 30, row=15, col=90) == 1

    def test_midpoint_boundary(self):
        assert category_at_pos(100, 30, row=15, col=50) == 0
        assert category_at_pos(100, 30, row=15, col=51) == 1

    def test_border_and_hint_rows_are_dead(self):
        assert category_at_pos(100, 30, row=1, col=50) is None
        assert category_at_pos(100, 30, row=30, col=50) is None
