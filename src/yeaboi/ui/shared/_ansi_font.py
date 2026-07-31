"""The tall ANSI Shadow display face, as a per-character table.

The product has two display faces. ``_ascii_font.py`` holds the compact two-row
one that sets every TUI mode title; this is the six-row one the splash screen and
the mode intros use, the one people mean when they say the app "looks like a
terminal".

Until now it existed only as **whole baked words**: 21 of them in ``_wordmarks.py``
and the YEABOI banner in ``splash.py``, each generated once from pyfiglet's
``ansi_shadow`` font and pasted in so there is no runtime figlet dependency. That
is fine for a fixed menu of modes and hopeless for the web, where the wordmark is
whatever an exporter passes — ``plan``, ``team``, ``masked``, ``prep``,
``summary``, ``review``, ``report``, ``roadmap``. None of those are baked, and
baking eight more would not fix the ninth.

So the face is stored here as characters instead, and the words are built. The
table is exported to TypeScript by ``scripts/gen_web_types.py`` for
``<Wordmark variant="shadow">``, the same seam ``BLOCK_GLYPHS`` already uses — the
browser and the terminal cannot drift because there is one table.

## Why there is a kerning step

Letters are *not* simply concatenated. figlet's default layout for this font is
"fitting": each glyph slides left until one of its cells would touch the previous
glyph's, so an ``L`` followed by a ``Y`` closes up by three columns where the L's
low bar sits under the Y's open arms. ``ANALYSIS`` is three columns narrower than
its glyph widths sum to, and ``YEABOI`` is exactly its sum because none of its
pairs happen to nest. Rendering without :func:`_fit` would reproduce most words
and quietly widen a few — the kind of difference nobody notices until the terminal
and the browser are open side by side.

``tests/unit/test_ansi_font.py`` pins this by rebuilding all 22 baked words from
this table; that test is the reason to trust the glyphs below.

## Coverage

A–Z and space. Those 21 distinct letters are what the bakes contain, so those are
what the test can prove — ``Q``, ``V``, ``W``, ``X`` and ``Z`` are standard
``ansi_shadow`` forms carried here for completeness but are not pinned by any
bake. Digits and punctuation are deliberately absent rather than guessed at:
:func:`render_shadow_text` returns ``None`` for a word it cannot set, and callers
fall back to the compact face. A missing digit therefore shows up as the smaller
wordmark, never as a malformed one.

Note this module changes nothing in the TUI. ``_wordmarks.py`` remains the source
for the intros; folding it into this table is a possible follow-up, but it would
make screens that currently fall back to the compact face render tall, and that is
a separate change with its own screenshots to look at.
"""

from __future__ import annotations

# Every glyph is exactly this many rows, so a word is too.
SHADOW_ROWS = 6

# Blank column. The face draws with block and box-drawing characters; a space is
# genuinely empty, which is what makes :func:`_fit` able to nest neighbours.
_BLANK = " "

# fmt: off
SHADOW_GLYPHS: dict[str, list[str]] = {
    "A": [
        " █████╗ ",
        "██╔══██╗",
        "███████║",
        "██╔══██║",
        "██║  ██║",
        "╚═╝  ╚═╝",
    ],
    "B": [
        "██████╗ ",
        "██╔══██╗",
        "██████╔╝",
        "██╔══██╗",
        "██████╔╝",
        "╚═════╝ ",
    ],
    "C": [
        " ██████╗",
        "██╔════╝",
        "██║     ",
        "██║     ",
        "╚██████╗",
        " ╚═════╝",
    ],
    "D": [
        "██████╗ ",
        "██╔══██╗",
        "██║  ██║",
        "██║  ██║",
        "██████╔╝",
        "╚═════╝ ",
    ],
    "E": [
        "███████╗",
        "██╔════╝",
        "█████╗  ",
        "██╔══╝  ",
        "███████╗",
        "╚══════╝",
    ],
    "F": [
        "███████╗",
        "██╔════╝",
        "█████╗  ",
        "██╔══╝  ",
        "██║     ",
        "╚═╝     ",
    ],
    "G": [
        " ██████╗ ",
        "██╔════╝ ",
        "██║  ███╗",
        "██║   ██║",
        "╚██████╔╝",
        " ╚═════╝ ",
    ],
    "H": [
        "██╗  ██╗",
        "██║  ██║",
        "███████║",
        "██╔══██║",
        "██║  ██║",
        "╚═╝  ╚═╝",
    ],
    "I": [
        "██╗",
        "██║",
        "██║",
        "██║",
        "██║",
        "╚═╝",
    ],
    "J": [
        "     ██╗",
        "     ██║",
        "     ██║",
        "██   ██║",
        "╚█████╔╝",
        " ╚════╝ ",
    ],
    "K": [
        "██╗  ██╗",
        "██║ ██╔╝",
        "█████╔╝ ",
        "██╔═██╗ ",
        "██║  ██╗",
        "╚═╝  ╚═╝",
    ],
    "L": [
        "██╗     ",
        "██║     ",
        "██║     ",
        "██║     ",
        "███████╗",
        "╚══════╝",
    ],
    "M": [
        "███╗   ███╗",
        "████╗ ████║",
        "██╔████╔██║",
        "██║╚██╔╝██║",
        "██║ ╚═╝ ██║",
        "╚═╝     ╚═╝",
    ],
    "N": [
        "███╗   ██╗",
        "████╗  ██║",
        "██╔██╗ ██║",
        "██║╚██╗██║",
        "██║ ╚████║",
        "╚═╝  ╚═══╝",
    ],
    "O": [
        " ██████╗ ",
        "██╔═══██╗",
        "██║   ██║",
        "██║   ██║",
        "╚██████╔╝",
        " ╚═════╝ ",
    ],
    "P": [
        "██████╗ ",
        "██╔══██╗",
        "██████╔╝",
        "██╔═══╝ ",
        "██║     ",
        "╚═╝     ",
    ],
    "Q": [
        " ██████╗ ",
        "██╔═══██╗",
        "██║   ██║",
        "██║▄▄ ██║",
        "╚██████╔╝",
        " ╚══▀▀═╝ ",
    ],
    "R": [
        "██████╗ ",
        "██╔══██╗",
        "██████╔╝",
        "██╔══██╗",
        "██║  ██║",
        "╚═╝  ╚═╝",
    ],
    "S": [
        "███████╗",
        "██╔════╝",
        "███████╗",
        "╚════██║",
        "███████║",
        "╚══════╝",
    ],
    "T": [
        "████████╗",
        "╚══██╔══╝",
        "   ██║   ",
        "   ██║   ",
        "   ██║   ",
        "   ╚═╝   ",
    ],
    "U": [
        "██╗   ██╗",
        "██║   ██║",
        "██║   ██║",
        "██║   ██║",
        "╚██████╔╝",
        " ╚═════╝ ",
    ],
    "V": [
        "██╗   ██╗",
        "██║   ██║",
        "██║   ██║",
        "╚██╗ ██╔╝",
        " ╚████╔╝ ",
        "  ╚═══╝  ",
    ],
    "W": [
        "██╗    ██╗",
        "██║    ██║",
        "██║ █╗ ██║",
        "██║███╗██║",
        "╚███╔███╔╝",
        " ╚══╝╚══╝ ",
    ],
    "X": [
        "██╗  ██╗",
        "╚██╗██╔╝",
        " ╚███╔╝ ",
        " ██╔██╗ ",
        "██╔╝ ██╗",
        "╚═╝  ╚═╝",
    ],
    "Y": [
        "██╗   ██╗",
        "╚██╗ ██╔╝",
        " ╚████╔╝ ",
        "  ╚██╔╝  ",
        "   ██║   ",
        "   ╚═╝   ",
    ],
    "Z": [
        "███████╗",
        "╚══███╔╝",
        "  ███╔╝ ",
        " ███╔╝  ",
        "███████╗",
        "╚══════╝",
    ],
    " ": [
        "    ",
        "    ",
        "    ",
        "    ",
        "    ",
        "    ",
    ],
}
# fmt: on


def _fit(left: list[str], right: list[str]) -> int:
    """Return how many columns *right* may slide left before the two touch.

    This is figlet's "fitting" layout. Neighbouring glyphs nest into each other's
    empty corners — the whole reason the face looks set rather than typed — but
    only where *every* row stays clear, so no cell is ever overwritten.

    *left* is the word assembled so far, not just the previous glyph, which is
    what lets a narrow letter like ``I`` be nested past by its successor.
    """
    for shift in range(min(len(left[0]), len(right[0])), 0, -1):
        clear = True
        for left_row, right_row in zip(left, right):
            tail = left_row[len(left_row) - shift :]
            head = right_row[:shift]
            if any(a != _BLANK and b != _BLANK for a, b in zip(tail, head)):
                clear = False
                break
        if clear:
            return shift
    return 0


def render_shadow_text(text: str) -> list[str] | None:
    """Render *text* as six rows of ANSI Shadow art, or ``None``.

    Returns ``None`` — rather than dropping the character or substituting a gap —
    when *text* contains anything the face has no glyph for. The two faces are
    interchangeable at the call site, so a caller's fallback is to set the word in
    the compact one; half a word in the tall face would be worse than the whole
    word in the small one.

    Case-insensitive: the table is uppercase, like ``render_ascii_text``.
    """
    rows: list[str] = []
    previous = ""
    for ch in text.upper():
        glyph = SHADOW_GLYPHS.get(ch)
        if glyph is None:
            return None
        if not rows:
            rows = list(glyph)
            previous = ch
            continue

        # Nothing fits across a space. The space glyph is all blanks, so _fit
        # would happily slide its neighbour straight through it and the gap
        # between two words would disappear — the one pair where "they do not
        # touch" is the wrong question to ask.
        shift = 0 if _BLANK in (ch, previous) else _fit(rows, glyph)
        previous = ch
        merged = []
        for row, cell in zip(rows, glyph):
            # Inside the overlap neither side has ink where the other does (that
            # is what _fit guarantees), so taking whichever is non-blank merges
            # them without a collision.
            zone = "".join(a if b == _BLANK else b for a, b in zip(row[len(row) - shift :], cell[:shift]))
            merged.append(row[: len(row) - shift] + zone + cell[shift:])
        rows = merged

    return rows or [""] * SHADOW_ROWS
