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

import rich.box
from rich.align import Align
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.shared._mascot import render_full, render_head, walk_cells

IDLE_SECONDS = 5 * 60

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


# One blank row under his feet so they rest just above the bottom border. The music
# pocket is drawn with preserve_content, so it no longer blanks the rows he's on.
_SAVER_FOOT_RESERVE = 1
_SAVER_DUCK_W = 34  # full duck trace width
_SAVER_JUMP_H = 4  # how many rows he springs up as he reaches the music tab


def build_screensaver(*, width: int, height: int, elapsed: float | None = None) -> RenderableType:
    """Build a size-aware animated saver frame without mutating app content."""
    elapsed = idle_controller.animation_elapsed() if elapsed is None else elapsed
    frame = int(elapsed * 8) % 8

    # Roomy terminals: the duck waddles back and forth along the floor (feet
    # stepping) rather than standing still in the centre. Needs room for the 18-row
    # duck + the caption/hint + the pocket-clearance reserve.
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
        # Spring up as he nears the music tab at the right end (the last ~40% of the
        # leg). While airborne the sunglasses bob; on the ground they hold still.
        jump = int(_SAVER_JUMP_H * max(0.0, (travel - 0.6) / 0.4))
        glasses_frame = frame if jump > 0 else 0
        grid = walk_cells(frame, foot=foot, glasses_frame=glasses_frame, flip=facing_left)
        duck_rows = [_cells_to_text(r, x) for r in grid]

        caption = Text("YEABOI · chilling", style="bold rgb(105,220,235)", justify="center")
        hint = Text("press any key", style="rgb(95,105,115)", justify="center")
        below = _SAVER_FOOT_RESERVE + jump  # blank rows under him → raises him mid-jump
        above = max(0, content_h - len(duck_rows) - below)
        cap_top = max(0, (above - 2) // 2)
        rows: list[RenderableType] = [Text("") for _ in range(cap_top)]
        if above >= 2:
            rows += [caption, hint]
        rows += [Text("") for _ in range(max(0, above - cap_top - 2))]
        rows += duck_rows
        rows += [Text("") for _ in range(below)]
        return Panel(
            Group(*rows),
            border_style="white",
            box=rich.box.ROUNDED,
            height=max(1, height),
            padding=(0, 2),
        )

    # Thresholds account for the surrounding Panel: the full duck is 18 half-block
    # rows + caption + hint = 20, plus 2 border rows = 22. Between 22 and the walk
    # threshold he stands centred.
    if width >= 46 and height >= 22:
        art: RenderableType | None = render_full(frame)
    elif width >= 22 and height >= 13:
        art = render_head(frame)
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
        caption = Text("YEABOI · chilling", style="bold rgb(105,220,235)", justify="center")
        hint = Text("press any key", style="rgb(95,105,115)", justify="center")
        inner = Group(art, caption, hint)

    # Wrap in the app's rounded Panel so the border stays put when the saver takes
    # over the screen — otherwise the frame vanishes on idle. Border-only chrome
    # (no vertical padding) so the duck keeps as much height as possible.
    pad = (0, 2) if width >= 8 else (0, 0)
    return Panel(
        Align.center(inner, vertical="middle"),
        border_style="white",
        box=rich.box.ROUNDED,
        height=max(1, height),
        padding=pad,
    )
