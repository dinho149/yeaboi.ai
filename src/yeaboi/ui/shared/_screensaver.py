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

from yeaboi.ui.shared._mascot import render_full, render_head

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


def build_screensaver(*, width: int, height: int, elapsed: float | None = None) -> RenderableType:
    """Build a size-aware animated saver frame without mutating app content."""
    elapsed = idle_controller.animation_elapsed() if elapsed is None else elapsed
    frame = int(elapsed * 8) % 8

    if width >= 46 and height >= 19:
        art = render_full(frame)
    elif width >= 22 and height >= 13:
        art = render_head(frame)
    else:
        if width >= 20:
            label = "<(o )___ YEABOI"
        elif width >= 12:
            label = "<(o )_ YEABOI"
        else:
            label = "YEABOI"[:width]
        line = Text(label, style="bold rgb(42,170,105)")
        return Align.center(line, vertical="middle", height=max(1, height))

    caption = Text("YEABOI · chilling", style="bold rgb(105,220,235)", justify="center")
    hint = Text("press any key", style="rgb(95,105,115)", justify="center")
    content = Group(art, caption, hint)
    return Align.center(content, vertical="middle", height=max(1, height))
