"""Emit font-free markup and sprite data for the landing page's terminal mockup.

A browser draws U+2580-259F with the *font*; iTerm2/Kitty/Alacritty ignore the
font for those and fill the cell procedurally, which is why block art looks
solid in a terminal and mottled in a browser. So we do what the terminals do:
the shapes become geometry, not glyphs.

  - mode titles  -> an SVG mask (one rect per half-cell), so the existing
                    colour + shimmer CSS still paints it
  - the duck     -> a grid of cells whose background carries both halves
                    (markup that keeps only the foreground loses the bottom
                    half of every two-colour cell)
  - the gag      -> the same grid at each lift of SHADES_LIFT_SEQUENCE, encoded
                    small enough to sit in site.js and be rebuilt on click

Run it and paste; nothing here writes to docs/ on its own.
"""

from urllib.parse import quote

from yeaboi.ui.shared._ascii_font import render_ascii_text
from yeaboi.ui.shared._mascot import (
    _SHADES_TOP_PAD,
    DUCK_HEAD_FACE,
    DUCK_HEAD_GLASSES,
    FRAMES,
    MASCOT_PALETTE,
    SHADES_LIFT_SEQUENCE,
    _compose,
    _pack_cells,
    _shift,
    full_cells,
    head_cells,
)

TITLES = [
    # The Humans menu, in _MODE_CARDS order.
    "Analysis",
    "Planning",
    "Standup",
    "Retro",
    "Poker",
    "Performance",
    "Reporting",
    "Usage",
    "Settings",
    # The landing split's two world-cards.
    "Humans",
    "Agents",
    # The Agents menu (_AGENT_CARDS); Usage and Standup reuse the masks above.
    "Security",
]

# A terminal cell is one advance wide and two half-rows tall.
_FILL = {"█": (0, 2), "▀": (0, 1), "▄": (1, 1)}  # glyph -> (top offset, height) in half-cells

# Stable index per palette colour, so the encoded frames below are just digits.
_COLOURS = [f"rgb({r},{g},{b})" for r, g, b in MASCOT_PALETTE.values()]
_INDEX = {c: i for i, c in enumerate(_COLOURS)}


def mask_for(text: str) -> tuple[str, int]:
    rows = render_ascii_text(text)
    cols = max(len(r) for r in rows)
    parts = []
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            fill = _FILL.get(ch)
            if fill is None:  # ░ and space are this font's negative space
                continue
            top, h = fill
            parts.append(f"M{x} {y * 2 + top}h1v{h}h-1z")
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {cols} {len(rows) * 2}' "
        f"preserveAspectRatio='none'><path fill='#fff' d='{''.join(parts)}'/></svg>"
    )
    # The URL sits bare inside url() in a style="" attribute, so nothing may be
    # left raw that either grammar reserves: an unquoted CSS url() token forbids
    # quotes, parens and whitespace, and " would close the attribute.
    return "data:image/svg+xml," + quote(svg, safe="/:=?"), cols


def shade_cells(lift: int) -> list[list[tuple[str, str | None]]]:
    """The head with its shades raised by *lift*, as (glyph, style) cells.

    Mirrors render_head_shades — including the top padding, which every frame
    carries so the sprite's box never changes size mid-animation.
    """
    pad = ("." * len(DUCK_HEAD_FACE[0]),) * _SHADES_TOP_PAD
    face = pad + DUCK_HEAD_FACE
    glasses = pad + DUCK_HEAD_GLASSES
    grid = _compose(face, glasses, _shift(glasses, lift))
    return _pack_cells(tuple(row[::-1] for row in grid))  # flip: he faces the menu


def _halves(glyph: str, style: str | None) -> tuple[str | None, str | None]:
    """A cell's (top, bottom) colours. _pack_cells packs two source pixels into
    one cell, so a ▀ carries the top colour as foreground and the bottom as
    background — reading only the foreground throws half the sprite away."""
    if style is None:
        return None, None
    if " on " in style:
        top, bot = style.split(" on ")
        return top, bot
    if glyph == "█":
        return style, style
    if glyph == "▀":
        return style, None
    return None, style


def cells_html(cells: list[list[tuple[str, str | None]]]) -> str:
    """A packed cell grid as the landing page's `<span class="r"><i>` markup.

    Shared by the corner duck and by the landing split's two full-body mascots —
    the encoding is the same, only the source grid differs.
    """
    out = []
    for row in cells:
        rendered = []
        for glyph, style in row:
            top, bot = _halves(glyph, style)
            if top is None and bot is None:
                rendered.append("<i></i>")
            elif top == bot:
                rendered.append(f'<i style="background:{top}"></i>')
            else:
                a, b = top or "transparent", bot or "transparent"
                rendered.append(f'<i style="background:linear-gradient(to bottom,{a} 50%,{b} 50%)"></i>')
        out.append('<span class="r">' + "".join(rendered) + "</span>")
    return "\n".join(out)


def duck_html(lift: int = 0) -> str:
    return cells_html(shade_cells(lift))


def head_html(mascot: str = "duck") -> str:
    """The corner companion's head, as the foot of a mode menu shows it.

    render_head(flip=True) — he faces the list. The duck's copy is the shades
    gag's starting frame and gets repainted by site.js; the robo's is static
    (his visor is bolted on, so there is no gag to drive).
    """
    return cells_html(head_cells(flip=True, mascot=mascot))


def full_html(mascot: str = "duck", frame: int = 0) -> str:
    """The 18-row full-body mascot the landing split shows in each world-card.

    Straight out of full_cells(), the same function the real screen calls
    (_screens_category.py), so the page shows the app's own pixels rather than
    an approximation of them. Frame 0 is the resting wing position.
    """
    return cells_html(full_cells(frame, mascot=mascot))


def frames_js() -> str:
    """Palette + one string per distinct lift, for site.js to rebuild on click.

    Two chars per cell (top index, bottom index), '.' for a transparent half,
    rows separated by '|'. ~1.3KB for the whole gag, versus four more copies of
    the markup in the page.
    """
    lifts = sorted(set(SHADES_LIFT_SEQUENCE))
    frames = {}
    for lift in lifts:
        rows = []
        for row in shade_cells(lift):
            rows.append("".join("".join("." if c is None else str(_INDEX[c]) for c in _halves(g, s)) for g, s in row))
        frames[lift] = "|".join(rows)
    pal = ",".join(f"'{c}'" for c in _COLOURS)
    body = ",".join(f"{lift}:'{frames[lift]}'" for lift in lifts)
    seq = ",".join(str(n) for n in SHADES_LIFT_SEQUENCE)
    return f"var DUCK_PALETTE = [{pal}];\nvar DUCK_FRAMES = {{{body}}};\nvar DUCK_LIFTS = [{seq}];"


# One char per palette index, so a cell stays exactly two chars however many
# colours the two mascots need between them (the duck gag's single-digit scheme
# tops out at ten, and duck + robo together exceed that).
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def world_frames_js() -> str:
    """Palette + the full-body flap cycle for both mascots, for site.js to paint.

    Same reasoning as :func:`frames_js`, and much more sharply: one full-body
    sprite is ~430 cells, so emitting the cycle as markup would add ~57KB to the
    landing page. Encoded, the whole thing is a few KB.

    Frames are deduplicated by content — WING_OFF only has three distinct
    offsets and the robo's visor never bobs, so the eight-frame cycle is really
    six sprites for the duck and three for the robo. ``WORLD_CYCLE`` maps frame
    number to the sprite that frame shows.
    """
    palette: list[str] = []
    index: dict[str, int] = {}
    sprites: list[str] = []
    seen: dict[str, int] = {}
    cycles: dict[str, list[int]] = {}

    for mascot in ("duck", "robo"):
        cycle = []
        for f in range(FRAMES):
            rows = []
            for row in full_cells(f, mascot=mascot):
                chars = []
                for glyph, style in row:
                    for half in _halves(glyph, style):
                        if half is None:
                            chars.append(".")
                            continue
                        if half not in index:
                            index[half] = len(palette)
                            palette.append(half)
                        chars.append(_ALPHABET[index[half]])
                rows.append("".join(chars))
            encoded = "|".join(rows)
            if encoded not in seen:
                seen[encoded] = len(sprites)
                sprites.append(encoded)
            cycle.append(seen[encoded])
        cycles[mascot] = cycle

    if len(palette) > len(_ALPHABET):
        raise ValueError(f"palette of {len(palette)} exceeds the {len(_ALPHABET)}-char alphabet")

    pal = ",".join(f"'{c}'" for c in palette)
    body = ",".join(f"'{s}'" for s in sprites)
    cyc = ",".join(f"{m}:[{','.join(str(n) for n in c)}]" for m, c in cycles.items())
    return (
        f"var WORLD_ALPHABET = '{_ALPHABET}';\n"
        f"var WORLD_PALETTE = [{pal}];\n"
        f"var WORLD_SPRITES = [{body}];\n"
        f"var WORLD_CYCLE = {{{cyc}}};"
    )


if __name__ == "__main__":
    for t in TITLES:
        url, cols = mask_for(t)
        print(f"=== {t} cols={cols}")
        print(url)
    print("=== DUCK (lift 0, the resting frame)")
    print(duck_html(0))
    print("=== FRAMES")
    print(frames_js())
    print("=== WORLD FRAMES (the landing split's two full-body mascots)")
    print(world_frames_js())
