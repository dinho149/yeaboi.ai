"""Two-line block-character ASCII font, ported from th's ascii_font.rs.

Each letter is a 2-element list of strings (top line, bottom line).
Characters use █ ▀ ▄ ░ block drawing chars for a compact, bold look.

This is the product's own display typeface. The TUI sets every mode title in it,
yeaboi.ai's hero recreates it in the browser, and the tunnel pages render it via
``<Wordmark>``. The table is exported to TypeScript by ``scripts/gen_web_types``
rather than hand-copied, so the web cannot drift from the terminal.
"""

from __future__ import annotations

# fmt: off
_LETTERS: dict[str, list[str]] = {
    "A": ["▄▀█", "█▀█"],
    "B": ["█▀▄", "█▄█"],
    "C": ["█▀▀", "█▄▄"],
    "D": ["█▀▄", "█▄▀"],
    "E": ["█▀▀", "██▄"],
    "F": ["█▀▀", "█▀░"],
    "G": ["█▀▀", "█▄█"],
    "H": ["█░█", "█▀█"],
    "I": ["█", "█"],
    "J": ["░░█", "█▄█"],
    "K": ["█▄▀", "█░█"],
    "L": ["█░░", "█▄▄"],
    "M": ["█▀▄▀█", "█░▀░█"],
    "N": ["█▄░█", "█░▀█"],
    "O": ["█▀█", "█▄█"],
    "P": ["█▀█", "█▀▀"],
    "Q": ["█▀█", "█▄▀"],
    "R": ["█▀█", "█▀▄"],
    "S": ["█▀", "▄█"],
    "T": ["▀█▀", "░█░"],
    "U": ["█░█", "█▄█"],
    "V": ["█░█", "▀▄▀"],
    "W": ["█░█░█", "▀▄▀▄▀"],
    "X": ["▀▄▀", "█░█"],
    "Y": ["█▄█", "░█░"],
    "Z": ["▀█", "█▄"],
    " ": ["░", "░"],
    "0": ["█▀█", "█▄█"],
    "1": ["▄█", "░█"],
    "2": ["▀▀█", "█▄▄"],
    "3": ["▀▀█", "▄▄█"],
    "4": ["█░█", "░▀█"],
    "5": ["█▀▀", "▄▄█"],
    "6": ["█▀▀", "█▄█"],
    "7": ["▀▀█", "░░█"],
    "8": ["█▀█", "█▄█"],
    "9": ["█▀█", "▀▀█"],
}
# fmt: on

# Public alias. The table itself stays underscore-private because nothing should
# reach past render_ascii_text() to draw with it; the codegen is the one caller
# that legitimately needs the raw glyphs, and naming that seam is better than
# having scripts/gen_web_types.py import a private.
BLOCK_GLYPHS: dict[str, list[str]] = _LETTERS


def render_ascii_text(text: str) -> list[str]:
    """Render *text* as two lines of block-character ASCII art.

    Returns a 2-element list: [top_line, bottom_line].
    Unknown characters are replaced with 3-space gaps.
    """
    lines = ["", ""]
    for ch in text.upper():
        letter = _LETTERS.get(ch)
        if letter:
            lines[0] += letter[0] + " "
            lines[1] += letter[1] + " "
        else:
            lines[0] += "   "
            lines[1] += "   "
    return [line.rstrip() for line in lines]


# Each half-block cell packs two vertical pixels, each at one of three levels:
# FULL (solid), LIGHT (the ░ shade) or EMPTY. Decoding to this 3-level grid — not
# 1-bit — lets the upscaler carry the ░ light-shade through, so the enlarged font
# keeps the menu titles' pixel texture (the ░ dots in counters/gaps) rather than
# flattening it away.
_FULL, _LIGHT, _EMPTY = 2, 1, 0
_HALF_PIXELS = {
    "█": (_FULL, _FULL),
    "▀": (_FULL, _EMPTY),
    "▄": (_EMPTY, _FULL),
    "░": (_LIGHT, _LIGHT),
    " ": (_EMPTY, _EMPTY),
}


def _pack_pixels(top: int, bot: int) -> str:
    """Re-encode a vertical pixel pair (each FULL/LIGHT/EMPTY) to one glyph."""
    if top == _FULL and bot == _FULL:
        return "█"
    if top == _FULL:
        return "▀"
    if bot == _FULL:
        return "▄"
    if _LIGHT in (top, bot):
        return "░"
    return " "


def scale_halfblock_lines(lines: list[str], scale: int = 2) -> list[str]:
    """Scale a half-block ASCII-art block (e.g. :func:`render_ascii_text` output)
    up by an integer ``scale`` in both axes, staying in the same ░▀▄█ alphabet.

    Each source text line encodes two pixel rows; we decode to the 3-level grid
    (so the ``░`` light shade survives), nearest-neighbour upscale it, then re-pack
    pixel-row pairs back into glyphs. ``scale=2`` doubles the menu font (2 rows →
    4). Every output line is padded to one width so the block centres cleanly.
    """
    if scale < 1:
        scale = 1
    width = max((len(line) for line in lines), default=0)

    # Decode: two pixel rows per source text line, each pixel FULL/LIGHT/EMPTY.
    pixels: list[list[int]] = []
    for line in lines:
        padded = line.ljust(width)
        pixels.append([_HALF_PIXELS.get(ch, (_EMPTY, _EMPTY))[0] for ch in padded])
        pixels.append([_HALF_PIXELS.get(ch, (_EMPTY, _EMPTY))[1] for ch in padded])

    # Nearest-neighbour upscale in both axes.
    big: list[list[int]] = []
    for row in pixels:
        scaled_row = [v for v in row for _ in range(scale)]
        big.extend([scaled_row] * scale)

    # Re-pack pixel-row pairs into glyphs.
    out_w = width * scale
    out: list[str] = []
    for j in range(0, len(big), 2):
        top = big[j]
        bot = big[j + 1] if j + 1 < len(big) else [_EMPTY] * out_w
        out.append("".join(_pack_pixels(t, b) for t, b in zip(top, bot)))
    return out


def render_ascii_text_large(text: str, scale: int = 2) -> list[str]:
    """Render *text* in the compact menu font, then scale it up (see
    :func:`scale_halfblock_lines`) — same ░▀▄█ letters, bigger. ``scale=2`` → a
    4-row wordmark; ``scale=3`` → 6 rows, keeping the ▀▄ edges and ░ texture."""
    return scale_halfblock_lines(render_ascii_text(text), scale)
