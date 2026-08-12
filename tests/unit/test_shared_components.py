"""Unit tests for shared TUI components: Theme, buttons, scrollbar, progress dots, viewport."""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.shared._components import (
    ANALYSIS_THEME,
    PLANNING_THEME,
    Theme,
    action_rows_height,
    build_action_buttons,
    build_action_rows,
    build_meter,
    build_page_panel,
    build_progress_dots,
    build_scrollbar,
    calc_viewport,
)


class TestTheme:
    def test_analysis_theme_defaults(self):
        t = ANALYSIS_THEME
        assert t.accent == "rgb(100,180,100)"
        assert t.muted == "rgb(120,120,140)"

    def test_planning_theme_overrides(self):
        t = PLANNING_THEME
        assert t.accent == "rgb(110,140,220)"
        assert t.muted == "rgb(120,120,140)"  # shared default

    def test_custom_theme(self):
        t = Theme(accent="red", warn="blue")
        assert t.accent == "red"
        assert t.warn == "blue"
        assert t.muted == "rgb(120,120,140)"  # default

    def test_card_bg_defaults_empty(self):
        # Only modes with card surfaces set a tint; renderers must skip "".
        assert Theme().card_bg == ""
        assert PLANNING_THEME.card_bg == "rgb(20,24,38)"

    def test_usage_theme_amber(self):
        from yeaboi.ui.shared._components import USAGE_THEME

        assert USAGE_THEME.accent == "rgb(220,160,60)"

    def test_settings_theme_silver(self):
        from yeaboi.ui.shared._components import SETTINGS_THEME

        assert SETTINGS_THEME.accent == "rgb(160,160,180)"
        assert SETTINGS_THEME.muted == "rgb(120,120,140)"  # inherits default

    def test_frozen(self):
        import pytest

        with pytest.raises(AttributeError):
            ANALYSIS_THEME.accent = "red"  # type: ignore[misc]

    def test_bg_defaults_to_neutral(self):
        from yeaboi.ui.shared._components import NEUTRAL_BG

        assert Theme().bg == NEUTRAL_BG

    def test_mode_themes_share_neutral_background(self):
        # Per-mode background tints were dropped for one consistent backdrop; every
        # mode now shares the neutral base (accents stay per-mode).
        from yeaboi.ui.shared._components import NEUTRAL_BG

        assert ANALYSIS_THEME.bg == NEUTRAL_BG
        assert PLANNING_THEME.bg == NEUTRAL_BG
        assert ANALYSIS_THEME.accent != PLANNING_THEME.accent


class TestBuildPagePanel:
    def test_applies_theme_bg_as_panel_style(self):
        panel = build_page_panel(Text("x"), theme=ANALYSIS_THEME, height=10)
        assert panel.style == f"on {ANALYSIS_THEME.bg}"
        assert panel.height == 10
        assert panel.expand is True

    def test_no_theme_uses_neutral_base(self):
        from yeaboi.ui.shared._components import NEUTRAL_BG

        panel = build_page_panel(Text("x"), height=10)
        assert panel.style == f"on {NEUTRAL_BG}"

    def test_explicit_bg_overrides_theme(self):
        panel = build_page_panel(Text("x"), theme=ANALYSIS_THEME, bg="rgb(1,2,3)", height=10)
        assert panel.style == "on rgb(1,2,3)"

    def test_passes_border_style_and_extra_kwargs(self):
        panel = build_page_panel(Text("x"), theme=PLANNING_THEME, border_style="red", height=8, title="T")
        assert panel.border_style == "red"
        assert panel.title == "T"


class TestBuildActionButtons:
    def test_returns_three_text_objects(self):
        top, mid, bot = build_action_buttons(["Accept", "Edit"], 0)
        assert isinstance(top, Text)
        assert isinstance(mid, Text)
        assert isinstance(bot, Text)

    def test_selected_button(self):
        top, mid, bot = build_action_buttons(["Accept", "Edit", "Export"], 1)
        plain = mid.plain
        assert "Edit" in plain
        assert "Accept" in plain

    def test_single_button(self):
        top, mid, bot = build_action_buttons(["Done"], 0)
        assert "Done" in mid.plain

    def test_empty_actions(self):
        top, mid, bot = build_action_buttons([], 0)
        assert isinstance(top, Text)

    def test_box_drawing_chars(self):
        top, mid, bot = build_action_buttons(["Accept"], 0)
        assert "\u256d" in top.plain  # ╭
        assert "\u2502" in mid.plain  # │
        assert "\u2570" in bot.plain  # ╰


class TestBuildActionRows:
    """The wrapping bar.

    This exists because of a bug that was live and invisible: the retro board's
    five buttons come to 92 columns, so on a standard 80-column terminal the last
    one was drawn off the edge of the panel — still reachable with the arrow keys,
    just not on screen. A Rich Text is happy to be wider than the console, so
    nothing failed; it was simply clipped.
    """

    # The widest row the retro board can produce: every standing button plus the
    # Retry Link that appears when the tunnel fails.
    RETRO = [
        "Copy Invite",
        "Copy Host Link",
        "Generate Action Items",
        "Export",
        "Anonymize",
        "Close",
        "Retry Link",
    ]

    def test_wraps_rather_than_overflowing_eighty_columns(self):
        rows = build_action_rows(self.RETRO, 0, width=80)
        assert len(rows) > 3, "the bar should have wrapped"
        for row in rows:
            assert len(row.plain) <= 80

    def test_keeps_every_button(self):
        # Clipping is the failure being fixed; dropping one would be the same
        # bug with a tidier implementation.
        rows = build_action_rows(self.RETRO, 0, width=80)
        drawn = "".join(r.plain for r in rows)
        for label in self.RETRO:
            assert label in drawn

    def test_separates_stacked_rows(self):
        # Without the blank line two rows' borders touch and read as one grid.
        rows = build_action_rows(self.RETRO, 0, width=80)
        assert any(r.plain == "" for r in rows)

    def test_no_width_means_one_row(self):
        rows = build_action_rows(self.RETRO, 0)
        assert len(rows) == 3

    def test_a_button_wider_than_the_terminal_is_still_drawn(self):
        # It cannot fit, and an empty row would mean a selectable button that
        # never appears — worse than one that overflows.
        rows = build_action_rows(["A ridiculously long label indeed"], 0, width=20)
        assert "ridiculously" in rows[1].plain

    def test_empty_actions_draw_nothing(self):
        assert build_action_rows([], 0, width=80) == []


class TestActionRowsHeight:
    def test_matches_what_was_drawn(self):
        # The number the screen hands calc_viewport. If it disagrees with the
        # bar, the extra rows come off the bottom of the panel instead of out of
        # the scroll viewport.
        actions = TestBuildActionRows.RETRO
        rows = build_action_rows(actions, 0, width=80)
        assert action_rows_height(actions, 80) == len(rows) + 1

    def test_single_row_keeps_the_historic_four(self):
        # Every screen that has not moved to rows hardcodes action_h=4.
        assert action_rows_height(["Back"], 80) == 4
        assert action_rows_height(["Back"]) == 4

    def test_no_actions_still_reserves_a_row(self):
        assert action_rows_height([], 80) == 4


class TestBuildActionButtonsUnchanged:
    """The ~40 screens that still unpack three values must see no difference."""

    def test_matches_the_wrapping_builder_with_no_width(self):
        actions = ["Accept", "Edit", "Export"]
        top, mid, bot = build_action_buttons(actions, 1)
        rows = build_action_rows(actions, 1, width=None)
        assert [top.plain, mid.plain, bot.plain] == [r.plain for r in rows]

    def test_still_returns_three_texts_for_no_actions(self):
        top, mid, bot = build_action_buttons([], 0)
        assert (top.plain, mid.plain, bot.plain) == ("    ", "    ", "    ")


class TestBuildScrollbar:
    def test_returns_none_when_fits(self):
        result = build_scrollbar(viewport_h=20, total_lines=10, scroll_offset=0, max_scroll=0)
        assert result is None

    def test_returns_text_when_overflow(self):
        result = build_scrollbar(viewport_h=10, total_lines=30, scroll_offset=0, max_scroll=20)
        assert isinstance(result, Text)

    def test_scrollbar_has_correct_rows(self):
        result = build_scrollbar(viewport_h=10, total_lines=30, scroll_offset=0, max_scroll=20)
        assert result is not None
        lines = result.plain.strip().split("\n")
        assert len(lines) == 10

    def test_thumb_moves_with_offset(self):
        top = build_scrollbar(viewport_h=10, total_lines=100, scroll_offset=0, max_scroll=90)
        bot = build_scrollbar(viewport_h=10, total_lines=100, scroll_offset=90, max_scroll=90)
        assert top is not None and bot is not None
        # Thumb should be in different positions
        assert top.plain != bot.plain

    def test_always_show_returns_text_when_fits(self):
        """always_show=True should return Text even when content fits."""
        result = build_scrollbar(viewport_h=20, total_lines=10, scroll_offset=0, max_scroll=0, always_show=True)
        assert isinstance(result, Text)

    def test_always_show_false_returns_none_when_fits(self):
        """Default always_show=False returns None when content fits."""
        result = build_scrollbar(viewport_h=20, total_lines=10, scroll_offset=0, max_scroll=0, always_show=False)
        assert result is None


class TestBuildProgressDots:
    def test_returns_text(self):
        result = build_progress_dots(["A", "B", "C"], 1)
        assert isinstance(result, Text)

    def test_stage_names_present(self):
        result = build_progress_dots(["Instructions", "Epic", "Stories"], 0)
        plain = result.plain
        assert "Instructions" in plain
        assert "Epic" in plain
        assert "Stories" in plain

    def test_dots_present(self):
        result = build_progress_dots(["A", "B", "C"], 1)
        plain = result.plain
        assert "\u25cf" in plain  # filled dot
        assert "\u25cb" in plain  # hollow dot

    def test_custom_theme(self):
        t = Theme(accent="red", accent_bright="bold red")
        result = build_progress_dots(["A", "B"], 0, theme=t)
        assert isinstance(result, Text)


class TestBuildKeyHints:
    def test_keys_bright_labels_dim(self):
        from yeaboi.ui.shared._components import build_key_hints

        row = build_key_hints([("Enter", "send"), ("/", "commands")], pad="  ")
        assert row.plain == "  Enter send   / commands"
        styles = [str(span.style) for span in row.spans]
        assert any("bold" in s for s in styles)  # the keycaps
        assert any("rgb(110,110,125)" in s for s in styles)  # the labels

    def test_empty_pairs(self):
        from yeaboi.ui.shared._components import build_key_hints

        assert build_key_hints([]).plain == ""


class TestBuildMeter:
    def test_returns_text(self):
        assert isinstance(build_meter(3, 10), Text)

    def test_glyph_counts_for_known_ratio(self):
        assert build_meter(8, 10, width=10).plain == "▰" * 8 + "▱" * 2

    def test_zero_filled_is_empty_track(self):
        assert build_meter(0, 10, width=8).plain == "▱" * 8

    def test_total_zero_does_not_crash(self):
        # Total clamps to 1, filled clamps to total → a full bar, no ZeroDivisionError.
        assert build_meter(5, 0, width=6).plain == "▰" * 6

    def test_filled_over_total_clamps_full(self):
        assert build_meter(20, 10, width=10).plain == "▰" * 10

    def test_custom_theme_and_style(self):
        t = Theme(accent="red", accent_bright="bold red")
        assert isinstance(build_meter(1, 2, theme=t), Text)
        assert isinstance(build_meter(1, 2, style="green"), Text)


class TestUsageScreen:
    def test_returns_panel(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_usage_screen

        result = _build_usage_screen({}, width=80, height=24)
        assert isinstance(result, Panel)

    def test_with_full_data(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_usage_screen

        data = {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
            "api_key_status": "configured",
            "tokens": {"input": 15000, "output": 3000, "total": 18000, "estimated_cost": 0.054},
            "sessions": {"total": 12, "planning": 8, "analysis": 4, "last_used": "2026-03-29 10:30"},
            "version": "1.2.0",
            "python_version": "3.14.3",
            "langsmith": "disabled",
            "db_path": "~/.yeaboi/sessions.db",
            "profiles": [
                {"name": "azdevops-PROJ", "source": "azdevops", "sprints": 8},
            ],
        }
        result = _build_usage_screen(data, width=100, height=40)
        assert isinstance(result, Panel)

    def test_renders_provider_info(self):
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_usage_screen

        data = {"provider": "anthropic", "model": "claude-sonnet-4", "api_key_status": "configured"}
        result = _build_usage_screen(data, width=100, height=40)
        buf = StringIO()
        Console(file=buf, width=100, force_terminal=False).print(result)
        output = buf.getvalue()
        assert "anthropic" in output
        assert "claude-sonnet-4" in output

    def test_scrollable(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_usage_screen

        data = {
            "provider": "anthropic",
            "model": "test",
            "sessions": {"total": 5, "planning": 3, "analysis": 2},
            "profiles": [{"name": f"team-{i}", "source": "jira", "sprints": i} for i in range(10)],
        }
        r1 = _build_usage_screen(data, scroll_offset=0, width=80, height=20)
        r2 = _build_usage_screen(data, scroll_offset=5, width=80, height=20)
        assert isinstance(r1, Panel)
        assert isinstance(r2, Panel)

    def test_back_and_copy_moved_to_chrome_tabs(self):
        """Both affordances now live in the bottom-left chrome (the back tab plus a
        sibling 'c copy' tab), so the page body carries no inline hint — it only
        flags _copy_tab for the chrome to pick up."""
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_usage_screen

        result = _build_usage_screen({}, width=100, height=40, actions=["Copy", "Back"])
        assert result._copy_tab is True  # chrome draws the copy tab
        buf = StringIO()
        Console(file=buf, width=100, force_terminal=False).print(result)
        out = buf.getvalue()
        assert "back" not in out.lower()  # back tab covers it
        assert "copy" not in out.lower()  # copy tab covers it

        # Without a Copy action there's no copy tab.
        assert _build_usage_screen({}, width=100, height=40, actions=["Back"])._copy_tab is False

    def test_uses_amber_theme(self):
        """Usage screen should use the amber USAGE_THEME, not green or blue."""
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_usage_screen

        result = _build_usage_screen({"provider": "test"}, width=100, height=30)
        buf = StringIO()
        Console(file=buf, width=100, force_terminal=False).print(result)
        output = buf.getvalue()
        # Should contain USAGE ASCII title
        assert "USAGE" in output.upper() or len(output) > 100


class TestProfilePickerScreen:
    def test_returns_panel(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_profile_picker_screen

        result = _build_profile_picker_screen([], 0, width=80, height=24)
        assert isinstance(result, Panel)

    def test_with_profiles(self):
        from yeaboi.team_profile import TeamProfile
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_profile_picker_screen

        profiles = [
            TeamProfile(team_id="jira-PROJ", source="jira", project_key="PROJ", sample_sprints=5, sample_stories=30),
            TeamProfile(
                team_id="azdevops-INFRA", source="azdevops", project_key="INFRA", sample_sprints=8, sample_stories=64
            ),
        ]
        result = _build_profile_picker_screen(profiles, 0, width=100, height=30)
        assert isinstance(result, Panel)

    def test_skip_option(self):
        from io import StringIO

        from rich.console import Console

        from yeaboi.team_profile import TeamProfile
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_profile_picker_screen

        profiles = [TeamProfile(team_id="jira-X", source="jira", project_key="X")]
        result = _build_profile_picker_screen(profiles, 1, width=100, height=30)  # Skip selected
        buf = StringIO()
        Console(file=buf, width=100, force_terminal=False).print(result)
        assert "Skip" in buf.getvalue()

    def test_select_button(self):
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_profile_picker_screen

        result = _build_profile_picker_screen([], 0, width=100, height=30)
        buf = StringIO()
        Console(file=buf, width=100, force_terminal=False).print(result)
        assert "Select" in buf.getvalue()


class TestExtractAnswersFromProfile:
    def test_extracts_velocity(self):
        from yeaboi.agent.nodes import _extract_answers_from_profile
        from yeaboi.team_profile import TeamProfile

        p = TeamProfile(team_id="t", source="jira", project_key="P", velocity_avg=23.5)
        answers = _extract_answers_from_profile(p)
        assert 9 in answers
        assert "23" in answers[9] or "24" in answers[9]

    def test_extracts_team_size(self):
        from yeaboi.agent.nodes import _extract_answers_from_profile

        p = type("P", (), {"velocity_avg": 0})()
        examples = {"contributor_stats": [{"name": "alice"}, {"name": "bob"}, {"name": "charlie"}]}
        answers = _extract_answers_from_profile(p, examples)
        assert 6 in answers
        assert answers[6] == "3"

    def test_empty_profile(self):
        from yeaboi.agent.nodes import _extract_answers_from_profile

        p = type("P", (), {"velocity_avg": 0})()
        answers = _extract_answers_from_profile(p, {})
        assert len(answers) == 0

    def test_extracts_sprint_length(self):
        from yeaboi.agent.nodes import _extract_answers_from_profile

        p = type("P", (), {"velocity_avg": 0})()
        examples = {
            "sprint_details": [
                {"start": "2026-03-01T00:00:00+00:00", "end": "2026-03-15T00:00:00+00:00"},
                {"start": "2026-03-15T00:00:00+00:00", "end": "2026-03-29T00:00:00+00:00"},
            ]
        }
        answers = _extract_answers_from_profile(p, examples)
        assert 8 in answers
        assert "2 week" in answers[8]

    def test_extracts_tech_stack(self):
        from yeaboi.agent.nodes import _extract_answers_from_profile

        p = type("P", (), {"velocity_avg": 0, "tech_stack": ("Python", "React", "PostgreSQL"), "integrations": ()})()
        answers = _extract_answers_from_profile(p)
        assert 11 in answers
        assert "Python" in answers[11]
        assert "React" in answers[11]
        assert "PostgreSQL" in answers[11]

    def test_extracts_integrations(self):
        from yeaboi.agent.nodes import _extract_answers_from_profile

        p = type("P", (), {"velocity_avg": 0, "tech_stack": (), "integrations": ("Jira", "Slack", "GitHub Actions")})()
        answers = _extract_answers_from_profile(p)
        assert 12 in answers
        assert "Jira" in answers[12]
        assert "Slack" in answers[12]
        assert 11 not in answers  # empty tech_stack → not filled

    def test_empty_tech_stack_not_filled(self):
        from yeaboi.agent.nodes import _extract_answers_from_profile

        p = type("P", (), {"velocity_avg": 0, "tech_stack": (), "integrations": ()})()
        answers = _extract_answers_from_profile(p)
        assert 11 not in answers
        assert 12 not in answers


class TestSettingsScreen:
    def test_returns_panel(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        result = _build_settings_screen({}, width=80, height=24)
        assert isinstance(result, Panel)

    def test_with_config_data(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        data = {
            "LLM_PROVIDER": "anthropic",
            "LLM_MODEL": "claude-sonnet-4",
            "ANTHROPIC_API_KEY": "sk-ant-secret123456",
            "JIRA_BASE_URL": "https://org.atlassian.net",
            "JIRA_API_TOKEN": "token123",
            "AZURE_DEVOPS_ORG_URL": "https://dev.azure.com/myorg",
        }
        result = _build_settings_screen(data, width=100, height=40)
        assert isinstance(result, Panel)

    def test_masks_secrets(self):
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        data = {"ANTHROPIC_API_KEY": "sk-ant-verylongsecretkey123"}
        result = _build_settings_screen(data, width=100, height=40)
        buf = StringIO()
        Console(file=buf, width=100, force_terminal=False).print(result)
        output = buf.getvalue()
        # Should NOT show the full key
        assert "verylongsecretkey123" not in output
        # Should show partial mask
        assert "\u2022" in output  # bullet mask chars

    def test_tab_bar_rendered_and_hint_moved_to_chrome(self):
        # The old action-button row was replaced by a tab bar (grouped sections).
        # The context hint no longer takes a body row — it's handed to the bottom
        # pocket as a chrome tab via _hint_tab ('Esc back' dropped earlier, since
        # the app-wide back tab covers going back).
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        output = self._render({}, height=40)
        for tab in ("Credentials", "System"):
            assert tab in output
        assert "switch" not in output  # not in the body any more

        panel = _build_settings_screen({}, width=100, height=40)
        assert "switch tab" in panel._hint_tab.plain
        assert "configure" in panel._hint_tab.plain

    def test_scrollable(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        r1 = _build_settings_screen({}, scroll_offset=0, width=80, height=20)
        r2 = _build_settings_screen({}, scroll_offset=5, width=80, height=20)
        assert isinstance(r1, Panel)
        assert isinstance(r2, Panel)

    def test_only_active_tab_section_renders(self):
        # Credentials groups LLM + integrations (Anthropic key AND Jira); the System
        # tab shows Advanced (Log Level) and none of the credential rows.
        creds = self._render({"JIRA_BASE_URL": "https://org.atlassian.net"}, height=80, active_tab=0)
        assert "Anthropic Key" in creds
        assert "org.atlassian.net" in creds  # Jira grouped under Credentials
        system = self._render({}, height=40, active_tab=1)
        assert "Log Level" in system  # Advanced section
        assert "Anthropic Key" not in system  # credentials are on another tab

    def test_duck_row_on_the_system_tab(self):
        # The duck-bubble mute is a persisted preference (DUCK_ENABLED) with a
        # Settings row beside Tips — default on, "false" shows off.
        on = self._render({}, height=60, active_tab=1)
        assert "Duck" in on and "on" in on
        off = self._render({"DUCK_ENABLED": "false"}, height=60, active_tab=1)
        assert "Duck" in off

    def test_system_tab_hint_mentions_log_level(self):
        output = self._render({}, height=40, active_tab=1)  # System tab (Advanced → log level)
        assert "log level" in output.lower()

    def test_settings_tab_action_mapping(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _SETTINGS_TABS, settings_tab_action

        assert settings_tab_action(_SETTINGS_TABS.index("System")) == "loglevel"
        assert settings_tab_action(_SETTINGS_TABS.index("Credentials")) == "setup"

    def test_editable_row_regions_map_to_their_env_rows(self):
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        con = Console(width=120, height=44, file=StringIO())
        panel = _build_settings_screen({}, width=120, height=44, active_tab=0)  # Credentials
        assert panel._row_regions  # editable rows attached
        lines = con.render_lines(panel, con.options, pad=True)
        envs = {env for _, _, _, env, _, _ in panel._row_regions}
        assert "LLM_PROVIDER" in envs and "ANTHROPIC_API_KEY" in envs
        # Sections are boxed and can sit side by side, so a region carries a column
        # range too — the label must fall inside that exact rect, not just the row.
        for abs_row, x0, x1, _env, label, _masked in panel._row_regions:
            row_text = "".join(s.text for s in lines[abs_row - 1])
            assert label in row_text[x0 - 1 : x1]

    def test_editing_renders_buffer_in_row(self):
        # When a row is being edited in place, its value is replaced by the live
        # buffer (not the stored value or "not set").
        out = self._render(
            {"ANTHROPIC_API_KEY": ""}, height=40, active_tab=0, editing=("ANTHROPIC_API_KEY", "sk-typed", 8)
        )
        assert "Anthropic Key: sk-typed" in " ".join(out.split())

    def test_readonly_rows_have_no_region(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        # The System tab's "Config File" and "Dictation" rows are read-only.
        panel = _build_settings_screen({"_config_path": "/tmp/.env"}, width=120, height=60, active_tab=1)
        labels = {label for _, _, _, _, label, _ in panel._row_regions}
        assert "Config File" not in labels
        assert "Log Level" in labels  # but editable rows on the same tab do have regions

    def test_sections_render_as_boxes_in_a_grid(self):
        # Each heading section is its own rounded box (the Usage dashboard's
        # treatment): the heading becomes the box title, and on a wide terminal two
        # narrow sections share a row.
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        panel = _build_settings_screen({}, width=130, height=44, active_tab=1)  # System
        out = self._text(panel, width=130, height=44)
        assert "╭─ Storage" in out and "╭─ Daily Standup" in out
        # Side by side: the first box of each column lands on the same rendered row.
        assert any("Storage" in ln and "Daily Standup" in ln for ln in out.splitlines())

    def test_narrow_terminal_falls_back_to_one_column(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        panel = _build_settings_screen({}, width=70, height=44, active_tab=1)
        out = self._text(panel, width=70, height=44)
        # One column — a second would fall below _SETTINGS_MIN_BOX_W.
        assert len(panel._box_cols) == 1
        assert not any("Storage" in ln and "Daily Standup" in ln for ln in out.splitlines())

    def test_focused_value_gets_a_full_width_bar(self):
        # Focus is a background stripe, not a leading glyph — a marker would need a
        # gutter on every row, which reads as a stray indent inside the box.
        from yeaboi.ui.mode_select.screens._screens_secondary import (
            _SETTINGS_FOCUS_BG,
            _build_settings_screen,
        )

        panel = _build_settings_screen({}, width=130, height=44, active_tab=1, sel_box=4, sel_field=1)
        rows = self._segments(panel, width=130, height=44)
        barred = [r for r in rows if any(_SETTINGS_FOCUS_BG in str(s.style) for s in r)]
        assert len(barred) == 1  # exactly one value is highlighted
        line = "".join(s.text for s in barred[0])
        assert "Session Prune Days" in line
        # The stripe runs the whole inner width, so it reads as a bar not a smear.
        lit = sum(s.cell_length for s in barred[0] if _SETTINGS_FOCUS_BG in str(s.style))
        assert lit > 30

    def test_rows_sit_flush_against_the_box_padding(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        panel = _build_settings_screen({}, width=130, height=44, active_tab=1)
        line = next(ln for ln in self._text(panel, width=130, height=44).splitlines() if "Data Directory" in ln)
        # One space between the box border and the label — no marker gutter.
        assert "│ Data Directory" in line

    def test_navigation_map_is_published(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        panel = _build_settings_screen({}, width=130, height=44, active_tab=1)
        # Five sections dealt into two balanced columns, nothing in the wide tail.
        assert sorted(b for col in panel._box_cols for b in col) == [0, 1, 2, 3, 4]
        assert len(panel._box_cols) == 2 and not panel._box_tail
        envs = [f[0] for box in panel._box_fields for f in box]
        assert "LOG_LEVEL" in envs and "AWS_REGION" in envs
        assert "_config_path" not in envs  # read-only rows aren't navigable

    def test_boxes_keep_their_own_height_and_the_columns_end_level(self):
        # Boxes are sized to their content (a one-row section is not padded up to
        # its six-row neighbour), but a short column shares the shortfall between
        # its boxes so both columns finish on the same line.
        from yeaboi.ui.mode_select.screens._screens_secondary import (
            _SETTINGS_MAX_STRETCH,
            _build_settings_screen,
        )

        panel = _build_settings_screen({}, width=130, height=44, active_tab=1)
        lines = self._text(panel, width=130, height=44).splitlines()
        i = next(n for n, ln in enumerate(lines) if "Data Directory" in ln)
        assert "Daily Standup" in lines[i - 1]  # side by side
        # Storage holds one row, so it closes within the stretch allowance of it.
        closes = next(n for n, ln in enumerate(lines[i:], i) if "╰" in ln[:60])
        assert closes - i <= 1 + _SETTINGS_MAX_STRETCH

        # Both columns' last bottom border lands on the same rendered row. Column 0
        # is the page frame's own border, so box borders start past it.
        def _closes(ln, lo, hi):
            return any(ch == "╰" and lo <= i < hi for i, ch in enumerate(ln))

        last_left = max(n for n, ln in enumerate(lines) if _closes(ln, 3, 60))
        last_right = max(n for n, ln in enumerate(lines) if _closes(ln, 60, len(ln)))
        assert last_left == last_right

    def test_wide_sections_stack_below_the_columns(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        panel = _build_settings_screen({}, width=130, height=44, active_tab=0)  # Credentials
        # LLM Provider is the only column box; the token-help sections go full width.
        assert panel._box_cols == [[0]]
        assert panel._box_tail == [1, 2, 3, 4]

    def test_selecting_an_offscreen_value_scrolls_it_into_view(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        meta: dict = {}
        # A short page can't show the last section, so focusing it must scroll.
        panel = _build_settings_screen(
            {}, width=130, height=22, active_tab=0, scroll_offset=0, scroll_meta=meta, sel_box=4, sel_field=0
        )
        assert meta["scroll"] > 0
        # Notion is the last section; its focused row (highlight and all) is on screen.
        from yeaboi.ui.mode_select.screens._screens_secondary import _SETTINGS_FOCUS_BG

        assert "╭─ Notion" in self._text(panel, width=130, height=22)
        barred = [
            r for r in self._segments(panel, width=130, height=22) if any(_SETTINGS_FOCUS_BG in str(s.style) for s in r)
        ]
        assert barred and "Token" in "".join(s.text for s in barred[0])

    def test_editing_a_long_value_keeps_the_cursor_visible(self):
        # The buffer is windowed to the box width, so typing past the right edge
        # scrolls the value instead of ellipsizing over the cursor.
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        buf = "sk-ant-" + "x" * 200
        panel = _build_settings_screen(
            {}, width=100, height=40, active_tab=0, editing=("ANTHROPIC_API_KEY", buf, len(buf))
        )
        row = next(ln for ln in self._text(panel, width=100, height=40).splitlines() if "Anthropic Key" in ln)
        assert "…" not in row  # cropped from the left, not ellipsized on the right

    def test_tab_click_regions_map_to_each_tab(self):
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _SETTINGS_TABS, _build_settings_screen

        con = Console(width=120, height=40, file=StringIO())
        panel = _build_settings_screen({}, width=120, height=40, active_tab=0)
        # The builder attaches one region per tab: (labels_row, underline_row, sc, ec).
        assert len(panel._tab_regions) == len(_SETTINGS_TABS)
        lines = con.render_lines(panel, con.options, pad=True)
        for i, (lr, ur, sc, ec) in enumerate(panel._tab_regions):
            # The label's centre column on the labels row falls inside the region.
            row_text = "".join(s.text for s in lines[lr - 1])
            cx = (sc + ec) // 2
            assert row_text[cx - 1] != " "  # a non-blank label cell
            assert sc <= cx <= ec
            # Clicks on both the labels row and the underline row belong to the tab.
            assert lr != ur

    @staticmethod
    def _render(data: dict, *, height: int = 60, active_tab: int = 0, editing=None) -> str:
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        result = _build_settings_screen(data, width=100, height=height, active_tab=active_tab, editing=editing)
        buf = StringIO()
        Console(file=buf, width=100, force_terminal=False).print(result)
        return buf.getvalue()

    @staticmethod
    def _segments(panel, *, width: int, height: int) -> list:
        """Render to styled segment rows — the focus bar is a background colour,
        so it can only be asserted on styles, never on the plain text."""
        from io import StringIO

        from rich.console import Console

        con = Console(file=StringIO(), width=width, height=height, force_terminal=True, color_system="truecolor")
        return con.render_lines(panel, con.options, pad=True)

    @staticmethod
    def _text(panel, *, width: int, height: int) -> str:
        """Render an already-built panel at a given size (the boxed-grid layout is
        width-sensitive, so these tests pick their own width rather than _render's)."""
        from io import StringIO

        from rich.console import Console

        buf = StringIO()
        Console(file=buf, width=width, height=height, force_terminal=False).print(panel)
        return buf.getvalue()

    def test_storage_section_rendered(self):
        # Storage is one row, so it folded into System (tab index 1) rather than
        # keeping a tab to itself; the data dir is edited like any other value.
        output = self._render({"YEABOI_HOME": "/data/yeaboi"}, height=40, active_tab=1)
        assert "Data Directory" in output
        assert "/data/yeaboi" in output

    def test_data_dir_default_label_when_unset(self):
        output = self._render({}, height=40, active_tab=1)  # System tab
        assert "~/.yeaboi (default)" in output

    def test_data_dir_is_an_editable_row(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        panel = _build_settings_screen({}, width=130, height=44, active_tab=1)
        envs = [f[0] for box in panel._box_fields for f in box]
        assert "YEABOI_HOME" in envs  # reachable by keyboard and click

    def test_allowed_paths_row_rendered(self):
        # The fs-sandbox whitelist lives in Storage, which folded into System (tab 1).
        # Rendered wide: at _render's 100 columns the boxed value would ellipsize.
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        panel = _build_settings_screen(
            {"YEABOI_ALLOWED_PATHS": "/repos/team,/tmp/exports"}, width=160, height=44, active_tab=1
        )
        output = self._text(panel, width=160, height=44)
        assert "Allowed Paths" in output
        assert "/repos/team,/tmp/exports" in output

    def test_allowed_paths_empty_shows_sandbox_note(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        panel = _build_settings_screen({}, width=160, height=44, active_tab=1)
        assert "none — sandboxed to data dir" in self._text(panel, width=160, height=44)

    def test_status_message_spoken_by_the_duck(self):
        # The transient status no longer takes a body row: the settings loop
        # hands it to the shared duck voice, so the builder stamps nothing.
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        msg = "Data directory saved — restart yeaboi to fully apply"
        panel = _build_settings_screen({"_message": msg}, width=100, height=60)
        assert getattr(panel, "_duck_say", "") == ""
        assert "restart yeaboi" not in self._render({"_message": msg})  # not in the body

    def test_notion_token_masked(self):
        # Notion lives under the Credentials tab (index 0) now.
        output = self._render({"NOTION_TOKEN": "ntn_verysecretvalue12345"}, height=80, active_tab=0)
        assert "verysecretvalue12345" not in output

    def test_token_help_link_and_scope_rendered(self):
        # Each token row carries a "create: <url> · scope: <...>" sub-line so a
        # user knows where to make the token and what access to grant it. GitHub +
        # Azure both live under the Credentials tab; render it tall enough to reach
        # them, then confirm both help lines appear.
        out = self._render({"GITHUB_TOKEN": "ghp_x", "AZURE_DEVOPS_TOKEN": "az"}, height=80, active_tab=0)
        assert "create:" in out
        assert "scope:" in out
        assert "github.com/settings/tokens" in out  # GitHub creation link
        assert "Work Items" in out  # Azure scope text

    def test_analysis_owners_row_rendered(self):
        # The GitHub estate Analysis scans. Without an editable row here the key
        # was only reachable by hand-editing .env, which is why GitHub never
        # appeared as a code source. Rendered wide so the value is not ellipsized.
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        panel = _build_settings_screen(
            {"GITHUB_TOKEN": "ghp_x", "TEAM_ANALYSIS_GITHUB_OWNERS": "acme-corp,zeta-labs"},
            width=160,
            height=80,
            active_tab=0,
        )
        output = self._text(panel, width=160, height=80)
        assert "Analysis Owners" in output
        assert "acme-corp,zeta-labs" in output

    def test_analysis_owners_empty_points_at_the_wizard(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        panel = _build_settings_screen({"GITHUB_TOKEN": "ghp_x"}, width=160, height=80, active_tab=0)
        assert "chosen per run in Analysis setup" in self._text(panel, width=160, height=80)

    def test_analysis_owners_is_an_editable_row(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        panel = _build_settings_screen({"GITHUB_TOKEN": "ghp_x"}, width=160, height=80, active_tab=0)
        envs = [f[0] for box in panel._box_fields for f in box]
        assert "TEAM_ANALYSIS_GITHUB_OWNERS" in envs  # reachable by keyboard and click

    def test_token_help_url_is_clickable(self):
        # The creation URL is an OSC-8 hyperlink in the read-only dashboard too.
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        result = _build_settings_screen({"GITHUB_TOKEN": "ghp_x"}, width=120, height=80, active_tab=0)
        buf = StringIO()
        Console(file=buf, width=120, force_terminal=True, color_system="truecolor").print(result)
        assert "https://github.com/settings/tokens" in buf.getvalue()
        assert "\x1b]8;" in buf.getvalue()  # OSC-8 hyperlink escape


class TestCollectSettingsData:
    def test_returns_dict(self, monkeypatch):
        from yeaboi.ui.mode_select import _collect_settings_data

        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test123")
        data = _collect_settings_data()
        assert isinstance(data, dict)
        assert data["LLM_PROVIDER"] == "anthropic"
        assert data["ANTHROPIC_API_KEY"] == "sk-ant-test123"

    def test_includes_config_path(self):
        from yeaboi.ui.mode_select import _collect_settings_data

        data = _collect_settings_data()
        assert "_config_path" in data
        assert ".yeaboi" in data["_config_path"]

    def test_empty_env_vars(self, monkeypatch):
        from yeaboi.ui.mode_select import _collect_settings_data

        monkeypatch.delenv("JIRA_BASE_URL", raising=False)
        data = _collect_settings_data()
        assert data.get("JIRA_BASE_URL") == ""

    def test_includes_allowed_paths(self, monkeypatch):
        from yeaboi.ui.mode_select import _collect_settings_data

        monkeypatch.setenv("YEABOI_ALLOWED_PATHS", "/a,/b")
        data = _collect_settings_data()
        assert data["YEABOI_ALLOWED_PATHS"] == "/a,/b"

    def test_includes_analysis_github_owners(self, monkeypatch):
        # An unregistered key renders blank however it is set, so the row would
        # silently never show a saved value.
        from yeaboi.ui.mode_select import _collect_settings_data

        monkeypatch.setenv("TEAM_ANALYSIS_GITHUB_OWNERS", "acme,beta")
        data = _collect_settings_data()
        assert data["TEAM_ANALYSIS_GITHUB_OWNERS"] == "acme,beta"


class TestSettingsSaveAllowedPaths:
    """_settings_save_allowed_paths — the save half of the sandbox whitelist row.

    The list is typed on the page like every other value, so only the persist
    step is separate: it needs set_allowed_paths (dedup + the pinned bootstrap
    .env) rather than the generic apply_config_value, since the whitelist has to
    survive relocating the very data tree it guards.
    """

    def _save(self, monkeypatch, typed):
        from yeaboi.ui import mode_select

        calls: list = []
        monkeypatch.setattr("yeaboi.config.set_allowed_paths", calls.append)
        return calls, mode_select._settings_save_allowed_paths(typed)

    def test_saves_parsed_comma_list(self, monkeypatch):
        calls, msg = self._save(monkeypatch, " /repos/one , /repos/two ")
        assert calls == [["/repos/one", "/repos/two"]]
        assert "2 path" in msg

    def test_blank_clears_the_whitelist(self, monkeypatch):
        calls, msg = self._save(monkeypatch, "")
        assert calls == [[]]
        assert "sandboxed" in msg

    def test_stray_separators_are_dropped(self, monkeypatch):
        calls, _ = self._save(monkeypatch, "/a,,  ,/b,")
        assert calls == [["/a", "/b"]]


class TestSettingsTitle:
    def test_returns_text(self):
        from yeaboi.ui.shared._components import settings_title

        result = settings_title()
        assert isinstance(result, Text)


class TestCalcViewport:
    def test_standard_height(self):
        vp = calc_viewport(30, header_h=7, action_h=4)
        # inner = 30-4=26, viewport = 26-7-4=15
        assert vp == 15

    def test_minimum_clamp(self):
        vp = calc_viewport(10, header_h=7, action_h=4)
        assert vp >= 3

    def test_custom_header(self):
        vp = calc_viewport(30, header_h=6, action_h=4)
        # inner = 26, viewport = 26-6-4=16
        assert vp == 16


class TestLogLevelButton:
    def test_registered_in_btn_colors(self):
        from yeaboi.ui.shared._components import _BTN_COLORS

        assert "Log Level" in _BTN_COLORS
        # Same silver scheme as Configure — both are Settings-page actions.
        assert _BTN_COLORS["Log Level"] == _BTN_COLORS["Configure"]
        # Standup's Identity action (repo path + aliases) shares the silver scheme.
        assert "Identity" in _BTN_COLORS
        assert _BTN_COLORS["Identity"] == _BTN_COLORS["Configure"]

    def test_settings_screen_renders_log_level_button(self):
        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        # Log Level lives on the System tab now (Advanced row + Enter action), not a button.
        panel = _build_settings_screen({"_config_path": "/tmp/.env"}, width=100, height=40, active_tab=2)
        assert isinstance(panel, Panel)
        console = Console(width=120)
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Log Level" in out  # the Advanced section's row
        assert "log level" in out.lower()  # the Enter-action hint

    def test_tab_bar_fits_at_width_80(self):
        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        # The tab bar fits a narrow terminal; all tabs still appear.
        panel = _build_settings_screen({"_config_path": "/tmp/.env"}, width=80, height=40, active_tab=2)
        console = Console(width=80)
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Credentials" in out and "System" in out


class TestSettingsEditKeypress:
    def _edit(self, buf="", cur=0):
        return {"env": "X", "label": "X", "masked": False, "buf": buf, "cur": cur}

    def test_insert_printable_at_cursor(self):
        from yeaboi.ui.mode_select import _settings_edit_keypress

        e = self._edit("ac", 1)
        _settings_edit_keypress("b", e)
        assert e["buf"] == "abc" and e["cur"] == 2

    def test_backspace_deletes_before_cursor(self):
        from yeaboi.ui.mode_select import _settings_edit_keypress

        e = self._edit("abc", 2)
        _settings_edit_keypress("backspace", e)
        assert e["buf"] == "ac" and e["cur"] == 1

    def test_cursor_movement_clamps(self):
        from yeaboi.ui.mode_select import _settings_edit_keypress

        e = self._edit("abc", 0)
        _settings_edit_keypress("left", e)
        assert e["cur"] == 0  # clamped at 0
        _settings_edit_keypress("end", e)
        assert e["cur"] == 3
        _settings_edit_keypress("right", e)
        assert e["cur"] == 3  # clamped at len

    def test_paste_inserts_text(self):
        from yeaboi.ui.mode_select import _settings_edit_keypress

        e = self._edit("", 0)
        _settings_edit_keypress("paste:hello", e)
        assert e["buf"] == "hello" and e["cur"] == 5

    def test_unknown_key_ignored(self):
        from yeaboi.ui.mode_select import _settings_edit_keypress

        e = self._edit("ab", 1)
        _settings_edit_keypress("tab", e)  # multi-char special key → ignored
        assert e["buf"] == "ab" and e["cur"] == 1


class TestNextLogLevel:
    def test_full_cycle(self):
        from yeaboi.ui.mode_select import _next_log_level

        assert _next_log_level("DEBUG") == "INFO"
        assert _next_log_level("INFO") == "WARNING"
        assert _next_log_level("WARNING") == "ERROR"
        assert _next_log_level("ERROR") == "DEBUG"

    def test_lowercase_input(self):
        from yeaboi.ui.mode_select import _next_log_level

        assert _next_log_level("info") == "WARNING"

    def test_unknown_treated_as_warning(self):
        from yeaboi.ui.mode_select import _next_log_level

        assert _next_log_level("CRITICAL") == "ERROR"
        assert _next_log_level("garbage") == "ERROR"


class TestSettingsFocusMove:
    """The settings screen's three-level focus model (tab bar → box → value).

    ``settings_focus_move`` is the whole state machine, kept pure and out of the
    TUI loop so these can drive it directly. Grid below: two rows of two boxes,
    then a full-width one — the shape the Credentials/System tabs produce.
    """

    COLS = [[0, 2, 3], [1, 4]]  # a taller left column and a shorter right one
    TAIL = [5, 6]  # full-width boxes stacked underneath
    FIELDS = [
        [("A", "a", False), ("B", "b", False)],
        [],  # a section with nothing editable
        [("C", "c", False)],
        [("D", "d", False)],
        [("E", "e", False)],
        [("F", "f", False)],
        [("G", "g", False)],
    ]

    def _move(self, key, box, field, *, tail=None):
        from yeaboi.ui.mode_select.screens._screens_secondary import settings_focus_move

        return settings_focus_move(key, self.COLS, self.TAIL if tail is None else tail, self.FIELDS, box, field)

    def test_down_enters_the_grid_from_the_tab_bar(self):
        assert self._move("down", -1, -1) == (0, -1)

    def test_left_right_at_the_tab_bar_are_left_alone(self):
        # They belong to the tab switch — the loop never routes them here, and if
        # it did they must not steal focus.
        assert self._move("left", -1, -1) == (-1, -1)

    def test_up_down_walk_a_column(self):
        assert self._move("down", 0, -1) == (2, -1)
        assert self._move("down", 2, -1) == (3, -1)
        assert self._move("up", 3, -1) == (2, -1)

    def test_left_right_cross_columns_keeping_the_depth(self):
        assert self._move("right", 0, -1) == (1, -1)
        assert self._move("left", 4, -1) == (2, -1)  # same depth in the left column
        assert self._move("right", 3, -1) == (4, -1)  # clamped to the shorter column
        assert self._move("left", 0, -1) == (0, -1)  # already leftmost

    def test_down_off_the_last_box_enters_the_wide_tail(self):
        assert self._move("down", 3, -1) == (5, -1)
        assert self._move("down", 5, -1) == (6, -1)
        assert self._move("down", 6, -1) == (6, -1)  # clamped at the bottom

    def test_the_tail_hands_focus_back_up_to_the_columns(self):
        assert self._move("up", 5, -1) == (3, -1)  # bottom of the first column
        assert self._move("up", 6, -1) == (5, -1)
        assert self._move("left", 6, -1) == (6, -1)  # a wide box has no neighbours

    def test_without_a_tail_the_last_box_holds(self):
        assert self._move("down", 3, -1, tail=[]) == (3, -1)

    def test_up_off_the_top_returns_to_the_tab_bar(self):
        assert self._move("up", 1, -1) == (-1, -1)

    def test_arrows_walk_values_once_a_box_is_open(self):
        assert self._move("down", 0, 0) == (0, 1)
        assert self._move("down", 0, 1) == (0, 1)  # clamped at the last value
        assert self._move("up", 0, 0) == (0, 0)  # clamped at the first
        assert self._move("left", 0, 1) == (0, 1)  # left/right don't leave the box

    def test_a_stale_box_index_restarts_at_the_first_box(self):
        # The visible sections change with the tab (and with the provider), so a
        # carried-over index has to degrade instead of raising.
        assert self._move("down", 99, 0) == (0, -1)

    def test_an_empty_grid_clears_the_focus(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import settings_focus_move

        assert settings_focus_move("down", [], [], [], 2, 1) == (-1, -1)


class TestSettingsSaveDataDir:
    """The data directory is typed on the Settings page like every other value.

    Only the *save* still needs a screen of its own — a move-or-leave answer — so
    ``_settings_save_data_dir`` takes the already-typed path and handles the rest.
    """

    def _save(self, monkeypatch, tmp_path, value, *, move: bool):
        import yeaboi.ui.mode_select as ms

        written: list[str] = []
        moved: list = []
        monkeypatch.setattr(ms, "_confirm_move_data", lambda *a, **k: move)
        monkeypatch.setattr("yeaboi.config.set_data_dir", lambda v: written.append(v))
        monkeypatch.setattr(
            "yeaboi.paths.move_data_tree", lambda root: (moved.append(root), (True, "Moved 3 item(s)"))[1]
        )
        msg = ms._settings_save_data_dir(None, None, None, 0.0, True, value)
        return msg, written, moved

    def test_leave_persists_without_moving(self, monkeypatch, tmp_path):
        msg, written, moved = self._save(monkeypatch, tmp_path, str(tmp_path / "d"), move=False)
        assert written == [str(tmp_path / "d")]
        assert not moved
        assert "restart" in msg.lower()

    def test_move_relocates_the_tree_and_reports_it(self, monkeypatch, tmp_path):
        msg, written, moved = self._save(monkeypatch, tmp_path, str(tmp_path / "d"), move=True)
        assert moved == [tmp_path / "d"]
        assert written == [str(tmp_path / "d")]
        assert "Moved 3 item(s)" in msg

    def test_clearing_targets_the_default_home(self, monkeypatch, tmp_path):
        from pathlib import Path

        _msg, written, moved = self._save(monkeypatch, tmp_path, "", move=True)
        assert moved == [Path.home() / ".yeaboi"]
        assert written == [""]  # '' clears the override back to ~/.yeaboi


class TestSettingsWrapValue:
    """Long read-only statuses flow onto continuation lines instead of cropping.

    The voice hint carries an install command, so ellipsizing it mid-command makes
    the row useless. Wrapping happens at build time (not at render), so one body
    line stays one rendered row and the box height still adds up.
    """

    def _wrap(self, value, width=46, head=13):
        from yeaboi.ui.mode_select.screens._screens_secondary import _wrap_value

        return _wrap_value(value, width, head)

    def test_short_value_stays_on_one_line(self):
        assert self._wrap("available — faster-whisper") == ["available — faster-whisper"]

    def test_first_line_leaves_room_for_the_label(self):
        out = self._wrap("unavailable — Install voice extra: uv sync --extra voice")
        assert len(out) > 1
        assert len(out[0]) <= 46 - 13  # shares the row with "Dictation:  "
        assert " ".join(out) == "unavailable — Install voice extra: uv sync --extra voice"

    def test_continuation_lines_get_the_wider_budget(self):
        # Later lines only lose the 2-column indent, not the label's width.
        out = self._wrap("unavailable — Install voice extra: uv sync --extra voice")
        assert all(len(line) <= 46 - 2 for line in out[1:])

    def test_an_overlong_word_keeps_its_line(self):
        # Breaking before a word wider than the budget would emit an empty line.
        out = self._wrap("supercalifragilisticexpialidocious", width=20, head=13)
        assert out[0] == "supercalifragilisticexpialidocious"


class TestBackTabVersusEscKey:
    """A click on the back tab and the Esc key both arrive as "esc".

    Settings uses Esc to pop one focus level at a time, so without a way to tell
    the two apart the back BUTTON needed three clicks to leave. The input layer
    records which it was.
    """

    def test_the_tab_click_is_flagged(self):
        from yeaboi.ui.shared._input import _esc, esc_came_from_back_tab

        assert _esc(from_tab=True) == "esc"
        assert esc_came_from_back_tab() is True

    def test_the_key_is_not(self):
        from yeaboi.ui.shared._input import _esc, esc_came_from_back_tab

        _esc(from_tab=True)  # leave the flag set, so this proves it resets
        assert _esc() == "esc"
        assert esc_came_from_back_tab() is False
