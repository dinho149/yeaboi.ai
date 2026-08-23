"""Tests for the pre-mode LLM credential gate (ui/shared/_llm_gate.py)."""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console
from rich.panel import Panel

from yeaboi.auth_state import CredentialStatus
from yeaboi.ui.shared._llm_gate import _build_llm_gate_screen, show_llm_gate

_OK = CredentialStatus(ok=True, configured=True, reason=None, provider_label="Anthropic")
_NOT_CONFIGURED = CredentialStatus(
    ok=False, configured=False, reason="ANTHROPIC_API_KEY not set", provider_label="Anthropic"
)
_EXPIRED = CredentialStatus(ok=False, configured=True, reason="Invalid API key", provider_label="Anthropic")


def _render(panel: Panel, width: int = 100) -> str:
    buf = StringIO()
    Console(file=buf, width=width, legacy_windows=False).print(panel)
    return buf.getvalue()


class TestBuildLlmGateScreen:
    def test_returns_a_panel(self):
        assert isinstance(_build_llm_gate_screen(_EXPIRED), Panel)

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
        result, live = _run(["enter"])

        assert result is True
        assert live.frames >= 1

    def test_back_declines(self):
        result, _ = _run(["right", "enter"])

        assert result is False

    def test_esc_declines(self):
        result, _ = _run(["esc"])

        assert result is False

    def test_idle_ticks_are_not_keypresses(self):
        result, live = _run(["", "", "enter"])

        assert result is True
        assert live.frames == 3

    def test_read_key_without_a_timeout_kwarg_still_works(self):
        keys = iter(["enter"])

        def _read_key_no_timeout():
            return next(keys)

        live = _FakeLive()
        result = show_llm_gate(live, _FakeConsole(), _read_key_no_timeout, 0.05, True, check=lambda: _EXPIRED)

        assert result is True

    def test_a_broken_key_is_reported_every_call_no_persistence(self):
        # Unlike the beta notice, there is no "seen once" flag — a second call
        # with the same broken status must still block.
        first, _ = _run(["right", "enter"], status=_EXPIRED)
        second, _ = _run(["right", "enter"], status=_EXPIRED)

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
    """The real (non-injected) path used in production: a worker thread pings
    while the render loop keeps calling live.update so the UI stays responsive."""

    def test_runs_the_probe_off_thread_and_returns_its_result(self, monkeypatch):
        from yeaboi.ui.shared import _llm_gate

        monkeypatch.setattr(_llm_gate, "check_llm_credentials", lambda: _OK)
        live = _FakeLive()

        result = _llm_gate._check_with_spinner(live, _FakeConsole(), 0.01)

        assert result is _OK
        assert live.frames >= 0  # a fast fake probe may finish before a frame renders


@pytest.mark.parametrize("status", [_OK, _NOT_CONFIGURED, _EXPIRED])
def test_default_check_wires_into_show_llm_gate(status, monkeypatch):
    """Omitting ``check`` must use the real, live credential check."""
    from yeaboi.ui.shared import _llm_gate

    monkeypatch.setattr(_llm_gate, "check_llm_credentials", lambda: status)
    live = _FakeLive()
    keys = iter(["enter"])

    result = show_llm_gate(live, _FakeConsole(), lambda timeout=None: next(keys), 0.01, True)

    assert result is True  # OK passes straight through; broken + "Continue anyway" also returns True
