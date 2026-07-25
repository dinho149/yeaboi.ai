"""Render tests for _build_roadmap_screen (source + results views)."""

import io

from rich.console import Console
from rich.panel import Panel

from yeaboi.agent.state import RoadmapAnalysis, RoadmapProject
from yeaboi.ui.mode_select.screens._screens_secondary import _build_roadmap_screen


def _render_to_text(panel: Panel, width: int = 100, height: int = 40) -> str:
    console = Console(file=io.StringIO(), width=width, height=height, legacy_windows=False)
    console.print(panel)
    return console.file.getvalue()


def _sources():
    return [
        ("confluence", "Confluence page", "Read a page by URL, ID, or title"),
        ("notion", "Notion page", "Not configured — set NOTION_TOKEN in .env"),
        ("local", "Local file (.md .txt .pdf)", "Read a roadmap document from disk"),
    ]


def _analysis(n_projects: int = 2):
    projects = tuple(
        RoadmapProject(
            name=f"Project {i}",
            description=f"Deliver capability number {i} for the team. UNIQUEDESC{i} spells out the scope.",
            size="large" if i % 2 else "small",
            rationale=f"Reason {i}.",
            priority=i + 1,
            themes=("Growth",),
            quarter="Q3 2026",
        )
        for i in range(n_projects)
    )
    return RoadmapAnalysis(
        source_type="confluence",
        source_locator="42",
        source_label="Q3 Roadmap",
        summary="The quarter in one line.",
        projects=projects,
        warnings=("Roadmap truncated at 24,000 characters",),
        generated_at="2026-07-18T09:00:00",
    )


class TestSourceView:
    def test_returns_panel(self):
        data = {"view": "source", "sources": _sources(), "selected_idx": 0, "actions": ["Select", "Back"]}
        assert isinstance(_build_roadmap_screen(data), Panel)

    def test_renders_options_and_hints(self):
        data = {"view": "source", "sources": _sources(), "selected_idx": 1, "actions": ["Select", "Back"]}
        out = _render_to_text(_build_roadmap_screen(data))
        assert "Confluence page" in out
        assert "NOTION_TOKEN" in out
        assert "▸" in out

    def test_empty_sources_render(self):
        data = {"view": "source", "sources": [], "selected_idx": 0, "actions": ["Select", "Back"]}
        assert isinstance(_build_roadmap_screen(data), Panel)

    def test_message_shown(self):
        data = {
            "view": "source",
            "sources": _sources(),
            "selected_idx": 0,
            "actions": ["Select", "Back"],
            "message": "File not found: /nope.md",
        }
        out = _render_to_text(_build_roadmap_screen(data))
        assert "File not found" in out

    def test_default_view_is_source(self):
        # No "view" key → the source picker renders (the old list view is gone;
        # saved roadmaps live in the Planning project list now).
        data = {"sources": _sources(), "selected_idx": 0, "actions": ["Select", "Back"]}
        out = _render_to_text(_build_roadmap_screen(data))
        assert "Confluence page" in out

    def test_busy_hides_source_options(self):
        # While analyzing, only the spinner shows — the source list + buttons are
        # suppressed so the user isn't confused by still-selectable options.
        data = {
            "view": "source",
            "busy": True,
            "message": "◐ Analyzing with the AI — extracting projects…  (3s)",
            "sources": _sources(),
            "selected_idx": 2,
            "actions": ["Select", "Back"],
        }
        out = _render_to_text(_build_roadmap_screen(data))
        assert "Analyzing with the AI" in out
        assert "Confluence page" not in out
        assert "Local file" not in out
        assert "Select" not in out  # no action buttons while busy


class TestResultsView:
    def _data(self, analysis, cursor=0, message=""):
        return {
            "view": "results",
            "analysis": analysis,
            "project_cursor": cursor,
            "actions": ["Plan This", "Re-analyze", "Change Source", "Back"],
            "message": message,
            "source_label": getattr(analysis, "source_label", ""),
            "analyzed_at": "2026-07-18",
        }

    def test_returns_panel(self):
        assert isinstance(_build_roadmap_screen(self._data(_analysis())), Panel)

    def test_renders_projects_badges_and_notices(self):
        # Tall panel so the full list + notices fit inside the viewport.
        out = _render_to_text(_build_roadmap_screen(self._data(_analysis()), height=40))
        assert "The quarter in one line." in out
        assert "1. Project 0" in out
        assert "[Small]" in out
        assert "[Large]" in out
        assert "Notices" in out
        assert "truncated" in out
        assert "Q3 Roadmap" in out  # subtitle carries the source label

    def test_cursor_marks_selected_project(self):
        out = _render_to_text(_build_roadmap_screen(self._data(_analysis(), cursor=1)))
        assert "▸ 2. Project 1" in out.replace("\n", " ") or "▸" in out

    def test_none_analysis_renders_empty_state(self):
        out = _render_to_text(_build_roadmap_screen(self._data(None)))
        assert "No projects extracted" in out

    def test_zero_projects_still_shows_notices(self):
        # The zero-project fallback carries the failure reason in warnings —
        # they must render alongside the empty-state card.
        analysis = RoadmapAnalysis(
            source_type="local",
            source_label="q3.md",
            summary="",
            projects=(),
            warnings=("LLM request failed: Unknown LLM_PROVIDER: 'ollama'",),
            generated_at="2026-07-20T09:00:00",
        )
        out = _render_to_text(_build_roadmap_screen(self._data(analysis), height=40))
        assert "No projects extracted" in out
        assert "Notices" in out
        assert "Unknown LLM_PROVIDER" in out

    def test_long_project_list_scrolls_to_cursor(self):
        """With many projects and a small height, the cursor row stays visible."""
        analysis = _analysis(n_projects=20)
        panel = _build_roadmap_screen(self._data(analysis, cursor=19), height=24)
        out = _render_to_text(panel, height=24)
        assert "Project 19" in out

    def test_small_dimensions_do_not_crash(self):
        panel = _build_roadmap_screen(self._data(_analysis()), width=40, height=12)
        assert isinstance(panel, Panel)

    def test_cards_are_bordered(self):
        out = _render_to_text(_build_roadmap_screen(self._data(_analysis()), height=40))
        assert "╭" in out  # projects render as bordered card panels, not flat text

    def test_selected_card_shows_full_description(self):
        # Cursor on project 0 → its card expands to reveal the full description.
        out = _render_to_text(_build_roadmap_screen(self._data(_analysis(), cursor=0), height=40))
        assert "UNIQUEDESC0" in out

    def test_unselected_card_hides_description(self):
        # Project 1 is not selected, so its description stays hidden (compact card).
        out = _render_to_text(_build_roadmap_screen(self._data(_analysis(), cursor=0), height=40))
        assert "UNIQUEDESC1" not in out

    def test_notices_degrade_to_hint_on_small_height(self):
        # Too short for the full notices card → a one-line ⚠ hint instead. (The
        # compact two-line title frees ~4 rows vs the old tall wordmark, so the
        # threshold sits a few rows lower than before.)
        out = _render_to_text(_build_roadmap_screen(self._data(_analysis(), cursor=0), height=24), height=60)
        assert "enlarge the window to view" in out
        assert "Notices" not in out  # the multi-line notices card is suppressed


# NOTE: the old "list" view (saved-roadmaps list inside the roadmap page) was
# removed — saved roadmaps now render as amber-tagged cards inside the Planning
# "Your projects" list. See tests/test_mode_select.py + test_planning_rows.py.
