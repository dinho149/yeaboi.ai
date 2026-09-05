"""The front page's ink backdrops (_scene_backdrops.py): one per scene, sized for the plate."""

from __future__ import annotations

import pytest

from yeaboi.news import edition
from yeaboi.ui.shared import _scene_backdrops as backdrops

INK = set("·✦▁▔▐▌▓═─│╱╲┃╭╮╰╯┬┼├┤┴▄▀▂▟▙▲●○◯◀╳~█")


class TestBackdrops:
    def test_one_per_scene(self):
        assert set(backdrops.BACKDROPS) == set(edition.CAPTIONS)

    @pytest.mark.parametrize("scene", sorted(edition.CAPTIONS))
    def test_sized_for_the_plate_in_ink(self, scene):
        grid = backdrops.backdrop(scene)
        assert len(grid) == backdrops.PLATE_ROWS
        assert all(len(row) == backdrops.PLATE_COLS for row in grid), scene
        used = set("".join(grid)) - {"."}
        assert used <= INK, (scene, used - INK)

    @pytest.mark.parametrize("scene", sorted(edition.CAPTIONS))
    def test_the_duck_has_the_ground_and_the_left_to_himself(self, scene):
        grid = backdrops.backdrop(scene)
        assert grid[-1].strip(".") and grid[-1] == backdrops.GROUND
        # Where the duck's body stands is clear, so the picture never draws
        # through him; the sky over his head may carry a star.
        left = backdrops.DUCK_X
        for row in grid[6:-1]:
            assert row[left : left + 18].strip(".") == "", (scene, row)

    @pytest.mark.parametrize("scene", sorted(edition.CAPTIONS))
    def test_doubled_for_the_large_picture(self, scene):
        grid = backdrops.scaled(backdrops.backdrop(scene), 2)
        assert len(grid) == backdrops.PLATE_ROWS * 2
        assert all(len(row) == backdrops.PLATE_COLS * 2 for row in grid), scene
        assert grid[-1] == "▁" * (backdrops.PLATE_COLS * 2) and not grid[-2].strip(".")
        assert set("".join(grid)) - {"."} <= INK | set("▜▛")

    def test_doubling_keeps_the_lines_joined(self):
        top, bottom = backdrops.scaled(("╭─╮", "│.│", "╰─╯"), 2)[0:2]
        assert (top, bottom) == ("╭───╮.", "│...│.")
        assert backdrops.scaled(("x",), 1) == ("x",)

    def test_an_unknown_scene_is_bare_ground(self):
        grid = backdrops.backdrop("nowhere")
        assert len(grid) == backdrops.PLATE_ROWS and grid[-1] == backdrops.GROUND
        assert not "".join(grid[:-1]).strip(".")
