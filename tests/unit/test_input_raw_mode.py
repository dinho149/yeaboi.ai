"""Regression tests for TUI terminal raw-mode handling (ui/shared/_input.py).

The "fast scroll breaks the view" bug: read_key() flips to cbreak per call and
restores the prior (cooked + echo) mode in its finally, so between keypresses the
terminal echoes any incoming bytes. During a fast mouse-wheel scroll, mouse-report
bytes arriving in that window get echoed as on-screen garbage. enter_raw_mode()
holds cbreak + no-echo for the whole session so that can't happen.
"""

from __future__ import annotations

import os
import select
import sys
import termios

import pytest

from yeaboi.ui.shared import _input
from yeaboi.ui.shared._input import enter_raw_mode, exit_raw_mode


def _echoed_bytes(fd_holder) -> int:
    """Write mouse-report bytes to a pty master; count what the slave echoes back."""
    master, slave = fd_holder
    payload = b"\x1b[<64;10;20M" * 5
    os.write(master, payload)
    echoed = b""
    for _ in range(50):
        r, _, _ = select.select([master], [], [], 0.05)
        if not r:
            break
        try:
            echoed += os.read(master, 4096)
        except OSError:
            break
    return len(echoed)


@pytest.fixture
def pty_pair(monkeypatch):
    master, slave = os.openpty()
    # Start in a normal cooked + echo mode, like a fresh shell.
    m = termios.tcgetattr(slave)
    m[3] |= termios.ICANON | termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, m)

    class _Stdin:
        def fileno(self):
            return slave

    monkeypatch.setattr(sys, "stdin", _Stdin())
    yield (master, slave)
    os.close(master)
    os.close(slave)


def test_cooked_mode_echoes_mouse_bytes(pty_pair):
    # Baseline: without raw mode the terminal echoes mouse bytes (the bug).
    assert _echoed_bytes(pty_pair) > 0


def test_enter_raw_mode_suppresses_mouse_echo(pty_pair):
    enter_raw_mode()
    try:
        assert _echoed_bytes(pty_pair) == 0
    finally:
        exit_raw_mode()


def test_exit_raw_mode_restores_echo_and_canonical(pty_pair):
    _, slave = pty_pair
    # Cooked mode has ECHO + ICANON on; enter_raw_mode clears them.
    assert termios.tcgetattr(slave)[3] & (termios.ECHO | termios.ICANON)
    enter_raw_mode()
    assert not (termios.tcgetattr(slave)[3] & (termios.ECHO | termios.ICANON))
    exit_raw_mode()
    # Restored — the meaningful line-discipline flags are back (ignoring the
    # driver's volatile PENDIN status bit, which isn't a real setting).
    assert termios.tcgetattr(slave)[3] & (termios.ECHO | termios.ICANON)


def test_exit_without_enter_is_noop():
    _input._saved_term_settings = None
    exit_raw_mode()  # must not raise


def test_ctrl_v_decodes_to_paste_image_key(pty_pair):
    # Ctrl+V (\x16) must map to the "ctrl+v" action so input loops can trigger
    # clipboard image paste (ui/shared/_attachments.py).
    #
    # The byte is written from a timer thread AFTER read_key is already
    # select()-waiting: read_key's setcbreak uses TCSAFLUSH, which both discards
    # any input written beforehand and (in the fixture's cooked+echo mode) would
    # let the line discipline swallow \x16 as VLNEXT — writing mid-wait mirrors
    # how a real keypress arrives.
    import threading

    master, slave = pty_pair

    class _Stdin:
        def fileno(self):
            return slave

    t = threading.Timer(0.2, os.write, args=(master, b"\x16"))
    t.start()
    try:
        assert _input.read_key(stdin=_Stdin(), timeout=3.0) == "ctrl+v"
    finally:
        t.cancel()


def test_ctrl_y_decodes_to_hidden_screensaver_key(pty_pair):
    import threading

    master, slave = pty_pair

    class _Stdin:
        def fileno(self):
            return slave

    t = threading.Timer(0.2, os.write, args=(master, b"\x19"))
    t.start()
    try:
        assert _input._read_key_impl(stdin=_Stdin(), timeout=3.0) == "ctrl+y"
    finally:
        t.cancel()


def _decode_mouse(pty_pair, payload: bytes) -> str:
    """Feed an SGR mouse report mid-wait and return the decoded key name."""
    import threading

    master, slave = pty_pair

    class _Stdin:
        def fileno(self):
            return slave

    t = threading.Timer(0.2, os.write, args=(master, payload))
    t.start()
    try:
        return _input._read_key_impl(stdin=_Stdin(), timeout=3.0)
    finally:
        t.cancel()


def test_back_tab_decodes_to_shift_tab(pty_pair):
    # Shift+Tab is CSI Z. Without a branch for it the sequence falls into the
    # unknown-CSI drain and reaches the app as nothing at all, so a page that
    # binds "shift+tab" silently has no backwards gesture at all.
    assert _decode_mouse(pty_pair, b"\x1b[Z") == "shift+tab"


def test_left_click_decodes_to_click_coordinates(pty_pair):
    # SGR left-button press \x1b[<0;12;5M → "click:12:5" (1-based col;row) so a
    # screen can hit-test the click against its layout (click-to-select a mode).
    assert _decode_mouse(pty_pair, b"\x1b[<0;12;5M") == "click:12:5"


def test_scroll_still_decodes_after_click_support(pty_pair):
    # Regression: adding click parsing must not disturb wheel decoding.
    assert _decode_mouse(pty_pair, b"\x1b[<64;3;4M") == "scroll_up"
    assert _decode_mouse(pty_pair, b"\x1b[<65;3;4M") == "scroll_down"


def test_left_button_release_is_swallowed(pty_pair):
    # Release events ('m') must not fire a second click (would double-activate).
    assert _decode_mouse(pty_pair, b"\x1b[<0;12;5m") == ""


def test_right_click_is_swallowed(pty_pair):
    # Only plain left clicks (button 0) select; other buttons stay consumed.
    assert _decode_mouse(pty_pair, b"\x1b[<2;12;5M") == ""


def test_enter_raw_mode_on_non_tty_is_safe(monkeypatch):
    # A pipe fd is not a terminal — enter_raw_mode must swallow the error.
    r, w = os.pipe()

    class _Stdin:
        def fileno(self):
            return r

    monkeypatch.setattr(sys, "stdin", _Stdin())
    try:
        enter_raw_mode()
        assert _input._saved_term_settings is None
        exit_raw_mode()  # no-op, must not raise
    finally:
        os.close(r)
        os.close(w)


class TestGlobalLetterShortcuts:
    """'c' toggles the controls drawer, but only where it's safe to claim.

    A bare letter as an app-wide shortcut has two ways to go wrong: shadowing a
    page's own 'c' (copy on Usage, changelog on the welcome screen) and eating a
    character out of a field being typed into. Both are guarded.
    """

    def test_text_entry_flag_round_trips(self):
        from yeaboi.ui.shared._input import set_text_entry

        set_text_entry(True)
        assert _input._text_entry is True
        set_text_entry(False)
        assert _input._text_entry is False

    def _press_c(self, monkeypatch, *, tab_visible: bool, typing: bool) -> tuple[str, bool]:
        """Drive read_key with a pending 'c'; return (key, drawer_toggled)."""
        from yeaboi.ui.shared import _music_bar
        from yeaboi.ui.shared._input import push_back_key, read_key, set_text_entry

        toggled: list[bool] = []
        monkeypatch.setattr(_music_bar, "controls_tab_visible", lambda: tab_visible)
        monkeypatch.setattr(_music_bar, "toggle_controls", lambda: toggled.append(True))
        monkeypatch.setattr(_music_bar, "nudge_music_bar", lambda: None)
        set_text_entry(typing)
        try:
            push_back_key("c")
            return read_key(timeout=0), bool(toggled)
        finally:
            set_text_entry(False)

    def test_c_opens_the_drawer_where_its_tab_shows(self, monkeypatch):
        key, toggled = self._press_c(monkeypatch, tab_visible=True, typing=False)
        assert toggled and key == ""  # consumed by the drawer

    def test_c_passes_through_where_the_tab_is_absent(self, monkeypatch):
        # Usage's "c copy" and the welcome screen's "c changelog" still get their key.
        key, toggled = self._press_c(monkeypatch, tab_visible=False, typing=False)
        assert not toggled and key == "c"

    def test_c_is_typed_into_a_field_being_edited(self, monkeypatch):
        key, toggled = self._press_c(monkeypatch, tab_visible=True, typing=True)
        assert not toggled and key == "c"

    def test_any_keypress_skips_the_duck_entrance(self):
        # The waddle-in must never make the user wait: the app-wide key layer
        # jumps it to the settled pose on the first real key, on every page.
        from yeaboi.ui.shared import _music_bar
        from yeaboi.ui.shared._input import push_back_key, read_key

        _music_bar._reset_duck_state()
        try:
            _music_bar.start_duck_entrance()
            assert _music_bar._duck_entrance_start > 0
            push_back_key("x")
            assert read_key(timeout=0) == "x"
            assert _music_bar._duck_entrance_start == 0.0
        finally:
            _music_bar._reset_duck_state()

    def test_ctrl_c_quits_outright(self, monkeypatch):
        # ISIG is cleared so it arrives as a keypress; read_key re-raises it, so
        # the conventional interrupt behaves exactly as it looks (no chord).
        from yeaboi.ui.shared._input import push_back_key, read_key

        push_back_key("ctrl+c")
        with pytest.raises(KeyboardInterrupt):
            read_key(timeout=0)


class TestBracketedPaste:
    """Bracketed paste: the reader must drain the whole payload, keep the line
    breaks, and report what it dropped.

    The old loop stopped at 10,000 characters *without* consuming the rest of
    the paste or its end marker, so the overflow stayed in the tty and came back
    as fake keystrokes — including the "\\r" that means "enter", which sent a
    half-pasted message and typed the remainder into the next one.
    """

    def _paste(self, pty_pair, payload: bytes, *, trailer: bytes = b"", timeout: float = 5.0) -> str:
        import threading

        master, slave = pty_pair

        class _Stdin:
            def fileno(self):
                return slave

        # Written from a timer, after the read is already waiting — see
        # test_ctrl_v_decodes_to_paste_image_key for why.
        t = threading.Timer(0.2, os.write, args=(master, b"\x1b[200~" + payload + b"\x1b[201~" + trailer))
        t.start()
        try:
            return _input._read_key_impl(stdin=_Stdin(), timeout=timeout)
        finally:
            t.cancel()

    def test_paste_decodes(self, pty_pair):
        assert self._paste(pty_pair, b"hello") == "paste:hello"

    def test_newlines_survive(self, pty_pair):
        # Multi-line pastes used to arrive as "onetwothreefour".
        key = self._paste(pty_pair, b"one\r\ntwo\rthree\nfour")
        assert key == "paste:one\ntwo\nthree\nfour"

    def test_control_chars_dropped_but_newlines_kept(self, pty_pair):
        assert self._paste(pty_pair, b"a\x07b\nc") == "paste:ab\nc"

    def test_utf8_is_not_mangled(self, pty_pair):
        # Byte-at-a-time decoding turned every one of these into U+FFFD.
        assert self._paste(pty_pair, "héllo — ✓".encode()) == "paste:héllo — ✓"

    def test_oversized_paste_drains_fully_and_reports(self, pty_pair):
        import threading

        master, slave = pty_pair

        class _Stdin:
            def fileno(self):
                return slave

        size = _input._PASTE_KEEP_LIMIT + 5_000
        payload = b"\x1b[200~" + (b"a" * size) + b"\x1b[201~" + b"Z"
        t = threading.Timer(0.2, os.write, args=(master, payload))
        t.start()
        try:
            key = _input._read_key_impl(stdin=_Stdin(), timeout=5.0)
            assert key.startswith("paste:")
            assert len(key) - len("paste:") == _input._PASTE_KEEP_LIMIT
            assert _input.take_paste_dropped() == 5_000
            # The keystroke typed straight after the paste survives, and nothing
            # of the overflow leaks in front of it.
            assert _input._read_key_impl(stdin=_Stdin(), timeout=2.0) == "Z"
        finally:
            t.cancel()

    def test_unterminated_paste_does_not_hang(self, pty_pair, monkeypatch):
        import threading

        monkeypatch.setattr(_input, "_PASTE_IDLE_SECONDS", 0.2)
        master, slave = pty_pair

        class _Stdin:
            def fileno(self):
                return slave

        t = threading.Timer(0.1, os.write, args=(master, b"\x1b[200~abc"))
        t.start()
        try:
            assert _input._read_key_impl(stdin=_Stdin(), timeout=3.0) == "paste:abc"
        finally:
            t.cancel()

    def test_marker_split_across_writes(self, pty_pair):
        import threading

        master, slave = pty_pair

        class _Stdin:
            def fileno(self):
                return slave

        timers = [
            threading.Timer(0.2, os.write, args=(master, b"\x1b[2")),
            threading.Timer(0.3, os.write, args=(master, b"00~hello")),
            threading.Timer(0.4, os.write, args=(master, b"\x1b[20")),
            threading.Timer(0.5, os.write, args=(master, b"1~")),
        ]
        for t in timers:
            t.start()
        try:
            assert _input._read_key_impl(stdin=_Stdin(), timeout=5.0) == "paste:hello"
        finally:
            for t in timers:
                t.cancel()

    def test_drop_count_is_cleared_by_the_next_key(self, pty_pair):
        import threading

        master, slave = pty_pair

        class _Stdin:
            def fileno(self):
                return slave

        _input._last_paste_dropped = 999
        t = threading.Timer(0.2, os.write, args=(master, b"k"))
        t.start()
        try:
            assert _input._read_key_impl(stdin=_Stdin(), timeout=3.0) == "k"
        finally:
            t.cancel()
        assert _input.take_paste_dropped() == 0

    def test_an_escape_sequence_in_the_read_ahead_tail_still_decodes(self, pty_pair):
        """The tail is read ahead of the caller, so every "is there more input?"
        check has to see it too.

        Reading it a byte at a time against a select() on the fd alone turns an
        arrow key typed after a paste into "esc" plus its literal characters —
        and two of those inside the double-Esc window quit the chat.
        """
        import threading

        master, slave = pty_pair

        class _Stdin:
            def fileno(self):
                return slave

        t = threading.Timer(0.2, os.write, args=(master, b"\x1b[200~hi\x1b[201~\x1b[A"))
        t.start()
        try:
            assert _input._read_key_impl(stdin=_Stdin(), timeout=3.0) == "paste:hi"
            assert _input._read_key_impl(stdin=_Stdin(), timeout=2.0) == "up"
        finally:
            t.cancel()

    def test_two_pastes_in_one_burst_both_decode(self, pty_pair):
        import threading

        master, slave = pty_pair

        class _Stdin:
            def fileno(self):
                return slave

        payload = b"\x1b[200~first\x1b[201~" + b"\x1b[200~second\x1b[201~"
        t = threading.Timer(0.2, os.write, args=(master, payload))
        t.start()
        try:
            assert _input._read_key_impl(stdin=_Stdin(), timeout=3.0) == "paste:first"
            assert _input._read_key_impl(stdin=_Stdin(), timeout=2.0) == "paste:second"
        finally:
            t.cancel()

    def test_multibyte_char_after_a_paste_is_not_mangled(self, pty_pair):
        import threading

        master, slave = pty_pair

        class _Stdin:
            def fileno(self):
                return slave

        t = threading.Timer(0.2, os.write, args=(master, "\x1b[200~hi\x1b[201~é".encode()))
        t.start()
        try:
            assert _input._read_key_impl(stdin=_Stdin(), timeout=3.0) == "paste:hi"
            assert _input._read_key_impl(stdin=_Stdin(), timeout=2.0) == "é"
        finally:
            t.cancel()

    def test_giving_up_on_a_flowing_stream_arms_the_discard(self, pty_pair, monkeypatch):
        import threading

        monkeypatch.setattr(_input, "_PASTE_DRAIN_LIMIT", 200)
        master, slave = pty_pair

        class _Stdin:
            def fileno(self):
                return slave

        t = threading.Timer(0.2, os.write, args=(master, b"\x1b[200~" + b"a" * 2_000))
        t.start()
        try:
            # What arrived is still handed over; the rest is now disowned.
            assert _input._read_key_impl(stdin=_Stdin(), timeout=3.0).startswith("paste:")
            assert _input._paste_discarding is True
        finally:
            t.cancel()
            _input._paste_discarding = False

    def test_the_armed_discard_swallows_the_remainder(self, pty_pair):
        """A one-shot tcflush cannot hold a stream that is still being written:
        the remaining "\r" would decode as enter and send a half-pasted message."""
        import threading

        master, slave = pty_pair

        class _Stdin:
            def fileno(self):
                return slave

        _input._paste_discarding = True
        t = threading.Timer(0.2, os.write, args=(master, b"rest\rmore\x1b[201~Q"))
        t.start()
        try:
            assert _input._read_key_impl(stdin=_Stdin(), timeout=3.0) == "Q"
            assert _input._paste_discarding is False
        finally:
            t.cancel()
            _input._paste_discarding = False

    def test_a_stalled_paste_does_not_stay_armed(self, pty_pair, monkeypatch):
        # The opposite case: the stream died, so the next thing typed is a key.
        import threading

        monkeypatch.setattr(_input, "_PASTE_IDLE_SECONDS", 0.2)
        master, slave = pty_pair

        class _Stdin:
            def fileno(self):
                return slave

        t = threading.Timer(0.1, os.write, args=(master, b"\x1b[200~abc"))
        t.start()
        try:
            assert _input._read_key_impl(stdin=_Stdin(), timeout=3.0) == "paste:abc"
            assert _input._paste_discarding is False
        finally:
            t.cancel()


class TestPastePayload:
    """paste_payload() shapes one payload for the two kinds of field."""

    def test_single_line_collapses_newlines(self):
        # A copied token almost always carries a trailing newline.
        assert _input.paste_payload("paste:sk-abc\n") == "sk-abc"
        assert _input.paste_payload("paste:one\ntwo") == "one two"

    def test_single_line_keeps_real_spaces(self):
        # Only newlines are stripped: pasting mid-cell in an editor must not
        # lose a space the user meant to paste.
        assert _input.paste_payload("paste: padded ") == " padded "

    def test_multiline_preserves(self):
        assert _input.paste_payload("paste:one\ntwo", multiline=True) == "one\ntwo"

    def test_non_paste_key_yields_nothing(self):
        assert _input.paste_payload("enter") == ""
