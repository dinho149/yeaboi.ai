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

# Extra letters for the robotic duck (the Agents category mascot). A separate
# dict — MASCOT_PALETTE is pinned byte-for-byte to the sprite generator's
# PALETTE by tests, and the generator's nearest-colour tracer must never snap
# duck pixels to robo steel. Letters must not collide with MASCOT_PALETTE.
ROBO_PALETTE: dict[str, tuple[int, int, int]] = {
    "C": (140, 160, 178),  # light steel plating
    "c": (88, 104, 122),  # dark steel shading
    "V": (90, 200, 230),  # cyan LED (visor eyes, antenna bulb)
}

# One lookup for _style: duck letters and robo letters share the glyph packer.
_ALL_COLOURS: dict[str, tuple[int, int, int]] = {**MASCOT_PALETTE, **ROBO_PALETTE}

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

# ---------------------------------------------------------------------------
# The robotic duck — DERIVED from the traced duck grids, never re-drawn, head
# and body alike. A per-layer letter recolor keeps the silhouette (and
# therefore every animation offset, the walk-cycle foot columns, the head's
# 16x14 footprint and the body's 18-terminal-row height) pixel-identical to the
# duck, so every placement fits both mascots with no special cases — and the
# robo stays in sync if the duck art is ever re-traced. A hand-drawn copy is
# exactly the drift this exists to prevent.
#
# The maps are deliberately separate: on BASE, `W` is the breast/tail chrome
# shine and must stay white — only the GLASSES layer's `W` glints (and the
# head's, which carries its own eye band) become cyan LED eyes. The amber bill
# is left warm, so he still reads as a duck.
# ---------------------------------------------------------------------------

_ROBO_BASE_MAP = str.maketrans({"G": "C", "g": "c"})
_ROBO_HEAD_MAP = str.maketrans({"G": "C", "g": "c", "W": "V"})
_ROBO_GLASSES_MAP = str.maketrans({"W": "V", "g": "c"})
# The mini glasses trace has no W glint (downscale artifact leaves {S, b, k});
# S→c keeps the band steel without inventing a one-pixel LED the duck lacks.
_ROBO_MINI_GLASSES_MAP = str.maketrans({"S": "c"})


def _robo_variant(grid: tuple[str, ...], table: dict[int, str]) -> tuple[str, ...]:
    """Recolor one duck layer into its robo counterpart (same geometry)."""
    return tuple(row.translate(table) for row in grid)


def _with_antenna(grid: tuple[str, ...]) -> tuple[str, ...]:
    """Swap the middle pixel of row 0's crown outline for a cyan antenna bulb.

    An in-row swap rather than an overlay — no added rows, so the sprite's
    geometry (and every test pinning it) is untouched. The `k` run is
    located, not hardcoded, so a re-traced duck can't silently misplace the bulb.
    """
    row = grid[0]
    start = row.find("k")
    if start == -1:
        return grid
    end = start
    while end < len(row) and row[end] == "k":
        end += 1
    mid = (start + end) // 2
    return (row[:mid] + "V" + row[mid + 1 :], *grid[1:])


def _robo_head(grid: tuple[str, ...]) -> tuple[str, ...]:
    """Recolour one duck head grid into its robo counterpart, antenna included."""
    return _with_antenna(_robo_variant(grid, _ROBO_HEAD_MAP))


ROBO_HEAD = _robo_head(DUCK_HEAD)
# Open-bill robo variant — the same rows 9–10 split as DUCK_HEAD_QUACK, so the
# tip-arrival "beep" animation reuses the duck's quack mechanics unchanged.
ROBO_HEAD_QUACK = _robo_head(DUCK_HEAD_QUACK)

# mascot key -> (resting head, open-beak head). The shades gag stays duck-only:
# only DUCK_HEAD splits into FACE/GLASSES layers.
_HEADS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "duck": (DUCK_HEAD, DUCK_HEAD_QUACK),
    "robo": (ROBO_HEAD, ROBO_HEAD_QUACK),
}

ROBO_BASE = _with_antenna(_robo_variant(DUCK_BASE, _ROBO_BASE_MAP))
ROBO_WING = DUCK_WING  # {L, W, k} — light plumage + white specular; already reads as metal
ROBO_GLASSES = _robo_variant(DUCK_GLASSES, _ROBO_GLASSES_MAP)
ROBO_MINI_BASE = _with_antenna(_robo_variant(DUCK_MINI_BASE, _ROBO_BASE_MAP))
ROBO_MINI_WING = DUCK_MINI_WING
ROBO_MINI_GLASSES = _robo_variant(DUCK_MINI_GLASSES, _ROBO_MINI_GLASSES_MAP)

# mascot key -> (base, wing, glasses) per size. Distinct names from
# tests/unit/test_mascot.py's _LAYERS/_MINI_LAYERS lists, which pin the
# AUTO-GENERATED duck grids to the duck-only palette.
_BODY_GRIDS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "duck": (DUCK_BASE, DUCK_WING, DUCK_GLASSES),
    "robo": (ROBO_BASE, ROBO_WING, ROBO_GLASSES),
}
_MINI_GRIDS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "duck": (DUCK_MINI_BASE, DUCK_MINI_WING, DUCK_MINI_GLASSES),
    "robo": (ROBO_MINI_BASE, ROBO_MINI_WING, ROBO_MINI_GLASSES),
}

# Per-frame vertical offsets (pixels). Positive = lift the layer up.
WING_OFF = (0, 1, 2, 2, 1, 0, 0, 0)  # gentle wing flap
GLASS_OFF = (0, 0, 0, 1, 1, 1, 0, 0)  # slow glasses bob
HEAD_BOB = (0, 0, 1, 1, 1, 0, 0, 0)  # head breathing bob (shift down)
# Mini-duck flap. The full-body offsets (up to 2px) are proportionally too large
# for the 22px trace — a 1px flap keeps the wing attached to the body. The mini
# glasses stay put: at this scale a bob detaches the lens by a whole half-block.
MINI_WING_OFF = (0, 0, 1, 1, 1, 0, 0, 0)


def _style(letter: str) -> str | None:
    rgb = _ALL_COLOURS.get(letter)
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


def head_cells(*, flip: bool = False, mascot: str = "duck") -> list[list[tuple[str, str | None]]]:
    """The head sprite as a grid of (glyph, style) cells, for compositing the duck
    over other content (e.g. running across the splash wordmark). Static frame 0.
    ``mascot`` picks the sprite family: "duck" (default) or "robo" (Agents)."""
    grid = _HEADS.get(mascot, _HEADS["duck"])[0]
    if flip:
        grid = tuple(row[::-1] for row in grid)
    return _pack_cells(grid)


def mini_cells(frame: int = 0, *, flip: bool = False, mascot: str = "duck") -> list[list[tuple[str, str | None]]]:
    """The small full-body duck (legs and all) as (glyph, style) cells, for
    compositing him over other content — e.g. waddling across the splash wordmark.

    Unlike :func:`head_cells` this includes the body and feet, and takes a
    ``frame`` so his wing flaps as he moves (the glasses stay put — see MINI_WING_OFF
    and render_mini). ``flip`` mirrors him to face the other way. ``mascot`` picks
    the sprite family ("duck" default, "robo" for the Agents side)."""
    f = frame % FRAMES
    base, wing, glasses = _MINI_GRIDS.get(mascot, _MINI_GRIDS["duck"])
    grid = _compose(base, _shift(wing, MINI_WING_OFF[f]), glasses)
    if flip:
        grid = tuple(row[::-1] for row in grid)
    return _pack_cells(grid)


def _at_x(grid: tuple[str, ...], x: int, total: int) -> tuple[str, ...]:
    """Pad a pixel grid to ``total`` columns with the sprite starting at ``x``."""
    return tuple("." * x + row + "." * (total - x - len(row)) for row in grid)


def flock_cells(
    frame: int = 0, *, count: int = 3, stride: int = 6, mascot: str = "duck"
) -> list[list[tuple[str, str | None]]]:
    """A row of ``count`` mini mascots as one cell grid — the Team card's trio.

    Each neighbour stands ``stride`` pixels along, wings offset a few frames so
    the group isn't flapping in lock-step, and the middle one is painted last so
    it stands in front where the sprites overlap. At the defaults the trio is
    22 + 2×6 = 34 pixels wide — the same footprint as the full-body sprite.
    """
    base, wing, glasses = _MINI_GRIDS.get(mascot, _MINI_GRIDS["duck"])
    total = len(base[0]) + stride * (count - 1)
    order = [i for i in range(count) if i != count // 2] + [count // 2]
    layers = []
    for i in order:
        f = (frame + i * 3) % FRAMES
        grid = _compose(base, _shift(wing, MINI_WING_OFF[f]), glasses)
        layers.append(_at_x(grid, i * stride, total))
    return _pack_cells(_compose(*layers))


def flock_head_cells(
    frame: int = 0, *, count: int = 3, stride: int = 3, mascot: str = "duck"
) -> list[list[tuple[str, str | None]]]:
    """The trio as overlapped heads — the Team card's crowd when a full-width
    flock doesn't fit. At the defaults it is 16 + 2×3 = 22 pixels wide, the same
    footprint as the mini sprite. The front head quacks on the ``frame`` clock so
    the crowd reads as alive rather than a decal."""
    head, quack = _HEADS.get(mascot, _HEADS["duck"])[:2]
    total = len(head[0]) + stride * (count - 1)
    order = [i for i in range(count) if i != count // 2] + [count // 2]
    front = count // 2
    layers = [_at_x(quack if i == front and frame % 4 < 2 else head, i * stride, total) for i in order]
    return _pack_cells(_compose(*layers))


def render_full(frame: int, *, mascot: str = "duck") -> Group:
    """Full-body idle duck: wing-flap + glasses-bob for the given frame."""
    f = frame % FRAMES
    base, wing, glasses = _BODY_GRIDS.get(mascot, _BODY_GRIDS["duck"])
    # The robo's visor is bolted on — no bob (the duck adjusts his sunglasses).
    glass_off = GLASS_OFF[f] if mascot == "duck" else 0
    grid = _compose(base, _shift(wing, WING_OFF[f]), _shift(glasses, glass_off))
    return Group(*_pack(grid))


def full_cells(
    frame: int, *, glasses_frame: int | None = None, flip: bool = False, mascot: str = "duck"
) -> list[list[tuple[str, str | None]]]:
    """The full-size idle duck (wing-flap + glasses-bob) as (glyph, style) cells, for
    compositing him over other content — e.g. walking along the saver floor.

    ``glasses_frame`` drives the glasses bob independently of the wing ``frame`` (so
    the glasses can hold still while the wings flap); defaults to ``frame``. The bob
    is duck-only — a bolted visor doesn't wiggle, so the robo ignores it."""
    f = frame % FRAMES
    gf = (frame if glasses_frame is None else glasses_frame) % FRAMES
    base, wing, glasses = _BODY_GRIDS.get(mascot, _BODY_GRIDS["duck"])
    glass_off = GLASS_OFF[gf] if mascot == "duck" else 0
    grid = _compose(base, _shift(wing, WING_OFF[f]), _shift(glasses, glass_off))
    if flip:
        grid = tuple(row[::-1] for row in grid)
    return _pack_cells(grid)


# Orange foot columns in the full duck's bottom row (see full_cells): the two feet
# the walk cycle steps by dropping each in turn (half-block ▀→▄, the pixel lowers).
_FULL_LEFT_FOOT = (11, 12, 13, 14, 15, 16)
_FULL_RIGHT_FOOT = (17, 18, 19, 20, 21, 22, 23)


def _step_foot(cell: tuple[str, str | None]) -> tuple[str, str | None]:
    """Lower a foot half-block: swap the top pixel glyph ▀ for the bottom ▄ (and back)
    so the orange foot appears to plant/step, keeping the cell's colour."""
    glyph, style = cell
    if glyph == "▀":
        return ("▄", style)
    if glyph == "▄":
        return ("▀", style)
    return cell


def walk_cells(
    frame: int, *, foot: int | None = None, glasses_frame: int = 0, flip: bool = False, mascot: str = "duck"
) -> list[list[tuple[str, str | None]]]:
    """The full-size duck mid-walk: wing flap (from ``frame``) plus an alternating
    foot plant, as (glyph, style) cells for compositing him moving along a surface.

    ``foot`` (its own slow phase, so the steps aren't tied to the fast wing frame)
    plants the left/right foot in turn on even/odd — defaults to ``frame``. The
    glasses hold still by default (``glasses_frame=0``) — pass a live frame to bob
    them (e.g. only while jumping). ``flip`` mirrors him to face his travel direction.
    The foot columns are valid for every mascot: robo grids are letter-recolors of
    the duck trace, so the feet sit in the same columns by construction."""
    grid = [list(row) for row in full_cells(frame, glasses_frame=glasses_frame, flip=False, mascot=mascot)]
    last = len(grid) - 1  # feet occupy the bottom row
    step = frame if foot is None else foot
    down = _FULL_LEFT_FOOT if step % 2 == 0 else _FULL_RIGHT_FOOT
    for col in down:
        if 0 <= col < len(grid[last]):
            grid[last][col] = _step_foot(grid[last][col])
    if flip:
        grid = [row[::-1] for row in grid]
    return grid


def render_mini(frame: int, *, flip: bool = False, mascot: str = "duck") -> Group:
    """Smaller full-body idle duck — legs and all (~11 half-block terminal rows).

    Same three layers as :func:`render_full` at a smaller trace (see MINI_WIDTH in
    scripts/gen_mascot_sprites.py), for compact placements where the full duck is
    too tall. Only the wing flaps (1px); the glasses are static at this scale.
    ``flip=True`` mirrors him to face the other way, like :func:`render_head`.
    """
    f = frame % FRAMES
    base, wing, glasses = _MINI_GRIDS.get(mascot, _MINI_GRIDS["duck"])
    grid = _compose(base, _shift(wing, MINI_WING_OFF[f]), glasses)
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


def render_head_idle(frame: int, lift: int | None = None, *, flip: bool = False, mascot: str = "duck") -> Group:
    """Resting head and shades gag at one common height.

    :func:`render_head_shades` pads :data:`_SHADES_TOP_PAD` rows above the crown
    so the raised pair has somewhere to float; :func:`render_head` does not. Cut
    between the two mid-animation and the duck drops two terminal rows at the
    moment the gag ends, which is the one thing a loop cannot hide. So anything
    that plays the gag *occasionally* has to draw its resting frames at the
    padded height too — that is all this is.

    ``lift=None`` rests, with the usual breathing bob; anything else plays the
    gag at that lift. The gag is duck-only (only DUCK_HEAD has FACE/GLASSES
    layers), so a non-duck ``mascot`` always rests — same padded height, so
    swapping mascots never changes the sprite's geometry.
    """
    if lift is not None and mascot == "duck":
        return render_head_shades(lift, flip=flip)
    head = _HEADS.get(mascot, _HEADS["duck"])[0]
    pad = ("." * len(DUCK_HEAD_FACE[0]),) * _SHADES_TOP_PAD
    grid = pad + _bob(head, HEAD_BOB[frame % FRAMES])
    if flip:
        grid = tuple(row[::-1] for row in grid)
    return Group(*_pack(grid))


def render_head(frame: int, *, flip: bool = False, beak_open: bool = False, mascot: str = "duck") -> Group:
    """Small head companion: a gentle breathing bob (glints are baked in).

    ``flip=True`` mirrors the duck horizontally (reverse each pixel row) so he
    can face the other way — e.g. face left toward the menu when perched on the
    right-hand side of a screen. ``beak_open=True`` uses the open-mouth variant
    (for a quack when a new tip appears — the robo's bill "beeps" the same way).
    ``mascot`` picks the sprite family: "duck" (default) or "robo" (Agents).
    """
    f = frame % FRAMES
    resting, quacking = _HEADS.get(mascot, _HEADS["duck"])
    grid = _bob(quacking if beak_open else resting, HEAD_BOB[f])
    if flip:
        grid = tuple(row[::-1] for row in grid)
    return Group(*_pack(grid))
