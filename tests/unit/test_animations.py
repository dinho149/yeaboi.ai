"""Tests for shared animation helpers (yeaboi.ui.shared._animations)."""

from __future__ import annotations

import re

from yeaboi.ui.shared._animations import shimmer_style

_PERIOD = 3.1  # keep in step with shimmer_style
_SWEEP_FRAC = 0.64


def _rgb(style: str) -> tuple[int, int, int]:
    m = re.search(r"rgb\((\d+),(\d+),(\d+)\)", style)
    assert m, style
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


class TestShimmerStyle:
    def test_rest_beat_is_flat_base_colour(self):
        # During the resting portion of the cycle every glyph sits at the base
        # colour — no hotspot anywhere (this is the pause between waves).
        base = "rgb(220,110,90)"
        rest_tick = _PERIOD * (_SWEEP_FRAC + (1 - _SWEEP_FRAC) / 2)  # mid-rest
        styles = {shimmer_style(base, i, 11, rest_tick) for i in range(11)}
        assert styles == {f"bold {base}"}

    def test_sweep_brightens_some_glyph(self):
        # Partway through the sweep, at least one glyph is lifted toward white
        # (brighter than the base colour).
        base = "rgb(220,110,90)"
        br, bg, bb = 220, 110, 90
        sweep_tick = _PERIOD * (_SWEEP_FRAC * 0.5)  # mid-sweep
        brightest = max(_rgb(shimmer_style(base, i, 11, sweep_tick)) for i in range(11))
        assert brightest > (br, bg, bb)

    def test_hotspot_travels_across_the_word(self):
        # Early vs late in the sweep the brightest glyph moves left→right.
        base = "rgb(80,190,190)"

        def brightest_index(tick: float) -> int:
            vals = [sum(_rgb(shimmer_style(base, i, 11, tick))) for i in range(11)]
            return max(range(11), key=vals.__getitem__)

        early = brightest_index(_PERIOD * _SWEEP_FRAC * 0.2)
        late = brightest_index(_PERIOD * _SWEEP_FRAC * 0.85)
        assert late > early
