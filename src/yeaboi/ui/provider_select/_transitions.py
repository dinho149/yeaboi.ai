"""Transition animations for the provider selection wizard.

# See docs: "Architecture" — UI animation layer for the setup wizard.
# Handles the crossfade from provider selection to API key input.
"""

from __future__ import annotations

import math
import time
from typing import Any

from rich.console import Console
from rich.live import Live

from yeaboi.ui.provider_select._constants import _PROVIDER_CARDS
from yeaboi.ui.provider_select.screens._screens import _build_input_screen, _build_select_screen
from yeaboi.ui.shared._animations import COLOR_RGB, FADE_IN_LEVELS, FADE_OUT_LEVELS, FRAME_TIME_30FPS


def _transition_to_input(live: Live, console: Console, selected: int, provider: dict[str, Any]):
    """Animate from provider selection to API key input.

    1. Fade out unselected providers
    2. Brief pause with only selected provider
    3. Fade in the input box elements
    """
    w, h = console.size
    all_indices = list(range(len(_PROVIDER_CARDS)))
    others = [i for i in all_indices if i != selected]

    # Phase 0: pulse the selected provider (base colour -> white -> back)
    # First, clear shimmer by rendering with a static override on selected
    base_r, base_g, base_b = COLOR_RGB.get(provider["color"], (180, 180, 180))
    base_style = f"bold rgb({base_r},{base_g},{base_b})"
    live.update(
        _build_select_screen(
            selected,
            width=w,
            height=h,
            visible=all_indices,
            step=0,
            fade_style=base_style,
            fade_indices=[selected],
        )
    )
    time.sleep(FRAME_TIME_30FPS)

    pulse_frames = 12  # 6 up + 6 down
    for frame in range(pulse_frames):
        # Sinusoidal intensity: 0->1->0
        t = frame / (pulse_frames - 1)
        intensity = math.sin(t * math.pi)
        r = int(base_r + (255 - base_r) * intensity)
        g = int(base_g + (255 - base_g) * intensity)
        b = int(base_b + (255 - base_b) * intensity)
        pulse_style = f"bold rgb({r},{g},{b})"
        live.update(
            _build_select_screen(
                selected,
                width=w,
                height=h,
                visible=all_indices,
                step=0,
                fade_style=pulse_style,
                fade_indices=[selected],
            )
        )
        time.sleep(FRAME_TIME_30FPS)

    # Phase 1: fade out unselected providers (keep selected on static base colour)
    for grey in FADE_OUT_LEVELS:
        live.update(
            _build_select_screen(
                selected,
                width=w,
                height=h,
                visible=all_indices,
                step=0,
                fade_style=grey,
                fade_indices=others,
                selected_style=base_style,
            )
        )
        time.sleep(FRAME_TIME_30FPS)

    # Phase 2: crossfade directly into the input screen — no pause.
    # The input screen already shows the selected provider at the top,
    # so we just fade in the new elements (instructions, input box).
    for grey in FADE_IN_LEVELS:
        live.update(_build_input_screen(provider, "", width=w, height=h, input_fade=grey))
        time.sleep(FRAME_TIME_30FPS)

    # Final clean render
    live.update(_build_input_screen(provider, "", width=w, height=h))
