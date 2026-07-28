"""Tests for the block-character ASCII font and its integer upscaler.

``scale_halfblock_lines`` powers the enlarged wordmark on the brand splash — it
takes the compact two-line menu font and scales it up in the same ▀▄█ alphabet
so the splash reads as "the menu font, bigger" rather than a second typeface.
"""

from __future__ import annotations

from yeaboi.ui.shared._ascii_font import (
    render_ascii_text,
    render_ascii_text_large,
    scale_halfblock_lines,
)

_HALFBLOCK = set(" █▀▄")


class TestRenderAsciiText:
    def test_two_lines(self):
        lines = render_ascii_text("YEABOI")
        assert len(lines) == 2

    def test_unknown_char_is_a_gap(self):
        # '@' is not in the alphabet → rendered as blank columns, not a crash.
        lines = render_ascii_text("A@")
        assert len(lines) == 2


class TestScaleHalfblock:
    def test_scale_one_is_identity_height(self):
        src = render_ascii_text("YEABOI")
        assert len(scale_halfblock_lines(src, 1)) == 2

    def test_scale_two_doubles_rows(self):
        src = render_ascii_text("YEABOI")
        assert len(scale_halfblock_lines(src, 2)) == 4

    def test_scale_three_gives_six_rows(self):
        src = render_ascii_text("YEABOI")
        assert len(scale_halfblock_lines(src, 3)) == 6

    def test_scale_two_doubles_width(self):
        src = render_ascii_text("YEABOI")
        base_w = max(len(line) for line in src)
        big = scale_halfblock_lines(src, 2)
        assert max(len(line) for line in big) == base_w * 2

    def test_only_halfblock_glyphs(self):
        big = scale_halfblock_lines(render_ascii_text("YEABOI"), 3)
        used = set("".join(big))
        assert used <= _HALFBLOCK

    def test_blank_input_is_safe(self):
        assert scale_halfblock_lines([], 2) == []

    def test_scale_below_one_clamps_to_one(self):
        src = render_ascii_text("A")
        assert len(scale_halfblock_lines(src, 0)) == 2


class TestRenderAsciiTextLarge:
    def test_default_scale_two(self):
        assert len(render_ascii_text_large("YEABOI")) == 4

    def test_explicit_scale(self):
        assert len(render_ascii_text_large("YEABOI", 3)) == 6

    def test_produces_visible_ink(self):
        # The enlarged block must actually contain glyphs, not just spaces.
        big = render_ascii_text_large("YEABOI", 2)
        assert any(ch in "█▀▄" for line in big for ch in line)

    def test_texture_dithers_the_fill(self):
        # texture=True introduces the ▓ shade the solid version doesn't have, so the
        # enlarged font keeps the menu titles' pixel texture instead of flat blocks.
        solid = render_ascii_text_large("YEABOI", 3)
        textured = render_ascii_text_large("YEABOI", 3, texture=True)
        assert "▓" not in "".join(solid)
        assert "▓" in "".join(textured)

    def test_texture_preserves_dimensions(self):
        # Dithering must not change the block's size — same rows and width.
        solid = render_ascii_text_large("YEABOI", 3)
        textured = render_ascii_text_large("YEABOI", 3, texture=True)
        assert len(textured) == len(solid)
        assert [len(t) for t in textured] == [len(s) for s in solid]
