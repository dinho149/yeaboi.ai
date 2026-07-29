"""Tests for the persistent music status bar (ui/shared/_music_bar.py)."""

from io import StringIO

import pytest
from rich.console import Console
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
    _music_bar._back_presence = 0.0  # back-tab animation state is module-global
    _music_bar._back_region = None
    monkeypatch.setattr(music, "is_music_available", lambda: (True, ""))
    yield
    _music_bar._active = None


# ── Subtitle content ──────────────────────────────────────────────────────────


def test_subtitle_when_stopped():
    music._state.status = "stopped"
    text = build_music_subtitle().plain
    assert "off" in text
    assert "ctrl+P play" in text
    assert "ctrl+O channel" in text


def test_subtitle_when_playing():
    music._state.status = "playing"
    music._state.channel_idx = 0
    text = build_music_subtitle().plain
    assert music.CHANNELS[0]["name"] in text
    assert "playing" in text
    assert "ctrl+P pause" in text


def test_subtitle_when_paused():
    music._state.status = "paused"
    text = build_music_subtitle().plain
    assert "paused" in text
    assert "ctrl+P play" in text


def test_subtitle_shows_crash_notice_when_stopped_with_error():
    # A player that died on its own reverts to "stopped" but leaves a last_error;
    # the bar shows it instead of a bare "off" so a broken player is diagnosable.
    music._state.status = "stopped"
    music._state.last_error = "music stopped — stream unavailable, ctrl+P to retry"
    text = build_music_subtitle().plain
    assert "stream unavailable" in text
    assert "off" not in text
    assert "ctrl+P play" in text


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
    assert "ctrl+P pause" in text


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


# ── Back tab (bottom-left "go back" pocket) ────────────────────────────────


def test_back_tab_renders_and_publishes_region():
    # with_back=True draws the left pocket ("‹ back  esc") and publishes a
    # clickable rect so read_key can map a click there onto Esc.
    from yeaboi.ui.shared._music_bar import _MusicPocketFrame, back_region

    # Pin the tab fully in (presence 1) so it renders at rest.
    _music_bar._back_presence = 1.0
    panel = Panel(Text("body"), height=12, padding=(1, 2))
    console = Console(width=90, height=12, file=StringIO())
    frame = _MusicPocketFrame(panel, with_back=True)
    lines = console.render_lines(frame, console.options, pad=True)
    text = "\n".join("".join(seg.text for seg in row) for row in lines)
    assert "back" in text and "esc" in text
    r = back_region()
    assert r is not None and len(r) == 4
    x0, y0, x1, y1 = r
    assert 1 <= x0 < x1 and y1 == len(lines)  # spans the bottom rows, left side


def test_back_tab_absent_without_flag():
    # Default frame (with_back=False) draws no back tab.
    from yeaboi.ui.shared._music_bar import _MusicPocketFrame

    panel = Panel(Text("body"), height=12, padding=(1, 2))
    console = Console(width=90, height=12, file=StringIO())
    lines = console.render_lines(_MusicPocketFrame(panel), console.options, pad=True)
    text = "\n".join("".join(seg.text for seg in row) for row in lines)
    assert "‹ back" not in text


def _wide_ml(renderable):
    from rich.console import Console

    # Height ≥ the min-size floor so the app-wide too-small guard doesn't fire.
    return make_live(renderable, console=Console(width=120, height=45, file=StringIO()))


def test_pocket_frame_boxes_the_music():
    # The app-wide pocket frame raises the music into a rounded alcove one line
    # above the bottom border, which curves up-and-under it (╯ … ╰ corners).
    from yeaboi.ui.shared._music_bar import _MusicPocketFrame

    panel = Panel(Text("body"), height=8, padding=(1, 2))
    console = Console(width=80, height=8, file=StringIO())
    console.print(_MusicPocketFrame(panel))
    out = console.file.getvalue()
    assert "╭" in out and "╯" in out and "channel" in out


def test_pocket_frame_has_no_trailing_newline():
    # A trailing newline after the last row of a full-height frame scrolls the
    # whole frame up by one (the "bottom border moves up on entry" glitch). The
    # frame must emit exactly `height` rows: `height-1` separators, none trailing.
    from rich.segment import Segment

    from yeaboi.ui.shared._music_bar import _MusicPocketFrame

    panel = Panel(Text("body"), height=8, padding=(1, 2))
    console = Console(width=80, height=8, file=StringIO())
    segments = list(console.render(_MusicPocketFrame(panel), console.options))
    newlines = sum(1 for s in segments if s.text == "\n" or s is Segment.line())
    assert newlines == 7  # 8 rows → 7 separators, no trailing newline


def test_pocket_pads_short_panel_to_bottom_row():
    # Many pages build their panel one row short (legacy `h - 1` margin). The
    # pocket must pad it up so the border+music land on the TRUE bottom row, not
    # one above it (the "border moved up with a gap beneath" glitch).
    from yeaboi.ui.shared._music_bar import _MusicPocketFrame

    term_h = 20
    panel = Panel(Text("body"), height=term_h - 1, padding=(1, 2))  # built one short
    console = Console(width=80, height=term_h, file=StringIO())
    lines = console.render_lines(_MusicPocketFrame(panel), console.options, pad=True)
    assert len(lines) == term_h  # padded up to fill the terminal
    last = "".join(seg.text for seg in lines[-1])
    assert last.startswith("╰") and last.endswith("╯")  # bottom border on the last row
    assert "╭" in "".join(seg.text for seg in lines[-3])  # roof sits above the text
    textrow = "".join(seg.text for seg in lines[-2])
    assert "channel" in textrow  # music one line below the roof, above the border


def test_companion_duck_slides_from_centre_to_the_corner(monkeypatch):
    # On screen entry the mascot starts relatively central and glides RIGHT into
    # its corner: its leftmost glyph column moves rightward as the slide settles.
    from yeaboi.ui.shared._music_bar import _MusicPocketFrame

    panel = Panel(Text("body"), height=20, padding=(1, 2))

    def duck_left_col(clock: float, slide_start: float, last_draw: float) -> int:
        _music_bar._duck_slide_start = slide_start
        _music_bar._duck_last_draw = last_draw
        monkeypatch.setattr(_music_bar.time, "monotonic", lambda: clock)
        console = Console(width=80, height=20, file=StringIO())
        lines = console.render_lines(_MusicPocketFrame(panel), console.options, pad=True)
        cols = ["".join(seg.text for seg in ln).find("█") for ln in lines if "█" in "".join(seg.text for seg in ln)]
        return min(cols) if cols else -1

    # Early in the slide (progress ≈ 0, no gap reset) vs settled (progress ≥ 1).
    early = duck_left_col(10.01, slide_start=10.0, last_draw=10.0)
    settled = duck_left_col(10.6, slide_start=10.0, last_draw=10.5)
    assert 0 < early < settled  # both visible; the duck moved right into the corner


def test_get_renderable_pockets_a_bare_panel():
    panel = Panel(Text("body"))
    ml = _wide_ml(panel)
    from yeaboi.ui.shared._music_bar import _MusicPocketFrame

    assert isinstance(ml.get_renderable(), _MusicPocketFrame)


def test_min_size_guard_replaces_small_screens():
    # Below the welcome floor, EVERY screen (not just the menu) shows the
    # too-small guard via the shared MusicLive chokepoint.
    from yeaboi.ui.mode_select.screens._screens import _MIN_HEIGHT, _MIN_WIDTH

    def _out(w, h):
        con = Console(width=w, height=h, file=StringIO())
        ml = make_live(Panel(Text("body"), height=h), console=con)
        con.print(ml.get_renderable())
        return con.file.getvalue()

    assert "a bit cramped" not in _out(_MIN_WIDTH, _MIN_HEIGHT)  # exactly at the floor → fine
    assert "a bit cramped" in _out(_MIN_WIDTH, _MIN_HEIGHT - 1)  # one row short → guard
    assert "a bit cramped" in _out(_MIN_WIDTH - 1, _MIN_HEIGHT)  # one col short → guard


def test_no_companion_duck_panel_pockets_without_duck():
    # A screen that already shows the mascot (e.g. the too-small guard) marks
    # itself so the app-wide chrome adds the pocket but not a second corner duck.
    panel = Panel(Text("body"))
    panel._no_companion_duck = True
    ml = _wide_ml(panel)
    from yeaboi.ui.shared._music_bar import _MusicPocketFrame

    result = ml.get_renderable()
    assert isinstance(result, _MusicPocketFrame)
    assert result.with_duck is False


def test_too_small_screen_marks_itself_no_companion_duck():
    from yeaboi.ui.mode_select.screens._screens import _build_too_small_screen

    panel = _build_too_small_screen(60, 20)
    assert getattr(panel, "_no_companion_duck", False) is True


def test_get_renderable_leaves_popup_subtitle_untouched():
    panel = Panel(Text("body"), subtitle="Board required")
    ml = _wide_ml(panel)
    result = ml.get_renderable()
    assert result is panel  # a popup's own subtitle survives, no pocket
    assert panel.subtitle == "Board required"


def test_get_renderable_passes_non_panels_through():
    text = Text("plain")
    ml = _wide_ml(text)
    assert ml.get_renderable() is text  # e.g. the welcome screen's own frame


def test_unstyled_panel_gains_neutral_background():
    from yeaboi.ui.shared._components import NEUTRAL_BG

    panel = Panel(Text("body"))  # a screen that bypassed build_page_panel
    ml = _wide_ml(panel)
    ml.get_renderable()  # the neutral-base safety net is applied in place here
    assert panel.style == f"on {NEUTRAL_BG}"


def test_styled_panel_background_is_left_untouched():
    panel = Panel(Text("body"), style="on rgb(9,23,19)")  # build_page_panel output
    ml = _wide_ml(panel)
    ml.get_renderable()
    assert panel.style == "on rgb(9,23,19)"


def test_install_hint_when_unavailable(monkeypatch):
    monkeypatch.setattr(music, "is_music_available", lambda: (False, "no ffplay"))
    text = build_music_subtitle().plain
    assert "brew install" in text and "ffmpeg" in text


def test_pocket_shows_install_hint_when_unavailable(monkeypatch):
    # The bar stays present (dim install hint) even without ffplay, so the
    # feature remains discoverable.
    monkeypatch.setattr(music, "is_music_available", lambda: (False, "no ffplay"))
    from yeaboi.ui.shared._music_bar import _MusicPocketFrame

    panel = Panel(Text("body"), height=8, padding=(1, 2))
    console = Console(width=80, height=8, file=StringIO())
    console.print(_MusicPocketFrame(panel))
    out = console.file.getvalue()
    assert "brew install" in out and "ffmpeg" in out


def test_update_registers_active_bar():
    ml = make_live(Text(""))
    panel = Panel(Text("body"))
    ml.update(panel)
    assert _music_bar._active is ml  # so music.py can nudge it after a state change


def test_nudge_is_safe_when_no_active_bar():
    _music_bar._active = None
    nudge_music_bar()  # must not raise
