"""Shared animation and colour utilities for the TUI screens.

# See docs: "Architecture" — shared UI utility layer for visual effects.
# Provides colour interpolation, shimmer highlights, fade transitions, and
# easing functions used across mode_select, session, and provider_select.
"""

from __future__ import annotations

import math
import time

from rich.console import Console
from rich.live import Live

# ---------------------------------------------------------------------------
# Frame rate
# ---------------------------------------------------------------------------

FRAME_TIME_60FPS = 1.0 / 60  # ~60fps — used by mode_select for smooth animations
FRAME_TIME_30FPS = 1.0 / 30  # ~30fps — used by session and provider_select

# ---------------------------------------------------------------------------
# Shared colour constants
# ---------------------------------------------------------------------------

COLOR_RGB: dict[str, tuple[int, int, int]] = {
    "rgb(70,100,180)": (70, 100, 180),
    "rgb(100,180,100)": (100, 180, 100),
    "rgb(110,140,220)": (110, 140, 220),
    "rgb(220,160,60)": (220, 160, 60),
    "rgb(160,160,180)": (160, 160, 180),
    "rgb(200,100,180)": (200, 100, 180),  # Daily Standup accent (magenta)
    "rgb(80,190,190)": (80, 190, 190),  # Retro accent (teal)
    "rgb(220,110,90)": (220, 110, 90),  # Performance accent (coral)
    "rgb(140,120,230)": (140, 120, 230),  # Reporting accent (indigo)
}

# Grey levels for fade-out (bright → invisible) and fade-in (invisible → bright).
# Used by provider_select and mode_select for screen transitions.
FADE_OUT_LEVELS = [
    "rgb(100,100,100)",
    "rgb(80,80,80)",
    "rgb(60,60,60)",
    "rgb(45,45,45)",
    "rgb(30,30,30)",
    "rgb(20,20,20)",
    "rgb(12,12,12)",
]

FADE_IN_LEVELS = list(reversed(FADE_OUT_LEVELS))

BLACK_RGB = (10, 10, 12)  # fade-in starting colour (near-black)


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------


def lerp_color(t: float, from_rgb: tuple[int, int, int], to_rgb: tuple[int, int, int]) -> str:
    """Linearly interpolate between two RGB colours, returning an rgb(...) string."""
    r = int(from_rgb[0] + (to_rgb[0] - from_rgb[0]) * t)
    g = int(from_rgb[1] + (to_rgb[1] - from_rgb[1]) * t)
    b = int(from_rgb[2] + (to_rgb[2] - from_rgb[2]) * t)
    return f"rgb({r},{g},{b})"


def shimmer_style(base_color: str, char_index: int, total_chars: int, tick: float) -> str:
    """Return a per-character style that produces a traveling highlight shimmer.

    A bright white 'hotspot' sweeps across the text. Characters far from the
    hotspot use the base colour; those near it blend towards white.
    """
    speed = 0.6  # full sweeps per second
    hotspot = (tick * speed) % 1.0
    pos = char_index / max(total_chars - 1, 1)
    dist = min(abs(pos - hotspot), 1.0 - abs(pos - hotspot))
    intensity = math.exp(-(dist * dist) / 0.005)
    r, g, b = COLOR_RGB.get(base_color, (180, 180, 180))
    r2 = int(r + (255 - r) * intensity)
    g2 = int(g + (255 - g) * intensity)
    b2 = int(b + (255 - b) * intensity)
    return f"bold rgb({r2},{g2},{b2})"


def loading_border_color(tick: float) -> str:
    """Compute the white/grey cycling border colour for loading animations.

    Uses a sine wave to smoothly oscillate between white and grey so the
    input box border pulses while the LLM is working.
    """
    t = (math.cos(tick * 3) + 1) / 2
    grey_r, grey_g, grey_b = 100, 100, 100
    white_r, white_g, white_b = 255, 255, 255
    r = int(grey_r + (white_r - grey_r) * t)
    g = int(grey_g + (white_g - grey_g) * t)
    b = int(grey_b + (white_b - grey_b) * t)
    return f"rgb({r},{g},{b})"


def ease_out_cubic(t: float) -> float:
    """Cubic ease-out: decelerates smoothly into final position."""
    return 1 - (1 - t) ** 3


# ---------------------------------------------------------------------------
# Fade transitions
# ---------------------------------------------------------------------------


def fade_out(live: Live, console: Console, build_fn, *, frame_time: float = FRAME_TIME_30FPS, **kwargs):
    """Fade out the current screen by rendering it at decreasing brightness."""
    w, h = console.size
    for grey in FADE_OUT_LEVELS:
        live.update(build_fn(width=w, height=h, fade_style=grey, **kwargs))
        time.sleep(frame_time)


def fade_in(live: Live, console: Console, build_fn, *, frame_time: float = FRAME_TIME_30FPS, **kwargs):
    """Fade in a new screen by rendering it at increasing brightness."""
    w, h = console.size
    for grey in FADE_IN_LEVELS:
        live.update(build_fn(width=w, height=h, input_fade=grey, **kwargs))
        time.sleep(frame_time)


def scrollbar_column(
    viewport_h: int,
    total_lines: int,
    scroll_offset: int,
    *,
    track_char: str = "│",
    thumb_char: str = "┃",
    track_style: str = "rgb(40,40,50)",
    thumb_style: str = "rgb(100,100,120)",
) -> list[str]:
    """Build a vertical scrollbar as a list of styled characters (one per viewport row).

    Returns a list of length viewport_h. Each element is a Rich-markup string
    for one row of the scrollbar. When total_lines <= viewport_h (no scrolling
    needed), returns empty strings so no scrollbar is shown.
    """
    if total_lines <= viewport_h or viewport_h <= 0:
        return [""] * viewport_h

    # Thumb size: proportional to visible fraction, minimum 1 row
    thumb_size = max(1, round(viewport_h * viewport_h / total_lines))
    # Thumb position: map scroll_offset to the available track space
    max_scroll = total_lines - viewport_h
    track_space = viewport_h - thumb_size
    if max_scroll > 0 and track_space > 0:
        thumb_top = round(scroll_offset / max_scroll * track_space)
    else:
        thumb_top = 0
    thumb_top = max(0, min(thumb_top, viewport_h - thumb_size))

    rows: list[str] = []
    for i in range(viewport_h):
        if thumb_top <= i < thumb_top + thumb_size:
            rows.append(f"[{thumb_style}]{thumb_char}[/]")
        else:
            rows.append(f"[{track_style}]{track_char}[/]")
    return rows
