"""Single source of truth for the Yeaboi duck mascot sprite.

# See docs: "TUI system" — the mascot renders as chunky half-block (▀/▄) pixel
# art. Full-body layers (base/wing/glasses) are traced offline and frozen in
# _mascot_sprites.py; the small head companion is hand-authored below. Animation
# is pure: compose the layers at a per-frame offset. No timing or IO lives here.
"""

from __future__ import annotations

from rich.console import Group
from rich.text import Text

from yeaboi.ui.shared._mascot_sprites import (
    DUCK_BASE,
    DUCK_GLASSES,
    DUCK_MINI_BASE,
    DUCK_MINI_GLASSES,
    DUCK_MINI_WING,
    DUCK_WING,
)

FRAMES = 8

# Letter -> rgb. MUST equal PALETTE in scripts/gen_mascot_sprites.py.
MASCOT_PALETTE: dict[str, tuple[int, int, int]] = {
    "k": (9, 14, 18),
    "o": (26, 32, 40),
    "G": (34, 158, 122),
    "g": (22, 110, 92),
    "W": (232, 240, 238),
    "L": (150, 190, 190),
    "M": (96, 140, 144),
    "S": (60, 100, 108),
    "b": (250, 176, 44),
    "r": (228, 104, 22),
}

# Hand-authored, hand-cleaned head companion (glints baked in as constant "W").
DUCK_HEAD: tuple[str, ...] = (
    "......kkkk......",
    ".....GGGGGG.....",
    "....oGGGGGGG....",
    "...oGGGGGGGGo...",
    "...kkkkkWkkkWkk.",
    "...gggkWkkkWkkk.",
    "...gGGkkkkkkkkk.",
    "...gGGGkkkbbkk..",
    "...ggGGGGbbbbbkk",
    "...kggGGrrrbbbbk",
    "....kggggkkkkk..",
    ".....ggggg......",
    ".....GGGGGG.....",
    "....ggGGGGG.....",
)

# Layered head for the "double shades" gag (click the companion duck): his
# sunglasses lift up to reveal an identical pair underneath, then drop back. The
# head is split into a glasses-less FACE and a separable GLASSES band so one pair
# can slide up (masked to the head silhouette) while a static second pair stays on
# the eyes. Composited at lift 0 they reproduce DUCK_HEAD exactly.
DUCK_HEAD_FACE: tuple[str, ...] = (
    "......kkkk......",
    ".....GGGGGG.....",
    "....oGGGGGGG....",
    "...oGGGGGGGGo...",
    "...GGGGGGGGGGGG.",  # eye band, de-glassed to plain face green
    "...gggGGGGGGGGg.",
    "...gGGGGGGGGGGg.",
    "...gGGGkkkbbkk..",
    "...ggGGGGbbbbbkk",
    "...kggGGrrrbbbbk",
    "....kggggkkkkk..",
    ".....ggggg......",
    ".....GGGGGG.....",
    "....ggGGGGG.....",
)

# Just the sunglasses band (transparent elsewhere), positioned over the eyes —
# rows 4–6 match DUCK_HEAD's lenses so FACE + GLASSES == DUCK_HEAD.
DUCK_HEAD_GLASSES: tuple[str, ...] = (
    "...............",
    "...............",
    "...............",
    "...............",
    "...kkkkkWkkkWkk.",
    "......kWkkkWkkk.",
    "......kkkkkkkkk.",
    "...............",
    "...............",
    "...............",
    "...............",
    "...............",
    "...............",
    "...............",
)

# Open-beak variant — identical to DUCK_HEAD except the bill splits into an upper
# and lower half with a dark mouth gap between (rows 9–10). Toggling with the
# closed head makes the duck look like he's quacking (used when a new tip appears).
DUCK_HEAD_QUACK: tuple[str, ...] = (
    *DUCK_HEAD[:9],
    "...kggGGkkkkkkkk",  # mouth open (dark gap where the lower bill was)
    "....kgggbrrbbk..",  # lower bill dropped a row
    *DUCK_HEAD[11:],
)

# Per-frame vertical offsets (pixels). Positive = lift the layer up.
WING_OFF = (0, 1, 2, 2, 1, 0, 0, 0)  # gentle wing flap
GLASS_OFF = (0, 0, 0, 1, 1, 1, 0, 0)  # slow glasses bob
HEAD_BOB = (0, 0, 1, 1, 1, 0, 0, 0)  # head breathing bob (shift down)
# Mini-duck flap. The full-body offsets (up to 2px) are proportionally too large
# for the 22px trace — a 1px flap keeps the wing attached to the body. The mini
# glasses stay put: at this scale a bob detaches the lens by a whole half-block.
MINI_WING_OFF = (0, 0, 1, 1, 1, 0, 0, 0)


def _style(letter: str) -> str | None:
    rgb = MASCOT_PALETTE.get(letter)
    return None if rgb is None else f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"


def _shift(grid: tuple[str, ...], dy: int) -> tuple[str, ...]:
    """Lift a layer up by dy pixels (content from below), transparent fill."""
    if dy <= 0:
        return grid
    blank = "." * len(grid[0])
    return tuple(grid[y + dy] if y + dy < len(grid) else blank for y in range(len(grid)))


def _bob(grid: tuple[str, ...], up: int) -> tuple[str, ...]:
    """Shift a whole sprite down by prepending `up` transparent rows."""
    if up <= 0:
        return grid
    return ("." * len(grid[0]),) * up + tuple(grid)


def _compose(*grids: tuple[str, ...]) -> tuple[str, ...]:
    """Overlay grids in order; later grids paint over earlier ones."""
    h = max(len(g) for g in grids)
    w = max(len(g[0]) for g in grids)
    out = []
    for y in range(h):
        row = ["."] * w
        for g in grids:
            if y >= len(g):
                continue
            line = g[y]
            for x in range(min(w, len(line))):
                if line[x] != ".":
                    row[x] = line[x]
        out.append("".join(row))
    return tuple(out)


def _pack_cells(rows: tuple[str, ...]) -> list[list[tuple[str, str | None]]]:
    """Half-block pack to a grid of (glyph, style) cells. A fully-transparent cell
    is ``(" ", None)`` so callers can composite the sprite over a background by
    skipping those cells."""
    out: list[list[tuple[str, str | None]]] = []
    width = len(rows[0]) if rows else 0
    for y in range(0, len(rows), 2):
        top = rows[y]
        bot = rows[y + 1] if y + 1 < len(rows) else "." * width
        cells: list[tuple[str, str | None]] = []
        for x in range(width):
            t = _style(top[x])
            b = _style(bot[x]) if x < len(bot) else None
            if t is None and b is None:
                cells.append((" ", None))
            elif t == b:
                cells.append(("█", t))
            elif t and b:
                cells.append(("▀", f"{t} on {b}"))
            elif t:
                cells.append(("▀", t))
            else:
                cells.append(("▄", b))
        out.append(cells)
    return out


def _pack(rows: tuple[str, ...]) -> list[Text]:
    """Compress two pixel rows into each terminal row using ▀/▄ half-blocks."""
    out: list[Text] = []
    for cells in _pack_cells(rows):
        line = Text()
        for glyph, style in cells:
            line.append(glyph, style=style)
        out.append(line)
    return out


def head_cells(*, flip: bool = False) -> list[list[tuple[str, str | None]]]:
    """The head sprite as a grid of (glyph, style) cells, for compositing the duck
    over other content (e.g. running across the splash wordmark). Static frame 0."""
    grid = DUCK_HEAD
    if flip:
        grid = tuple(row[::-1] for row in grid)
    return _pack_cells(grid)


def mini_cells(frame: int = 0, *, flip: bool = False) -> list[list[tuple[str, str | None]]]:
    """The small full-body duck (legs and all) as (glyph, style) cells, for
    compositing him over other content — e.g. waddling across the splash wordmark.

    Unlike :func:`head_cells` this includes the body and feet, and takes a
    ``frame`` so his wing flaps as he moves (the glasses stay put — see MINI_WING_OFF
    and render_mini). ``flip`` mirrors him to face the other way."""
    f = frame % FRAMES
    grid = _compose(DUCK_MINI_BASE, _shift(DUCK_MINI_WING, MINI_WING_OFF[f]), DUCK_MINI_GLASSES)
    if flip:
        grid = tuple(row[::-1] for row in grid)
    return _pack_cells(grid)


def render_full(frame: int) -> Group:
    """Full-body idle duck: wing-flap + glasses-bob for the given frame."""
    f = frame % FRAMES
    grid = _compose(DUCK_BASE, _shift(DUCK_WING, WING_OFF[f]), _shift(DUCK_GLASSES, GLASS_OFF[f]))
    return Group(*_pack(grid))


def render_mini(frame: int, *, flip: bool = False) -> Group:
    """Smaller full-body idle duck — legs and all (~11 half-block terminal rows).

    Same three layers as :func:`render_full` at a smaller trace (see MINI_WIDTH in
    scripts/gen_mascot_sprites.py), for compact placements where the full duck is
    too tall. Only the wing flaps (1px); the glasses are static at this scale.
    ``flip=True`` mirrors him to face the other way, like :func:`render_head`.
    """
    f = frame % FRAMES
    grid = _compose(DUCK_MINI_BASE, _shift(DUCK_MINI_WING, MINI_WING_OFF[f]), DUCK_MINI_GLASSES)
    if flip:
        grid = tuple(row[::-1] for row in grid)
    return Group(*_pack(grid))


# Blank pixel rows added above the crown so the raised pair has somewhere to float
# (it lifts clear of the head, leaving a green gap between the two pairs). 4 px = 2
# half-block terminal rows — the duck grows upward by that much during the gag.
_SHADES_TOP_PAD = 4
# Lift stages (in pixels) for the double-shades gag: raise to 5 so the top pair
# floats clear of the crown, hold, then drop back to 0 (== DUCK_HEAD).
SHADES_LIFT_SEQUENCE = (1, 3, 5, 5, 5, 5, 5, 3, 1, 0)


def render_head_shades(lift: int, *, flip: bool = False) -> Group:
    """The head with its sunglasses raised by ``lift`` pixels, revealing a second
    identical pair on the eyes underneath (the click-the-duck gag).

    ``lift=0`` is :data:`DUCK_HEAD` shifted down by :data:`_SHADES_TOP_PAD` (the two
    pairs coincide). As ``lift`` grows the top pair floats up off the crown while
    the static pair stays on the eyes. Always the padded height so the sprite
    doesn't change size across the animation. ``flip`` mirrors him like
    :func:`render_head`.
    """
    pad = ("." * len(DUCK_HEAD_FACE[0]),) * _SHADES_TOP_PAD
    face = pad + DUCK_HEAD_FACE
    glasses = pad + DUCK_HEAD_GLASSES
    grid = _compose(face, glasses, _shift(glasses, lift))
    if flip:
        grid = tuple(row[::-1] for row in grid)
    return Group(*_pack(grid))


def render_head(frame: int, *, flip: bool = False, beak_open: bool = False) -> Group:
    """Small head companion: a gentle breathing bob (glints are baked in).

    ``flip=True`` mirrors the duck horizontally (reverse each pixel row) so he
    can face the other way — e.g. face left toward the menu when perched on the
    right-hand side of a screen. ``beak_open=True`` uses the open-mouth variant
    (for a quack when a new tip appears).
    """
    f = frame % FRAMES
    grid = _bob(DUCK_HEAD_QUACK if beak_open else DUCK_HEAD, HEAD_BOB[f])
    if flip:
        grid = tuple(row[::-1] for row in grid)
    return Group(*_pack(grid))
