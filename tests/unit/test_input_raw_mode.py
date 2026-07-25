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
