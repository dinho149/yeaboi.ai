"""Regenerate the frozen mascot sprite layers from the website art.

Dev tool — NOT imported at runtime (keeps Pillow out of the shipped app).
Run from the repo root:  uv run python scripts/gen_mascot_sprites.py
"""

import sys
from pathlib import Path

# scripts/ is not a package, and these modules are also loaded by path in tests,
# where sys.path[0] is not this directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _sibling_repos import desktop_brand, site_assets  # noqa: E402
from PIL import Image

# Letter -> rgb. MUST stay identical to MASCOT_PALETTE in _mascot.py.
PALETTE = {
    "k": (9, 14, 18),  # outline
    "o": (26, 32, 40),  # sunglass lens
    "G": (34, 158, 122),  # head green
    "g": (22, 110, 92),  # head green shadow
    "W": (232, 240, 238),  # glint / belly white
    "L": (150, 190, 190),  # body light blue-grey
    "M": (96, 140, 144),  # body mid blue-grey
    "S": (60, 100, 108),  # body shadow
    "b": (250, 176, 44),  # bill / feet
    "r": (228, 104, 22),  # orange shadow
}
LETTERS = list(PALETTE)
RGBS = [PALETTE[c] for c in LETTERS]
# The robo steels (ROBO_PALETTE in _mascot.py): the persona costumes are drawn in
# them too, so a costume trace snaps against both dicts. Kept out of PALETTE,
# which the duck layers and test_mascot pin.
PERSONA_EXTRA = {
    "C": (140, 160, 178),  # steel light
    "c": (88, 104, 122),  # steel dark
    "V": (90, 200, 230),  # cyan LED
}
PERSONA_IDS = ("engineer", "teacher", "martial", "chef", "astronaut", "dj", "detective", "wizard")
# The paper's figure: 18 cells wide packs to 13 terminal rows with the hat room.
PERSONA_WIDTH = 18
# The desktop's costume layers sit on a 128x136 duck with 40px above the crown.
COSTUME_SIZE = (128, 176)
COSTUME_HEADROOM = 40
# A dressed robo is a steel ramp; snapping it against the duck greens lands on
# the body mid-tone and comes out teal, so it only sees the steels and the fixtures.
ROBO_LETTERS = ("k", "o", "W", "b", "r", "C", "c", "V")
WIDTH = 34
# A second, smaller full-body trace (legs and all) for compact placements. 22px
# is the smallest width where the sunglasses, bill, wing and both feet survive the
# NEAREST downscale cleanly (18px muddies the glasses; see render_mini in
# _mascot.py). Frozen as DUCK_MINI_{BASE,WING,GLASSES}.
MINI_WIDTH = 22
# The master art is a served asset of the website; resolved on use, not at
# import, because tests/unit/test_mascot.py loads this module. See _site_repo.py.
OUT = Path("src/yeaboi/ui/shared/_mascot_sprites.py")
PERSONA_OUT = Path("src/yeaboi/ui/shared/_persona_sprites.py")


def nearest(r, g, b):
    return LETTERS[
        min(range(len(RGBS)), key=lambda i: (RGBS[i][0] - r) ** 2 + (RGBS[i][1] - g) ** 2 + (RGBS[i][2] - b) ** 2)
    ]


def nearest_in(palette):
    """A ``nearest`` over any letter -> rgb dict."""
    letters = list(palette)
    rgbs = [palette[c] for c in letters]

    def _nearest(r, g, b):
        return letters[min(range(len(rgbs)), key=lambda i: sum((rgbs[i][k] - v) ** 2 for k, v in enumerate((r, g, b))))]

    return _nearest


def despeckle(grid, min_size=3):
    """Drop connected islands smaller than min_size (8-connectivity)."""
    h = len(grid)
    w = len(grid[0])
    seen = [[False] * w for _ in range(h)]
    keep = [[False] * w for _ in range(h)]
    for sy in range(h):
        for sx in range(w):
            if grid[sy][sx] == "." or seen[sy][sx]:
                continue
            stack = [(sx, sy)]
            comp = []
            seen[sy][sx] = True
            while stack:
                x, y = stack.pop()
                comp.append((x, y))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < w and 0 <= ny < h and not seen[ny][nx] and grid[ny][nx] != ".":
                            seen[ny][nx] = True
                            stack.append((nx, ny))
            if len(comp) >= min_size:
                for x, y in comp:
                    keep[y][x] = True
    return ["".join(grid[y][x] if keep[y][x] else "." for x in range(w)) for y in range(h)]


def trace_image(im, width, palette=PALETTE):
    """A right-facing RGBA image as a letter grid ``width`` cells wide, even-height."""
    w, h = im.size
    height = round(width * h / w)
    if height % 2:
        height += 1
    snap = nearest_in(palette)
    # Flatten (snap to palette) FIRST, then NEAREST downscale so blocks stay solid.
    src = im.load()
    flat = Image.new("RGBA", (w, h))
    fp = flat.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = src[x, y]
            fp[x, y] = (*palette[snap(r, g, b)], 255) if a >= 128 else (0, 0, 0, 0)
    small = flat.resize((width, height), Image.NEAREST).load()
    grid = []
    for y in range(height):
        grid.append("".join(snap(*small[x, y][:3]) if small[x, y][3] >= 128 else "." for x in range(width)))
    return despeckle(grid)


def _site_layer(name):
    return Image.open(site_assets() / f"{name}.png").convert("RGBA")


def trace(name, width=WIDTH):
    return trace_image(_site_layer(name).transpose(Image.FLIP_LEFT_RIGHT), width)


def _costume(name):
    return Image.open(desktop_brand() / f"{name}.png").convert("RGBA")


def composite_persona(pid):
    """The duck in a costume, at the desktop's size and facing: site layers under the costume layers."""
    canvas = Image.new("RGBA", COSTUME_SIZE)
    duck_size = (COSTUME_SIZE[0], COSTUME_SIZE[1] - COSTUME_HEADROOM)
    layers = [_site_layer("duck-base").resize(duck_size, Image.NEAREST)]
    if pid == "martial":
        layers.append(_costume("persona-martial-body").crop((0, COSTUME_HEADROOM, *COSTUME_SIZE)))
    layers += [
        _site_layer("duck-wing").resize(duck_size, Image.NEAREST),
        _site_layer("duck-glasses").resize(duck_size, Image.NEAREST),
    ]
    for layer in layers:
        canvas.alpha_composite(layer, (0, COSTUME_HEADROOM))
    canvas.alpha_composite(_costume(f"persona-{pid}"))
    return canvas


def trace_persona(pid, width=PERSONA_WIDTH):
    im = composite_persona(pid).transpose(Image.FLIP_LEFT_RIGHT)
    return trace_image(im, width, {**PALETTE, **PERSONA_EXTRA})


def trace_robo_persona(pid, width=PERSONA_WIDTH):
    im = _costume(f"robo-{pid}").transpose(Image.FLIP_LEFT_RIGHT)
    palette = {**PALETTE, **PERSONA_EXTRA}
    return trace_image(im, width, {letter: palette[letter] for letter in ROBO_LETTERS})


def emit(name, grid):
    body = ",\n    ".join(f'"{row}"' for row in grid)
    return f"{name} = (\n    {body},\n)\n"


def main():
    parts = ["# AUTO-GENERATED by scripts/gen_mascot_sprites.py — do not edit by hand.\n\n"]
    for const, asset in (("DUCK_BASE", "duck-base"), ("DUCK_WING", "duck-wing"), ("DUCK_GLASSES", "duck-glasses")):
        parts.append(emit(const, trace(asset)))
        parts.append("\n")
    # Smaller full-body trace (legs and all) — same layers at MINI_WIDTH.
    for const, asset in (
        ("DUCK_MINI_BASE", "duck-base"),
        ("DUCK_MINI_WING", "duck-wing"),
        ("DUCK_MINI_GLASSES", "duck-glasses"),
    ):
        parts.append(emit(const, trace(asset, MINI_WIDTH)))
        parts.append("\n")
    # The loop puts a blank line AFTER each block, including the last — which
    # `ruff format --check` rejects as a trailing blank line, so trim it here
    # rather than leaving every regeneration to fail CI.
    OUT.write_text("".join(parts).rstrip("\n") + "\n")
    print("wrote", OUT)

    parts = [
        "# AUTO-GENERATED by scripts/gen_mascot_sprites.py — do not edit by hand.\n",
        "# The eight persona costumes from yeaboi-desktop's brand art, on the duck and on the robo.\n\n",
        f"PERSONA_WIDTH = {PERSONA_WIDTH}\n\n",
    ]
    for const, tracer in (("PERSONAS", trace_persona), ("ROBO_PERSONAS", trace_robo_persona)):
        parts.append(f"{const} = {{\n")
        for pid in PERSONA_IDS:
            body = ",\n        ".join(f'"{row}"' for row in tracer(pid))
            parts.append(f'    "{pid}": (\n        {body},\n    ),\n')
        parts.append("}\n\n")
    PERSONA_OUT.write_text("".join(parts).rstrip("\n") + "\n")
    print("wrote", PERSONA_OUT)


if __name__ == "__main__":
    main()
