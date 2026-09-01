"""Tests for the Weekly Review renderers (solo/render.py)."""

from __future__ import annotations

from rich.console import Console

from yeaboi.agent.state import ReviewAction, WeeklyReview
from yeaboi.solo.render import _accent, format_review_rich


def _review() -> WeeklyReview:
    return WeeklyReview(
        week_label="2026-W36",
        week_start="2026-08-31",
        week_end="2026-09-04",
        project_name="Apollo",
        plan_line="Day 2/5 · On track",
        summary="Solid.",
        went_well=("Shipped S-1",),
        to_change=("Ask earlier",),
        actions=(ReviewAction(id="a1", text="Split S-2"),),
        carried_actions=(
            ReviewAction(id="c1", text="Write docs", status="done", origin="carryover", week_label="W35"),
        ),
        warnings=("no key",),
    )


class TestRich:
    def test_renders_without_error(self):
        console = Console(width=80, record=True, force_terminal=False, color_system=None)
        console.print(format_review_rich(_review(), accent="rgb(1,2,3)"))
        text = console.export_text()
        assert "Weekly Review — Apollo — 2026-W36" in text
        assert "● Write docs" in text
        assert "! no key" in text

    def test_default_accent_is_the_solo_theme(self):
        from yeaboi.ui.shared._components import SOLO_THEME

        assert _accent() == SOLO_THEME.accent
        console = Console(width=80, record=True, force_terminal=False, color_system=None)
        console.print(format_review_rich(_review()))
        assert "Weekly Review — Apollo — 2026-W36" in console.export_text()
