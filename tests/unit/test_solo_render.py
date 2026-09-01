"""Tests for the Weekly Review renderers (solo/render.py)."""

from __future__ import annotations

from rich.console import Console

from yeaboi.agent.state import ReviewAction, WeeklyReview
from yeaboi.solo.render import format_review_lines, format_review_rich


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


class TestPlaintext:
    def test_layout(self):
        lines = format_review_lines(_review())
        assert lines[0] == "Weekly Review — Apollo — 2026-W36"
        assert "Against the plan: Day 2/5 · On track" in lines
        assert "  ○ Split S-2" in lines
        assert "  ● Write docs  (from W35)" in lines
        assert lines[-1] == "  ! no key"

    def test_empty_review(self):
        lines = format_review_lines(WeeklyReview())
        assert lines[0] == "Weekly Review — Solo — "
        assert "Against the plan: no verdict" in lines
        assert lines[-1] != ""


class TestRich:
    def test_renders_without_error(self):
        console = Console(width=80, record=True, force_terminal=False, color_system=None)
        console.print(format_review_rich(_review(), accent="rgb(1,2,3)"))
        text = console.export_text()
        assert "Weekly Review — Apollo — 2026-W36" in text
        assert "● Write docs" in text
        assert "! no key" in text
