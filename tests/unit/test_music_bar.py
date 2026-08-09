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
    _music_bar._back_retracting = False
    _music_bar.collapse_tabs()  # the action strip's open/peek state is module-global too
    _music_bar._reset_duck_state()  # bubble + quack/working/entrance clocks are module-global too
    from yeaboi.ui.shared import _duck_voice

    _duck_voice._reset()  # the shared voice + its mute flag are module-global too
    monkeypatch.setattr(music, "is_music_available", lambda: (True, ""))
    yield
    _music_bar._active = None
    _duck_voice._reset()


# ── Subtitle content ──────────────────────────────────────────────────────────


def test_subtitle_when_stopped():
    music._state.status = "stopped"
    text = build_music_subtitle().plain
    assert "off" in text
    assert "P play" in text
    assert "O channel" in text


def test_subtitle_when_playing():
    music._state.status = "playing"
    music._state.channel_idx = 0
    text = build_music_subtitle().plain
    assert music.CHANNELS[0]["name"] in text
    assert "playing" in text
    assert "P pause" in text


def test_subtitle_when_paused():
    music._state.status = "paused"
    text = build_music_subtitle().plain
    assert "paused" in text
    assert "P play" in text


def test_subtitle_shows_crash_notice_when_stopped_with_error():
    # A player that died on its own reverts to "stopped" but leaves a last_error;
    # the bar shows it instead of a bare "off" so a broken player is diagnosable.
    music._state.status = "stopped"
    music._state.last_error = "music stopped — stream unavailable, P to retry"
    text = build_music_subtitle().plain
    assert "stream unavailable" in text
    assert "off" not in text
    assert "P play" in text


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
    assert "P pause" in text


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


def test_duck_say_fades_in_holds_then_clears():
    # The duck's status bubble dissolves in, holds, then dissolves out and stops
    # drawing entirely — the same fade shape the rotating menu tips use, so a
    # message clears itself after a couple of seconds.
    from yeaboi.ui.shared._music_bar import _SAY_FADE_IN, _SAY_FADE_OUT, _SAY_HOLD, _say_brightness

    _music_bar._say_start = 0.0
    assert _say_brightness(0.0) == 0.0
    assert _say_brightness(_SAY_FADE_IN / 2) == pytest.approx(0.5)
    assert _say_brightness(_SAY_FADE_IN) == 1.0
    assert _say_brightness(_SAY_FADE_IN + _SAY_HOLD / 2) == 1.0  # holds at full
    mid_out = _SAY_FADE_IN + _SAY_HOLD + _SAY_FADE_OUT / 2
    assert 0.0 < _say_brightness(mid_out) < 1.0  # fading back out
    assert _say_brightness(_SAY_FADE_IN + _SAY_HOLD + _SAY_FADE_OUT) == 0.0  # cleared
    assert _say_brightness(99.0) == 0.0


def test_duck_say_bubble_drawn_beside_the_duck():
    # A fresh message renders a bubble next to the corner duck (and restarts the
    # fade), rather than the page spending a body row on the status.
    from yeaboi.ui.shared._music_bar import _MusicPocketFrame

    _music_bar._say_text = ""  # force a fresh message → fade restarts at full-in
    panel = Panel(Text("body"), height=20, padding=(1, 2))
    console = Console(width=120, height=20, file=StringIO())
    frame = _MusicPocketFrame(panel, duck_say="Anthropic Key updated")
    lines = console.render_lines(frame, console.options, pad=True)
    text = "\n".join("".join(seg.text for seg in row) for row in lines)
    assert "Anthropic Key updated" in text


def _duck_rows(clock, monkeypatch, *, settled=True):
    """Plain-text rows of a rendered frame at a frozen clock, slide settled."""
    if settled:
        _music_bar._duck_slide_start = clock - 10.0
        _music_bar._duck_last_draw = clock - 0.01
    monkeypatch.setattr(_music_bar.time, "monotonic", lambda: clock)
    from yeaboi.ui.shared._music_bar import _MusicPocketFrame

    panel = Panel(Text("body"), height=24, padding=(1, 2))
    console = Console(width=120, height=24, file=StringIO())
    lines = console.render_lines(_MusicPocketFrame(panel), console.options, pad=True)
    return ["".join(seg.text for seg in row) for row in lines]


def test_quack_duck_opens_the_beak_during_its_window(monkeypatch):
    # quack_duck() toggles the bill at _DUCK_QUACK_HZ for its window: at a phase
    # where the bill is open the head renders differently from the closed pose.
    closed = _duck_rows(100.0, monkeypatch)
    _music_bar._reset_duck_state()
    _music_bar._duck_quack_start = 100.0 - (1.5 / _music_bar._DUCK_QUACK_HZ)  # int(e*hz)=1 → open
    open_ = _duck_rows(100.0, monkeypatch)
    assert closed != open_


def test_quack_duck_coalesces_while_one_is_playing(monkeypatch):
    monkeypatch.setattr(_music_bar.time, "monotonic", lambda: 50.0)
    _music_bar.quack_duck()
    monkeypatch.setattr(_music_bar.time, "monotonic", lambda: 50.2)  # mid-quack
    _music_bar.quack_duck()
    assert _music_bar._duck_quack_start == 50.0  # second call did not restart it
    monkeypatch.setattr(_music_bar.time, "monotonic", lambda: 51.0)  # finished
    _music_bar.quack_duck()
    assert _music_bar._duck_quack_start == 51.0


def test_working_duck_bobs_over_time(monkeypatch):
    # set_duck_working(True) drives the head-bob: the sprite changes across
    # frames instead of holding the constant frame-0 pose.
    _music_bar.set_duck_working(True)
    _music_bar._duck_working_start = 100.0
    a = _duck_rows(100.0, monkeypatch)  # frame 0
    _music_bar.set_duck_working(True)
    _music_bar._duck_working_start = 100.0
    b = _duck_rows(100.375, monkeypatch)  # frame 3 (HEAD_BOB shifts the head down)
    assert a != b
    _music_bar.set_duck_working(False)
    assert _music_bar._duck_frame() == 0  # idle → still pose


def test_say_hold_extends_the_bubble_dwell():
    from yeaboi.ui.shared._music_bar import _SAY_FADE_IN, _SAY_HOLD, _say_brightness

    _music_bar._say_start = 0.0
    after_default_hold = _SAY_FADE_IN + _SAY_HOLD + 1.0
    assert _say_brightness(after_default_hold) < 1.0  # default dwell has ended
    assert _say_brightness(after_default_hold, hold=_SAY_HOLD + 2.0) == 1.0  # still holding


def test_say_seq_bump_restarts_identical_text(monkeypatch):
    # The same text twice is normally swallowed (say != _say_text guard); a
    # bumped seq restarts the fade so repeated statuses still show.
    from yeaboi.ui.shared._music_bar import _MusicPocketFrame

    panel = Panel(Text("body"), height=20, padding=(1, 2))

    def render_at(clock, seq):
        monkeypatch.setattr(_music_bar.time, "monotonic", lambda: clock)
        frame = _MusicPocketFrame(panel, duck_say="Export finished.")
        frame.duck_say_seq = seq
        console = Console(width=120, height=20, file=StringIO())
        console.render_lines(frame, console.options, pad=True)

    render_at(10.0, seq=1)
    assert _music_bar._say_start == 10.0
    render_at(20.0, seq=1)  # same text, same seq → fade NOT restarted
    assert _music_bar._say_start == 10.0
    render_at(30.0, seq=2)  # same text, new seq → restarted
    assert _music_bar._say_start == 30.0


def test_duck_working_cm_sets_and_clears():
    from yeaboi.ui.shared._music_bar import duck_working

    with duck_working():
        assert _music_bar._duck_working is True
    assert _music_bar._duck_working is False


def test_duck_working_cm_nesting_keeps_the_bob_until_the_outer_exit():
    from yeaboi.ui.shared._music_bar import duck_working

    with duck_working():
        with duck_working():
            assert _music_bar._duck_working is True
        assert _music_bar._duck_working is True  # inner exit must not stop the outer wait
    assert _music_bar._duck_working is False


def test_duck_working_cm_clears_on_exception():
    from yeaboi.ui.shared._music_bar import duck_working

    with pytest.raises(RuntimeError):
        with duck_working():
            raise RuntimeError("worker blew up")
    assert _music_bar._duck_working is False


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_duck_working_thread_bobs_for_the_workers_lifetime():
    # The drop-in Thread factory the mode pages use: the duck bobs while the
    # target runs and settles when it finishes, even if the target raises.
    import threading

    from yeaboi.ui.shared._music_bar import duck_working_thread

    started, release = threading.Event(), threading.Event()

    def _work():
        started.set()
        release.wait(timeout=5)

    t = duck_working_thread(_work, name="test-worker")
    t.start()
    assert started.wait(timeout=5)
    assert _music_bar._duck_working is True
    release.set()
    t.join(timeout=5)
    assert _music_bar._duck_working is False

    def _boom():
        raise RuntimeError("dead worker")

    t2 = duck_working_thread(_boom, name="test-worker-boom")
    t2.start()
    t2.join(timeout=5)
    assert _music_bar._duck_working is False


def test_entrance_plays_once_per_process(monkeypatch):
    monkeypatch.setattr(_music_bar.time, "monotonic", lambda: 5.0)
    _music_bar.start_duck_entrance()
    assert _music_bar._duck_entrance_start == 5.0
    monkeypatch.setattr(_music_bar.time, "monotonic", lambda: 99.0)
    _music_bar.start_duck_entrance()  # second call is a no-op
    assert _music_bar._duck_entrance_start == 5.0
    _music_bar.skip_duck_entrance()
    assert _music_bar._duck_entrance_start == 0.0


def test_entrance_replay_plays_again_per_card_entry(monkeypatch):
    # Default stays once-per-process (the chat greeting), but a mode card entry
    # passes replay=True so the duck walks in with every page.
    monkeypatch.setattr(_music_bar.time, "monotonic", lambda: 5.0)
    _music_bar.start_duck_entrance()
    _music_bar.skip_duck_entrance()  # settled
    monkeypatch.setattr(_music_bar.time, "monotonic", lambda: 60.0)
    _music_bar.start_duck_entrance()  # once-per-process default: no-op
    assert _music_bar._duck_entrance_start == 0.0
    _music_bar.start_duck_entrance(replay=True)  # a mode card entry replays
    assert _music_bar._duck_entrance_start == 60.0


def test_entrance_replay_never_restarts_mid_waddle(monkeypatch):
    monkeypatch.setattr(_music_bar.time, "monotonic", lambda: 5.0)
    _music_bar.start_duck_entrance()
    monkeypatch.setattr(_music_bar.time, "monotonic", lambda: 5.4)
    _music_bar.start_duck_entrance(replay=True)  # mid-stride: keep walking
    assert _music_bar._duck_entrance_start == 5.0


def test_entrance_walks_the_mini_duck_toward_the_corner(monkeypatch):
    # During the entrance the (wider) mini duck is composited walking rightward;
    # its leftmost ink column advances with progress, and the sprite is taller
    # than the settled 7-row head.
    def ink_cols_and_rows(clock):
        monkeypatch.setattr(_music_bar.time, "monotonic", lambda: clock)
        from yeaboi.ui.shared._music_bar import _MusicPocketFrame

        panel = Panel(Text("body"), height=30, padding=(1, 2))
        console = Console(width=120, height=30, file=StringIO())
        lines = console.render_lines(_MusicPocketFrame(panel), console.options, pad=True)
        rows = ["".join(seg.text for seg in row) for row in lines]
        cols = [r.find("█") for r in rows if "█" in r]
        return (min(cols) if cols else -1), sum(1 for r in rows if "█" in r)

    _music_bar._duck_entrance_start = 100.0
    early_col, early_rows = ink_cols_and_rows(100.15)  # 10% in
    _music_bar._duck_entrance_start = 100.0
    late_col, late_rows = ink_cols_and_rows(101.2)  # 80% in
    assert 0 < early_col < late_col  # he moved right
    _music_bar._reset_duck_state()
    settled_col, settled_rows = ink_cols_and_rows(200.0)
    assert early_rows > settled_rows  # full-body mini vs the settled head


def test_entrance_completion_hands_back_and_quacks(monkeypatch):
    monkeypatch.setattr(_music_bar.time, "monotonic", lambda: 100.0)
    _music_bar._duck_entrance_start = 100.0 - _music_bar._DUCK_ENTRANCE_SECONDS - 0.1
    assert _music_bar._duck_entrance_progress() is None  # finished → cleared
    assert _music_bar._duck_entrance_start == 0.0
    assert _music_bar._duck_quack_start == 100.0  # the arrival hello


def test_skip_entrance_jumps_to_settled(monkeypatch):
    monkeypatch.setattr(_music_bar.time, "monotonic", lambda: 100.0)
    _music_bar.start_duck_entrance()
    _music_bar.skip_duck_entrance()
    assert _music_bar._duck_entrance_progress() is None
    assert _music_bar._duck_quack_start == 0.0  # a skipped arrival doesn't quack


def test_reset_duck_state_restores_idle():
    _music_bar.quack_duck()
    _music_bar.set_duck_working(True)
    _music_bar.start_duck_entrance()
    _music_bar._say_text, _music_bar._say_seq = "hi", 3
    _music_bar._reset_duck_state()
    assert not _music_bar._duck_working and _music_bar._duck_quack_start == 0.0
    assert _music_bar._say_text == "" and _music_bar._say_seq == 0
    assert not _music_bar._duck_entrance_played and _music_bar._duck_entrance_start == 0.0


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


def test_shared_voice_line_is_stamped_by_the_chrome(monkeypatch):
    # A page that opted in (declared bubble room) gets the app-wide voice's
    # line with zero render wiring: the chrome ticks the singleton and stamps
    # the frame itself.
    from yeaboi.ui.shared import _duck_voice as dv

    monkeypatch.delenv("DUCK_ENABLED", raising=False)
    dv.duck_voice().say("Exported!")
    panel = Panel(Text("body"))
    panel._bubble_room = 40
    frame = _wide_ml(panel).get_renderable()
    assert frame.duck_say == "Exported!"
    assert frame.duck_say_seq > 0


def test_page_without_declared_room_stays_silent(monkeypatch):
    # Ordinary lines are opt-in: a page that never declared _bubble_room gets
    # no bubble (its content may reach the right edge) — the quack still lands.
    from yeaboi.ui.shared import _duck_voice as dv

    monkeypatch.delenv("DUCK_ENABLED", raising=False)
    dv.duck_voice().say("Exported!")
    frame = _wide_ml(Panel(Text("body"))).get_renderable()
    assert frame.duck_say == ""


def test_panel_stamped_line_wins_over_the_shared_voice(monkeypatch):
    # The chat (and any page with its own fence) stamps panel attrs directly —
    # that always takes precedence over the singleton.
    from yeaboi.ui.shared import _duck_voice as dv

    monkeypatch.delenv("DUCK_ENABLED", raising=False)
    dv.duck_voice().say("shared line")
    panel = Panel(Text("body"))
    panel._duck_say = "my own line"
    panel._bubble_room = 40
    frame = _wide_ml(panel).get_renderable()
    assert frame.duck_say == "my own line"


def test_bubble_room_zero_suppresses_the_shared_line(monkeypatch):
    # Pages whose content reaches the right edge (retro board, analysis
    # results) declare no room — the bubble is skipped, never overlapped.
    from yeaboi.ui.shared import _duck_voice as dv

    monkeypatch.delenv("DUCK_ENABLED", raising=False)
    dv.duck_voice().say("Exported!")
    panel = Panel(Text("body"))
    panel._bubble_room = 0
    frame = _wide_ml(panel).get_renderable()
    assert frame.duck_say == ""


def test_sticky_bypasses_the_fence_and_is_never_truncated(monkeypatch):
    # A sticky line is a modal prompt (Enter deletes!) — it renders whole even
    # on a page that declared no room or a tiny one; losing "Enter to confirm"
    # would make the next Enter destructive with no visible prompt.
    from yeaboi.ui.shared import _duck_voice as dv

    monkeypatch.delenv("DUCK_ENABLED", raising=False)
    prompt = 'Delete "Standup — 2026-…"?  Enter to confirm'
    dv.duck_voice().say_sticky(prompt)
    panel = Panel(Text("body"))
    panel._bubble_room = 0  # even a bubble-suppressed page shows the prompt
    frame = _wide_ml(panel).get_renderable()
    assert frame.duck_say == prompt  # whole, no ellipsis
    assert frame.duck_say_sticky is True


def test_sticky_shows_even_when_globally_muted(monkeypatch):
    from yeaboi.ui.shared import _duck_voice as dv

    monkeypatch.delenv("DUCK_ENABLED", raising=False)
    dv.set_duck_muted(True)
    dv.duck_voice().say_sticky("Sure?")
    frame = _wide_ml(Panel(Text("body"))).get_renderable()
    assert frame.duck_say == "Sure?"


def test_shared_line_truncates_to_the_declared_room(monkeypatch):
    from yeaboi.ui.shared import _duck_voice as dv

    monkeypatch.delenv("DUCK_ENABLED", raising=False)
    dv.duck_voice().say("A rather long completion line")
    panel = Panel(Text("body"))
    panel._bubble_room = 13
    frame = _wide_ml(panel).get_renderable()
    assert frame.duck_say.endswith("…")
    assert len(frame.duck_say) <= 13


def test_shared_line_skipped_below_minimum_room(monkeypatch):
    from yeaboi.ui.shared import _duck_voice as dv

    monkeypatch.delenv("DUCK_ENABLED", raising=False)
    dv.duck_voice().say("Exported!")
    panel = Panel(Text("body"))
    panel._bubble_room = dv._BUBBLE_MIN_COLS - 1
    frame = _wide_ml(panel).get_renderable()
    assert frame.duck_say == ""


def test_global_mute_suppresses_the_shared_line(monkeypatch):
    from yeaboi.ui.shared import _duck_voice as dv

    monkeypatch.delenv("DUCK_ENABLED", raising=False)
    dv.duck_voice().say("Exported!")
    dv.set_duck_muted(True)
    panel = Panel(Text("body"))
    panel._bubble_room = 40
    frame = _wide_ml(panel).get_renderable()
    assert frame.duck_say == ""


def test_sticky_shared_line_rides_the_no_fade_path(monkeypatch):
    # A sticky confirmation is stamped with the sticky flag and NO hold — its
    # infinite hold must never reach the fade envelope.
    from yeaboi.ui.shared import _duck_voice as dv

    monkeypatch.delenv("DUCK_ENABLED", raising=False)
    dv.duck_voice().say_sticky('Delete "sprint 3"?')
    frame = _wide_ml(Panel(Text("body"))).get_renderable()
    assert frame.duck_say.startswith("Delete")
    assert frame.duck_say_sticky is True
    assert frame.duck_say_hold is None


def test_no_companion_duck_page_never_ticks_the_voice(monkeypatch):
    # A page that opted out of the duck gets no bubble either.
    from yeaboi.ui.shared import _duck_voice as dv

    monkeypatch.delenv("DUCK_ENABLED", raising=False)
    dv.duck_voice().say("Exported!")
    panel = Panel(Text("body"))
    panel._no_companion_duck = True
    frame = _wide_ml(panel).get_renderable()
    assert frame.duck_say == ""


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


# ── Controls drawer ───────────────────────────────────────────────────────────


def test_controls_tab_only_binds_its_letter_while_it_is_showing():
    # The drawer's shortcut is a bare 'c', so it must only claim the key on pages
    # that actually show the tab — otherwise it would shadow the per-page 'c'
    # bindings (copy on Usage, changelog on the welcome screen).
    from yeaboi.ui.shared._music_bar import close_controls, controls_tab_visible

    _music_bar._controls_tab_presence = 0.0
    _music_bar._controls_open = False
    assert controls_tab_visible() is False

    _music_bar._controls_tab_presence = 1.0  # tab eased in on a qualifying page
    assert controls_tab_visible() is True

    # Still true while open even if the collapsed tab isn't being drawn, so 'c'
    # can close what it opened.
    _music_bar._controls_tab_presence = 0.0
    _music_bar._controls_open = True
    assert controls_tab_visible() is True
    close_controls()


def test_controls_tab_advertises_the_letter_and_the_drawer_lists_it():
    from yeaboi.ui.shared._music_bar import _MusicPocketFrame, close_controls, toggle_controls

    _music_bar._controls_tab_presence = 1.0
    _music_bar._controls_open = False
    _music_bar._controls_presence = 0.0
    hint = Text("Enter  edit")
    console = Console(width=100, height=14, file=StringIO())

    def _text(frame):
        rows = console.render_lines(frame, console.options, pad=True)
        return "\n".join("".join(s.text for s in row) for row in rows)

    collapsed = _text(_MusicPocketFrame(Panel(Text(""), height=14), hint_tab=hint))
    assert "c  controls" in collapsed

    toggle_controls()
    _music_bar._controls_presence = 1.0  # skip the eased expansion
    opened = _text(_MusicPocketFrame(Panel(Text(""), height=14), hint_tab=hint))
    assert "close this" in opened  # 'c' closes it
    assert "quit" in opened  # ctrl+C still quits outright
    close_controls()


# TestTabSlots lived here: it covered the LEFT strip laying its tabs out in fixed
# slots, so one animating tab could not shove the settled ones along. Two or more
# actions on the left are a drawer now — they grow upward as a list instead of
# peeling sideways as a row — so there are no slots left on that side to defend.
# TestCollapsedActionStrip covers what replaced it; the right strip still uses
# _resolve_tabs and is covered by TestTabEntrance.
class TestTabEntrance:
    """Every tab peels in on its own account, not just with the strip.

    `_back_presence` describes the STRIP. A page adding a "next" tab mid-flow
    leaves it at 1.0, so without a per-tab presence the new tab snapped into
    existence beside tabs that had glided in.
    """

    @staticmethod
    def _blank(console, options, width, height):
        from rich.text import Text

        row = console.render_lines(Text(" " * width), options.update_width(width), pad=True)[0]
        return [list(row) for _ in range(height)]

    def _strip(self, right=(), left=(), width=120, height=12):
        from rich.console import Console

        from yeaboi.ui.shared import _music_bar as mb

        console = Console(width=width, height=height, force_terminal=False)
        options = console.options.update_dimensions(width, height)
        lines = self._blank(console, options, width, height)
        mb.draw_back_pocket(console, options, lines, target=1.0, extra_tabs=list(left), right_tabs=list(right))
        return "".join(seg.text for seg in lines[-2])

    def _settle(self, **kwargs):
        for _ in range(24):
            row = self._strip(**kwargs)
        return row

    @pytest.fixture(autouse=True)
    def _fresh(self):
        from yeaboi.ui.shared import _music_bar as mb

        mb._forget_tabs()
        mb._back_presence = 0.0
        yield
        mb._forget_tabs()
        mb._back_presence = 0.0

    def _next_tab(self):
        from yeaboi.ui.shared._music_bar import build_next_text

        return [(build_next_text(label="next"), "enter")]

    def test_a_tab_added_to_a_settled_strip_peels_in(self):
        settled = self._settle()
        assert "next" not in settled
        first = self._strip(right=self._next_tab())
        # Partway in on its first frame, whole a few frames later.
        assert "next ⏎" not in first
        for _ in range(6):
            grown = self._strip(right=self._next_tab())
        assert "next ⏎" in grown

    def test_it_unfolds_leftwards_out_of_the_music_pocket(self):
        # The right strip reveals its RIGHTMOST columns first, so the label
        # arrives as a growing suffix: "", "t ⏎", "next ⏎". Growing the other
        # way would peel the tab away from the pocket it belongs to.
        self._settle()
        seen = []
        for _ in range(5):
            row = self._strip(right=self._next_tab())
            label = "next ⏎"
            seen.append(max((n for n in range(len(label) + 1) if label[len(label) - n :] in row), default=0))
        assert seen == sorted(seen)
        assert seen[-1] > seen[0]

    def test_a_dropped_tab_folds_away_rather_than_vanishing(self):
        from yeaboi.ui.shared._music_bar import build_next_text

        other = [(build_next_text(label="Run"), "enter")]
        self._settle(right=self._next_tab())
        assert "next ⏎" in self._strip(right=self._next_tab())
        # Replaced, not removed: a page offering NOTHING is a different page, and
        # freezing the registry there is what stops a progress screen wiping
        # every tab and replaying its entrance on the way back.
        first = self._strip(right=other)
        assert "next" in first  # still on screen, folding
        for _ in range(12):
            gone = self._strip(right=other)
        assert "next ⏎" not in gone
        assert "Run" in gone

    def test_a_folding_tab_is_not_clickable(self):
        from yeaboi.ui.shared._music_bar import chrome_tab_regions

        self._settle(right=self._next_tab())
        assert any(key == "enter" for *_rest, key in chrome_tab_regions())
        self._strip()
        assert not any(key == "enter" for *_rest, key in chrome_tab_regions())

    def test_the_strip_retracting_takes_a_brand_new_tab_with_it(self):
        from yeaboi.ui.shared import _music_bar as mb

        self._settle()
        mb._back_presence = 0.05  # mid-retract
        row = self._strip(right=self._next_tab())
        assert "next" not in row  # its own presence cannot outrun the strip's


class TestNoTabsIsADifferentPage:
    """A page offering no tabs is not asking for the current ones to be dropped.

    Anonymize runs behind a worker; the screen that covers it has no tabs of its
    own. Decaying the registry there wiped every tab, so coming back replayed
    every entrance — which is what "all the buttons animate again" was.
    """

    @pytest.fixture(autouse=True)
    def _fresh(self):
        from yeaboi.ui.shared import _music_bar as mb

        mb._forget_tabs()
        mb._back_presence = 0.0
        yield
        mb._forget_tabs()
        mb._back_presence = 0.0

    TABS = ["Export", "Share Online", "Anonymize"]

    @staticmethod
    def _strip(names, width=130, height=12):
        from rich.console import Console
        from rich.text import Text

        from yeaboi.ui.shared import _music_bar as mb

        console = Console(width=width, height=height, force_terminal=False)
        options = console.options.update_dimensions(width, height)
        row = console.render_lines(Text(" " * width), options.update_width(width), pad=True)[0]
        lines = [list(row) for _ in range(height)]
        mb.draw_back_pocket(
            console,
            options,
            lines,
            target=1.0,
            right_tabs=[(mb.build_tab_text(n), f"act:{n}") for n in names],
        )
        return "".join(seg.text for seg in lines[-2]).rstrip()

    def test_the_tabs_come_back_whole_after_a_tabless_screen(self):
        for _ in range(30):
            settled = self._strip(self.TABS)
        for _ in range(40):  # a long worker behind a screen with no tabs
            self._strip([])
        assert self._strip(self.TABS) == settled  # first frame back, identical

    def test_a_tabless_screen_shows_no_stale_tabs(self):
        for _ in range(30):
            self._strip(self.TABS)
        assert "Export" not in self._strip([])


class TestCollapsedActionStrip:
    """Three or four action tabs fill the border end to end, so they stack."""

    W, H = 110, 14

    # The first action stays a tab of its own — it is the page's forward move —
    # so only the ones after it stack.
    def _draw(self, names=("Continue", "Edit", "Regenerate", "Export"), frames=40):
        from yeaboi.ui.shared._components import ANALYSIS_THEME

        console = Console(width=self.W, height=self.H, force_terminal=False)
        options = console.options.update_dimensions(self.W, self.H)
        tabs = [(_music_bar.build_tab_text(n, ANALYSIS_THEME), f"act:{n}") for n in names]
        lines = []
        for _ in range(frames):  # settle every entrance animation
            body = Text("\n".join("." * (self.W - 2) for _ in range(self.H)))
            lines = console.render_lines(body, options, pad=True)
            _music_bar.draw_music_pocket(console, options, lines)
            _music_bar.draw_back_pocket(console, options, lines, 1.0, extra_tabs=tabs, lead_tab="Continue")
        return "\n".join("".join(s.text for s in row) for row in lines)

    def setup_method(self):
        _music_bar.collapse_tabs()
        _music_bar._tab_presence.clear()
        _music_bar._tab_memory.clear()

    def test_the_actions_stack_into_one_tab(self):
        drawn = self._draw()
        assert "3 actions" in drawn
        assert "Continue" in drawn  # the forward move is never swallowed
        for name in ("Edit", "Regenerate", "Export"):
            assert name not in drawn
        # No key of its own — a click on it opens the drawer instead.
        assert _music_bar.stack_region() is not None
        # Only the lead tab is clickable while the rest are stacked.
        assert [k for *_r, k in _music_bar.chrome_tab_regions()] == ["act:Continue"]

    def test_opening_it_grows_a_list_upward_out_of_the_tab(self):
        self._draw()
        _music_bar.toggle_tabs()
        drawn = self._draw().split("\n")
        assert "3 actions" not in "\n".join(drawn)
        rows = {name: i for i in range(len(drawn)) for name in ("Edit", "Regenerate", "Export") if name in drawn[i]}
        assert set(rows) == {"Edit", "Regenerate", "Export"}
        # Stacked upward, in order, above the border they came out of.
        assert rows["Edit"] < rows["Regenerate"] < rows["Export"] < len(drawn) - 1
        assert sorted(k for *_r, k in _music_bar.chrome_tab_regions()) == sorted(
            ["act:Continue", "act:Edit", "act:Regenerate", "act:Export"]
        )
        assert _music_bar.stack_region() is None

    def test_every_row_of_the_open_list_is_its_own_click_target(self):
        self._draw()
        _music_bar.toggle_tabs()
        self._draw()
        rects = [r for r in _music_bar.chrome_tab_regions() if r[4] != "act:Continue"]
        assert len(rects) == 3
        # One row each, and no two on the same row.
        assert all(y0 == y1 for _x0, y0, _x1, y1, _k in rects)
        assert len({y0 for _x0, y0, _x1, _y1, _k in rects}) == 3

    def test_one_action_is_not_worth_stacking(self):
        drawn = self._draw(names=("Export",))
        assert "Export" in drawn
        assert "actions" not in drawn
        assert _music_bar.stack_region() is None
        assert not _music_bar.actions_available()

    def test_a_page_with_a_drawer_says_so_for_the_tab_key(self):
        self._draw()
        assert _music_bar.actions_available()


class TestActionsDrawerSelection:
    """Open, it is a menu: one live row, arrows to move, Enter to take it."""

    def _rows(self, names=("Continue", "Edit", "Regenerate", "Export")):
        from yeaboi.ui.shared._components import ANALYSIS_THEME

        console = Console(width=110, height=14, force_terminal=False)
        options = console.options.update_dimensions(110, 14)
        tabs = [(_music_bar.build_tab_text(n, ANALYSIS_THEME), f"act:{n}") for n in names]
        lines = []
        for _ in range(40):
            body = Text("\n".join(" " * 108 for _ in range(14)))
            lines = console.render_lines(body, options, pad=True)
            _music_bar.draw_music_pocket(console, options, lines)
            _music_bar.draw_back_pocket(console, options, lines, 1.0, extra_tabs=tabs, lead_tab="Continue")
        return ["".join(s.text for s in row) for row in lines]

    def setup_method(self):
        _music_bar.collapse_tabs()
        _music_bar._tab_presence.clear()
        _music_bar._tab_memory.clear()
        _music_bar.toggle_tabs()

    def teardown_method(self):
        _music_bar.collapse_tabs()

    def _live(self):
        return [r.split("›")[1].split("│")[0].strip() for r in self._rows() if "›" in r]

    def test_exactly_one_row_is_live(self):
        assert self._live() == ["Edit"]

    def test_the_rows_are_separated(self):
        rows = self._rows()
        at = {n: i for i in range(len(rows)) for n in ("Edit", "Regenerate", "Export") if n in rows[i]}
        # A blank row between each pair, so three actions read as three things
        # to pick from rather than one block of text.
        assert at["Regenerate"] - at["Edit"] == 2
        assert at["Export"] - at["Regenerate"] == 2

    def test_the_arrows_move_it_and_stop_at_the_ends(self):
        _music_bar.stack_move(1)
        assert self._live() == ["Regenerate"]
        _music_bar.stack_move(1)
        _music_bar.stack_move(1)  # past the bottom
        assert self._live() == ["Export"]
        assert _music_bar.stack_selected_key() == "act:Export"
        for _ in range(5):  # and past the top
            _music_bar.stack_move(-1)
        assert self._live() == ["Edit"]
        assert _music_bar.stack_selected_key() == "act:Edit"

    def test_it_always_opens_on_the_first_row(self):
        self._rows()
        _music_bar.stack_move(2)
        _music_bar.toggle_tabs()  # closed
        _music_bar.toggle_tabs()  # and open again
        assert _music_bar.stack_selected_key() == "act:Edit"


class TestPagerPill:
    """One tab split in two: this page, and the one Continue leads to."""

    W, H = 120, 14

    def _rows(self, pager=("Analysis", "Insights", 0)):
        panel = Panel(Text("body"), height=self.H, padding=(1, 2))
        console = Console(width=self.W, height=self.H, file=StringIO())
        _music_bar._back_presence = 1.0
        frame = _music_bar._MusicPocketFrame(panel, with_back=True)
        frame.pager = pager
        lines = console.render_lines(frame, console.options, pad=True)
        return ["".join(s.text for s in row) for row in lines]

    def test_the_key_is_named_outside_the_button(self):
        # The two halves are what the button offers; how to move between them is
        # an instruction ABOUT the button, so it sits outside its left border
        # and the divider stays a plain rule.
        row = next(r for r in self._rows() if "Analysis" in r and "Insights" in r)
        between = row[row.index("Analysis") : row.index("Insights")]
        assert "│" in between
        assert "tab" not in between
        assert "←tab→" in row[: row.index("Analysis")]

    def test_each_half_s_own_label_is_inside_its_own_region(self):
        # The divider is five cells wide now, not one. Both halves' click rects
        # have to clear it, or the far label reports the near half.
        rows = self._rows()
        row = next(r for r in rows if "Analysis" in r and "Insights" in r)
        regions = {key: (x0, x1) for x0, _y0, x1, _y1, key in _music_bar.pager_regions()}
        # 1-based columns, the convention read_key hit-tests against.
        for label, key in (("Analysis", "pager:0"), ("Insights", "pager:1")):
            lo, hi = regions[key]
            at = row.index(label) + 1
            assert lo <= at <= hi, (label, at, lo, hi)
            assert lo <= at + len(label) - 1 <= hi, (label, at, lo, hi)

    def test_it_sits_between_the_two_corners_and_touches_neither(self):
        rows = self._rows()
        row = next(r for r in rows if "Analysis" in r)
        assert row.index("back") < row.index("Analysis") < row.index("Insights") < row.index("♪")

    def test_the_live_half_is_marked_without_moving_anything(self):
        # Weight and colour only — no caret. A marker that appears on one side
        # and not the other is one more thing that changes when you cross.
        console = Console(width=self.W, height=self.H, file=StringIO())

        def _styles(active):
            panel = Panel(Text("body"), height=self.H, padding=(1, 2))
            _music_bar._back_presence = 1.0
            frame = _music_bar._MusicPocketFrame(panel, with_back=True)
            frame.pager = ("Analysis", "Insights", active)
            out = {}
            for row in console.render_lines(frame, console.options, pad=True):
                for seg in row:
                    if seg.text.strip() in ("Analysis", "Insights"):
                        out[seg.text.strip()] = str(seg.style)
            return out

        here, ahead = _styles(0), _styles(1)
        assert "bold" in here["Analysis"] and "bold" not in here["Insights"]
        assert "bold" in ahead["Insights"] and "bold" not in ahead["Analysis"]
        assert "›" not in "".join(self._rows())

    def test_it_never_moves(self):
        # Not centred on the gap between the corners: that gap changes width
        # whenever the left strip does — a drawer opening, Anonymize becoming
        # Adjust and Revert — and a control that slides because something else
        # resized is a control you have to find again.
        seen = set()
        for left_end, active in ((2, 0), (2, 1), (30, 0), (48, 1)):
            _music_bar._left_tabs_end = left_end
            rows = self._rows(("Analysis", "Insights", active))
            seen.add(next(r for r in rows if "Analysis" in r).index("Analysis"))
        assert len(seen) == 1, seen

    def test_each_half_is_its_own_click_target(self):
        self._rows()
        halves = _music_bar.pager_regions()
        assert [k for *_r, k in halves] == ["pager:0", "pager:1"]
        # Side by side on one row, and they do not overlap.
        (lx0, ly0, lx1, _ly1, _lk), (rx0, _ry0, rx1, _ry1, _rk) = halves
        assert lx0 < lx1 < rx0 < rx1
        assert ly0 == _ly1 == _ry0

    def test_no_pager_draws_nothing(self):
        rows = self._rows(pager=None)
        assert not any("Analysis" in r for r in rows)
        assert _music_bar.pager_regions() == []


def test_the_lead_tab_is_the_forward_action_not_the_first_one():
    # By NAME, not by position. On the results page the first action was Export,
    # so an unrelated action stood on its own while the one you were looking for
    # stayed stacked.
    from yeaboi.ui.shared._components import ANALYSIS_THEME

    console = Console(width=110, height=14, force_terminal=False)
    options = console.options.update_dimensions(110, 14)
    names = ("Export", "Share Online", "Anonymize", "Continue")
    tabs = [(_music_bar.build_tab_text(n, ANALYSIS_THEME), f"act:{n}") for n in names]
    _music_bar.collapse_tabs()
    _music_bar._tab_presence.clear()
    _music_bar._tab_memory.clear()
    lines = []
    for _ in range(40):
        lines = console.render_lines(Text("\n".join(" " * 108 for _ in range(14))), options, pad=True)
        _music_bar.draw_music_pocket(console, options, lines)
        _music_bar.draw_back_pocket(console, options, lines, 1.0, extra_tabs=tabs, lead_tab="Continue")
    drawn = "\n".join("".join(s.text for s in row) for row in lines)
    assert "Continue" in drawn
    assert "3 actions" in drawn
    assert "Export" not in drawn


def test_tab_never_crosses_a_pager_whose_far_half_leaves():
    # Tab is one of the easiest keys to hit by accident. A page whose other half
    # walks out of the flow opts out, and the pill stays clickable either way.
    console = Console(width=150, height=14, file=StringIO())
    _music_bar._back_presence = 1.0
    for pager, crossable in ((("A", "B", 0), True), (("A", "B", 1, False), False)):
        panel = Panel(Text("body"), height=14, padding=(1, 2))
        frame = _music_bar._MusicPocketFrame(panel, with_back=True)
        frame.pager = pager
        console.render_lines(frame, console.options, pad=True)
        assert bool(_music_bar.pager_other_key()) is crossable, pager
        assert [k for *_r, k in _music_bar.pager_regions()] == ["pager:0", "pager:1"], pager


# ── Chrome mascot (the robo on Agents pages) ─────────────────────────────────


def _styled_frame_text(frame, width=120, height=24):
    """ANSI-styled render of a pocket frame (rgb assertions, not glyphs)."""
    # Pinned truecolor: auto-detection reads COLORTERM (set in dev shells,
    # unset on CI), which would downgrade these rgb assertions to 8-colour.
    console = Console(width=width, height=height, file=StringIO(), force_terminal=True, color_system="truecolor")
    with console.capture() as cap:
        console.print(frame)
    return cap.get()


def test_robo_mascot_draws_steel_companion():
    from yeaboi.ui.shared._music_bar import _MusicPocketFrame

    _music_bar._duck_slide_start = 0.0
    panel = Panel(Text("body"), height=24, padding=(1, 2))
    out = _styled_frame_text(_MusicPocketFrame(panel, duck_mascot="robo"))
    assert "140;160;178" in out  # robo steel
    assert "34;158;122" not in out  # no duck green anywhere on a robo page


def test_duck_mascot_default_unchanged():
    from yeaboi.ui.shared._music_bar import _MusicPocketFrame

    panel = Panel(Text("body"), height=24, padding=(1, 2))
    out = _styled_frame_text(_MusicPocketFrame(panel))
    assert "34;158;122" in out


def test_entrance_uses_the_robo_mini(monkeypatch):
    from yeaboi.ui.shared._music_bar import _MusicPocketFrame, start_duck_entrance

    start_duck_entrance()
    monkeypatch.setattr(_music_bar.time, "monotonic", lambda: _music_bar._duck_entrance_start + 0.5)
    panel = Panel(Text("body"), height=30, padding=(1, 2))
    out = _styled_frame_text(_MusicPocketFrame(panel, duck_mascot="robo"), height=30)
    assert "140;160;178" in out
    assert "34;158;122" not in out


def test_get_renderable_adopts_and_resets_the_stamp():
    live = _music_bar.MusicLive(console=Console(width=120, height=40, file=StringIO()))
    stamped = Panel(Text("body"), height=40)
    stamped._duck_mascot = "robo"
    live.update(stamped)
    live.get_renderable()
    assert _music_bar.current_chrome_mascot() == "robo"
    live.update(Panel(Text("body"), height=40))
    live.get_renderable()
    assert _music_bar.current_chrome_mascot() == "duck"


def test_reset_duck_state_restores_duck():
    _music_bar._chrome_mascot = "robo"
    _music_bar._reset_duck_state()
    assert _music_bar.current_chrome_mascot() == "duck"


def test_poke_duck_quacks_for_robo(monkeypatch):
    _music_bar._chrome_mascot = "robo"
    _music_bar.poke_duck()
    assert _music_bar._duck_shades_start == 0.0  # no shades gag on a visor
    assert _music_bar._duck_quack_start > 0.0  # he beeps instead


def test_poke_duck_still_lifts_shades_for_duck():
    _music_bar.poke_duck()
    assert _music_bar._duck_shades_start > 0.0
