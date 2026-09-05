from rich.console import Group

from yeaboi.ui.shared import _mascot
from yeaboi.ui.shared import _mascot_sprites as sprites
from yeaboi.ui.shared._mascot import FRAMES, MASCOT_PALETTE, render_full, render_head, render_head_shades, render_mini

_LAYERS = ("DUCK_BASE", "DUCK_WING", "DUCK_GLASSES")
_MINI_LAYERS = ("DUCK_MINI_BASE", "DUCK_MINI_WING", "DUCK_MINI_GLASSES")
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


def test_mini_layers_exist_share_dims_and_valid_letters():
    grids = [getattr(sprites, n) for n in _MINI_LAYERS]
    for g in grids:
        assert isinstance(g, tuple) and g
    assert len({len(g) for g in grids}) == 1  # same height
    assert len({len(row) for g in grids for row in g}) == 1  # same width
    for g in grids:
        for row in g:
            assert set(row) <= _VALID


def test_mini_is_smaller_than_full_but_taller_than_head():
    mini_rows = len(render_mini(0).renderables)
    assert isinstance(render_mini(0), Group)
    assert len(render_head(0).renderables) < mini_rows < len(render_full(0).renderables)


def test_render_mini_returns_group_for_all_frames():
    for f in range(FRAMES):
        g = render_mini(f)
        assert isinstance(g, Group)
        assert all(hasattr(t, "plain") for t in g.renderables)


def test_render_mini_flip_mirrors():
    normal = [t.plain for t in render_mini(0).renderables]
    flipped = [t.plain for t in render_mini(0, flip=True).renderables]
    assert normal != flipped
    assert normal == [row[::-1] for row in flipped]


def test_walk_cells_steps_alternate_feet():
    from yeaboi.ui.shared._mascot import walk_cells

    even = walk_cells(0)
    odd = walk_cells(1)
    # Same dimensions as the mini duck, but the bottom (feet) row differs between
    # the two beats as each foot steps in turn.
    assert len(even) == len(odd)
    assert [g for g, _ in even[-1]] != [g for g, _ in odd[-1]]


def test_walk_cells_flip_mirrors():
    from yeaboi.ui.shared._mascot import walk_cells

    normal = [[g for g, _ in row] for row in walk_cells(0)]
    flipped = [[g for g, _ in row] for row in walk_cells(0, flip=True)]
    assert normal == [row[::-1] for row in flipped]


def test_palette_matches_generator():
    import pytest

    pytest.importorskip("PIL")
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("_gen", Path("scripts/gen_mascot_sprites.py"))
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert gen.PALETTE == MASCOT_PALETTE
    from yeaboi.ui.shared._mascot import ROBO_PALETTE

    assert gen.PERSONA_EXTRA == ROBO_PALETTE  # the costume traces snap to the same steels the robo wears


def test_render_head_shades_lift_zero_matches_resting_head():
    # At lift 0 the two pairs coincide, so the visible art equals the normal head
    # (padded with blank top rows). Compare the non-blank rows.
    shades = [t.plain for t in render_head_shades(0).renderables]
    head = [t.plain for t in render_head(0).renderables]
    assert [r for r in shades if r.strip()] == [r for r in head if r.strip()]


def test_render_head_shades_lift_changes_art_and_flips():
    from rich.console import Group

    assert isinstance(render_head_shades(5), Group)
    rest = [t.plain for t in render_head_shades(0).renderables]
    lifted = [t.plain for t in render_head_shades(5).renderables]
    assert rest != lifted  # the raised pair moved
    flipped = [t.plain for t in render_head_shades(5, flip=True).renderables]
    assert lifted == [row[::-1] for row in flipped]


def test_shades_sequence_starts_lifting_and_returns_to_zero():
    from yeaboi.ui.shared._mascot import SHADES_LIFT_SEQUENCE

    assert SHADES_LIFT_SEQUENCE[-1] == 0  # ends resting (== DUCK_HEAD)
    assert max(SHADES_LIFT_SEQUENCE) >= 4  # lifts clear of the crown


# ---------------------------------------------------------------------------
# The persona costumes (the front page's figure)
# ---------------------------------------------------------------------------

PERSONA_IDS = {"engineer", "teacher", "martial", "chef", "astronaut", "dj", "detective", "wizard"}


def test_persona_grids_cover_every_costume_on_both_mascots():
    from yeaboi.ui.shared import _persona_sprites as personas

    assert set(personas.PERSONAS) == PERSONA_IDS
    assert set(personas.ROBO_PERSONAS) == PERSONA_IDS


def test_persona_grids_share_the_plate_size_and_the_palette():
    from yeaboi.ui.shared import _persona_sprites as personas
    from yeaboi.ui.shared._mascot import _ALL_COLOURS

    valid = set(_ALL_COLOURS) | {"."}
    for grids in (personas.PERSONAS, personas.ROBO_PERSONAS):
        for pid, grid in grids.items():
            assert len(grid) == 26, pid
            assert all(len(row) == personas.PERSONA_WIDTH for row in grid), pid
            assert set("".join(grid)) <= valid, pid


def test_persona_cells_pack_to_thirteen_rows_and_fall_back_to_the_engineer():
    from yeaboi.ui.shared._mascot import persona_cells

    wizard = persona_cells("wizard")
    assert len(wizard) == 13 and all(len(row) == 18 for row in wizard)
    assert any(style and "250,176,44" in style for row in wizard for _g, style in row)  # the bill
    assert persona_cells("pirate") == persona_cells("engineer")
    robo = persona_cells("wizard", mascot="robo")
    assert any(style and "140,160,178" in style for row in robo for _g, style in row)  # steel
    large = persona_cells("wizard", scale=2)
    assert len(large) == 26 and all(len(row) == 36 for row in large)
