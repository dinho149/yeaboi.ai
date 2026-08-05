"""Emit font-free markup for the landing page's terminal mockup.

A browser draws U+2580-259F with the *font*; iTerm2/Kitty/Alacritty ignore the
font for those and fill the cell procedurally, which is why block art looks
solid in a terminal and mottled in a browser. So we do what the terminals do:
the shapes become geometry, not glyphs.

  - mode titles  -> an SVG mask (one rect per half-cell), so the existing
                    colour + shimmer CSS still paints it
  - the duck     -> a grid of cells whose background carries both halves
                    (the old markup kept only the foreground, losing the
                    bottom half of every two-colour cell)
"""

from urllib.parse import quote

from yeaboi.ui.shared._ascii_font import render_ascii_text
from yeaboi.ui.shared._mascot import head_cells

TITLES = [
    "Analysis",
    "Planning",
    "Standup",
    "Retro",
    "Poker",
    "Performance",
    "Reporting",
    "Usage",
    "Settings",
]

# A terminal cell is one advance wide and two half-rows tall.
_FILL = {"█": (0, 2), "▀": (0, 1), "▄": (1, 1)}  # glyph -> (top offset, height) in half-cells


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


def duck() -> str:
    out = []
    for row in head_cells(flip=True):
        cells = []
        for glyph, style in row:
            if style is None:
                cells.append("<i></i>")
                continue
            if " on " in style:
                fg, bg = style.split(" on ")
            elif glyph == "█":
                fg = bg = style
            elif glyph == "▀":
                fg, bg = style, "transparent"
            else:
                fg, bg = "transparent", style
            if fg == bg:
                cells.append(f'<i style="background:{fg}"></i>')
            else:
                cells.append(f'<i style="background:linear-gradient(to bottom,{fg} 50%,{bg} 50%)"></i>')
        out.append('<span class="r">' + "".join(cells) + "</span>")
    return "\n".join(out)


if __name__ == "__main__":
    for t in TITLES:
        url, cols = mask_for(t)
        print(f"=== {t} cols={cols}")
        print(url)
    print("=== DUCK")
    print(duck())
