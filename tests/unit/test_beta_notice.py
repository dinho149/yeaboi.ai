"""Tests for the one-time beta entry notice (ui/shared/_beta_notice.py)."""

from io import StringIO

import pytest
from rich.console import Console
from rich.panel import Panel

from yeaboi.beta import BETA_LABEL
from yeaboi.config import BETA_ACK_KEY, FORCE_BETA_NOTICE_ENV, is_beta_notice_seen
from yeaboi.ui.shared._beta_notice import _build_beta_notice_screen, show_beta_notice


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch, tmp_path):
    """Keep the acknowledgement out of the developer's real ~/.yeaboi/.env."""
    monkeypatch.setattr("yeaboi.config.get_config_file", lambda: tmp_path / ".env")
    monkeypatch.delenv(BETA_ACK_KEY, raising=False)
    monkeypatch.delenv(FORCE_BETA_NOTICE_ENV, raising=False)


def _render(panel: Panel, width: int = 100) -> str:
    buf = StringIO()
    Console(file=buf, width=width, legacy_windows=False).print(panel)
    return buf.getvalue()


class TestBuildBetaNoticeScreen:
    def test_returns_a_panel(self):
        assert isinstance(_build_beta_notice_screen(mode_key="performance"), Panel)

    def test_shows_the_badge_headline_and_buttons(self):
        out = _render(_build_beta_notice_screen(mode_key="performance", width=100, height=32))
        assert BETA_LABEL in out
        assert "Performance is in beta." in out
        assert "Continue" in out and "Back" in out

    def test_says_what_can_go_wrong_and_what_stays_local(self):
        # The point of the screen is the specifics; a generic disclaimer would
        # pass a "renders without error" test just as well.
        out = _render(_build_beta_notice_screen(mode_key="performance", width=100, height=32))
        assert "not an assessment" in out
        assert "Nothing is sent to anyone automatically" in out

    def test_header_stays_two_rows_at_a_narrow_width(self):
        out = _render(_build_beta_notice_screen(mode_key="performance", width=60, height=30), width=60)
        glyph_rows = [line for line in out.splitlines() if any(ch in line for ch in "█▀▄")]
        assert len(glyph_rows) == 2


class _FakeConsole:
    size = (100, 30)


class _FakeLive:
    def __init__(self):
        self.frames = 0

    def update(self, _panel):
        self.frames += 1


def _run(keys, *, mode_key="performance"):
    it = iter(keys)

    def _read_key(timeout=None):
        return next(it)

    live = _FakeLive()
    result = show_beta_notice(live, _FakeConsole(), _read_key, 0.05, True, mode_key=mode_key)
    return result, live


class TestShowBetaNotice:
    def test_continue_enters_the_mode_and_records_the_ack(self):
        result, live = _run(["enter"])

        assert result is True
        assert live.frames >= 1
        assert is_beta_notice_seen("performance") is True

    def test_back_returns_to_the_menu_without_recording(self):
        # Someone who backed out hasn't read it — they get told again next time.
        result, _ = _run(["right", "enter"])

        assert result is False
        assert is_beta_notice_seen("performance") is False

    def test_esc_returns_to_the_menu_without_recording(self):
        result, _ = _run(["esc"])

        assert result is False
        assert is_beta_notice_seen("performance") is False

    def test_idle_ticks_are_not_keypresses(self):
        result, live = _run(["", "", "enter"])

        assert result is True
        assert live.frames == 3

    def test_already_acked_renders_nothing(self, monkeypatch):
        monkeypatch.setenv(BETA_ACK_KEY, "performance")

        result, live = _run([])  # would StopIteration if it tried to read a key

        assert result is True
        assert live.frames == 0

    def test_force_flag_regates_an_acked_mode(self, monkeypatch):
        monkeypatch.setenv(BETA_ACK_KEY, "performance")
        monkeypatch.setenv(FORCE_BETA_NOTICE_ENV, "1")

        result, live = _run(["enter"])

        assert result is True
        assert live.frames >= 1

    def test_mode_without_a_notice_passes_straight_through(self):
        # Only registered beta modes are gated; everything else must not be
        # blocked by a screen that has no copy written for it.
        result, live = _run([], mode_key="reporting")

        assert result is True
        assert live.frames == 0

    def test_read_key_without_a_timeout_kwarg_still_works(self):
        """Some phase loops pass a `_key()` that doesn't accept `timeout=`.

        The export picker carries the same TypeError fallback; without it the
        notice would explode on exactly those callers.
        """
        keys = iter(["enter"])

        def _read_key_no_timeout():  # no timeout parameter at all
            return next(keys)

        live = _FakeLive()
        result = show_beta_notice(live, _FakeConsole(), _read_key_no_timeout, 0.05, True, mode_key="performance")

        assert result is True
        assert is_beta_notice_seen("performance") is True


class TestShowBetaNoticeClicks:
    """Clicking a button must behave exactly like arrowing to it and pressing Enter."""

    def _click_key(self, console, label_index: int) -> str:
        """Build a `click:x:y` key aimed at the centre of a rendered button."""
        from yeaboi.ui.shared._click import _button_runs

        panel = _build_beta_notice_screen(mode_key="performance", width=100, height=30)
        lines = console.render_lines(panel, console.options, pad=True)
        row = next(r for r, ln in enumerate(lines) if len(_button_runs("".join(s.text for s in ln))) == 2)
        start, end = _button_runs("".join(s.text for s in lines[row]))[label_index]
        return f"click:{(start + end) // 2}:{row + 2}"

    def _run_clicks(self, keys):
        console = Console(file=StringIO(), width=100, height=30, legacy_windows=False)
        resolved = [self._click_key(console, k) if isinstance(k, int) else k for k in keys]
        it = iter(resolved)
        live = _FakeLive()
        result = show_beta_notice(live, console, lambda timeout=None: next(it), 0.05, True, mode_key="performance")
        return result

    def test_clicking_continue_enters_and_acks(self):
        assert self._run_clicks([0]) is True
        assert is_beta_notice_seen("performance") is True

    def test_clicking_back_returns_without_acking(self):
        assert self._run_clicks([1]) is False
        assert is_beta_notice_seen("performance") is False

    def test_clicking_off_the_button_row_is_ignored(self):
        # A stray click must not count as a dismissal of a one-time notice.
        assert self._run_clicks(["click:50:3", 1]) is False
        assert is_beta_notice_seen("performance") is False
