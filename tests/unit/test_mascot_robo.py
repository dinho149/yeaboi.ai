"""Tests for the robotic duck (Agents mascot) in ui/shared/_mascot.py."""

from rich.console import Console

from yeaboi.ui.shared._mascot import (
    DUCK_HEAD,
    MASCOT_PALETTE,
    ROBO_HEAD,
    ROBO_HEAD_QUACK,
    ROBO_PALETTE,
    head_cells,
    render_head,
    render_head_idle,
)


def _render(group) -> str:
    console = Console(width=40, force_terminal=True)
    with console.capture() as cap:
        console.print(group)
    return cap.get()


class TestRoboGrids:
    def test_same_footprint_as_duck(self):
        assert len(ROBO_HEAD) == len(DUCK_HEAD)
        assert {len(row) for row in ROBO_HEAD} == {len(DUCK_HEAD[0])}
        assert len(ROBO_HEAD_QUACK) == len(DUCK_HEAD)
        assert {len(row) for row in ROBO_HEAD_QUACK} == {len(DUCK_HEAD[0])}

    def test_every_letter_resolves(self):
        valid = set(MASCOT_PALETTE) | set(ROBO_PALETTE) | {"."}
        for grid in (ROBO_HEAD, ROBO_HEAD_QUACK):
            for row in grid:
                assert set(row) <= valid, f"unknown letters in {row!r}"

    def test_robo_letters_do_not_collide_with_duck(self):
        assert not set(ROBO_PALETTE) & set(MASCOT_PALETTE)

    def test_quack_variant_differs_only_in_bill_rows(self):
        differing = [i for i, (a, b) in enumerate(zip(ROBO_HEAD, ROBO_HEAD_QUACK)) if a != b]
        assert differing == [9, 10]


class TestRoboRendering:
    def test_render_head_robo_uses_steel_not_green(self):
        out = _render(render_head(0, mascot="robo"))
        assert "140;160;178" in out  # light steel
        assert "34;158;122" not in out  # duck green must not appear

    def test_render_head_duck_unchanged_by_default(self):
        assert _render(render_head(0)) == _render(render_head(0, mascot="duck"))
        assert "34;158;122" in _render(render_head(0))

    def test_beak_open_differs(self):
        assert _render(render_head(0, mascot="robo")) != _render(render_head(0, beak_open=True, mascot="robo"))

    def test_unknown_mascot_falls_back_to_duck(self):
        assert _render(render_head(0, mascot="???")) == _render(render_head(0))

    def test_idle_height_matches_duck_idle(self):
        duck_rows = _render(render_head_idle(0)).count("\n")
        robo_rows = _render(render_head_idle(0, mascot="robo")).count("\n")
        assert duck_rows == robo_rows

    def test_idle_lift_is_duck_only(self):
        # A lift on the robo must not play the duck's shades gag frames.
        assert _render(render_head_idle(0, 5, mascot="robo")) == _render(render_head_idle(0, mascot="robo"))

    def test_head_cells_robo(self):
        cells = head_cells(mascot="robo")
        assert len(cells) == len(head_cells())  # same packed height
        styles = {style for row in cells for _, style in row if style}
        assert any("90,200,230" in s for s in styles)  # cyan LED present
