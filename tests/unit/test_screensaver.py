"""Tests for app-wide idle tracking and the animated ANSI screensaver."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.shared import _input, _screensaver
from yeaboi.ui.shared._music_bar import make_live
from yeaboi.ui.shared._screensaver import IdleController, build_screensaver


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _controller(seconds: float = 300) -> tuple[IdleController, FakeClock]:
    clock = FakeClock()
    return IdleController(idle_seconds=seconds, clock=clock), clock


def test_activates_at_idle_boundary_and_polling_does_not_reset():
    controller, clock = _controller()
    controller.begin_input_wait()
    clock.advance(299)
    controller.begin_input_wait()  # another timed input poll, not activity
    assert controller.should_show() is False
    clock.advance(1)
    assert controller.should_show() is True


def test_first_event_wakes_only_then_next_event_is_actionable():
    controller, clock = _controller(seconds=5)
    controller.begin_input_wait()
    clock.advance(5)
    assert controller.should_show() is True
    assert controller.handle_input_event() is True
    assert controller.should_show() is False
    assert controller.handle_input_event() is False


def test_processing_is_excluded_and_idle_restarts_afterward(monkeypatch):
    controller, clock = _controller(seconds=10)
    monkeypatch.setattr(_screensaver, "idle_controller", controller)
    controller.begin_input_wait()
    clock.advance(9)

    with _screensaver.suppress_screensaver():
        clock.advance(1000)
        controller.begin_input_wait()
        assert controller.should_show() is False

    controller.begin_input_wait()
    clock.advance(9)
    assert controller.should_show() is False
    clock.advance(1)
    assert controller.should_show() is True


def test_read_key_consumes_wake_before_music_shortcut(monkeypatch):
    controller, clock = _controller(seconds=1)
    monkeypatch.setattr(_screensaver, "idle_controller", controller)
    controller.begin_input_wait()
    clock.advance(1)
    assert controller.should_show() is True

    def fake_read(**_kwargs):
        _input._last_read_had_input = True
        return "ctrl+p"

    toggles: list[bool] = []
    monkeypatch.setattr(_input, "_read_key_impl", fake_read)
    monkeypatch.setattr("yeaboi.music.toggle", lambda: toggles.append(True))

    assert _input.read_key(timeout=0) == ""
    assert toggles == []

    # The same shortcut is actionable after the saver has been dismissed.
    assert _input.read_key(timeout=0) == ""
    assert toggles == [True]


def test_ctrl_y_previews_saver_and_ctrl_y_again_only_wakes(monkeypatch):
    controller, _clock = _controller()
    monkeypatch.setattr(_screensaver, "idle_controller", controller)

    def fake_read(**_kwargs):
        _input._last_read_had_input = True
        return "ctrl+y"

    monkeypatch.setattr(_input, "_read_key_impl", fake_read)

    assert _input.read_key(timeout=0) == ""
    assert controller.should_show() is True
    assert _input.read_key(timeout=0) == ""
    assert controller.should_show() is False


def test_ctrl_y_preview_is_ignored_during_processing(monkeypatch):
    controller, _clock = _controller()
    monkeypatch.setattr(_screensaver, "idle_controller", controller)

    def fake_read(**_kwargs):
        _input._last_read_had_input = True
        return "ctrl+y"

    monkeypatch.setattr(_input, "_read_key_impl", fake_read)
    with _screensaver.suppress_screensaver():
        assert _input.read_key(timeout=0) == ""
        assert controller.should_show() is False


class TestSaverOff:
    """The one style value the terminal understands from the shared catalogue."""

    def test_idling_never_takes_the_screen_over(self, monkeypatch):
        monkeypatch.setenv("SAVER_STYLE", "off")
        controller, clock = _controller(seconds=5)
        controller.begin_input_wait()
        clock.advance(1000)
        assert controller.should_show() is False

    def test_ctrl_y_does_nothing(self, monkeypatch):
        monkeypatch.setenv("SAVER_STYLE", "off")
        controller, _clock = _controller()
        assert controller.show_now() is False
        assert controller.should_show() is False

    def test_every_other_style_still_draws_the_ducks(self, monkeypatch):
        # The terminal has no canvas; a desktop-only style is not a reason to
        # leave a terminal user without the saver they already had.
        monkeypatch.setenv("SAVER_STYLE", "aurora")
        controller, _clock = _controller()
        assert controller.show_now() is True
        assert controller.should_show() is True

    def test_turning_it_back_on_needs_no_restart(self, monkeypatch):
        monkeypatch.setenv("SAVER_STYLE", "off")
        controller, clock = _controller(seconds=5)
        controller.begin_input_wait()
        clock.advance(1000)
        assert controller.should_show() is False
        monkeypatch.setenv("SAVER_STYLE", "duck-yard")
        assert controller.should_show() is True


def test_live_swaps_saver_without_losing_underlying_renderable(monkeypatch):
    controller, clock = _controller(seconds=1)
    monkeypatch.setattr(_screensaver, "idle_controller", controller)
    underlying = Text("underlying")
    # Above the min-size floor so the app-wide too-small guard doesn't intercept.
    live = make_live(underlying, console=Console(width=100, height=45))

    controller.begin_input_wait()
    clock.advance(1)
    assert live.get_renderable() is not underlying

    assert controller.handle_input_event() is True
    assert live.get_renderable() is underlying


def test_full_compact_and_tiny_layouts_fit_the_terminal():
    for width, height in ((80, 24), (30, 14), (18, 5)):
        console = Console(width=width, height=height, color_system=None)
        lines = console.render_lines(
            build_screensaver(width=width, height=height, elapsed=0.25),
            console.options.update(width=width, height=height),
            pad=False,
        )
        assert len(lines) <= height
        assert all(sum(segment.cell_length for segment in line) <= width for line in lines)


def test_screensaver_large_uses_full_duck():
    saver = build_screensaver(width=60, height=22, elapsed=0.0)
    assert isinstance(saver, Panel)  # framed so the border stays put on idle


def test_screensaver_compact_tier_renders():
    saver = build_screensaver(width=30, height=15, elapsed=0.0)
    assert isinstance(saver, Panel)


def test_screensaver_tiny_tier_renders():
    saver = build_screensaver(width=10, height=4, elapsed=0.0)
    assert isinstance(saver, Panel)


def test_screensaver_is_framed_with_a_border():
    # The saver must keep the app's rounded border when it takes over the screen.
    con = Console(width=60, height=24, record=True, file=open("/dev/null", "w"))
    con.print(build_screensaver(width=60, height=24, elapsed=0.0))
    text = con.export_text()
    assert "╭" in text and "╰" in text  # rounded box corners present


def test_screensaver_animates_between_frames():
    def rendered(elapsed):
        con = Console(width=60, height=22, record=True, file=open("/dev/null", "w"))
        con.print(build_screensaver(width=60, height=22, elapsed=elapsed))
        return con.export_text()

    assert rendered(0.0) != rendered(0.375)  # frame 0 vs frame 3 (wing lifted)


def test_screensaver_has_no_caption_or_hint():
    # The "chilling" caption and "press any key" hint were removed — the saver is
    # just the duck. Check the standing full tier (22) and the walking tier (28).
    for h in (22, 28):
        con = Console(width=60, height=h, record=True, file=open("/dev/null", "w"))
        con.print(build_screensaver(width=60, height=h, elapsed=0.0))
        text = con.export_text()
        assert "chilling" not in text and "press any key" not in text
        assert any(ch in text for ch in "▀▄█")  # the duck art still renders


class TestSaverMascot:
    """Idling on an Agents page keeps the robo — the saver reads the chrome mascot."""

    def _styled(self, width, height, monkeypatch, mascot):
        from io import StringIO

        from rich.console import Console

        from yeaboi.ui.shared import _music_bar, _screensaver

        monkeypatch.setattr(_music_bar, "_chrome_mascot", mascot)
        # Pinned truecolor: auto-detection reads COLORTERM (set in dev shells,
        # unset on CI), which would downgrade these rgb assertions to 8-colour.
        console = Console(width=width, height=height, file=StringIO(), force_terminal=True, color_system="truecolor")
        with console.capture() as cap:
            console.print(_screensaver.build_screensaver(width=width, height=height, elapsed=1.0))
        return cap.get()

    def test_standing_tier_robo(self, monkeypatch):
        out = self._styled(50, 23, monkeypatch, "robo")
        assert "140;160;178" in out
        assert "34;158;122" not in out

    def test_compact_tier_robo(self, monkeypatch):
        out = self._styled(30, 15, monkeypatch, "robo")
        assert "140;160;178" in out
        assert "34;158;122" not in out

    def test_walking_tier_robo(self, monkeypatch):
        out = self._styled(50, 27, monkeypatch, "robo")
        assert "140;160;178" in out
        assert "34;158;122" not in out

    def test_mayhem_tier_robo(self, monkeypatch):
        out = self._styled(80, 30, monkeypatch, "robo")
        assert "140;160;178" in out
        assert "34;158;122" not in out

    def test_duck_tiers_unchanged(self, monkeypatch):
        assert "34;158;122" in self._styled(50, 23, monkeypatch, "duck")
        assert "34;158;122" in self._styled(80, 30, monkeypatch, "duck")

    def test_mayhem_yard_rebuilds_on_mascot_flip(self, monkeypatch):
        # Same size/seed/scale — only the mascot changes; the yard key must
        # rebuild so squashed's cleared cache never serves duck frames.
        from yeaboi.ui.shared import _mayhem

        _mayhem.render(80, 30, 0.5, mascot="robo")
        assert _mayhem.MASCOT == "robo"
        _mayhem.render(80, 30, 0.5, mascot="duck")
        assert _mayhem.MASCOT == "duck"
