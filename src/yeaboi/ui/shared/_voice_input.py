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
from rich.cells import cell_len
from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from yeaboi.ui.shared._components import build_popup

logger = logging.getLogger(__name__)

# Amber. The border used to be green while the *pulse* animated through red,
# which put the recording state within a few points of _ERR_BORDER below — a
# composer that flushed red read as "something crashed" rather than "I am
# listening". Amber is unambiguous against both the error red and the working
# blue, and the pulse now stays inside that hue instead of crossing into it.
_REC_BORDER = "rgb(240,165,70)"
_WORK_BORDER = "rgb(110,140,220)"
_ERR_BORDER = "rgb(220,80,80)"

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_METER_CELLS = 8
# A peak below this counts as silence. Room-tone on a live mic sits well under
# it; anything you actually say clears it easily.
_SILENCE_LEVEL = 0.02
# How long the meter must stay flat before we say so. Long enough not to fire in
# the pause before someone starts talking.
_SILENCE_SECONDS = 2.5

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


# The chip lives in the input box\'s title, which is the only part of an input
# screen that is never cropped — the hint line below the box is rendered
# no_wrap/ellipsis, so on an 80-column terminal the dictation hint at its tail
# was cut off entirely and the feature looked like it did not exist.
_CHIP_ON = "🎤 Space Space"
_CHIP_OFF = "🎤 off"
_CHIP_ON_STYLE = "rgb(110,110,125)"
_CHIP_OFF_STYLE = "rgb(80,80,92)"

# Memoised: titles are rebuilt every frame and is_voice_available() walks
# sys.path twice. Tests that monkeypatch availability call reset_voice_chip().
_chip_cache: tuple[str, str] | None = None


def reset_voice_chip() -> None:
    """Forget the cached availability chip.

    Called by tests that monkeypatch availability. Nothing in production calls
    it: the voice extra cannot appear part-way through a process, so the cache
    is valid for the life of the run.
    """
    global _chip_cache
    _chip_cache = None


def voice_chip() -> tuple[str, str]:
    """Return ``(chip_text, style)`` advertising dictation on an input box.

    Shown whether or not the optional extra is installed — an affordance is UI,
    not a tip, so unlike :func:`_voice_hint` it ignores TIPS_ENABLED. The "off"
    form is what tells someone the feature exists at all.
    """
    global _chip_cache
    if _chip_cache is None:
        from yeaboi.voice import is_voice_available

        available, _reason = is_voice_available()
        _chip_cache = (_CHIP_ON, _CHIP_ON_STYLE) if available else (_CHIP_OFF, _CHIP_OFF_STYLE)
    return _chip_cache


def input_box_title(label: str, box_width: int = 0) -> Text:
    """Panel title for a text-input box: ``label`` plus the dictation chip.

    ``box_width`` is the box\'s declared width. Rich grows a Panel *past* its
    declared width when the title is wider than the body (panel.py), and these
    boxes are padded into a fixed column — so a title that would not fit drops
    the chip rather than pushing the border off the page. Rich would otherwise
    hard-cut it with no ellipsis.
    """
    plain = Text(f" {label} ")
    chip, style = voice_chip()
    candidate = Text(f" {label}  ")
    candidate.append(chip, style=style)
    candidate.append(" ")
    # +4: Rich pads the title a cell either side, then reserves two more before
    # it starts widening the panel.
    if box_width and candidate.cell_len + 4 > box_width:
        return plain
    return candidate


def level_meter(level: float) -> str:
    """An 8-cell input-level bar for a 0..1 peak amplitude.

    Public because the Settings mic picker draws the same bar; a sibling module
    reaching for an underscore-private is the coupling this name avoids. Kept
    here rather than beside ``build_meter`` in ``_components`` because that one
    is a *progress* bar (▰/▱, a value out of a total) — this is a live signal
    level, square-rooted because speech peaks land around 0.1–0.4 on a healthy
    mic and a linear bar would barely move, reading as a dead microphone.
    """
    filled = int(round(min(1.0, max(0.0, level) ** 0.5) * _METER_CELLS))
    return "▇" * filled + "▁" * (_METER_CELLS - filled)


def _clock(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def _fit(candidates: list[str], width: int) -> str:
    """First candidate that fits ``width``, else the shortest one.

    The status line is rendered no_wrap/ellipsis by every screen that shows it,
    so an over-long line loses its *tail* — which is where "press any key to
    stop" lives. Dropping a part deliberately beats being cropped arbitrarily.
    """
    if not width:
        return candidates[0]
    for candidate in candidates:
        if cell_len(candidate) <= width:
            return candidate
    return candidates[-1]


def voice_indicator(
    status: str,
    tick: float,
    *,
    level: float = 0.0,
    elapsed: float = 0.0,
    device: str = "",
    silent: bool = False,
    preparing: bool = False,
    width: int = 0,
) -> tuple[str, str]:
    """Return ``(border_style, status_line)`` for an inline recording indicator.

    ``record_voice_input`` calls this and hands the pair to its caller — screens
    never compute it themselves, because only the recording loop knows the live
    level, the elapsed time and which microphone actually opened.

    ``tick`` drives the animation (pulsing dot and border while recording, a
    spinner while transcribing). ``width`` is the space the line has; :func:`_fit`
    picks the longest form that fits rather than letting the caller\'s ellipsis
    eat the "any key to stop" that ends it.
    """
    if status == "recording":
        # Triangle-wave pulse (0..1) without importing math — brightens the amber.
        p = abs((tick * 1.5 % 1.0) - 0.5) * 2
        r = 225 + int(30 * p)
        g = 150 + int(35 * p)
        b = 60 + int(35 * p)
        dot = "●" if int(tick * 3) % 2 == 0 else "○"
        head = f"{dot} REC {_clock(elapsed)}  {level_meter(level)}"
        border = f"rgb({r},{g},{b})"
        if silent:
            # The meter is flat: say which mic is quiet and what to do about it.
            quiet = f"no sound from {device}" if device else "no sound reaching the mic"
            # Every candidate keeps a way out. This is the branch a confused
            # user actually lands on, so dropping "Esc" here — while the
            # non-silent branch below preserves it down to its narrowest form —
            # would strand exactly the person who most needs it.
            return border, _fit(
                [
                    f"{head}  {quiet} — Tab tries the next mic  ·  Esc cancels",
                    f"{head}  no sound — Tab tries the next mic  ·  Esc cancels",
                    f"{head}  no sound  ·  Tab next mic  ·  Esc",
                    f"{head}  no sound  ·  Esc",
                ],
                width,
            )
        named = f"{head}  {device}" if device else head
        return border, _fit(
            [
                f"{named}  ·  any key to stop  ·  Tab switch mic  ·  Esc cancels",
                f"{head}  ·  any key to stop  ·  Tab switch mic  ·  Esc cancels",
                f"{head}  ·  any key to stop  ·  Esc cancels",
                f"{head}  any key stops  ·  Esc",
                # Last resort on a terminal narrower than the app supports: the
                # head alone still says it is recording, and for how long.
                head,
            ],
            width,
        )
    if status == "transcribing":
        spin = _SPINNER[int(tick * 12) % len(_SPINNER)]
        if preparing:
            return _WORK_BORDER, f"{spin} Preparing the speech model (first run downloads it)…"
        return _WORK_BORDER, f"{spin} Transcribing your speech…"
    return "", ""


def _center(console: Console, message: str, border_style: str) -> Group:
    """Fallback overlay: a popup centred over a full screen.

    Sized to the message rather than a fixed 52 — the recording line and the real
    PortAudio error strings are both longer than that, and a popup that wraps
    also breaks the 5-row assumption the vertical centring below depends on.
    """
    w, h = console.size
    width = max(52, min(w - 8, len(message) + 8))
    popup = build_popup(message, width=width, border_style=border_style)
    top_pad = max(0, (h - 5) // 2)
    return Group(*[Text("") for _ in range(top_pad)], Align.center(popup))


def _next_device(current: int | None) -> tuple[int | None, str] | None:
    """The input device after ``current`` in the host\'s list, or None if there is
    only one to choose from.

    ``current`` is ``None`` whenever VOICE_DEVICE is unset — i.e. the default
    state, and the state a user is in when the silence warning tells them to
    press Tab. That must resolve to the *system default's own index* first: with
    a plain "not found → start at the top", the first Tab lands on ``devices[0]``,
    which is usually the system default itself. Switching restarts the take, so
    the one remedy the feature advertises would have cost the user their
    recording to move to the microphone they were already on.
    """
    from yeaboi.voice import list_input_devices

    devices = list_input_devices()
    if len(devices) < 2:
        return None
    indices = [d["index"] for d in devices]
    if current is None:
        current = next((d["index"] for d in devices if d["is_default"]), None)
    try:
        position = indices.index(current)
    except ValueError:  # no default reported either — start at the top
        position = -1
    chosen = devices[(position + 1) % len(devices)]
    return chosen["index"], chosen["name"]


def record_voice_input(live: Live, console: Console, _key, render_status=None) -> str | None:
    """Record from the mic and return the transcribed text, or None.

    ``render_status(border, line)`` — optional callback returning a renderable
    for the caller\'s own screen, given a border style for its input box and a
    status line to show in place of the usual submit hint. When omitted, a
    centred popup is used instead. The loop owns the whole indicator (level,
    elapsed time, device name, silence warning) and hands the caller a finished
    pair, because none of that state exists on the screen side.

    Records until any key is pressed (Esc cancels, Tab switches microphone),
    transcribes in a background thread while animating, and returns the
    transcript. Returns None on cancel, no speech, or error (errors are logged
    and shown briefly).
    """
    from yeaboi.voice import (
        Recorder,
        is_model_loaded,
        is_voice_available,
        resolve_device,
        transcribe,
        voice_install_command,
    )

    def _paint(border: str, line: str) -> None:
        if render_status is not None:
            live.update(render_status(border, line))
        else:
            live.update(_center(console, line, border))

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
    device = resolve_device()
    try:
        recorder = Recorder(device=device)
    except Exception as exc:  # noqa: BLE001 - reported to the user below
        logger.warning("Failed to start microphone", exc_info=True)
        music.resume_after_voice()
        # Show the *real* reason. "Could not access microphone" was the same
        # message whether the mic was busy, denied by the OS, or simply refusing
        # the format — none of which the user could act on.
        _flash(live, console, _key, _mic_error(exc), _ERR_BORDER)
        return None

    # ── Recording: animate until any key is pressed ───────────────────────
    cancelled = False
    tick = 0.0
    started = time.monotonic()
    heard_at = started  # last moment the meter showed sound
    warned = False  # the silence warning is logged once, never per frame

    def _width() -> int:
        """Room the status line actually gets, read fresh each frame.

        Every screen indents the line and draws a page border around it, so the
        raw console width overstates it. Recomputed per frame rather than
        snapshotted: a terminal resized mid-take would otherwise keep fitting
        the line to a width that no longer exists.
        """
        return max(20, console.size[0] - 12)

    def _frame() -> None:
        level = recorder.level()
        silent = (time.monotonic() - heard_at) >= _SILENCE_SECONDS
        border, line = voice_indicator(
            "recording",
            tick,
            level=level,
            elapsed=time.monotonic() - started,
            device=recorder.device_name,
            silent=silent,
            width=_width(),
        )
        _paint(border, line)

    _frame()
    try:
        while True:
            try:
                key = _key(timeout=0.06)
            except TypeError:
                key = _key()  # key reader without timeout support
            if key == "":
                tick += 0.06
                if recorder.level() > _SILENCE_LEVEL:
                    heard_at = time.monotonic()
                elif not warned and (time.monotonic() - heard_at) >= _SILENCE_SECONDS:
                    logger.warning("Voice input: no audio level from %s", recorder.device_name)
                    warned = True
                _frame()
                continue
            if key == "tab":
                # Switch microphone mid-take. The take restarts: devices can
                # negotiate different sample rates, so the frames captured so far
                # cannot be concatenated with what the next one produces.
                nxt = _next_device(device)
                if nxt is None:
                    continue
                previous = device
                recorder.stop()
                device, name = nxt
                logger.info("Voice input: switching microphone to %s (index %s)", name, device)
                try:
                    recorder = Recorder(device=device)
                except Exception:  # noqa: BLE001 - recovered by reopening the previous mic
                    # Actually fall back: a mic that is busy or unplugged must not
                    # cost the user the session. The take is gone either way (the
                    # old stream is closed above), but they keep a working mic and
                    # can carry on speaking rather than being dropped back to the
                    # screen with nothing.
                    logger.warning("Could not switch to microphone %s; reopening previous", name, exc_info=True)
                    device = previous
                    try:
                        recorder = Recorder(device=device)
                    except Exception:  # noqa: BLE001 - both mics gone; nothing left to record with
                        logger.warning("Could not reopen the previous microphone either", exc_info=True)
                        music.resume_after_voice()
                        _flash(live, console, _key, f"Could not switch to {name}", _ERR_BORDER)
                        return None
                started = heard_at = time.monotonic()
                warned = False
                _frame()
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
    preparing = not is_model_loaded()
    while not done.is_set():
        _paint(*voice_indicator("transcribing", tick, preparing=preparing, width=_width()))
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


def _mic_error(exc: Exception) -> str:
    """Actionable one-liner for a microphone that would not open."""
    from yeaboi.voice import device_name, resolve_device

    detail = str(exc).strip() or exc.__class__.__name__
    return f"Mic '{device_name(resolve_device())}' would not start — {detail}. Pick another in Settings → Voice Input."


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
