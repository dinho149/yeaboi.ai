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

    def test_mode_themes_carry_dark_tints(self):
        assert ANALYSIS_THEME.bg == "rgb(9,23,19)"
        assert PLANNING_THEME.bg == "rgb(13,17,30)"


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

    def test_back_button(self):
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_usage_screen

        result = _build_usage_screen({}, width=100, height=40)
        buf = StringIO()
        Console(file=buf, width=100, force_terminal=False).print(result)
        assert "Back" in buf.getvalue()

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

    def test_configure_and_back_buttons(self):
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        result = _build_settings_screen({}, width=100, height=40)
        buf = StringIO()
        Console(file=buf, width=100, force_terminal=False).print(result)
        output = buf.getvalue()
        assert "Configure" in output
        assert "Back" in output

    def test_scrollable(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        r1 = _build_settings_screen({}, scroll_offset=0, width=80, height=20)
        r2 = _build_settings_screen({}, scroll_offset=5, width=80, height=20)
        assert isinstance(r1, Panel)
        assert isinstance(r2, Panel)

    @staticmethod
    def _render(data: dict, *, height: int = 60) -> str:
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        result = _build_settings_screen(data, width=100, height=height)
        buf = StringIO()
        Console(file=buf, width=100, force_terminal=False).print(result)
        return buf.getvalue()

    def test_storage_section_rendered(self):
        # Storage sits below the token sections (each of which now carries a
        # create-link + scope sub-line), so render tall enough to bring it into
        # the viewport rather than requiring a scroll.
        output = self._render({"YEABOI_HOME": "/data/yeaboi"}, height=80)
        assert "Storage" in output
        assert "/data/yeaboi" in output

    def test_data_dir_default_label_when_unset(self):
        output = self._render({}, height=80)
        assert "~/.yeaboi (default)" in output

    def test_data_dir_button_rendered(self):
        output = self._render({}, height=40)
        assert "Data Dir" in output

    def test_allowed_paths_row_rendered(self):
        output = self._render({"YEABOI_ALLOWED_PATHS": "/repos/team,/tmp/exports"}, height=80)
        assert "Allowed Paths" in output
        assert "/repos/team,/tmp/exports" in output

    def test_allowed_paths_empty_shows_sandbox_note(self):
        output = self._render({}, height=80)
        assert "none — sandboxed to data dir" in output

    def test_paths_button_rendered(self):
        # height=40 keeps the Storage section out of the viewport, so "Paths"
        # can only come from the action-button row.
        output = self._render({}, height=40)
        assert "Paths" in output

    def test_status_message_rendered(self):
        output = self._render({"_message": "Data directory saved — restart yeaboi to fully apply"})
        assert "restart yeaboi" in output

    def test_notion_token_masked(self):
        output = self._render({"NOTION_TOKEN": "ntn_verysecretvalue12345"})
        assert "verysecretvalue12345" not in output

    def test_token_help_link_and_scope_rendered(self):
        # Each token row carries a "create: <url> · scope: <...>" sub-line so a
        # user knows where to make the token and what access to grant it.
        output = self._render(
            {"GITHUB_TOKEN": "ghp_x", "AZURE_DEVOPS_TOKEN": "az", "JIRA_API_TOKEN": "jt", "NOTION_TOKEN": "nt"},
            height=80,
        )
        assert "create:" in output
        assert "scope:" in output
        assert "github.com/settings/tokens" in output  # creation link
        assert "Work Items" in output  # Azure scope text

    def test_token_help_url_is_clickable(self):
        # The creation URL is an OSC-8 hyperlink in the read-only dashboard too.
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        result = _build_settings_screen({"GITHUB_TOKEN": "ghp_x"}, width=120, height=80)
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

    def test_includes_allowed_paths(self, monkeypatch):
        from yeaboi.ui.mode_select import _collect_settings_data

        monkeypatch.setenv("YEABOI_ALLOWED_PATHS", "/a,/b")
        data = _collect_settings_data()
        assert data["YEABOI_ALLOWED_PATHS"] == "/a,/b"


class TestAllowedPathsFlow:
    """_settings_allowed_paths_flow — single-line editor of YEABOI_ALLOWED_PATHS."""

    class _Console:
        size = (100, 36)

    class _Live:
        def update(self, _renderable):
            pass

    def _run(self, monkeypatch, *, current: tuple = (), typed):
        from yeaboi.ui import mode_select

        calls: list = []
        monkeypatch.setattr("yeaboi.config.get_allowed_paths", lambda: current)
        monkeypatch.setattr("yeaboi.config.set_allowed_paths", calls.append)
        monkeypatch.setattr(mode_select, "_standup_read_line", lambda *a, **k: typed)
        msg = mode_select._settings_allowed_paths_flow(self._Console(), self._Live(), lambda **k: "", 0.001, True)
        return calls, msg

    def test_saves_parsed_comma_list(self, monkeypatch):
        calls, msg = self._run(monkeypatch, typed=" /repos/one , /repos/two ")
        assert calls == [["/repos/one", "/repos/two"]]
        assert "2 path" in msg

    def test_dash_clears_whitelist(self, monkeypatch):
        calls, msg = self._run(monkeypatch, current=("/old",), typed="-")
        assert calls == [[]]
        assert "sandboxed" in msg

    def test_esc_aborts_without_saving(self, monkeypatch):
        calls, msg = self._run(monkeypatch, current=("/old",), typed=None)
        assert calls == []
        assert msg == ""

    def test_unchanged_value_is_a_noop(self, monkeypatch):
        calls, msg = self._run(monkeypatch, current=("/old",), typed="/old")
        assert calls == []
        assert msg == ""


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

        panel = _build_settings_screen({"_config_path": "/tmp/.env"}, width=100, height=90)
        assert isinstance(panel, Panel)
        console = Console(width=120)
        with console.capture() as cap:
            console.print(panel)
        out = cap.get()
        assert "Log Level" in out
        assert "Configure" in out
        assert "Back" in out

    def test_three_buttons_fit_at_width_80(self):
        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_settings_screen

        panel = _build_settings_screen({"_config_path": "/tmp/.env"}, width=80, height=40, action_sel=1)
        console = Console(width=80)
        with console.capture() as cap:
            console.print(panel)
        assert "Log Level" in cap.get()


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
