"""Two-line block-character ASCII font, ported from th's ascii_font.rs.

Each letter is a 2-element list of strings (top line, bottom line).
Characters use █ ▀ ▄ ░ block drawing chars for a compact, bold look.
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


# Half-block glyphs pack two vertical pixels into one character cell (▀ = top,
# ▄ = bottom, █ = both, blank/░ = none). Decoding to a 1-bit grid lets us scale
# the compact menu font up cleanly instead of shipping a second, larger font.
_HALF_TOP = {"█": True, "▀": True, "▄": False, "░": False, " ": False}
_HALF_BOT = {"█": True, "▀": False, "▄": True, "░": False, " ": False}


def scale_halfblock_lines(lines: list[str], scale: int = 2) -> list[str]:
    """Scale a half-block ASCII-art block (e.g. :func:`render_ascii_text` output)
    up by an integer ``scale`` in both axes, staying in the same ▀▄█ alphabet.

    Each source text line encodes two pixel rows; we decode to a 1-bit grid,
    nearest-neighbour upscale it, then re-pack pixel-row pairs back into
    half-block glyphs. ``scale=2`` doubles the menu font (2 rows → 4). Every
    output line is padded to one width so the block centres cleanly.
    """
    if scale < 1:
        scale = 1
    width = max((len(line) for line in lines), default=0)

    # Decode: two pixel rows per source text line.
    pixels: list[list[bool]] = []
    for line in lines:
        padded = line.ljust(width)
        pixels.append([_HALF_TOP.get(ch, False) for ch in padded])
        pixels.append([_HALF_BOT.get(ch, False) for ch in padded])

    # Nearest-neighbour upscale in both axes.
    big: list[list[bool]] = []
    for row in pixels:
        scaled_row = [on for on in row for _ in range(scale)]
        big.extend([scaled_row] * scale)

    # Re-pack pixel-row pairs into half-block glyphs.
    out_w = width * scale
    out: list[str] = []
    for j in range(0, len(big), 2):
        top = big[j]
        bot = big[j + 1] if j + 1 < len(big) else [False] * out_w
        row_chars = []
        for t, b in zip(top, bot):
            row_chars.append("█" if t and b else "▀" if t else "▄" if b else " ")
        out.append("".join(row_chars))
    return out


# The dither shade: a *half-block* (same colour, half-height cell) — NOT a darker
# shade like ▓ — so the texture reads like the menu titles' own ▀▄ half-blocks
# rather than high-contrast lego studs.
_TEXTURE_SHADE = "▄"


def _checker_texture(lines: list[str], shade: str = _TEXTURE_SHADE) -> list[str]:
    """Dither every solid ``█`` cell on a checkerboard to ``shade``.

    Scaling the menu font up fills the stroke interiors with flat ``█`` blocks,
    which reads as heavy/blocky. Alternating ``█`` with a half-block scatters
    half-height cells through the fill — the same subtle pixel texture the small
    menu titles get from their own ▀▄ glyphs — without the lego-stud contrast a
    darker shade (▓) gives. Only ``█`` is dithered, so the letterforms are intact.
    """
    return [
        "".join(shade if (ch == "█" and (r + c) % 2) else ch for c, ch in enumerate(line))
        for r, line in enumerate(lines)
    ]


def render_ascii_text_large(text: str, scale: int = 2, *, texture: bool = False) -> list[str]:
    """Render *text* in the compact menu font, then scale it up (see
    :func:`scale_halfblock_lines`). ``scale=2`` → a 4-row wordmark. ``texture=True``
    dithers the solid fill so the enlarged font keeps the menu titles' pixel
    texture instead of reading as flat blocks."""
    big = scale_halfblock_lines(render_ascii_text(text), scale)
    return _checker_texture(big) if texture else big
