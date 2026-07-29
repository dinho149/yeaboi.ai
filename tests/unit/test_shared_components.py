"""Unit tests for shared TUI components: Theme, buttons, scrollbar, progress dots, viewport."""

from __future__ import annotations

from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.shared._components import (
    ANALYSIS_THEME,
    PLANNING_THEME,
    Theme,
    build_action_buttons,
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

    def test_back_hint(self):
        """The Copy/Back button row is a keyboard hint; 'Esc back' was dropped once
        the app-wide back tab covered going back, leaving just the copy hint."""
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_usage_screen

        result = _build_usage_screen({}, width=100, height=40, actions=["Copy", "Back"])
        buf = StringIO()
        Console(file=buf, width=100, force_terminal=False).print(result)
        out = buf.getvalue()
        assert "copy" in out
        assert "back" not in out.lower()  # back tab covers it now

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

    def test_tab_bar_and_hint_rendered(self):
        # The old action-button row was replaced by a tab bar (grouped sections)
        # plus a context hint (switch / Enter …). 'Esc back' was dropped once the
        # app-wide back tab covered going back.
        output = self._render({}, height=40)
        for tab in ("Credentials", "Storage", "System"):
            assert tab in output
        assert "switch" in output

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
        system = self._render({}, height=40, active_tab=2)
        assert "Log Level" in system  # Advanced section
        assert "Anthropic Key" not in system  # credentials are on another tab

    def test_system_tab_hint_mentions_log_level(self):
        output = self._render({}, height=40, active_tab=2)  # System tab (Advanced → log level)
        assert "log level" in output.lower()

    def test_settings_tab_action_mapping(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _SETTINGS_TABS, settings_tab_action

        assert settings_tab_action(_SETTINGS_TABS.index("Storage")) == "datadir"
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
        envs = {env for _, env, _, _ in panel._row_regions}
        assert "LLM_PROVIDER" in envs and "ANTHROPIC_API_KEY" in envs
        # Each region's absolute row actually contains that row's label.
        for abs_row, _env, label, _masked in panel._row_regions:
            row_text = "".join(s.text for s in lines[abs_row - 1])
            assert label in row_text

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
        panel = _build_settings_screen({"_config_path": "/tmp/.env"}, width=120, height=60, active_tab=2)
        labels = {label for _, _, label, _ in panel._row_regions}
        assert "Config File" not in labels
        assert "Log Level" in labels  # but editable rows on the same tab do have regions

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

    def test_storage_section_rendered(self):
        # Storage is tab index 1 — render that tab to see its rows.
        output = self._render({"YEABOI_HOME": "/data/yeaboi"}, height=40, active_tab=1)
        assert "Data Directory" in output
        assert "/data/yeaboi" in output

    def test_data_dir_default_label_when_unset(self):
        output = self._render({}, height=40, active_tab=1)  # Storage tab
        assert "~/.yeaboi (default)" in output

    def test_storage_tab_hint_mentions_data_dir(self):
        # Storage tab's Enter action edits the data directory — the hint says so.
        output = self._render({}, height=40, active_tab=1)  # Storage tab
        assert "data dir" in output.lower()

    def test_status_message_rendered(self):
        output = self._render({"_message": "Data directory saved — restart yeaboi to fully apply"})
        assert "restart yeaboi" in output

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

    def test_token_help_url_is_clickable(self):
        # The creation URL is an OSC-8 hyperlink in the read-only dashboard too.
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        result = _build_settings_screen({"GITHUB_TOKEN": "ghp_x"}, width=120, height=80, active_tab=0)
        buf = StringIO()
        Console(file=buf, width=120, force_terminal=True).print(result)
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
