"""Render tests for the in-place anonymize indicator (`anon_note`) + the progress screen.

The old raw-Markdown review screen is gone: anonymizing now re-renders each mode's OWN
screen with masked words and a slim `anon_note` subtitle. These tests assert every result
builder surfaces that note while still rendering its native content, and that the shared
progress screen still themes for the "Anonymizing output" loading state.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.panel import Panel

from yeaboi.ui.mode_select.screens._screens_secondary import (
    _build_performance_screen,
    _build_reporting_screen,
    _build_retro_screen,
    _build_roadmap_screen,
    _build_standup_progress_screen,
    _build_standup_screen,
    _build_team_analysis_screen,
)
from yeaboi.ui.shared._components import REPORTING_THEME, reporting_title

NOTE = "Anonymized · 3 masked"


def _render(panel: Panel, width: int = 100, height: int = 40) -> str:
    console = Console(file=StringIO(), width=width, height=height)
    console.print(panel)
    return console.file.getvalue()


class TestAnonNoteRendersPerBuilder:
    """Each result builder shows the anon_note subtitle when anonymized."""

    def test_standup(self):
        out = _render(_build_standup_screen({"session_name": "Team", "report": None}, anon_note=NOTE, sub_reveal=999.0))
        assert "3 masked" in out

    def test_performance_detail(self):
        data = {"view": "detail", "detail_lines": ["• shipped it"], "detail_title": "1:1 — X"}
        out = _render(_build_performance_screen(data, anon_note=NOTE, sub_reveal=999.0))
        assert "3 masked" in out
        assert "shipped it" in out  # native content still renders

    def test_reporting_detail(self):
        from yeaboi.agent.state import DeliveryReport

        report = DeliveryReport(period_label="Last month", headline="delivered 5 items")
        data = {"view": "detail", "report": report, "detail_title": "Report"}
        out = _render(_build_reporting_screen(data, anon_note=NOTE, sub_reveal=999.0))
        assert "3 masked" in out
        assert "delivered 5 items" in out

    def test_retro(self):
        out = _render(_build_retro_screen({"session_name": "Team", "grids": {}}, anon_note=NOTE, sub_reveal=999.0))
        assert "3 masked" in out

    def test_roadmap_results(self):
        from yeaboi.agent.state import RoadmapAnalysis

        data = {"view": "results", "analysis": RoadmapAnalysis(summary="s"), "actions": ["Back"]}
        out = _render(_build_roadmap_screen(data, anon_note=NOTE, sub_reveal=999.0))
        assert "3 masked" in out

    def test_analysis(self):
        from yeaboi.team_profile import TeamProfile

        profile = TeamProfile(team_id="t", source="jira", project_key="KEY")
        out = _render(_build_team_analysis_screen(profile, anon_note=NOTE))
        assert "3 masked" in out

    def test_no_note_when_empty(self):
        # Empty anon_note ⇒ the native subtitle is unchanged (no "masked" indicator).
        out = _render(_build_standup_screen({"session_name": "Team", "report": None}, sub_reveal=999.0))
        assert "masked" not in out.lower()


class TestProgressScreenTheming:
    def test_reuses_screen_with_custom_theme_and_label(self):
        panel = _build_standup_progress_screen(
            ["Masking sensitive data with the AI…"],
            width=90,
            height=24,
            theme=REPORTING_THEME,
            title=reporting_title(),
            label="Anonymizing output",
        )
        out = _render(panel, width=90, height=30)
        assert "Anonymizing output" in out
        current = next(
            item
            for item in panel.renderable.renderables
            if hasattr(item, "plain") and "Masking sensitive data" in item.plain
        )
        assert str(current.style) == f"bold {REPORTING_THEME.accent_bright}"
        assert str(panel.border_style) == REPORTING_THEME.accent

    def test_default_is_standup(self):
        panel = _build_standup_progress_screen(["Starting"], width=90, height=24)
        out = _render(panel, width=90, height=30)
        assert "Generating standup" in out
