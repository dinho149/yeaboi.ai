"""Tests for the first-run setup wizard."""

import os
from io import StringIO

import pytest
from rich.console import Console

from yeaboi.setup_wizard import is_first_run, run_setup_wizard, save_config
from yeaboi.ui.provider_select._constants import _PROVIDER_CARDS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _card(provider_val: str) -> dict:
    return next(c for c in _PROVIDER_CARDS if c["provider_val"] == provider_val)


# The wizard's provider registry now lives on the cards; these tests address it
# by the historical 1-5 numbering, so rebuild that mapping here.
_PROVIDERS = {
    "1": _card("anthropic"),
    "2": _card("openai"),
    "3": _card("google"),
    "4": _card("bedrock"),
    "5": _card("ollama"),
}


def _make_console() -> Console:
    return Console(file=StringIO(), highlight=False)


def _patch_config_file(monkeypatch, tmp_path):
    """Redirect get_config_file() to a path inside tmp_path."""
    config_file = tmp_path / ".env"
    monkeypatch.setattr("yeaboi.setup_wizard.get_config_file", lambda: config_file)
    monkeypatch.setattr("yeaboi.config.get_config_file", lambda: config_file)
    return config_file


def _mock_inputs(*values):
    """Return a prompt mock that yields values from a list."""
    it = iter(values)
    return lambda *a, **kw: next(it)


def _mock_select_provider(
    provider_key: str,
    *,
    api_key: str = "",
    issue_tracking: dict | None = None,
    confluence: dict | None = None,
):
    """Return a mock select_provider that returns the provider dict.

    provider_key: "1" for Anthropic, "2" for OpenAI, "3" for Google, or None for cancel.
    api_key: optional API key — when set, the wizard skips inline key prompt.
    issue_tracking: optional dict of Jira env vars.
    confluence: optional dict of Confluence env vars (collected in the Docs step).

    The full-screen select_provider returns a dict with optional keys:
        api_key, vc_env_var, vc_token, issue_tracking, notion, confluence
    This mock emulates that so the wizard doesn't need inline prompts.
    """
    if provider_key is None:
        return lambda *a, **kw: None

    p = dict(_PROVIDERS[provider_key])  # shallow copy
    if api_key:
        p["api_key"] = api_key
    if issue_tracking:
        p["issue_tracking"] = issue_tracking
    if confluence:
        p["confluence"] = confluence
    return lambda *a, **kw: p


def _patch_provider(monkeypatch, provider_key: str, **kwargs):
    """Patch select_provider to return the given provider without full-screen UI."""
    monkeypatch.setattr(
        "yeaboi.setup_wizard.select_provider",
        _mock_select_provider(provider_key, **kwargs),
    )


# ---------------------------------------------------------------------------
# TestIsFirstRun
# ---------------------------------------------------------------------------


class TestIsFirstRun:
    def test_returns_true_when_config_absent(self, monkeypatch, tmp_path):
        _patch_config_file(monkeypatch, tmp_path)
        assert is_first_run() is True

    def test_returns_false_when_config_exists(self, monkeypatch, tmp_path):
        config_file = _patch_config_file(monkeypatch, tmp_path)
        config_file.write_text("ANTHROPIC_API_KEY=sk-ant-test\n")
        assert is_first_run() is False

    def test_returns_true_when_config_only_whitespace(self, monkeypatch, tmp_path):
        """A file with only newlines/spaces should be treated as empty."""
        config_file = _patch_config_file(monkeypatch, tmp_path)
        config_file.write_text("\n")
        assert is_first_run() is True

    def test_returns_true_when_config_only_blank_lines(self, monkeypatch, tmp_path):
        config_file = _patch_config_file(monkeypatch, tmp_path)
        config_file.write_text("\n\n  \n")
        assert is_first_run() is True


# ---------------------------------------------------------------------------
# TestSaveConfig
# ---------------------------------------------------------------------------


class TestSaveConfig:
    def test_writes_key_value_lines(self, monkeypatch, tmp_path):
        _patch_config_file(monkeypatch, tmp_path)
        data = {"ANTHROPIC_API_KEY": "sk-ant-abc", "GITHUB_TOKEN": "ghp_xyz"}
        path = save_config(data)
        content = path.read_text()
        assert "ANTHROPIC_API_KEY=sk-ant-abc\n" in content
        assert "GITHUB_TOKEN=ghp_xyz\n" in content

    def test_skips_empty_values(self, monkeypatch, tmp_path):
        _patch_config_file(monkeypatch, tmp_path)
        data = {"ANTHROPIC_API_KEY": "sk-ant-abc", "GITHUB_TOKEN": ""}
        path = save_config(data)
        content = path.read_text()
        assert "GITHUB_TOKEN" not in content

    def test_returns_path_written(self, monkeypatch, tmp_path):
        config_file = _patch_config_file(monkeypatch, tmp_path)
        path = save_config({"ANTHROPIC_API_KEY": "sk-ant-abc"})
        assert path == config_file

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_saved_config_is_owner_only(self, monkeypatch, tmp_path):
        import stat

        _patch_config_file(monkeypatch, tmp_path)
        # The .env holds plaintext API keys, so it must not be group/other readable.
        path = save_config({"ANTHROPIC_API_KEY": "sk-ant-abc"})
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ---------------------------------------------------------------------------
# TestRunSetupWizard
# ---------------------------------------------------------------------------
# After the full-screen provider selector, prompts are:
#   1. ANTHROPIC_API_KEY:         → step 2 (masked)
#   2. GitHub [y/N]:              → step 3
#   3. Jira [y/N]:                → step 3
#   4. Azure DevOps [y/N]:        → step 3


class TestRunSetupWizard:
    def test_happy_path_anthropic_key_saves_and_returns_true(self, monkeypatch, tmp_path):
        _patch_config_file(monkeypatch, tmp_path)
        _patch_provider(monkeypatch, "1")
        monkeypatch.setattr(
            "yeaboi.setup_wizard.prompt",
            _mock_inputs("sk-ant-validkey", "n", "n", "n"),
        )
        console = _make_console()
        result = run_setup_wizard(console)
        assert result is True
        content = (tmp_path / ".env").read_text()
        assert "ANTHROPIC_API_KEY=sk-ant-validkey" in content
        assert "LLM_PROVIDER=anthropic" in content

    def test_welcome_panel_states_the_privacy_headline(self, monkeypatch, tmp_path):
        # The wizard renders yeaboi.privacy's headline — the copy owner every
        # surface shares — so first-run and the privacy page can never disagree.
        from yeaboi.privacy import PRIVACY_HEADLINE

        _patch_config_file(monkeypatch, tmp_path)
        _patch_provider(monkeypatch, "1")
        monkeypatch.setattr("yeaboi.setup_wizard.prompt", _mock_inputs("sk-ant-validkey", "n", "n", "n"))
        console = _make_console()
        run_setup_wizard(console)
        out = console.file.getvalue()
        assert PRIVACY_HEADLINE in out
        assert "~/.yeaboi" in out

    def test_openai_provider_saves_openai_key_and_provider(self, monkeypatch, tmp_path):
        _patch_config_file(monkeypatch, tmp_path)
        _patch_provider(monkeypatch, "2")
        monkeypatch.setattr(
            "yeaboi.setup_wizard.prompt",
            _mock_inputs("sk-openai-testkey", "n", "n", "n"),
        )
        console = _make_console()
        run_setup_wizard(console)
        content = (tmp_path / ".env").read_text()
        assert "OPENAI_API_KEY=sk-openai-testkey" in content
        assert "LLM_PROVIDER=openai" in content

    def test_google_provider_saves_google_key_and_provider(self, monkeypatch, tmp_path):
        _patch_config_file(monkeypatch, tmp_path)
        _patch_provider(monkeypatch, "3")
        monkeypatch.setattr(
            "yeaboi.setup_wizard.prompt",
            _mock_inputs("AIzaGoogleKey123", "n", "n", "n"),
        )
        console = _make_console()
        run_setup_wizard(console)
        content = (tmp_path / ".env").read_text()
        assert "GOOGLE_API_KEY=AIzaGoogleKey123" in content
        assert "LLM_PROVIDER=google" in content

    def test_cancelled_provider_returns_false(self, monkeypatch, tmp_path):
        """When user cancels provider selection (q/Esc), wizard returns False."""
        _patch_config_file(monkeypatch, tmp_path)
        _patch_provider(monkeypatch, None)  # simulate cancel
        console = _make_console()
        result = run_setup_wizard(console)
        assert result is False

    def test_empty_key_returns_false(self, monkeypatch, tmp_path):
        _patch_config_file(monkeypatch, tmp_path)
        _patch_provider(monkeypatch, "1")
        monkeypatch.setattr("yeaboi.setup_wizard.prompt", lambda *a, **kw: "")
        console = _make_console()
        result = run_setup_wizard(console)
        assert result is False
        output = console.file.getvalue()
        assert "required" in output

    def test_invalid_key_format_warns_and_retries(self, monkeypatch, tmp_path):
        """Bad format → warning + retry prompt → user re-enters a good key."""
        _patch_config_file(monkeypatch, tmp_path)
        _patch_provider(monkeypatch, "1")
        monkeypatch.setattr(
            "yeaboi.setup_wizard.prompt",
            # bad key, re-enter=Y (retry), good key, integrations
            _mock_inputs("not-a-valid-key", "y", "sk-ant-real", "n", "n", "n"),
        )
        console = _make_console()
        result = run_setup_wizard(console)
        assert result is True
        content = (tmp_path / ".env").read_text()
        assert "ANTHROPIC_API_KEY=sk-ant-real" in content
        output = console.file.getvalue()
        assert "Warning" in output

    def test_invalid_key_format_accepted_when_retry_declined(self, monkeypatch, tmp_path):
        """Bad format → warning → user types 'n' to skip retry → key saved as-is."""
        _patch_config_file(monkeypatch, tmp_path)
        _patch_provider(monkeypatch, "1")
        monkeypatch.setattr(
            "yeaboi.setup_wizard.prompt",
            _mock_inputs("not-an-anthropic-key", "n", "n", "n", "n"),
        )
        console = _make_console()
        result = run_setup_wizard(console)
        assert result is True
        content = (tmp_path / ".env").read_text()
        assert "ANTHROPIC_API_KEY=not-an-anthropic-key" in content
        output = console.file.getvalue()
        assert "Warning" in output

    def test_github_integration_saves_token(self, monkeypatch, tmp_path):
        """GitHub token is collected via select_provider's VC phase."""
        _patch_config_file(monkeypatch, tmp_path)
        _patch_provider(monkeypatch, "1", api_key="sk-ant-key")
        # Simulate select_provider returning a VC token
        mock = _mock_select_provider("1", api_key="sk-ant-key")
        result = mock()
        result["vc_env_var"] = "GITHUB_TOKEN"
        result["vc_token"] = "ghp_mytoken"
        monkeypatch.setattr("yeaboi.setup_wizard.select_provider", lambda *a, **kw: result)
        console = _make_console()
        run_setup_wizard(console)
        content = (tmp_path / ".env").read_text()
        assert "GITHUB_TOKEN=ghp_mytoken" in content

    def test_jira_integration_saves_all_four_vars(self, monkeypatch, tmp_path):
        """Jira vars are collected via select_provider's issue tracking phase."""
        _patch_config_file(monkeypatch, tmp_path)
        jira_vars = {
            "JIRA_BASE_URL": "https://myorg.atlassian.net",
            "JIRA_EMAIL": "me@example.com",
            "JIRA_API_TOKEN": "jira-api-token",
            "JIRA_PROJECT_KEY": "MYPROJ",
        }
        _patch_provider(monkeypatch, "1", api_key="sk-ant-key", issue_tracking=jira_vars)
        console = _make_console()
        run_setup_wizard(console)
        content = (tmp_path / ".env").read_text()
        assert "JIRA_BASE_URL=https://myorg.atlassian.net" in content
        assert "JIRA_EMAIL=me@example.com" in content
        assert "JIRA_API_TOKEN=jira-api-token" in content
        assert "JIRA_PROJECT_KEY=MYPROJ" in content

    def test_cancel_jira_saves_no_jira_vars(self, monkeypatch, tmp_path):
        _patch_config_file(monkeypatch, tmp_path)
        _patch_provider(monkeypatch, "1")
        monkeypatch.setattr(
            "yeaboi.setup_wizard.prompt",
            _mock_inputs("sk-ant-key", "n", "n", "n"),
        )
        console = _make_console()
        run_setup_wizard(console)
        content = (tmp_path / ".env").read_text()
        assert "JIRA_BASE_URL" not in content
        assert "JIRA_EMAIL" not in content

    def test_confluence_prompt_only_shown_when_jira_configured(self, monkeypatch, tmp_path):
        _patch_config_file(monkeypatch, tmp_path)
        _patch_provider(monkeypatch, "1")
        calls = []

        def _mock_prompt(text, **kw):
            calls.append(text)
            if "ANTHROPIC_API_KEY" in text:
                return "sk-ant-key"
            return "n"

        monkeypatch.setattr("yeaboi.setup_wizard.prompt", _mock_prompt)
        console = _make_console()
        run_setup_wizard(console)
        assert not any("Confluence" in c for c in calls)

    def test_confluence_saves_space_key(self, monkeypatch, tmp_path):
        """Confluence is collected in the Docs step (separate from issue tracking).

        The space key rides on the Jira Atlassian creds gathered in the Issue
        Tracking step, but is returned under its own `confluence` result key.
        """
        _patch_config_file(monkeypatch, tmp_path)
        issue_tracking = {
            "JIRA_BASE_URL": "https://org.atlassian.net",
            "JIRA_EMAIL": "user@example.com",
            "JIRA_API_TOKEN": "tok",
            "JIRA_PROJECT_KEY": "PROJ",
        }
        _patch_provider(
            monkeypatch,
            "1",
            api_key="sk-ant-key",
            issue_tracking=issue_tracking,
            confluence={"CONFLUENCE_SPACE_KEY": "MYSPACE"},
        )
        console = _make_console()
        run_setup_wizard(console)
        content = (tmp_path / ".env").read_text()
        assert "CONFLUENCE_SPACE_KEY=MYSPACE" in content

    def test_existing_config_preserved_on_rerun(self, monkeypatch, tmp_path):
        """--setup re-run merges new values with existing config keys."""
        config_file = _patch_config_file(monkeypatch, tmp_path)
        config_file.write_text("ANTHROPIC_API_KEY=sk-ant-old\nGITHUB_TOKEN=ghp_existing\n")
        _patch_provider(monkeypatch, "1")
        monkeypatch.setattr(
            "yeaboi.setup_wizard.prompt",
            _mock_inputs("sk-ant-new", "n", "n", "n"),
        )
        console = _make_console()
        run_setup_wizard(console)

        content = config_file.read_text()
        assert "ANTHROPIC_API_KEY=sk-ant-new" in content
        assert "GITHUB_TOKEN=ghp_existing" in content

    def test_env_updated_in_current_process(self, monkeypatch, tmp_path):
        _patch_config_file(monkeypatch, tmp_path)
        _patch_provider(monkeypatch, "1")
        monkeypatch.setattr(
            "yeaboi.setup_wizard.prompt",
            _mock_inputs("sk-ant-process-test", "n", "n", "n"),
        )
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        console = _make_console()
        run_setup_wizard(console)
        assert os.environ.get("ANTHROPIC_API_KEY") == "sk-ant-process-test"


# ---------------------------------------------------------------------------
# TestProviderSelect — unit tests for the full-screen selector logic
# ---------------------------------------------------------------------------


def _safe_key_fn(*keys):
    """Return a read_key function that yields given keys, then "esc" forever.

    After the explicit keys are exhausted, returns "esc" so any subsequent phase
    (API key input, VC selection, etc.) cleanly exits instead of crashing.
    """
    it = iter(keys)

    def _read(timeout=None):
        try:
            return next(it)
        except StopIteration:
            return "esc"

    return _read


class TestProviderSelect:
    """Test the provider selection UI component in isolation.

    After Phase 1 (provider selection), the flow enters Phase 2 (API key input).
    Tests that only care about Phase 1 navigation use _safe_key_fn which returns
    "esc" after the explicit keys — Phase 2's Esc triggers a recursive restart,
    so we use q/Esc cancel tests for that path. For navigation tests, we verify
    the function returns None (cancelled in Phase 2) and just check that Phase 1
    navigation didn't crash.
    """

    def test_q_cancels(self):
        from yeaboi.ui.provider_select import select_provider

        console = _make_console()
        result = select_provider(console, _read_key_fn=_safe_key_fn("q"))
        assert result is None

    def test_esc_cancels(self):
        from yeaboi.ui.provider_select import select_provider

        console = _make_console()
        result = select_provider(console, _read_key_fn=_safe_key_fn("esc"))
        assert result is None

    def test_enter_does_not_crash(self):
        """Pressing Enter on Phase 1 proceeds to Phase 2 (doesn't crash)."""
        from yeaboi.ui.provider_select import select_provider

        # After Enter selects Claude, Phase 2 gets "esc" → recursive restart → "esc" again
        console = _make_console()
        result = select_provider(console, _read_key_fn=_safe_key_fn("enter"))
        # Returns None because Esc cancels in Phase 2 → recursion → Esc again
        assert result is None

    def test_right_arrow_does_not_crash(self):
        """Right arrow navigates to next provider without crashing."""
        from yeaboi.ui.provider_select import select_provider

        console = _make_console()
        result = select_provider(console, _read_key_fn=_safe_key_fn("right", "esc"))
        assert result is None

    def test_left_wraps_does_not_crash(self):
        """Left from index 0 wraps to last provider without crashing."""
        from yeaboi.ui.provider_select import select_provider

        console = _make_console()
        result = select_provider(console, _read_key_fn=_safe_key_fn("left", "esc"))
        assert result is None


class TestVoiceTipTruthiness:
    """is_voice_available returns (available, reason). A bare truthiness test on
    that tuple is always True, so the wizard used to tell every user dictation
    was ready — installed or not."""

    def test_the_tip_reflects_a_missing_extra(self, monkeypatch, capsys):
        """Installable: point at the gesture, not at a shell command. The
        double-tap does the install, and sending the user to another terminal
        for it is the friction this whole feature removed."""
        from rich.console import Console

        import yeaboi.setup_wizard as wizard

        monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
        monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "installable")
        monkeypatch.setattr("yeaboi.voice.voice_install_command", lambda: "uv sync --extra voice")
        console = Console(force_terminal=False, color_system=None)
        wizard._print_voice_tip(console)
        out = capsys.readouterr().out
        assert "double-tap Space" in out
        assert "uv sync --extra voice" not in out
        assert "is ready" not in out

    def test_the_tip_falls_back_to_a_command_once_declined(self, monkeypatch, capsys):
        """ "Never" was the answer to the offer, not to dictation — so this is
        the one state where naming the manual command is the right move."""
        from rich.console import Console

        import yeaboi.setup_wizard as wizard

        monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
        monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "declined")
        monkeypatch.setattr("yeaboi.voice.voice_install_command", lambda: "uv sync --extra voice")
        console = Console(force_terminal=False, color_system=None)
        wizard._print_voice_tip(console)
        assert "uv sync --extra voice" in capsys.readouterr().out

    def test_the_tip_never_prints_a_doomed_command(self, monkeypatch, capsys):
        """The wizard is the first surface a user meets, so it is the worst one
        to print an install command that physically cannot succeed."""
        from rich.console import Console

        import yeaboi.setup_wizard as wizard

        monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
        monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "unsupported")
        monkeypatch.setattr("yeaboi.voice.unsupported_blocker", lambda: "musl libc has no speech-engine wheel")
        monkeypatch.setattr("yeaboi.voice.voice_install_command", lambda: "uv sync --extra voice")
        console = Console(force_terminal=False, color_system=None)
        wizard._print_voice_tip(console)
        out = capsys.readouterr().out
        assert "musl libc" in out
        assert "uv sync --extra voice" not in out

    def test_the_tip_says_ready_only_when_it_is(self, monkeypatch, capsys):
        from rich.console import Console

        import yeaboi.setup_wizard as wizard

        monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
        monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
        console = Console(force_terminal=False, color_system=None)
        wizard._print_voice_tip(console)
        assert "ready" in capsys.readouterr().out

    def test_tips_off_prints_nothing(self, monkeypatch, capsys):
        from rich.console import Console

        import yeaboi.setup_wizard as wizard

        monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: False)
        wizard._print_voice_tip(Console(force_terminal=False, color_system=None))
        assert capsys.readouterr().out.strip() == ""


class TestOfferCatalog:
    """The post-save catalog hand-off must never change a scripted run."""

    def _console(self):
        from rich.console import Console

        return Console(force_terminal=False, color_system=None)

    def test_a_non_tty_run_is_untouched(self, monkeypatch, capsys):
        import yeaboi.setup_wizard as wizard

        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("a non-TTY run must not prompt"))
        wizard._offer_catalog(self._console())
        assert capsys.readouterr().out.strip() == ""

    def test_declining_points_at_the_catalog_and_opens_nothing(self, monkeypatch, capsys):
        import yeaboi.setup_wizard as wizard

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda *a: "")
        monkeypatch.setattr(
            "yeaboi.ui.catalog.run_catalog_browser_standalone",
            lambda *a, **k: pytest.fail("declining must not open the browser"),
        )
        wizard._offer_catalog(self._console())
        out = capsys.readouterr().out
        assert "connections list --all" in out

    def test_accepting_opens_the_browser_and_echoes_its_status(self, monkeypatch, capsys):
        import yeaboi.setup_wizard as wizard

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda *a: "y")
        monkeypatch.setattr("yeaboi.ui.catalog.run_catalog_browser_standalone", lambda *a, **k: "GitLab verified")
        wizard._offer_catalog(self._console())
        assert "GitLab verified" in capsys.readouterr().out

    def test_nothing_left_to_connect_stays_silent(self, monkeypatch, capsys):
        import yeaboi.setup_wizard as wizard

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        connected = {"connectors": [{"connected": True}]}
        monkeypatch.setattr("yeaboi.connectors.engine.list_connections", lambda **k: connected)
        monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("nothing to offer means no prompt"))
        wizard._offer_catalog(self._console())
        assert capsys.readouterr().out.strip() == ""
