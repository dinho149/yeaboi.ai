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
# Set while a field is being typed into, so the app-wide single-letter shortcuts
# (currently 'c' for the controls drawer) don't steal characters from the buffer.
_text_entry = False
# Raw bytes read ahead of the caller and not yet consumed: the tail of a chunked
# bracketed-paste read that ran past the end marker. Popped before touching the fd
# so the keystroke typed straight after a paste is never lost. Distinct from
# _pushback, which holds already-decoded key NAMES and pops LIFO; these are bytes
# and must come back in order. Single-threaded input, so a plain bytearray is safe.
_pending_bytes = bytearray()
# Characters the most recent bracketed paste dropped at _PASTE_KEEP_LIMIT, so the
# caller can report what the terminal sent rather than only what survived. Read via
# take_paste_dropped(); same "extra detail about the key just returned" pattern as
# _last_read_had_input above.
_last_paste_dropped = 0
# Set when a paste was abandoned mid-stream. A one-shot tcflush only discards
# what is queued at that instant, and both abandon triggers mean "the stream is
# still coming" — so the remainder would arrive as keystrokes, including the
# "\r" that means enter. While this is set, every read first swallows input up
# to the end marker.
_paste_discarding = False

# Bracketed paste bounds. The keep limit is the de-facto ceiling for every
# single-line field in the app — most take a payload with no bound of their own,
# and a six-figure paste into one builds a Text of that many cells and re-wraps
# it every frame. Raising it does NOT buy the chat box better reporting: the
# overflow is counted into take_paste_dropped() either way, so "Pasted 34,812
# characters" survives a ceiling far below 34,812. The drain limit and the two
# clocks bound a paste whose end marker never arrives — a byte-at-a-time
# blocking read used to hang the TUI outright in that case.
_PASTE_KEEP_LIMIT = 10_000
_PASTE_DRAIN_LIMIT = 4_000_000
_PASTE_IDLE_SECONDS = 1.5
_PASTE_DRAIN_SECONDS = 5.0
_PASTE_END = b"\x1b[201~"
_PASTE_CHUNK = 65536


def set_text_entry(active: bool) -> None:
    """Suppress/restore bare-letter global shortcuts around an in-place text edit.

    Screens that type into a field WHILE showing app-wide chrome (Settings is the
    first) bracket the edit with this, so pressing 'c' types a 'c'.
    """
    global _text_entry
    _text_entry = active


def push_back_key(key: str) -> None:
    """Return a key to the front of the input stream (LIFO with the buffer)."""
    _pushback.append(key)


def take_paste_dropped() -> int:
    """Characters the most recent bracketed paste dropped at _PASTE_KEEP_LIMIT.

    Read-and-reset: a "paste:" key and its drop count are consumed together, so a
    stale count can never be attributed to the next paste. Kept off the key string
    on purpose — "paste:<content>" is parsed by a dozen screens, and a wire-format
    change would make every one of them silently swallow the paste instead of
    failing loudly.
    """
    global _last_paste_dropped
    dropped, _last_paste_dropped = _last_paste_dropped, 0
    return dropped


def paste_payload(key: str, *, multiline: bool = False) -> str:
    """The text carried by a "paste:" key, shaped for the field receiving it.

    Bracketed paste preserves newlines (a pasted brief keeps its paragraphs), so
    single-line fields — the default — drop the edge newlines and collapse the
    inner ones to spaces: a copied API token almost always carries a trailing
    newline, and a literal "\n" in a one-row box is invisible corruption.

    Only newlines are stripped, not whitespace generally — pasting mid-cell in
    an editor must not silently lose a space the user meant to paste.
    """
    text = key[len("paste:") :] if key.startswith("paste:") else ""
    if multiline:
        return text
    return text.strip("\r\n").replace("\n", " ")


# True when the most recent "esc" event came from clicking the back tab rather
# than from the Esc key. Screens where Esc pops an internal focus level use this
# to keep the two apart: the key steps back one level, the tab leaves outright.
_esc_from_back_tab = False


def esc_came_from_back_tab() -> bool:
    """Whether the last ``"esc"`` was a click on the back tab, not the Esc key."""
    return _esc_from_back_tab


def _esc(*, from_tab: bool = False) -> str:
    """Return the ``"esc"`` key event, starting the back tab's fold-away first.

    Esc (and a click on the tab itself) is the app-wide go-back gesture, so the
    tab must begin retracting on the PRESS rather than when the destination screen
    finally renders — otherwise the fold trails into the next screen's entrance.
    Latching here covers every screen at once, since all input flows through here.

    ``from_tab`` records which of the two it was, for the screens that care (see
    esc_came_from_back_tab).
    """
    global _esc_from_back_tab
    _esc_from_back_tab = from_tab
    try:
        from yeaboi.ui.shared._music_bar import close_controls, controls_open, nudge_music_bar, retract_back_tab

        # An open controls drawer swallows the Esc: it closes the drawer instead of
        # navigating back, so Esc always means "dismiss what's on top".
        if controls_open():
            close_controls()
            nudge_music_bar()
            return ""
        retract_back_tab()
    except Exception:  # noqa: BLE001 - never let chrome bookkeeping break input
        pass
    return "esc"


def _read_paste(fd: int) -> tuple[str, int]:
    """Read a bracketed-paste payload through to its "\x1b[201~" end marker.

    Returns (kept, total): the cleaned text, capped at _PASTE_KEEP_LIMIT, and how
    many characters the terminal actually sent.

    Three things here are load-bearing and were each a bug before:

    1. It ALWAYS drains to the end marker, however much it keeps. Stopping early
       leaves the rest of the paste in the tty, where the next reads decode it as
       individual keystrokes — including the "\r" that means "enter", which sends
       a half-pasted message and then types the remainder into the next one.
    2. It reads in chunks and decodes ONCE. Byte-at-a-time decoding turned every
       non-ASCII character (curly quotes, em dashes, emoji) into U+FFFD, and would
       cost millions of syscalls at these sizes.
    3. Every read is bounded. The old loop's blocking read hung the TUI forever if
       a paste was aborted mid-stream.
    """
    global _paste_discarding
    import select as _select
    import time as _time

    # Seed from the read-ahead tail: two pastes in one burst leave the second
    # one's opening bytes there, and reading the fd first would reorder them.
    buf = bytearray(_pending_bytes)
    _pending_bytes.clear()
    deadline = _time.monotonic() + _PASTE_DRAIN_SECONDS
    end = buf.find(_PASTE_END)
    abandoned = overflowing = False
    while end == -1:
        if _time.monotonic() > deadline or len(buf) > _PASTE_DRAIN_LIMIT:
            # Still gushing when we gave up — the rest is on its way.
            abandoned = overflowing = True
            break
        if not _select.select([fd], [], [], _PASTE_IDLE_SECONDS)[0]:
            # Paste bytes stream continuously; a gap this long means the stream
            # died without a terminator. Take what we have rather than block.
            # Generous on purpose: a short gap is latency on a lossy link, and
            # calling that death costs more than waiting does.
            abandoned = True
            break
        chunk = os.read(fd, _PASTE_CHUNK)
        if not chunk:
            abandoned = True
            break
        # Search from just before the join so a marker split across two chunks
        # is still found, without rescanning the whole buffer each time.
        start = max(0, len(buf) - (len(_PASTE_END) - 1))
        buf += chunk
        end = buf.find(_PASTE_END, start)
        if end != -1:
            break

    if end == -1:
        payload, tail = bytes(buf), b""
    else:
        payload, tail = bytes(buf[:end]), bytes(buf[end + len(_PASTE_END) :])
    if abandoned:
        # Only here: the stream is malformed or absurd, so its remainder must be
        # discarded rather than replayed as fake keystrokes. The normal path must
        # never flush — per-call reads preserve type-ahead (see _read_key_impl).
        # The flush clears what is queued now; _paste_discarding covers whatever
        # the terminal is still writing — armed only when we gave up on a stream
        # that was still flowing. After a stall the stream is dead, and staying
        # armed would swallow the user's next keypress instead.
        _paste_discarding = overflowing
        try:
            termios.tcflush(fd, termios.TCIFLUSH)
        except Exception:  # noqa: BLE001 - a failed flush must not break input
            pass
    elif tail:
        _pending_bytes.extend(tail)

    text = payload.decode("utf-8", errors="replace")
    # CRLF and lone CR both mean "new line" here; keeping \n is the whole point,
    # since multi-line pastes used to arrive with their words glued together.
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if c == "\n" or c.isprintable())
    return text[:_PASTE_KEEP_LIMIT], len(text)


def _discard_to_marker(fd: int) -> None:
    """Swallow the remainder of an abandoned paste, up to its end marker.

    Armed by _read_paste when it gives up on a stream. Disarms on the marker —
    or on a long silence, which means the stream really did die and staying
    armed would eat the user's next keypress instead.
    """
    global _paste_discarding
    import select as _select
    import time as _time

    deadline = _time.monotonic() + _PASTE_DRAIN_SECONDS
    window = b""
    while _time.monotonic() < deadline:
        if not _select.select([fd], [], [], _PASTE_IDLE_SECONDS)[0]:
            break
        chunk = os.read(fd, _PASTE_CHUNK)
        if not chunk:
            break
        if _PASTE_END in window + chunk:
            tail = (window + chunk).split(_PASTE_END, 1)[1]
            if tail:
                _pending_bytes.extend(tail)
            break
        window = (window + chunk)[-(len(_PASTE_END) - 1) :]
    _paste_discarding = False


def _read_key_impl(stdin=None, timeout: float | None = None) -> str:
    """Read a single keypress from the terminal in raw mode.

    If timeout is given, returns "" if no key is pressed within that time.

    A key stashed by push_back_key() is returned first (immediately, ignoring
    timeout) — this is how a coalesced scroll burst hands back the non-scroll key
    that ended it.

    Returns standardised key names:
      - "up", "down", "left", "right" — arrow keys
      - "scroll_up", "scroll_down" — mouse wheel events
      - "click:<x>:<y>" — left-button click at 1-based cell (x, y)
      - "enter", "tab", "esc", "backspace", "clear" — special keys
      - "paste:<content>" — bracketed paste payload
      - single character — printable input

    Uses os.read() instead of file-object read() to bypass Python's internal
    read buffer.  Python's buffered I/O can pull extra bytes (e.g. the "[B"
    of an arrow-key escape sequence) into its own buffer where they become
    invisible to select(), causing escape-sequence detection to fail.
    """
    import select as _select

    global _last_read_had_input, _last_paste_dropped
    _last_read_had_input = False
    _last_paste_dropped = 0

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
        #   - ISIG — signal generation, so every control char arrives as a keypress
        #     rather than a signal; read_key turns Ctrl+C back into KeyboardInterrupt
        #     itself, so quitting behaves exactly as it looks.
        #   - ICRNL — CR-to-NL translation, so a pasted CRLF arrives as CRLF and
        #     _read_paste can fold it into one newline. Left on, the line
        #     discipline hands us "\n\n" and every Windows-line-ended paste
        #     comes out double-spaced. Enter is unaffected: "\r" and "\n" both
        #     already decode to "enter" below.
        new_settings = termios.tcgetattr(fd)
        new_settings[0] &= ~(termios.IXON | termios.ICRNL)  # input flags (c_iflag)
        new_settings[3] &= ~(termios.IEXTEN | termios.ISIG)  # local flags (c_lflag)
        termios.tcsetattr(fd, termios.TCSANOW, new_settings)
        # After the mode is set, never before: in the terminal's cooked mode a
        # read stops at the line ending, so the marker would never be found.
        if _paste_discarding:
            _discard_to_marker(fd)
        if timeout is not None and not _pending_bytes:
            try:
                ready, _, _ = _select.select([fd], [], [], timeout)
            except KeyboardInterrupt:
                raise
            if not ready:
                return ""

        def _read1() -> str:
            """Read exactly 1 byte, from the paste read-ahead tail or the fd.

            Bypasses Python's buffer (see the docstring above); the tail comes
            first and in order, so a key typed straight after a paste survives.
            """
            if _pending_bytes:
                # Take a whole UTF-8 sequence, not a byte: a curly quote typed
                # straight after a paste would otherwise arrive as U+FFFD.
                lead = _pending_bytes[0]
                width = 4 if lead >= 0xF0 else 3 if lead >= 0xE0 else 2 if lead >= 0xC0 else 1
                chunk = bytes(_pending_bytes[:width])
                del _pending_bytes[:width]
                return chunk.decode("utf-8", errors="replace")
            return os.read(fd, 1).decode("utf-8", errors="replace")

        def _more_input(wait: float) -> bool:
            """Is another byte available — from the read-ahead tail or the fd?

            Every "is there more?" check must ask this rather than select() on
            the fd alone. A tail holding an escape sequence (a second paste in
            the same burst, or an arrow key typed straight after one) would
            otherwise be read as a bare Esc followed by its literal characters
            — and two of those inside the double-Esc window quit the chat and
            throw the draft away.
            """
            if _pending_bytes:
                return True
            return bool(_select.select([fd], [], [], wait)[0])

        def _read_available(wait: float = 0.05) -> str:
            """Read all immediately available bytes (tail first, then the fd)."""
            buf = ""
            while _more_input(wait):
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
            if not _more_input(0.1):
                return _esc()
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
                    if is_press and len(parts) >= 3:
                        try:
                            button = int(parts[0])
                            cx = int(parts[1])
                            cy = int(parts[2])
                        except ValueError:
                            return ""
                        if button == 64:
                            return "scroll_up"
                        if button == 65:
                            return "scroll_down"
                        # Plain left-button press (button 0, no motion/modifier
                        # flag bits set) → a click. Return the 1-based cell the
                        # pointer is over so a screen can hit-test it against its
                        # own layout (e.g. click-to-select a menu item). Middle/
                        # right clicks and modified clicks are still swallowed.
                        if button == 0:
                            # A click on the app-wide "go back" tab (bottom-left)
                            # is Esc, so the tab works on every screen without
                            # per-loop wiring. Lazy import avoids an import cycle.
                            from yeaboi.ui.shared._music_bar import back_region, chrome_tab_regions

                            _br = back_region()
                            if _br is not None and _br[0] <= cx <= _br[2] and _br[1] <= cy <= _br[3]:
                                # Clicking the tab IS Esc (and folds it away) — flagged
                                # so a screen can tell the button from the key.
                                return _esc(from_tab=True)
                            for _x0, _y0, _x1, _y1, _key in chrome_tab_regions():
                                if _x0 <= cx <= _x1 and _y0 <= cy <= _y1:
                                    return _key  # e.g. the 'c copy' tab presses 'c'
                            # The persistent controls tab toggles its drawer.
                            from yeaboi.ui.shared._music_bar import controls_region, toggle_controls

                            _cr = controls_region()
                            if _cr is not None and _cr[0] <= cx <= _cr[2] and _cr[1] <= cy <= _cr[3]:
                                toggle_controls()
                                return ""
                            # Poke the companion duck → the double-shades gag, the
                            # same reward the welcome screen gives, on every page
                            # he rides along on.
                            from yeaboi.ui.shared._music_bar import duck_region, nudge_music_bar, poke_duck

                            _dr = duck_region()
                            if _dr is not None and _dr[0] <= cx <= _dr[2] and _dr[1] <= cy <= _dr[3]:
                                poke_duck()
                                nudge_music_bar()
                                return ""
                            return f"click:{cx}:{cy}"
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
                        content, total = _read_paste(fd)
                        if content:
                            _last_paste_dropped = total - len(content)
                            return f"paste:{content}"
                    else:
                        _read_available()
                    return ""
                # Back-tab. Terminals send Shift+Tab as CSI Z, and without this it
                # falls into the drain below and reaches the app as nothing at all.
                if ch3 == "Z":
                    return "shift+tab"
                # Unknown CSI sequence — drain and ignore
                _read_available()
                return ""
            return _esc()
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
        if ch == "\x12":
            # Ctrl+R → refresh an expired subscription token. Bound app-wide (see
            # read_key) but live only while one IS expired, so it stays free
            # otherwise.
            return "ctrl+r"
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

    # ISIG is off (see enter_raw_mode), so Ctrl+C arrives here as a keypress —
    # turn it back into the interrupt it looks like.
    if key == "ctrl+c":
        raise KeyboardInterrupt

    # Any real keypress skips the duck's waddle-in — the user is here to work,
    # and the entrance must never make them wait (same rule as the chat).
    if key:
        from yeaboi.ui.shared._music_bar import skip_duck_entrance

        skip_duck_entrance()

    # Ctrl+R jumps to the subscription row in Settings, but only while the token
    # is actually stale — otherwise it falls through as an ordinary key so nothing
    # is shadowed for the sake of a state that is almost never on. The jump is a
    # request, not a navigation: the key can be pressed from inside any mode, and
    # only the hub knows how to route (see mode_select's pending-jump check).
    if key == "ctrl+r":
        from yeaboi.auth_state import subscription_stale

        if subscription_stale():
            from yeaboi.ui.shared._music_bar import nudge_music_bar, request_settings_jump

            request_settings_jump()
            nudge_music_bar()
            return ""

    # 'c' toggles the app-wide controls drawer, but ONLY where its tab is showing
    # and nothing is being typed into — a bare letter must not shadow a page's own
    # 'c' (copy on Usage, changelog on the welcome screen) or eat a character.
    if key == "c" and not _text_entry:
        from yeaboi.ui.shared._music_bar import controls_tab_visible, nudge_music_bar, toggle_controls

        if controls_tab_visible():
            toggle_controls()
            nudge_music_bar()  # redraw immediately so it opens on the keypress
            return ""

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
        # Mirror read_key: drop IXON/IEXTEN/ISIG so Ctrl+S / Ctrl+O / Ctrl+C reach
        # the app rather than the line discipline (read_key re-raises Ctrl+C),
        # and ICRNL so a pasted CRLF stays CRLF (see read_key for why).
        m = termios.tcgetattr(fd)
        m[0] &= ~(termios.IXON | termios.ICRNL)
        m[3] &= ~(termios.IEXTEN | termios.ISIG)
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


def drain_pending_input(stdin=None) -> None:
    """Discard anything typed but not yet read.

    For the moment a flow hands control back: a paste can overrun its field, and a
    key pressed while something was still finishing is not an instruction to the
    screen that appears next. Without this, a leftover keystroke arrives as a
    command the user never aimed at what is now in front of them.

    Never raises — a terminal that will not flush is not a reason to fail.
    """
    try:
        fd = (stdin or sys.stdin).fileno()
        termios.tcflush(fd, termios.TCIFLUSH)
    except Exception:  # noqa: BLE001 - not a tty, or a platform without tcflush
        pass


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
