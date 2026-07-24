"""Terminal input reading — raw keypress handling and bracketed paste mode.

# See docs: "Architecture" — shared UI utility for reading raw keypresses.
# Used by mode_select, session, and provider_select screens. Reads single
# keypresses in cbreak mode, handles escape sequences (arrows, paste), and
# returns standardised key names ("up", "down", "enter", "esc", etc.).
"""

from __future__ import annotations

import os
import sys
import termios
import tty

# Keys read ahead while coalescing a fast scroll burst but not consumed (a
# non-scroll key drained past the end of the burst) are stashed here and returned
# by the next read_key() call, so no keypress is ever lost. Single-threaded input,
# so a plain module list is safe. See coalesce_scroll() in _scroll.py.
_pushback: list[str] = []
# Set by _read_key_impl so the public wrapper can distinguish a real terminal
# event that decoded to "" (for example a consumed mouse click) from a timeout.
_last_read_had_input = False


def push_back_key(key: str) -> None:
    """Return a key to the front of the input stream (LIFO with the buffer)."""
    _pushback.append(key)


def _read_key_impl(stdin=None, timeout: float | None = None) -> str:
    """Read a single keypress from the terminal in raw mode.

    If timeout is given, returns "" if no key is pressed within that time.

    A key stashed by push_back_key() is returned first (immediately, ignoring
    timeout) — this is how a coalesced scroll burst hands back the non-scroll key
    that ended it.

    Returns standardised key names:
      - "up", "down", "left", "right" — arrow keys
      - "scroll_up", "scroll_down" — mouse wheel events
      - "enter", "tab", "esc", "backspace", "clear" — special keys
      - "paste:<content>" — bracketed paste payload
      - single character — printable input

    Uses os.read() instead of file-object read() to bypass Python's internal
    read buffer.  Python's buffered I/O can pull extra bytes (e.g. the "[B"
    of an arrow-key escape sequence) into its own buffer where they become
    invisible to select(), causing escape-sequence detection to fail.
    """
    import select as _select

    global _last_read_had_input
    _last_read_had_input = False

    if _pushback:
        _last_read_had_input = True
        return _pushback.pop()

    fd = (stdin or sys.stdin).fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        # TCSANOW, not setcbreak's default TCSAFLUSH: TCSAFLUSH discards any
        # input queued between read_key calls, silently dropping keypresses
        # that arrive while a frame is rendering (worst under slow terminals,
        # where rendering dominates the frame budget). Session-start flushing
        # is enter_raw_mode's job; per-call reads must preserve type-ahead.
        tty.setcbreak(fd, termios.TCSANOW)
        # Disable two terminal features so their control chars reach the app
        # instead of being consumed by the line discipline (restored below):
        #   - IXON  — XON/XOFF flow control, so Ctrl+S (\x13) doesn't freeze us.
        #   - IEXTEN — extended input, so Ctrl+O (\x0f, VDISCARD on macOS/BSD)
        #     is delivered as a keypress (used for the music channel-switch chord)
        #     rather than swallowed as "discard output".
        new_settings = termios.tcgetattr(fd)
        new_settings[0] &= ~termios.IXON  # input flags (c_iflag)
        new_settings[3] &= ~termios.IEXTEN  # local flags (c_lflag)
        termios.tcsetattr(fd, termios.TCSANOW, new_settings)
        if timeout is not None:
            try:
                ready, _, _ = _select.select([fd], [], [], timeout)
            except KeyboardInterrupt:
                raise
            if not ready:
                return ""

        def _read1() -> str:
            """Read exactly 1 byte from fd, bypassing Python's buffer."""
            return os.read(fd, 1).decode("utf-8", errors="replace")

        def _read_available(wait: float = 0.05) -> str:
            """Read all immediately available bytes from fd."""
            buf = ""
            while _select.select([fd], [], [], wait)[0]:
                buf += _read1()
                wait = 0.01  # shorter timeout for subsequent chars
            return buf

        ch = _read1()
        _last_read_had_input = True
        if ch == "\x1b":
            # Non-blocking check for the second byte — if nothing arrives
            # within 100ms, this is a bare Escape keypress (not an arrow
            # key or other escape sequence, which always sends \x1b[...
            # within microseconds). 100ms is imperceptible to a human
            # but safe for slow terminals / SSH connections.
            if not _select.select([fd], [], [], 0.1)[0]:
                return "esc"
            ch2 = _read1()
            if ch2 == "\x7f":
                # Alt+Backspace → delete word backward
                return "word_backspace"
            # Alt+Enter (Option+Enter on macOS) → newline
            if ch2 in ("\r", "\n"):
                return "alt+enter"
            # Alt+b / Alt+f — word-level navigation (emacs-style)
            if ch2 == "b":
                return "shift+left"
            if ch2 == "f":
                return "shift+right"
            if ch2 == "[":
                # Third byte: blocking read
                ch3 = _read1()
                if ch3 == "A":
                    return "up"
                if ch3 == "B":
                    return "down"
                if ch3 == "C":
                    return "right"
                if ch3 == "D":
                    return "left"
                # SGR mouse events: \x1b[<button;x;yM or \x1b[<button;x;ym
                # Enabled by enable_mouse_tracking(). Button 64 = scroll up,
                # 65 = scroll down. Other mouse events (clicks, motion) are
                # consumed and discarded so they don't leak to the terminal.
                # Modified keys: \x1b[1;{mod}{dir} where mod 2=Shift, 3=Alt, 5=Ctrl
                # Shift+Left/Right are used for word-level navigation.
                # CSI u (kitty keyboard protocol): \x1b[13;2u = Shift+Enter
                if ch3 == "1":
                    rest = _read_available(0.05)
                    if rest == "~":
                        return "home"  # \x1b[1~ — Home on vt-style terminals
                    if rest.startswith("3;2u"):
                        return "alt+enter"
                    if rest.startswith(";2D"):
                        return "shift+left"
                    if rest.startswith(";2C"):
                        return "shift+right"
                    if rest.startswith(";2A"):
                        return "shift+up"
                    if rest.startswith(";2B"):
                        return "shift+down"
                    # Alt+arrow: \x1b[1;3{dir}
                    if rest.startswith(";3D"):
                        return "shift+left"  # treat Alt+arrow same as Shift+arrow
                    if rest.startswith(";3C"):
                        return "shift+right"
                    # Ctrl+arrow: \x1b[1;5{dir}
                    if rest.startswith(";5D"):
                        return "shift+left"
                    if rest.startswith(";5C"):
                        return "shift+right"
                    return ""
                if ch3 == "3":
                    ch4 = _read1()
                    if ch4 == "~":
                        return "delete"
                    if ch4 == ";":
                        # Shift+Delete: \x1b[3;2~
                        rest = _read_available(0.05)
                        if rest.startswith("2~"):
                            return "word_delete"
                    _read_available()
                    return ""
                # Page / Home / End as CSI-tilde sequences (vt-style, used by
                # many terminals for the navigation cluster). Scroll loops handle
                # these via apply_scroll(). \x1b[5~ PageUp, \x1b[6~ PageDown,
                # \x1b[1~/\x1b[7~ Home, \x1b[4~/\x1b[8~ End.
                if ch3 in ("5", "6", "4", "7", "8"):
                    ch4 = _read1()
                    if ch4 == "~":
                        return {
                            "5": "pageup",
                            "6": "pagedown",
                            "4": "end",
                            "7": "home",
                            "8": "end",
                        }[ch3]
                    _read_available()
                    return ""
                if ch3 == "H":
                    return "home"
                if ch3 == "F":
                    return "end"
                if ch3 == "<":
                    # Read until 'M' or 'm' (SGR terminator).
                    # 'M' = button press, 'm' = button release.
                    sgr_buf = ""
                    is_press = True
                    while True:
                        c = _read1()
                        if c == "M":
                            is_press = True
                            break
                        if c == "m":
                            is_press = False
                            break
                        sgr_buf += c
                        if len(sgr_buf) > 20:
                            break  # safety limit
                    parts = sgr_buf.split(";")
                    # Only act on press events — release events ('m') for
                    # scroll wheel would double-count each tick, causing jumps.
                    if is_press and len(parts) >= 1:
                        try:
                            button = int(parts[0])
                        except ValueError:
                            return ""
                        if button == 64:
                            return "scroll_up"
                        if button == 65:
                            return "scroll_down"
                    return ""  # consume releases & other mouse events silently
                # Legacy mouse: \x1b[M followed by 3 raw bytes (button, x, y).
                # Button byte 96 = scroll up (64+32), 97 = scroll down (65+32).
                if ch3 == "M":
                    btn = ord(_read1())
                    _read1()  # x
                    _read1()  # y
                    if btn == 96:
                        return "scroll_up"
                    if btn == 97:
                        return "scroll_down"
                    return ""  # consume other mouse events silently
                # Bracketed paste starts with \x1b[200~
                if ch3 == "2":
                    # Read remaining 3 chars of the start marker: "00~"
                    marker_rest = _read1() + _read1() + _read1()
                    if marker_rest == "00~":
                        # Blocking reads until end marker \x1b[201~
                        chars: list[str] = []
                        max_len = 10000  # safety limit
                        while len(chars) < max_len:
                            c = _read1()
                            if c == "\x1b":
                                # Potential end marker: \x1b[201~
                                m1 = _read1()
                                if m1 == "[":
                                    m2 = _read1() + _read1() + _read1() + _read1()
                                    if m2 == "201~":
                                        break  # end of paste
                                    chars.append("\x1b[" + m2)
                                else:
                                    chars.append("\x1b" + m1)
                            else:
                                chars.append(c)
                        content = "".join(chars)
                        content = content.replace("\r", "").replace("\n", "")
                        content = "".join(c for c in content if c.isprintable())
                        if content:
                            return f"paste:{content}"
                    else:
                        _read_available()
                    return ""
                # Unknown CSI sequence — drain and ignore
                _read_available()
                return ""
            return "esc"
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\t":
            return "tab"
        if ch in ("\x7f", "\x08"):
            return "backspace"
        if ch == "\x15":
            # Ctrl+U (kill line) → clear all
            return "clear"
        if ch == "\x17":
            # Ctrl+W → delete word backward
            return "word_backspace"
        if ch == "\x0e":
            # Ctrl+N → new line (works in all terminals)
            return "alt+enter"
        if ch == "\x13":
            return "ctrl+s"
        if ch == "\x16":
            # Ctrl+V → paste image from the OS clipboard. Terminals cannot deliver
            # image bytes via stdin (bracketed paste above is text-only), so input
            # loops handle "ctrl+v" by reading the clipboard directly — see
            # ui/shared/_attachments.py. Note: Cmd+V on macOS stays a terminal
            # *text* paste; Ctrl+V is the image binding, like Claude Code.
            return "ctrl+v"
        # Return global music controls as internal key names. The public wrapper
        # performs the action only after giving an active screensaver first chance
        # to consume the event as its wake-only key.
        if ch in ("\x10", "\x0f"):
            return "ctrl+p" if ch == "\x10" else "ctrl+o"
        if ch == "\x19":
            return "ctrl+y"
        if ch == "\x03":
            return "ctrl+c"
        if ch.isprintable():
            return ch
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old_settings)


def read_key(stdin=None, timeout: float | None = None) -> str:
    """Read one standardized key, with app-wide idle and wake handling.

    A real event wakes the screensaver before any global shortcut or underlying
    screen action runs. Timed polls that return no input leave the idle baseline
    untouched.
    """
    from yeaboi.ui.shared._screensaver import begin_input_wait, handle_input_event, show_screensaver_now

    begin_input_wait()
    key = _read_key_impl(stdin=stdin, timeout=timeout)
    if _last_read_had_input and handle_input_event():
        return ""

    if key == "ctrl+c":
        raise KeyboardInterrupt

    # Hidden app-wide preview shortcut: Y for Yeaboi. It deliberately has no
    # on-screen hint, but uses the same rendering/wake path as genuine idleness.
    if key == "ctrl+y":
        show_screensaver_now()
        return ""

    # Ctrl+P / Ctrl+O are global background-music controls. Keeping them after
    # wake handling prevents the key that dismisses the saver from also changing
    # playback state.
    if key in ("ctrl+p", "ctrl+o"):
        from yeaboi import music

        if key == "ctrl+p":
            music.toggle()
        else:
            music.cycle_channel()
        return ""
    return key


# Terminal settings saved by enter_raw_mode(), restored by exit_raw_mode().
_saved_term_settings = None


def enter_raw_mode(stdin=None) -> None:
    """Hold the terminal in cbreak + no-echo for the whole full-screen TUI.

    read_key() flips to cbreak per call but restores the *prior* settings in its
    finally, so between keypresses the terminal reverts to cooked + echo. During
    a fast mouse-wheel scroll, mouse-tracking report bytes (``\\x1b[<64;…M``)
    arrive in that between-reads window, get echoed to the screen as garbage, and
    tear the view — and the terminal (e.g. iTerm2) flags "mouse reporting left
    on". Holding cbreak for the entire session closes that window: read_key's
    per-call save/restore now captures and restores cbreak, so echo stays off the
    whole time. Idempotent-safe; a no-op if the fd isn't a real terminal.
    """
    global _saved_term_settings
    try:
        fd = (stdin or sys.stdin).fileno()
        _saved_term_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)  # disables ICANON + ECHO
        # Mirror read_key: drop IXON/IEXTEN so Ctrl+S / Ctrl+O reach the app.
        m = termios.tcgetattr(fd)
        m[0] &= ~termios.IXON
        m[3] &= ~termios.IEXTEN
        termios.tcsetattr(fd, termios.TCSANOW, m)
    except Exception:  # noqa: BLE001 - not a tty (pipe, redirect, CI); leave as-is
        _saved_term_settings = None


def exit_raw_mode(stdin=None) -> None:
    """Restore the terminal mode saved by :func:`enter_raw_mode`."""
    global _saved_term_settings
    if _saved_term_settings is None:
        return
    try:
        fd = (stdin or sys.stdin).fileno()
        termios.tcsetattr(fd, termios.TCSANOW, _saved_term_settings)
    except Exception:  # noqa: BLE001
        pass
    finally:
        _saved_term_settings = None


def enable_bracketed_paste() -> None:
    """Enable bracketed paste mode on the terminal."""
    sys.stdout.write("\x1b[?2004h")
    sys.stdout.flush()


def disable_bracketed_paste() -> None:
    """Disable bracketed paste mode on the terminal."""
    sys.stdout.write("\x1b[?2004l")
    sys.stdout.flush()


def _is_ide_terminal() -> bool:
    """Detect if we're running inside an IDE terminal that may not support mouse tracking.

    VS Code, JetBrains, and other IDE terminals can crash or misbehave when
    receiving mouse tracking escape sequences. Detecting these environments
    lets us skip mouse tracking to prevent terminal corruption.
    """
    import os

    # VS Code integrated terminal
    if os.environ.get("VSCODE_PID") or os.environ.get("TERM_PROGRAM") == "vscode":
        return True
    # JetBrains IDEs (IntelliJ, PyCharm, WebStorm, etc.)
    if os.environ.get("TERMINAL_EMULATOR") == "JetBrains-JediTerm":
        return True
    # Dumb terminals
    if os.environ.get("TERM") in ("dumb", "unknown", ""):
        return True
    return False


def enable_mouse_tracking() -> None:
    """Enable mouse event reporting so scrolling stays within the app.

    Skips mouse tracking in IDE terminals (VS Code, JetBrains) that are
    known to crash or misbehave with these escape sequences. Bracketed
    paste mode is still enabled as it's more widely supported.
    """
    if not _is_ide_terminal():
        sys.stdout.write("\x1b[?1000h")  # enable basic mouse tracking
        sys.stdout.write("\x1b[?1006h")  # enable SGR extended mode
    sys.stdout.write("\x1b[?2004h")  # enable bracketed paste mode
    sys.stdout.flush()


def disable_mouse_tracking() -> None:
    """Disable mouse event reporting — restore normal terminal behaviour."""
    sys.stdout.write("\x1b[?2004l")  # disable bracketed paste mode
    sys.stdout.write("\x1b[?1006l")
    sys.stdout.write("\x1b[?1000l")
    sys.stdout.flush()
