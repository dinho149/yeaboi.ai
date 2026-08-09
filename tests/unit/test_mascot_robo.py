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
    # color_system is pinned, never left to auto-detection: these tests assert
    # truecolor SGR fragments ("34;158;122"), and Rich picks the system from
    # COLORTERM/TERM. A dev shell exports COLORTERM=truecolor and CI does not,
    # so an unpinned console passes locally and downgrades to 8-colour on CI.
    console = Console(width=40, force_terminal=True, color_system="truecolor")
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


class TestRoboBody:
    """The full robo body is DERIVED from the duck grids — geometry-identical."""

    def test_layers_share_duck_dimensions(self):
        from yeaboi.ui.shared import _mascot as m

        for robo, duck in (
            (m.ROBO_BASE, m.DUCK_BASE),
            (m.ROBO_WING, m.DUCK_WING),
            (m.ROBO_GLASSES, m.DUCK_GLASSES),
            (m.ROBO_MINI_BASE, m.DUCK_MINI_BASE),
            (m.ROBO_MINI_WING, m.DUCK_MINI_WING),
            (m.ROBO_MINI_GLASSES, m.DUCK_MINI_GLASSES),
        ):
            assert len(robo) == len(duck)
            assert [len(r) for r in robo] == [len(r) for r in duck]

    def test_letters_resolve_in_merged_palette(self):
        from yeaboi.ui.shared import _mascot as m

        valid = set(MASCOT_PALETTE) | set(ROBO_PALETTE) | {"."}
        for grid in (m.ROBO_BASE, m.ROBO_WING, m.ROBO_GLASSES, m.ROBO_MINI_BASE, m.ROBO_MINI_WING, m.ROBO_MINI_GLASSES):
            for row in grid:
                assert set(row) <= valid

    def test_feet_row_identical_to_duck(self):
        # The walk cycle's hardcoded foot columns depend on the duck's bottom
        # row; the recolor must not touch it (b/r/k are unmapped).
        from yeaboi.ui.shared import _mascot as m

        assert m.ROBO_BASE[-1] == m.DUCK_BASE[-1]
        assert m.ROBO_MINI_BASE[-1] == m.DUCK_MINI_BASE[-1]

    def test_walk_alternates_feet_for_robo(self):
        from yeaboi.ui.shared._mascot import walk_cells

        even = walk_cells(0, mascot="robo")[-1]
        odd = walk_cells(1, mascot="robo")[-1]
        assert even != odd

    def test_antenna_bulb_in_crown_run(self):
        from yeaboi.ui.shared import _mascot as m

        for robo, duck in ((m.ROBO_BASE, m.DUCK_BASE), (m.ROBO_MINI_BASE, m.DUCK_MINI_BASE)):
            assert robo[0].count("V") == 1
            mid = robo[0].index("V")
            # The bulb sits inside what was the duck's crown outline run.
            assert duck[0][mid] == "k"

    def test_cyan_on_glasses_only_no_glowing_belly(self):
        from yeaboi.ui.shared import _mascot as m

        assert "V" not in "".join(m.ROBO_BASE[1:])  # row 0 carries only the antenna
        assert "V" not in "".join(m.ROBO_WING)
        assert "V" in "".join(m.ROBO_GLASSES)  # LED eye glints
        assert "W" in "".join(m.ROBO_BASE)  # chrome shine stays white
        assert "G" not in "".join(m.ROBO_BASE) and "g" not in "".join(m.ROBO_BASE)

    def test_render_full_robo_is_18_rows_all_frames(self):
        from yeaboi.ui.shared._mascot import FRAMES, render_full

        for frame in range(FRAMES):
            assert len(_render(render_full(frame, mascot="robo")).splitlines()) == 18

    def test_robo_renders_steel_not_green(self):
        from yeaboi.ui.shared._mascot import render_full, render_mini

        for out in (_render(render_full(0, mascot="robo")), _render(render_mini(0, mascot="robo"))):
            assert "140;160;178" in out
            assert "90;200;230" in out  # cyan (antenna and/or visor)
            assert "34;158;122" not in out

    def test_glasses_bob_suppressed_for_robo(self):
        # Frames 0 and 3 differ only by wing offset for the robo (GLASS_OFF[3]=1
        # would move the duck's shades); assert the visor rows above the wing
        # band are identical while the duck's differ.
        from yeaboi.ui.shared._mascot import full_cells

        robo0 = full_cells(0, glasses_frame=0, mascot="robo")
        robo3 = full_cells(0, glasses_frame=3, mascot="robo")
        assert robo0 == robo3  # only the glasses_frame changed → no-op for robo
        duck0 = full_cells(0, glasses_frame=0)
        duck3 = full_cells(0, glasses_frame=3)
        assert duck0 != duck3

    def test_duck_defaults_unchanged(self):
        from yeaboi.ui.shared._mascot import render_full, render_mini, walk_cells

        assert _render(render_full(0)) == _render(render_full(0, mascot="duck"))
        assert _render(render_mini(0)) == _render(render_mini(0, mascot="duck"))
        assert walk_cells(2) == walk_cells(2, mascot="duck")
        assert "34;158;122" in _render(render_full(0))

    def test_sprites_module_stays_duck_only(self):
        # The AUTO-GENERATED file must never grow robo letters — derivation
        # lives in _mascot.py.
        from yeaboi.ui.shared import _mascot_sprites as sprites

        blob = "".join(
            "".join(grid)
            for grid in (
                sprites.DUCK_BASE,
                sprites.DUCK_WING,
                sprites.DUCK_GLASSES,
                sprites.DUCK_MINI_BASE,
                sprites.DUCK_MINI_WING,
                sprites.DUCK_MINI_GLASSES,
            )
        )
        assert not set("CcV") & set(blob)

    def test_unknown_mascot_falls_back_to_duck_body(self):
        from yeaboi.ui.shared._mascot import render_full

        assert _render(render_full(0, mascot="???")) == _render(render_full(0))
