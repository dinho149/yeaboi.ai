"""Tests for the persistent music status bar (ui/shared/_music_bar.py)."""

import pytest
from rich.panel import Panel
from rich.text import Text

from yeaboi import music
from yeaboi.ui.shared import _music_bar
from yeaboi.ui.shared._music_bar import (
    _EQ_CHARS,
    MusicLive,
    _connecting_dots,
    _eq_bars,
    build_music_subtitle,
    make_live,
    nudge_music_bar,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    music._state = music._State()
    music._state._initialised = True
    _music_bar._active = None
    monkeypatch.setattr(music, "is_music_available", lambda: (True, ""))
    yield
    _music_bar._active = None


# ── Subtitle content ──────────────────────────────────────────────────────────


def test_subtitle_when_stopped():
    music._state.status = "stopped"
    text = build_music_subtitle().plain
    assert "off" in text
    assert "^P play" in text
    assert "^O channel" in text


def test_subtitle_when_playing():
    music._state.status = "playing"
    music._state.channel_idx = 0
    text = build_music_subtitle().plain
    assert music.CHANNELS[0]["name"] in text
    assert "playing" in text
    assert "^P pause" in text


def test_subtitle_when_paused():
    music._state.status = "paused"
    text = build_music_subtitle().plain
    assert "paused" in text
    assert "^P play" in text


def test_subtitle_shows_crash_notice_when_stopped_with_error():
    # A player that died on its own reverts to "stopped" but leaves a last_error;
    # the bar shows it instead of a bare "off" so a broken player is diagnosable.
    music._state.status = "stopped"
    music._state.last_error = "music stopped — stream unavailable, ^P to retry"
    text = build_music_subtitle().plain
    assert "stream unavailable" in text
    assert "off" not in text
    assert "^P play" in text


def test_eq_bars_shape():
    bars = _eq_bars(4)
    assert len(bars) == 4
    assert all(c in _EQ_CHARS for c in bars)


def test_playing_subtitle_includes_equalizer():
    music._state.status = "playing"
    text = build_music_subtitle().plain
    assert any(c in _EQ_CHARS for c in text)


def test_subtitle_when_connecting():
    # A freshly-spawned stream is "playing" but still buffering — the bar shows a
    # progress ellipsis, not the equalizer, so the silent gap doesn't look broken.
    music._state.status = "playing"
    music._state.started_at = music.time.monotonic()  # spawn just happened
    text = build_music_subtitle().plain
    assert "connecting" in text
    assert not any(c in _EQ_CHARS for c in text)  # no equalizer while buffering
    assert "^P pause" in text


def test_connecting_dots_shape(monkeypatch):
    # Always width 3 (padded), cycling 0..3 dots by the wall clock.
    monkeypatch.setattr(_music_bar.time, "monotonic", lambda: 0.0)
    assert _connecting_dots() == "   "
    monkeypatch.setattr(_music_bar.time, "monotonic", lambda: 1.2)  # ~3 dots
    dots = _connecting_dots()
    assert len(dots) == 3 and set(dots) <= {".", " "}


# ── MusicLive stamping ────────────────────────────────────────────────────────


def test_make_live_returns_music_live():
    assert isinstance(make_live(Text("")), MusicLive)


def test_stamps_bare_panel():
    ml = make_live(Text(""))
    panel = Panel(Text("body"))
    ml._stamp(panel)
    assert panel.subtitle is not None
    assert getattr(panel, "_music_stamped", False) is True


def test_leaves_existing_subtitle_untouched():
    ml = make_live(Text(""))
    panel = Panel(Text("body"), subtitle="Board required")
    ml._stamp(panel)
    assert panel.subtitle == "Board required"  # a popup's own subtitle survives


def test_ignores_non_panel_renderables():
    ml = make_live(Text(""))
    ml._stamp(Text("plain"))  # must not raise
    assert ml._stamped is False


def test_unstyled_panel_gains_neutral_background():
    from yeaboi.ui.shared._components import NEUTRAL_BG

    ml = make_live(Text(""))
    panel = Panel(Text("body"))  # a screen that bypassed build_page_panel
    ml._stamp(panel)
    assert panel.style == f"on {NEUTRAL_BG}"


def test_styled_panel_background_is_left_untouched():
    ml = make_live(Text(""))
    panel = Panel(Text("body"), style="on rgb(9,23,19)")  # build_page_panel output
    ml._stamp(panel)
    assert panel.style == "on rgb(9,23,19)"


def test_install_hint_when_unavailable(monkeypatch):
    monkeypatch.setattr(music, "is_music_available", lambda: (False, "no ffplay"))
    text = build_music_subtitle().plain
    assert "brew install" in text and "ffmpeg" in text


def test_stamps_install_hint_when_unavailable(monkeypatch):
    # The bar stays present (dim install hint) even without ffplay, so the
    # feature remains discoverable.
    monkeypatch.setattr(music, "is_music_available", lambda: (False, "no ffplay"))
    ml = make_live(Text(""))
    panel = Panel(Text("body"))
    ml._stamp(panel)
    assert panel.subtitle is not None
    assert "brew install" in panel.subtitle.plain and "ffmpeg" in panel.subtitle.plain
    assert getattr(panel, "_music_stamped", False) is True


def test_update_registers_active_and_stamps():
    ml = make_live(Text(""))
    panel = Panel(Text("body"))
    ml.update(panel)
    assert _music_bar._active is ml
    assert panel.subtitle is not None


def test_nudge_is_safe_when_no_active_bar():
    _music_bar._active = None
    nudge_music_bar()  # must not raise
