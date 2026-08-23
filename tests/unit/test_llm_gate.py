"""Tests for the pre-mode LLM credential gate (ui/shared/_llm_gate.py)."""

from __future__ import annotations

import ast
from io import StringIO
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from yeaboi.auth_state import CredentialStatus
from yeaboi.ui.shared._llm_gate import _build_checking_screen, _build_llm_gate_screen, show_llm_gate

_OK = CredentialStatus(ok=True, configured=True, reason=None, provider_label="Anthropic")
_NOT_CONFIGURED = CredentialStatus(
    ok=False, configured=False, reason="ANTHROPIC_API_KEY not set", provider_label="Anthropic"
)
_EXPIRED = CredentialStatus(ok=False, configured=True, reason="Invalid API key", provider_label="Anthropic")


def _render(panel: Panel, width: int = 100) -> str:
    buf = StringIO()
    Console(file=buf, width=width, legacy_windows=False).print(panel)
    return buf.getvalue()


class TestBuildCheckingScreen:
    def test_returns_a_panel_naming_the_provider(self):
        panel = _build_checking_screen(provider_label="Anthropic", width=100, height=30)
        assert isinstance(panel, Panel)
        assert "Anthropic" in _render(panel)


class TestBuildLlmGateScreen:
    def test_returns_a_panel(self):
        assert isinstance(_build_llm_gate_screen(_EXPIRED), Panel)

    def test_back_is_preselected(self):
        # Proceeding accepts placeholder output in place of a written analysis,
        # so a stray keypress must not be what chooses it.
        from yeaboi.ui.shared._llm_gate import _ACTIONS, _DEFAULT_ACTION

        assert _ACTIONS[_DEFAULT_ACTION] == "Back"

    def test_continue_anyway_has_its_own_button_colour(self):
        # Registered, so the risky action is visually distinct from the safe
        # one rather than falling back to the same grey (tui-standards rule 7).
        from yeaboi.ui.shared._components import _BTN_COLORS, _BTN_DEFAULT

        assert _BTN_COLORS.get("Continue anyway") not in (None, _BTN_DEFAULT)

    def test_shows_the_reason_and_buttons(self):
        out = _render(_build_llm_gate_screen(_EXPIRED, width=100, height=32))
        assert "Invalid API key" in out
        assert "Continue anyway" in out and "Back" in out

    def test_not_configured_has_a_different_headline_than_invalid(self):
        configured_out = _render(_build_llm_gate_screen(_EXPIRED, width=100, height=32))
        missing_out = _render(_build_llm_gate_screen(_NOT_CONFIGURED, width=100, height=32))
        assert "No Anthropic API key is configured" in missing_out
        assert "looks invalid" in configured_out


class _FakeConsole:
    size = (100, 30)


class _FakeLive:
    def __init__(self):
        self.frames = 0

    def update(self, _panel):
        self.frames += 1


def _run(keys, *, status=_EXPIRED):
    it = iter(keys)

    def _read_key(timeout=None):
        return next(it)

    live = _FakeLive()
    result = show_llm_gate(live, _FakeConsole(), _read_key, 0.05, True, check=lambda: status)
    return result, live


class TestShowLlmGate:
    def test_ok_status_renders_nothing_and_returns_immediately(self):
        result, live = _run([], status=_OK)  # would StopIteration if it tried to read a key

        assert result is True
        assert live.frames == 0

    def test_continue_anyway_proceeds(self):
        # "Back" is pre-selected, so reaching Continue takes a deliberate move.
        result, live = _run(["left", "enter"])

        assert result is True
        assert live.frames >= 1

    def test_enter_on_the_default_declines(self):
        # The safe default: a bare Enter must not be what accepts placeholder output.
        result, _ = _run(["enter"])

        assert result is False

    def test_esc_declines(self):
        result, _ = _run(["esc"])

        assert result is False

    def test_idle_ticks_are_not_keypresses(self):
        result, live = _run(["", "", "enter"])

        assert result is False
        assert live.frames == 3

    def test_read_key_without_a_timeout_kwarg_still_works(self):
        keys = iter(["left", "enter"])

        def _read_key_no_timeout():
            return next(keys)

        live = _FakeLive()
        result = show_llm_gate(live, _FakeConsole(), _read_key_no_timeout, 0.05, True, check=lambda: _EXPIRED)

        assert result is True

    def test_a_broken_key_is_reported_every_call_no_persistence(self):
        # Unlike the beta notice, there is no "seen once" flag — a second call
        # with the same broken status must still block.
        first, _ = _run(["enter"], status=_EXPIRED)
        second, _ = _run(["enter"], status=_EXPIRED)

        assert first is False
        assert second is False


class TestShowLlmGateClicks:
    def _click_key(self, console, label_index: int) -> str:
        from yeaboi.ui.shared._click import _button_runs

        panel = _build_llm_gate_screen(_EXPIRED, width=100, height=30)
        lines = console.render_lines(panel, console.options, pad=True)
        row = next(r for r, ln in enumerate(lines) if len(_button_runs("".join(s.text for s in ln))) == 2)
        start, end = _button_runs("".join(s.text for s in lines[row]))[label_index]
        return f"click:{(start + end) // 2}:{row + 2}"

    def _run_clicks(self, keys):
        console = Console(file=StringIO(), width=100, height=30, legacy_windows=False)
        resolved = [self._click_key(console, k) if isinstance(k, int) else k for k in keys]
        it = iter(resolved)
        live = _FakeLive()
        return show_llm_gate(live, console, lambda timeout=None: next(it), 0.05, True, check=lambda: _EXPIRED)

    def test_clicking_continue_proceeds(self):
        assert self._run_clicks([0]) is True

    def test_clicking_back_declines(self):
        assert self._run_clicks([1]) is False

    def test_clicking_off_the_button_row_is_ignored(self):
        assert self._run_clicks(["click:50:3", 1]) is False


class TestCheckWithSpinner:
    """The real (non-injected) path: a worker thread pings while the render loop
    keeps calling live.update, and never takes the app down with it."""

    def test_runs_the_probe_off_thread_and_returns_its_result(self, monkeypatch):
        from yeaboi.ui.shared import _llm_gate

        monkeypatch.setattr(_llm_gate, "check_llm_credentials", lambda: _OK)
        live = _FakeLive()

        result = _llm_gate._check_with_spinner(live, _FakeConsole(), 0.01)

        assert result is _OK
        assert live.frames >= 0  # a fast fake probe may finish before a frame renders

    def test_a_probe_that_raises_passes_the_user_through(self, monkeypatch):
        # A gate whose job is to stop a broken key breaking a mode must not be
        # able to crash the app itself.
        from yeaboi.ui.shared import _llm_gate

        def _boom():
            raise RuntimeError("provider exploded")

        monkeypatch.setattr(_llm_gate, "check_llm_credentials", _boom)

        result = _llm_gate._check_with_spinner(_FakeLive(), _FakeConsole(), 0.01)

        assert result.ok is True

    def test_a_probe_that_hangs_is_capped_and_passes_through(self, monkeypatch):
        # The TUI clears ISIG, so an unbounded probe would be unkillable from
        # inside the app. A timed-out probe is not evidence about the key.
        import threading

        from yeaboi.ui.shared import _llm_gate

        release = threading.Event()
        monkeypatch.setattr(_llm_gate, "_PROBE_TIMEOUT_S", 0.05)
        monkeypatch.setattr(_llm_gate, "check_llm_credentials", lambda: (release.wait(10), _EXPIRED)[1])
        try:
            result = _llm_gate._check_with_spinner(_FakeLive(), _FakeConsole(), 0.01)
            assert result.ok is True  # passed through, NOT reported as a bad key
        finally:
            release.set()  # let the daemon thread finish


def test_default_check_is_the_live_credential_check(monkeypatch):
    """Omitting ``check`` must use the real probe, not silently pass."""
    from yeaboi.ui.shared import _llm_gate

    monkeypatch.setattr(_llm_gate, "check_llm_credentials", lambda: _EXPIRED)
    keys = iter(["enter"])  # "Back" is pre-selected

    result = show_llm_gate(_FakeLive(), _FakeConsole(), lambda timeout=None: next(keys), 0.01, True)

    assert result is False  # the live status reached the modal and it blocked


class TestHubWiring:
    """The gate is only worth anything if the hub calls it correctly.

    `select_mode` is one 3,000-line function that cannot be imported and driven
    in a unit test, and the one path the pty smoke test exercises (`--dry-run`)
    skips the gate entirely — so this reads the source, the way
    test_mode_select_callsites.py does.
    """

    _HUB = Path("src/yeaboi/ui/mode_select/__init__.py")

    def _gate_if(self) -> ast.If:
        tree = ast.parse(self._HUB.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            if any(
                isinstance(c, ast.Call) and getattr(c.func, "id", None) == "show_llm_gate" for c in ast.walk(node.test)
            ):
                return node
        raise AssertionError("no `if ... show_llm_gate(...)` guard found in the hub")

    def test_declining_returns_to_the_menu_rather_than_quitting(self):
        """The bug this pins: a bare `continue` exits `while _restart_mode_select`.

        That falls out of `select_mode` into `return None`, which `cli.py` reads
        as "the user quit" — so Back and Esc killed the whole app instead of
        going back one screen.
        """
        assigned = {
            t.id
            for stmt in self._gate_if().body
            if isinstance(stmt, ast.Assign)
            for t in stmt.targets
            if isinstance(t, ast.Name)
        }
        assert "_restart_mode_select" in assigned, (
            "declining the gate must set _restart_mode_select = True before `continue`, "
            "or the loop exits and the app quits"
        )
        assert "_skip_fade_in" in assigned, "match the other return-to-menu branches (e.g. the beta-notice ones)"
        assert any(isinstance(stmt, ast.Continue) for stmt in self._gate_if().body)

    def test_exemptions_live_on_the_card_not_in_the_call_site(self):
        # A string literal here is the "author must remember" pattern the gate
        # exists to avoid; the card carries its own opt-out instead.
        source = ast.unparse(self._gate_if().test)
        assert "'llm'" in source or '"llm"' in source
        assert "settings" not in source

    def test_only_the_non_llm_cards_opt_out(self):
        from yeaboi.ui.mode_select.screens._screens import _AGENT_CARDS, _MODE_CARDS

        opted_out = {c["key"] for c in [*_MODE_CARDS, *_AGENT_CARDS] if not c.get("llm", True)}
        assert opted_out == {"usage", "settings"}
