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

# An unanswered offer is a decline. Without a ceiling the modal would hold the
# caller's key loop for as long as the app is open — a user who double-tapped
# Space by accident and walked away should get their text field back.
_OFFER_TIMEOUT_SECONDS = 120.0
_OFFER_POLL_SECONDS = 0.2

# Esc during the offer declines for this process only. People hit Space Space
# repeatedly while composing, and re-asking on every tap is nagging; re-asking
# on the next launch is not.
_offer_declined_session = False

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
# sys.path twice. Dropped by reset_voice_chip() once an in-app install lands.
_chip_cache: tuple[str, str] | None = None


def reset_voice_chip() -> None:
    """Forget the cached availability chip.

    Called by :func:`yeaboi.voice_install.refresh_imports` after an in-app
    install — the voice extra *can* now appear part-way through a process, which
    is exactly what this cache was originally allowed to assume away — and by
    tests that monkeypatch availability.
    """
    global _chip_cache, _offer_declined_session
    _chip_cache = None
    _offer_declined_session = False


def voice_chip() -> tuple[str, str]:
    """Return ``(chip_text, style)`` advertising dictation on an input box.

    Shown whether or not the optional extra is installed — an affordance is UI,
    not a tip, so unlike :func:`_voice_hint` it ignores TIPS_ENABLED. Three
    states, not two: when voice is *installable* the gesture is live (it opens
    the in-app install offer), so the chip keeps the shortcut and only dims. Only
    a machine that cannot run dictation, or a user who declined for good, sees
    "off" — otherwise the chip would deny a feature one keystroke away.
    """
    global _chip_cache
    if _chip_cache is None:
        from yeaboi.voice import voice_state

        state = voice_state()
        if state == "ready":
            _chip_cache = (_CHIP_ON, _CHIP_ON_STYLE)
        elif state == "installable":
            # The gesture works — it opens the install offer — so the chip must
            # not say "off". Dimmer, because it is live but not yet set up. Same
            # cell width as the ready form, so input_box_title's arithmetic and
            # the Settings box-top measurement are both unaffected.
            _chip_cache = (_CHIP_ON, _CHIP_OFF_STYLE)
        else:
            _chip_cache = (_CHIP_OFF, _CHIP_OFF_STYLE)
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


def _bar(fraction: float, cells: int = 10) -> str:
    """A plain-text progress bar for the download line.

    Not :func:`yeaboi.ui.shared._components.build_meter`: that returns a
    two-colour ``Text``, and this line is a single-style plain string threaded
    through every caller's ``render_status``, so the glyphs have to be inline.
    Out-of-range fractions clamp rather than raise — the parent computes them
    from bytes on disk, which can briefly exceed the announced total.
    """
    filled = int(round(min(1.0, max(0.0, fraction)) * cells))
    return "▰" * filled + "▱" * (cells - filled)


def install_offer_line(*, size_mb: int = 0, reinstall: bool = False, width: int = 0) -> str:
    """The one-line "shall I set dictation up?" prompt.

    The size is computed, not hardcoded: ``VOICE_MODEL=large-v3`` is a 3.3 GB
    agreement and a fixed "~140 MB" would be a lie to exactly the user who most
    needs the number. ``reinstall`` is for the case where a ``uv tool upgrade``
    rebuilt the venv and dropped the packages — saying so turns a baffling
    regression into an explained one.
    """
    size = f"~{size_mb} MB" if size_mb else ""
    if reinstall:
        head = "🎤 An upgrade removed dictation"
        return _fit(
            [
                f"{head} — Enter reinstalls ({size})  ·  Esc not now  ·  n never",
                f"{head} — Enter reinstalls  ·  Esc not now  ·  n never",
                f"{head}  ·  Enter  ·  Esc  ·  n never",
                "Dictation was removed  ·  Enter  ·  Esc",
                "Dictation? Enter · Esc",
                "Dictate? Enter · Esc",
            ],
            width,
        )
    return _fit(
        [
            f"🎤 Set up dictation now? {size}, about two minutes  ·  Enter installs  ·  Esc not now  ·  n never",
            f"🎤 Set up dictation? {size} one-off  ·  Enter installs  ·  Esc not now  ·  n never",
            "🎤 Set up dictation?  Enter installs  ·  Esc not now  ·  n never",
            "🎤 Set up dictation?  Enter  ·  Esc  ·  n never",
            "Set up dictation?  Enter  ·  Esc",
            # A 20-column terminal still has to be able to say yes and no.
            "Dictation? Enter · Esc",
            "Dictate? Enter · Esc",
        ],
        width,
    )


def install_status_line(
    stage: str,
    *,
    tick: float = 0.0,
    fraction: float | None = None,
    detail: str = "",
    elapsed: float = 0.0,
    size: str = "",
    can_cancel: bool = True,
    width: int = 0,
) -> tuple[str, str]:
    """Return ``(border_style, status_line)`` for one frame of the setup flow.

    Sibling of :func:`voice_indicator`, and deliberately the same shape: the
    install runs inside ``record_voice_input``'s own loop, so every frame has to
    be expressible as the ``(border, line)`` pair each caller already knows how
    to render. A multi-line installer pane would have meant a new callback
    threaded through six screen builders.

    ``can_cancel`` is False when the caller's key reader cannot poll with a
    timeout — advertising an Esc that physically cannot fire is worse than
    dropping it.
    """
    spin = _SPINNER[int(tick * 12) % len(_SPINNER)]
    esc = "  ·  Esc cancels" if can_cancel else ""
    short_esc = "  ·  Esc" if can_cancel else ""

    if stage == "install":
        head = f"{spin} Installing dictation {_clock(elapsed)}"
        return _WORK_BORDER, _fit(
            [
                f"{head}  ·  {detail}{esc}" if detail else f"{head}{esc}",
                f"{head}{esc}",
                f"{spin} Installing dictation{short_esc}",
                f"{spin} Installing dictation",
                f"{spin} Installing…",
            ],
            width,
        )
    if stage == "download":
        if fraction is None:
            return _WORK_BORDER, _fit(
                [
                    f"⬇ Speech model — {detail or 'connecting'}…{esc}",
                    f"⬇ Speech model…{short_esc}",
                    "⬇ Speech model…",
                ],
                width,
            )
        pct = f"{int(fraction * 100)}%"
        head = f"⬇ Speech model  {_bar(fraction)}  {pct}"
        return _WORK_BORDER, _fit(
            [
                f"{head}  ·  {size}{esc}" if size else f"{head}{esc}",
                f"{head}{esc}",
                f"⬇ Speech model {pct}{short_esc}",
                f"⬇ Speech model {pct}",
            ],
            width,
        )
    if stage == "load":
        return _WORK_BORDER, _fit(
            [
                f"{spin} Loading the speech model…{esc}",
                f"{spin} Loading the speech model…{short_esc}",
                f"{spin} Loading the speech model…",
                f"{spin} Loading the model…",
                f"{spin} Loading…",
            ],
            width,
        )
    # "ready" — one beat of confirmation before the REC frame takes over.
    return _REC_BORDER, _fit(["✓ Dictation is ready — start speaking", "✓ Dictation is ready"], width)


def _status_width(console: Console) -> int:
    """Room the status line actually gets, read fresh each frame.

    Every screen indents the line and draws a page border around it, so the raw
    console width overstates it. Recomputed per frame rather than snapshotted: a
    terminal resized mid-take would otherwise keep fitting the line to a width
    that no longer exists.
    """
    return max(20, console.size[0] - 12)


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


def _key_supports_timeout(_key) -> bool:
    """Can this key reader poll without blocking?

    Production always passes ``read_key``, which can. A test double or a legacy
    caller may not, and a blocking reader would freeze the install animation for
    minutes — so those runs animate on a plain sleep and stop advertising an Esc
    that physically cannot fire.
    """
    import inspect

    try:
        return "timeout" in inspect.signature(_key).parameters
    except (TypeError, ValueError):  # builtins and C callables have no signature
        return False


def _read(_key, timeout: float) -> str:
    """Poll for a key, tolerating readers without timeout support."""
    try:
        return _key(timeout=timeout)
    except TypeError:
        return _key()


def _run_install(live: Live, console: Console, _key, paint, plan) -> tuple[bool, str]:
    """Install the packages, fetch the model, warm it — animating throughout.

    Modelled on the Ollama model pull in ``ui/provider_select`` (worker thread,
    single-key progress dict, ``Esc`` sets a cancel Event): the loop that draws
    must never be the loop that waits on a subprocess. Returns ``(ok, message)``;
    ``message`` is a warning when ``ok`` is True (the model download failed but
    dictation still works, just cold on first use).
    """
    from yeaboi import voice_install
    from yeaboi.config import get_voice_model, mark_voice_extra_installed
    from yeaboi.ui.shared._animations import FRAME_TIME_30FPS
    from yeaboi.ui.shared._music_bar import duck_working_thread
    from yeaboi.ui.shared._screensaver import suppress_screensaver

    size = get_voice_model()
    cancel = threading.Event()
    can_cancel = _key_supports_timeout(_key)
    # Single-key dict writes are atomic under the GIL, so the worker and the
    # render loop need no lock between them.
    prog: dict = {"stage": "install", "detail": "", "fraction": None, "size": ""}
    outcome: dict = {"ok": False, "message": ""}

    def _worker() -> None:
        # duck_working_thread does not catch, so without this an unexpected
        # exception would print a traceback straight through the Rich Live and
        # leave outcome at its default — the user gets "see the log" and the log
        # has nothing in it.
        try:
            _install_and_fetch()
        except Exception:  # noqa: BLE001 - a worker thread must not take the TUI with it
            logger.exception("Voice install failed unexpectedly")
            outcome.update(ok=False, message="Dictation setup hit an unexpected error — see the log")

    def _install_and_fetch() -> None:
        ok, message = voice_install.install_packages(
            lambda phrase: prog.__setitem__("detail", phrase), cancel, plan=plan
        )
        if not ok:
            outcome.update(ok=False, message=message)
            return
        mark_voice_extra_installed()

        prog.update(stage="download", detail="", fraction=None, size="")

        def _on_progress(status: str, fraction: float | None) -> None:
            prog["fraction"] = fraction
            prog["size"] = status

        model_ok, model_message = voice_install.download_model(size, _on_progress, cancel)
        if not model_ok and cancel.is_set():
            outcome.update(ok=False, message=model_message)
            return
        if model_ok:
            # Only warm a model that is already on disk. warm_model() loads it
            # in-process, and WhisperModel downloads the weights itself when
            # they are missing — with no progress, no byte count and no cancel,
            # under a status line still offering Esc. It would also undo the
            # whole point of running the fetch in a child: on an AVX-less host
            # the child dies of SIGILL, and importing ctranslate2 here would
            # then take the TUI down with the same instruction.
            prog["stage"] = "load"
            voice_install.warm_model(size)
        # A failed model fetch is a warning, not a failure: the packages are in,
        # so the model simply downloads lazily on the first dictation as before.
        outcome.update(ok=True, message="" if model_ok else model_message)

    started = time.monotonic()
    tick = 0.0
    with suppress_screensaver():
        thread = duck_working_thread(_worker, name="voice-install")
        thread.start()
        while thread.is_alive():
            # Nothing reads the cancel Event during "load" — it is an in-process
            # model load, not a child — so the line must stop offering Esc there
            # rather than ignoring it.
            stage = prog["stage"]
            stage_cancellable = can_cancel and stage != "load"
            paint(
                *install_status_line(
                    stage,
                    tick=tick,
                    fraction=prog["fraction"],
                    detail=prog["detail"],
                    size=prog["size"],
                    elapsed=time.monotonic() - started,
                    can_cancel=stage_cancellable,
                    width=_status_width(console),
                )
            )
            tick += FRAME_TIME_30FPS
            frame_started = time.monotonic()
            if can_cancel:
                try:
                    if _read(_key, FRAME_TIME_30FPS) == "esc":
                        cancel.set()
                except KeyboardInterrupt:
                    cancel.set()
            # Floor the frame time rather than trusting the key reader to supply
            # it. A reader that returns early — a non-blocking stub, a terminal
            # replaying buffered input — would otherwise spin this loop flat out
            # for the whole install, burning a core and calling live.update
            # millions of times. Measured on a stub reader: 2.1M frames in 44s.
            remaining = FRAME_TIME_30FPS - (time.monotonic() - frame_started)
            if remaining > 0:
                time.sleep(remaining)
        thread.join(timeout=1.0)

    return outcome["ok"], outcome["message"]


def _offer_install(live: Live, console: Console, _key, paint, reason: str) -> bool:
    """Offer to set dictation up here and now. True if it is ready afterwards.

    This is what a double-tap of Space does when the optional packages are
    missing. Previously the app printed a command for the user to run in another
    terminal, which meant quitting mid-thought, reinstalling, and coming back —
    so in practice dictation stayed off.

    Key handling is deliberately narrow. ``Space`` is ignored: the user just
    double-tapped it, and key repeat must not authorise a 300 MB install.
    ``Tab`` means "next microphone" everywhere else and there is no microphone
    yet. Clicks and unknown keys are no-ops, because a modal that vanishes on a
    typo is worse than one that waits — and all three exits are named on screen.
    """
    global _offer_declined_session

    from yeaboi import voice, voice_install
    from yeaboi.config import set_voice_install_offer, voice_extra_was_installed
    from yeaboi.ui.shared._click import parse_click

    state = voice.voice_state()
    if state == "unsupported":
        # Covers both "no wheel for this machine" and "packages are in, the
        # audio backend is dead" — the latter carries its own apt hint, and an
        # install could not have helped with either.
        _flash(live, console, _key, f"Dictation can't run here — {voice.unsupported_blocker()}", _ERR_BORDER)
        return False
    if state == "declined":
        # They said "never". Honour it, and still show the manual command.
        _flash(live, console, _key, reason, _ERR_BORDER)
        return False
    if _offer_declined_session:
        _flash(
            live,
            console,
            _key,
            "Dictation isn't set up yet — Space Space, then Enter, installs it",
            _WORK_BORDER,
        )
        return False

    plan = voice_install.install_plan()
    if plan.blocked:
        logger.info("Voice install offer withheld: %s", plan.blocked)
        _flash(live, console, _key, f"Dictation can't be installed here — {plan.blocked}", _ERR_BORDER)
        return False

    size_mb = voice_install.size_estimate_mb()
    reinstall = voice_extra_was_installed()
    deadline = time.monotonic() + _OFFER_TIMEOUT_SECONDS
    accepted = False
    while time.monotonic() < deadline:
        paint(
            _REC_BORDER,
            install_offer_line(size_mb=size_mb, reinstall=reinstall, width=_status_width(console)),
        )
        polled_at = time.monotonic()
        try:
            key = _read(_key, _OFFER_POLL_SECONDS)
        except KeyboardInterrupt:
            key = "esc"
        # Same floor as the install loop: a key reader that returns early must
        # not turn a two-minute unanswered prompt into a two-minute busy-wait.
        # Applied to every key that does not exit the loop, not just the empty
        # one — bracketed paste hands over characters as fast as the reader pops
        # them, so a pasted paragraph would otherwise be one full live.update
        # per character with no ceiling. The three answering keys skip it, so
        # Enter still feels instant.
        idle = _OFFER_POLL_SECONDS - (time.monotonic() - polled_at)
        if key not in {"enter", "\r", "\n", "esc", "n"} and idle > 0:
            time.sleep(idle)
        if key in {"enter", "\r", "\n"}:
            accepted = True
            break
        if key == "esc":
            break
        if key == "n":
            logger.info("Voice install offer declined permanently")
            set_voice_install_offer(False)
            return False
        if parse_click(key) is not None or key in {"", " ", "tab"}:
            continue
        # Anything else: keep the offer up rather than dismissing on a typo.

    if not accepted:
        logger.info("Voice install offer declined for this session")
        _offer_declined_session = True
        return False

    logger.info("Voice install accepted: %s", plan.display_command)
    ok, message = _run_install(live, console, _key, paint, plan)
    if not ok:
        _flash(live, console, _key, message or "Dictation setup failed — see the log", _ERR_BORDER)
        return False
    if message:
        logger.info("Voice install finished with a warning: %s", message)
    paint(*install_status_line("ready", width=_status_width(console)))
    # A self-dismissing beat, not _flash: _flash waits for a key, which would
    # make the user press something before they could start speaking.
    time.sleep(0.6)
    return True


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
        probe_voice_backend,
        resolve_device,
        transcribe,
        voice_install_command,
    )

    def _paint(border: str, line: str) -> None:
        if render_status is not None:
            live.update(render_status(border, line))
        else:
            live.update(_center(console, line, border))

    # The strict probe, not the per-frame one: this is the moment a ~100 ms
    # PortAudio check is invisible and an honest error is worth having.
    available, reason = probe_voice_backend()
    if not available:
        logger.info("Voice input unavailable: %s", reason)
        if not _offer_install(live, console, _key, _paint, reason):
            return None
        available, reason = probe_voice_backend(force=True)
        if not available:
            # Installed, but this interpreter still cannot see or open it.
            logger.warning("Voice still unavailable after an in-app install: %s", reason)
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
        """Room this screen gives the status line — see :func:`_status_width`."""
        return _status_width(console)

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
