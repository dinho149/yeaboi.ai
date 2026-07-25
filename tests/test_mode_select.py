"""Tests for mode_select viewport scrolling, peek stubs, and project action buttons."""

from io import StringIO

from rich.console import Console

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
from yeaboi.ui.mode_select.screens._screens import _build_mode_screen


def _render(renderable, width: int = 80) -> str:
    """Render a Rich renderable to a plain string for testing."""
    buf = StringIO()
    console = Console(file=buf, width=width, force_terminal=True, no_color=True)
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
    """Test the head-companion mascot rendered beside the welcome-screen menu."""

    def test_companion_present_when_wide(self):
        panel = _build_mode_screen(0, width=110, height=30, shimmer_tick=0.0)
        text = _text_of(panel, 110, 30)
        # "chilling" is the companion's caption — unique to the duck column;
        # the block-font mode titles themselves already use ▀/▄ glyphs, so
        # glyph presence alone can't distinguish companion on/off.
        assert "chilling" in text

    def test_companion_absent_when_narrow(self):
        panel = _build_mode_screen(0, width=80, height=26, shimmer_tick=0.0)
        text = _text_of(panel, 80, 26)
        assert "chilling" not in text

    def test_mode_screen_exact_height_with_companion(self):
        panel = _build_mode_screen(0, width=110, height=30, shimmer_tick=0.0)
        text = _text_of(panel, 110, 30)
        assert len(text.splitlines()) == 30
