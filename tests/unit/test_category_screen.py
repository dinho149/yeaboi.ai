"""Tests for the Humans/Agents landing split (_screens_category.py)."""

from rich.console import Console

from yeaboi.ui.mode_select.screens._screens_category import (
    _CARD_ROWS,
    _CATEGORY_CARDS,
    _build_category_screen,
    _card_half,
    category_at_pos,
)


# 40 rows is the app's own floor (_MIN_HEIGHT); below it the caller shows the
# too-small guard screen instead, so the category screen never renders shorter.
def _render(width=110, height=40, selected=0, **kwargs) -> str:
    # Pinned truecolor (see TestTruecolorConsoles in test_screen_backgrounds):
    # auto-detection reads COLORTERM, which dev shells set and CI does not.
    console = Console(width=width, height=height, force_terminal=True, color_system="truecolor")
    with console.capture() as cap:
        console.print(_build_category_screen(selected, width=width, height=height, **kwargs))
    return cap.get()


class TestCards:
    def test_two_categories_in_order(self):
        assert [c["key"] for c in _CATEGORY_CARDS] == ["humans", "agents"]

    def test_core_keys_present(self):
        for card in _CATEGORY_CARDS:
            assert {"key", "title", "verb", "capabilities", "color", "bright", "dim", "tint", "mascot"} <= set(card)


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
        # The agents accent border + card tint appear only when agents is selected.
        assert "90;160;210" in right
        assert "15;24;32" in right  # agents card tint
        assert "15;24;32" not in left
        assert "17;28;20" in left  # humans card tint

    def test_selected_card_is_alive_resting_card_is_still(self):
        # The signature: the selected mascot's wing flaps on the clock; the
        # resting card holds frame 0 regardless of the clock.
        import re

        early = _render(selected=1, shimmer_tick=0.0)
        late = _render(selected=1, shimmer_tick=0.25)
        assert early != late  # the robo flapped

        def right_half(styled):
            plain = re.sub(r"\x1b\[[0-9;]*m", "", styled)
            return "\n".join(row[len(row) // 2 :] for row in plain.splitlines())

        # Humans selected → the agents half must not move between frames.
        a = _render(selected=0, shimmer_tick=0.0)
        b = _render(selected=0, shimmer_tick=0.25)
        assert right_half(a) == right_half(b)

    def test_card_eyebrows_in_borders(self):
        import re

        plain = re.sub(r"\x1b\[[0-9;]*m", "", _render())
        assert " humans " in plain and " agents " in plain

    def test_headline_and_hints(self):
        out = _render()
        # The question rides the page frame's border title now.
        assert "working with today?" in out
        assert "choose" in out and "switch" in out and "quit" in out

    def test_intro_hides_heads_early(self):
        early = _render(intro=0.0)
        assert "34;158;122" not in early  # no duck yet
        assert "working with today?" in early  # the frame's question is already there
        # The mascot rows are reserved, so the card height never jumps.
        assert early.count("\n") == 40

    def test_card_height_matches_the_layout_constant(self):
        # The card is a plain inner Panel (no height= — that is what the
        # full-screen-background guard flags), so its height comes from the
        # body. _CARD_ROWS drives the page's vertical centring, and the two
        # would drift silently if the mascot or the block font changed rows.
        console = Console(width=52, height=60, force_terminal=False)
        for card in _CATEGORY_CARDS:
            with console.capture() as cap:
                console.print(_card_half(card, selected=True, shimmer_tick=0.0, intro=1.0))
            assert cap.get().count("\n") == _CARD_ROWS

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


class TestChromeOptOuts:
    def test_no_corner_companion_on_the_landing_split(self):
        # The screen shows both mascots already; the chrome's corner duck would
        # be a third. The stamp is read by MusicLive.get_renderable.
        panel = _build_category_screen(0, width=110, height=32)
        assert getattr(panel, "_no_companion_duck", False) is True
        assert getattr(panel, "_no_back_hint", False) is True
