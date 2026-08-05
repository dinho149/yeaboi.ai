#!/usr/bin/env python3
"""Play the idle screensaver on demand, without waiting five minutes for it.

`build_screensaver()` is already a pure function of (width, height, elapsed), so
this drives it straight from a clock instead of going through IdleController —
no input plumbing, no monkeypatching IDLE_SECONDS, and the frame you get is the
frame a real idle session would draw.

Which duck you get is a function of the terminal size (see build_screensaver):

    >= 46 x >= 26   he waddles the floor and springs over the music tab
    >= 46 x >= 22   full duck, standing
    >= 22 x >= 13   head only — the breathing bob
    smaller         a text label

So `--head` just asks for a terminal in the third band. It exists because the
head loop is the one worth recording: the bob is a clean 8-frame cycle, and a
small grid means big cells, which is the only way to get a sharp capture off a
1x display.

    uv run python scripts/demo_screensaver.py            # size to the terminal
    uv run python scripts/demo_screensaver.py --head     # force the head band
    uv run python scripts/demo_screensaver.py --loops 3  # exit after 3 cycles
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rich.console import Console  # noqa: E402
from rich.live import Live  # noqa: E402

from yeaboi.ui.shared._screensaver import build_screensaver  # noqa: E402

# build_screensaver advances at int(elapsed * 8) % 8 — eight frames, eight per
# second, so the bob closes its cycle on the second. Loop length is not a
# guess; it is that constant read back.
FRAME_RATE = 8.0
FRAMES = 8
LOOP_SECONDS = FRAMES / FRAME_RATE

# Ceilings for the head band, one below each of build_screensaver's thresholds.
HEAD_MAX_W = 45
HEAD_MAX_H = 21


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--head", action="store_true", help="force the head-only band")
    parser.add_argument("--loops", type=float, default=0, help="exit after N animation cycles (0 = forever)")
    parser.add_argument("--fps", type=float, default=60.0, help="redraw rate")
    args = parser.parse_args()

    console = Console()
    width, height = console.size
    if args.head:
        # Clamp rather than assume: a window sized for the head band already
        # satisfies this, and one that is not gets the head anyway instead of
        # silently recording the wrong duck.
        width = min(width, HEAD_MAX_W)
        height = min(height, HEAD_MAX_H)

    deadline = args.loops * LOOP_SECONDS if args.loops else None
    frame_time = 1.0 / args.fps
    start = time.monotonic()

    # screen=True for the same reason the splash uses it: Rich double-buffers
    # the alternate screen and writes one atomic frame per refresh, so the
    # animation cannot tear. auto_refresh off so this loop is the only thing
    # deciding when a frame exists.
    with Live(
        build_screensaver(width=width, height=height, elapsed=0.0),
        console=console,
        auto_refresh=False,
        screen=True,
        vertical_overflow="crop",
    ) as live:
        while True:
            elapsed = time.monotonic() - start
            if deadline is not None and elapsed >= deadline:
                return 0
            live.update(build_screensaver(width=width, height=height, elapsed=elapsed), refresh=True)
            time.sleep(frame_time)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
