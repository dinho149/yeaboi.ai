"""Tests for the Humans/Agents landing split (_screens_category.py)."""

import pytest
from rich.console import Console

from yeaboi.ui.mode_select.screens._screens_category import (
    _CARD_ROWS,
    _CATEGORY_CARDS,
    _MASCOT_ROWS,
    _MASCOT_WALK_STEPS,
    _build_category_screen,
    _card_half,
    category_at_pos,
)


def _render_chromed(width=110, height=40, selected=0, **kwargs) -> str:
    """The page as the app shows it — through the chrome that draws the footer.

    The question sits ON the bottom border now, which is the chrome's layer, so
    a raw panel render cannot see it.
    """
    import yeaboi.ui.shared._music_bar as music_bar

    panel = _build_category_screen(selected, width=width, height=height, **kwargs)
    frame = music_bar._MusicPocketFrame(panel, with_duck=False, with_back=False)
    frame.with_music = not getattr(panel, "_no_music", False)
    frame.footer_note = str(getattr(panel, "_footer_note", "") or "")
    console = Console(width=width, height=height, force_terminal=False)
    rows = console.render_lines(frame, console.options.update(height=height), pad=True)
    return "\n".join("".join(seg.text for seg in row) for row in rows)


@pytest.fixture(autouse=True)
def _reset_walk():
    """Both mascots start where they belong.

    How far each has walked is module state, so without this one test's paces
    decide where the next one's duck is standing.
    """
    from yeaboi.ui.mode_select.screens._screens_category import reset_category_walk

    reset_category_walk()
    yield
    reset_category_walk()


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
        assert "34;158;122" in out  # duck green, at the front, in his own colour

    def test_the_resting_mascot_is_in_shadow(self):
        # Depth is light as well as size: at the back of the shot his colours
        # are mixed toward the page, so the steel that reads at the front is
        # not the steel that reads at the back.
        from yeaboi.ui.mode_select.screens._screens_category import _MASCOT_SHADE, _SHADE_TOWARD

        steel = (140, 160, 178)
        shaded = ",".join(
            str(round(c * _MASCOT_SHADE + t * (1.0 - _MASCOT_SHADE))) for c, t in zip(steel, _SHADE_TOWARD, strict=True)
        )
        resting = _render(selected=0)  # humans live, so the robo is at the back
        assert "140;160;178" not in resting
        assert shaded.replace(",", ";") in resting, shaded

    def test_exact_height(self):
        out = _render(width=100, height=30)
        assert out.count("\n") == 30

    def test_selected_half_carries_full_accent(self):
        left = _render(selected=0)
        right = _render(selected=1)
        assert left != right

        def _rgb(css):
            return css.removeprefix("rgb(").removesuffix(")").replace(",", ";")

        # No frame and no wash any more: the live card is marked by its wordmark
        # burning bright and by its mascot having walked to the front.
        humans, agents = (_rgb(c["bright"]) for c in _CATEGORY_CARDS)
        assert agents in right and agents not in left
        assert humans in left and humans not in right
        for tint in ("15;24;32", "17;28;20"):
            assert tint not in left and tint not in right, tint

    def test_the_resting_mascot_stands_further_back(self):
        # Depth, not just dimming: the resting duck is the smaller trace and
        # sits higher in the card, the way distance puts a thing further up the
        # ground plane. The card must not change height for it.
        from yeaboi.ui.mode_select.screens._screens_category import (
            _CATEGORY_CARDS,
            _MASCOT_BACK_ABOVE,
            _MASCOT_MINI_ROWS,
            _MASCOT_ROWS,
            _card_half,
        )

        def _rows(selected):
            from yeaboi.ui.mode_select.screens._screens_category import reset_category_walk

            reset_category_walk()  # a fresh card stands where it belongs
            console = Console(width=60, force_terminal=False)
            with console.capture() as cap:
                console.print(_card_half(_CATEGORY_CARDS[0], selected=selected, shimmer_tick=0.0, intro=1.0))
            return cap.get().split("\n")

        near, far = _rows(True), _rows(False)
        assert len(near) == len(far), "the card changed height with the selection"

        blocks = "▀▄█"

        def _sprite_rows(rows):
            # Only the rows the mascot draws on: the title under him is block
            # art too, so it is excluded by row, not by glyph.
            return [r for r in rows[: _MASCOT_ROWS + 2] if any(ch in blocks for ch in r)]

        def _widest(rows):
            return max((len(r.strip()) for r in _sprite_rows(rows)), default=0)

        def _first_ink(rows):
            return next(i for i, r in enumerate(rows) if any(ch in blocks for ch in r))

        assert _widest(far) < _widest(near), "the resting duck is not smaller"
        assert _first_ink(far) > _first_ink(near), "the resting duck is not further up"
        assert _MASCOT_BACK_ABOVE + _MASCOT_MINI_ROWS < _MASCOT_ROWS

    def test_the_mascot_walks_forward_rather_than_appearing(self):
        # Three paces down an inferred ground, then the arrival: the swap to
        # the full trace. A duck that grew on the spot would read as a zoom.
        from yeaboi.ui.mode_select.screens._screens_category import (
            _CATEGORY_CARDS,
            _card_half,
            reset_category_walk,
        )

        blocks = "▀▄█"

        def _shot(selected):
            console = Console(width=60, force_terminal=False)
            with console.capture() as cap:
                console.print(_card_half(_CATEGORY_CARDS[0], selected=selected, shimmer_tick=0.0, intro=1.0))
            rows = cap.get().split("\n")
            sprite = [r for r in rows[: _MASCOT_ROWS + 2] if any(c in blocks for c in r)]
            top = next(i for i, r in enumerate(rows) if any(c in blocks for c in r))
            return top, max((len(r.strip()) for r in sprite), default=0)

        reset_category_walk()
        _shot(False)  # standing at the back
        walk = [_shot(True) for _ in range(_MASCOT_WALK_STEPS + 3)]

        widths = [w for _t, w in walk]
        far = widths[0]
        # The paces he takes at the back, before the arrival swaps the trace.
        paces = [t for t, w in walk if w == far]
        # Walking is an arc, not a slide: he leaves the ground mid-stride and
        # plants between paces, by one row.
        assert len(set(paces)) == 2, paces
        assert max(paces) - min(paces) == 1, paces
        assert far < widths[-1], widths  # and arrives at full size
        assert widths[-1] == _shot(True)[1]  # then stays there

    def test_selected_card_is_alive_resting_card_is_still(self):
        # The signature: the selected mascot's wing flaps on the clock; the
        # resting card holds frame 0 regardless of the clock.
        import re

        def _settled(**kw):
            # The mascots take a few frames to finish walking; only once they
            # have stopped is a difference between frames the WING moving.
            for _ in range(_MASCOT_WALK_STEPS + 2):
                out = _render(**kw)
            return out

        early = _settled(selected=1, shimmer_tick=0.0)
        late = _render(selected=1, shimmer_tick=0.25)
        assert early != late  # the robo flapped

        def right_half(styled):
            plain = re.sub(r"\x1b\[[0-9;]*m", "", styled)
            return "\n".join(row[len(row) // 2 :] for row in plain.splitlines())

        # Humans selected → the agents half keeps its own, slower clock: it has
        # an idle hop, but it does not flap frame for frame the way the live one
        # does. Ticks a quarter apart land in the same beat of it.
        a = _settled(selected=0, shimmer_tick=0.0)
        b = _render(selected=0, shimmer_tick=0.25)
        assert right_half(a) == right_half(b)

    def test_the_resting_mascot_has_a_slow_idle(self):
        # Alive back there, but not competing with the card you are on: one row
        # up for a beat, every few beats.
        from yeaboi.ui.mode_select.screens._screens_category import (
            _CATEGORY_CARDS,
            _MASCOT_HOP_CYCLE,
            _MASCOT_HOP_HZ,
            _card_half,
            reset_category_walk,
        )

        blocks = "▀▄█"

        def _top(tick):
            reset_category_walk()
            console = Console(width=60, force_terminal=False)
            with console.capture() as cap:
                console.print(_card_half(_CATEGORY_CARDS[0], selected=False, shimmer_tick=tick, intro=1.0))
            rows = cap.get().split("\n")
            return next(i for i, r in enumerate(rows) if any(c in blocks for c in r))

        beat = 1.0 / _MASCOT_HOP_HZ
        tops = [_top(i * beat) for i in range(_MASCOT_HOP_CYCLE * 2)]
        assert len(set(tops)) == 2, tops  # he hops, and only by one row
        assert max(tops) - min(tops) == 1, tops
        # And he is down far more than he is up — a hop, not a hover.
        assert tops.count(min(tops)) < tops.count(max(tops)), tops

    def test_the_cards_have_no_frames(self):
        # Two boxes side by side made the choice look like a form, and their
        # eyebrows named what the wordmark under them already says.

        plain = _render_chromed()
        # An eyebrow rides a rule, so no rule may carry a card's name. ("agents"
        # on its own still appears — in "Watch your AI agents work".)
        for line in plain.split("\n"):
            if "─" in line:
                assert "humans" not in line and "agents" not in line, line
        # And the cards themselves draw none. Counted on the CARD, not on the
        # page: the page's frame count depends on what else the chrome has been
        # asked to draw (an update box, a drawer), which is not this test's
        # business and made it depend on which tests ran before it.
        from yeaboi.ui.mode_select.screens._screens_category import _CATEGORY_CARDS, _card_half

        for card in _CATEGORY_CARDS:
            for selected in (True, False):
                console = Console(width=60, force_terminal=False)
                with console.capture() as cap:
                    console.print(_card_half(card, selected=selected, shimmer_tick=0.0, intro=1.0))
                assert "╭" not in cap.get(), (card["key"], selected)

    def test_headline_and_hints(self):
        out = _render_chromed()
        # The question sits ON the bottom border, in the chrome's own frame.
        assert "working with today?" in out
        assert "choose" in out and "switch" in out and "quit" in out

    def test_the_ducks_arrive_before_their_names(self):
        # It was the other way round: the wordmark landed on an empty card and
        # the mascot appeared under a name already there, which reads as the
        # art being late.
        import re

        early = re.sub(r"\x1b\[[0-9;]*m", "", _render(intro=0.0))
        assert "34;158;122" in _render(intro=0.0)  # the duck is here
        assert "HUMANS" not in early.replace(" ", "")  # his name is not
        assert "working with today?" in _render_chromed(intro=0.0)  # the question is
        # Both sets of rows are reserved, so the card height never jumps.
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


class TestSeedFrame:
    """The frame Rich paints when select_mode's Live starts.

    Live refreshes its seed renderable on entry, before the loop body runs, so a
    seed that is not the first screen shown gets one visible frame of its own.
    Seeding the mode menu here put its version/hints row and music pocket over
    the tail of the splash — the flicker at the splash → landing-split boundary.
    """

    def _seed(self, category="humans", width=110, height=40):
        from yeaboi.ui.mode_select import _landing_first_frame

        return _landing_first_frame(category, width=width, height=height)

    def test_it_is_the_landing_split(self):
        console = Console(width=110, height=40, force_terminal=False)
        rows = console.render_lines(self._seed(), console.options.update(height=40), pad=True)
        text = "\n".join("".join(seg.text for seg in row) for row in rows)
        assert "switch" in text and "choose" in text  # the split's own hint row

    def test_it_is_not_the_mode_menu(self):
        # The row that flashed. If the seed ever goes back to _build_mode_screen
        # this is what reappears, so name it rather than assert a shape.
        # _MIN_HEIGHT is the screen's own minimum — the menu's hint row is the
        # first thing to fall off below it, and this test's premise is that the
        # row is there.
        from yeaboi.ui.mode_select.screens._screens import _MIN_HEIGHT, _build_mode_screen

        console = Console(width=185, height=_MIN_HEIGHT, force_terminal=False)

        def _flat(renderable):
            rows = console.render_lines(renderable, console.options.update(height=_MIN_HEIGHT), pad=True)
            return "\n".join("".join(seg.text for seg in row) for row in rows)

        menu = _flat(
            _build_mode_screen(
                0,
                width=185,
                height=_MIN_HEIGHT,
                shimmer_tick=0.0,
                desc_reveal=0,
                sweep_front=0.0,
                companion_intro=0.0,
            )
        )
        assert "changelog" in menu, "premise: the menu's hint row is what used to flash"
        assert "changelog" not in _flat(self._seed(width=185, height=_MIN_HEIGHT))

    def test_it_opens_on_the_remembered_category(self):
        from yeaboi.ui.mode_select.screens._screens_category import category_index

        assert category_index("humans") == 0
        assert category_index("agents") == 1
        # An unknown key must not raise — a hand-edited config lands on Humans.
        assert category_index("nonsense") == 0
