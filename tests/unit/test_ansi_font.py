"""The tall ANSI Shadow face.

The point of this file is the first class. ``_ansi_font.SHADOW_GLYPHS`` was
hand-authored, and a hand-authored font is only as good as what proves it: 22
words were generated independently by pyfiglet and pasted into the codebase over
the life of the project, so rebuilding all 22 from the table is a real check on
every glyph and on the kerning rule at once.
"""

from __future__ import annotations

import pytest

from yeaboi.ui.shared._ansi_font import SHADOW_GLYPHS, SHADOW_ROWS, render_shadow_text
from yeaboi.ui.shared._wordmarks import SHADOW_WORDMARKS
from yeaboi.ui.splash import _WORDMARK

# Every pre-baked word in the codebase, keyed the way it is stored.
BAKED: dict[str, list[str]] = {**SHADOW_WORDMARKS, "YEABOI": list(_WORDMARK)}

# The letters the bakes actually contain. Everything else in the table is a
# standard ansi_shadow form carried for completeness that no bake can prove — if
# one of those is ever wrong, this is the comment that explains why the suite was
# green. See the module docstring.
PINNED_BY_A_BAKE = set("".join(BAKED))


class TestReproducesTheBakedWordmarks:
    """The whole basis for trusting the table."""

    @pytest.mark.parametrize("word", sorted(BAKED))
    def test_rebuilds_each_baked_wordmark_exactly(self, word: str) -> None:
        assert render_shadow_text(word) == BAKED[word]

    def test_the_bakes_cover_most_of_the_alphabet(self) -> None:
        # Guards the claim in the docstring rather than the font: if a future bake
        # adds a letter, the "unproven" list shrinks and the comment above should
        # follow.
        unproven = sorted(set(SHADOW_GLYPHS) - PINNED_BY_A_BAKE - {" "})
        assert unproven == ["Q", "V", "W", "X", "Z"]

    def test_kerning_is_load_bearing(self) -> None:
        # ANALYSIS is the word that proves fitting is not optional: its L and Y
        # nest by three columns, so plain concatenation would render it wider than
        # the terminal does. If this ever equals the sum, _fit has stopped working
        # and most words would still pass.
        widths = sum(len(SHADOW_GLYPHS[c][0]) for c in "ANALYSIS")
        assert len(render_shadow_text("ANALYSIS")[0]) == widths - 3


class TestShape:
    def test_every_glyph_is_six_rows_of_one_width(self) -> None:
        for char, glyph in SHADOW_GLYPHS.items():
            assert len(glyph) == SHADOW_ROWS, char
            assert len({len(row) for row in glyph}) == 1, char

    def test_rendered_rows_are_equal_width(self) -> None:
        rows = render_shadow_text("roadmap")
        assert len(rows) == SHADOW_ROWS
        assert len({len(r) for r in rows}) == 1

    def test_is_case_insensitive(self) -> None:
        assert render_shadow_text("retro") == render_shadow_text("RETRO")

    def test_empty_text_gives_six_empty_rows(self) -> None:
        assert render_shadow_text("") == [""] * SHADOW_ROWS


class TestUnsupportedCharacters:
    """A word the face cannot set must be refused whole, not part-rendered."""

    @pytest.mark.parametrize("text", ["sprint 42", "n/a", "v1.2", "e2e"])
    def test_returns_none_rather_than_a_damaged_word(self, text: str) -> None:
        assert render_shadow_text(text) is None

    def test_a_space_sets_a_gap_and_is_not_kerned_away(self) -> None:
        # A blank glyph nests into anything, so without the guard in _fit the gap
        # between two words would close up completely.
        gap = len(render_shadow_text("A A")[0]) - len(render_shadow_text("AA")[0])
        assert gap == len(SHADOW_GLYPHS[" "][0])


class TestTheWordsTheWebActuallySets:
    """Every wordmark an exporter or a board passes must render in this face.

    These strings are the ``wordmark=`` arguments in the exporters plus the two
    board headings. A new export that picks a word with a digit in it would fall
    back to the compact face silently on a page nobody re-opens for months, so it
    is worth one cheap test.
    """

    @pytest.mark.parametrize(
        "word",
        [
            "yeaboi",
            "retro",
            "poker",
            "plan",
            "team",
            "masked",
            "prep",
            "summary",
            "review",
            "report",
            "roadmap",
            "standup",
        ],
    )
    def test_renders(self, word: str) -> None:
        rows = render_shadow_text(word)
        assert rows is not None, f"{word!r} would silently fall back to the compact face"
        assert len(rows) == SHADOW_ROWS
