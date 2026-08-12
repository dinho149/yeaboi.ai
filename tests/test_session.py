"""Tests for the full-screen TUI session (ui/session.py).

Tests focus on:
- Screen builder functions (pure functions returning Rich Panels)
- Helper utilities (_wrap_text, _render_to_lines)
- Input loops with mock key sequences
- Phase flow orchestration
"""

from io import StringIO
from unittest.mock import MagicMock, patch

from rich.console import Console
from rich.panel import Panel

from yeaboi.ui.session import (
    _build_description_screen,
    _build_edit_prompt_screen,
    _build_pipeline_screen,
    _build_question_screen,
    _build_summary_screen,
    _phase_description_input,
    _question_input_loop,
    _render_to_lines,
    _wrap_text,
    run_session,
)
from yeaboi.ui.session._renderers import _render_tui_stories
from yeaboi.ui.session.editor._editor import _parse_edited_story, _story_to_text, edit_story


def _make_console(width: int = 100, height: int = 30) -> Console:
    """Create a Console writing to StringIO for testing."""
    c = Console(file=StringIO(), width=width, force_terminal=True, color_system="truecolor")
    # Patch size to return predictable dimensions
    c._size = (width, height)
    return c


# ---------------------------------------------------------------------------
# Screen builders
# ---------------------------------------------------------------------------


class TestBuildDescriptionScreen:
    def test_returns_panel(self):
        result = _build_description_screen(["hello"], 0, 5, width=80, height=24)
        assert isinstance(result, Panel)

    def test_empty_input(self):
        result = _build_description_screen([""], 0, 0, width=80, height=24)
        assert isinstance(result, Panel)

    def test_multi_line_input(self):
        result = _build_description_screen(
            ["line one", "line two", "line three"],
            1,
            4,
            width=80,
            height=24,
        )
        assert isinstance(result, Panel)

    def test_cursor_at_end(self):
        """Cursor beyond line length should still produce a valid panel."""
        result = _build_description_screen(["abc"], 0, 100, width=80, height=24)
        assert isinstance(result, Panel)


class TestBuildQuestionScreen:
    def test_free_text_question(self):
        result = _build_question_screen(
            "What is the project?",
            "",
            width=80,
            height=24,
        )
        assert isinstance(result, Panel)

    def test_with_choices(self):
        choices = [("Greenfield", True), ("Existing codebase", False), ("Hybrid", False)]
        result = _build_question_screen(
            "Is this greenfield?",
            "",
            choices=choices,
            selected_choice=1,
            width=80,
            height=24,
        )
        assert isinstance(result, Panel)

    def test_with_suggestion(self):
        result = _build_question_screen(
            "Sprint length?",
            "",
            suggestion="2 weeks",
            width=80,
            height=24,
        )
        assert isinstance(result, Panel)

    def test_with_preamble(self):
        result = _build_question_screen(
            "Q6",
            "",
            preamble_lines=["Extracted from description:", "Team size: ~5"],
            phase_label="Phase 2: Team & Capacity",
            progress="Q6 of 26",
            width=80,
            height=24,
        )
        assert isinstance(result, Panel)

    def test_with_typed_input(self):
        result = _build_question_screen(
            "What is the project?",
            "An AI scrum master",
            width=80,
            height=24,
        )
        assert isinstance(result, Panel)


class TestBuildSummaryScreen:
    def test_basic_summary(self):
        lines = ["Q1: Project", "Q2: Greenfield", "Q3: Solve problem"]
        result = _build_summary_screen(lines, 0, 0, width=80, height=24)
        assert isinstance(result, Panel)

    def test_scroll_indicators(self):
        # Many lines to trigger scrolling
        lines = [f"Line {i}" for i in range(100)]
        result = _build_summary_screen(lines, 10, 0, width=80, height=24)
        assert isinstance(result, Panel)

    def test_menu_selection(self):
        lines = ["Q1: Project"]
        for menu_idx in range(3):
            result = _build_summary_screen(lines, 0, menu_idx, width=80, height=24)
            assert isinstance(result, Panel)

    def test_status_message(self):
        lines = ["Q1: Project"]
        result = _build_summary_screen(
            lines,
            0,
            2,
            width=80,
            height=24,
            status_msg="Exported successfully",
        )
        assert isinstance(result, Panel)


class TestBuildPipelineScreen:
    def test_processing_state(self):
        result = _build_pipeline_screen(
            "Analysing project",
            "[1/5]",
            [],
            0,
            0,
            status="processing",
            width=80,
            height=24,
            tick=1.5,
        )
        assert isinstance(result, Panel)

    def test_complete_state(self):
        lines = ["Feature 1: Authentication", "Feature 2: Dashboard"]
        result = _build_pipeline_screen(
            "Generating features",
            "[2/5]",
            lines,
            0,
            0,
            status="complete",
            width=80,
            height=24,
        )
        assert isinstance(result, Panel)

    def test_scrollable_content(self):
        lines = [f"Artifact line {i}" for i in range(100)]
        result = _build_pipeline_screen(
            "Task decomposition",
            "[4/5]",
            lines,
            20,
            1,
            status="complete",
            width=80,
            height=24,
        )
        assert isinstance(result, Panel)


class TestBuildEditPromptScreen:
    def test_basic_prompt(self):
        result = _build_edit_prompt_screen(
            "Which question would you like to change?",
            "",
            width=80,
            height=24,
        )
        assert isinstance(result, Panel)

    def test_with_input(self):
        result = _build_edit_prompt_screen(
            "Describe changes:",
            "Make the sprints 1 week",
            width=80,
            height=24,
        )
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


class TestWrapText:
    def test_short_text(self):
        result = _wrap_text("hello world", 80)
        assert result == ["hello world"]

    def test_long_text(self):
        text = "word " * 20
        result = _wrap_text(text.strip(), 40)
        assert len(result) > 1
        for line in result:
            assert len(line) <= 40

    def test_respects_newlines(self):
        result = _wrap_text("line one\nline two", 80)
        assert result == ["line one", "line two"]

    def test_empty_lines(self):
        result = _wrap_text("before\n\nafter", 80)
        assert result == ["before", "", "after"]

    def test_empty_string(self):
        result = _wrap_text("", 80)
        assert result == [""]


class TestRenderToLines:
    def test_renders_text(self):
        from rich.text import Text

        c = _make_console()
        text = Text("Hello World")
        lines = _render_to_lines(c, text, 80)
        assert any("Hello World" in line for line in lines)

    def test_renders_panel(self):
        panel = Panel("Content here")
        c = _make_console()
        lines = _render_to_lines(c, panel, 80)
        assert len(lines) > 0


# ---------------------------------------------------------------------------
# Phase A: Description Input
# ---------------------------------------------------------------------------


class TestPhaseDescriptionInput:
    def test_esc_returns_none(self):
        """Pressing Esc should return None (cancel)."""
        live = MagicMock()
        console = _make_console()
        keys = iter(["esc"])
        result = _phase_description_input(live, console, lambda: next(keys))
        assert result is None

    def test_submit_prefilled_example(self):
        """Pressing Enter twice submits the pre-filled example text (dry-run)."""
        live = MagicMock()
        console = _make_console()
        keys = iter(["enter", "enter"])
        result = _phase_description_input(live, console, lambda: next(keys), dry_run=True)
        assert result is not None
        desc, _, _, _, _ = result
        assert "restaurant reservations" in desc

    def test_clear_and_type(self):
        """Clear pre-filled text then type custom input (dry-run)."""
        live = MagicMock()
        console = _make_console()
        keys = iter(["clear", "H", "i", "enter", "enter"])
        result = _phase_description_input(live, console, lambda: next(keys), dry_run=True)
        desc, _, _, _, _ = result
        assert desc == "Hi"

    def test_backspace(self):
        """Backspace removes characters from pre-filled text."""
        live = MagicMock()
        console = _make_console()
        # Clear, type "ab", backspace, type "c" → "ac"
        keys = iter(["clear", "a", "b", "backspace", "c", "enter", "enter"])
        result = _phase_description_input(live, console, lambda: next(keys))
        desc, _, _, _, _ = result
        assert desc == "ac"

    def test_ctrl_v_attaches_image_and_returns_paths(self, monkeypatch, tmp_path):
        """Ctrl+V inserts an [image #N] chip; surviving chips return their paths."""
        img = tmp_path / "img-abc.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        monkeypatch.setattr("yeaboi.clipboard.read_clipboard_image", lambda: (img.read_bytes(), "image/png"))
        monkeypatch.setattr("yeaboi.paths.ATTACHMENTS_DIR", tmp_path / "attachments")
        live = MagicMock()
        console = _make_console()
        keys = iter(["s", "e", "e", " ", "ctrl+v", "enter"])
        result = _phase_description_input(live, console, lambda: next(keys), scope_id="proj-1")
        desc, _, _, _, images = result
        assert "see [image #1]" == desc
        assert len(images) == 1
        assert images[0].endswith(".png")

    def test_ctrl_v_deleted_chip_detaches_image(self, monkeypatch, tmp_path):
        """Deleting the chip text means the image is not returned at submit."""
        monkeypatch.setattr("yeaboi.clipboard.read_clipboard_image", lambda: (b"\x89PNG\r\n\x1a\n", "image/png"))
        monkeypatch.setattr("yeaboi.paths.ATTACHMENTS_DIR", tmp_path / "attachments")
        live = MagicMock()
        console = _make_console()
        # Paste a chip ("[image #1]" = 10 chars), delete it entirely, type "x".
        keys = iter(["ctrl+v"] + ["backspace"] * 10 + ["x", "enter"])
        result = _phase_description_input(live, console, lambda: next(keys), scope_id="proj-1")
        desc, _, _, _, images = result
        assert desc == "x"
        assert images == []

    def test_initial_text_prefills_editor(self):
        """A roadmap-seeded description pre-fills the editor and submits as-is."""
        live = MagicMock()
        console = _make_console()
        keys = iter(["enter", "enter"])
        seeded = "Add SSO across products.\nCovers SAML and OIDC."
        result = _phase_description_input(live, console, lambda: next(keys), initial_text=seeded)
        assert result is not None
        desc, lines, row, col, _ = result
        assert desc == seeded
        assert lines == ["Add SSO across products.", "Covers SAML and OIDC."]
        assert row == 1 and col == len("Covers SAML and OIDC.")

    def test_initial_text_wins_over_dry_run_example(self):
        """initial_text beats the dry-run example prefill."""
        live = MagicMock()
        console = _make_console()
        keys = iter(["enter", "enter"])
        result = _phase_description_input(
            live, console, lambda: next(keys), dry_run=True, initial_text="Seeded project."
        )
        desc, _, _, _, _ = result
        assert desc == "Seeded project."
        assert "restaurant reservations" not in desc

    def test_initial_text_still_editable(self):
        """The seeded text is a prefill, not a lock — clear + retype works."""
        live = MagicMock()
        console = _make_console()
        keys = iter(["clear", "H", "i", "enter", "enter"])
        result = _phase_description_input(live, console, lambda: next(keys), initial_text="Seeded.")
        desc, _, _, _, _ = result
        assert desc == "Hi"


# ---------------------------------------------------------------------------
# Question Input Loop
# ---------------------------------------------------------------------------


class TestQuestionInputLoop:
    def test_enter_empty_for_suggestion(self):
        """Pressing Enter with no typed input should return empty (suggestion flow)."""
        live = MagicMock()
        console = _make_console()
        keys = iter(["enter"])
        result = _question_input_loop(
            live,
            console,
            lambda: next(keys),
            question_text="Q?",
            choices=None,
            suggestion="2 weeks",
            progress="",
            phase_label="",
            preamble_lines=None,
            export_only=False,
            graph_state={},
        )
        assert result == ""  # caller handles suggestion resolution

    def test_type_answer(self):
        live = MagicMock()
        console = _make_console()
        keys = iter(["5", "enter"])
        result = _question_input_loop(
            live,
            console,
            lambda: next(keys),
            question_text="Team size?",
            choices=None,
            suggestion=None,
            progress="",
            phase_label="",
            preamble_lines=None,
            export_only=False,
            graph_state={},
        )
        assert result == "5"

    def test_esc_returns_none(self):
        live = MagicMock()
        console = _make_console()
        keys = iter(["esc"])
        result = _question_input_loop(
            live,
            console,
            lambda: next(keys),
            question_text="Q?",
            choices=None,
            suggestion=None,
            progress="",
            phase_label="",
            preamble_lines=None,
            export_only=False,
            graph_state={},
        )
        assert result is None

    def test_choice_selection(self):
        """Arrow down + Enter selects the second choice."""
        live = MagicMock()
        console = _make_console()
        keys = iter(["down", "enter"])
        choices = [("Greenfield", True), ("Existing codebase", False)]
        result = _question_input_loop(
            live,
            console,
            lambda: next(keys),
            question_text="Project type?",
            choices=choices,
            suggestion=None,
            progress="",
            phase_label="",
            preamble_lines=None,
            export_only=False,
            graph_state={},
        )
        assert result == "Existing codebase"

    def test_export_only_returns_synthetic_answer(self):
        """export_only mode should auto-answer without waiting for keys."""
        live = MagicMock()
        console = _make_console()
        # No keys needed — export_only bypasses input
        result = _question_input_loop(
            live,
            console,
            lambda: "should-not-be-called",
            question_text="Q?",
            choices=None,
            suggestion=None,
            progress="",
            phase_label="",
            preamble_lines=None,
            export_only=True,
            graph_state={},
        )
        assert result == "continue"

    def test_export_only_uses_suggestion(self):
        """export_only with a suggestion from _get_active_suggestion should return it."""
        live = MagicMock()
        console = _make_console()
        # Patch _get_active_suggestion to return the suggestion
        with patch("yeaboi.ui.session._get_active_suggestion", return_value="2 weeks"):
            result = _question_input_loop(
                live,
                console,
                lambda: "should-not-be-called",
                question_text="Q?",
                choices=None,
                suggestion="2 weeks",
                progress="",
                phase_label="",
                preamble_lines=None,
                export_only=True,
                graph_state={},
            )
        assert result == "2 weeks"


# ---------------------------------------------------------------------------
# run_session smoke test
# ---------------------------------------------------------------------------


class TestRunSession:
    @patch("yeaboi.ui.session.create_graph")
    def test_esc_on_description_exits_cleanly(self, mock_graph):
        """Double-Esc in the chat greeting should exit without errors."""
        live = MagicMock()
        console = _make_console()
        keys = iter(["esc", "esc"])
        run_session(
            live,
            console,
            intake_mode="smart",
            _read_key_fn=lambda: next(keys, ""),
        )
        # No graph invocation should happen
        mock_graph.return_value.invoke.assert_not_called()


# ---------------------------------------------------------------------------
# Story highlighting (_render_tui_stories with selected_index)
# ---------------------------------------------------------------------------


def _make_test_stories():
    """Create sample stories and features for rendering tests."""
    from yeaboi.agent.state import (
        AcceptanceCriterion,
        Discipline,
        Feature,
        Priority,
        StoryPointValue,
        UserStory,
    )

    features = [
        Feature(id="feature-1", title="Authentication", description="User auth", priority=Priority.HIGH),
        Feature(id="feature-2", title="Dashboard", description="Main dashboard", priority=Priority.MEDIUM),
    ]
    stories = [
        UserStory(
            id="story-1",
            feature_id="feature-1",
            persona="user",
            goal="log in",
            benefit="access the app",
            acceptance_criteria=(AcceptanceCriterion(given="credentials", when="submit", then="logged in"),),
            story_points=StoryPointValue.THREE,
            priority=Priority.HIGH,
            discipline=Discipline.FULLSTACK,
        ),
        UserStory(
            id="story-2",
            feature_id="feature-1",
            persona="admin",
            goal="manage users",
            benefit="control access",
            acceptance_criteria=(AcceptanceCriterion(given="admin role", when="open panel", then="see users"),),
            story_points=StoryPointValue.FIVE,
            priority=Priority.MEDIUM,
            discipline=Discipline.BACKEND,
        ),
        UserStory(
            id="story-3",
            feature_id="feature-2",
            persona="user",
            goal="view dashboard",
            benefit="see overview",
            acceptance_criteria=(AcceptanceCriterion(given="logged in", when="navigate", then="see dashboard"),),
            story_points=StoryPointValue.TWO,
            priority=Priority.LOW,
            discipline=Discipline.FRONTEND,
        ),
    ]
    return stories, features


class TestRenderTuiStoriesSelectedIndex:
    """Test that _render_tui_stories highlights the selected story."""

    def test_no_selection_all_grey(self):
        """Without selected_index, all panels should use grey borders."""
        stories, features = _make_test_stories()
        console = _make_console()
        group = _render_tui_stories(stories, features)
        lines = _render_to_lines(console, group, 80)
        rendered = "\n".join(lines)
        # All panels should render without error
        assert len(lines) > 0
        assert "story-1" in rendered or "log in" in rendered

    def test_selected_index_renders(self):
        """With selected_index=1, the rendering should succeed."""
        stories, features = _make_test_stories()
        console = _make_console()
        group = _render_tui_stories(stories, features, selected_index=1)
        lines = _render_to_lines(console, group, 80)
        assert len(lines) > 0

    def test_selected_index_out_of_range(self):
        """selected_index beyond story count should not crash."""
        stories, features = _make_test_stories()
        console = _make_console()
        group = _render_tui_stories(stories, features, selected_index=99)
        lines = _render_to_lines(console, group, 80)
        assert len(lines) > 0


# ---------------------------------------------------------------------------
# Story text editor: serialisation round-trip
# ---------------------------------------------------------------------------


class TestStoryToTextRoundTrip:
    """Test story → text → parsed story round-trip."""

    def test_round_trip_preserves_fields(self):
        stories, _ = _make_test_stories()
        story = stories[0]
        text = _story_to_text(story)
        parsed = _parse_edited_story(text, story)

        assert parsed.id == story.id
        assert parsed.feature_id == story.feature_id
        assert parsed.persona == story.persona
        assert parsed.goal == story.goal
        assert parsed.benefit == story.benefit
        assert parsed.story_points == story.story_points
        assert parsed.priority == story.priority
        assert parsed.discipline == story.discipline
        assert len(parsed.acceptance_criteria) == len(story.acceptance_criteria)
        assert parsed.acceptance_criteria[0].given == story.acceptance_criteria[0].given

    def test_modified_field_parsed_correctly(self):
        stories, _ = _make_test_stories()
        story = stories[0]
        text = _story_to_text(story)
        # Change persona
        text = text.replace("Persona:    user", "Persona:    developer")
        parsed = _parse_edited_story(text, story)
        assert parsed.persona == "developer"
        # Other fields unchanged
        assert parsed.goal == story.goal

    def test_invalid_points_falls_back(self):
        stories, _ = _make_test_stories()
        story = stories[0]
        text = _story_to_text(story)
        text = text.replace("Points: 3", "Points: 99")
        parsed = _parse_edited_story(text, story)
        assert parsed.story_points == story.story_points  # falls back to original

    def test_preserves_dod_and_ids(self):
        stories, _ = _make_test_stories()
        story = stories[0]
        text = _story_to_text(story)
        parsed = _parse_edited_story(text, story)
        assert parsed.dod_applicable == story.dod_applicable
        assert parsed.id == story.id
        assert parsed.feature_id == story.feature_id

    def test_preserves_title(self):
        # Title isn't part of the editable buffer — an edit must not reset it.
        from dataclasses import replace

        stories, _ = _make_test_stories()
        story = replace(stories[0], title="Create Bookmark Endpoint")
        parsed = _parse_edited_story(_story_to_text(story), story)
        assert parsed.title == "Create Bookmark Endpoint"


# ---------------------------------------------------------------------------
# Editor key handling
# ---------------------------------------------------------------------------


class TestEditStory:
    def test_esc_returns_none(self):
        """Pressing Esc should cancel and return None."""
        live = MagicMock()
        console = _make_console()
        stories, _ = _make_test_stories()
        keys = iter(["esc"])
        result = edit_story(live, console, stories[0], lambda: next(keys), width=80, height=24)
        assert result is None

    def test_ctrl_s_saves(self):
        """Pressing Ctrl+S immediately should return an unmodified story."""
        live = MagicMock()
        console = _make_console()
        stories, _ = _make_test_stories()
        keys = iter(["ctrl+s"])
        result = edit_story(live, console, stories[0], lambda: next(keys), width=80, height=24)
        assert result is not None
        assert result.persona == stories[0].persona
        assert result.goal == stories[0].goal

    def test_edit_and_save(self):
        """Type some characters then save."""
        live = MagicMock()
        console = _make_console()
        stories, _ = _make_test_stories()
        # Cursor starts at editable region of "Persona: user" (after "Persona: ")
        # Move to end of the value, type "s" → "users"
        keys = iter(["end", "s", "ctrl+s"])
        result = edit_story(live, console, stories[0], lambda: next(keys), width=80, height=24)
        assert result is not None
        assert result.persona == "users"


# ---------------------------------------------------------------------------
# Pipeline screen with custom actions
# ---------------------------------------------------------------------------


class TestEditStoryAddCriteria:
    def test_enter_on_marker_adds_ac(self):
        """Pressing Enter on the [+ Add Criteria] marker should add a new AC template."""
        live = MagicMock()
        console = _make_console()
        stories, _ = _make_test_stories()
        story = stories[0]  # has 1 AC
        # Navigate down to the marker then press Enter to add a new AC.
        # Buffer rows: Persona(0), Goal(1), Benefit(2), blank(3), Points(4),
        # Priority(5), Discipline(6), blank(7), header(8), Given(9), When(10),
        # Then(11), blank(12), marker(13), blank(14), DoD header(15), DoD×7(16-22).
        # 9 effective downs reach the marker; use 10 to be safe (extra is no-op
        # since DoD items follow and we want to stay on marker).
        # Navigate exactly: 9 downs reach marker, then up 0 times needed.
        # Strategy: go down 9 to land on marker, then enter + save.
        keys = iter(["down"] * 9 + ["enter", "ctrl+s"])
        result = edit_story(live, console, story, lambda: next(keys), width=80, height=30)
        assert result is not None
        assert len(result.acceptance_criteria) == 2
        # The new AC should have empty fields
        assert result.acceptance_criteria[1].given == ""

    def test_round_trip_with_marker(self):
        """The add-criteria marker should not appear in parsed output."""
        from yeaboi.ui.session.editor._editor import _story_to_text

        stories, _ = _make_test_stories()
        text = _story_to_text(stories[0])
        assert "Add Criteria" in text
        parsed = _parse_edited_story(text, stories[0])
        # Marker should not affect parsing
        assert len(parsed.acceptance_criteria) == len(stories[0].acceptance_criteria)


class TestBuildPipelineScreenActions:
    def test_default_actions(self):
        """Default actions should be Accept/Edit/Export."""
        result = _build_pipeline_screen(
            "Generating stories",
            "[3/5]",
            ["Story 1"],
            0,
            0,
            status="complete",
            width=80,
            height=24,
        )
        assert isinstance(result, Panel)

    def test_custom_actions(self):
        """Custom actions list (story stage) should render without error."""
        result = _build_pipeline_screen(
            "Generating stories",
            "[3/5]",
            ["Story 1"],
            0,
            0,
            status="complete",
            width=80,
            height=24,
            actions=["Accept", "Edit", "Regenerate", "Export"],
        )
        assert isinstance(result, Panel)
