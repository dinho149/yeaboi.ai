"""Single source of truth for the Yeaboi duck mascot sprite.

# See docs: "TUI system" — the mascot renders as chunky half-block (▀/▄) pixel
# art. Full-body layers (base/wing/glasses) are traced offline and frozen in
# _mascot_sprites.py; the small head companion is hand-authored below. Animation
# is pure: compose the layers at a per-frame offset. No timing or IO lives here.
"""

from __future__ import annotations

from rich.console import Group
from rich.text import Text

from yeaboi.ui.shared._mascot_sprites import DUCK_BASE, DUCK_GLASSES, DUCK_WING

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

# Per-frame vertical offsets (pixels). Positive = lift the layer up.
WING_OFF = (0, 1, 2, 2, 1, 0, 0, 0)  # gentle wing flap
GLASS_OFF = (0, 0, 0, 1, 1, 1, 0, 0)  # slow glasses bob
HEAD_BOB = (0, 0, 1, 1, 1, 0, 0, 0)  # head breathing bob (shift down)


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


def _pack(rows: tuple[str, ...]) -> list[Text]:
    """Compress two pixel rows into each terminal row using ▀/▄ half-blocks."""
    out: list[Text] = []
    width = len(rows[0]) if rows else 0
    for y in range(0, len(rows), 2):
        top = rows[y]
        bot = rows[y + 1] if y + 1 < len(rows) else "." * width
        line = Text()
        for x in range(width):
            t = _style(top[x])
            b = _style(bot[x]) if x < len(bot) else None
            if t is None and b is None:
                line.append(" ")
            elif t == b:
                line.append("█", style=t)
            elif t and b:
                line.append("▀", style=f"{t} on {b}")
            elif t:
                line.append("▀", style=t)
            else:
                line.append("▄", style=b)
        out.append(line)
    return out


def render_full(frame: int) -> Group:
    """Full-body idle duck: wing-flap + glasses-bob for the given frame."""
    f = frame % FRAMES
    grid = _compose(DUCK_BASE, _shift(DUCK_WING, WING_OFF[f]), _shift(DUCK_GLASSES, GLASS_OFF[f]))
    return Group(*_pack(grid))


def render_head(frame: int, *, flip: bool = False) -> Group:
    """Small head companion: a gentle breathing bob (glints are baked in).

    ``flip=True`` mirrors the duck horizontally (reverse each pixel row) so he
    can face the other way — e.g. face left toward the menu when perched on the
    right-hand side of a screen.
    """
    f = frame % FRAMES
    grid = _bob(DUCK_HEAD, HEAD_BOB[f])
    if flip:
        grid = tuple(row[::-1] for row in grid)
    return Group(*_pack(grid))
