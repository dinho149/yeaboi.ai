#!/usr/bin/env python3
"""Drive the screensaver's duck yard on demand, for recording.

The simulation lives in ``yeaboi.ui.shared._mayhem`` — this is a window onto it.
Two reasons to have it rather than just idling the app for five minutes: the
sprite scale can be raised past what a real terminal would use, which is what
makes a rotated duck survive being filmed, and it can be told to stop after a
fixed number of seconds so a take is reproducible.

    uv run python scripts/demo_duck_mayhem.py                # as the app draws it
    uv run python scripts/demo_duck_mayhem.py --scale 2      # for a 2x recording
    uv run python scripts/demo_duck_mayhem.py --seconds 8    # then exit
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rich.console import Console  # noqa: E402
from rich.live import Live  # noqa: E402

from yeaboi.ui.shared import _mayhem  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scale", type=int, default=1, help="sprite scale; 2 for a recording")
    parser.add_argument("--ducks", type=int, default=0, help="0 = as many as fit")
    parser.add_argument("--seed", type=int, default=3)
    parser.add_argument("--seconds", type=float, default=0, help="exit after N seconds (0 = forever)")
    parser.add_argument("--fps", type=float, default=60.0)
    args = parser.parse_args()

    _mayhem.configure(args.scale)
    if args.ducks:
        _mayhem.fits = lambda w, h, coverage=0.0, n=args.ducks: n  # type: ignore[assignment]

    console = Console()
    cols, rows = console.size
    dt = 1.0 / args.fps
    now = 0.0
    start = time.monotonic()

    # screen=True for the same reason the splash uses it: Rich double-buffers the
    # alternate screen and writes one atomic frame per refresh, so the animation
    # cannot tear. auto_refresh off so this loop alone decides when a frame exists.
    with Live(
        _mayhem.render(cols, rows, 0.0, seed=args.seed),
        console=console,
        auto_refresh=False,
        screen=True,
        vertical_overflow="crop",
    ) as live:
        while True:
            if args.seconds and now >= args.seconds:
                return 0
            now += dt
            live.update(_mayhem.render(cols, rows, now, seed=args.seed), refresh=True)
            # Pace against the clock rather than sleeping a flat dt, so it runs at
            # real speed even when a frame costs more than dt.
            behind = (start + now) - time.monotonic()
            if behind > 0:
                time.sleep(behind)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
