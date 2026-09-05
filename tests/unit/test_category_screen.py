"""Tests for the Solo/Team/Agents landing split (_screens_category.py)."""

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
    def test_three_categories_in_order(self):
        assert [c["key"] for c in _CATEGORY_CARDS] == ["solo", "team", "agents"]

    def test_core_keys_present(self):
        for card in _CATEGORY_CARDS:
            assert {"key", "title", "verb", "capabilities", "color", "bright", "dim", "tint", "mascot"} <= set(card)

    def test_solo_and_agents_are_the_beta_worlds(self):
        from yeaboi.beta import BETA_LABEL

        badged = {card["key"] for card in _CATEGORY_CARDS if card.get("badge") == BETA_LABEL}
        assert badged == {"solo", "agents"}


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
        resting = _render(selected=0)  # solo live, so the robo is at the back
        assert "140;160;178" not in resting
        assert shaded.replace(",", ";") in resting, shaded

    def test_exact_height(self):
        out = _render(width=100, height=30)
        assert out.count("\n") == 30

    def test_selected_card_carries_full_accent(self):
        def _rgb(css):
            return css.removeprefix("rgb(").removesuffix(")").replace(",", ";")

        # No frame and no wash any more: the live card is marked by its wordmark
        # burning bright and by its mascot having walked to the front. Each
        # card's bright appears only in the render where that card is selected.
        renders = [_render(selected=i) for i in range(len(_CATEGORY_CARDS))]
        brights = [_rgb(c["bright"]) for c in _CATEGORY_CARDS]
        for i, bright in enumerate(brights):
            for j, out in enumerate(renders):
                assert (bright in out) == (i == j), (i, j)
        for out in renders:
            for tint in ("15;24;32", "17;28;20", "30;25;15"):
                assert tint not in out, tint

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
            return "\n".join(row[2 * len(row) // 3 :] for row in plain.splitlines())

        # Solo selected → the agents third keeps its own, slower clock: it has
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
                assert "solo" not in line and "team" not in line and "agents" not in line, line
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

    def test_beta_worlds_wear_the_chip(self):
        import re

        plain = re.sub(r"\x1b\[[0-9;]*m", "", _render(width=110))
        chip_rows = [line for line in plain.splitlines() if "BETA" in line]
        assert chip_rows, "no BETA chip rendered"
        # Two chips on one row — solo's and agents'; the team card carries none
        # (its column between them stays blank).
        assert chip_rows[0].count("BETA") == 2

    def test_the_chip_enters_with_the_wordmark(self):
        import re

        early = re.sub(r"\x1b\[[0-9;]*m", "", _render(width=110, intro=0.0))
        assert "BETA" not in early

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
        assert "SOLO" not in early.replace(" ", "")  # his name is not
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
    def test_left_third_is_solo(self):
        assert category_at_pos(100, 30, row=15, col=10) == 0

    def test_middle_third_is_team(self):
        assert category_at_pos(100, 30, row=15, col=50) == 1

    def test_right_third_is_agents(self):
        assert category_at_pos(100, 30, row=15, col=90) == 2

    def test_region_boundaries_match_the_shared_bounds(self):
        # The hit test and the builder read the same _category_bounds, so the
        # boundary sits exactly one gutter past each card's last column.
        from yeaboi.ui.mode_select.screens._screens_category import _GUTTER_COLS, _category_bounds

        bounds = _category_bounds(100)
        for i, (_start, end) in enumerate(bounds[:-1]):
            assert category_at_pos(100, 30, row=15, col=end + _GUTTER_COLS) == i
            assert category_at_pos(100, 30, row=15, col=end + _GUTTER_COLS + 1) == i + 1

    def test_the_far_edges_count_for_the_outer_cards(self):
        assert category_at_pos(100, 30, row=15, col=1) == 0
        assert category_at_pos(100, 30, row=15, col=100) == 2

    def test_border_and_hint_rows_are_dead(self):
        assert category_at_pos(100, 30, row=1, col=50) is None
        assert category_at_pos(100, 30, row=30, col=50) is None

    def test_the_blank_rows_under_the_cards_still_count_when_there_is_no_informer(self):
        assert category_at_pos(110, 40, row=36, col=50) == 1

    def test_the_informer_band_is_dead_for_cards_and_live_for_the_paper(self):
        from yeaboi.ui.mode_select.screens._screens_category import informer_hit

        assert category_at_pos(110, 53, row=30, col=50) == 1  # the cards span rows 5–32
        assert category_at_pos(110, 53, row=33, col=50) is None
        assert informer_hit(110, 53, row=45, col=100)
        assert not informer_hit(110, 53, row=45, col=40)  # the blank left of the lane
        assert not informer_hit(110, 53, row=30, col=100)  # a card
        assert not informer_hit(110, 53, row=50, col=100)  # the hint row
        assert not informer_hit(110, 40, row=36, col=100)  # no informer at the floor


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

    def _seed(self, category="team", width=110, height=40):
        from yeaboi.ui.mode_select import _landing_first_frame

        return _landing_first_frame(category, width=width, height=height)

    def test_it_is_the_landing_split(self):
        console = Console(width=110, height=40, force_terminal=False)
        rows = console.render_lines(self._seed(), console.options.update(height=40), pad=True)
        text = "\n".join("".join(seg.text for seg in row) for row in rows)
        assert "switch" in text and "choose" in text  # the split's own hint row

    def test_it_is_not_the_door(self):
        # The door comes AFTER the split; seeding it would flash its heading
        # over the tail of the splash for a frame.
        console = Console(width=110, height=40, force_terminal=False)
        rows = console.render_lines(self._seed(), console.options.update(height=40), pad=True)
        text = "\n".join("".join(seg.text for seg in row) for row in rows)
        assert "work today" not in text and "working with" in text

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

        assert category_index("solo") == 0
        assert category_index("team") == 1
        assert category_index("agents") == 2
        # An unknown key must not raise — a hand-edited config lands on Solo.
        # (get_last_category sanitises before the key gets here.)
        assert category_index("nonsense") == 0


class TestFlock:
    """The Team card's trio — composed sprites, same footprint as the solo hero."""

    def test_the_flock_shares_the_full_body_footprint(self):
        from yeaboi.ui.shared._mascot import flock_cells, full_cells

        flock = flock_cells(0)
        full = full_cells(0)
        assert len(flock[0]) == len(full[0]) == 34
        assert len(flock) < len(full)  # minis are shorter; the card pads above

    def test_the_head_trio_shares_the_mini_footprint(self):
        from yeaboi.ui.shared._mascot import flock_head_cells, mini_cells

        heads = flock_head_cells(0)
        mini = mini_cells(0)
        assert len(heads[0]) == len(mini[0]) == 22

    def test_the_team_card_arrives_as_a_trio(self):
        # Wider ink than one mini duck, and different ink from the solo hero at
        # the same width — the trio is what marks the Team world.
        blocks = "▀▄█"

        def _sprite(card):
            from yeaboi.ui.mode_select.screens._screens_category import reset_category_walk

            reset_category_walk()
            console = Console(width=60, force_terminal=False)
            with console.capture() as cap:
                console.print(_card_half(card, selected=True, shimmer_tick=0.0, intro=1.0))
            rows = cap.get().split("\n")
            return [r for r in rows[: _MASCOT_ROWS + 2] if any(ch in blocks for ch in r)]

        solo = _sprite(_CATEGORY_CARDS[0])
        team = _sprite(_CATEGORY_CARDS[1])
        assert solo != team

    def test_the_front_head_quacks(self):
        from yeaboi.ui.shared._mascot import flock_head_cells

        assert flock_head_cells(0) != flock_head_cells(2)


class TestTipJumpTarget:
    """The cross-category jump rule: shared keys land on Team, never Solo."""

    def test_a_retro_tip_from_solo_lands_on_team(self):
        from yeaboi.ui.mode_select import _MODE_CARDS, _SOLO_CARDS, _tip_jump_target

        cat, j = _tip_jump_target("retro", _SOLO_CARDS)
        assert cat == "team"
        assert _MODE_CARDS[j]["key"] == "retro"

    def test_the_solo_only_review_key_lands_on_solo(self):
        from yeaboi.ui.mode_select import _MODE_CARDS, _SOLO_CARDS, _tip_jump_target

        cat, j = _tip_jump_target("weekly-review", _MODE_CARDS)
        assert cat == "solo"
        assert _SOLO_CARDS[j]["key"] == "weekly-review"

    def test_a_shared_key_from_agents_lands_on_team(self):
        from yeaboi.ui.mode_select import _AGENT_CARDS, _MODE_CARDS, _tip_jump_target

        cat, j = _tip_jump_target("daily-standup", _AGENT_CARDS)
        assert cat == "team"
        assert _MODE_CARDS[j]["key"] == "daily-standup"

    def test_an_agent_tip_from_team_lands_on_agents(self):
        from yeaboi.ui.mode_select import _AGENT_CARDS, _MODE_CARDS, _tip_jump_target

        cat, j = _tip_jump_target("agent-security", _MODE_CARDS)
        assert cat == "agents"
        assert _AGENT_CARDS[j]["key"] == "agent-security"

    def test_an_unknown_key_jumps_nowhere(self):
        from yeaboi.ui.mode_select import _MODE_CARDS, _tip_jump_target

        assert _tip_jump_target("astrology", _MODE_CARDS) is None


# ---------------------------------------------------------------------------
# The front page under the cards
# ---------------------------------------------------------------------------


def _story(title="OpenAI ships a new reasoning model", *, kind="article", url="https://news.example/story"):
    from yeaboi.news.edition import Page
    from yeaboi.news.parse import NewsItem

    item = NewsItem(id=title, title=title, url=url, source_name="Techmeme", column="ai", kind=kind)
    return Page(
        item=item,
        kicker="From the AI desk",
        counter="2 of 8",
        byline="Techmeme, 2 hours ago",
        read="Read more at Techmeme",
    )


def _rows(width=110, height=40, **kwargs) -> list[str]:
    console = Console(width=width, height=height, force_terminal=False)
    panel = _build_category_screen(1, width=width, height=height, **kwargs)
    rows = console.render_lines(panel, console.options.update(height=height), pad=True)
    return ["".join(seg.text for seg in row) for row in rows]


class TestInformer:
    def test_not_shown_below_forty_nine_rows(self):
        rows = _rows(height=48, page=_story(), edition="Refreshed just now.")
        text = "\n".join(rows)
        assert len(rows) == 48 and "▾" not in text and "OpenAI" not in text
        # The cards sit where they always did, centred, whatever the desk says.
        bare = _rows(height=48)
        assert [i for i, r in enumerate(bare) if "standups" in r] == [i for i, r in enumerate(rows) if "standups" in r]

    def test_the_split_is_untouched_at_the_floor(self):
        bare, printed = _rows(), _rows(page=_story(), edition="Refreshed 8 minutes ago.")
        assert len(bare) == len(printed) == 40
        assert next(i for i, row in enumerate(bare) if "switch" in row) == 36
        assert bare == printed

    def test_the_duck_and_his_bubble_under_the_cards(self):
        rows = _rows(height=53, page=_story(), edition="Refreshed just now.")
        assert len(rows) == 53 and "switch" in rows[49]
        text = "\n".join(rows)
        assert "OpenAI ships a new reasoning model" in text and "Techmeme, 2 hours ago" in text
        assert "[ prev" in text and "] next" in text and "i open the paper" in text
        assert "▾" in text and "ask niko" in text
        headline_row = next(row for row in rows if "OpenAI" in row)
        assert headline_row.index("OpenAI") > 110 - 3 - 44  # in the lane, bottom right
        cards_end = max(i for i, row in enumerate(rows) if "standups" in row)
        assert cards_end < rows.index(headline_row)

    def test_the_duck_is_green_and_the_agents_robo_is_steel(self):
        def _sgr(selected):
            console = Console(width=110, height=53, force_terminal=True, color_system="truecolor")
            with console.capture() as cap:
                console.print(_build_category_screen(selected, width=110, height=53, page=_story(), edition="x"))
            return cap.get()

        assert "34;158;122" in _sgr(1)
        assert "140;160;178" in _sgr(2)

    def test_a_long_headline_is_cut_to_two_lines(self):
        rows = _rows(height=53, page=_story("word " * 40), edition="x")
        assert len(rows) == 53 and "switch" in rows[49]
        assert "…" in "\n".join(rows)

    def test_a_quoted_headline_keeps_its_quote(self):
        text = "\n".join(_rows(height=53, page=_story("“Quoted” headline"), edition="x"))
        assert "“Quoted” headline" in text

    def test_no_story_yet(self):
        text = "\n".join(_rows(height=53, page=None, edition="Refreshing."))
        assert "Nothing to read yet." in text and "Refreshing." in text and "] next" not in text

    def test_seed_frame_shows_neither_duck_nor_bubble(self):
        from yeaboi.ui.mode_select import _landing_first_frame

        console = Console(width=110, height=53, force_terminal=False)
        seed = _landing_first_frame("team", width=110, height=53)
        rows = [
            "".join(seg.text for seg in row)
            for row in console.render_lines(seed, console.options.update(height=53), pad=True)
        ]
        text = "\n".join(rows)
        assert "▾" not in text and "Nothing to read" not in text
        # The duck glides in with the wordmarks, so at intro 0 the band under the cards is bare
        # (his caption keeps its glyphs while it fades from black, like on the welcome).
        from yeaboi.ui.mode_select.screens._screens_category import _card_band

        _first, last = _card_band(53)
        hint = next(i for i, row in enumerate(rows) if "switch" in row)
        band = [row for row in rows[last:hint] if "ask niko" not in row]
        assert len(band) > 10 and all(row.strip("│ ") == "" for row in band)

    def test_the_bubble_waits_for_the_duck(self):
        early = _rows(height=53, page=_story(), edition="x", intro=0.2)
        assert "OpenAI" not in "\n".join(early) and len(early) == 53

    def test_hint_row_is_the_split_s_own(self):
        hint = next(row for row in _rows(width=84) if "switch" in row)
        assert "q quit" in hint and "turn" not in hint and "…" not in hint


# ---------------------------------------------------------------------------
# The loop (Phase 0)
# ---------------------------------------------------------------------------


class _Live:
    def __init__(self):
        self.frames: list = []

    def update(self, renderable):
        self.frames.append(renderable)


class _Console:
    size = (84, 53)


def _keys(*sequence):
    remaining = list(sequence)

    def _read(timeout=None):
        return remaining.pop(0) if remaining else "q"

    return _read


def _paper(*titles, stale=False):
    from yeaboi.news.paper import Paper, Section
    from yeaboi.news.parse import NewsItem

    items = tuple(
        NewsItem(id=t, title=t, url=f"https://news.example/{t}", source_name="Src", column="ai") for t in titles
    )
    section = Section(column="ai", title="AI", items=items)
    return Paper(generated_at="2026-09-04T12:00:00+00:00", stale=stale, sections=(section,))


class FakeDesk:
    """Answers get_paper from a script; the last answer repeats."""

    def __init__(self, *answers, enabled=True):
        self.answers = list(answers) or [(_paper("Story one", "Story two"), False)]
        self.on = enabled
        self.asked = 0

    def enabled(self):
        return self.on

    def get_paper(self, *, refresh=False):
        self.asked += 1
        if len(self.answers) > 1:
            return self.answers.pop(0)
        return self.answers[0]


@pytest.fixture(autouse=True)
def _never_a_real_desk(monkeypatch):
    """A forgotten injection must never reach the network or the data home."""
    import yeaboi.ui.mode_select as ms

    monkeypatch.setattr(ms, "_LANDING_DESK", FakeDesk())


def _run(*keys, preselected="team", desk=None):
    import yeaboi.ui.mode_select as ms

    live = _Live()
    result = ms._run_category_screen(_Console(), live, _keys(*keys), True, preselected=preselected, desk=desk)
    return result, live


def _plain(renderable, width=84, height=53) -> str:
    console = Console(width=width, height=height, force_terminal=False)
    rows = console.render_lines(renderable, console.options.update(height=height), pad=True)
    return "\n".join("".join(seg.text for seg in row) for row in rows)


class TestCategoryLoop:
    @pytest.fixture(autouse=True)
    def _clock(self, monkeypatch):
        """Frames a few tenths apart, as in the app: the strip waits for the wordmarks' entrance."""
        import time as _time

        clock = [0.0]

        def _tick():
            clock[0] += 0.3
            return clock[0]

        monkeypatch.setattr(_time, "monotonic", _tick)

    def test_enter_picks_the_preselected_world(self):
        assert _run("enter")[0] == "team"
        assert _run("enter", preselected="agents")[0] == "agents"

    @pytest.mark.parametrize("key", ["right", "down", "tab"])
    def test_arrows_and_tab_move_on(self, key):
        assert _run(key, "enter", preselected="solo")[0] == "team"

    def test_left_wraps(self):
        assert _run("left", "enter", preselected="solo")[0] == "agents"

    @pytest.mark.parametrize("key", ["q", "esc"])
    def test_q_and_esc_quit(self, key):
        assert _run(key)[0] is None

    def test_the_bubble_carries_the_headline_and_turns_by_hand(self):
        _result, live = _run("]", "q")
        assert "Story two" in _plain(live.frames[-1])
        assert "Story one" in _plain(live.frames[0])

    def test_turning_back_wraps(self):
        _result, live = _run("[", "q")
        assert "Story two" in _plain(live.frames[-1])

    def test_a_click_on_the_duck_opens_the_paper_and_chooses_no_card(self, monkeypatch):
        import yeaboi.ui.mode_select as ms

        seen = []
        monkeypatch.setattr(ms, "_run_front_page_page", lambda *a, **k: seen.append(k["card"]["key"]))
        assert _run("click:70:45", "enter", preselected="agents")[0] == "agents"
        assert seen == ["agents"]
        # A click in the blank left of the lane is nothing.
        assert _run("click:10:45", "enter", preselected="solo")[0] == "solo"

    def test_polls_again_while_the_paper_is_stale(self, monkeypatch):
        import time as _time

        clock = [0.0]

        def _tick():
            clock[0] += 2.5
            return clock[0]

        monkeypatch.setattr(_time, "monotonic", _tick)
        desk = FakeDesk((_paper("Old", stale=True), True), (_paper("Fresh"), False))
        _result, live = _run("right", "right", "right", "q", desk=desk)
        assert desk.asked >= 2
        assert "Fresh" in _plain(live.frames[-1])

    def test_never_asks_again_when_news_is_off(self):
        desk = FakeDesk((_paper("Note"), False), enabled=False)
        _result, live = _run("right", "right", "q", desk=desk)
        assert desk.asked == 1
        assert "Note" in _plain(live.frames[-1])  # the release notes still make the paper

    def test_an_empty_paper_is_said_so(self):
        from yeaboi.news.paper import Paper

        desk = FakeDesk((Paper(stale=True), True))
        _result, live = _run("]", "q", desk=desk)  # nothing to turn to
        assert "Nothing to read yet." in _plain(live.frames[-1])

    def test_n_opens_niko_and_stays(self, monkeypatch):
        import yeaboi.ui.mode_select as ms

        opened = []
        monkeypatch.setattr(ms, "_open_niko", lambda *a, **k: opened.append(True))
        assert _run("n", "enter")[0] == "team"
        assert opened == [True]

    def test_i_opens_the_reader_with_the_same_desk_and_stays(self, monkeypatch):
        import yeaboi.ui.mode_select as ms

        seen = []
        monkeypatch.setattr(ms, "_run_front_page_page", lambda *a, **k: seen.append((k["desk"], k["card"]["key"])))
        desk = FakeDesk()
        assert _run("i", "enter", desk=desk)[0] == "team"
        assert seen == [(desk, "team")]

    def test_a_short_terminal_keeps_the_paper_off_the_split(self, monkeypatch):
        class Short:
            size = (84, 40)

        import yeaboi.ui.mode_select as ms

        opened = []
        monkeypatch.setattr(ms, "_run_front_page_page", lambda *a, **k: opened.append(True))
        live = _Live()
        keys = _keys("]", "i", "enter")
        assert ms._run_category_screen(Short(), live, keys, True, preselected="team", desk=FakeDesk()) == "team"
        assert "Story" not in _plain(live.frames[-1], height=40)
        assert opened == []  # no duck on screen, so no key nothing advertises


class TestLandingDesk:
    def test_built_once(self, monkeypatch):
        import yeaboi.news.desk as desk_mod
        import yeaboi.ui.mode_select as ms

        made = []
        monkeypatch.setattr(desk_mod, "NewsDesk", lambda: made.append(object()) or made[-1])
        monkeypatch.setattr(ms, "_LANDING_DESK", None)
        assert ms._landing_desk() is ms._landing_desk()
        assert len(made) == 1

    def test_open_story_warns_instead_of_raising(self, monkeypatch):
        import webbrowser

        import yeaboi.ui.mode_select as ms

        monkeypatch.setattr(webbrowser, "open", lambda url: (_ for _ in ()).throw(RuntimeError("no browser")))
        assert ms._open_story("https://x.example/") is False
        monkeypatch.setattr(webbrowser, "open", lambda url: True)
        assert ms._open_story("https://x.example/") is True
