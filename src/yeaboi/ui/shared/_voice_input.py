"""Voice-input overlay for the TUI text-entry loops.

# See docs: "TUI system" — shared component used by every text entry point
# (project description, intake answers, artifact editor). It drives the
# record → transcribe flow.

Design: the caller passes a ``render_status(status, tick)`` callback that
re-renders *its own* screen with a recording/transcribing indicator, so the user
stays on the same screen (a pulsing input-box border + a status line) instead of
being taken to a full-screen popup. Recording stops on the next keypress (Esc
cancels); transcription then runs in a background thread so the animated
indicator keeps ticking instead of freezing.

Callers that don't pass ``render_status`` fall back to a centred popup.
"""

from __future__ import annotations

import logging
import threading
import time

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from yeaboi.ui.shared._components import build_popup

logger = logging.getLogger(__name__)

_REC_BORDER = "rgb(80,200,100)"
_WORK_BORDER = "rgb(110,140,220)"
_ERR_BORDER = "rgb(220,80,80)"

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Voice input is triggered by a quick double-tap of the space bar — chosen over a
# Ctrl/Cmd chord because macOS terminals never receive Cmd (so Ctrl was the only
# option and read as ambiguous), and terminals can't detect key-release, ruling
# out true press-and-hold. Space is modifier-free and identical on every keyboard.
_DOUBLE_TAP_SECONDS = 0.30


class DoubleTapSpace:
    """Detects a rapid double-tap of the space bar in a text-entry loop.

    Call :meth:`is_double` on every Space keypress. It returns True when this
    press completes a double-tap within the time window *and* the character
    before the cursor is the space just inserted by the previous tap — in which
    case the caller should delete that space and start recording. Otherwise it
    returns False and the caller inserts the space normally.
    """

    def __init__(self, threshold: float = _DOUBLE_TAP_SECONDS) -> None:
        self._threshold = threshold
        self._last = 0.0

    def is_double(self, prev_char_is_space: bool, now: float) -> bool:
        if prev_char_is_space and 0.0 < (now - self._last) <= self._threshold:
            self._last = 0.0  # reset so a third tap doesn't immediately retrigger
            return True
        self._last = now
        return False


def voice_indicator(status: str, tick: float) -> tuple[str, str]:
    """Return ``(border_style, status_line)`` for an inline recording indicator.

    Callers use ``border_style`` for their input-box border and render
    ``status_line`` in place of the usual submit hint. ``tick`` drives the
    animation (pulsing dot while recording, spinner while transcribing).
    """
    if status == "recording":
        # Triangle-wave pulse (0..1) without importing math — brightens the red.
        p = abs((tick * 1.5 % 1.0) - 0.5) * 2
        r = 200 + int(55 * p)
        gb = 70 + int(40 * p)
        dot = "●" if int(tick * 3) % 2 == 0 else "○"
        return f"rgb({r},{gb},{gb})", f"{dot} Recording…  press any key to stop  ·  Esc cancels"
    if status == "transcribing":
        spin = _SPINNER[int(tick * 12) % len(_SPINNER)]
        return _WORK_BORDER, f"{spin} Transcribing your speech…"
    return "", ""


def _center(console: Console, message: str, border_style: str) -> Group:
    """Fallback overlay: a popup centred over a full screen."""
    _w, h = console.size
    popup = build_popup(message, width=52, border_style=border_style)
    top_pad = max(0, (h - 5) // 2)
    return Group(*[Text("") for _ in range(top_pad)], Align.center(popup))


def record_voice_input(live: Live, console: Console, _key, render_status=None) -> str | None:
    """Record from the mic and return the transcribed text, or None.

    ``render_status(status, tick)`` — optional callback returning a renderable
    for status in {"recording", "transcribing"}; when omitted, a centred popup
    is used. Records until any key is pressed (Esc cancels), transcribes in a
    background thread while animating, and returns the transcript. Returns None
    on cancel, no speech, or error (errors are logged and shown briefly).
    """
    from yeaboi.voice import Recorder, is_model_loaded, is_voice_available, transcribe, voice_install_command

    def _paint(status: str, tick: float) -> None:
        if render_status is not None:
            live.update(render_status(status, tick))
        elif status == "recording":
            live.update(_center(console, "\U0001f3a4  Recording…  press any key to stop", _REC_BORDER))
        else:
            msg = "⏳  Transcribing…" if is_model_loaded() else "⏳  Preparing speech model (first run downloads it)…"
            live.update(_center(console, msg, _WORK_BORDER))

    available, reason = is_voice_available()
    if not available:
        logger.info("Voice input unavailable: %s", reason)
        _flash(live, console, _key, reason, _ERR_BORDER)
        return None

    # Silence any background music so it doesn't bleed into the recording. Resumed
    # the moment recording stops (below), including on the mic-failure path.
    # # See docs: "Music (ffplay)"
    from yeaboi import music

    music.pause_for_voice()

    logger.info("Voice input: starting recording")
    try:
        recorder = Recorder()
    except Exception:
        logger.warning("Failed to start microphone", exc_info=True)
        music.resume_after_voice()
        _flash(live, console, _key, "Could not access microphone", _ERR_BORDER)
        return None

    # ── Recording: animate until any key is pressed ───────────────────────
    cancelled = False
    tick = 0.0
    _paint("recording", tick)
    try:
        while True:
            try:
                key = _key(timeout=0.06)
            except TypeError:
                key = _key()  # key reader without timeout support
            if key == "":
                tick += 0.06
                _paint("recording", tick)
                continue
            cancelled = key == "esc"
            break
    except KeyboardInterrupt:
        cancelled = True

    wav_bytes = recorder.stop()
    music.resume_after_voice()  # recording done — bring music back while we transcribe
    if cancelled:
        logger.info("Voice input: cancelled by user")
        return None
    if not wav_bytes:
        logger.info("Voice input: no audio captured")
        return None

    # ── Transcription: run in a thread so the indicator keeps animating ──
    result: list = [None]
    error: list = [None]
    done = threading.Event()

    def _worker() -> None:
        try:
            result[0] = transcribe(wav_bytes)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user below
            error[0] = exc
        finally:
            done.set()

    threading.Thread(target=_worker, daemon=True).start()
    while not done.is_set():
        _paint("transcribing", tick)
        time.sleep(0.08)
        tick += 0.08

    if error[0] is not None:
        logger.warning("Transcription failed", exc_info=error[0])
        _flash(live, console, _key, f"Transcription failed — see logs (try: {voice_install_command()})", _ERR_BORDER)
        return None

    text = result[0] or ""
    if not text:
        logger.info("Voice input: empty transcript")
        return None

    logger.info("Voice input: inserted %d chars", len(text))
    return text


def _flash(live: Live, console: Console, _key, message: str, border_style: str) -> None:
    """Show a message popup and wait for a keypress to dismiss it."""
    live.update(_center(console, message, border_style))
    try:
        try:
            _key(timeout=3.0)
        except TypeError:
            _key()
    except KeyboardInterrupt:
        pass
