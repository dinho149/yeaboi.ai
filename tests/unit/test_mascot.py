from yeaboi.ui.shared import _mascot_sprites as sprites

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
