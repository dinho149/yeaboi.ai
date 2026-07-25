from rich.console import Group

from yeaboi.ui.shared import _mascot
from yeaboi.ui.shared import _mascot_sprites as sprites
from yeaboi.ui.shared._mascot import FRAMES, MASCOT_PALETTE, render_full, render_head

_LAYERS = ("DUCK_BASE", "DUCK_WING", "DUCK_GLASSES")
_VALID = set("koGgWLMSbr.")


def test_layers_exist_and_are_string_tuples():
    for name in _LAYERS:
        grid = getattr(sprites, name)
        assert isinstance(grid, tuple) and grid, f"{name} empty"
        assert all(isinstance(row, str) for row in grid)


def test_layers_share_dimensions():
    grids = [getattr(sprites, n) for n in _LAYERS]
    heights = {len(g) for g in grids}
    assert len(heights) == 1, f"layer heights differ: {heights}"
    widths = {len(row) for g in grids for row in g}
    assert len(widths) == 1, f"row widths differ: {widths}"


def test_layers_use_only_palette_letters():
    for name in _LAYERS:
        for row in getattr(sprites, name):
            assert set(row) <= _VALID, f"{name} has invalid chars: {set(row) - _VALID}"


def test_head_grid_rows_equal_length_and_valid_letters():
    valid = set(MASCOT_PALETTE) | {"."}
    widths = {len(r) for r in _mascot.DUCK_HEAD}
    assert widths == {16}
    for row in _mascot.DUCK_HEAD:
        assert set(row) <= valid


def test_render_full_returns_group_for_all_frames():
    for f in range(FRAMES):
        g = render_full(f)
        assert isinstance(g, Group)
        assert len(g.renderables) == 18  # 36 pixel rows -> 18 half-block rows


def test_render_head_row_count_in_range():
    for f in range(FRAMES):
        g = render_head(f)
        assert isinstance(g, Group)
        assert 6 <= len(g.renderables) <= 8  # 14 px (+bob) -> 7..8 text rows


def test_frame_index_is_deterministic():
    a = render_full(2).renderables
    b = render_full(2).renderables
    assert [t.plain for t in a] == [t.plain for t in b]


def test_wing_flap_changes_a_frame():
    rest = [t.plain for t in render_full(0).renderables]
    lifted = [t.plain for t in render_full(3).renderables]  # WING_OFF[3]=2
    assert rest != lifted


def test_palette_matches_generator():
    import pytest

    pytest.importorskip("PIL")
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("_gen", Path("scripts/gen_mascot_sprites.py"))
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert gen.PALETTE == MASCOT_PALETTE
