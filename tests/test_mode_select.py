"""Tests for mode_select viewport scrolling, peek stubs, and project action buttons."""

from io import StringIO

import pytest
from rich.console import Console

from yeaboi import music
from yeaboi.ui.mode_select import (
    ProjectSummary,
    _build_action_button,
    _build_peek_above,
    _build_peek_below,
    _build_project_card,
    _build_project_export_success_screen,
    _build_project_list_screen,
    _build_project_row,
    _compute_viewport,
)
from yeaboi.ui.mode_select.screens._screens import (
    _COMPANION_MIN_WIDTH,
    _MIN_HEIGHT,
    _MODE_CARDS,
    _build_mode_screen,
    duck_hit,
    mode_at_row,
    selected_title_offset,
)


def _render(renderable, width: int = 80) -> str:
    """Render a Rich renderable to a plain string for testing.

    ``color_system`` is pinned even though ``no_color=True`` makes this file's own
    assertions colour-blind, because the damage is not to this file. Rich memoises
    ``Style`` objects globally and caches the rendered escape on them, so the first
    render at a given colour system fixes what every later render in the process
    gets. Left to auto-detection this console is truecolor on a dev machine and
    8-colour on CI (which sets no ``COLORTERM``), and the 8-colour pass poisons the
    styles `test_screen_backgrounds.py` then asserts truecolor SGR fragments on —
    nine failures in a file that pins its own console correctly and never imports
    this one. `TestTruecolorConsoles` there enforces this repo-wide.
    """
    buf = StringIO()
    console = Console(file=buf, width=width, force_terminal=True, color_system="truecolor", no_color=True)
    console.print(renderable)
    return buf.getvalue()


class TestComputeViewport:
    """Test the viewport calculation for scrolling project lists."""

    def test_all_items_fit_no_scrolling(self):
        """When all cards fit, return full range with no peeks."""
        # 3 items: 3*5 + 2*1 = 17 lines needed; 20 available
        start, end, above, below = _compute_viewport(3, 0, 20)
        assert (start, end) == (0, 3)
        assert above is False
        assert below is False

    def test_scrolling_selected_at_top(self):
        """When selected is at top, no peek above, peek below."""
        start, end, above, below = _compute_viewport(10, 0, 14)
        assert start == 0
        assert above is False
        assert below is True
        assert end > start

    def test_scrolling_selected_at_bottom(self):
        """When selected is last item, peek above, no peek below."""
        start, end, above, below = _compute_viewport(10, 9, 14)
        assert end == 10
        assert above is True
        assert below is False

    def test_scrolling_selected_in_middle(self):
        """When selected is in the middle, peeks on both sides."""
        start, end, above, below = _compute_viewport(10, 5, 14)
        assert above is True
        assert below is True
        assert start <= 5 < end

    def test_selected_always_visible(self):
        """Selected item must always be within the visible range."""
        for n in range(1, 15):
            for sel in range(n):
                start, end, _, _ = _compute_viewport(n, sel, 14)
                assert start <= sel < end, f"n={n}, sel={sel}: not in [{start}, {end})"

    def test_tiny_terminal(self):
        """When terminal is too small for even one card, show just the selected."""
        start, end, above, below = _compute_viewport(5, 2, 3)
        assert start == 2
        assert end == 3
        assert above is False
        assert below is False

    def test_single_item_no_scrolling(self):
        start, end, above, below = _compute_viewport(1, 0, 20)
        assert (start, end) == (0, 1)
        assert above is False
        assert below is False

    def test_reclaims_space_from_unused_peek(self):
        """When only one peek is needed, the freed space fits more cards."""
        start, end, above, below = _compute_viewport(10, 0, 14)
        visible = end - start
        assert visible >= 2


class TestBuildPeeks:
    """Test the 2-line peek stubs with project titles."""

    def test_peek_above_contains_title(self):
        result = _build_peek_above(box_w=40, title="My Project")
        rendered = _render(result)
        assert "My Project" in rendered

    def test_peek_above_has_top_border(self):
        """Peek above shows top border ╭──╮ (open side faces viewport below)."""
        result = _build_peek_above(box_w=40, title="Test")
        rendered = _render(result)
        assert "╭" in rendered
        assert "╮" in rendered

    def test_peek_below_contains_title(self):
        result = _build_peek_below(box_w=40, title="My Project")
        rendered = _render(result)
        assert "My Project" in rendered

    def test_peek_below_has_bottom_border(self):
        """Peek below shows bottom border ╰──╯ (open side faces viewport above)."""
        result = _build_peek_below(box_w=40, title="Test")
        rendered = _render(result)
        assert "╰" in rendered
        assert "╯" in rendered

    def test_peek_is_two_lines(self):
        above = _build_peek_above(box_w=40, title="Test")
        below = _build_peek_below(box_w=40, title="Test")
        assert len(above.renderables) == 2
        assert len(below.renderables) == 2

    def test_peek_truncates_long_title(self):
        long_title = "A" * 200
        above = _build_peek_above(box_w=30, title=long_title)
        rendered = _render(above)
        assert "A" in rendered

    def test_peek_with_empty_title(self):
        above = _build_peek_above(box_w=30)
        below = _build_peek_below(box_w=30)
        assert len(above.renderables) == 2
        assert len(below.renderables) == 2


class TestBuildActionButton:
    """Test the action button rendering placed beside project cards."""

    def test_button_contains_label(self):
        btn = _build_action_button("Delete", card_selected=True, fade_t=1.0)
        rendered = _render(btn)
        assert "Delete" in rendered

    def test_button_has_rounded_corners(self):
        btn = _build_action_button("Export", card_selected=True, fade_t=0.0)
        rendered = _render(btn)
        assert "╭" in rendered
        assert "╰" in rendered

    def test_unfocused_button_renders(self):
        btn = _build_action_button("Delete", card_selected=False)
        rendered = _render(btn)
        assert "Delete" in rendered

    def test_focused_button_with_full_fade(self):
        btn = _build_action_button("Export", focused=True, card_selected=True, fade_t=1.0)
        rendered = _render(btn)
        assert "Export" in rendered

    def test_button_at_zero_fade(self):
        """Button at fade_t=0 should render in grey (no error)."""
        btn = _build_action_button("Delete", card_selected=True, fade_t=0.0)
        rendered = _render(btn)
        assert "Delete" in rendered


class TestBuildProjectRow:
    """Test the horizontal project row layout (card + buttons)."""

    def test_row_contains_delete_and_export(self):
        project = ProjectSummary(name="Test Project", status="In Progress")
        row = _build_project_row(project, selected=True, box_w=40, action_btns_visible=2.0)
        rendered = _render(row)
        assert "Test Project" in rendered
        assert "Delete" in rendered
        assert "Export" in rendered

    def test_row_unselected_hides_buttons(self):
        """Unselected rows should not show Delete/Export buttons."""
        project = ProjectSummary(name="Other")
        row = _build_project_row(project, selected=False, box_w=40, action_btns_visible=0.0)
        rendered = _render(row)
        assert "Other" in rendered
        assert "Delete" not in rendered
        assert "Export" not in rendered

    def test_row_with_button_focus(self):
        """When focus is on Delete (1), it should still render all elements."""
        project = ProjectSummary(name="Focused")
        row = _build_project_row(project, selected=True, focus=1, box_w=40, del_fade=1.0, action_btns_visible=2.0)
        rendered = _render(row)
        assert "Focused" in rendered
        assert "Delete" in rendered
        assert "Export" in rendered

    def test_row_with_export_submenu(self):
        """When submenu is open, separate HTML and Markdown buttons appear."""
        project = ProjectSummary(name="SubTest")
        row = _build_project_row(
            project,
            selected=True,
            focus=2,
            box_w=40,
            exp_fade=0.0,  # Export greyed out
            show_export_submenu=True,
            submenu_sel=0,
            submenu_html_fade=1.0,
            submenu_md_fade=0.0,
            action_btns_visible=2.0,
            submenu_visible=3.0,
        )
        rendered = _render(row, width=120)
        assert "Export" in rendered
        assert "HTML" in rendered
        assert "Markdown" in rendered
        assert "Jira" in rendered

    def test_row_without_export_submenu_no_html_markdown(self):
        """Without submenu, HTML and Markdown labels should not appear."""
        project = ProjectSummary(name="NoSub")
        row = _build_project_row(project, selected=True, box_w=40, action_btns_visible=2.0)
        rendered = _render(row)
        assert "Export" in rendered
        assert "HTML" not in rendered
        assert "Markdown" not in rendered

    def test_row_submenu_markdown_selected(self):
        """When submenu_sel=1, Markdown button should be the focused one."""
        project = ProjectSummary(name="MdSel")
        row = _build_project_row(
            project,
            selected=True,
            focus=2,
            box_w=40,
            show_export_submenu=True,
            submenu_sel=1,
            submenu_html_fade=0.0,
            submenu_md_fade=1.0,
            action_btns_visible=2.0,
            submenu_visible=3.0,
        )
        rendered = _render(row, width=120)
        assert "HTML" in rendered
        assert "Markdown" in rendered


class TestBuildProjectCard:
    """Test project card rendering (without inline buttons)."""

    def test_card_contains_project_name(self):
        project = ProjectSummary(name="My Cool App")
        card = _build_project_card(project, selected=True)
        rendered = _render(card)
        assert "My Cool App" in rendered

    def test_card_does_not_contain_buttons(self):
        """Buttons are now separate panels, not inside the card."""
        project = ProjectSummary(name="Test")
        card = _build_project_card(project, selected=True)
        rendered = _render(card)
        assert "Delete" not in rendered
        assert "Export" not in rendered


class TestRoadmapProjectRows:
    """Saved roadmaps render as tagged ProjectSummary rows in the merged list."""

    def _roadmap_row(self, analyzed: bool = True):
        meta = "local · 4 candidate projects · analyzed 2026-07-18" if analyzed else "local · not analyzed yet"
        return ProjectSummary(name="Q3 2026 Roadmap", kind="roadmap", roadmap_id=7, created=meta)

    def test_roadmap_card_shows_tag_and_meta(self):
        card = _build_project_card(self._roadmap_row(), selected=True)
        rendered = _render(card)
        assert "Q3 2026 Roadmap" in rendered
        assert "[roadmap]" in rendered
        assert "4 candidate projects" in rendered
        assert "analyzed 2026-07-18" in rendered

    def test_not_analyzed_meta(self):
        rendered = _render(_build_project_card(self._roadmap_row(analyzed=False), selected=False))
        assert "not analyzed yet" in rendered

    def test_project_card_has_no_roadmap_tag(self):
        rendered = _render(_build_project_card(ProjectSummary(name="Real Project"), selected=True))
        assert "[roadmap]" not in rendered

    def test_merged_list_renders_both_kinds(self):
        rows = [ProjectSummary(name="Billing revamp", id="1", status="In Progress"), self._roadmap_row()]
        screen = _build_project_list_screen(rows, 1, action_btns_visible=2.0)
        rendered = _render(screen, width=120)
        assert "Billing revamp" in rendered
        assert "Q3 2026 Roadmap" in rendered
        assert "[roadmap]" in rendered
        # The selected roadmap row shows the standard Delete/Export buttons.
        assert "Delete" in rendered
        assert "Export" in rendered

    def test_delete_popup_shows_roadmap_name(self):
        rows = [self._roadmap_row()]
        screen = _build_project_list_screen(rows, 0, delete_popup_name="Q3 2026 Roadmap", delete_popup_t=1.0)
        rendered = _render(screen)
        assert "Q3 2026 Roadmap" in rendered
        assert "Enter to confirm" in rendered


class TestDeletePopup:
    """Test the delete popup overlay in the project list screen."""

    def _projects(self):
        return [ProjectSummary(name="My App", id="1")]

    def test_popup_shows_project_name(self):
        screen = _build_project_list_screen(self._projects(), 0, delete_popup_name="My App", delete_popup_t=1.0)
        rendered = _render(screen)
        assert "My App" in rendered

    def test_popup_hidden_when_t_zero(self):
        screen = _build_project_list_screen(self._projects(), 0, delete_popup_name="My App", delete_popup_t=0.0)
        rendered = _render(screen)
        assert "Enter to confirm" not in rendered

    def test_popup_shows_confirm_hint(self):
        screen = _build_project_list_screen(self._projects(), 0, delete_popup_name="My App", delete_popup_t=1.0)
        rendered = _render(screen)
        assert "Enter to confirm" in rendered


class TestExportSuccessScreen:
    """Test the export success screen rendering."""

    def test_shows_file_path(self):
        screen = _build_project_export_success_screen("/tmp/test-export.json")
        rendered = _render(screen)
        assert "/tmp/test-export.json" in rendered

    def test_shows_success_message(self):
        screen = _build_project_export_success_screen("/tmp/test.json")
        rendered = _render(screen)
        assert "exported" in rendered.lower()


def _text_of(panel, width, height):
    con = Console(width=width, height=height, record=True, file=open("/dev/null", "w"))
    con.print(panel)
    return con.export_text()


class TestModeScreenCompanion:
    """Test the head-companion mascot rendered beside the welcome-screen menu.

    Both sizes are derived from `_COMPANION_MIN_WIDTH`, not written out. The
    narrow case used to be a hardcoded 80x34, which stopped testing anything the
    day the *screen's* own minimum rose to 84x40: below that the menu is not
    drawn at all, so the control row this asserts on simply was not there and the
    test raised `StopIteration`. Nothing noticed, because no CI lane ran this
    file. Pinning the fixture to the constant means the threshold can move again
    and the test moves with it.
    """

    def _control_line(self, width, height):
        panel = _build_mode_screen(0, width=width, height=height, shimmer_tick=0.0, desc_reveal=999)
        lines = _text_of(panel, width, height).splitlines()
        line = next((ln for ln in lines if "prev" in ln and "next" in ln), None)
        assert line is not None, f"no control row rendered at {width}x{height} — the fixture is below a minimum"
        return line

    # Height comes from _MIN_HEIGHT, not _COMPANION_MIN_HEIGHT: this pair is about
    # the WIDTH threshold, and the height only has to be one the screen renders
    # fully at — which is the screen's own minimum, and moves when it does.
    def test_companion_present_when_wide(self):
        # Companion layout: the tip controls sit ON the speech-bubble border, so the
        # "prev/next" line carries the bubble's rounded corners.
        line = self._control_line(_COMPANION_MIN_WIDTH, _MIN_HEIGHT)
        assert "╰" in line and "╯" in line

    def test_companion_absent_when_narrow(self):
        # One column under the threshold: the controls are a plain centred bottom
        # row with no bubble border.
        line = self._control_line(_COMPANION_MIN_WIDTH - 1, _MIN_HEIGHT)
        assert "╰" not in line and "╯" not in line

    def test_mode_screen_exact_height_with_companion(self):
        panel = _build_mode_screen(0, width=110, height=39, shimmer_tick=0.0)
        text = _text_of(panel, 110, 39)
        assert len(text.splitlines()) == 39

    def test_tip_controls_live_on_the_bubble_border(self):
        # The tip controls sit ON the speech-bubble's bottom border (subtitle),
        # not as a separate row — the control line also carries the bubble's
        # rounded corners.
        panel = _build_mode_screen(0, width=120, height=40, desc_reveal=999, shimmer_tick=1.0)
        lines = _text_of(panel, 120, 40).splitlines()
        control_line = next(ln for ln in lines if "prev" in ln and "next" in ln)
        assert "╰" in control_line and "╯" in control_line  # it's the bubble's border row

    def test_no_chilling_caption(self):
        # The "chilling" caption was removed entirely.
        panel = _build_mode_screen(0, width=120, height=40, desc_reveal=999, shimmer_tick=1.0)
        assert "chilling" not in _text_of(panel, 120, 40)


class TestModeAtRow:
    """Click hit-testing: map a terminal (row, col) to a mode card (click-to-select)."""

    def _title_rows(self, selected, width, height):
        """1-based rows that carry block-font *title* glyphs, from the real render.

        Only the left menu region is scanned — the companion duck also uses block
        glyphs but lives in the reserved right-hand lane, so it must be excluded.
        """
        panel = _build_mode_screen(selected, width=width, height=height, desc_reveal=999)
        lines = _text_of(panel, width, height).splitlines()
        left_w = width - 40  # comfortably left of the _COMPANION_COLS (36) duck lane
        return [i for i, ln in enumerate(lines, 1) if any(g in ln[:left_w] for g in "█▀▄")]

    def test_every_title_row_resolves_to_a_mode(self):
        # Every rendered block-glyph row must hit *some* mode — no dead titles.
        w, h = 120, 40
        for selected in (0, 3, 7):
            for row in self._title_rows(selected, w, h):
                assert mode_at_row(selected, width=w, height=h, row=row, col=10) is not None

    def test_rows_are_contiguous_and_ordered(self):
        # Walking rows top→bottom yields modes 0,1,2,… in order with no gaps.
        w, h = 120, 40
        hit_order = []
        for row in range(1, h + 1):
            idx = mode_at_row(3, width=w, height=h, row=row, col=10)
            if idx is not None and (not hit_order or hit_order[-1] != idx):
                hit_order.append(idx)
        assert hit_order == list(range(len(_MODE_CARDS)))

    def test_selected_mode_spans_more_rows_than_unselected(self):
        # The selected card also shows its description, so it owns extra rows.
        w, h = 120, 40
        rows = {i: 0 for i in range(len(_MODE_CARDS))}
        for row in range(1, h + 1):
            idx = mode_at_row(2, width=w, height=h, row=row, col=10)
            if idx is not None:
                rows[idx] += 1
        assert rows[2] > rows[0]

    def test_click_in_duck_lane_returns_none(self):
        # A click in the reserved right-hand companion lane isn't a menu click.
        w, h = 120, 40
        # A row that maps to a mode in the left column…
        left = mode_at_row(0, width=w, height=h, row=self._title_rows(0, w, h)[0], col=10)
        assert left is not None
        # …resolves to None at the same row but in the duck lane.
        assert mode_at_row(0, width=w, height=h, row=self._title_rows(0, w, h)[0], col=w - 5) is None

    def test_click_off_the_list_returns_none(self):
        # The top border and the very bottom (version row) aren't modes.
        w, h = 120, 40
        assert mode_at_row(0, width=w, height=h, row=1, col=10) is None
        assert mode_at_row(0, width=w, height=h, row=h, col=10) is None

    def test_narrow_layout_without_companion_still_hits(self):
        # Below the companion width the menu is full-width; hit-testing still works
        # and there is no duck lane to exclude.
        w, h = 90, 40
        assert mode_at_row(0, width=w, height=h, row=self._title_rows(0, w, h)[0], col=w - 5) is not None


class TestSelectedTitleOffset:
    """The select→top slide starts from the item's actual resting row."""

    def _title_rows(self, selected, width, height):
        panel = _build_mode_screen(selected, width=width, height=height, desc_reveal=999)
        lines = _text_of(panel, width, height).splitlines()
        left_w = width - 40  # exclude the right-hand duck lane
        return [i for i, ln in enumerate(lines, 1) if any(g in ln[:left_w] for g in "█▀▄")]

    def test_offset_matches_rendered_title_row(self):
        # Content begins at 1-based terminal row 3 (top border + top pad); the
        # offset counts blank rows above the title, so title_row == offset + 3.
        w, h = 120, 40
        for selected in (0, 3, 7):
            rows = self._title_rows(selected, w, h)
            first_title_row = rows[2 * selected]  # each title is 2 glyph rows
            assert selected_title_offset(selected, width=w, height=h) + 3 == first_title_row

    def test_later_items_rest_lower_than_earlier_ones(self):
        # A mid-list pick must start its slide below a top-of-list pick.
        w, h = 120, 40
        assert selected_title_offset(7, width=w, height=h) > selected_title_offset(0, width=w, height=h)

    def test_narrow_layout_offset_matches_render(self):
        # Same invariant holds below the companion width (no duck lane).
        w, h = 90, 40
        rows = self._title_rows(2, w, h)
        assert selected_title_offset(2, width=w, height=h) + 3 == rows[4]


class TestDuckHit:
    """Click-the-duck hit-testing (triggers the double-shades gag)."""

    def test_false_when_no_companion(self):
        # Below the companion size there's no duck in a lane to click.
        assert duck_hit(90, 40, row=35, col=80) is False

    def test_true_on_duck_region_in_lane(self):
        w, h = 120, 40
        # The resting head spans rows [h-11 .. h-5]; a mid-duck row in the right lane.
        assert duck_hit(w, h, row=h - 8, col=w - 15) is True

    def test_false_outside_the_lane(self):
        w, h = 120, 40
        # Same row but in the left (menu) column — not the duck.
        assert duck_hit(w, h, row=h - 8, col=10) is False

    def test_false_well_above_the_duck(self):
        w, h = 120, 40
        assert duck_hit(w, h, row=6, col=w - 15) is False


class TestCompanionEntrance:
    """On welcome load the duck slides in from the right, then the tip fades in."""

    def _lane_glyphs(self, txt: str) -> int:
        # Count block-font glyphs in the right-hand companion lane (col ≥ 85); the
        # mode titles live well to the left of it, so this is the duck.
        return sum(ln[85:].count("█") for ln in txt.splitlines())

    def test_duck_offscreen_at_start_present_when_settled(self):
        early = _text_of(_build_mode_screen(0, width=120, height=40, companion_intro=0.0), 120, 40)
        settled = _text_of(_build_mode_screen(0, width=120, height=40, companion_intro=1.0), 120, 40)
        assert self._lane_glyphs(early) == 0  # duck still off-screen during the slide
        assert self._lane_glyphs(settled) > 0  # duck arrived in its corner

    def test_tip_bubble_hidden_until_the_duck_settles(self):
        early = _text_of(_build_mode_screen(0, width=120, height=40, companion_intro=0.0), 120, 40)
        settled = _text_of(_build_mode_screen(0, width=120, height=40, companion_intro=1.0), 120, 40)
        assert "▾" not in early  # no speech-bubble tail while the duck is sliding in
        assert "▾" in settled  # bubble (and its tail) appear once he's in


class TestMusicPocket:
    """The welcome screen boxes the music bar in a bottom-right pocket.

    Music availability is pinned, because `build_music_subtitle` early-returns a
    `brew install ffmpeg` hint when `ffplay` is missing — and that is the one
    branch with no "channel" hint in it. So this file passed on any machine with
    ffmpeg and failed on any without, which for months meant it passed for
    everyone who ran it and was never run anywhere that did not have it. Bringing
    `tests/*.py` into CI is what surfaced it.
    """

    @pytest.fixture(autouse=True)
    def _music_installed(self, monkeypatch):
        monkeypatch.setattr(music, "is_music_available", lambda: (True, ""))

    def test_pocket_renders_with_music_and_border(self):
        panel = _build_mode_screen(0, width=120, height=40, shimmer_tick=1.0)
        text = _text_of(panel, 120, 40)
        assert "channel" in text  # the music controls are present…
        assert "╭" in text and "╰" in text  # …inside a rounded box (roof + floor)

    def test_pocket_frame_is_not_a_panel_so_musiclive_skips_it(self):
        # The welcome screen returns a frame (not a bare Panel), so MusicLive won't
        # ALSO stamp the flat subtitle bar onto it (see MusicLive._stamp).
        from rich.panel import Panel

        result = _build_mode_screen(0, width=120, height=40, shimmer_tick=1.0)
        assert not isinstance(result, Panel)

    def test_narrow_keeps_flat_bar_panel(self):
        # Narrow (no companion) keeps the flat subtitle bar — returns a plain Panel.
        from rich.panel import Panel

        result = _build_mode_screen(0, width=90, height=40, shimmer_tick=1.0)
        assert isinstance(result, Panel)

    def test_border_reroutes_up_over_the_music(self):
        # The bottom border rises at the pocket (╯ … ╰ with a gap) rather than a
        # flat line under the music — verify the rerouted glyphs are on the last row.
        panel = _build_mode_screen(0, width=120, height=40, shimmer_tick=1.0)
        lines = [ln for ln in _text_of(panel, 120, 40).splitlines() if ln.strip()]
        border = next(ln for ln in reversed(lines) if ln.strip().startswith("╰"))
        assert border.count("╯") == 2 and border.count("╰") == 2  # rose (╯) then dropped (╰)
        assert border.strip().endswith("╯")  # panel bottom-right corner intact
