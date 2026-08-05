#!/usr/bin/env python3
"""A yard full of ducks, for recording. Not part of the app.

The idle screensaver is one calm head. This is the opposite: a dozen of them
adrift in zero gravity, spinning, ricocheting off each other and off the big one
anchored in the middle, quacking on every impact. It exists to be captured by
tui-recorder and posted somewhere; nothing in yeaboi imports it.

Three things shape the implementation.

**It composites at pixel level, not cell level.** The sprites in _mascot are
half-block packed — two pixel rows per terminal row — and the packing happens
last there. Doing the same here buys positions at half-cell precision, and it is
also the only level at which a sprite can be rotated at all. Everything below
works on grids of letter codes ('.' being transparent), exactly the form _mascot
keeps its sprites in, and the whole canvas is packed once at the end.

**Rotation is real, and pre-baked.** Each duck is nearest-neighbour rotated into
ROT_STEPS fixed angles once at import, then indexed per frame. Rotating on the
fly would be the same arithmetic a thousand times a second for results that
never change. Nearest-neighbour rather than any smoothing: the edges are drawn
deliberately at this size, and interpolating them just makes mud.

**The simulation is deterministic.** Fixed timestep, RNG seeded once at setup and
never touched while stepping. Re-recording a seed gives the same clip, which is
the difference between a demo you can regenerate and one you got lucky with.

    uv run python scripts/demo_duck_mayhem.py                 # size to terminal
    uv run python scripts/demo_duck_mayhem.py --seconds 8     # then exit
    uv run python scripts/demo_duck_mayhem.py --seed 7        # a different yard
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
from dataclasses import dataclass
from functools import cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rich.console import Console, Group  # noqa: E402
from rich.live import Live  # noqa: E402
from rich.text import Text  # noqa: E402

from yeaboi.ui.shared._mascot import (  # noqa: E402
    DUCK_HEAD,
    DUCK_HEAD_FACE,
    DUCK_HEAD_GLASSES,
    DUCK_HEAD_QUACK,
    SHADES_LIFT_SEQUENCE,
    _compose,
    _pack_cells,
    _shift,
)

Grid = tuple[str, ...]

# ---------------------------------------------------------------------------
# Tuning. Distances are sprite pixels, angles degrees, time seconds.
# ---------------------------------------------------------------------------

# No gravity. Ducks drift; nothing falls and nothing piles up. With gravity they
# spent the clip settling into a twitching heap along the floor, which is the
# opposite of mayhem.
DRIFT_SPEED = (9.0, 20.0)  # initial speed, px/s
SPIN_SPEED = (-150.0, 150.0)  # deg/s; sign is direction

# Elastic. Any energy loss at all and a gravity-free yard visibly winds down
# over eight seconds, with nothing to put the energy back.
BOUNCE = 1.0

QUACK_SECONDS = 0.22  # beak stays open this long after a hit
ROT_STEPS = 24  # baked angles, i.e. 15 degrees apart

# Squash and stretch on impact, recovering over SQUISH_SECONDS. Each entry is a
# height multiplier; width takes the inverse, capped, so he reads as compressing
# rather than shrinking.
#
# Deliberately gentle. The first version squashed to 58% and looked broken: at
# that depth the sunglasses and beak are folded into each other, and stacked on
# top of a rotation there is no reading of the frame in which it is a duck. The
# eye registers a squash from very little — a fifth of the height is plenty, and
# the point is the impact reading as an impact, not the pose being legible.
SQUISH_SECONDS = 0.16
SQUISH_CURVE = (0.80, 0.87, 0.93, 0.97)
MAX_STRETCH = 1.10
# Quantised impact directions. The squash flattens along the contact normal —
# the face that hit the wall is the face that goes flat, like a ball — so it has
# to be applied in world space, after the duck's own rotation. Sixteen
# directions is 22.5 degrees apart, finer than anyone can pick out mid-bounce.
NORMAL_STEPS = 16

# Crowd sprites are upscaled before they are rotated, and this is the single
# thing that decides whether a rotated duck still looks like a duck.
#
# The sunglasses are one pixel thick in the source art. Rotate 16x14 by anything
# that is not a multiple of 90 and that pixel lands between two others and is
# lost, which turns the face to mush. At 2x it is two pixels thick and survives.
#
# It costs nothing on screen: a duck's size in the frame is its cell footprint
# times the cell size, so doubling the sprite and halving the font leaves him
# exactly as big while doubling the detail he is drawn with. Pair this with a
# smaller terminal font, not with a bigger duck.
DUCK_SCALE = 2

# The anchored duck in the middle — twice the crowd again, immovable, and the
# fixed point the whole scene is arranged around.
HERO_SCALE = DUCK_SCALE * 2
HERO_SHADES_EVERY = 3.0
HERO_SHADES_FPS = 8


# ---------------------------------------------------------------------------
# Grid helpers — a grid is a tuple of equal-length rows of letter codes
# ---------------------------------------------------------------------------


def scale(grid: Grid, factor: int) -> Grid:
    """Nearest-neighbour upscale, the only honest way to enlarge pixel art."""
    if factor == 1:
        return grid
    out: list[str] = []
    for row in grid:
        wide = "".join(ch * factor for ch in row)
        out.extend([wide] * factor)
    return tuple(out)


def squash(grid: Grid, factor: float) -> Grid:
    """Compress vertically by ``factor`` and widen by its inverse.

    Applied *before* rotation, so the squash is along the duck's own body axis
    and turns with him. Doing it after would need a general affine baked for
    every (angle, impact-direction) pair, for a difference nobody watching a
    dozen ducks at once could pick out.
    """
    if factor >= 1.0:
        return grid
    h, w = len(grid), len(grid[0])
    new_h = max(1, round(h * factor))
    new_w = max(1, round(w * min(1.0 / factor, MAX_STRETCH)))
    return tuple(
        "".join(grid[min(h - 1, y * h // new_h)][min(w - 1, x * w // new_w)] for x in range(new_w))
        for y in range(new_h)
    )


def rotate(grid: Grid, degrees: float, size: int | None = None) -> Grid:
    """Rotate about the centre onto a square canvas big enough for any angle.

    Square and diagonal-sized so every baked angle comes out the same shape — a
    sprite that changed size as it turned would need its geometry recomputed
    every frame and would visibly breathe.

    Inverse mapping (walk the destination, sample the source) rather than
    forward: rotating source pixels *into* the destination leaves unpainted
    holes wherever the mapping is not onto.
    """
    h, w = len(grid), len(grid[0])
    # Callers pass an explicit size so every squash level bakes to the same
    # canvas — a sprite whose footprint changed with its squash would need its
    # geometry recomputed per frame.
    size = size or math.ceil(math.hypot(w, h))
    src_cx, src_cy = (w - 1) / 2, (h - 1) / 2
    dst_c = (size - 1) / 2
    rad = math.radians(degrees)
    cos, sin = math.cos(rad), math.sin(rad)

    out: list[str] = []
    for dy in range(size):
        row: list[str] = []
        for dx in range(size):
            ox, oy = dx - dst_c, dy - dst_c
            sx = round(cos * ox + sin * oy + src_cx)
            sy = round(-sin * ox + cos * oy + src_cy)
            row.append(grid[sy][sx] if 0 <= sy < h and 0 <= sx < w else ".")
        out.append("".join(row))
    return tuple(out)


SOURCE = scale(DUCK_HEAD, DUCK_SCALE)
SOURCE_QUACK = scale(DUCK_HEAD_QUACK, DUCK_SCALE)

# One canvas for every (squash, angle) pair: the widest the sprite ever gets, on
# its diagonal.
SPRITE_SIZE = math.ceil(math.hypot(len(SOURCE[0]) * MAX_STRETCH, len(SOURCE)))


@cache
def squashed(angle_idx: int, level: int, normal_idx: int, quack: bool) -> Grid:
    """A duck at ``angle_idx``, flattened along world direction ``normal_idx``.

    Squashing along an arbitrary world axis is three steps: turn that axis
    vertical, squash vertically, turn back. Written out, the whole transform is
    R(normal) . S_v(f) . R(angle - normal) applied to the source.

    Cached rather than pre-baked. The full table is 5 levels x 24 angles x 16
    normals x 2 beaks — 3,840 grids, several seconds of import for a set of
    which a given clip touches a few hundred. lru_cache pays only for what is
    actually asked for, and every combination repeats constantly once the yard
    is moving.
    """
    source = SOURCE_QUACK if quack else SOURCE
    if level == 0:
        return rotate(source, angle_idx * 360.0 / ROT_STEPS, SPRITE_SIZE)
    normal = normal_idx * 360.0 / NORMAL_STEPS
    angle = angle_idx * 360.0 / ROT_STEPS
    upright = rotate(source, angle - normal, SPRITE_SIZE)
    return rotate(squash(upright, SQUISH_CURVE[level - 1]), normal, SPRITE_SIZE)


def bake(grid: Grid) -> tuple[tuple[Grid, ...], ...]:
    """Every squash level at every angle, once, at import.

    Five levels by twenty-four angles is 120 small grids per sprite — built once
    in a few milliseconds, then indexed for the rest of the run. Doing either
    transform live would be the same arithmetic a thousand times a second for
    results that never change.
    """
    levels = (1.0, *SQUISH_CURVE)
    return tuple(
        tuple(rotate(squash(grid, level), i * 360.0 / ROT_STEPS, SPRITE_SIZE) for i in range(ROT_STEPS))
        for level in levels
    )


ROTATED = bake(SOURCE)
ROTATED_QUACK = bake(SOURCE_QUACK)
# Collision radius from the *unrotated* sprite, not from the diagonal canvas it
# is baked onto — the corners of that canvas are empty, and using them would
# have ducks bouncing off each other's whitespace.
DUCK_RADIUS = max(len(SOURCE[0]), len(SOURCE)) / 2.0

# Where the sunglasses sit inside the source art, so the collider can be put on
# them. Derived rather than typed in, or it silently drifts if the art changes.
_GLASSES_PX = [(x, y) for y, row in enumerate(DUCK_HEAD_GLASSES) for x, ch in enumerate(row) if ch != "."]
GLASSES_X0, GLASSES_X1 = min(x for x, _ in _GLASSES_PX), max(x for x, _ in _GLASSES_PX)
GLASSES_Y0, GLASSES_Y1 = min(y for _, y in _GLASSES_PX), max(y for _, y in _GLASSES_PX)
# A circle is a poor fit for something 12 wide and 3 tall, so this splits the
# difference — big enough to feel solid, small enough not to bounce ducks off
# thin air either side of him.
GLASSES_RADIUS = (GLASSES_X1 - GLASSES_X0 + 1) / 3.0 * HERO_SCALE

HERO_W = len(DUCK_HEAD[0]) * HERO_SCALE
HERO_H = len(DUCK_HEAD) * HERO_SCALE
HERO_RADIUS = max(HERO_W, HERO_H) / 2.0


# Blank rows above the crown for the raised pair to float into. Always present,
# even at rest: a grid that grew when the gag started would shift the head half a
# dozen pixels every three seconds, because compose() centres on the duck.
HERO_PAD = 4


def hero_lift(elapsed: float) -> int:
    """How far the shades are currently raised, in source pixels. 0 at rest."""
    period = int(HERO_SHADES_EVERY * HERO_SHADES_FPS)
    step_i = int(elapsed * HERO_SHADES_FPS) % period
    start = period - len(SHADES_LIFT_SEQUENCE)
    return SHADES_LIFT_SEQUENCE[step_i - start] if step_i >= start else 0


def hero_grid(elapsed: float) -> Grid:
    """The anchored duck, running the shades gag on a timer.

    Mirrors render_head_shades' composition rather than calling it: that returns
    a packed renderable, and everything here stays an unpacked pixel grid until
    the final blit.
    """
    pad = ("." * len(DUCK_HEAD_FACE[0]),) * HERO_PAD
    face, glasses = pad + DUCK_HEAD_FACE, pad + DUCK_HEAD_GLASSES
    return scale(_compose(face, glasses, _shift(glasses, hero_lift(elapsed))), HERO_SCALE)


def blit(canvas: list[list[str]], sprite: Grid, left: int, top: int) -> None:
    """Paint a sprite, skipping its transparent pixels."""
    height, width = len(canvas), len(canvas[0])
    for dy, row in enumerate(sprite):
        cy = top + dy
        if not 0 <= cy < height:
            continue
        for dx, ch in enumerate(row):
            cx = left + dx
            if ch != "." and 0 <= cx < width:
                canvas[cy][cx] = ch


# ---------------------------------------------------------------------------
# Simulation. Positions are sprite centres — the only origin that still makes
# sense once things rotate.
# ---------------------------------------------------------------------------


@dataclass
class Duck:
    x: float
    y: float
    vx: float
    vy: float
    angle: float = 0.0
    spin: float = 0.0
    quack_until: float = -1.0
    squish_until: float = -1.0
    # Direction the last hit came from, quantised. The squash flattens along it.
    squish_normal: int = 0
    radius: float = DUCK_RADIUS
    anchored: bool = False
    is_hero: bool = False
    # The hero's raised sunglasses: a collider with no sprite of its own, live
    # only while they are actually off his head. Ducks bounce off them.
    is_shades: bool = False
    enabled: bool = True
    base_y: float = 0.0

    @property
    def mass(self) -> float:
        """Area, so a 2x duck weighs four of the little ones. The hero is
        anchored so it never comes up for him — but a second big one would work."""
        return self.radius * self.radius

    def sprite(self, now: float) -> Grid:
        if self.is_hero:
            # Quacking beats the shades gag: he cannot be mid-cool-reveal and
            # mid-yelp at once, and the yelp is the one a duck to the face causes.
            return scale(DUCK_HEAD_QUACK, HERO_SCALE) if now < self.quack_until else hero_grid(now)
        level = 0
        if now < self.squish_until:
            # Flattest at the moment of impact, easing back out as the timer runs.
            remaining = (self.squish_until - now) / SQUISH_SECONDS
            level = 1 + min(len(SQUISH_CURVE) - 1, int((1.0 - remaining) * len(SQUISH_CURVE)))
        angle_idx = int(self.angle / (360.0 / ROT_STEPS)) % ROT_STEPS
        return squashed(angle_idx, level, self.squish_normal, now < self.quack_until)


def _hit(duck: Duck, now: float, nx: float, ny: float) -> None:
    """Record an impact: open the beak, and flatten along the contact normal."""
    duck.quack_until = now + QUACK_SECONDS
    if duck.is_hero or duck.is_shades:
        return  # immovable things do not visibly give
    duck.squish_until = now + SQUISH_SECONDS
    duck.squish_normal = round(math.degrees(math.atan2(ny, nx)) / (360.0 / NORMAL_STEPS)) % NORMAL_STEPS


def make_ducks(count: int, width: int, height: int, rng: random.Random) -> list[Duck]:
    """Scatter drifting ducks, then anchor the hero dead centre in both axes.

    RNG is used here and nowhere else, so a whole run is a pure function of the
    seed.
    """
    hero = Duck(
        x=width / 2,
        y=height / 2,
        vx=0.0,
        vy=0.0,
        radius=HERO_RADIUS,
        anchored=True,
        is_hero=True,
    )

    ducks: list[Duck] = []
    margin = SPRITE_SIZE / 2
    for _ in range(count):
        # Rejection-sample a start clear of the hero, so nothing begins the clip
        # already inside him and gets flung out on frame one.
        x = y = 0.0
        for _attempt in range(200):
            x = rng.uniform(margin, width - margin)
            y = rng.uniform(margin, height - margin)
            if math.hypot(x - hero.x, y - hero.y) > hero.radius + DUCK_RADIUS + 2:
                break
        heading = rng.uniform(0, 2 * math.pi)
        speed = rng.uniform(*DRIFT_SPEED)
        ducks.append(
            Duck(
                x=x,
                y=y,
                vx=math.cos(heading) * speed,
                vy=math.sin(heading) * speed,
                angle=rng.uniform(0, 360),
                spin=rng.uniform(*SPIN_SPEED),
            )
        )
    # The raised sunglasses, as a collider with no sprite. Its resting centre is
    # the glasses' own centre inside the hero grid; step() lifts it from there.
    gx = (GLASSES_X0 + GLASSES_X1) / 2 - (len(DUCK_HEAD[0]) - 1) / 2
    gy = HERO_PAD + (GLASSES_Y0 + GLASSES_Y1) / 2 - (HERO_PAD + len(DUCK_HEAD) - 1) / 2
    ducks.append(
        Duck(
            x=hero.x + gx * HERO_SCALE,
            y=hero.y + gy * HERO_SCALE,
            vx=0.0,
            vy=0.0,
            radius=GLASSES_RADIUS,
            anchored=True,
            is_shades=True,
            enabled=False,
            base_y=hero.y + gy * HERO_SCALE,
        )
    )
    ducks.append(hero)
    return ducks


def step(ducks: list[Duck], dt: float, now: float, width: int, height: int) -> None:
    """Advance one fixed timestep. Mutates in place."""
    margin = SPRITE_SIZE / 2

    # The sunglasses are only solid while they are actually off his head.
    lift = hero_lift(now)
    for duck in ducks:
        if duck.is_shades:
            duck.enabled = lift > 0
            duck.y = duck.base_y - lift * HERO_SCALE

    for duck in ducks:
        if duck.anchored:
            continue
        duck.x += duck.vx * dt
        duck.y += duck.vy * dt
        duck.angle = (duck.angle + duck.spin * dt) % 360.0

        # Walls, clamped on the *drawn* half-size rather than the collision
        # radius, so a rotated duck never has a corner cut off by the frame edge.
        if duck.x <= margin:
            duck.x, duck.vx = margin, abs(duck.vx) * BOUNCE
            _hit(duck, now, -1.0, 0.0)
        elif duck.x >= width - margin:
            duck.x, duck.vx = width - margin, -abs(duck.vx) * BOUNCE
            _hit(duck, now, 1.0, 0.0)
        if duck.y <= margin:
            duck.y, duck.vy = margin, abs(duck.vy) * BOUNCE
            _hit(duck, now, 0.0, -1.0)
        elif duck.y >= height - margin:
            duck.y, duck.vy = height - margin, -abs(duck.vy) * BOUNCE
            _hit(duck, now, 0.0, 1.0)

    # Circle against circle, resolved after everyone has moved so the outcome
    # does not depend on list order. Circles and not boxes because these things
    # rotate: an axis-aligned box around a spinning sprite is the wrong shape at
    # most of the baked angles.
    for i, a in enumerate(ducks):
        if not a.enabled:
            continue
        for b in ducks[i + 1 :]:
            if not b.enabled or (a.anchored and b.anchored):
                continue
            dx, dy = b.x - a.x, b.y - a.y
            dist = math.hypot(dx, dy)
            gap = a.radius + b.radius
            if dist >= gap:
                continue
            if dist < 1e-6:
                # Exactly coincident, so there is no normal to work with. Pick
                # one — deterministically, because this must not reach for RNG.
                nx, ny, dist = 1.0, 0.0, 1e-6
            else:
                nx, ny = dx / dist, dy / dist

            overlap = gap - dist
            # Separate along the normal by inverse mass — or entirely onto the
            # loose one when the other cannot move.
            if a.anchored:
                b.x += nx * overlap
                b.y += ny * overlap
            elif b.anchored:
                a.x -= nx * overlap
                a.y -= ny * overlap
            else:
                total = a.mass + b.mass
                a.x -= nx * overlap * (b.mass / total)
                a.y -= ny * overlap * (b.mass / total)
                b.x += nx * overlap * (a.mass / total)
                b.y += ny * overlap * (a.mass / total)

            # Exchange only the velocity component along the normal; leaving the
            # tangential part alone is what makes a glancing blow graze rather
            # than stop dead.
            avn = a.vx * nx + a.vy * ny
            bvn = b.vx * nx + b.vy * ny
            if avn - bvn <= 0:  # already separating
                continue
            if a.anchored:
                delta = -bvn * BOUNCE - bvn
                b.vx += delta * nx
                b.vy += delta * ny
                b.spin = -b.spin
            elif b.anchored:
                delta = -avn * BOUNCE - avn
                a.vx += delta * nx
                a.vy += delta * ny
                a.spin = -a.spin
            else:
                total = a.mass + b.mass
                new_a = ((a.mass - b.mass) * avn + 2 * b.mass * bvn) / total * BOUNCE
                new_b = ((b.mass - a.mass) * bvn + 2 * a.mass * avn) / total * BOUNCE
                a.vx += (new_a - avn) * nx
                a.vy += (new_a - avn) * ny
                b.vx += (new_b - bvn) * nx
                b.vy += (new_b - bvn) * ny
                a.spin, b.spin = b.spin, a.spin

            # Each flattens against the other, so the normals are opposite.
            _hit(a, now, nx, ny)
            _hit(b, now, -nx, -ny)

    # Separation runs after the wall clamps, so a duck shoved by a neighbour can
    # end up part-way out of frame. Clamp last and the walls always win — which
    # matters most in a pile-up, exactly when there are most ducks to lose.
    for duck in ducks:
        if duck.anchored:
            continue
        duck.x = min(max(duck.x, margin), float(width) - margin)
        duck.y = min(max(duck.y, margin), float(height) - margin)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def compose(ducks: list[Duck], now: float, cols: int, rows: int) -> Group:
    """Blit the yard onto one pixel canvas, then pack it once."""
    canvas = [["." for _ in range(cols)] for _ in range(rows * 2)]

    # Hero last, so the crowd passes behind him rather than over his face. He is
    # one of the ducks now, so this is a draw order and not a special case.
    for duck in sorted(ducks, key=lambda d: d.is_hero):
        if duck.is_shades:
            continue  # a collider, drawn as part of the hero
        sprite = duck.sprite(now)
        blit(canvas, sprite, int(duck.x - len(sprite[0]) / 2), int(duck.y - len(sprite) / 2))

    lines: list[Text] = []
    for cells in _pack_cells(tuple("".join(row) for row in canvas)):
        line = Text()
        for glyph, style in cells:
            line.append(glyph, style=style)
        lines.append(line)
    return Group(*lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ducks", type=int, default=12)
    parser.add_argument("--seed", type=int, default=3)
    parser.add_argument("--seconds", type=float, default=0, help="exit after N seconds (0 = forever)")
    parser.add_argument("--fps", type=float, default=60.0)
    args = parser.parse_args()

    console = Console()
    cols, rows = console.size
    px_h = rows * 2

    # noqa S311: this is a duck, not a nonce. Reproducibility is the whole
    # requirement — a cryptographic generator would be strictly worse.
    rng = random.Random(args.seed)  # noqa: S311
    ducks = make_ducks(args.ducks, cols, px_h, rng)

    # Fixed timestep, decoupled from how long a frame takes to draw. A
    # wall-clock dt would make the run unreproducible, and the point of seeding
    # is that a seed gives the same clip every time.
    dt = 1.0 / args.fps
    now = 0.0
    start = time.monotonic()

    with Live(
        compose(ducks, now, cols, rows),
        console=console,
        auto_refresh=False,
        screen=True,
        vertical_overflow="crop",
    ) as live:
        while True:
            if args.seconds and now >= args.seconds:
                return 0
            step(ducks, dt, now, cols, px_h)
            now += dt
            live.update(compose(ducks, now, cols, rows), refresh=True)
            # Pace against the clock rather than sleeping a flat dt, so the
            # animation runs at real speed even when a frame costs more than dt.
            behind = (start + now) - time.monotonic()
            if behind > 0:
                time.sleep(behind)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130) from None
