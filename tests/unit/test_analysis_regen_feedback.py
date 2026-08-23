"""Unit tests for the feedback-before-regenerate flow on the analysis preview pages.

Covers: _ask_regen_feedback (Esc cancels, empty Enter regenerates as-is, typed
text returned) and the theme/title re-branding of _build_standup_input_screen.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from rich.panel import Panel

from yeaboi.ui.mode_select import _ask_regen_feedback


def _drive(keys: list[str]) -> str | None:
    """Run _ask_regen_feedback against a scripted key sequence and stub Live."""
    console = MagicMock()
    console.size = (100, 30)
    live = MagicMock()
    key_iter = iter(keys)

    def read_key(timeout=None):
        return next(key_iter)

    return _ask_regen_feedback(console, live, read_key, 0.05, True, "epic")


class TestAskRegenFeedback:
    """Drive the feedback prompt with scripted keys."""

    def test_typed_feedback_returned(self):
        assert _drive(list("fix it") + ["enter"]) == "fix it"

    def test_empty_enter_returns_empty_string(self):
        """Empty Enter = regenerate as-is (no feedback)."""
        assert _drive(["enter"]) == ""

    def test_esc_returns_none(self):
        """Esc = cancel the regenerate entirely."""
        assert _drive(["esc"]) is None

    def test_backspace_edits_value(self):
        assert _drive(list("abc") + ["backspace", "enter"]) == "ab"

    def test_paste_appended(self):
        assert _drive(["paste:shorter titles", "enter"]) == "shorter titles"


class TestInputScreenBranding:
    """_build_standup_input_screen honours theme/title overrides."""

    def test_default_is_standup_branding(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_input_screen

        panel = _build_standup_input_screen("What should change?", "", step="Regenerate epic")
        assert isinstance(panel, Panel)

    def test_analysis_theme_and_title_accepted(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_input_screen
        from yeaboi.ui.shared._components import ANALYSIS_THEME, analysis_title

        panel = _build_standup_input_screen(
            "What should change?",
            "less jargon",
            step="Regenerate epic — feedback",
            theme=ANALYSIS_THEME,
            title=analysis_title(),
            width=100,
            height=30,
        )
        assert isinstance(panel, Panel)

    def test_context_message_and_step_dots_render(self):
        """A text step can be the first screen a wizard shows (an earlier step
        was skipped), so it must carry its own framing: the progress dots and a
        context paragraph, not a bare input box."""
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_input_screen

        buf = StringIO()
        console = Console(file=buf, width=110, force_terminal=False)
        console.print(
            _build_standup_input_screen(
                "Hostname to serve boards on",
                "",
                step="Hostname",
                width=110,
                height=30,
                message="yeaboi points this address at a tunnel in your Cloudflare account.",
                step_names=["Sign in", "Hostname", "Access app", "Verify"],
                step_index=1,
            )
        )
        out = buf.getvalue()
        assert "tunnel in your Cloudflare account" in out
        assert "Sign in" in out and "Access app" in out  # the dots strip
        assert "Configure standup" not in out

    def test_page_tint_follows_the_theme(self):
        """The page panel must wear the caller's theme, not standup's — a
        settings-branded wizard on a standup-tinted page reads as the wrong mode."""
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_input_screen
        from yeaboi.ui.shared._components import ANALYSIS_THEME, STANDUP_THEME

        panel = _build_standup_input_screen("Q", "", theme=ANALYSIS_THEME)
        assert ANALYSIS_THEME.bg in str(panel.style)
        default = _build_standup_input_screen("Q", "")
        assert STANDUP_THEME.bg in str(default.style)

    def test_analysis_title_rendered(self):
        """The overridden title actually replaces the standup ASCII art."""
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_input_screen
        from yeaboi.ui.shared._components import ANALYSIS_THEME, analysis_title

        buf = StringIO()
        console = Console(file=buf, width=120, force_terminal=False)
        console.print(
            _build_standup_input_screen(
                "What should change?",
                "",
                step="Regenerate epic — feedback",
                theme=ANALYSIS_THEME,
                title=analysis_title(),
                width=120,
                height=30,
            )
        )
        out = buf.getvalue()
        assert "What should change?" in out
        assert "Regenerate epic" in out


class TestMultiRowInputBox:
    """box_rows > 1 renders a large wrapping text box (used by regen feedback)."""

    @staticmethod
    def _render(value: str, *, box_rows: int, width: int = 120, height: int = 38) -> str:
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_input_screen
        from yeaboi.ui.shared._components import ANALYSIS_THEME, analysis_title

        buf = StringIO()
        Console(file=buf, width=width, force_terminal=False).print(
            _build_standup_input_screen(
                "What should change? (Enter to regenerate as-is)",
                value,
                step="Regenerate epic — feedback",
                theme=ANALYSIS_THEME,
                title=analysis_title(),
                width=width,
                height=height,
                box_rows=box_rows,
            )
        )
        return buf.getvalue()

    def test_large_box_has_requested_rows(self):
        out = self._render("short feedback", box_rows=6)
        # 6 interior rows -> 6 lines whose content sits between two │ box edges
        interior_rows = [ln for ln in out.splitlines() if ln.count("│") >= 4]
        assert len(interior_rows) == 6

    def test_value_and_cursor_render(self):
        out = self._render("make the title shorter", box_rows=6)
        assert "make the title shorter█" in out

    def test_long_text_wraps_and_cursor_stays_visible(self):
        out = self._render("word " * 80, box_rows=6)
        assert "█" in out
        # wrapped across multiple interior rows
        rows_with_text = [ln for ln in out.splitlines() if ln.count("│") >= 4 and "word" in ln]
        assert len(rows_with_text) >= 2

    def test_small_terminal_clamps_box_keeps_hint(self):
        out = self._render("x", box_rows=6, height=22)
        assert "Enter to confirm" in out
        assert "█" in out
        interior_rows = [ln for ln in out.splitlines() if ln.count("│") >= 4]
        assert 2 <= len(interior_rows) < 6

    def test_single_row_default_unchanged(self):
        out = self._render("hello", box_rows=1)
        assert " hello█" in out
        interior_rows = [ln for ln in out.splitlines() if ln.count("│") >= 4]
        assert len(interior_rows) == 1
