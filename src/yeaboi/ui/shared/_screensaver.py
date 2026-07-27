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

from yeaboi.ui.shared._components import NEUTRAL_BG

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


_PALETTE = {
    "K": "rgb(3,8,11)",  # outline / sunglasses frame
    "S": "rgb(22,31,37)",  # subtly lighter sunglasses lenses
    "G": "rgb(42,170,105)",  # mallard green
    "g": "rgb(30,125,112)",  # teal shadow
    "W": "rgb(225,245,241)",  # wing / glasses glint
    "C": "rgb(105,220,235)",  # cool highlight
    "B": "rgb(132,184,186)",  # blue-grey body
    "b": "rgb(74,139,145)",  # body shadow
    "O": "rgb(255,165,15)",  # bill / feet
    "R": "rgb(235,65,12)",  # warm orange shadow
    "D": "rgb(55,58,72)",  # ground shadow
}

# Each source cell is rendered as a two-column pixel, giving the full duck a
# deliberately chunky ANSI/pixel-art silhouette close to the Yeaboi icon.
_FULL_DUCK = (
    "........KKKKK..........",
    "......KKGGGGGKK.........",
    ".....KGGGGGGGGGK........",
    "....KGGKKKKKKKKGGK......",
    "...KGGKSSKKSSKGGGK......",
    "OOKGGGKSWKKSWKGGGGK.....",
    "OOOOOKGKKKKKKKKGGGGK....",
    ".RRROKGGGGGGGGGGGGK.....",
    ".....KGGGWWWWWWGGGKKK...",
    ".....KGGWWCWWWWWGGGGGK..",
    "......KGGGGggGGGGGGGGK..",
    ".......KKKKKKKKKKKKKKK..",
    "..........O.....O........",
    ".........OOO...OOO.......",
)

_COMPACT_DUCK = (
    "...KKK.....",
    "..KGGGKK...",
    ".KGKKKGGK..",
    "OKKSKSKGGK.",
    "OOOKKKGGGK.",
    "..KGWWWGKK.",
    "...KGGGGGK.",
    "....KKKKK..",
    ".....O.O...",
)


def _fill_ellipse(canvas: list[list[str | None]], cx: int, cy: int, rx: int, ry: int, color: str) -> None:
    for y in range(max(0, cy - ry), min(len(canvas), cy + ry + 1)):
        for x in range(max(0, cx - rx), min(len(canvas[0]), cx + rx + 1)):
            if ((x - cx) / max(rx, 1)) ** 2 + ((y - cy) / max(ry, 1)) ** 2 <= 1:
                canvas[y][x] = color


def _inside_polygon(x: float, y: float, points: tuple[tuple[int, int], ...]) -> bool:
    inside = False
    previous = points[-1]
    for current in points:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing:
                inside = not inside
        previous = current
    return inside


def _fill_polygon(canvas: list[list[str | None]], points: tuple[tuple[int, int], ...], color: str) -> None:
    min_x = max(0, min(x for x, _ in points))
    max_x = min(len(canvas[0]) - 1, max(x for x, _ in points))
    min_y = max(0, min(y for _, y in points))
    max_y = min(len(canvas) - 1, max(y for _, y in points))
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            if _inside_polygon(x + 0.5, y + 0.5, points):
                canvas[y][x] = color


def _half_block_rows(canvas: list[list[str | None]]) -> list[Text]:
    """Compress two colour pixels into each terminal row using ▀/▄ blocks."""
    rows: list[Text] = []
    for y in range(0, len(canvas), 2):
        row = Text()
        lower_y = y + 1
        for x, top in enumerate(canvas[y]):
            bottom = canvas[lower_y][x] if lower_y < len(canvas) else None
            if top is None and bottom is None:
                row.append(" ")
            elif top == bottom:
                row.append("█", style=_PALETTE[top])
            elif top is not None and bottom is not None:
                row.append("▀", style=f"{_PALETTE[top]} on {_PALETTE[bottom]}")
            elif top is not None:
                row.append("▀", style=_PALETTE[top])
            else:
                row.append("▄", style=_PALETTE[bottom])
        rows.append(row)
    return rows


def _high_resolution_duck(frame: int) -> Group:
    """Draw the full duck on a 42×30 pixel canvas, then pack it into 15 rows."""
    width, height = 42, 30
    canvas: list[list[str | None]] = [[None for _ in range(width)] for _ in range(height)]

    # Ground shadow and feet sit behind the body.
    _fill_ellipse(canvas, 22, 28, 14, 1, "D")
    foot_shift = 1 if frame in (1, 5) else 0
    _fill_polygon(canvas, ((12 + foot_shift, 26), (20 + foot_shift, 26), (21 + foot_shift, 29), (11, 29)), "K")
    _fill_polygon(canvas, ((13 + foot_shift, 26), (18 + foot_shift, 26), (19 + foot_shift, 28), (12, 28)), "O")
    _fill_polygon(canvas, ((26, 26), (34, 26), (35, 29), (25, 29)), "K")
    _fill_polygon(canvas, ((27, 26), (32, 26), (33, 28), (26, 28)), "O")
    for x in (16 + foot_shift, 30):
        for y in range(22, 27):
            canvas[y][x] = "K"
        for y in range(23, 27):
            canvas[y][x] = "O"

    # Raised tail, rounded body, lower shadow, and a clearly separate pale wing.
    _fill_polygon(canvas, ((32, 14), (39, 9), (41, 10), (40, 20), (35, 22)), "K")
    _fill_polygon(canvas, ((33, 15), (38, 11), (39, 11), (39, 19), (35, 20)), "B")
    _fill_ellipse(canvas, 25, 19, 15, 8, "K")
    _fill_ellipse(canvas, 25, 19, 14, 7, "B")
    _fill_ellipse(canvas, 26, 22, 12, 4, "b")
    _fill_polygon(canvas, ((18, 14), (34, 14), (36, 18), (32, 23), (22, 24), (16, 19)), "K")
    _fill_polygon(canvas, ((19, 15), (33, 15), (34, 18), (31, 22), (23, 23), (17, 19)), "W")
    _fill_polygon(canvas, ((30, 20), (34, 18), (32, 22), (24, 23)), "C")

    # Green chest/neck visually joins the head to the body without swallowing
    # the wing silhouette.
    _fill_ellipse(canvas, 14, 18, 7, 8, "K")
    _fill_ellipse(canvas, 15, 18, 6, 7, "G")
    _fill_polygon(canvas, ((15, 17), (20, 15), (22, 24), (16, 24)), "g")

    # Rounded mallard head.
    _fill_ellipse(canvas, 13, 9, 10, 9, "K")
    _fill_ellipse(canvas, 14, 9, 9, 8, "G")
    _fill_ellipse(canvas, 17, 6, 5, 5, "G")

    # Orange bill with a red lower edge and a black attachment/outline.
    _fill_polygon(canvas, ((0, 8), (7, 6), (11, 8), (11, 13), (6, 15), (0, 13)), "K")
    _fill_polygon(canvas, ((1, 9), (7, 7), (10, 9), (10, 12), (6, 13), (1, 12)), "O")
    _fill_polygon(canvas, ((2, 12), (10, 11), (9, 13), (3, 14)), "R")

    # Two discrete sunglass lenses, frames, bridge, and temple arm.
    left_frame = ((5, 5), (12, 5), (13, 10), (11, 12), (5, 11), (4, 7))
    right_frame = ((13, 5), (20, 5), (21, 7), (20, 11), (14, 12), (12, 10))
    _fill_polygon(canvas, left_frame, "K")
    _fill_polygon(canvas, right_frame, "K")
    _fill_polygon(canvas, ((6, 6), (11, 6), (12, 9), (10, 10), (6, 10), (5, 7)), "S")
    _fill_polygon(canvas, ((14, 6), (19, 6), (20, 7), (19, 10), (15, 11), (13, 9)), "S")
    for x in range(11, 15):
        canvas[7][x] = "K"
    for x in range(20, 24):
        canvas[7][x] = "K"

    # The shine crosses the left lens, bridge, then right lens over the loop.
    shine_positions = ((6, 6), (7, 6), (9, 7), (14, 6), (16, 7), (18, 8), None, None)
    shine = shine_positions[frame]
    if shine is not None:
        sx, sy = shine
        canvas[sy][sx] = "W"
        if sx + 1 < width:
            canvas[sy][sx + 1] = "W"

    rows = _half_block_rows(canvas)
    if frame in (2, 3, 4):
        rows.insert(0, Text(""))
    return Group(*rows)


def _pixel_line(source: str, *, glint_column: int | None = None) -> Text:
    line = Text()
    for column, pixel in enumerate(source.rstrip(".")):
        if pixel == ".":
            line.append("  ")
            continue
        if pixel == "S" and glint_column is not None and column == glint_column:
            pixel = "W"
        line.append("██", style=_PALETTE[pixel])
    return line


def _duck_art(source: tuple[str, ...], frame: int, *, full: bool) -> Group:
    glint_columns = (None, 7, 8, 11, 12, None, None, None)
    glint = glint_columns[frame] if full else ({2: 3, 3: 5}.get(frame))
    bob = 1 if frame in (2, 3, 4) else 0
    rows: list[Text] = [Text("")] * bob

    for row_index, row in enumerate(source):
        # The last two rows are feet in the full sprite. Shift one foot by a
        # pixel on alternating frames to give the duck a relaxed little shuffle.
        if full and row_index >= len(source) - 2 and frame in (1, 5):
            row = "." + row[:-1]
        rows.append(_pixel_line(row, glint_column=glint))

    shadow_width = 9 + (frame % 3)
    shadow = Text("  " * max(0, (len(source[0]) - shadow_width) // 2))
    shadow.append("▄" * (shadow_width * 2), style=_PALETTE["D"])
    rows.append(shadow)
    return Group(*rows)


def build_screensaver(*, width: int, height: int, elapsed: float | None = None) -> RenderableType:
    """Build a size-aware animated saver frame without mutating app content."""
    elapsed = idle_controller.animation_elapsed() if elapsed is None else elapsed
    frame = int(elapsed * 8) % 8

    if width >= 46 and height >= 19:
        art = _high_resolution_duck(frame)
    elif width >= 22 and height >= 13:
        art = _duck_art(_COMPACT_DUCK, frame, full=False)
    else:
        if width >= 20:
            label = "<(o )___ YEABOI"
        elif width >= 12:
            label = "<(o )_ YEABOI"
        else:
            label = "YEABOI"[:width]
        line = Text(label, style="bold rgb(42,170,105)")
        return Align.center(line, vertical="middle", height=max(1, height), style=f"on {NEUTRAL_BG}")

    caption = Text("YEABOI · chilling", style="bold rgb(105,220,235)", justify="center")
    hint = Text("press any key", style="rgb(95,105,115)", justify="center")
    content = Group(art, caption, hint)
    return Align.center(content, vertical="middle", height=max(1, height), style=f"on {NEUTRAL_BG}")
