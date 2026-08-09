"""Application-wide idle tracking and animated Yeaboi screensaver.

The TUI is made of many small Rich frame loops.  Keeping the idle state here
lets the shared input reader say when the application is waiting for a person,
while the shared Live wrapper decides which renderable should be visible.
"""

from __future__ import annotations

import functools
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import ParamSpec, TypeVar

from rich.align import Align
from rich.console import Group, RenderableType
from rich.text import Text

from yeaboi.ui.shared._components import build_page_panel
from yeaboi.ui.shared._mascot import SHADES_LIFT_SEQUENCE, render_full, render_head_idle, walk_cells

IDLE_SECONDS = 5 * 60

# build_screensaver advances the sprite at this rate; `frame` below is derived
# from it. Named because the shades gag has to land on the same grid — a lift
# sequence stepping at a different rate than the bob reads as two animations.
SAVER_FPS = 8
# How often the idle head lifts its shades. The mode-select companion plays the
# same double-shades gag on click; here it is on a timer, because nobody is
# there to click — that is what idle means.
#
# Anything recording this as a loop wants a whole number of these: the bob
# closes its cycle every second, but the clip only wraps invisibly on a
# multiple of the gag period.
SHADES_EVERY_SECONDS = 4.0

_P = ParamSpec("_P")
_R = TypeVar("_R")


class IdleController:
    """Thread-safe idle state shared by terminal input and Rich's refresh thread."""

    def __init__(self, *, idle_seconds: float = IDLE_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self.idle_seconds = idle_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._last_activity = clock()
        self._animation_started = self._last_activity
        self._waiting_for_input = False
        self._suppression_depth = 0
        self._active = False

    def begin_input_wait(self) -> None:
        """Declare that the current screen is ready for user input.

        Repeated timed polls keep the original baseline.  Transitioning back
        from processing starts a fresh idle period so work time never counts.
        """
        now = self._clock()
        with self._lock:
            if self._suppression_depth:
                return
            if not self._waiting_for_input:
                self._last_activity = now
            self._waiting_for_input = True

    def handle_input_event(self) -> bool:
        """Record a real terminal event; return True when it is a wake-only event."""
        now = self._clock()
        with self._lock:
            self._last_activity = now
            if self._active:
                self._active = False
                self._animation_started = now
                # The wake key is swallowed, so the screen remains in its input wait.
                self._waiting_for_input = True
                return True
            # The caller is about to act on the key.  Its next read starts a new
            # waiting interval; any processing in between is therefore excluded.
            self._waiting_for_input = False
            return False

    def is_active(self) -> bool:
        """Whether the saver is on screen. Unlike handle_input_event this only
        asks — it does not wake anything — so a key that is allowed to work
        *through* the saver can check first."""
        with self._lock:
            return self._active

    def should_show(self) -> bool:
        """Return whether the saver should replace the current renderable."""
        now = self._clock()
        with self._lock:
            if self._suppression_depth or not self._waiting_for_input:
                self._active = False
                return False
            if not self._active and now - self._last_activity >= self.idle_seconds:
                self._active = True
                self._animation_started = now
            return self._active

    def animation_elapsed(self) -> float:
        with self._lock:
            return max(0.0, self._clock() - self._animation_started)

    def show_now(self) -> bool:
        """Activate immediately for the hidden preview shortcut.

        Returns False while processing is suppressing the saver.
        """
        now = self._clock()
        with self._lock:
            if self._suppression_depth:
                return False
            self._waiting_for_input = True
            self._active = True
            self._animation_started = now
            return True

    def push_suppression(self) -> None:
        with self._lock:
            self._suppression_depth += 1
            self._waiting_for_input = False
            self._active = False

    def pop_suppression(self) -> None:
        now = self._clock()
        with self._lock:
            self._suppression_depth = max(0, self._suppression_depth - 1)
            if self._suppression_depth == 0:
                self._last_activity = now
                self._waiting_for_input = False
                self._active = False


idle_controller = IdleController()


def begin_input_wait() -> None:
    idle_controller.begin_input_wait()


def handle_input_event() -> bool:
    return idle_controller.handle_input_event()


def screensaver_active() -> bool:
    return idle_controller.is_active()


def show_screensaver_now() -> bool:
    return idle_controller.show_now()


@contextmanager
def suppress_screensaver() -> Iterator[None]:
    """Exclude a worker/agent operation from idle tracking."""
    idle_controller.push_suppression()
    try:
        yield
    finally:
        idle_controller.pop_suppression()


def suppress_during_call(fn: Callable[_P, _R]) -> Callable[_P, _R]:
    """Decorator form of :func:`suppress_screensaver` for processing helpers."""

    @functools.wraps(fn)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with suppress_screensaver():
            return fn(*args, **kwargs)

    return wrapped


def _cells_to_text(row: list[tuple[str, str | None]], left_pad: int) -> Text:
    """Render one sprite cell-row to a Text, shifted right by ``left_pad`` columns —
    for compositing the duck at a moving x along the saver floor."""
    t = Text()
    if left_pad > 0:
        t.append(" " * left_pad)
    for glyph, style in row:
        t.append(glyph, style=style)
    return t


# No blank rows under his feet — they rest right on the bottom border. The music
# pocket is drawn with preserve_content, so it no longer blanks the rows he's on.
_SAVER_FOOT_RESERVE = 0
_SAVER_DUCK_W = 34  # full duck trace width
_SAVER_JUMP_H = 2  # rows he springs up onto bar level as he reaches the music tab
_SAVER_JUMP_HALF = 0.06  # half-width of the hop as a fraction of the walk period


def _shades_lift(elapsed: float) -> int | None:
    """Where the sunglasses are in the periodic gag, or None while resting.

    Steps on the same 8-per-second grid as the bob, so the two never drift
    against each other.
    """
    period = int(SHADES_EVERY_SECONDS * SAVER_FPS)
    step = int(elapsed * SAVER_FPS) % period
    # The gag sits at the *end* of each period, so the saver opens on the resting
    # bob rather than halfway through a lift — which is what it would do at
    # elapsed 0, and what a recorded loop would open on.
    start = period - len(SHADES_LIFT_SEQUENCE)
    return SHADES_LIFT_SEQUENCE[step - start] if step >= start else None


def build_screensaver(*, width: int, height: int, elapsed: float | None = None) -> RenderableType:
    """Build a size-aware animated saver frame without mutating app content."""
    elapsed = idle_controller.animation_elapsed() if elapsed is None else elapsed
    frame = int(elapsed * SAVER_FPS) % 8

    # The saver wears whichever mascot the page it interrupted wears — idling on
    # an Agents page keeps the robo (lazy import: _music_bar imports this module).
    from yeaboi.ui.shared._music_bar import current_chrome_mascot

    mascot = current_chrome_mascot()

    # Roomy terminals: the duck waddles back and forth along the floor (feet
    # stepping) rather than standing still in the centre. Needs room for the 18-row
    # duck + the caption/hint + the pocket-clearance reserve.
    # A yard of ducks, for any terminal with room to swing them. Below this the
    # crowd has nowhere to go and it reads as a jam rather than mayhem, so the
    # older single-duck bands still handle the small end — and so does the whole
    # roomy band when the style preference asks for the original (Settings →
    # System → Advanced → Screensaver).
    from yeaboi.config import screensaver_style

    if width >= 60 and height >= 24 and screensaver_style() == "ducks":
        from yeaboi.ui.shared._mayhem import render as render_mayhem

        return build_page_panel(
            render_mayhem(width - 6, height - 2, elapsed, mascot=mascot), height=max(1, height), padding=(0, 2)
        )

    if width >= 46 and height >= 26:
        content_h = max(1, height - 2)  # inside the border
        inner_w = width - 6  # borders (2) + horizontal padding (4)
        span = max(1, inner_w - _SAVER_DUCK_W)
        speed = 14.0  # columns per second — a brisk waddle
        period = max(0.1, 2.0 * span / speed)
        phase = (elapsed % period) / period  # 0 → 1 over a there-and-back trip
        travel = phase * 2.0 if phase < 0.5 else (1.0 - phase) * 2.0  # 0 → 1 → 0
        x = int(travel * span)
        facing_left = phase >= 0.5  # heading left on the return leg → mirror him
        foot = int(elapsed * 3.0)  # step cadence, decoupled from the fast wing frame
        # Spring up onto bar level the moment his leading edge reaches the music tab
        # and hold there while he's over it, dropping back as he walks off — so the
        # jump lands right when he meets the tab, not late at the far turnaround.
        # While airborne the sunglasses bob; on the ground they hold still.
        from yeaboi.ui.shared._music_bar import build_music_subtitle

        tab_w = build_music_subtitle().cell_len + 4  # music alcove width
        tab_left = inner_w - tab_w  # content column where the tab begins
        over = (x + _SAVER_DUCK_W) - tab_left  # how far his right edge is over the tab
        jump = int(round(_SAVER_JUMP_H * max(0.0, min(1.0, over / 4.0))))  # ramp over 4 cols
        glasses_frame = frame if jump > 0 else 0
        grid = walk_cells(frame, foot=foot, glasses_frame=glasses_frame, flip=facing_left, mascot=mascot)
        duck_rows = [_cells_to_text(r, x) for r in grid]

        # No caption/hint — just the duck walking along the floor.
        below = _SAVER_FOOT_RESERVE + jump  # blank rows under him → raises him mid-jump
        duck_top = max(0, content_h - len(duck_rows) - below)  # rises as he jumps
        rows: list[RenderableType] = [Text("") for _ in range(duck_top)]
        rows += duck_rows
        rows += [Text("") for _ in range(below)]
        # build_page_panel (main #104) gives the rounded white border + neutral
        # base tint so the saver never shows the terminal's own background.
        return build_page_panel(Group(*rows), height=max(1, height), padding=(0, 2))

    # Thresholds account for the surrounding Panel: the full duck is 18 half-block
    # rows plus 2 border rows. Between this and the walk threshold he stands centred.
    if width >= 46 and height >= 22:
        art: RenderableType | None = render_full(frame, mascot=mascot)
    elif width >= 22 and height >= 13:
        # render_head_idle's lift is duck-only internally, so passing the
        # shades clock is safe for the robo (he just rests).
        art = render_head_idle(frame, _shades_lift(elapsed), mascot=mascot)
    else:
        art = None

    if art is None:
        if width >= 20:
            label = "<(o )___ YEABOI"
        elif width >= 12:
            label = "<(o )_ YEABOI"
        else:
            label = "YEABOI"[:width]
        inner: RenderableType = Text(label, style="bold rgb(42,170,105)")
    else:
        inner = art  # no caption/hint — just the duck

    # Wrap in the app's rounded Panel so the border stays put when the saver takes
    # over the screen — otherwise the frame vanishes on idle. Border-only chrome
    # (no vertical padding) so the duck keeps as much height as possible. The
    # neutral base tint (main #104) keeps the saver off the terminal background.
    pad = (0, 2) if width >= 8 else (0, 0)
    return build_page_panel(Align.center(inner, vertical="middle"), height=max(1, height), padding=pad)
