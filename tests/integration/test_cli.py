"""Tests for CLI argument parsing and entry point."""

import os
from unittest.mock import MagicMock, patch

import pytest

from yeaboi import __version__
from yeaboi.agent.state import Feature, Priority, ProjectAnalysis, QuestionnaireState
from yeaboi.cli import (
    DEFAULT_QUESTIONNAIRE_FILENAME,
    _build_sessions_table,
    _build_welcome_panel,
    _clear_sessions,
    _resolve_resume,
    build_parser,
    main,
)
from yeaboi.repl._ui import _predict_next_node
from yeaboi.sessions import SessionStore


@pytest.fixture(autouse=True)
def _no_wizard_by_default(monkeypatch):
    """Prevent the setup wizard from running in existing tests.

    All test classes that need to test the wizard explicitly override
    is_first_run or run_setup_wizard as needed.
    """
    monkeypatch.setattr("yeaboi.setup_wizard.is_first_run", lambda: False)
    monkeypatch.setattr("yeaboi.cli.load_user_config", lambda: None)


class TestArgParsing:
    def test_no_args(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.resume is None

    def test_resume_without_session_id(self):
        parser = build_parser()
        args = parser.parse_args(["--resume"])
        assert args.resume == "__pick__"

    def test_resume_with_session_id(self):
        parser = build_parser()
        args = parser.parse_args(["--resume", "abc-123"])
        assert args.resume == "abc-123"

    def test_resume_with_latest(self):
        parser = build_parser()
        args = parser.parse_args(["--resume", "latest"])
        assert args.resume == "latest"

    def test_list_sessions_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--list-sessions"])
        assert args.list_sessions is True

    def test_list_sessions_default_false(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.list_sessions is False

    def test_version_flag(self, capsys):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert __version__ in output

    def test_help_flag(self, capsys):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--help"])
        assert exc_info.value.code == 0
        output = capsys.readouterr().out
        assert "yeaboi" in output
        assert "--resume" in output
        assert "--version" in output

    def test_export_questionnaire_no_path(self):
        parser = build_parser()
        args = parser.parse_args(["--export-questionnaire"])
        assert args.export_questionnaire == DEFAULT_QUESTIONNAIRE_FILENAME

    def test_export_questionnaire_custom_path(self):
        parser = build_parser()
        args = parser.parse_args(["--export-questionnaire", "my-q.md"])
        assert args.export_questionnaire == "my-q.md"

    def test_questionnaire_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--questionnaire", "intake.md"])
        assert args.questionnaire == "intake.md"

    def test_quick_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--quick"])
        assert args.quick is True

    def test_full_intake_flag_removed(self):
        # The 30-question "standard" mode (--full-intake) has been retired.
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--full-intake"])


class TestNonInteractiveFlags:
    """Tests for --non-interactive, --output, --description, --team-size, --sprint-length."""

    def test_non_interactive_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--non-interactive", "--description", "Build a todo app"])
        assert args.non_interactive is True
        assert args.description == "Build a todo app"

    def test_non_interactive_default_false(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.non_interactive is False

    def test_output_json(self):
        parser = build_parser()
        args = parser.parse_args(["--output", "json", "--non-interactive", "--description", "x"])
        assert args.output == "json"

    def test_output_html(self):
        parser = build_parser()
        args = parser.parse_args(["--output", "html", "--non-interactive", "--description", "x"])
        assert args.output == "html"

    def test_output_markdown(self):
        parser = build_parser()
        args = parser.parse_args(["--output", "markdown", "--non-interactive", "--description", "x"])
        assert args.output == "markdown"

    def test_output_invalid_rejected(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--output", "csv"])

    def test_team_size_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["--team-size", "5"])
        assert args.team_size == 5

    def test_sprint_length_valid(self):
        parser = build_parser()
        args = parser.parse_args(["--sprint-length", "2"])
        assert args.sprint_length == 2

    def test_sprint_length_invalid_rejected(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--sprint-length", "6"])

    def test_description_at_file(self, tmp_path):
        """--description @file.txt reads from file."""
        desc_file = tmp_path / "desc.txt"
        desc_file.write_text("Build a booking system for restaurants")
        # We test the resolution logic in main(), not in the parser
        parser = build_parser()
        args = parser.parse_args(["--description", f"@{desc_file}"])
        assert args.description == f"@{desc_file}"

    def test_non_interactive_requires_description(self, capsys):
        """--non-interactive without --description exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            main(argv=["--non-interactive"])
        assert exc_info.value.code == 1

    def test_output_requires_non_interactive_or_export_only(self, capsys):
        """--output without --non-interactive or --export-only exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            main(argv=["--output", "json", "--mode", "project-planning"])
        assert exc_info.value.code == 1

    @patch("yeaboi.repl.run_repl")
    def test_non_interactive_calls_repl_with_params(self, mock_repl, tmp_path, monkeypatch):
        """--non-interactive passes correct params to run_repl."""
        main(argv=["--non-interactive", "--description", "Build a todo app", "--team-size", "5"])
        mock_repl.assert_called_once()
        call_kwargs = mock_repl.call_args[1]
        assert call_kwargs["non_interactive"] is True
        assert call_kwargs["export_only"] is True
        assert call_kwargs["intake_mode"] == "quick"
        assert call_kwargs["output_format"] == "markdown"
        # Questionnaire should have Q1 and Q6 pre-filled
        qs = call_kwargs["questionnaire"]
        assert qs.answers[1] == "Build a todo app"
        assert qs.answers[6] == "5"

    @patch("yeaboi.repl.run_repl")
    def test_non_interactive_json_output(self, mock_repl):
        """--non-interactive --output json passes output_format='json'."""
        main(argv=["--non-interactive", "--description", "Test", "--output", "json"])
        call_kwargs = mock_repl.call_args[1]
        assert call_kwargs["output_format"] == "json"

    @patch("yeaboi.repl.run_repl")
    def test_non_interactive_loads_scrum_md(self, mock_repl, tmp_path, monkeypatch):
        """--non-interactive picks up SCRUM.md from CWD for keyword extraction."""
        monkeypatch.chdir(tmp_path)
        # SCRUM.md mentions "greenfield" which should be extracted as Q2
        (tmp_path / "SCRUM.md").write_text("# Project\nThis is a greenfield project using React and Node.js\n")
        main(argv=["--non-interactive", "--description", "Build a todo app"])
        call_kwargs = mock_repl.call_args[1]
        qs = call_kwargs["questionnaire"]
        # Q2 should have been filled from SCRUM.md keyword extraction
        assert 2 in qs.answers
        assert "greenfield" in qs.answers[2].lower()

    @patch("yeaboi.repl.run_repl")
    def test_non_interactive_cli_args_win_over_scrum_md(self, mock_repl, tmp_path, monkeypatch):
        """CLI args take priority over SCRUM.md extracted answers."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "SCRUM.md").write_text("# Project\nTeam of 10 engineers\n")
        main(argv=["--non-interactive", "--description", "Build a todo app", "--team-size", "3"])
        call_kwargs = mock_repl.call_args[1]
        qs = call_kwargs["questionnaire"]
        # CLI --team-size=3 should win over SCRUM.md's "10 engineers"
        assert qs.answers[6] == "3"

    @patch("yeaboi.repl.run_repl")
    def test_description_file_resolved(self, mock_repl, tmp_path):
        """--description @file.txt resolves to file contents."""
        desc_file = tmp_path / "desc.txt"
        desc_file.write_text("Build a booking system")
        main(argv=["--non-interactive", "--description", f"@{desc_file}"])
        call_kwargs = mock_repl.call_args[1]
        qs = call_kwargs["questionnaire"]
        assert qs.answers[1] == "Build a booking system"

    def test_description_file_not_found(self, tmp_path, capsys):
        """--description @nonexistent.txt exits with error."""
        with pytest.raises(SystemExit) as exc_info:
            main(argv=["--non-interactive", "--description", f"@{tmp_path}/nope.txt"])
        assert exc_info.value.code == 1


class TestHelpOutput:
    """Tests for --help epilog with usage examples."""

    def _get_help_output(self, capsys) -> str:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--help"])
        return capsys.readouterr().out

    def test_help_contains_examples(self, capsys):
        output = self._get_help_output(capsys)
        assert "examples:" in output

    def test_help_preserves_formatting(self, capsys):
        """Usage examples should preserve whitespace (RawDescriptionHelpFormatter)."""
        output = self._get_help_output(capsys)
        assert "yeaboi --quick" in output

    def test_help_mentions_export_only(self, capsys):
        output = self._get_help_output(capsys)
        assert "--export-only" in output


class TestWelcomePanel:
    """Tests for the branded welcome panel."""

    def test_panel_contains_version(self):
        panel = _build_welcome_panel()
        # The panel renderable is a Text object; check its plain text.
        plain = panel.renderable.plain
        assert __version__ in plain

    def test_panel_contains_tagline(self):
        panel = _build_welcome_panel()
        plain = panel.renderable.plain
        assert "yeaboi.ai" in plain
        assert "Best friend to engineers and agents" in plain

    def test_panel_contains_quick_start_hint(self):
        panel = _build_welcome_panel()
        plain = panel.renderable.plain
        assert "help" in plain

    @patch("yeaboi.repl.run_repl")
    @patch("yeaboi.ui.splash.show_splash")
    def test_welcome_panel_shown_on_startup(self, mock_splash, mock_repl, capsys):
        main(argv=["--mode", "project-planning"])
        # Splash animation replaced the static welcome panel — verify it was called
        mock_splash.assert_called_once()
        # The old REPL path still prints the SCRUM.md tip to stdout
        output = capsys.readouterr().out
        assert "SCRUM.md" in output


class TestScrumMdBanner:
    """Tests for the SCRUM.md startup hint."""

    @patch("yeaboi.repl.run_repl")
    def test_tip_shown_when_no_scrum_md(self, mock_repl, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        main(argv=["--mode", "project-planning"])
        output = capsys.readouterr().out
        assert "SCRUM.md" in output
        assert "Tip:" in output

    @patch("yeaboi.repl.run_repl")
    def test_detected_shown_when_scrum_md_present(self, mock_repl, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "SCRUM.md").write_text("# My project notes")
        main(argv=["--mode", "project-planning"])
        output = capsys.readouterr().out
        assert "SCRUM.md detected" in output
        assert "Tip:" not in output


class TestMain:
    @patch("yeaboi.repl.run_repl")
    def test_main_calls_repl(self, mock_repl):
        main(argv=["--mode", "project-planning"])
        mock_repl.assert_called_once()

    @patch("yeaboi.repl.run_repl")
    @patch("yeaboi.cli._resolve_resume")
    def test_resume_with_id_calls_repl(self, mock_resolve, mock_repl):
        """--resume <id> resolves the session and calls run_repl with resume_state."""
        mock_resolve.return_value = ({"messages": []}, "test-session")
        main(argv=["--resume", "test-session"])
        mock_resolve.assert_called_once()
        mock_repl.assert_called_once()
        call_kwargs = mock_repl.call_args[1]
        assert call_kwargs["resume_state"] == {"messages": []}
        assert call_kwargs["resume_session_id"] == "test-session"

    @patch("yeaboi.repl.run_repl")
    @patch("yeaboi.cli._resolve_resume", return_value=(None, None))
    def test_resume_returns_early_when_no_session(self, mock_resolve, mock_repl):
        """--resume exits early when resolve returns None (cancelled or no sessions)."""
        main(argv=["--resume", "latest"])
        mock_repl.assert_not_called()

    @patch("yeaboi.repl.run_repl")
    @patch("yeaboi.cli._resolve_resume")
    def test_resume_skips_mode_menu(self, mock_resolve, mock_repl, capsys):
        """--resume should skip the startup mode selection entirely."""
        mock_resolve.return_value = ({"messages": []}, "test-session")
        main(argv=["--resume"])
        # Should NOT show mode menu text
        output = capsys.readouterr().out
        assert "Project Planning" not in output
        mock_repl.assert_called_once()

    @patch("yeaboi.repl.run_repl")
    def test_quick_flag_passes_intake_mode(self, mock_repl):
        main(argv=["--quick", "--mode", "project-planning"])
        call_kwargs = mock_repl.call_args[1]
        assert call_kwargs["intake_mode"] == "quick"

    @patch("yeaboi.repl.run_repl")
    def test_default_intake_mode_is_none(self, mock_repl):
        """Default intake mode is None — triggers the interactive intake menu in the REPL."""
        main(argv=["--mode", "project-planning"])
        call_kwargs = mock_repl.call_args[1]
        assert call_kwargs["intake_mode"] is None

    @patch("yeaboi.repl.run_repl")
    def test_proxy_warning_disables_langsmith(self, mock_repl, monkeypatch, capsys):
        # Enable LangSmith
        monkeypatch.setenv("LANGSMITH_TRACING", "true")
        monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test-key")
        # Set a proxy
        monkeypatch.setenv("HTTPS_PROXY", "http://corporate-proxy:8080")

        main(argv=["--mode", "project-planning"])

        output = capsys.readouterr().out
        assert "proxy detected" in output
        assert "auto-disabled" in output
        # LangSmith tracing should have been unset
        assert os.environ.get("LANGSMITH_TRACING") is None


class TestExportQuestionnaireFlag:
    """Tests for --export-questionnaire CLI flag."""

    @patch("yeaboi.repl.run_repl")
    def test_creates_file(self, mock_repl, tmp_path, monkeypatch):
        """--export-questionnaire should create a .md file."""
        out = tmp_path / "test-export.md"
        monkeypatch.chdir(tmp_path)
        main(argv=["--export-questionnaire", str(out)])
        assert out.exists()
        content = out.read_text()
        assert "**Q1.**" in content
        # Should NOT start the REPL
        mock_repl.assert_not_called()

    @patch("yeaboi.repl.run_repl")
    def test_default_filename(self, mock_repl, tmp_path, monkeypatch):
        """--export-questionnaire without a path should use the default filename."""
        monkeypatch.chdir(tmp_path)
        main(argv=["--export-questionnaire"])
        # File should exist at the resolved default filename path
        mock_repl.assert_not_called()

    @patch("yeaboi.repl.run_repl")
    def test_exits_without_repl(self, mock_repl, tmp_path, monkeypatch):
        """--export-questionnaire should exit without starting the REPL."""
        monkeypatch.chdir(tmp_path)
        main(argv=["--export-questionnaire", str(tmp_path / "q.md")])
        mock_repl.assert_not_called()


class TestQuestionnaireFlag:
    """Tests for --questionnaire CLI flag."""

    @patch("yeaboi.repl.run_repl")
    def test_valid_file_passes_questionnaire(self, mock_repl, tmp_path):
        """--questionnaire with a valid file should parse and pass to run_repl."""
        qfile = tmp_path / "intake.md"
        qfile.write_text("**Q1.** What is the project?\n> My awesome project\n")
        main(argv=["--questionnaire", str(qfile), "--mode", "project-planning"])
        mock_repl.assert_called_once()
        # The questionnaire kwarg should be a QuestionnaireState
        call_kwargs = mock_repl.call_args[1]
        assert isinstance(call_kwargs["questionnaire"], QuestionnaireState)
        assert call_kwargs["questionnaire"].answers[1] == "My awesome project"

    def test_nonexistent_file_error(self, tmp_path, capsys):
        """--questionnaire with a nonexistent file should print error and exit."""
        with pytest.raises(SystemExit) as exc_info:
            main(argv=["--questionnaire", str(tmp_path / "nope.md")])
        assert exc_info.value.code == 1
        output = capsys.readouterr().out
        assert "file not found" in output

    @patch("yeaboi.repl.run_repl")
    def test_malformed_file_error(self, mock_repl, tmp_path, capsys):
        """--questionnaire with a malformed file should print error and exit."""
        bad = tmp_path / "bad.md"
        bad.write_text("nothing useful here")
        with pytest.raises(SystemExit) as exc_info:
            main(argv=["--questionnaire", str(bad)])
        assert exc_info.value.code == 1
        output = capsys.readouterr().out
        assert "Error" in output
        mock_repl.assert_not_called()

    @patch("yeaboi.repl.run_repl")
    def test_loaded_answer_count_shown(self, mock_repl, tmp_path, capsys):
        """--questionnaire should print how many answers were loaded."""
        qfile = tmp_path / "intake.md"
        qfile.write_text("**Q1.** What is the project?\n> App\n\n**Q6.** Engineers?\n> 5\n")
        main(argv=["--questionnaire", str(qfile), "--mode", "project-planning"])
        output = capsys.readouterr().out
        assert "2 answers" in output


class TestExportOnlyFlag:
    """Tests for --export-only CLI flag."""

    def test_export_only_flag_parsing(self):
        parser = build_parser()
        args = parser.parse_args(["--export-only", "--quick"])
        assert args.export_only is True
        assert args.quick is True

    def test_export_only_default_false(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.export_only is False

    def test_export_only_without_quick_or_questionnaire_exits(self, capsys):
        """--export-only without --quick or --questionnaire should print error and exit."""
        with pytest.raises(SystemExit) as exc_info:
            main(argv=["--export-only"])
        assert exc_info.value.code == 1
        output = capsys.readouterr().out
        assert "--export-only requires" in output

    @patch("yeaboi.repl.run_repl")
    def test_export_only_with_quick_passes_to_repl(self, mock_repl):
        main(argv=["--export-only", "--quick", "--mode", "project-planning"])
        call_kwargs = mock_repl.call_args[1]
        assert call_kwargs["export_only"] is True


class TestNoBellFlag:
    """Tests for --no-bell CLI flag."""

    def test_no_bell_flag_parsing(self):
        parser = build_parser()
        args = parser.parse_args(["--no-bell"])
        assert args.no_bell is True

    def test_bell_default_true(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.no_bell is False

    @patch("yeaboi.repl.run_repl")
    def test_no_bell_passes_bell_false(self, mock_repl):
        main(argv=["--no-bell", "--mode", "project-planning"])
        call_kwargs = mock_repl.call_args[1]
        assert call_kwargs["bell"] is False

    @patch("yeaboi.repl.run_repl")
    def test_default_passes_bell_true(self, mock_repl):
        main(argv=["--mode", "project-planning"])
        call_kwargs = mock_repl.call_args[1]
        assert call_kwargs["bell"] is True


class TestThemeFlag:
    """Tests for --theme CLI flag."""

    def test_theme_dark(self):
        parser = build_parser()
        args = parser.parse_args(["--theme", "dark"])
        assert args.theme == "dark"

    def test_theme_light(self):
        parser = build_parser()
        args = parser.parse_args(["--theme", "light"])
        assert args.theme == "light"

    def test_theme_default_dark(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.theme == "dark"

    def test_theme_invalid_rejected(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--theme", "neon"])

    @patch("yeaboi.repl.run_repl")
    def test_theme_passed_to_repl(self, mock_repl):
        main(argv=["--theme", "light", "--mode", "project-planning"])
        call_kwargs = mock_repl.call_args[1]
        assert call_kwargs["theme"] == "light"

    @patch("yeaboi.repl.run_repl")
    def test_default_theme_passed(self, mock_repl):
        main(argv=["--mode", "project-planning"])
        call_kwargs = mock_repl.call_args[1]
        assert call_kwargs["theme"] == "dark"


class TestStartupModeMenu:
    """Tests for the top-level mode selection screen."""

    def test_mode_flag_project_planning_calls_repl(self):
        with patch("yeaboi.repl.run_repl") as mock_repl:
            main(argv=["--mode", "project-planning"])
        mock_repl.assert_called_once()

    def test_mode_flag_invalid_rejected_by_argparse(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--mode", "not-a-mode"])

    def test_mode_flag_default_is_none(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.mode is None

    @patch("yeaboi.repl.run_repl")
    @patch("yeaboi.ui.mode_select.select_mode", return_value=("project-planning", "smart", None))
    @patch("yeaboi.ui.splash.show_splash")
    def test_interactive_menu_shown_when_no_mode_flag(self, mock_splash, mock_select, mock_repl):
        """Without --mode, the TUI mode selection screen is displayed."""
        main(argv=[])
        mock_select.assert_called_once()

    @patch("yeaboi.ui.mode_select.select_mode", return_value=None)
    @patch("yeaboi.ui.splash.show_splash")
    def test_select_mode_returns_none_exits_gracefully(self, mock_splash, mock_select):
        """When select_mode returns None (Esc/Ctrl+C), main exits cleanly."""
        main(argv=[])
        mock_select.assert_called_once()

    def test_coming_soon_modes_not_selectable_via_flag(self):
        """Coming-soon modes are not valid --mode choices (argparse rejects them)."""
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--mode", "coming-soon-1"])


class TestSetupWizardIntegration:
    """Tests for setup wizard triggering in main()."""

    @patch("yeaboi.repl.run_repl")
    @patch("yeaboi.setup_wizard.run_setup_wizard", return_value=True)
    def test_setup_flag_triggers_wizard(self, mock_wizard, mock_repl, monkeypatch):
        """--setup flag always triggers the wizard regardless of config file state."""
        monkeypatch.setattr("yeaboi.setup_wizard.is_first_run", lambda: False)
        main(argv=["--setup", "--mode", "project-planning"])
        mock_wizard.assert_called_once()

    @patch("yeaboi.repl.run_repl")
    @patch("yeaboi.setup_wizard.run_setup_wizard", return_value=True)
    def test_first_run_triggers_wizard(self, mock_wizard, mock_repl, monkeypatch):
        """No config file (first run) triggers the wizard automatically."""
        monkeypatch.setattr("yeaboi.setup_wizard.is_first_run", lambda: True)
        main(argv=["--mode", "project-planning"])
        mock_wizard.assert_called_once()

    @patch("yeaboi.repl.run_repl")
    @patch("yeaboi.setup_wizard.run_setup_wizard", return_value=False)
    def test_wizard_cancelled_exits_before_repl(self, mock_wizard, mock_repl, monkeypatch):
        """If wizard returns False (user cancelled), main() exits without starting REPL."""
        monkeypatch.setattr("yeaboi.setup_wizard.is_first_run", lambda: True)
        main(argv=["--mode", "project-planning"])
        mock_wizard.assert_called_once()
        mock_repl.assert_not_called()

    @patch("yeaboi.repl.run_repl")
    @patch("yeaboi.setup_wizard.run_setup_wizard")
    def test_normal_run_skips_wizard(self, mock_wizard, mock_repl, monkeypatch):
        """If config file exists and --setup not passed, wizard is skipped."""
        monkeypatch.setattr("yeaboi.setup_wizard.is_first_run", lambda: False)
        main(argv=["--mode", "project-planning"])
        mock_wizard.assert_not_called()
        mock_repl.assert_called_once()

    def test_setup_flag_parsed(self):
        parser = build_parser()
        args = parser.parse_args(["--setup"])
        assert args.setup is True

    def test_setup_flag_default_false(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.setup is False


# ---------------------------------------------------------------------------
# --list-sessions flag
# ---------------------------------------------------------------------------


class TestListSessionsFlag:
    """Tests for --list-sessions CLI flag."""

    @patch("yeaboi.repl.run_repl")
    def test_list_sessions_shows_table(self, mock_repl, tmp_path, monkeypatch, capsys):
        """--list-sessions prints a table and exits without starting REPL."""
        db_path = tmp_path / "sessions.db"
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        with SessionStore(db_path) as store:
            store.create_session("new-aaaa1111-2026-03-06", project_name="TestProject")
            store.update_last_node("new-aaaa1111-2026-03-06", "feature_generator")
        main(argv=["--list-sessions"])
        output = capsys.readouterr().out
        # Phase 8C: Project column now shows unique display name (slug-date)
        assert "testproject" in output
        assert "feature_generator" in output
        mock_repl.assert_not_called()

    @patch("yeaboi.repl.run_repl")
    def test_list_sessions_empty(self, mock_repl, tmp_path, monkeypatch, capsys):
        """--list-sessions with no sessions prints a helpful message."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        main(argv=["--list-sessions"])
        output = capsys.readouterr().out
        assert "No saved sessions" in output
        mock_repl.assert_not_called()


# ---------------------------------------------------------------------------
# _build_sessions_table helper
# ---------------------------------------------------------------------------


class TestBuildSessionsTable:
    def test_table_has_correct_columns(self):
        sessions = [
            {
                "session_id": "new-aaa-2026-03-06",
                "project_name": "MyApp",
                "created_at": "2026-03-06T12:00:00+00:00",
                "last_node_completed": "sprint_planner",
            }
        ]
        table = _build_sessions_table(sessions)
        assert table.title == "Saved sessions"
        assert len(table.columns) == 5

    def test_table_unnamed_project(self):
        sessions = [
            {
                "session_id": "new-bbb-2026-03-06",
                "project_name": "",
                "created_at": "2026-03-06T12:00:00+00:00",
                "last_node_completed": "",
            }
        ]
        table = _build_sessions_table(sessions)
        # Row should show "(unnamed)" for empty project name
        assert table.row_count == 1


# ---------------------------------------------------------------------------
# _resolve_resume helper
# ---------------------------------------------------------------------------


class TestResolveResume:
    """Tests for the --resume resolution logic."""

    def test_latest_no_sessions(self, tmp_path, monkeypatch, capsys):
        """--resume latest with no sessions returns (None, None)."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        console = MagicMock()
        state, sid = _resolve_resume(console, "latest")
        assert state is None
        assert sid is None

    def test_latest_with_session(self, tmp_path, monkeypatch):
        """--resume latest returns the most recent session's state."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        db_path = tmp_path / "sessions.db"
        with SessionStore(db_path) as store:
            store.create_session("new-aaaa1111-2026-03-06")
            store.save_state("new-aaaa1111-2026-03-06", {"messages": [], "team_size": 5})
        console = MagicMock()
        state, sid = _resolve_resume(console, "latest")
        assert sid == "new-aaaa1111-2026-03-06"
        assert state is not None
        assert state["team_size"] == 5

    def test_specific_id(self, tmp_path, monkeypatch):
        """--resume <id> returns the specific session's state."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        db_path = tmp_path / "sessions.db"
        with SessionStore(db_path) as store:
            store.create_session("my-session-id")
            store.save_state("my-session-id", {"messages": [], "team_size": 3})
        console = MagicMock()
        state, sid = _resolve_resume(console, "my-session-id")
        assert sid == "my-session-id"
        assert state["team_size"] == 3

    def test_specific_id_not_found(self, tmp_path, monkeypatch):
        """--resume <id> with nonexistent ID returns (None, None)."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        console = MagicMock()
        state, sid = _resolve_resume(console, "nonexistent")
        assert state is None
        assert sid is None

    def test_latest_corrupt_state(self, tmp_path, monkeypatch):
        """--resume latest with corrupt state returns (None, None)."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        db_path = tmp_path / "sessions.db"
        with SessionStore(db_path) as store:
            store.create_session("new-bbbb2222-2026-03-06")
            # Manually write corrupt JSON
            store._conn.execute(
                "UPDATE sessions_meta SET session_state = ? WHERE session_id = ?",
                ("{{{not valid json", "new-bbbb2222-2026-03-06"),
            )
        console = MagicMock()
        state, sid = _resolve_resume(console, "latest")
        assert state is None

    @patch("prompt_toolkit.PromptSession")
    def test_picker_cancel(self, mock_session_cls, tmp_path, monkeypatch):
        """--resume (picker mode) returns (None, None) when user cancels."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        db_path = tmp_path / "sessions.db"
        with SessionStore(db_path) as store:
            store.create_session("new-cccc3333-2026-03-06")
            store.save_state("new-cccc3333-2026-03-06", {"messages": []})
        mock_session = MagicMock()
        mock_session.prompt.return_value = "q"
        mock_session_cls.return_value = mock_session
        console = MagicMock()
        state, sid = _resolve_resume(console, "__pick__")
        assert state is None

    @patch("prompt_toolkit.PromptSession")
    def test_picker_selects_session(self, mock_session_cls, tmp_path, monkeypatch):
        """--resume (picker mode) loads the selected session."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        db_path = tmp_path / "sessions.db"
        with SessionStore(db_path) as store:
            store.create_session("new-dddd4444-2026-03-06", project_name="PickMe")
            store.save_state("new-dddd4444-2026-03-06", {"messages": [], "team_size": 8})
        mock_session = MagicMock()
        mock_session.prompt.return_value = "1"
        mock_session_cls.return_value = mock_session
        console = MagicMock()
        state, sid = _resolve_resume(console, "__pick__")
        assert sid == "new-dddd4444-2026-03-06"
        assert state["team_size"] == 8

    @patch("prompt_toolkit.PromptSession")
    def test_picker_no_sessions(self, mock_session_cls, tmp_path, monkeypatch):
        """--resume (picker mode) with no sessions returns (None, None)."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        console = MagicMock()
        state, sid = _resolve_resume(console, "__pick__")
        assert state is None


# ---------------------------------------------------------------------------
# Phase 8D: Session resumption integration tests
# ---------------------------------------------------------------------------


class TestResumeLatestPicksMostRecent:
    """--resume latest picks the most recently modified session among multiple."""

    def test_latest_picks_most_recently_modified(self, tmp_path, monkeypatch):
        """With two sessions, --resume latest returns the one with the newer last_modified."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        db_path = tmp_path / "sessions.db"
        with SessionStore(db_path) as store:
            store.create_session("session-old", project_name="OldProject")
            store.save_state("session-old", {"messages": [], "team_size": 3})
            store.create_session("session-new", project_name="NewProject")
            store.save_state("session-new", {"messages": [], "team_size": 7})
            # Touch session-new so it has the latest last_modified
            store.update_last_node("session-new", "feature_generator")
        console = MagicMock()
        state, sid = _resolve_resume(console, "latest")
        assert sid == "session-new"
        assert state["team_size"] == 7

    def test_latest_picks_updated_old_session(self, tmp_path, monkeypatch):
        """If an older session is updated more recently, --resume latest picks it."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        db_path = tmp_path / "sessions.db"
        with SessionStore(db_path) as store:
            store.create_session("session-a", project_name="Alpha")
            store.save_state("session-a", {"messages": [], "team_size": 1})
            store.create_session("session-b", project_name="Beta")
            store.save_state("session-b", {"messages": [], "team_size": 2})
            # Now update session-a so it becomes the most recent
            store.update_last_node("session-a", "sprint_planner")
        console = MagicMock()
        state, sid = _resolve_resume(console, "latest")
        assert sid == "session-a"


class TestResumeSkipsCompletedNodes:
    """Resumed sessions route to the correct next node based on existing artifacts."""

    def _make_analysis(self) -> ProjectAnalysis:
        return ProjectAnalysis(
            project_name="Test",
            project_description="A test project",
            project_type="greenfield",
            goals=("g1",),
            end_users=("u1",),
            target_state="done",
            tech_stack=("Python",),
            integrations=(),
            constraints=(),
            sprint_length_weeks=2,
            target_sprints=4,
            risks=(),
            out_of_scope=(),
            assumptions=(),
        )

    def test_resume_with_features_routes_to_story_writer(self, tmp_path, monkeypatch):
        """Session with questionnaire + analysis + features → next node is story_writer."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        db_path = tmp_path / "sessions.db"
        qs = QuestionnaireState(completed=True)
        feature = Feature(id="f-1", title="F1", description="D", priority=Priority.HIGH)
        state_in = {
            "messages": [],
            "questionnaire": qs,
            "project_analysis": self._make_analysis(),
            "features": [feature],
        }
        with SessionStore(db_path) as store:
            store.create_session("resume-feature-done")
            store.save_state("resume-feature-done", state_in)
        console = MagicMock()
        state, sid = _resolve_resume(console, "resume-feature-done")
        assert state is not None
        assert _predict_next_node(state) == "story_writer"

    def test_resume_with_analysis_only_routes_to_feature_generator(self, tmp_path, monkeypatch):
        """Session with questionnaire + analysis (no features) → next node is feature_generator."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        db_path = tmp_path / "sessions.db"
        qs = QuestionnaireState(completed=True)
        state_in = {
            "messages": [],
            "questionnaire": qs,
            "project_analysis": self._make_analysis(),
        }
        with SessionStore(db_path) as store:
            store.create_session("resume-analysis-done")
            store.save_state("resume-analysis-done", state_in)
        console = MagicMock()
        state, sid = _resolve_resume(console, "resume-analysis-done")
        assert state is not None
        assert _predict_next_node(state) == "feature_generator"

    def test_resume_mid_questionnaire_routes_to_intake(self, tmp_path, monkeypatch):
        """Session with incomplete questionnaire → next node is project_intake."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        db_path = tmp_path / "sessions.db"
        qs = QuestionnaireState(current_question=5, completed=False)
        state_in = {"messages": [], "questionnaire": qs}
        with SessionStore(db_path) as store:
            store.create_session("resume-mid-qs")
            store.save_state("resume-mid-qs", state_in)
        console = MagicMock()
        state, sid = _resolve_resume(console, "resume-mid-qs")
        assert state is not None
        assert _predict_next_node(state) == "project_intake"


class TestStaleCorruptSessionFallback:
    """Stale/corrupt sessions produce (None, None) without crashing."""

    def test_empty_state_returns_none(self, tmp_path, monkeypatch):
        """Session created but never saved state → returns (None, None)."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        db_path = tmp_path / "sessions.db"
        with SessionStore(db_path) as store:
            store.create_session("empty-session")
        console = MagicMock()
        state, sid = _resolve_resume(console, "empty-session")
        assert state is None
        assert sid is None

    def test_corrupt_json_returns_none(self, tmp_path, monkeypatch):
        """Session with corrupt JSON state → returns (None, None)."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        db_path = tmp_path / "sessions.db"
        with SessionStore(db_path) as store:
            store.create_session("corrupt-session")
            store._conn.execute(
                "UPDATE sessions_meta SET session_state = ? WHERE session_id = ?",
                ("not valid json{{{", "corrupt-session"),
            )
        console = MagicMock()
        state, sid = _resolve_resume(console, "corrupt-session")
        assert state is None
        assert sid is None

    def test_nonexistent_id_returns_none(self, tmp_path, monkeypatch):
        """Resuming a session ID that doesn't exist → returns (None, None)."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        console = MagicMock()
        state, sid = _resolve_resume(console, "does-not-exist")
        assert state is None
        assert sid is None


# ---------------------------------------------------------------------------
# --clear-sessions flag
# ---------------------------------------------------------------------------


class TestClearSessions:
    """Tests for --clear-sessions CLI flag and _clear_sessions()."""

    @patch("yeaboi.repl.run_repl")
    def test_clear_sessions_flag_parsed(self, mock_repl):
        parser = build_parser()
        args = parser.parse_args(["--clear-sessions"])
        assert args.clear_sessions is True

    @patch("prompt_toolkit.PromptSession")
    def test_clear_single_session(self, mock_session_cls, tmp_path, monkeypatch):
        """Pick a session number → deletes that session."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        db_path = tmp_path / "sessions.db"
        with SessionStore(db_path) as store:
            store.create_session("s1", project_name="Alpha")
            store.create_session("s2", project_name="Beta")
        mock_session = MagicMock()
        mock_session.prompt.return_value = "1"
        mock_session_cls.return_value = mock_session
        console = MagicMock()
        _clear_sessions(console)
        # s1 should be deleted (it's the most recently modified, shown first)
        with SessionStore(db_path) as store:
            sessions = store.list_sessions()
        assert len(sessions) == 1

    @patch("prompt_toolkit.PromptSession")
    def test_clear_all_sessions(self, mock_session_cls, tmp_path, monkeypatch):
        """Type 'all' → deletes everything."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        db_path = tmp_path / "sessions.db"
        with SessionStore(db_path) as store:
            store.create_session("s1", project_name="Alpha")
            store.create_session("s2", project_name="Beta")
        mock_session = MagicMock()
        mock_session.prompt.return_value = "all"
        mock_session_cls.return_value = mock_session
        console = MagicMock()
        _clear_sessions(console)
        with SessionStore(db_path) as store:
            assert store.list_sessions() == []

    @patch("prompt_toolkit.PromptSession")
    def test_clear_cancel(self, mock_session_cls, tmp_path, monkeypatch):
        """Type 'q' → nothing deleted."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        db_path = tmp_path / "sessions.db"
        with SessionStore(db_path) as store:
            store.create_session("s1", project_name="Keep")
        mock_session = MagicMock()
        mock_session.prompt.return_value = "q"
        mock_session_cls.return_value = mock_session
        console = MagicMock()
        _clear_sessions(console)
        with SessionStore(db_path) as store:
            assert len(store.list_sessions()) == 1

    def test_clear_empty_db(self, tmp_path, monkeypatch):
        """No sessions → prints hint, no crash."""
        monkeypatch.setattr("yeaboi.cli._SESSIONS_DB_DIR", tmp_path)
        console = MagicMock()
        _clear_sessions(console)
        console.print.assert_called_with("[hint]No saved sessions found.[/hint]")


class TestStandupCLI:
    def test_standup_run_flag_parses(self):
        parser = build_parser()
        args = parser.parse_args(["--standup-run"])
        assert args.standup_run is True

    def test_standup_run_default_false(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.standup_run is False

    def test_standup_session_and_output(self):
        parser = build_parser()
        args = parser.parse_args(["--standup-run", "--standup-session", "s1", "--standup-output", "slack"])
        assert args.standup_session == "s1"
        assert args.standup_output == "slack"

    def test_standup_output_rejects_bad_channel(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--standup-output", "carrier-pigeon"])

    def test_run_standup_no_session_returns_2(self, tmp_path, monkeypatch):
        from yeaboi import cli

        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "sessions.db")
        monkeypatch.setattr("yeaboi.paths.get_standup_log_dir", lambda: tmp_path)
        parser = build_parser()
        args = parser.parse_args(["--standup-run"])
        assert cli._run_standup(args) == 2

    def test_run_standup_invokes_engine(self, tmp_path, monkeypatch):
        from yeaboi import cli
        from yeaboi.agent.state import StandupReport
        from yeaboi.sessions import SessionStore

        db = tmp_path / "sessions.db"
        with SessionStore(db) as store:
            store.create_session("s1", project_name="Demo")
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
        monkeypatch.setattr("yeaboi.paths.get_standup_log_dir", lambda: tmp_path)

        calls = {}

        def fake_run(session_id, channels=None, deliver=True):
            calls["session_id"] = session_id
            calls["channels"] = channels
            return StandupReport(session_id=session_id, sprint_day=2, sprint_total_days=10)

        monkeypatch.setattr("yeaboi.standup.engine.run_standup", fake_run)
        parser = build_parser()
        args = parser.parse_args(["--standup-run", "--standup-session", "s1", "--standup-output", "all"])
        rc = cli._run_standup(args)
        assert rc == 0
        assert calls["session_id"] == "s1"
        # "all" expands to every channel
        assert set(calls["channels"]) == {"terminal", "desktop", "slack", "email"}


class TestUpdateRestartWiring:
    """main()'s half of the ctrl+U relaunch: skip the splash, exec after cleanup.

    The flow itself can't call ``os.execv`` — it runs deep inside select_mode's Live
    context, where exec (which skips ``atexit``) would hand the new process a
    terminal still in raw mode. It leaves a request on ``update_check``; these pin
    that main acts on it, and only once the terminal has been restored.
    """

    @pytest.fixture(autouse=True)
    def _no_restart_by_default(self, monkeypatch):
        monkeypatch.setattr("yeaboi.update_check.is_fresh_restart", lambda: False)
        monkeypatch.setattr("yeaboi.update_check.restart_requested", lambda: "")

    @patch("yeaboi.ui.mode_select.select_mode", return_value=None)
    @patch("yeaboi.ui.splash.show_splash")
    def test_splash_plays_on_a_normal_launch(self, mock_splash, mock_select):
        main(argv=[])
        mock_splash.assert_called_once()

    @patch("yeaboi.ui.mode_select.select_mode", return_value=None)
    @patch("yeaboi.ui.splash.show_splash")
    def test_splash_skipped_when_this_process_is_the_relaunch(self, mock_splash, mock_select, monkeypatch):
        # The user just watched the upgrade land; select_mode's Live(screen=True)
        # enters the alt-screen itself, so only the animation is lost.
        monkeypatch.setattr("yeaboi.update_check.is_fresh_restart", lambda: True)
        main(argv=[])
        mock_splash.assert_not_called()

    @patch("yeaboi.ui.mode_select.select_mode", return_value=None)
    @patch("yeaboi.ui.splash.show_splash")
    def test_no_restart_requested_never_execs(self, mock_splash, mock_select, monkeypatch):
        called = []
        monkeypatch.setattr("yeaboi.update_check.restart_in_place", lambda: called.append(True) or False)
        main(argv=[])
        assert called == []

    @patch("yeaboi.ui.mode_select.select_mode", return_value=None)
    @patch("yeaboi.ui.splash.show_splash")
    def test_restart_happens_only_after_the_terminal_is_restored(self, mock_splash, mock_select, monkeypatch):
        order: list[str] = []
        monkeypatch.setattr("yeaboi.update_check.restart_requested", lambda: "2.13.0")
        monkeypatch.setattr("yeaboi.update_check.restart_in_place", lambda: order.append("exec") or True)
        monkeypatch.setattr("yeaboi.ui.shared._input.exit_raw_mode", lambda: order.append("raw-off"))
        monkeypatch.setattr("yeaboi.ui.shared._input.disable_mouse_tracking", lambda: order.append("mouse-off"))
        main(argv=[])
        assert "exec" in order
        assert order.index("exec") > order.index("raw-off")
        assert order.index("exec") > order.index("mouse-off")

    @patch("yeaboi.ui.mode_select.select_mode", return_value=None)
    @patch("yeaboi.ui.splash.show_splash")
    def test_failed_exec_falls_back_to_telling_the_user(self, mock_splash, mock_select, monkeypatch, capsys):
        # restart_in_place only returns when it could not replace the process.
        monkeypatch.setattr("yeaboi.update_check.restart_requested", lambda: "2.13.0")
        monkeypatch.setattr("yeaboi.update_check.restart_in_place", lambda: False)
        main(argv=[])
        assert "restart yeaboi" in capsys.readouterr().out.lower()
