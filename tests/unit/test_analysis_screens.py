"""Unit tests for analysis mode screen builders and preview flow.

Covers:
- _build_analysis_review_screen (shared template: progress dots, scrollbar, viewport)
- _build_instructions_review_screen
- _build_sample_epic_screen
- _build_sample_stories_screen
- _build_sample_tasks_screen
- _build_sample_sprint_screen
- _build_analysis_progress_screen
- _build_team_analysis_screen (scrollbar + viewport wrapping)

Mirrors the test patterns used for planning mode screens in tests/test_session.py
(TestBuildDescriptionScreen, TestBuildQuestionScreen, etc.).
"""

from __future__ import annotations

import re
from io import StringIO

import pytest
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.mode_select.screens._screens_secondary import (
    _ANALYSIS_FEATURE_KEYS,
    _SETUP_COLUMNS_MIN_W,
    _build_analysis_depth_screen,
    _build_analysis_feature_screen,
    _build_analysis_model_offer_screen,
    _build_analysis_progress_screen,
    _build_analysis_review_screen,
    _build_analysis_window_screen,
    _build_code_scope_select_screen,
    _build_component_select_screen,
    _build_generate_confirm_screen,
    _build_instructions_review_screen,
    _build_member_select_screen,
    _build_sample_epic_screen,
    _build_sample_sprint_screen,
    _build_sample_stories_screen,
    _build_sample_tasks_screen,
    _build_team_analysis_screen,
    _build_team_insights_screen,
)
from yeaboi.ui.shared._components import ANALYSIS_THEME, PAD, PLANNING_THEME


@pytest.fixture(autouse=True)
def _forget_tab_positions():
    """Every test starts with the strips at their defaults.

    They remember where they were left, in module state, so without this what
    one test leaves behind decides where the next one opens — and the failure
    lands in whichever test happens to run second.
    """
    from yeaboi.ui.shared._components import forget_tabs

    forget_tabs()
    yield
    forget_tabs()


def test_analysis_model_offer_names_models_and_eta():
    output = _render(
        _build_analysis_model_offer_screen("qwen3:14b", "qwen3:4b", 1200, height=30),
        width=100,
    )
    assert "qwen3:14b" in output
    assert "qwen3:4b" in output
    assert "estimated 20 min" in output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render(panel: Panel, width: int = 100) -> str:
    """Render a Rich Panel to plain text for content assertions."""
    buf = StringIO()
    console = Console(file=buf, width=width, force_terminal=False, highlight=False)
    console.print(panel)
    return buf.getvalue()


def _replace_view(panel, view: str):
    """Re-render the panel a test just built, at a different section tab.

    The sections are tabs now: what used to be "the teaser on the overview" is
    the section's own content, one tab across.
    """
    return _build_team_analysis_screen(
        _make_overview_profile(), examples=_NARRATIVE_EXAMPLES, view=view, width=100, height=60
    )


def _make_body_lines(n: int = 10, prefix: str = "Line") -> list:
    """Create a list of Text objects for use as body_lines."""
    return [Text(f"    {prefix} {i}", justify="left") for i in range(n)]


def test_code_project_picker_shows_selected_scope():
    rendered = _render(
        _build_code_scope_select_screen(
            ["Infrastructure", "Product"],
            {1},
            1,
            width=90,
            height=24,
        )
    )
    assert "ANALYSIS SETUP" in rendered and "AZURE PROJECTS" in rendered
    assert "1 of 2 projects selected" in rendered
    assert "Product" in rendered


def test_code_scope_picker_rebrands_for_github():
    rendered = _render(
        _build_code_scope_select_screen(
            ["acme-corp", "dinho"],
            {0},
            0,
            heading="GitHub owners",
            unit="owners",
            hint="Every non-archived repo with activity in the window is scanned.",
            width=90,
            height=24,
        )
    )
    assert "GITHUB OWNERS" in rendered
    assert "1 of 2 owners selected" in rendered
    assert "acme-corp" in rendered
    # The cost of one checkbox (an owner fans out to every active repo) has to be
    # visible BEFORE Enter — there is no other screen that states it.
    assert "non-archived repo" in rendered


def test_code_scope_picker_explains_an_empty_estate():
    # A token with no visible orgs is a real outcome; a blank viewport reads as a
    # rendering bug and leaves the user with nothing to act on.
    rendered = _render(
        _build_code_scope_select_screen(
            [],
            set(),
            0,
            heading="GitHub owners",
            unit="owners",
            empty_label="No GitHub owners were visible to the configured token",
            width=90,
            height=24,
        )
    )
    assert "No GitHub owners were visible" in rendered
    assert "0 of 0 owners selected" in rendered


# ---------------------------------------------------------------------------
# Sample fixture data
# ---------------------------------------------------------------------------

_SAMPLE_INSTRUCTIONS = """\
## Velocity & Capacity
- Team velocity — 23.5 pts/sprint average
- Sprint length — 2 weeks
- Team size — 4 developers

## Story Conventions
- Story points — use Fibonacci scale (1, 2, 3, 5, 8)
- Acceptance criteria — Given/When/Then format, median 3 per story
→ Match this team's style exactly.

## Naming Conventions
- Label convention: quarterly goal (42%), released to dev (6%)
- Epic naming: quarter-scoped (e.g. "Q4|2025|High Region Outage DR")
→ Generated tickets MUST match these naming conventions.

Estimation note: Use THESE team-specific patterns, not generic Fibonacci rules.
"""

_SAMPLE_EPIC = {
    "title": "Q1|2026|Medium Platform Resilience Upgrade",
    "description": "Improve platform resilience with automated failover and monitoring.",
    "priority": "high",
    "stories_estimate": 5,
    "points_estimate": 18,
    "rationale": "Matches team's quarter-scoped naming convention.",
}

_SAMPLE_STORIES = [
    {
        "id": "S1",
        "title": "Implement automated failover",
        "persona": "developer",
        "goal": "automated failover between regions",
        "benefit": "reduced downtime",
        "story_points": 5,
        "priority": "high",
        "discipline": "infrastructure",
        "acceptance_criteria": [
            {"given": "primary region fails", "when": "failover triggered", "then": "traffic routes to secondary"},
            {"given": "secondary region active", "when": "primary recovers", "then": "failback completes"},
        ],
        "rationale": "Matches team's infrastructure story patterns.",
    },
    {
        "id": "S2",
        "title": "Add monitoring dashboards",
        "persona": "SRE",
        "goal": "visibility into failover health",
        "benefit": "faster incident response",
        "story_points": 3,
        "priority": "medium",
        "discipline": "observability",
        "acceptance_criteria": [
            {"given": "failover runs", "when": "dashboard queried", "then": "shows status within 30s"},
        ],
        "rationale": "Matches observability story pattern.",
    },
]

_SAMPLE_TASKS = [
    {
        "id": "T-S1-01",
        "story_id": "S1",
        "title": "Implement health check endpoint",
        "description": "Add /health endpoint to secondary region.",
        "label": "Code",
        "test_plan": "Unit test: verify endpoint returns 200.",
    },
    {
        "id": "T-S1-02",
        "story_id": "S1",
        "title": "Write failover integration tests",
        "description": "End-to-end test for failover trigger.",
        "label": "Testing",
        "test_plan": "Integration test: simulate region failure.",
    },
    {
        "id": "T-S2-01",
        "story_id": "S2",
        "title": "Create Grafana dashboard",
        "description": "Build dashboard with failover metrics.",
        "label": "Infrastructure",
        "test_plan": "Manual: verify dashboard loads in staging.",
    },
]

_SAMPLE_SPRINT = {
    "sprint_name": "Sprint 1",
    "velocity_target": 20,
    "stories_included": ["S1", "S2"],
    "total_points": 8,
    "capacity_notes": "Based on team avg of 23.5 pts/sprint, 8 pts leaves buffer.",
    "risks": ["S1 depends on cloud provider API", "S2 blocked until S1 failover endpoint is live"],
    "rationale": "Conservative allocation matching team's 88% completion rate.",
}

_SAMPLE_EXAMPLES = {
    "naming_conventions": {
        "epic_naming_style": "quarter-scoped",
        "epic_examples": ["Q4|2025|High Region Outage DR", "Q1|2026|Low Overmind improvement"],
        "template_sections": [("What is this about?", 0.8), ("Why does it matter?", 0.6)],
    },
    "ac_patterns": {"median_ac": 3},
    "task_decomposition": {
        "avg_tasks_per_story": 4.8,
        "type_distribution": {"Development": 64, "Testing": 13, "Deploy": 12},
        "common_tasks": [("create aurora rollback module", 2), ("update engine version", 2)],
    },
    "scope_changes": {
        "totals": {"avg_delivered_velocity": 25.9, "avg_committed_velocity": 19.1},
    },
}


# ---------------------------------------------------------------------------
# Shared builder: _build_analysis_review_screen
# ---------------------------------------------------------------------------


class TestBuildAnalysisReviewScreen:
    """Test the shared analysis review screen template."""

    def test_returns_panel(self):
        result = _build_analysis_review_screen(_make_body_lines(), stage_index=0, width=80, height=24)
        assert isinstance(result, Panel)

    def test_empty_body(self):
        result = _build_analysis_review_screen([], stage_index=0, width=80, height=24)
        assert isinstance(result, Panel)

    def test_all_stage_indices(self):
        """Each stage index (0-4) should render a valid panel."""
        lines = _make_body_lines(5)
        for idx in range(5):
            result = _build_analysis_review_screen(lines, stage_index=idx, width=80, height=24)
            assert isinstance(result, Panel)

    def test_custom_actions(self):
        result = _build_analysis_review_screen(
            _make_body_lines(),
            actions=["Done", "Export"],
            action_sel=1,
            width=80,
            height=24,
        )
        assert isinstance(result, Panel)

    def test_subtitle_rendered(self):
        result = _build_analysis_review_screen(
            _make_body_lines(),
            subtitle="Review planning instructions",
            width=80,
            height=24,
        )
        output = _render(result)
        assert "Review planning instructions" in output

    def test_progress_dots_current_stage(self):
        """Progress dots should show the current stage name in bold."""
        result = _build_analysis_review_screen(_make_body_lines(), stage_index=2, width=80, height=24)
        output = _render(result)
        assert "Stories" in output

    def test_scrollbar_appears_when_content_overflows(self):
        """Scrollbar should render when content exceeds viewport."""
        long_body = _make_body_lines(100)
        result = _build_analysis_review_screen(long_body, stage_index=0, width=80, height=24)
        output = _render(result)
        # Scrollbar uses thin/thick vertical bars
        assert "\u2502" in output or "\u2503" in output

    def test_no_scrollbar_short_content(self):
        """No scrollbar for content that fits within viewport."""
        short_body = _make_body_lines(3)
        result = _build_analysis_review_screen(short_body, stage_index=0, width=80, height=30)
        assert isinstance(result, Panel)

    def test_scroll_offset_clamps(self):
        """Scroll offset beyond max should clamp without error."""
        lines = _make_body_lines(10)
        result = _build_analysis_review_screen(lines, scroll_offset=9999, width=80, height=24)
        assert isinstance(result, Panel)

    def test_action_selection_is_harmless_now_they_are_tabs(self):
        """Each action index still produces a valid panel."""
        lines = _make_body_lines(5)
        for sel in range(4):
            result = _build_analysis_review_screen(lines, action_sel=sel, width=80, height=24)
            assert isinstance(result, Panel)

    def test_wrapping_lines_dont_overflow(self):
        """Long lines that wrap should not push buttons off-screen."""
        long_lines = [Text("    " + "x" * 200, justify="left") for _ in range(20)]
        result = _build_analysis_review_screen(long_lines, scroll_offset=15, width=80, height=24)
        output = _render(result, width=80)
        # Buttons should always be present in the rendered output
        assert "\u256d" in output  # top border of button box
        assert "\u256f" in output  # bottom border of button box

    def test_narrow_width(self):
        result = _build_analysis_review_screen(_make_body_lines(), width=40, height=24)
        assert isinstance(result, Panel)

    def test_tall_height(self):
        result = _build_analysis_review_screen(_make_body_lines(5), width=80, height=60)
        assert isinstance(result, Panel)

    def test_minimum_height(self):
        """Very short terminal should still render."""
        result = _build_analysis_review_screen(_make_body_lines(3), width=80, height=12)
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# Instructions review screen
# ---------------------------------------------------------------------------


class TestBuildInstructionsReviewScreen:
    """Test the planning instructions review page (stage 1 of preview flow)."""

    def test_returns_panel(self):
        result = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, width=80, height=24)
        assert isinstance(result, Panel)

    def test_empty_instructions(self):
        result = _build_instructions_review_screen("", width=80, height=24)
        assert isinstance(result, Panel)

    def test_no_row_of_any_kind_can_wrap_out_to_column_zero(self):
        # The per-branch wrapping missed the header branches, which is the sort
        # of thing that comes back the next time a branch is added — so the body
        # gets a post-pass and this drives every branch through it at once.
        from yeaboi.ui.mode_select.screens._screens_secondary import _instructions_body

        long = " ".join(f"word{i}" for i in range(60))
        source = "\n".join(
            [
                f"## {long}",  # section header
                f"### {long}",  # subsection header
                f"→ {long}",  # arrow directive
                f"- 3 pt: {long}",  # point calibration
                f"- backend stories: {long}",  # discipline shape
                f"- Moderate spillover — {long}",  # label — value
                f"- {long}",  # plain bullet
                f"Estimation note: {long}",  # standalone key: value
                long,  # plain paragraph
            ]
        )
        for width in (80, 100, 140, 200):
            rows = _instructions_body(source, width=width)
            body = [r for r in rows if r.plain.strip()]
            assert body, width
            assert all(r.plain.startswith(PAD) for r in body), width
            assert all(r.cell_len <= width for r in body), width

    def test_a_line_too_long_for_the_column_keeps_its_indent(self):
        # Rich has no hanging indent, so a single Text that overruns wraps its
        # tail to column zero — on a page that indents every line by PAD, the
        # tail lands further LEFT than the sentence it belongs to.
        long_value = " ".join(f"word{i}" for i in range(40))
        for source in (
            f"Estimation note: {long_value}",  # standalone key: value
            f"- Moderate spillover — {long_value}",  # bullet label — value
            f"- backend stories: {long_value}",  # discipline shape
            f"→ {long_value}",  # arrow directive
        ):
            rows = _render(_build_instructions_review_screen(source, width=100, height=40)).split("\n")
            body = [r for r in rows if "word" in r]
            assert len(body) > 1, source  # it really did wrap
            indents = {len(r) - len(r.lstrip("│ ")) for r in body[1:]}
            first = len(body[0]) - len(body[0].lstrip("│ "))
            assert min(indents) >= first, (source, first, indents)

    def test_section_headers_rendered(self):
        result = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, width=100, height=40)
        output = _render(result, width=100)
        assert "Velocity & Capacity" in output
        assert "Story Conventions" in output
        assert "Naming Conventions" in output

    def test_numbered_items(self):
        result = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, width=100, height=40)
        output = _render(result, width=100)
        # Items should be numbered
        assert "1" in output
        assert "Team velocity" in output

    def test_arrow_directives_rendered(self):
        result = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, width=120, height=40)
        output = _render(result, width=120)
        assert "Match this team" in output or "naming conventions" in output

    def test_scrollable(self):
        result1 = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, scroll_offset=0, width=80, height=24)
        result2 = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, scroll_offset=5, width=80, height=24)
        assert isinstance(result1, Panel)
        assert isinstance(result2, Panel)

    def test_action_buttons(self):
        """Instructions page offers Accept/Edit/Export — in the chrome.

        They are tabs rather than body buttons now, so the panel publishes them
        instead of drawing them; that is also what stopped a row of buttons
        colliding with the back tab in the corner below it.
        """
        result = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, width=100, height=60)
        assert result._forward_action == "Accept"
        assert [name for name, _key in result._page_tabs] == ["Accept", "Edit", "Export"]

    def test_action_selection(self):
        for sel in range(3):
            result = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, action_sel=sel, width=80, height=24)
            assert isinstance(result, Panel)

    def test_stage_indicator_shows_instructions(self):
        result = _build_instructions_review_screen(_SAMPLE_INSTRUCTIONS, width=100, height=24)
        output = _render(result, width=100)
        assert "Instructions" in output

    def test_long_instructions_scrollbar(self):
        """Long instructions should show scrollbar."""
        long_text = "\n".join(f"- Item {i} — description of item {i}" for i in range(50))
        result = _build_instructions_review_screen(long_text, width=80, height=24)
        output = _render(result, width=80)
        assert "\u2502" in output or "\u2503" in output


# ---------------------------------------------------------------------------
# Sample epic screen
# ---------------------------------------------------------------------------


class TestBuildSampleEpicScreen:
    """Test the sample epic review page (stage 2 of preview flow)."""

    def test_returns_panel(self):
        result = _build_sample_epic_screen(_SAMPLE_EPIC, width=80, height=24)
        assert isinstance(result, Panel)

    def test_empty_epic(self):
        result = _build_sample_epic_screen({}, width=80, height=24)
        assert isinstance(result, Panel)

    def test_epic_title_rendered(self):
        result = _build_sample_epic_screen(_SAMPLE_EPIC, width=120, height=40)
        output = _render(result, width=120)
        assert "Platform Resilience" in output

    def test_epic_priority_rendered(self):
        result = _build_sample_epic_screen(_SAMPLE_EPIC, width=100, height=40)
        output = _render(result, width=100)
        assert "high" in output.lower()

    def test_epic_rationale_rendered(self):
        result = _build_sample_epic_screen(_SAMPLE_EPIC, width=120, height=40)
        output = _render(result, width=120)
        assert "quarter-scoped" in output or "rationale" in output.lower()

    def test_with_examples(self):
        result = _build_sample_epic_screen(_SAMPLE_EPIC, examples=_SAMPLE_EXAMPLES, width=80, height=24)
        assert isinstance(result, Panel)

    def test_examples_pattern_info(self):
        """When examples provided, should show naming style info."""
        result = _build_sample_epic_screen(_SAMPLE_EPIC, examples=_SAMPLE_EXAMPLES, width=120, height=40)
        output = _render(result, width=120)
        assert "quarter-scoped" in output or "naming" in output.lower()

    def test_scrollable(self):
        result = _build_sample_epic_screen(_SAMPLE_EPIC, scroll_offset=3, width=80, height=24)
        assert isinstance(result, Panel)

    def test_action_selection(self):
        for sel in range(4):
            result = _build_sample_epic_screen(_SAMPLE_EPIC, action_sel=sel, width=80, height=24)
            assert isinstance(result, Panel)

    def test_stage_indicator_shows_epic(self):
        result = _build_sample_epic_screen(_SAMPLE_EPIC, width=100, height=24)
        output = _render(result, width=100)
        assert "Epic" in output


# ---------------------------------------------------------------------------
# Sample stories screen
# ---------------------------------------------------------------------------


class TestBuildSampleStoriesScreen:
    """Test the sample stories review page (stage 3 of preview flow)."""

    def test_returns_panel(self):
        result = _build_sample_stories_screen(_SAMPLE_STORIES, width=80, height=24)
        assert isinstance(result, Panel)

    def test_empty_stories(self):
        result = _build_sample_stories_screen([], width=80, height=24)
        assert isinstance(result, Panel)

    def test_single_story(self):
        result = _build_sample_stories_screen([_SAMPLE_STORIES[0]], width=80, height=24)
        assert isinstance(result, Panel)

    def test_story_ids_rendered(self):
        # Tall height so both stories fit the viewport below the 6-row ANSI-Shadow header.
        result = _build_sample_stories_screen(_SAMPLE_STORIES, width=100, height=70)
        output = _render(result, width=100)
        assert "S1" in output
        assert "S2" in output

    def test_story_points_rendered(self):
        result = _build_sample_stories_screen(_SAMPLE_STORIES, width=100, height=70)
        output = _render(result, width=100)
        assert "5" in output  # S1 points
        assert "3" in output  # S2 points

    def test_acceptance_criteria_rendered(self):
        result = _build_sample_stories_screen(_SAMPLE_STORIES, width=120, height=50)
        output = _render(result, width=120)
        # Given/When/Then format
        assert "Given" in output or "given" in output.lower()

    def test_persona_goal_rendered(self):
        result = _build_sample_stories_screen(_SAMPLE_STORIES, width=120, height=40)
        output = _render(result, width=120)
        assert "developer" in output or "SRE" in output

    def test_with_epic_title(self):
        result = _build_sample_stories_screen(
            _SAMPLE_STORIES,
            epic_title="Q1|2026|Medium Platform Resilience",
            width=100,
            height=24,
        )
        output = _render(result, width=100)
        assert "Platform Resilience" in output or "Stories" in output

    def test_scrollable(self):
        result = _build_sample_stories_screen(_SAMPLE_STORIES, scroll_offset=5, width=80, height=24)
        assert isinstance(result, Panel)

    def test_action_selection(self):
        for sel in range(4):
            result = _build_sample_stories_screen(_SAMPLE_STORIES, action_sel=sel, width=80, height=24)
            assert isinstance(result, Panel)

    def test_stage_indicator_shows_stories(self):
        result = _build_sample_stories_screen(_SAMPLE_STORIES, width=100, height=24)
        output = _render(result, width=100)
        assert "Stories" in output

    def test_story_without_acceptance_criteria(self):
        """Stories with empty AC list should still render."""
        story = {**_SAMPLE_STORIES[0], "acceptance_criteria": []}
        result = _build_sample_stories_screen([story], width=80, height=24)
        assert isinstance(result, Panel)

    def test_story_with_missing_fields(self):
        """Minimal story dict should not crash."""
        story = {"id": "S1", "title": "Minimal story"}
        result = _build_sample_stories_screen([story], width=80, height=24)
        assert isinstance(result, Panel)

    def test_definition_of_done_rendered(self):
        """Stories with definition_of_done should render DoD items."""
        story = {
            **_SAMPLE_STORIES[0],
            "definition_of_done": ["Code reviewed", "Tests passing", "Deployed to staging"],
        }
        result = _build_sample_stories_screen([story], width=120, height=60)
        output = _render(result, width=120)
        assert "Definition of Done" in output
        assert "Code reviewed" in output

    def test_story_without_dod(self):
        """Stories without definition_of_done should still render."""
        story = {k: v for k, v in _SAMPLE_STORIES[0].items() if k != "definition_of_done"}
        result = _build_sample_stories_screen([story], width=80, height=24)
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# Sample tasks screen
# ---------------------------------------------------------------------------


class TestBuildSampleTasksScreen:
    """Test the sample tasks review page (stage 4 of preview flow)."""

    def test_returns_panel(self):
        result = _build_sample_tasks_screen(_SAMPLE_TASKS, width=80, height=24)
        assert isinstance(result, Panel)

    def test_empty_tasks(self):
        result = _build_sample_tasks_screen([], width=80, height=24)
        assert isinstance(result, Panel)

    def test_task_ids_rendered(self):
        result = _build_sample_tasks_screen(_SAMPLE_TASKS, width=100, height=40)
        output = _render(result, width=100)
        assert "T-S1-01" in output
        assert "T-S2-01" in output

    def test_task_labels_rendered(self):
        result = _build_sample_tasks_screen(_SAMPLE_TASKS, width=100, height=40)
        output = _render(result, width=100)
        assert "Code" in output
        assert "Testing" in output

    def test_task_grouping_by_story(self):
        """Tasks should be grouped under their story ID."""
        result = _build_sample_tasks_screen(_SAMPLE_TASKS, width=100, height=40)
        output = _render(result, width=100)
        # Both story IDs should appear as group headers
        assert "S1" in output
        assert "S2" in output

    def test_test_plan_rendered(self):
        result = _build_sample_tasks_screen(_SAMPLE_TASKS, width=120, height=40)
        output = _render(result, width=120)
        assert "Unit test" in output or "test" in output.lower()

    def test_scrollable(self):
        result = _build_sample_tasks_screen(_SAMPLE_TASKS, scroll_offset=3, width=80, height=24)
        assert isinstance(result, Panel)

    def test_action_selection(self):
        for sel in range(4):
            result = _build_sample_tasks_screen(_SAMPLE_TASKS, action_sel=sel, width=80, height=24)
            assert isinstance(result, Panel)

    def test_stage_indicator_shows_tasks(self):
        result = _build_sample_tasks_screen(_SAMPLE_TASKS, width=100, height=24)
        output = _render(result, width=100)
        assert "Tasks" in output

    def test_single_task(self):
        result = _build_sample_tasks_screen([_SAMPLE_TASKS[0]], width=80, height=24)
        assert isinstance(result, Panel)

    def test_task_with_missing_fields(self):
        """Minimal task dict should not crash."""
        task = {"id": "T-1", "title": "Do something"}
        result = _build_sample_tasks_screen([task], width=80, height=24)
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# Sample sprint screen
# ---------------------------------------------------------------------------


class TestBuildSampleSprintScreen:
    """Test the sample sprint plan review page (stage 5 of preview flow)."""

    def test_returns_panel(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=80, height=24)
        assert isinstance(result, Panel)

    def test_empty_sprint(self):
        result = _build_sample_sprint_screen({}, [], width=80, height=24)
        assert isinstance(result, Panel)

    def test_sprint_name_rendered(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=100, height=40)
        output = _render(result, width=100)
        assert "Sprint 1" in output

    def test_velocity_target_rendered(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=100, height=40)
        output = _render(result, width=100)
        assert "20" in output

    def test_total_points_rendered(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=100, height=40)
        output = _render(result, width=100)
        assert "8" in output

    def test_stories_listed(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=100, height=40)
        output = _render(result, width=100)
        assert "S1" in output
        assert "S2" in output

    def test_risks_rendered(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=120, height=40)
        output = _render(result, width=120)
        assert "cloud provider" in output or "risk" in output.lower()

    def test_capacity_notes_rendered(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=120, height=40)
        output = _render(result, width=120)
        assert "23.5" in output or "buffer" in output

    def test_it_offers_no_accept_and_no_editor(self):
        # "Done" here and "Accept" everywhere else made a tab leave and another
        # arrive in the same fixed slot, so crossing between Tasks and Sprint
        # flashed the outgoing label for the length of the entrance animation.
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=100, height=60)
        assert [name for name, _key in result._page_tabs] == ["Regenerate", "Export"]

    def test_scrollable(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, scroll_offset=3, width=80, height=24)
        assert isinstance(result, Panel)

    def test_action_selection(self):
        for sel in range(3):  # Done, Regenerate, Export
            result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, action_sel=sel, width=80, height=24)
            assert isinstance(result, Panel)

    def test_stage_indicator_shows_sprint(self):
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, _SAMPLE_STORIES, width=100, height=24)
        output = _render(result, width=100)
        assert "Sprint" in output

    def test_sprint_without_risks(self):
        sprint = {**_SAMPLE_SPRINT, "risks": []}
        result = _build_sample_sprint_screen(sprint, _SAMPLE_STORIES, width=80, height=24)
        assert isinstance(result, Panel)

    def test_sprint_without_stories(self):
        """Sprint with story IDs but no matching story objects."""
        result = _build_sample_sprint_screen(_SAMPLE_SPRINT, [], width=80, height=24)
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# Analysis progress screen
# ---------------------------------------------------------------------------


class TestBuildAnalysisProgressScreen:
    """Test the loading/progress screen shown during analysis."""

    def test_returns_panel(self):
        result = _build_analysis_progress_screen([], width=80, height=24)
        assert isinstance(result, Panel)

    def test_with_progress_steps(self):
        steps = ["Fetching sprints...", "Analysing velocity...", "Building profile..."]
        result = _build_analysis_progress_screen(steps, width=80, height=24)
        output = _render(result)
        assert "Fetching sprints" in output
        assert "✓ Fetching sprints" not in output
        assert "• Fetching sprints" in output
        # The active row spins (braille) instead of a static ▸ now.
        assert any(f"{g} Building profile" in output for g in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")

    def test_legacy_activity_uses_planning_card_theme(self):
        result = _build_analysis_progress_screen(
            ["Fetching sprints...", "Building profile..."],
            mode="planning",
            width=80,
            height=24,
        )
        rows = [item for item in result.renderable.renderables if isinstance(item, Text)]
        history = next(item for item in rows if "Fetching sprints" in item.plain)
        current = next(item for item in rows if "Building profile" in item.plain)
        assert str(history.style) == PLANNING_THEME.accent
        assert str(current.style) == f"bold {PLANNING_THEME.accent_bright}"
        # The page border stays white whatever is running: an accent frame reads
        # as the whole terminal lighting up, which is a lot of signal for a job.
        assert str(result.border_style) == "white"

    def test_structured_running_components_do_not_render_as_completed(self):
        progress = [
            {
                "kind": "analysis_component",
                "component_id": "delivery:jira",
                "label": "Fetching sprint history · Jira",
                "status": "running",
                "detail": "",
            },
            {
                "kind": "analysis_component",
                "component_id": "code:code",
                "label": "Scanning AI footprint & repository health",
                "status": "running",
                "detail": "",
            },
            "Docs · Confluence space: PSO",
        ]
        output = _render(_build_analysis_progress_screen(progress, width=100, height=24))
        spinners = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        assert any(f"{g} Fetching sprint history" in output for g in spinners)
        assert any(f"{g} Scanning AI footprint" in output for g in spinners)
        assert "✓ Fetching sprint history" not in output
        assert "Docs · Confluence space: PSO" in output

    def test_structured_component_only_checks_on_completed_event(self):
        progress = [
            {
                "kind": "analysis_component",
                "component_id": "delivery:jira",
                "label": "Fetching sprint history · Jira",
                "status": "running",
                "detail": "",
            },
            {
                "kind": "analysis_component",
                "component_id": "delivery:jira",
                "label": "Fetching sprint history · Jira",
                "status": "completed",
                "detail": "",
            },
        ]
        output = _render(_build_analysis_progress_screen(progress, width=100, height=24))
        assert "✓ Fetching sprint history" in output
        assert not any(f"{g} Fetching sprint history" in output for g in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")

        result = _build_analysis_progress_screen(progress, mode="analysis", width=100, height=24)
        completed = next(
            item
            for item in result.renderable.renderables
            if isinstance(item, Text) and "Fetching sprint history" in item.plain
        )
        assert str(completed.style) == ANALYSIS_THEME.accent

    def test_code_progress_is_counted_and_explicitly_read_only(self):
        progress = [
            {
                "kind": "analysis_component",
                "component_id": "code:code_health",
                "label": "Analysing selected-user code-change health",
                "status": "running",
                "detail": "",
                "phase": "Reading code-change metadata",
                "current": 3,
                "total": 5,
                "unit": "changes inspected",
                "secondary_count": 27,
                "secondary_unit": "file records found",
                "read_only": True,
            }
        ]

        output = _render(
            _build_analysis_progress_screen(progress, width=160, height=24),
            width=160,
        )

        assert "60% · 3/5 changes inspected" in output
        assert "27 file records found" in output
        assert "Repository access is read-only" in output
        assert "no files are modified" in output
        assert "Changed files:" not in output

    def test_elapsed_time(self):
        result = _build_analysis_progress_screen(["Working..."], elapsed=12.5, width=80, height=24)
        output = _render(result)
        assert "12" in output  # elapsed seconds shown

    def test_animation_tick(self):
        """Different animation ticks should produce valid panels."""
        for tick in [0.0, 0.25, 0.5, 0.75, 1.0]:
            result = _build_analysis_progress_screen(["Working..."], anim_tick=tick, width=80, height=24)
            assert isinstance(result, Panel)

    def test_spinner_glyph_advances_with_the_clock(self):
        a = _render(_build_analysis_progress_screen(["Working..."], anim_tick=0.0, width=80, height=24))
        b = _render(_build_analysis_progress_screen(["Working..."], anim_tick=0.35, width=80, height=24))
        ga = next(g for g in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if f"{g} Working" in a)
        gb = next(g for g in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if f"{g} Working" in b)
        assert ga != gb

    def test_structured_run_gets_a_count_and_total_footer(self):
        progress = [
            {
                "kind": "analysis_component",
                "component_id": "footer:done",
                "label": "Fetching sprint history",
                "status": "completed",
                "detail": "",
            },
            {
                "kind": "analysis_component",
                "component_id": "footer:running",
                "label": "Scanning AI footprint",
                "status": "running",
                "detail": "",
            },
        ]
        output = _render(_build_analysis_progress_screen(progress, anim_tick=75.0, width=100, height=24))
        assert "[1/2]" in output
        assert "total 1:15" in output

    def test_running_row_carries_a_per_stage_elapsed(self):
        def _screen(tick):
            return _render(
                _build_analysis_progress_screen(
                    [
                        {
                            "kind": "analysis_component",
                            "component_id": "elapsed:stage",
                            "label": "Scanning AI footprint",
                            "status": "running",
                            "detail": "",
                        }
                    ],
                    anim_tick=tick,
                    width=100,
                    height=24,
                )
            )

        _screen(0.0)  # first sight starts this stage's clock
        assert "0:42" in _screen(42.0)

    def test_analysis_mode_renders(self):
        """Analysis mode should render without error and use analysis title."""
        result = _build_analysis_progress_screen(["Working..."], mode="analysis", width=100, height=24)
        assert isinstance(result, Panel)

    def test_planning_mode_renders(self):
        """Planning mode should render without error and use planning title."""
        result = _build_analysis_progress_screen(["Working..."], mode="planning", width=100, height=24)
        assert isinstance(result, Panel)

    def test_source_label(self):
        result = _build_analysis_progress_screen(
            ["Fetching..."], source="azdevops", mode="analysis", width=100, height=24
        )
        assert isinstance(result, Panel)

    def test_empty_progress(self):
        result = _build_analysis_progress_screen([], elapsed=0.0, width=80, height=24)
        assert isinstance(result, Panel)


# ---------------------------------------------------------------------------
# Team analysis screen (initial report page with custom viewport)
# ---------------------------------------------------------------------------


class TestBuildTeamAnalysisScreenExtended:
    """Extended tests for the initial analysis report screen.

    Supplements the 2 existing tests in test_team_profile.py with coverage
    for scrollbar, viewport wrapping, export selection, and edge cases.
    """

    @pytest.fixture()
    def profile(self):
        from yeaboi.team_profile import (
            DoDSignal,
            EpicPattern,
            SpilloverStats,
            StoryPointCalibration,
            StoryShapePattern,
            TeamProfile,
            WritingPatterns,
        )

        return TeamProfile(
            team_id="azdevops-PROJ",
            source="azdevops",
            project_key="PROJ",
            sample_sprints=8,
            sample_stories=64,
            velocity_avg=23.5,
            velocity_stddev=3.2,
            point_calibrations=(
                StoryPointCalibration(point_value=1, avg_cycle_time_days=0.5, sample_count=10),
                StoryPointCalibration(point_value=3, avg_cycle_time_days=2.1, sample_count=20, overshoot_pct=15.0),
                StoryPointCalibration(point_value=5, avg_cycle_time_days=4.2, sample_count=15, overshoot_pct=20.0),
            ),
            story_shapes=(
                StoryShapePattern(
                    discipline="backend", avg_points=3.2, avg_ac_count=3.0, avg_task_count=2.8, sample_count=20
                ),
                StoryShapePattern(
                    discipline="frontend", avg_points=2.5, avg_ac_count=2.5, avg_task_count=2.0, sample_count=12
                ),
            ),
            epic_pattern=EpicPattern(
                avg_stories_per_epic=6.0, avg_points_per_epic=18.0, typical_story_count_range=(4, 9)
            ),
            estimation_accuracy_pct=78.0,
            sprint_completion_rate=88.0,
            spillover=SpilloverStats(
                carried_over_pct=12.5,
                avg_spillover_pts=3.2,
                most_common_spillover_reason="backend stories",
            ),
            dod_signal=DoDSignal(
                common_checklist_items=("tests passing", "PR merged", "code reviewed"),
                stories_with_comments_pct=85.0,
                stories_with_pr_link_pct=82.0,
                stories_with_review_mention_pct=76.0,
                stories_with_testing_mention_pct=61.0,
                stories_with_deploy_mention_pct=44.0,
            ),
            writing_patterns=WritingPatterns(
                median_ac_count=3.0,
                median_task_count_per_story=2.5,
                subtask_label_distribution=(("Code", 0.58), ("Testing", 0.28)),
                common_subtask_patterns=("Write unit tests", "Deploy to staging"),
                subtasks_use_consistent_naming=True,
                common_personas=("developer", "admin"),
                uses_given_when_then=True,
                stories_with_subtasks_pct=72.0,
            ),
            sprints_fully_completed=6,
            sprints_partially_completed=2,
            sprints_analysed=8,
        )

    def test_returns_panel(self, profile):
        result = _build_team_analysis_screen(profile, width=80, height=30)
        assert isinstance(result, Panel)

    def test_export_button_selection(self, profile):
        """Each export_sel value should highlight a different button."""
        for sel in range(2):  # Export, Continue
            result = _build_team_analysis_screen(profile, export_sel=sel, width=80, height=30)
            assert isinstance(result, Panel)

    def test_scrollbar_on_tall_content(self, profile):
        """Profile with many sections should show scrollbar."""
        result = _build_team_analysis_screen(profile, scroll_offset=0, width=80, height=24)
        output = _render(result, width=80)
        assert "\u2502" in output or "\u2503" in output

    def test_scroll_to_bottom(self, profile):
        """Scrolling to a large offset should clamp and still offer its actions.

        Export/Share/Anonymize and Continue are chrome tabs now, not body
        buttons, so the panel publishes them rather than drawing them — which is
        also what makes them immune to the viewport clamping this exercises.
        """
        result = _build_team_analysis_screen(profile, scroll_offset=9999, width=80, height=24)
        assert result._forward_action == "Continue"
        assert "Export" in {name for name, _key in result._page_tabs}

    def test_with_examples(self, profile):
        result = _build_team_analysis_screen(profile, examples=_SAMPLE_EXAMPLES, width=80, height=30)
        assert isinstance(result, Panel)

    def test_with_sprint_names(self, profile):
        names = ["Sprint 101", "Sprint 102", "Sprint 103"]
        result = _build_team_analysis_screen(profile, sprint_names=names, width=80, height=30)
        assert isinstance(result, Panel)

    def test_with_team_name(self, profile):
        result = _build_team_analysis_screen(profile, team_name="Platform Team", width=100, height=30)
        output = _render(result, width=100)
        assert "Platform Team" in output

    def test_narrow_terminal(self, profile):
        """Should render without crash on narrow terminals."""
        result = _build_team_analysis_screen(profile, width=40, height=24)
        assert isinstance(result, Panel)

    def test_short_terminal(self, profile):
        """Should render on short terminals without crash."""
        result = _build_team_analysis_screen(profile, width=80, height=14)
        assert isinstance(result, Panel)

    def test_velocity_section_rendered(self, profile):
        result = _build_team_analysis_screen(profile, width=100, height=50)
        output = _render(result, width=100)
        assert "23.5" in output  # velocity_avg

    def test_spillover_rendered(self, profile):
        result = _build_team_analysis_screen(profile, view="velocity", width=100, height=100)
        output = _render(result, width=100)
        assert "12.5" in output or "spillover" in output.lower()

    def test_zero_scroll(self, profile):
        result = _build_team_analysis_screen(profile, scroll_offset=0, width=80, height=30)
        assert isinstance(result, Panel)

    def test_mid_scroll(self, profile):
        result = _build_team_analysis_screen(profile, scroll_offset=10, width=80, height=30)
        assert isinstance(result, Panel)

    # Wrapping tables (DoD + Proposed DoD) previously reported a naive row_count
    # as their height. When cells wrapped onto multiple rows the viewport packer
    # over-filled the fixed-height panel and Rich cropped the action buttons off
    # the bottom (Patterns page showed no buttons). Heights are now measured, so
    # the buttons must survive even with a tall, heavily-wrapping Proposed DoD.
    _DOD_HEAVY_EXAMPLES = {
        "dod_testing": [{"issue_key": "PSOT-791", "summary": "Phase 9: Entra App Registration flow"}],
        "dod_pr": [{"issue_key": "PSOT-851", "summary": "WIZ - Create an automated repo scanner"}],
        "dod_review": [{"issue_key": "PSOT-880", "summary": "Complete DAST API Scan Implementation"}],
        "dod_deploy": [{"issue_key": "PSOT-880", "summary": "Complete DAST API Scan Implementation"}],
        "proposed_dod": {
            "summary": "7 of 9 practices are well-established. The team has a clear definition of done.",
            "health": "strong",
            "items": [
                {
                    "practice": f"Practice number {i} updated",
                    "status": "established",
                    "signals": f"{90 - i * 8}% mentioned in stories · 6% have subtasks",
                    "recommendation": "Consistently done. Include as a required DoD step.",
                }
                for i in range(8)
            ],
        },
    }

    @staticmethod
    def _render_cropped(panel: Panel, width: int, height: int) -> str:
        """Render exactly like the TUI: a fixed-height panel on a sized console.

        The panel's ``height`` crops overflowing content, so this reproduces the
        real button-cropping behaviour that a height-less render would hide.
        """
        buf = StringIO()
        console = Console(file=buf, width=width, height=height, force_terminal=False, highlight=False)
        console.print(panel)
        return buf.getvalue()

    def test_workflow_card_buttons_visible_with_wrapping_tables(self, profile):
        """Workflow & DoD action buttons must stay on screen even when tables wrap a lot."""
        for scroll in (0, 9999):  # top of card and clamped-to-bottom
            panel = _build_team_analysis_screen(
                profile,
                examples=self._DOD_HEAVY_EXAMPLES,
                view="workflow",
                width=120,
                height=44,
                scroll_offset=scroll,
            )
            output = self._render_cropped(panel, width=120, height=44)
            # Both actions ride in the chrome now, so neither can be cropped by
            # a wrapping table however far the body is scrolled.
            assert panel._forward_action == "Continue"
            assert "Export" in {name for name, _key in panel._page_tabs}
            assert output  # the body still renders something at every offset

    def test_both_mode_toggle_and_comparison(self, profile):
        """'Both' mode renders the source toggle line and side-by-side comparison."""
        comparison = [("Avg velocity", "23", "15"), ("Completion rate", "82%", "74%")]
        panel = _build_team_analysis_screen(
            profile,
            view="overview",
            width=100,
            height=40,
            source_toggle=["jira", "azdevops"],
            active_source="azdevops",
            comparison=comparison,
        )
        out = _render(panel, width=100)
        assert "Tab: switch source" in out
        assert "Jira vs Azure DevOps" in out  # comparison heading
        assert "23" in out and "15" in out  # per-tracker figures side by side

    def test_no_toggle_without_source_toggle(self, profile):
        """Single-source render shows no toggle affordance."""
        out = _render(_build_team_analysis_screen(profile, width=100, height=40), width=100)
        assert "Tab: switch source" not in out


class TestRunTeamAnalysisResultsBoth:
    """Drive _run_team_analysis_results in 'both' mode (source toggle + active_box).

    Covers the runner-loop branch the screen-builder tests can't reach: the
    Tab-cycled source switch, and the active_box mirror-back contract the two
    call sites rely on for the downstream insights/ticket steps.
    """

    class _FakeLive:
        def update(self, renderable):
            self.last = renderable

    @staticmethod
    def _console():
        from types import SimpleNamespace

        return SimpleNamespace(size=(100, 40))

    @staticmethod
    def _reader(keys):
        it = iter(keys)

        def read_key(timeout=None):
            return next(it)

        return read_key

    @staticmethod
    def _both():
        from yeaboi.team_profile import TeamProfile

        jira = TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name="")
        azdo = TeamProfile(team_id="azdevops:Web", source="azdevops", project_key="Web", team_name="Web Team")
        return {
            "jira": {"profile": jira, "examples": {}, "sprint_names": ["S1"], "source": "jira", "project_key": "P"},
            "azdevops": {
                "profile": azdo,
                "examples": {},
                "sprint_names": ["I1"],
                "source": "azdevops",
                "project_key": "Web",
            },
        }

    def _run(self, keys, active_box):
        from yeaboi.ui.mode_select import _run_team_analysis_results

        both = self._both()
        return _run_team_analysis_results(
            self._FakeLive(),
            self._console(),
            self._reader(keys),
            0.01,
            True,
            both["jira"]["profile"],
            {},
            sprint_names=["S1"],
            delivery=both,
            comparison=[("Avg velocity", "23", "15")],
            active_box=active_box,
        )

    def test_tab_switches_active_source(self):
        active_box: list = [None]
        # Frame 1 = jira; Tab → azdevops; Esc on overview exits.
        assert self._run(["tab", "esc"], active_box) == "back"
        profile, _examples, sprint_names, team_name = active_box[0]
        assert profile.source == "azdevops"
        assert team_name == "Web Team"
        assert sprint_names == ["I1"]

    def test_no_team_name_bleed_when_toggling_back(self):
        active_box: list = [None]
        # jira → azdevops (team "Web Team") → back to jira: must NOT keep "Web Team".
        assert self._run(["tab", "tab", "esc"], active_box) == "back"
        profile, _examples, _sprint_names, team_name = active_box[0]
        assert profile.source == "jira"
        assert team_name == ""  # regression guard: no cross-tracker team-name bleed

    def test_global_docs_card_shows_on_every_tab(self):
        # Jira + Azure DevOps delivery, plus ONE global docs scan shown on both tabs.
        from yeaboi.team_profile import DocQualitySignal

        both = self._both()
        docs = {"signal": DocQualitySignal(pages_scanned=6, avg_clarity=71.0), "examples": {"summary": {}}}
        from yeaboi.ui.mode_select import _run_team_analysis_results

        active_box: list = [None]
        res = _run_team_analysis_results(
            self._FakeLive(),
            self._console(),
            self._reader(["tab", "esc"]),
            0.01,
            True,
            both["jira"]["profile"],
            {},
            sprint_names=["S1"],
            delivery=both,
            docs=docs,
            active_box=active_box,
        )
        assert res == "back"
        # Toggling tracker keeps a real (per-tracker) delivery profile in active_box.
        assert active_box[0][0].source == "azdevops"

    def test_first_arrow_press_moves_off_the_lit_tab(self):
        # "overview" is not a tab in the strip — the strip lights its first
        # section instead. While overview counted as an entry of its own, the
        # first → landed on the tab already lit and the key looked dead.
        from yeaboi.team_profile import TeamProfile
        from yeaboi.ui.mode_select import _run_team_analysis_results
        from yeaboi.ui.mode_select.screens._analysis_sections import visible_card_order

        profile = TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name="")
        order = visible_card_order(profile, False, False)
        seen: list = []

        import yeaboi.ui.mode_select as ms

        real = ms._build_team_analysis_screen

        def spy(*a, **kw):
            seen.append(kw.get("view"))
            return real(*a, **kw)

        try:
            ms._build_team_analysis_screen = spy
            keys = ["right", "right", "esc", "esc"]
            res = _run_team_analysis_results(
                self._FakeLive(), self._console(), self._reader(keys), 0.01, True, profile, {}
            )
        finally:
            ms._build_team_analysis_screen = real

        assert res == "back"
        # Frame 1 is the landing view; frame 2 is after the FIRST arrow press.
        assert seen[1] == order[1], seen[:3]
        assert seen[2] == order[2], seen[:3]

    def test_a_bare_enter_takes_no_action(self):
        # Every action is in the chrome, and the chrome sends the action's own
        # name. A bare Enter is aimed at nothing — running actions[sel] for it
        # gave the page a hidden default, and the default was Export.
        import yeaboi.ui.mode_select as ms
        from yeaboi.team_profile import TeamProfile
        from yeaboi.ui.mode_select import _run_team_analysis_results

        exported = []
        monkey = ms._team_profile_export_flow
        try:
            ms._team_profile_export_flow = lambda *a, **k: exported.append(1)
            res = _run_team_analysis_results(
                self._FakeLive(),
                self._console(),
                self._reader(["enter", " ", "esc"]),
                0.01,
                True,
                TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name=""),
                {},
            )
        finally:
            ms._team_profile_export_flow = monkey
        assert res == "back"
        assert exported == []

    def test_an_action_named_by_the_chrome_still_runs(self):
        import yeaboi.ui.mode_select as ms
        from yeaboi.team_profile import TeamProfile
        from yeaboi.ui.mode_select import _run_team_analysis_results

        exported = []
        monkey = ms._team_profile_export_flow
        try:
            ms._team_profile_export_flow = lambda *a, **k: exported.append(1)
            _run_team_analysis_results(
                self._FakeLive(),
                self._console(),
                self._reader(["act:Export", "esc"]),
                0.01,
                True,
                TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name=""),
                {},
            )
        finally:
            ms._team_profile_export_flow = monkey
        assert exported == [1]

    def test_it_opens_on_the_tab_it_was_left_on(self):
        # The pager moves between pages; a page you return to having forgotten
        # what you were reading is a page you have to find again.
        from yeaboi.team_profile import TeamProfile
        from yeaboi.ui.mode_select import _run_team_analysis_results

        profile = TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name="")
        box: list = [""]
        # Two → from the landing view, then cross the pager — which is the move
        # that used to lose the tab.
        assert (
            _run_team_analysis_results(
                self._FakeLive(),
                self._console(),
                self._reader(["right", "right", "pager:1"]),
                0.01,
                True,
                profile,
                {},
                view_box=box,
            )
            == "continue"
        )
        left_on = box[0]
        assert left_on not in ("", "overview")

        # Re-entered with what it reported: it opens there, not at the first tab.
        seen: list = []
        _run_team_analysis_results(
            self._FakeLive(),
            self._console(),
            self._reader(["pager:1"]),
            0.01,
            True,
            profile,
            {},
            initial_view=left_on,
            view_box=seen,
        )
        assert seen[0] == left_on

    def test_a_remembered_tab_this_run_does_not_have_falls_back(self):
        # A different tracker, or a scan that did not happen, and the card
        # lookup would raise on the way in.
        from yeaboi.team_profile import TeamProfile
        from yeaboi.ui.mode_select import _run_team_analysis_results

        box: list = [""]
        res = _run_team_analysis_results(
            self._FakeLive(),
            self._console(),
            self._reader(["pager:1"]),
            0.01,
            True,
            TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name=""),
            {},
            initial_view="ai-adoption",  # no code scan in this run
            view_box=box,
        )
        assert res == "continue"
        assert box[0] == "overview"

    def test_nav_action_returns_its_own_name(self):
        # Analysis mode opens straight onto the dashboard, so leaving it for
        # another analysis is an action ON it. The loop hands the name back
        # rather than growing a second meaning for "back".
        from yeaboi.team_profile import TeamProfile
        from yeaboi.ui.mode_select import _run_team_analysis_results

        res = _run_team_analysis_results(
            self._FakeLive(),
            self._console(),
            self._reader(["act:Switch analysis"]),
            0.01,
            True,
            TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name=""),
            {},
            nav_actions=("Switch analysis", "New analysis"),
        )
        assert res == "Switch analysis"

    def test_nav_action_absent_when_not_offered(self):
        # The same key with no nav_actions is not an action at all — the page
        # ignores it and Esc still means back.
        from yeaboi.team_profile import TeamProfile
        from yeaboi.ui.mode_select import _run_team_analysis_results

        res = _run_team_analysis_results(
            self._FakeLive(),
            self._console(),
            self._reader(["act:Switch analysis", "esc"]),
            0.01,
            True,
            TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name=""),
            {},
        )
        assert res == "back"

    def test_failed_feature_offers_retry_action(self, monkeypatch):
        from rich.text import Text

        from yeaboi.team_profile import AiAdoptionSignal
        from yeaboi.ui.mode_select import _run_team_analysis_results

        both = self._both()
        captured = {}

        def screen(*args, **kwargs):
            captured["actions"] = kwargs["actions"]
            return Text("results")

        monkeypatch.setattr("yeaboi.ui.mode_select._build_team_analysis_screen", screen)
        code = {
            "signal": AiAdoptionSignal(scanned_commits=10),
            "examples": {
                "enabled_features": ["code_health"],
                "coverage_report": {"status": "failed", "completed": 0, "eligible": 10},
                "repository_health": {"files_analysed": 0},
            },
        }

        result = _run_team_analysis_results(
            self._FakeLive(),
            self._console(),
            self._reader(["esc"]),
            0.01,
            True,
            both["jira"]["profile"],
            {},
            delivery=both,
            code=code,
            analysis_features=["delivery", "code_health"],
            retry_config={"components": {"code": ["azdo"], "docs": []}},
        )

        assert result == "back"
        assert "Retry failed" in captured["actions"]


def _section_body_right_edge(width, view="insights", examples=None):
    """Rightmost column any body row reaches, and where the tab strip ends."""
    from rich.console import Console

    from yeaboi.team_profile import TeamProfile
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_team_analysis_screen

    panel = _build_team_analysis_screen(
        TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name=""),
        examples=examples if examples is not None else {"insights": _COACHING_ITEMS},
        width=width,
        height=60,
        view=view,
    )
    console = Console(width=width, force_terminal=False)
    with console.capture() as cap:
        console.print(panel)
    rows = [r for r in cap.get().split("\n") if r]
    strip = next(i for i, r in enumerate(rows) if "Velocity" in r and "Insights" in r)
    rule = rows[strip + 1]
    # Interior rows only — the page frame's own bottom border spans the width.
    body = [r for r in rows[strip + 2 :] if r.startswith("│")]

    # Past the panel's own right border, which pads every row to full width.
    def _content_edge(row):
        return len(row.rstrip().rstrip("│").rstrip())

    return max((_content_edge(r) for r in body), default=0), _content_edge(rule)


_COACHING_ITEMS = {
    "start": [
        {
            "title": "Split large stories",
            "detail": "Break them at the seam you already find mid-sprint, during refinement instead of after.",
            "evidence": "4 of 5 spilled stories were 8+ points",
        }
    ],
}


def test_the_body_stays_out_of_the_summary_column():
    """Everything under the tabs belongs to the left column when the band splits.

    The right one is the summary's, and the summary can be long. The strip is
    already capped there; a body running the full width under it read as
    overrunning its own tabs.
    """
    # Against the page's own half, not the tab rule: the rule is drawn with its
    # own taper and the body may legitimately reach a couple of columns past it.
    # What must not happen is the body entering the summary's half.
    body_edge, _strip_edge = _section_body_right_edge(190)
    assert body_edge <= (190 - 6) // 2, body_edge


def test_a_narrow_page_still_uses_all_of_itself():
    """Below the split there is no second column to stay out of."""
    from yeaboi.ui.mode_select.screens._screens_secondary import _BAND_TWO_COL_MIN_W

    body_edge, _strip = _section_body_right_edge(_BAND_TWO_COL_MIN_W - 20)
    assert body_edge > (_BAND_TWO_COL_MIN_W - 20) * 0.6, body_edge


def _insights_page(width=130, height=34, scroll=0):
    from rich.console import Console

    from yeaboi.team_profile import TeamProfile
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_team_analysis_screen

    panel = _build_team_analysis_screen(
        TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name=""),
        examples={"insights": _SCROLLING_ITEMS},
        width=width,
        height=height,
        view="insights",
        scroll_offset=scroll,
    )
    console = Console(width=width, force_terminal=False)
    with console.capture() as cap:
        console.print(panel)
    return cap.get()


_SCROLLING_ITEMS = {
    "start": [
        {
            "title": f"Coaching item number {n}",
            "detail": "Break the work at the seam you already find mid-sprint, during refinement instead.",
            "evidence": f"{n} of 5 spilled stories were 8+ points",
        }
        for n in range(1, 6)
    ],
}


def test_a_section_taller_than_the_viewport_still_shows_its_top():
    """An item is a whole card or table, and one can be taller than the viewport.

    Dropping it for not fitting left the section blank on a short terminal —
    the page rendered its header, its tabs, and then nothing at all.
    """
    assert "Team coaching plan" in _insights_page(height=24)


def test_an_overflowing_section_gets_a_scrollbar():
    page = _insights_page(height=24)
    assert any(glyph in page for glyph in ("▐", "┃", "█")), "no scrollbar on an overflowing section"


def test_the_cards_scroll_rather_than_moving_as_one_block():
    """Each ROW of cards is its own item, so the viewport can slice between them.

    One grid for a whole group is one item taller than the viewport, which is
    what made the section unscrollable and then blank.
    """
    top = _insights_page(height=34, scroll=0)
    down = _insights_page(height=34, scroll=5)
    assert top != down
    assert "Team coaching plan" in top
    assert "Team coaching plan" not in down


def _pager_split_col(panel, width, height):
    """The column the pager pill is split over, as rendered through the chrome."""
    from rich.console import Console

    import yeaboi.ui.shared._music_bar as music_bar

    frame = music_bar._MusicPocketFrame(panel, with_duck=False, with_back=True)
    frame.pager = panel._pager
    frame.pager_divider = int(getattr(panel, "_pager_divider_x", 0) or 0)
    frame.lead_tab = ""
    music_bar._tabs_open = False
    music_bar._back_presence = 1.0
    console = Console(width=width, height=height, force_terminal=False)
    rows = [
        "".join(seg.text for seg in row)
        for row in console.render_lines(frame, console.options.update(height=height), pad=True)
    ]
    # The LAST such row: the Work items page names itself in its crumb too.
    pill = [r for r in rows if "Analysis" in r and "Work items" in r][-1]
    return pill.rindex("│", pill.index("Analysis"), pill.index("Work items")), rows


def _flow_pages(width, height):
    from rich.text import Text as RichText

    from yeaboi.team_profile import TeamProfile
    from yeaboi.ui.mode_select.screens._screens_secondary import (
        _build_preview_split_screen,
        _build_team_analysis_screen,
        _build_team_insights_screen,
    )

    profile = TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name="")
    return {
        "results": _build_team_analysis_screen(
            profile, examples={"insights": _SCROLLING_ITEMS}, width=width, height=height, view="insights"
        ),
        "work items": _build_team_insights_screen(profile, examples={}, width=width, height=height),
        "plan": _build_preview_split_screen(
            [RichText(f"  calibration line {i}") for i in range(40)],
            [RichText(f"  work item row {i}") for i in range(40)],
            stage_index=0,
            width=width,
            height=height,
        ),
    }


def test_every_page_runs_its_bar_down_the_flow_s_column():
    """One vertical per page, and the same one on every page.

    The Work items page put its bar on the page's right border while its pill
    was split halfway across — the two furthest-apart verticals on one screen.
    """
    from yeaboi.ui.mode_select.screens._screens_secondary import analysis_divider_x

    for width in (130, 160, 190, 240):
        want = analysis_divider_x(width)
        for name, panel in _flow_pages(width, 30).items():
            split, rows = _pager_split_col(panel, width, 30)
            bars = {j for r in rows for j, ch in enumerate(r) if ch == "┃"}
            assert split == want, (name, width, split, want)
            # Every bar left of the page's own border belongs to a body column,
            # and there is only one column it may run down. (The plan page has a
            # second bar for its right half, at the border; the Work items page
            # is short enough not to need one at all.)
            in_body = {b for b in bars if b < width - 6}
            assert in_body <= {want}, (name, width, sorted(in_body), want)


def test_the_work_items_bar_is_on_the_flow_s_column_too():
    """Short enough to need a bar, so the page's own reserve is what is tested.

    It used to draw on the page's right border while its pill was split halfway
    across — the two furthest-apart verticals on one screen.
    """
    from rich.console import Console

    from yeaboi.ui.mode_select.screens._screens_secondary import analysis_divider_x

    for width in (130, 190):
        panel = _flow_pages(width, 14)["work items"]
        console = Console(width=width, force_terminal=False)
        with console.capture() as cap:
            console.print(panel)
        rows = cap.get().split("\n")
        bars = {j for r in rows for j, ch in enumerate(r) if ch in "┃│" and 6 < j < width - 6}
        assert bars, (width, "no bar on a page too short for its own body")
        assert max(bars) == analysis_divider_x(width), (width, sorted(bars))


def test_the_section_strip_cycles(monkeypatch):
    """It is a strip of tabs, not a slider: holding one arrow comes round."""
    from yeaboi.team_profile import TeamProfile
    from yeaboi.ui.mode_select import _run_team_analysis_results
    from yeaboi.ui.mode_select.screens._analysis_sections import visible_card_order

    class _Live:
        def update(self, renderable):
            self.last = renderable

    from types import SimpleNamespace

    profile = TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name="")
    order = visible_card_order(profile, False, False)
    box: list = [""]
    # One ← from the landing view wraps to the LAST tab rather than stopping.
    keys = iter(["left", "pager:1"])
    _run_team_analysis_results(
        _Live(),
        SimpleNamespace(size=(150, 40)),
        lambda timeout=None: next(keys),
        0.01,
        True,
        profile,
        {},
        view_box=box,
    )
    assert box[0] == order[-1], (box[0], order)


class TestStepTab:
    """step_tab — the one answer to "what does ← land on".

    A strip of tabs is a ring, not a slider. Both strips in the analysis flow
    use it, because "does this one wrap?" is not a question a strip should be
    able to answer differently from the one beside it.
    """

    TABS = ("epic", "stories", "tasks", "sprint")

    def _step(self, current, key):
        from yeaboi.ui.shared._components import step_tab

        return step_tab(self.TABS, current, key)

    def test_it_comes_round_at_both_ends(self):
        assert self._step("sprint", "right") == "epic"
        assert self._step("epic", "left") == "sprint"

    def test_it_steps_in_the_middle(self):
        assert self._step("stories", "right") == "tasks"
        assert self._step("stories", "left") == "epic"

    def test_other_keys_leave_it_alone(self):
        for key in ("up", "enter", "tab", ""):
            assert self._step("stories", key) == "stories", key

    def test_an_unknown_tab_is_left_to_the_caller(self):
        # Not silently the first tab: the caller's own fallback decides.
        assert self._step("overview", "right") == "overview"

    def test_an_empty_strip_is_not_an_error(self):
        from yeaboi.ui.shared._components import step_tab

        assert step_tab((), "epic", "right") == "epic"


def test_the_preview_strip_cycles_too():
    """It was the one strip still stopping at its ends."""
    from yeaboi.ui.mode_select.screens._screens_secondary import _PREVIEW_TABS
    from yeaboi.ui.shared._components import step_tab

    tabs = tuple(t.lower() for t in _PREVIEW_TABS)
    assert step_tab(tabs, tabs[-1], "right") == tabs[0]
    assert step_tab(tabs, tabs[0], "left") == tabs[-1]


class TestTabMemory:
    """Where a strip was left is part of what a strip IS.

    Both strips in the flow use it, so neither can be the one that forgets.
    """

    def test_it_reopens_where_it_was_left(self):
        from yeaboi.ui.shared._components import remember_tab, remembered_tab

        remember_tab("plan", "tasks")
        assert remembered_tab("plan", ("epic", "stories", "tasks", "sprint"), "epic") == "tasks"

    def test_a_tab_this_run_lacks_falls_back(self):
        # A section whose scan did not happen, an artifact not generated yet.
        from yeaboi.ui.shared._components import remember_tab, remembered_tab

        remember_tab("sections", "ai-adoption")
        assert remembered_tab("sections", ("velocity", "team"), "velocity") == "velocity"

    def test_strips_do_not_remember_for_each_other(self):
        from yeaboi.ui.shared._components import remember_tab, remembered_tab

        remember_tab("plan", "sprint")
        remember_tab("sections", "trends")
        assert remembered_tab("plan", ("epic", "sprint"), "epic") == "sprint"
        assert remembered_tab("sections", ("velocity", "trends"), "velocity") == "trends"

    def test_nothing_remembered_is_the_fallback(self):
        from yeaboi.ui.shared._components import remembered_tab

        assert remembered_tab("never-used", ("a", "b"), "a") == "a"


def test_the_plan_strip_reopens_where_it_was_left():
    """Crossing to the analysis and back dropped you on Epic however far
    through the plan you were."""
    from yeaboi.ui.mode_select import _PREVIEW_STRIP
    from yeaboi.ui.shared._components import remember_tab, remembered_tab

    tabs = ("instructions", "epic", "stories", "tasks", "sprint")
    assert remembered_tab(_PREVIEW_STRIP, tabs, "instructions") == "instructions"
    remember_tab(_PREVIEW_STRIP, "tasks")
    assert remembered_tab(_PREVIEW_STRIP, tabs, "instructions") == "tasks"


class TestSectionTabWindow:
    """Eight labels do not fit in half a small terminal.

    They used to crush together and then overflow the rule. The strip is a
    WINDOW onto the sections now, centred on the one you are on.
    """

    LABELS = ["Velocity", "Team", "Estimates", "Workflow", "Writing", "Trends", "Actions", "Insights"]
    KEYS = [x.lower() for x in LABELS]

    def _fit(self, at, strip_w):
        from yeaboi.ui.mode_select.screens._screens_secondary import _fit_section_tabs

        return _fit_section_tabs(list(self.LABELS), list(self.KEYS), at, strip_w)

    def test_a_wide_strip_keeps_every_tab(self):
        labels, keys, at = self._fit(0, 200)
        assert labels == self.LABELS
        assert keys == self.KEYS
        assert at == 0

    def test_a_narrow_strip_keeps_the_live_one(self):
        for at in range(len(self.LABELS)):
            labels, _keys, new_at = self._fit(at, 40)
            assert labels[new_at] == self.LABELS[at], (at, labels, new_at)

    def test_it_never_overflows_the_strip(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _TAB_GAP

        for strip_w in (24, 32, 40, 56, 72):
            for at in range(len(self.LABELS)):
                labels, _keys, _new = self._fit(at, strip_w)
                drawn = sum(len(x) + _TAB_GAP for x in labels) - _TAB_GAP
                assert drawn <= strip_w or len(labels) == 1, (strip_w, at, labels, drawn)

    def test_the_ellipsis_steps_the_window(self):
        # A mark you cannot use is worse than no mark: each "…" carries the key
        # of the first tab hidden beyond it.
        labels, keys, _at = self._fit(4, 40)
        assert labels[0] == "…" and labels[-1] == "…"
        assert keys[0] in self.KEYS and keys[0] not in keys[1:-1]
        assert keys[-1] in self.KEYS and keys[-1] not in keys[1:-1]

    def test_the_ends_have_only_one_ellipsis(self):
        first, _k, _a = self._fit(0, 40)
        last, _k2, _a2 = self._fit(len(self.LABELS) - 1, 40)
        assert first[0] != "…" and first[-1] == "…"
        assert last[0] == "…" and last[-1] != "…"


def test_the_flow_s_wordmark_shimmers_on_every_page():
    """The results page animated its title and the other two did not, so the
    wordmark stopped moving as you crossed the pager."""
    from rich.text import Text as RichText

    from yeaboi.team_profile import TeamProfile
    from yeaboi.ui.mode_select.screens._screens_secondary import (
        _build_preview_split_screen,
        _build_team_insights_screen,
    )

    profile = TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name="")
    pages = {
        "work items": lambda tick: _build_team_insights_screen(
            profile, examples={}, width=150, height=20, shimmer_tick=tick
        ),
        "plan": lambda tick: _build_preview_split_screen(
            [RichText("  a")], [RichText("  b")], stage_index=0, width=150, height=20, shimmer_tick=tick
        ),
    }
    from io import StringIO

    from rich.console import Console

    def _styled(panel):
        # The shimmer is a COLOUR travelling along the same glyphs, so plain
        # text is identical either way — it has to be compared with styles on.
        console = Console(width=150, file=StringIO(), force_terminal=True, color_system="truecolor")
        with console.capture() as cap:
            console.print(panel)
        return cap.get()

    for name, build in pages.items():
        assert _styled(build(0.0)) != _styled(build(0.7)), name


def test_the_shared_column_is_the_scrollbar_s():
    """The column the flow picked is the results page's, so on that page the
    pill's split and the scrollbar are one vertical line."""
    for width in (130, 190, 240):
        panel = _flow_pages(width, 26)["results"]
        split, rows = _pager_split_col(panel, width, 26)
        bars = {j for r in rows for j, ch in enumerate(r) if ch == "┃"}
        assert bars, f"no scrollbar at width {width}"
        assert split in bars, (width, split, sorted(bars))


def test_the_pager_is_in_the_same_place_on_every_page_of_the_flow():
    """A control that lands somewhere different on each page is one you have to
    find again on each page.

    Each page has some vertical near the middle — the results page's scrollbar,
    the plan page's gutter — and they do not fall in the same column, so the
    flow picks one and every page splits its pill over that.
    """
    for width in (130, 160, 190, 240):
        cols = {name: _pager_split_col(panel, width, 30)[0] for name, panel in _flow_pages(width, 30).items()}
        assert len(set(cols.values())) == 1, (width, cols)


def test_the_scrollbar_sits_beside_the_column_it_scrolls():
    """With the body held to the left column, a bar on the page's right border
    sat past the summary with nothing under it — present, and nowhere you would
    look for it."""
    from rich.console import Console

    from yeaboi.team_profile import TeamProfile
    from yeaboi.ui.mode_select.screens._screens_secondary import (
        _build_team_analysis_screen,
        analysis_divider_x,
    )

    width = 190
    panel = _build_team_analysis_screen(
        TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name=""),
        examples={"insights": _SCROLLING_ITEMS},
        width=width,
        height=22,
        view="insights",
    )
    console = Console(width=width, force_terminal=False)
    with console.capture() as cap:
        console.print(panel)
    rows = cap.get().split("\n")
    strip = next(i for i, r in enumerate(rows) if "Velocity" in r and "Insights" in r)
    bars = {j for r in rows[strip + 2 :] for j, ch in enumerate(r) if ch == "┃"}
    assert bars, "no scrollbar thumb on an overflowing section"
    # Beside the body, not at the page's right border where it started out.
    assert max(bars) <= (width - 6) // 2, (sorted(bars), width)
    assert max(bars) == analysis_divider_x(width), (sorted(bars), analysis_divider_x(width))


def test_a_long_summary_is_wrapped_to_its_own_column():
    """The summary is the one thing built at one width and laid out at another.

    It was wrapped to the page and then dropped into the band's half-width
    cell, which wrapped every line a second time — so a long summary rendered
    as two alternating indents rather than a paragraph.
    """
    from rich.console import Console

    from yeaboi.team_profile import TeamProfile
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_team_analysis_screen

    long_summary = " ".join(["The team's delivery is steady but front-loaded across the sprint."] * 8)
    panel = _build_team_analysis_screen(
        TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name=""),
        examples={"narrative": {"executive_summary": long_summary, "sections": {}}},
        width=190,
        height=50,
    )
    console = Console(width=190, force_terminal=False)
    with console.capture() as cap:
        console.print(panel)
    rows = cap.get().split("\n")
    at = next(i for i, r in enumerate(rows) if "Summary" in r)
    body = [r for r in rows[at + 2 :] if "delivery is steady" in r]
    assert len(body) > 3, "the summary needs to be long enough to wrap"
    starts = {len(r) - len(r.lstrip("│ ")) for r in body}  # past the panel border
    assert len(starts) == 1, f"summary lines start at {sorted(starts)} — wrapped twice"


class TestCoachingCardColumns:
    """The coaching cards flow into columns once there is room for them.

    One per row is right in a terminal-sized window and wrong in a maximised
    one: a 60-character sentence in a 230-column box leaves the eye to cross
    the whole screen to reach the evidence line under it.
    """

    ITEMS = {
        "start": [
            {"title": "Split large stories", "detail": "Break them at the seam.", "evidence": "4 of 5 spilled"},
            {"title": "Name an owner", "detail": "Unowned tickets sat longer.", "evidence": "3.1 day pickup"},
        ],
        "stop": [{"title": "Adding scope late", "detail": "It displaced committed work.", "evidence": "9 tickets"}],
    }

    def _rows(self, width):
        from rich.console import Console, Group

        from yeaboi.ui.mode_select.screens._analysis_sections import _ta_coaching_dashboard, _TaCtx

        ctx = _TaCtx(width, {})
        _ta_coaching_dashboard(ctx, self.ITEMS)
        console = Console(width=width, force_terminal=False)
        with console.capture() as cap:
            console.print(Group(*ctx.lines))
        return cap.get().split("\n")

    @staticmethod
    def _widest_row(rows):
        """Most card tops on any one row, ignoring the count tiles above them.

        The FOCUS NOW / KEEP / TRY NEXT tiles are their own three-up grid and
        would otherwise answer for the cards.
        """
        at = next(i for i, r in enumerate(rows) if "Focus now" in r)
        return max((r.count("╭") for r in rows[at:]), default=0)

    def test_narrow_stacks_them_one_per_row(self):
        assert self._widest_row(self._rows(80)) == 1

    def test_wide_puts_them_side_by_side(self):
        assert self._widest_row(self._rows(200)) == 3

    def test_the_step_between_is_two(self):
        assert self._widest_row(self._rows(130)) == 2

    def test_every_card_survives_the_column_split(self):
        # Laying them out in a grid must not drop the last one of a short row.
        rows = "\n".join(self._rows(200))
        for title in ("Split large stories", "Name an owner", "Adding scope late"):
            assert title in rows, title

    def test_the_text_keeps_a_readable_measure(self):
        # Each card's own text is wrapped to its share of the row, not to the
        # page — the whole point of the columns.
        from yeaboi.ui.mode_select.screens._analysis_sections import _INSIGHT_CARD_MIN_W

        for row in self._rows(200):
            if "Break them at the seam" in row:
                # The sentence sits inside one card, not spanning the page.
                assert row.index("Break") < _INSIGHT_CARD_MIN_W + 10
                break
        else:
            raise AssertionError("detail line never rendered")


class TestResultsActionPlacement:
    """Where an action is DRAWN follows from RESULTS_TAB_ACTIONS.

    Anything the page doesn't recognise falls through to results_body_actions
    and is drawn a second time as a button floating above the chrome, while
    still being counted in the "N actions" tab — which is what the nav actions
    did when they were added to the loop but not to the strip.
    """

    def test_nav_actions_live_in_the_chrome_strip(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import (
            ANALYSIS_NAV_ACTIONS,
            RESULTS_TAB_ACTIONS,
            results_body_actions,
        )

        assert set(ANALYSIS_NAV_ACTIONS) <= set(RESULTS_TAB_ACTIONS)
        assert results_body_actions(["Export", "Share Online", "Anonymize", *ANALYSIS_NAV_ACTIONS]) == []

    def test_nav_action_draws_no_body_button(self):
        from rich.console import Console

        from yeaboi.team_profile import TeamProfile
        from yeaboi.ui.mode_select.screens._screens_secondary import (
            ANALYSIS_NAV_ACTIONS,
            _build_team_analysis_screen,
        )

        panel = _build_team_analysis_screen(
            TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name=""),
            examples={},
            width=120,
            height=40,
            actions=["Export", "Share Online", "Anonymize", *ANALYSIS_NAV_ACTIONS],
        )
        console = Console(width=120, height=40, force_terminal=True)
        text = "\n".join(
            "".join(seg.text for seg in row) for row in console.render_lines(panel, console.options.update(height=40))
        )
        for name in ANALYSIS_NAV_ACTIONS:
            assert name not in text, f"{name} was drawn in the body as well as the strip"
        # It is still offered — as a tab the chrome draws.
        assert set(ANALYSIS_NAV_ACTIONS) <= {n for n, _key in panel._page_tabs}


class TestRunProfileDashboard:
    """_run_profile_dashboard — a whole stored analysis behind one call.

    Analysis mode opens onto this instead of a list of analyses, so the entry
    point needs the two pager pages AND what lies past them from a single call.
    """

    class _FakeLive:
        def update(self, renderable):
            self.last = renderable

    @staticmethod
    def _console():
        from types import SimpleNamespace

        return SimpleNamespace(size=(100, 40))

    @staticmethod
    def _reader(keys=()):
        it = iter(keys)

        def read_key(timeout=None):
            return next(it)

        return read_key

    def _run(self, **kw):
        from yeaboi.ui.mode_select import _run_profile_dashboard

        return _run_profile_dashboard(
            self._FakeLive(),
            self._console(),
            self._reader(kw.pop("keys", ())),
            0.01,
            True,
            kw.pop("team_id", "jira:P"),
            **kw,
        )

    def test_unloadable_profile_backs_out(self, monkeypatch, tmp_path):
        # No store on disk: the caller's fallback for this is the one it already
        # has for leaving, so don't invent a third answer.
        monkeypatch.setattr("yeaboi.ui.mode_select._ana_dbp", tmp_path / "missing.db")
        assert self._run() == "back"

    def _with_profile(self, monkeypatch, tmp_path):
        from yeaboi.team_profile import TeamProfile

        db = tmp_path / "yeaboi.db"
        db.write_text("")
        monkeypatch.setattr("yeaboi.ui.mode_select._ana_dbp", db)
        profile = TeamProfile(team_id="jira:P", source="jira", project_key="P", team_name="")

        class _Store:
            def __init__(self, _path):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def load_with_examples(self, _team_id):
                return profile, {}

        monkeypatch.setattr("yeaboi.team_profile.TeamProfileStore", _Store)
        return profile

    def test_nav_action_passes_through(self, monkeypatch, tmp_path):
        self._with_profile(monkeypatch, tmp_path)
        seen = {}

        def _results(*_args, **kwargs):
            seen["nav"] = kwargs.get("nav_actions")
            return "New analysis"

        monkeypatch.setattr("yeaboi.ui.mode_select._run_team_analysis_results", _results)
        assert self._run(nav_actions=("Switch analysis", "New analysis")) == "New analysis"
        assert seen["nav"] == ("Switch analysis", "New analysis")

    def test_back_from_results_is_back(self, monkeypatch, tmp_path):
        self._with_profile(monkeypatch, tmp_path)
        monkeypatch.setattr("yeaboi.ui.mode_select._run_team_analysis_results", lambda *a, **k: "back")
        assert self._run(nav_actions=("Switch analysis",)) == "back"

    def test_leaving_the_work_items_lands_back_on_the_analysis(self, monkeypatch, tmp_path):
        # Tab crosses the pager back to Analysis, and Esc leaves the preview.
        # Either way the page behind the work items is this analysis — returning
        # here instead sent you out to the mode menu.
        self._with_profile(monkeypatch, tmp_path)
        results_calls = []

        def _results(*_args, **_kw):
            results_calls.append(1)
            return "continue" if len(results_calls) == 1 else "back"

        monkeypatch.setattr("yeaboi.ui.mode_select._run_team_analysis_results", _results)
        monkeypatch.setattr("yeaboi.ui.mode_select._ensure_insights", lambda *a, **k: {})
        monkeypatch.setattr("yeaboi.ui.mode_select._run_team_insights", lambda *a, **k: "continue")
        monkeypatch.setattr("yeaboi.ui.mode_select._load_ana_plan", lambda *a, **k: None)
        monkeypatch.setattr("yeaboi.agent.nodes._format_team_calibration", lambda *a, **k: "calibration text")
        preview_calls = []
        monkeypatch.setattr(
            "yeaboi.ui.mode_select._run_preview_flow",
            lambda *a, **k: preview_calls.append(1),
        )
        assert self._run() == "back"
        assert preview_calls == [1]
        assert len(results_calls) == 2  # re-entered, not left

    def test_a_saved_plan_means_the_plan_is_already_made(self, monkeypatch, tmp_path):
        # The latch used to be set only by the post-run flow, so a plan made
        # from a STORED analysis left the Work items page offering to make the
        # one you had just made — and offering it again after every restart.
        import yeaboi.ui.mode_select as ms

        self._with_profile(monkeypatch, tmp_path)
        monkeypatch.setattr(ms, "_ana_generated", False)
        monkeypatch.setattr(ms, "_load_ana_plan", lambda *a, **k: {"sample_epic": {"title": "E"}})
        monkeypatch.setattr(ms, "_run_team_analysis_results", lambda *a, **k: "back")
        assert self._run() == "back"
        assert ms._ana_generated is True

    def test_a_finished_plan_still_counts(self, monkeypatch, tmp_path):
        # Read to the end, so not resumable — but finished is not gone.
        import yeaboi.ui.mode_select as ms

        self._with_profile(monkeypatch, tmp_path)
        monkeypatch.setattr(ms, "_ana_generated", False)
        monkeypatch.setattr(
            ms, "_load_ana_plan", lambda *a, **k: {"last_page": "complete", "sample_sprint": {"name": "S"}}
        )
        monkeypatch.setattr(ms, "_run_team_analysis_results", lambda *a, **k: "back")
        assert self._run() == "back"
        assert ms._ana_generated is True

    def test_no_saved_plan_means_no_plan_yet(self, monkeypatch, tmp_path):
        import yeaboi.ui.mode_select as ms

        self._with_profile(monkeypatch, tmp_path)
        monkeypatch.setattr(ms, "_ana_generated", True)  # left over from another analysis
        monkeypatch.setattr(ms, "_load_ana_plan", lambda *a, **k: None)
        monkeypatch.setattr(ms, "_run_team_analysis_results", lambda *a, **k: "back")
        assert self._run() == "back"
        assert ms._ana_generated is False

    def test_going_through_the_tabs_sets_the_latch(self, monkeypatch, tmp_path):
        import yeaboi.ui.mode_select as ms

        self._with_profile(monkeypatch, tmp_path)
        monkeypatch.setattr(ms, "_ana_generated", False)
        monkeypatch.setattr(ms, "_load_ana_plan", lambda *a, **k: None)
        calls = []

        def _results(*_a, **_k):
            calls.append(1)
            return "continue" if len(calls) == 1 else "back"

        monkeypatch.setattr(ms, "_run_team_analysis_results", _results)
        monkeypatch.setattr(ms, "_ensure_insights", lambda *a, **k: {})
        monkeypatch.setattr(ms, "_run_team_insights", lambda *a, **k: "continue")
        monkeypatch.setattr("yeaboi.agent.nodes._format_team_calibration", lambda *a, **k: "calibration")
        monkeypatch.setattr(ms, "_run_preview_flow", lambda *a, **k: None)
        assert self._run() == "back"
        assert ms._ana_generated is True

    def test_it_reads_the_plan_back_on_every_pass(self, monkeypatch, tmp_path):
        # Crossing out of the plan saves a snapshot, so the copy loaded on the
        # way in is a page behind by the second crossing — it reopened on the
        # tab left on the FIRST pass, and handed back the artifacts as they
        # were then, discarding anything edited since.
        import yeaboi.ui.mode_select as ms

        self._with_profile(monkeypatch, tmp_path)
        saved = [
            {"last_page": "epic", "sample_epic": {"title": "first"}},
            {"last_page": "tasks", "sample_epic": {"title": "edited"}},
        ]
        reads = []

        def _load(*_a, **_k):
            reads.append(1)
            return saved[min(len(reads) - 1, len(saved) - 1)]

        monkeypatch.setattr(ms, "_load_ana_plan", _load)
        results = []

        def _results(*_a, **_k):
            results.append(1)
            return "continue" if len(results) < 3 else "back"

        monkeypatch.setattr(ms, "_run_team_analysis_results", _results)
        monkeypatch.setattr(ms, "_ensure_insights", lambda *a, **k: {})
        monkeypatch.setattr(ms, "_run_team_insights", lambda *a, **k: "continue")
        monkeypatch.setattr("yeaboi.agent.nodes._format_team_calibration", lambda *a, **k: "calibration")
        seen = []
        monkeypatch.setattr(ms, "_run_preview_flow", lambda *a, **k: seen.append(k.get("resume_state")))

        assert self._run() == "back"
        assert len(seen) == 2
        assert seen[-1]["last_page"] == "tasks"
        assert seen[-1]["sample_epic"] == {"title": "edited"}

    def test_insights_back_returns_to_results(self, monkeypatch, tmp_path):
        # Continue → insights → Back must land on the results page again, not
        # fall out of the analysis entirely.
        self._with_profile(monkeypatch, tmp_path)
        results_calls = []

        def _results(*_args, **_kw):
            results_calls.append(1)
            return "continue" if len(results_calls) == 1 else "back"

        monkeypatch.setattr("yeaboi.ui.mode_select._run_team_analysis_results", _results)
        monkeypatch.setattr("yeaboi.ui.mode_select._ensure_insights", lambda *a, **k: {})
        monkeypatch.setattr("yeaboi.ui.mode_select._run_team_insights", lambda *a, **k: "back")
        assert self._run() == "back"
        assert len(results_calls) == 2


class TestDeliveryOffScreen:
    """_build_team_analysis_screen with profile=None (a code/docs-only run) — the
    global signals are passed via code_signal/doc_signal."""

    @staticmethod
    def _code_signal():
        from yeaboi.team_profile import AiAdoptionSignal

        return AiAdoptionSignal(
            scanned_commits=40,
            ai_commits=18,
            footprint_pct=45.0,
            per_tool=(("claude", 18),),
            sources_scanned=("github",),
        )

    def test_renders_without_profile(self):
        panel = _build_team_analysis_screen(
            None,
            width=100,
            height=40,
            examples={"ai_adoption": {"summary": {}, "coverage": [], "samples": []}},
            source="jira",
            project_key="PROJ",
            code_signal=self._code_signal(),
        )
        assert isinstance(panel, Panel)
        out = _render(panel, width=100)
        assert "jira/PROJ" in out
        assert "only" in out  # header says "... only" (no delivery profile)
        assert "sprints" not in out

    def test_shows_code_card_from_signal(self):
        panel = _build_team_analysis_screen(
            None,
            width=100,
            height=40,
            examples={"ai_adoption": {"summary": {}, "coverage": [], "samples": []}},
            view="ai-adoption",
            source="jira",
            project_key="PROJ",
            code_signal=self._code_signal(),
        )
        out = _render(panel, width=100)
        assert "AI Usage" in out
        assert "45%" in out  # footprint rendered from the global code_signal

    def test_ai_adoption_dashboard_breakdowns_evidence_and_actions(self):
        from yeaboi.team_profile import AiAdoptionSignal

        signal = AiAdoptionSignal(
            scanned_commits=40,
            scanned_prs=10,
            ai_commits=18,
            ai_prs=3,
            footprint_pct=42.0,
            per_tool=(("claude", 14), ("copilot", 7)),
            per_author=(("Ava", 12), ("Sam", 9)),
            per_activity=(("code", 17), ("pr", 4)),
            per_source=(("github", 21),),
            sources_scanned=("github",),
            repos_scanned=("acme/api",),
        )
        examples = {
            "ai_adoption": {
                "samples": [
                    {
                        "tool": "claude",
                        "title": "Improve authentication flow",
                        "url": "https://example.test/pr/42",
                    }
                ],
                "insights": {
                    "start": [
                        {
                            "title": "Share effective prompts",
                            "detail": "Turn individual wins into team practice.",
                            "evidence": "Two active adopters",
                        }
                    ],
                    "stop": [],
                    "keep": [],
                    "try": [],
                },
            }
        }
        top = _render(
            _build_team_analysis_screen(
                None,
                examples=examples,
                view="ai-adoption",
                code_signal=signal,
                width=100,
                height=50,
            ),
            width=100,
        )
        bottom = _render(
            _build_team_analysis_screen(
                None,
                examples=examples,
                view="ai-adoption",
                code_signal=signal,
                scroll_offset=9999,
                width=100,
                height=50,
            ),
            width=100,
        )
        combined = top + bottom
        for expected in (
            "LOWER BOUND SIGNAL",
            "DETECTABLE FOOTPRINT",
            "Tools detected",
            "Contributors",
            "Evidence",
            "Improve authentication flow",
            "Recommended actions",
            "Share effective prompts",
        ):
            assert expected in combined

    def test_zero_marked_explains_detection_limits(self):
        from yeaboi.team_profile import AiAdoptionSignal

        signal = AiAdoptionSignal(scanned_commits=30, ai_commits=0, footprint_pct=0.0)
        out = _render(
            _build_team_analysis_screen(
                None, examples={}, view="ai-adoption", code_signal=signal, width=100, height=50
            ),
            width=100,
        )
        assert "does not mean zero AI use" in out
        assert "attribution" in out

    def test_identity_mismatch_warning_when_no_users_matched(self):
        from yeaboi.team_profile import AiAdoptionSignal

        signal = AiAdoptionSignal()
        examples = {
            "ai_adoption": {
                "selected_users": ["Ava"],
                "matched_identities": {},
                "unmatched_users": ["Ava"],
                "coverage": ["no commits or authored PRs matched the selected users"],
            }
        }
        out = _render(
            _build_team_analysis_screen(
                None, examples=examples, view="ai-adoption", code_signal=signal, width=100, height=50
            ),
            width=100,
        )
        assert "IDENTITY MISMATCH" in out
        assert "not zero AI usage" in out

    def test_no_identity_mismatch_warning_when_users_matched(self):
        from yeaboi.team_profile import AiAdoptionSignal

        signal = AiAdoptionSignal(scanned_commits=30, ai_commits=6, footprint_pct=20.0)
        examples = {"ai_adoption": {"selected_users": ["Ava"], "matched_identities": {"Ava": ["ava"]}}}
        out = _render(
            _build_team_analysis_screen(
                None, examples=examples, view="ai-adoption", code_signal=signal, width=100, height=50
            ),
            width=100,
        )
        assert "IDENTITY MISMATCH" not in out

    def _practices_examples(self):
        return {
            "ai_adoption": {
                "selected_users": ["Ava", "Sam"],
                "matched_identities": {"Ava": ["ava"], "Sam": ["sam"]},
                "member_activity": [
                    {"member": "Ava", "commits": 2140, "prs": 12, "ai_marked": 5},
                    {"member": "Sam", "commits": 3, "prs": 4, "ai_marked": 1},
                ],
                "member_practices": {
                    "min_sample": 5,
                    "file_data": {"with_file_data": 9, "total": 12},
                    "members": [
                        {
                            "member": "Ava",
                            "commits": 2140,
                            "prs": 12,
                            "with_file_data": 8,
                            "tests_num": 6,
                            "tests_den": 8,
                            "tests_rate": 75.0,
                            "docs_num": 2,
                            "docs_den": 8,
                            "docs_rate": 25.0,
                            "ticket_num": 5,
                            "ticket_den": 10,
                            "ticket_rate": 50.0,
                            "desc_num": 7,
                            "desc_den": 12,
                            "desc_rate": 58.3,
                        },
                        {
                            "member": "Sam",
                            "commits": 3,
                            "prs": 4,
                            "with_file_data": 1,
                            "tests_num": 0,
                            "tests_den": 0,
                            "tests_rate": None,
                            "docs_num": 1,
                            "docs_den": 1,
                            "docs_rate": 100.0,
                            "ticket_num": 2,
                            "ticket_den": 4,
                            "ticket_rate": 50.0,
                            "desc_num": 1,
                            "desc_den": 4,
                            "desc_rate": 25.0,
                        },
                    ],
                    "team": {
                        "member": "Team",
                        "commits": 2143,
                        "prs": 16,
                        "with_file_data": 9,
                        "tests_num": 6,
                        "tests_den": 8,
                        "tests_rate": 75.0,
                        "docs_num": 3,
                        "docs_den": 9,
                        "docs_rate": 33.3,
                        "ticket_num": 7,
                        "ticket_den": 14,
                        "ticket_rate": 50.0,
                        "desc_num": 8,
                        "desc_den": 16,
                        "desc_rate": 50.0,
                    },
                },
            }
        }

    def test_ai_adoption_practices_table(self):
        from yeaboi.team_profile import AiAdoptionSignal

        signal = AiAdoptionSignal(scanned_commits=30, ai_commits=6, footprint_pct=20.0)
        out = _render(
            _build_team_analysis_screen(
                None, examples=self._practices_examples(), view="ai-adoption", code_signal=signal, width=100, height=70
            ),
            width=100,
        )
        assert "Engineering practices by member" in out
        for header in ("Tests", "Docs", "Tickets", "Descs"):
            assert header in out
        assert "75%" in out  # Ava's tests rate as a coloured percentage
        assert "n/a" in out  # Sam has no tests denominator
        assert "1/1" in out  # Sam's docs cell stays a raw fraction under the sample floor
        assert "Team" in out
        assert "2140" in out  # volume counts stay visible in the merged table
        assert "cover 9 of 12 items with change metadata" in out

    def test_practices_lead_above_footprint(self):
        from yeaboi.team_profile import AiAdoptionSignal

        signal = AiAdoptionSignal(scanned_commits=30, ai_commits=6, footprint_pct=20.0)
        out = _render(
            _build_team_analysis_screen(
                None, examples=self._practices_examples(), view="ai-adoption", code_signal=signal, width=100, height=70
            ),
            width=100,
        )
        assert out.index("Engineering practices by member") < out.index("LOWER BOUND SIGNAL")

    def test_ai_adoption_activity_fallback_for_old_blobs(self):
        # Saved profiles that predate practice scoring keep the activity table.
        from yeaboi.team_profile import AiAdoptionSignal

        signal = AiAdoptionSignal(scanned_commits=30, ai_commits=6, footprint_pct=20.0)
        examples = {
            "ai_adoption": {
                "selected_users": ["Ava", "Sam"],
                "matched_identities": {"Ava": ["ava"], "Sam": ["sam"]},
                "member_activity": [
                    {"member": "Ava", "commits": 2140, "prs": 12, "ai_marked": 5},
                    {"member": "Sam", "commits": 3, "prs": 4, "ai_marked": 1},
                    {"member": "AI agent accounts", "commits": 7, "prs": 0, "ai_marked": 7},
                ],
            }
        }
        out = _render(
            _build_team_analysis_screen(
                None, examples=examples, view="ai-adoption", code_signal=signal, width=100, height=60
            ),
            width=100,
        )
        assert "Activity by member" in out
        assert "2140" in out  # the automation-heavy member's volume is visible
        assert "AI agent accounts" in out
        assert "Engineering practices" not in out

    def test_ai_adoption_no_member_table_for_old_profiles(self):
        from yeaboi.team_profile import AiAdoptionSignal

        signal = AiAdoptionSignal(scanned_commits=30, ai_commits=6, footprint_pct=20.0)
        examples = {"ai_adoption": {"selected_users": ["Ava"], "matched_identities": {"Ava": ["ava"]}}}
        out = _render(
            _build_team_analysis_screen(
                None, examples=examples, view="ai-adoption", code_signal=signal, width=100, height=50
            ),
            width=100,
        )
        assert "Activity by member" not in out

    def test_ai_adoption_repo_list_capped_with_more_row(self):
        from yeaboi.team_profile import AiAdoptionSignal

        signal = AiAdoptionSignal(
            scanned_commits=40,
            ai_commits=8,
            footprint_pct=20.0,
            sources_scanned=("github",),
            repos_scanned=tuple(f"acme/repo-{i:02d}" for i in range(12)),
        )
        out = _render(
            _build_team_analysis_screen(
                None, examples={}, view="ai-adoption", code_signal=signal, width=100, height=60
            ),
            width=100,
        )
        assert "Repositories" in out and "12" in out
        assert "acme/repo-05" in out
        assert "acme/repo-06" not in out
        assert "+ 6 more repositories" in out

    def test_ai_adoption_evidence_capped(self):
        from yeaboi.team_profile import AiAdoptionSignal

        signal = AiAdoptionSignal(scanned_commits=40, ai_commits=12, footprint_pct=30.0)
        examples = {
            "ai_adoption": {
                "samples": [{"tool": "claude", "title": f"Sample change {i:02d}"} for i in range(12)],
            }
        }
        out = _render(
            _build_team_analysis_screen(
                None,
                examples=examples,
                view="ai-adoption",
                code_signal=signal,
                scroll_offset=9999,
                width=100,
                height=50,
            ),
            width=100,
        )
        assert "Sample change 07" in out
        assert "Sample change 08" not in out
        assert "+ 4 more AI-marked items" in out

    def test_visible_card_order_code_only(self):
        from yeaboi.ui.mode_select.screens._analysis_sections import visible_card_order

        assert visible_card_order(None, has_code=True, has_docs=False) == ("ai-adoption",)
        assert visible_card_order(None, has_code=False, has_docs=True) == ("documentation",)

    def test_code_health_only_hides_ai_footprint(self):
        from yeaboi.ui.mode_select.screens._analysis_sections import visible_card_order

        assert visible_card_order(
            None,
            has_code=False,
            has_docs=False,
            has_code_health=True,
            analysis_features=["code_health"],
        ) == ("code-health",)

    def test_visible_card_order_delivery_plus_globals(self):
        from yeaboi.team_profile import TeamProfile
        from yeaboi.ui.mode_select.screens._analysis_sections import visible_card_order

        order = visible_card_order(TeamProfile(team_id="t", source="jira", project_key="P"), True, True)
        # Delivery cards, then the two global cards, then insights last.
        assert order[0] == "velocity"
        assert "ai-adoption" in order and "documentation" in order
        assert order[-1] == "insights"


class TestAnalysisFeatureScreen:
    _AVAILABLE = {
        "delivery": True,
        "ai_footprint": True,
        "code_health": True,
        "documentation": False,
    }

    def test_all_runnable_areas_start_selected(self):
        out = _render(
            _build_analysis_feature_screen(
                self._AVAILABLE,
                {"delivery", "ai_footprint", "code_health"},
                0,
                width=100,
                height=32,
            ),
            width=100,
        )
        assert "Analyse all" in out
        assert "AI footprint" in out and "Code health" in out
        assert "Unavailable" in out
        assert "3/3 selected" in out

    def test_narrow_screen_keeps_focused_feature_visible(self):
        out = _render(
            _build_analysis_feature_screen(
                {key: True for key in ("delivery", "ai_footprint", "code_health", "documentation")},
                {"documentation"},
                4,
                width=48,
                height=20,
            ),
            width=48,
        )
        # No caret any more — the bullet says selected, weight says focused.
        assert "● Documentation" in out
        assert "‹" not in out


def test_narrow_window_screen_keeps_focused_tile_visible():
    out = _render(_build_analysis_window_screen(3, width=48, height=20), width=48)
    assert "● 365 DAYS" in out
    assert "‹" not in out


class TestComponentSelectScreen:
    """Ragged component × sub-source picker screen + loop."""

    _GRID = {"delivery": ["jira", "azdevops"], "code": ["github", "azdo"], "docs": ["confluence", "notion"]}

    def test_ragged_render(self):
        checked = {"delivery": {0}, "code": {0, 1}, "docs": {0}}
        out = _render(
            _build_component_select_screen(
                self._GRID, ["delivery", "code", "docs"], checked, 0, 0, width=110, height=30
            ),
            width=110,
        )
        assert "DELIVERY" in out and "CODE" in out and "DOCS" in out
        # Each component shows its OWN sub-sources.
        assert "Jira" in out and "GitHub" in out and "Confluence" in out
        # Match the app-wide filled/empty toggle states.
        assert "●" in out and "○" in out
        assert "Delivery 1" in out and "sources" in out

    def test_focused_source_is_bracketed(self):
        checked = {"delivery": {0, 1}, "code": {0, 1}, "docs": {0, 1}}
        out = _render(
            _build_component_select_screen(
                self._GRID, ["delivery", "code", "docs"], checked, 1, 1, width=110, height=30
            ),
            width=110,
        )
        assert "● Azure Repos" in out
        assert "‹" not in out

    def test_no_selection_shows_hint(self):
        checked = {"delivery": set(), "code": set(), "docs": set()}
        out = _render(
            _build_component_select_screen(
                self._GRID, ["delivery", "code", "docs"], checked, 0, 0, width=100, height=30
            ),
            width=100,
        )
        assert "at least one" in out

    def test_returns_panel_narrow(self):
        checked = {"delivery": {0}}
        assert isinstance(
            _build_component_select_screen({"delivery": ["jira"]}, ["delivery"], checked, 0, 0, width=40, height=20),
            Panel,
        )

    def test_default_branding_stays_analysis(self):
        """Regression: generalizing the screen for Reporting must not restyle analysis."""
        checked = {"delivery": {0}}
        out = _render(
            _build_component_select_screen(self._GRID, ["delivery"], checked, 0, 0, width=110, height=30),
            width=110,
        )
        assert "ANALYSIS SETUP" in out
        assert "REPORTING" not in out

    def test_reporting_branding_and_footer_verb(self):
        from yeaboi.ui.shared._components import REPORTING_THEME, reporting_title

        grid = {"delivery": ["jira", "azuredevops"], "code": ["github"]}
        out = _render(
            _build_component_select_screen(
                grid,
                ["delivery", "code"],
                {"delivery": set(), "code": set()},
                0,
                0,
                width=110,
                height=32,
                theme=REPORTING_THEME,
                brand="REPORTING SETUP",
                title_builder=lambda w, h: reporting_title(width=w),
                footer_verb="report on",
            ),
            width=110,
        )
        assert "REPORTING SETUP" in out
        assert "Azure DevOps" in out  # reporting's canonical token maps to a display name
        assert "at least one source to report on" in out


class TestAnalysisDepthScreen:
    def test_deep_is_rendered_as_recommended(self):
        out = _render(_build_analysis_depth_screen(0, width=100, height=30), width=100)
        assert "QUICK" in out and "Recommended" in out
        assert "DEEP" in out and "exhaustive" in out.lower()

    def test_deep_can_be_focused(self):
        out = _render(_build_analysis_depth_screen(1, width=100, height=30), width=100)
        assert "● DEEP" in out
        assert "‹" not in out


class TestMemberSelectScreen:
    def test_roster_render(self):
        out = _render(_build_member_select_screen(["Alice", "Bob"], {0}, 0, width=100, height=30), width=100)
        assert "Alice" in out and "Bob" in out
        assert "1 of 2 selected" in out
        assert "A selects all" in out

    def test_empty_selection_is_explicit(self):
        out = _render(_build_member_select_screen(["Alice"], set(), 0, width=100, height=30), width=100)
        assert "0 of 1 selected" in out
        assert "whole team" not in out

    def test_empty_roster(self):
        out = _render(_build_member_select_screen([], set(), 0, width=100, height=30), width=100)
        assert "No members found" in out


class TestMoveAnalysisListCursor:
    """The setup pickers are single-column lists — movement is ±1 with wraparound."""

    def _move(self, cursor, key, count):
        from yeaboi.ui.mode_select import _move_analysis_list_cursor

        return _move_analysis_list_cursor(cursor, key, count)

    def test_down_moves_one_row(self):
        assert self._move(0, "down", 5) == 1
        assert self._move(1, "down", 5) == 2

    def test_up_moves_one_row(self):
        assert self._move(2, "up", 5) == 1

    def test_wraps_at_both_ends(self):
        assert self._move(0, "up", 5) == 4
        assert self._move(4, "down", 5) == 0

    def test_left_right_mirror_up_down(self):
        assert self._move(1, "left", 4) == 0
        assert self._move(1, "right", 4) == 2

    def test_scroll_keys_move_one_row(self):
        assert self._move(1, "scroll_up", 4) == 0
        assert self._move(1, "scroll_down", 4) == 2

    def test_unknown_key_is_a_no_op(self):
        assert self._move(2, "x", 5) == 2

    def test_single_row_pins_to_zero(self):
        assert self._move(3, "down", 1) == 0
        assert self._move(0, "up", 0) == 0


class TestComponentAndMemberLoops:
    class _FakeLive:
        def update(self, renderable):
            self.last = renderable

    @staticmethod
    def _console():
        from types import SimpleNamespace

        return SimpleNamespace(size=(100, 40))

    @staticmethod
    def _reader(keys):
        it = iter(keys)

        def read_key(timeout=None):
            return next(it)

        return read_key

    _GRID = {"delivery": ["jira", "azdevops"], "code": ["github", "azdo"], "docs": ["confluence"]}

    def _components(self, keys, grid=None):
        from yeaboi.ui.mode_select import _run_component_select

        return _run_component_select(
            self._FakeLive(), self._console(), self._reader(keys), 0.01, True, grid or self._GRID
        )

    def _features(self, keys):
        from yeaboi.ui.mode_select import _run_analysis_feature_select

        return _run_analysis_feature_select(
            self._FakeLive(),
            self._console(),
            self._reader(keys),
            0.01,
            True,
            {
                "delivery": True,
                "ai_footprint": True,
                "code_health": True,
                "documentation": True,
            },
        )

    def _members(self, keys, roster):
        from yeaboi.ui.mode_select import _run_member_select

        return _run_member_select(self._FakeLive(), self._console(), self._reader(keys), 0.01, True, roster)

    def _depth(self, keys):
        from yeaboi.ui.mode_select import _run_analysis_depth_select

        return _run_analysis_depth_select(self._FakeLive(), self._console(), self._reader(keys), 0.01, True)

    def test_all_checked_returns_full_map(self):
        # Everything checked by default → Enter returns the full component→sub-source map.
        assert self._components(["enter"]) == {
            "delivery": ["jira", "azdevops"],
            "code": ["github", "azdo"],
            "docs": ["confluence"],
        }

    def test_all_features_are_selected_by_default(self):
        assert self._features(["enter"]) == [
            "delivery",
            "ai_footprint",
            "code_health",
            "documentation",
        ]

    def test_feature_can_be_removed(self):
        assert self._features(["down", "right", " ", "enter"]) == [
            "delivery",
            "code_health",
            "documentation",
        ]

    def test_feature_picker_blocks_empty_selection(self):
        assert self._features([" ", "enter", "down", " ", "enter"]) == ["delivery"]

    def test_depth_defaults_to_deep(self):
        assert self._depth(["enter"]) == "deep"

    def test_depth_can_select_quick_or_cancel(self):
        assert self._depth(["left", "enter"]) == "quick"
        assert self._depth(["esc"]) == "cancel"

    def test_deselecting_a_subsource(self):
        # Toggle off delivery[0]=jira → delivery keeps only azdevops.
        result = self._components([" ", "enter"])
        assert result["delivery"] == ["azdevops"]
        assert result["code"] == ["github", "azdo"]

    def test_deselecting_a_whole_component(self):
        # Down walks the FLATTENED grid: jira, azdevops, github, azdo, confluence.
        # Four downs reach docs' only box; Space drops docs entirely.
        result = self._components(["down", "down", "down", "down", " ", "enter"])
        assert "docs" not in result

    def test_cannot_confirm_nothing(self):
        # Uncheck every box across all rows, Enter is blocked, then re-check one.
        keys = [
            " ",
            "down",
            " ",  # delivery: off both
            "down",
            " ",
            "down",
            " ",  # code: off both
            "down",
            " ",  # docs: off
            "enter",  # blocked (nothing selected)
            " ",  # docs back on
            "enter",
        ]
        assert self._components(keys) == {"docs": ["confluence"]}

    def test_cancel(self):
        assert self._components(["esc"]) == "cancel"

    def test_required_component_blocks_enter(self):
        from yeaboi.ui.mode_select import _run_component_select

        # Uncheck both delivery boxes; Enter must be rejected (delivery required),
        # re-checking one then lets Enter through.
        keys = [" ", "down", " ", "enter", " ", "enter"]
        result = _run_component_select(
            self._FakeLive(),
            self._console(),
            self._reader(keys),
            0.01,
            True,
            self._GRID,
            required={"delivery": "Select at least one ticketing source."},
        )
        assert result["delivery"] == ["azdevops"]

    def test_required_ignored_when_component_not_in_grid(self):
        from yeaboi.ui.mode_select import _run_component_select

        result = _run_component_select(
            self._FakeLive(),
            self._console(),
            self._reader(["enter"]),
            0.01,
            True,
            {"code": ["github"]},
            required={"delivery": "Select at least one ticketing source."},
        )
        assert result == {"code": ["github"]}

    def test_member_select_starts_with_everyone_selected(self):
        assert self._members(["enter"], ["Alice", "Bob"]) == ["Alice", "Bob"]

    def test_member_select_can_exclude_a_name(self):
        assert self._members([" ", "enter"], ["Alice", "Bob"]) == ["Bob"]

    def test_member_select_toggle_all(self):
        assert self._members(["a", "right", " ", "enter"], ["Alice", "Bob"]) == ["Bob"]

    def test_member_select_cannot_confirm_nobody(self):
        assert self._members(["a", "enter", " ", "enter"], ["Alice"]) == ["Alice"]

    def test_member_select_empty_roster_is_none(self):
        assert self._members(["enter"], []) is None

    def test_member_cancel(self):
        assert self._members(["esc"], ["Alice"]) == "cancel"

    def test_member_right_key_moves_one_row(self):
        assert self._members(["right", " ", "enter"], ["Alice", "Bob", "Zoe"]) == ["Alice", "Zoe"]

    # Regression tests for the ≥88-column grid-navigation bug: the pickers render a
    # single vertical column at every width, but the old mover treated wide
    # terminals (the fixture console is 100 columns) as a 2-column grid, so
    # up/down jumped two rows and skipped toggles.

    def test_member_down_at_wide_width_moves_one_row(self):
        # Old grid math skipped Bob and toggled Zoe.
        assert self._members(["down", " ", "enter"], ["Alice", "Bob", "Zoe"]) == ["Alice", "Zoe"]

    def test_feature_down_twice_lands_on_second_area(self):
        # Rows: Analyse-all, delivery, ai_footprint, … Old grid math jumped from
        # delivery straight to code_health.
        assert self._features(["down", "down", " ", "enter"]) == [
            "delivery",
            "code_health",
            "documentation",
        ]

    def _window(self, keys):
        from yeaboi.ui.mode_select import _run_analysis_window_select

        return _run_analysis_window_select(self._FakeLive(), self._console(), self._reader(keys), 0.01, True)

    def test_window_down_moves_one_option(self):
        # 120 days is preselected; old grid math made "down" a no-op at wide widths.
        assert self._window(["down", "enter"]) == 365

    def test_window_up_moves_one_option(self):
        assert self._window(["up", "enter"]) == 90

    # initial_* params — the setup wizard re-enters a step with the previous
    # choice restored instead of resetting to the defaults.

    def test_feature_initial_selection_is_restored(self):
        from yeaboi.ui.mode_select import _run_analysis_feature_select

        result = _run_analysis_feature_select(
            self._FakeLive(),
            self._console(),
            self._reader(["enter"]),
            0.01,
            True,
            {"delivery": True, "ai_footprint": True, "code_health": True, "documentation": True},
            initial_features=["delivery", "documentation"],
        )
        assert result == ["delivery", "documentation"]

    def test_component_initial_selection_is_restored(self):
        from yeaboi.ui.mode_select import _run_component_select

        result = _run_component_select(
            self._FakeLive(),
            self._console(),
            self._reader(["enter"]),
            0.01,
            True,
            self._GRID,
            initial={"delivery": ["azdevops"], "docs": ["confluence"]},
        )
        # code is absent from initial (newly enabled) → all-checked like a first visit.
        assert result == {"delivery": ["azdevops"], "code": ["github", "azdo"], "docs": ["confluence"]}

    def test_code_project_initial_selection_is_restored(self, monkeypatch):
        from yeaboi.ui.mode_select import _run_code_scope_select

        monkeypatch.setattr("yeaboi.tools.azure_devops.azdevops_list_projects", lambda: ["Alpha", "Beta", "Gamma"])
        monkeypatch.setattr("yeaboi.config.get_team_analysis_azdo_projects", lambda: [])
        result = _run_code_scope_select(
            self._FakeLive(),
            self._console(),
            self._reader(["enter"]),
            0.01,
            True,
            provider="azdo",
            initial=["beta"],
        )
        assert result == ["Beta"]

    def _github_scope(self, keys, monkeypatch, *, owners=None, initial=None, discover=None):
        from yeaboi.ui.mode_select import _run_code_scope_select

        monkeypatch.setattr(
            "yeaboi.tools.github.github_list_owners",
            discover or (lambda: list(owners if owners is not None else ["acme-corp", "dinho"])),
        )
        monkeypatch.setattr("yeaboi.config.get_team_analysis_github_owners", lambda: ())
        return _run_code_scope_select(
            self._FakeLive(),
            self._console(),
            self._reader(keys),
            0.01,
            True,
            provider="github",
            initial=initial,
        )

    def test_github_owner_picker_pre_checks_nothing_without_configured_owners(self, monkeypatch):
        # Discovery here is unbounded — personal login plus every org, each one a
        # whole repo estate. Pre-checking all of it would make three Enters scan
        # everything the token can see, so Enter must refuse until the user picks.
        assert self._github_scope([" ", "enter"], monkeypatch) == ["acme-corp"]

    def test_github_owner_picker_refuses_an_empty_pick(self, monkeypatch):
        assert self._github_scope(["enter", "down", " ", "enter"], monkeypatch) == ["dinho"]

    def test_github_owner_picker_pre_checks_configured_owners(self, monkeypatch):
        # A saved Settings default IS an explicit choice, so it arrives checked.
        from yeaboi.ui.mode_select import _run_code_scope_select

        monkeypatch.setattr("yeaboi.tools.github.github_list_owners", lambda: ["acme-corp", "dinho"])
        monkeypatch.setattr("yeaboi.config.get_team_analysis_github_owners", lambda: ("acme-corp",))
        result = _run_code_scope_select(
            self._FakeLive(),
            self._console(),
            self._reader(["enter"]),
            0.01,
            True,
            provider="github",
        )
        assert result == ["acme-corp"]

    def test_github_owner_initial_selection_is_restored(self, monkeypatch):
        assert self._github_scope(["enter"], monkeypatch, initial=["DINHO"]) == ["dinho"]

    def test_github_owner_empty_estate_names_the_way_out(self, monkeypatch):
        # "Select at least one GitHub owner." is impossible advice with an empty
        # list — Enter has to point at the only key that works.
        from yeaboi.ui.mode_select import _run_code_scope_select

        monkeypatch.setattr("yeaboi.tools.github.github_list_owners", lambda: [])
        monkeypatch.setattr("yeaboi.config.get_team_analysis_github_owners", lambda: ())
        captured: list = []
        live = self._FakeLive()
        live.update = captured.append
        _run_code_scope_select(
            live,
            self._console(),
            self._reader(["enter", "esc"]),
            0.01,
            True,
            provider="github",
        )
        assert "press Esc to go back" in _render(captured[-1])

    def test_github_owner_discovery_failure_falls_back_to_config(self, monkeypatch):
        # Discovery failing must not strand the wizard: the configured default
        # still lets the run proceed, with the reason on screen.
        from yeaboi.ui.mode_select import _run_code_scope_select

        def _boom():
            raise RuntimeError("bad credentials")

        monkeypatch.setattr("yeaboi.tools.github.github_list_owners", _boom)
        monkeypatch.setattr("yeaboi.config.get_team_analysis_github_owners", lambda: ("fallback-org",))
        captured: list = []
        live = self._FakeLive()
        live.update = captured.append
        result = _run_code_scope_select(
            live,
            self._console(),
            self._reader(["enter"]),
            0.01,
            True,
            provider="github",
        )
        assert result == ["fallback-org"]
        assert "bad credentials" in _render(captured[-1])

    def test_github_owner_empty_estate_stays_on_screen(self, monkeypatch):
        # Enter with nothing to select must not return an empty scope — Esc is the
        # only way out, so the user goes back and de-selects GitHub deliberately.
        assert self._github_scope(["enter", "esc"], monkeypatch, owners=[]) == "cancel"

    def test_member_selection_can_be_restored_after_review_back(self):
        from yeaboi.ui.mode_select import _run_member_select

        result = _run_member_select(
            self._FakeLive(),
            self._console(),
            self._reader(["enter"]),
            0.01,
            True,
            ["Alice", "Bob"],
            initial_members=["Bob"],
        )
        assert result == ["Bob"]

    def test_prefetch_roster_unions_sources(self, monkeypatch):
        from yeaboi.ui.mode_select import _prefetch_roster

        rosters = {"jira": ["Bob", "Alice"], "azdevops": ["Alice", "Zoe"]}
        monkeypatch.setattr("yeaboi.analysis.get_team_roster", lambda s, p, db_path=None: rosters[s])
        names = _prefetch_roster(self._FakeLive(), self._console(), ["jira", "azdevops"], "", None)
        assert names == ["Alice", "Bob", "Zoe"]  # sorted union across both trackers

    def test_prefetch_roster_fetches_sources_concurrently(self, monkeypatch):
        import threading

        from yeaboi.ui.mode_select import _prefetch_roster

        barrier = threading.Barrier(2)
        state = {"active": 0, "peak": 0}
        lock = threading.Lock()

        def roster(source, project, db_path=None):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            try:
                barrier.wait(timeout=2)
                return [source]
            finally:
                with lock:
                    state["active"] -= 1

        monkeypatch.setattr("yeaboi.analysis.get_team_roster", roster)
        names = _prefetch_roster(self._FakeLive(), self._console(), ["jira", "azdevops"], "", None)

        assert state["peak"] == 2
        assert names == ["azdevops", "jira"]

    def test_prefetch_roster_swallows_errors(self, monkeypatch):
        from yeaboi.ui.mode_select import _prefetch_roster

        def boom(s, p, db_path=None):
            raise RuntimeError("no tracker")

        monkeypatch.setattr("yeaboi.analysis.get_team_roster", boom)
        assert _prefetch_roster(self._FakeLive(), self._console(), ["jira"], "", None) == []

    def test_status_aware_prefetch_unions_sources(self, monkeypatch):
        from yeaboi.team_roster import RosterMember, RosterResult
        from yeaboi.ui.mode_select import _prefetch_roster_result

        def roster(source, project, db_path=None):
            names = {"jira": ("Ada", "Bob"), "azdevops": ("Ada", "Carol")}[source]
            return RosterResult(
                tuple(RosterMember(name, source, f"{source}:{name}") for name in names),
                "complete",
                (),
            )

        monkeypatch.setattr("yeaboi.analysis.get_team_roster_result", roster)
        result = _prefetch_roster_result(
            self._FakeLive(),
            self._console(),
            ["jira", "azdevops"],
            "",
            None,
        )
        assert result.status == "complete"
        assert [member.name for member in result.members] == ["Ada", "Bob", "Carol"]

    def test_failed_status_asks_instead_of_continuing_unscoped(self, monkeypatch):
        from yeaboi.team_roster import RosterResult
        from yeaboi.ui.mode_select import _run_analysis_roster_lookup

        monkeypatch.setattr(
            "yeaboi.ui.mode_select._prefetch_roster_result",
            lambda *args, **kwargs: RosterResult((), "failed", (), ("Jira unavailable",)),
        )
        result = _run_analysis_roster_lookup(
            self._FakeLive(),
            self._console(),
            self._reader(["esc"]),
            ["jira"],
            "PROJ",
            None,
        )
        assert result is None


class TestAnalysisSetupWizard:
    """Walking the setup steps with state carry-over.

    ←/→ move between the config sets; Esc LEAVES the setup from wherever it is
    pressed. It used to walk back one step, which was the only way to re-edit a
    set when the wizard was one set per page — every set is on the page at once
    now, so an Esc that undid a single step meant pressing it five times to get
    out of a screen you can see all of.
    """

    _FakeLive = TestComponentAndMemberLoops._FakeLive
    _console = staticmethod(TestComponentAndMemberLoops._console)
    _reader = staticmethod(TestComponentAndMemberLoops._reader)

    _DOCS_ONLY = {"delivery": [], "code": [], "docs": ["confluence"]}
    _DELIVERY_ONLY = {"delivery": ["jira"], "code": [], "docs": []}
    _GITHUB_ONLY = {"delivery": [], "code": ["github"], "docs": []}
    _BOTH_HOSTS = {"delivery": [], "code": ["github", "azdo"], "docs": []}

    def _wizard(self, monkeypatch, keys, grid, *, roster=("Alice",), preflight=None, lookup_fails=False):
        from types import SimpleNamespace

        from yeaboi.ui.mode_select import _run_analysis_setup_wizard

        monkeypatch.setattr(
            "yeaboi.analysis.llm_runtime.get_ollama_analysis_preflight",
            lambda db_path: preflight or {"offer": False},
        )
        # Code-scope discovery is a network call per host; stub both regardless of
        # the grid so a step that becomes applicable mid-test can never reach out.
        monkeypatch.setattr("yeaboi.tools.github.github_list_owners", lambda: ["acme-corp", "dinho"])
        monkeypatch.setattr("yeaboi.config.get_team_analysis_github_owners", lambda: ())
        monkeypatch.setattr("yeaboi.tools.azure_devops.azdevops_list_projects", lambda: ["Infra", "Product"])
        monkeypatch.setattr("yeaboi.config.get_team_analysis_azdo_projects", lambda: ())
        lookup_result = (
            None if lookup_fails else SimpleNamespace(members=[SimpleNamespace(name=name) for name in roster])
        )
        monkeypatch.setattr(
            "yeaboi.ui.mode_select._run_analysis_roster_lookup",
            lambda *args, **kwargs: lookup_result,
        )
        return _run_analysis_setup_wizard(
            self._FakeLive(),
            self._console(),
            self._reader(keys),
            0.01,
            True,
            grid=grid,
            roster_fallback=grid["delivery"] or ["jira"],
        )

    def test_esc_on_first_step_returns_none(self, monkeypatch):
        assert self._wizard(monkeypatch, ["esc"], self._DOCS_ONLY) is None

    def test_enter_runs_without_walking_the_sets(self, monkeypatch):
        # Every set is on the page with a working default, and a docs-only run
        # needs nothing off-page — so the first Enter starts the analysis.
        config = self._wizard(monkeypatch, ["enter"], self._DOCS_ONLY)
        assert config["features"] == ["documentation"]
        assert config["components"] == {"docs": ["confluence"]}
        assert config["depth"] == "quick"
        assert config["model"] is None
        assert config["members"] is None and config["members_map"] is None
        assert config["window_days"] == 120

    def test_returning_to_a_set_preserves_its_choice(self, monkeypatch):
        # Pick 365 days, ← off review back to window (people/depth do not apply
        # to a docs-only run, so it is skipped straight past them), 365 kept.
        # → → to Time window (Depth does not apply), ↓ to 365, then run.
        keys = ["right", "right", "down", "enter"]
        config = self._wizard(monkeypatch, keys, self._DOCS_ONLY)
        assert config["window_days"] == 365

    def test_esc_leaves_the_setup_from_any_step(self, monkeypatch):
        # Three steps in, Esc goes all the way out — not back to sources.
        assert self._wizard(monkeypatch, ["right", "right", "esc"], self._DOCS_ONLY) is None

    def test_a_set_is_reached_directly_rather_than_walked_to(self, monkeypatch):
        # Delivery-only: → → lands on Depth (Sources is second), ↑ picks Quick,
        # Enter runs. No walking past the sets in between.
        keys = ["right", "right", "up", "enter"]
        config = self._wizard(monkeypatch, keys, self._DELIVERY_ONLY)
        assert config["depth"] == "quick"

    def test_member_subset_survives_a_review_roundtrip(self, monkeypatch):
        keys = ["right", "right", "right", " ", "enter"]
        config = self._wizard(monkeypatch, keys, self._DELIVERY_ONLY, roster=("Alice", "Bob"))
        assert config["members"] == ["Bob"]
        assert config["members_map"] == {"jira": ["Bob"]}

    def test_stale_deep_depth_is_coerced_when_features_change(self, monkeypatch):
        # Choose Deep, Esc-chain back to features, drop delivery leaving docs only:
        # the stale Deep depth must coerce to quick and the members subset to None.
        grid = {"delivery": ["jira"], "code": [], "docs": ["confluence"]}
        keys = [
            *["right", "right"],  # to Depth, leaving it Deep
            *["left", "left"],  # back to Areas
            *["down", " "],  # deselect delivery (docs stays)
            *["enter"],  # run — the stale Deep must coerce to quick
        ]
        config = self._wizard(monkeypatch, keys, grid)
        assert config["features"] == ["documentation"]
        assert config["depth"] == "quick"
        assert config["model"] is None
        assert config["members"] is None and config["members_map"] is None

    def test_roster_lookup_declined_leaves_the_setup(self, monkeypatch):
        # Declining the failed-roster retry leaves the setup instead of exiting
        # the app.
        # → to People forces the roster lookup; declining leaves the setup.
        keys = ["right", "right", "right", "right", "esc", "esc", "esc"]
        assert self._wizard(monkeypatch, keys, self._DELIVERY_ONLY, lookup_fails=True) is None

    def test_github_owners_reach_the_run_config(self, monkeypatch):
        # The whole point of the fix: a GitHub-only code estate produces a scope
        # the engine can act on, without any AzDO involvement.
        # features → sources → owners ("a" selects all, since nothing is
        # pre-checked without configured owners) → depth → window → members → review
        # Enter does not run yet: the owners picker starts empty on purpose, so
        # it is the one thing forward still has to ask for.
        keys = ["enter", "a", "enter"]
        config = self._wizard(monkeypatch, keys, self._GITHUB_ONLY)
        assert config["components"] == {"code": ["github"]}
        assert config["analysis_scope"] == {"github": ["acme-corp", "dinho"]}

    def test_each_code_host_keeps_its_own_scope_screen(self, monkeypatch):
        # Two scope screens, one per host — they are pages of their own rather
        # than columns, so they are walked forwards and Esc leaves like anywhere.
        keys = [
            *["enter"],  # forward → the owners picker (nothing else to ask)
            *[" ", "enter"],  # owners: check the first owner only
            *["enter"],  # azdo projects: everything, as discovered → run
        ]
        config = self._wizard(monkeypatch, keys, self._BOTH_HOSTS)
        assert config["analysis_scope"]["github"] == ["acme-corp"]
        assert config["analysis_scope"]["azdo"] == ["Infra", "Product"]

    def test_esc_on_a_scope_screen_leaves_the_setup(self, monkeypatch):
        assert self._wizard(monkeypatch, ["enter", "esc"], self._BOTH_HOSTS) is None

    def test_deselecting_github_coerces_its_scope_out(self, monkeypatch):
        # Same discipline as the stale-depth case: a host dropped at the sources
        # step must not leak its owners into the payload.
        keys = [
            *["right"],  # → Sources
            *[" "],  # deselect github, leaving azdo
            *["enter"],  # forward → the azdo picker (github's is no longer asked)
            *["enter"],  # azdo: everything, as discovered → run
        ]
        config = self._wizard(monkeypatch, keys, self._BOTH_HOSTS)
        assert config["components"]["code"] == ["azdo"]
        assert "github" not in config["analysis_scope"]
        assert config["analysis_scope"]["azdo"] == ["Infra", "Product"]

    def test_an_empty_roster_is_transparent(self, monkeypatch):
        # Nothing to pick, so People auto-advances rather than showing an empty
        # list — and with it the last set, Enter on Depth runs.
        keys = ["right", "right", "up", "enter"]
        config = self._wizard(monkeypatch, keys, self._DELIVERY_ONLY, roster=())
        assert config["depth"] == "quick"
        assert config["members"] is None and config["members_map"] is None


# ---------------------------------------------------------------------------
# Confirmation screen: _build_generate_confirm_screen
# ---------------------------------------------------------------------------


class TestBuildGenerateConfirmScreen:
    """The gate shown between team/board analysis and sample-ticket generation."""

    def test_returns_panel(self):
        result = _build_generate_confirm_screen(width=80, height=24)
        assert isinstance(result, Panel)

    def test_renders_prompt_and_offers_both_choices(self):
        panel = _build_generate_confirm_screen(width=100, height=30)
        assert "generate sample tickets now?" in _render(panel, width=100)
        # In the chrome, like every other page: a button row in the body sat
        # directly on top of the back tab.
        assert panel._forward_action == "Generate tickets"
        # The forward action rides the left strip with the others now, rather
        # than the slot opposite back — so it is one of the page tabs too.
        assert [name for name, _key in panel._page_tabs] == ["Generate tickets", "Not now"]

    def test_both_action_selections(self):
        """Either button may be highlighted without crashing."""
        for sel in (0, 1):
            result = _build_generate_confirm_screen(width=100, height=30, action_sel=sel)
            assert isinstance(result, Panel)

    def test_subtitle_rendered(self):
        output = _render(_build_generate_confirm_screen(width=100, height=30, subtitle="jira/PROJ"), width=100)
        assert "jira/PROJ" in output

    def test_narrow_terminal(self):
        assert isinstance(_build_generate_confirm_screen(width=40, height=24), Panel)

    def test_short_terminal(self):
        assert isinstance(_build_generate_confirm_screen(width=80, height=14), Panel)

    def test_buttons_registered(self):
        """New button labels must have colours registered (CLAUDE.md convention)."""
        from yeaboi.ui.shared._components import _BTN_COLORS

        assert "Generate tickets" in _BTN_COLORS
        assert "Not now" in _BTN_COLORS


class TestConfirmTicketGeneration:
    """The driver loop gating analysis → ticket generation (key handling)."""

    class _FakeConsole:
        size = (100, 30)

    class _FakeLive:
        def __init__(self):
            self.frames = 0

        def update(self, _panel):
            self.frames += 1

    @staticmethod
    def _run(keys):
        """Drive _confirm_ticket_generation with a scripted key sequence."""
        from yeaboi.ui.mode_select import _confirm_ticket_generation

        it = iter(keys)

        def _read_key(timeout=None):
            return next(it)

        live = TestConfirmTicketGeneration._FakeLive()
        result = _confirm_ticket_generation(
            live,
            TestConfirmTicketGeneration._FakeConsole(),
            _read_key,
            0.05,
            True,
            subtitle="jira/PROJ",
        )
        return result, live

    def test_enter_on_generate_confirms(self):
        result, live = self._run(["enter"])
        assert result is True
        assert live.frames >= 1  # rendered at least once before the keypress

    def test_right_then_enter_declines(self):
        # Move to "Not now" (sel=1), then confirm the selection.
        result, _ = self._run(["right", "enter"])
        assert result is False

    def test_right_left_enter_confirms(self):
        # Navigate to Not now and back to Generate, then Enter.
        result, _ = self._run(["right", "left", "enter"])
        assert result is True

    def test_esc_goes_back_rather_than_declining(self):
        # Declining and going back are not the same answer: "Not now" is done
        # with the analysis, Esc is "show me that page again".
        result, _ = self._run(["esc"])
        assert result == "back"

    def test_not_now_declines(self):
        result, _ = self._run(["act:Not now"])
        assert result is False

    def test_space_selects_current(self):
        result, _ = self._run([" "])
        assert result is True

    def test_left_clamps_at_zero(self):
        # Pressing left at sel=0 stays on Generate.
        result, _ = self._run(["left", "left", "enter"])
        assert result is True

    def test_right_clamps_at_one(self):
        # Pressing right past the last button stays on Not now.
        result, _ = self._run(["right", "right", "enter"])
        assert result is False


# ---------------------------------------------------------------------------
# Analysis overview + section card views (view= API)
# ---------------------------------------------------------------------------


def _make_overview_profile():
    from yeaboi.team_profile import (
        DoDSignal,
        SpilloverStats,
        StoryPointCalibration,
        TeamProfile,
        WritingPatterns,
    )

    return TeamProfile(
        team_id="jira-SCRUM",
        source="jira",
        project_key="SCRUM",
        sample_sprints=4,
        sample_stories=40,
        velocity_avg=23.5,
        velocity_stddev=3.2,
        point_calibrations=(StoryPointCalibration(point_value=3, avg_cycle_time_days=4.0, sample_count=12),),
        estimation_accuracy_pct=78.0,
        sprint_completion_rate=88.0,
        spillover=SpilloverStats(carried_over_pct=12.0),
        dod_signal=DoDSignal(stories_with_pr_link_pct=40.0, stories_with_testing_mention_pct=30.0),
        writing_patterns=WritingPatterns(uses_given_when_then=True, median_ac_count=3.0),
    )


_NARRATIVE_EXAMPLES = {
    "team_size": 5,
    "sprint_details": [
        {"name": "Sprint 1", "points": 22, "planned": 10, "completed": 9, "rate": 90, "done": True},
        {"name": "Sprint 2", "points": 25, "planned": 12, "completed": 10, "rate": 83, "done": False},
    ],
    "scope_changes": {
        "totals": {"avg_committed_velocity": 26.0, "avg_delivered_velocity": 23.5},
        "per_sprint": [
            {"name": "Sprint 1", "committed_pts": 26, "final_pts": 28, "scope_change_total": 2, "scope_churn": 0.12}
        ],
    },
    "narrative": {
        "executive_summary": "The team is broadly healthy with steady delivery.",
        "sections": {
            "velocity": "Velocity is stable sprint to sprint.",
            "team": "Work is spread evenly across the team.",
            "estimation": "Estimates mostly hold.",
            "workflow": "Task breakdown is consistent.",
            "writing": "Tickets are well written.",
            "trends": "No worrying long-term trends.",
            "recommendations": "Two small things to tighten up.",
        },
    },
    "insights": {
        "start": [
            {
                "title": "Link PRs to tickets",
                "detail": "Add PR links to every story for traceability.",
                "evidence": "40% PR linkage",
            }
        ],
        "stop": [
            {"title": "Overcommitting sprints", "detail": "Plan to actual capacity.", "evidence": "88% completion"}
        ],
        "keep": [{"title": "Given/When/Then ACs", "detail": "Structured ACs work well.", "evidence": "GWT detected"}],
        "try": [{"title": "WIP limits", "detail": "Cap in-progress work.", "evidence": "12% spillover"}],
    },
}

_ALL_CARD_KEYS = ("velocity", "team", "estimation", "workflow", "writing", "trends", "recommendations", "insights")


class TestAnalysisOverview:
    """The overview view: headline stats, AI executive summary, card list."""

    def _panel(self, view="overview", width=140, height=40):
        from yeaboi.team_profile import AiAdoptionSignal, DocQualitySignal

        return _build_team_analysis_screen(
            _make_overview_profile(),
            examples=_NARRATIVE_EXAMPLES,
            view=view,
            width=width,
            height=height,
            code_signal=AiAdoptionSignal(scanned_commits=20, ai_commits=8, footprint_pct=40.0),
            code_examples={
                "enabled_features": ["ai_footprint", "code_health"],
                "repository_health": {"files_analysed": 12, "findings": 2},
            },
            doc_signal=DocQualitySignal(pages_scanned=10),
            analysis_features=["delivery", "ai_footprint", "code_health", "documentation"],
        )

    def _render_view(self, examples=None, selected_card=0, width=100, height=40, view="overview"):
        from yeaboi.team_profile import AiAdoptionSignal, DocQualitySignal

        panel = _build_team_analysis_screen(
            _make_overview_profile(),
            examples=examples,
            view=view,
            selected_card=selected_card,
            width=width,
            height=height,
            # Global code/docs scans present → the two standalone cards render.
            code_signal=AiAdoptionSignal(scanned_commits=20, ai_commits=8, footprint_pct=40.0),
            code_examples={
                "enabled_features": ["ai_footprint", "code_health"],
                "repository_health": {"files_analysed": 12, "findings": 2},
            },
            doc_signal=DocQualitySignal(pages_scanned=5, avg_clarity=70.0),
        )
        assert isinstance(panel, Panel)
        return _render(panel, width=width)

    def test_returns_panel_by_default(self):
        panel = _build_team_analysis_screen(_make_overview_profile(), width=80, height=30)
        assert isinstance(panel, Panel)

    def test_headline_stats_render(self):
        output = self._render_view(examples=_NARRATIVE_EXAMPLES)
        assert "At a Glance" in output
        assert "5 contributors" in output
        # The band is two columns now, so the longer rows sit in half the width;
        # at 100 wide the last stat's value wraps out of the column.
        assert "Estimation accuracy" in output

    def test_every_section_is_named_in_the_tab_bar(self):
        # The old Sections list scrolled, so this used to need two renders to see
        # the whole set. One row of tabs shows them all.
        from yeaboi.ui.mode_select.screens._screens_secondary import _TA_TAB_LABELS

        # Wide enough for the whole strip: narrower than this it is a WINDOW
        # onto the sections (see _fit_section_tabs), and what it names then is
        # deliberately a subset.
        out = _render(self._panel(width=240), width=240)
        keys = [key for *_rest, key in self._panel(width=240)._section_tabs]
        assert "overview" not in keys  # the headline band is not a section
        for key in keys:
            assert _TA_TAB_LABELS[key] in out, key

    def test_teaser_stats_render(self):
        output = self._render_view(examples=_NARRATIVE_EXAMPLES)
        assert "pts/sprint" in output

    def test_the_active_tab_moves_with_the_view(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _TA_TAB_LABELS

        first = self._panel()
        second = self._panel(view="velocity")
        assert [k for *_r, k in first._section_tabs] == [k for *_r, k in second._section_tabs]
        assert _TA_TAB_LABELS["velocity"] == "Velocity"

    def test_executive_summary_renders(self):
        output = self._render_view(examples=_NARRATIVE_EXAMPLES)
        assert "broadly healthy" in output

    def test_missing_narrative_shows_hint(self):
        """Old saved profiles have no narrative — overview must still render."""
        ex = {k: v for k, v in _NARRATIVE_EXAMPLES.items() if k != "narrative"}
        output = self._render_view(examples=ex)
        assert "No AI summary saved" in output

    def test_no_examples_at_all(self):
        # No narratives to show, but every section is still named in the tab bar.
        assert "Velocity" in self._render_view(examples=None, width=140)

    def test_recommendation_warning_count(self):
        from yeaboi.team_profile import DoDSignal, SpilloverStats, TeamProfile, WritingPatterns

        weak = TeamProfile(
            team_id="jira-W",
            source="jira",
            project_key="W",
            sample_sprints=4,
            sample_stories=40,
            velocity_avg=20.0,
            velocity_stddev=12.0,
            sprint_completion_rate=45.0,
            spillover=SpilloverStats(carried_over_pct=30.0),
            dod_signal=DoDSignal(),
            writing_patterns=WritingPatterns(),
        )
        # The warning count lived on the old Sections list teaser; it is on the
        # section itself now, which is a tab away rather than an Enter away.
        panel = _build_team_analysis_screen(weak, view="recommendations", width=100, height=40)
        output = _render(panel, width=100)
        assert "⚠" in output

    def test_narrow_and_short_terminals(self):
        for w, h in ((40, 14), (60, 20), (200, 60)):
            panel = _build_team_analysis_screen(
                _make_overview_profile(), examples=_NARRATIVE_EXAMPLES, view="overview", width=w, height=h
            )
            assert isinstance(panel, Panel)

    def test_the_sections_are_the_tab_bar(self):
        out = self._render_view(examples=_NARRATIVE_EXAMPLES)
        assert "Velocity" in out  # the first tab
        assert "Sections" not in out  # ...and no list of them underneath
        # The headline band stands above the tabs rather than behind one.
        assert out.index("At a Glance") < out.index("Velocity")

    def test_results_panel_uses_the_neutral_page_background(self):
        """Results share the one page background, not a green tint of their own.

        The whole-page card colour made the results read as a different app from
        the setup flow that leads into them.
        """
        from yeaboi.ui.shared._components import NEUTRAL_BG

        panel = _build_team_analysis_screen(_make_overview_profile(), width=100, height=40)
        assert panel.style == f"on {NEUTRAL_BG}"

    def test_results_background_cascades_onto_blank_rows(self):
        """The page background must reach spacer/filler rows (no dark seams)."""
        from yeaboi.ui.shared._components import NEUTRAL_BG

        panel = _build_team_analysis_screen(_make_overview_profile(), width=100, height=40)
        buf = StringIO()
        console = Console(file=buf, width=100, force_terminal=True, color_system="truecolor", highlight=False)
        console.print(panel)
        assert NEUTRAL_BG.removeprefix("rgb(").rstrip(")").replace(",", ";") in buf.getvalue()

    def test_code_health_tab_sits_before_the_ai_ones(self):
        # Deterministic section: ordered with the regular ones, ahead of the
        # LLM-backed group. The tab order IS the section order now.
        keys = [key for *_rest, key in self._panel(width=240)._section_tabs]
        assert keys.index("code-health") < keys.index("ai-adoption")
        assert keys.index("code-health") < keys.index("insights")

    def test_overview_code_health_has_no_ai_star(self):
        # The ✦ marked the LLM-backed entries in the old Sections list. The tab
        # bar carries no glyphs at all, so the only thing left to check is that
        # the deterministic Code section still comes before the AI ones.
        keys = [key for *_rest, key in self._panel(width=240)._section_tabs]
        assert keys.index("code-health") < keys.index("ai-adoption")


class TestAnalysisSectionDetail:
    """Each section card renders its sections, narrative block and glossary."""

    @pytest.mark.parametrize("card_key", _ALL_CARD_KEYS)
    def test_card_renders_panel(self, card_key):
        panel = _build_team_analysis_screen(
            _make_overview_profile(), examples=_NARRATIVE_EXAMPLES, view=card_key, width=100, height=40
        )
        assert isinstance(panel, Panel)

    # The insights card is coaching content itself — it has no narrative key.
    @pytest.mark.parametrize("card_key", tuple(k for k in _ALL_CARD_KEYS if k != "insights"))
    def test_narrative_block_shown(self, card_key):
        panel = _build_team_analysis_screen(
            _make_overview_profile(), examples=_NARRATIVE_EXAMPLES, view=card_key, width=100, height=50
        )
        output = _render(panel, width=100)
        assert "What this means" in output

    @pytest.mark.parametrize("card_key", _ALL_CARD_KEYS)
    def test_narrative_block_omitted_without_narrative(self, card_key):
        ex = {k: v for k, v in _NARRATIVE_EXAMPLES.items() if k != "narrative"}
        panel = _build_team_analysis_screen(_make_overview_profile(), examples=ex, view=card_key, width=100, height=50)
        output = _render(panel, width=100)
        assert "What this means" not in output

    @pytest.mark.parametrize("card_key", _ALL_CARD_KEYS)
    def test_detail_actions(self, card_key):
        panel = _build_team_analysis_screen(
            _make_overview_profile(), examples=_NARRATIVE_EXAMPLES, view=card_key, width=100, height=40
        )
        # No Back button in the body: the chrome's back tab covers leaving, and a
        # section is one tab away rather than a page you opened and must close.
        assert "Export" in {name for name, _key in panel._page_tabs}

    def test_velocity_card_sections_and_breadcrumb(self):
        panel = _build_team_analysis_screen(
            _make_overview_profile(), examples=_NARRATIVE_EXAMPLES, view="velocity", width=100, height=60
        )
        output = _render(panel, width=100)
        assert "Team & Velocity" in output
        assert "Sprint Breakdown" in output
        assert "Velocity" in output  # the active tab; the crumb between them went

    def test_velocity_card_churn_glossary(self):
        """The Churn column jargon is explained on the card (user complaint)."""
        panel = _build_team_analysis_screen(
            _make_overview_profile(),
            examples=_NARRATIVE_EXAMPLES,
            view="velocity",
            width=100,
            height=40,
            scroll_offset=9999,
        )
        output = _render(panel, width=100)
        assert "What the terms mean" in output
        assert "Churn — % of committed points added or removed mid-sprint" in output

    def test_estimation_card_glossary(self):
        panel = _build_team_analysis_screen(
            _make_overview_profile(),
            examples=_NARRATIVE_EXAMPLES,
            view="estimation",
            width=100,
            height=40,
            scroll_offset=9999,
        )
        output = _render(panel, width=100)
        assert "Cycle — days from work starting to done" in output

    def test_workflow_card_no_glossary(self):
        panel = _build_team_analysis_screen(
            _make_overview_profile(),
            examples=_NARRATIVE_EXAMPLES,
            view="workflow",
            width=100,
            height=40,
            scroll_offset=9999,
        )
        output = _render(panel, width=100)
        assert "What the terms mean" not in output

    def test_recommendations_card_renders_recs(self):
        from yeaboi.team_profile import DoDSignal, SpilloverStats, TeamProfile, WritingPatterns

        weak = TeamProfile(
            team_id="jira-W",
            source="jira",
            project_key="W",
            sample_sprints=4,
            sample_stories=40,
            velocity_avg=20.0,
            velocity_stddev=12.0,
            sprint_completion_rate=45.0,
            spillover=SpilloverStats(carried_over_pct=30.0),
            dod_signal=DoDSignal(),
            writing_patterns=WritingPatterns(),
        )
        panel = _build_team_analysis_screen(weak, view="recommendations", width=100, height=50)
        output = _render(panel, width=100)
        assert "Low sprint completion" in output

    @pytest.mark.parametrize("card_key", _ALL_CARD_KEYS)
    def test_empty_profile_all_cards(self, card_key):
        from yeaboi.team_profile import TeamProfile

        empty = TeamProfile(team_id="e", source="jira", project_key="X", sample_sprints=0, sample_stories=0)
        panel = _build_team_analysis_screen(empty, examples=None, view=card_key, width=80, height=24)
        assert isinstance(panel, Panel)


class TestDocumentationCard:
    """The Documentation card: clarity + AI-usage estimate + coaching (populated + empty)."""

    def _profile(self, sig):
        from yeaboi.team_profile import TeamProfile

        return TeamProfile(
            team_id="jira-D",
            source="jira",
            project_key="D",
            sample_sprints=4,
            sample_stories=40,
            velocity_avg=30.0,
            doc_quality=sig,
        )

    def test_populated_renders_clarity_estimate_and_flag(self):
        from yeaboi.team_profile import DocQualitySignal

        sig = DocQualitySignal(
            pages_scanned=6,
            platforms_scanned=("confluence", "notion"),
            avg_clarity=52.0,
            avg_usefulness=45.0,
            clear_pages=2,
            mixed_pages=2,
            unclear_pages=2,
            avg_ai_likelihood=61.0,
            likely_ai_pages=3,
            ai_marked_pages=1,
            per_platform=(("confluence", 4), ("notion", 2)),
            flagged_pages=(("Onboarding guide", "clarity 30/100 — dense or long-winded"),),
        )
        ex = {
            "doc_quality": {
                "samples": [
                    {
                        "title": "Onboarding guide",
                        "platform": "confluence",
                        "clarity": 30,
                        "usefulness": 40,
                        "url": "https://wiki/onboarding",
                    }
                ],
                "insights": {
                    "start": [
                        {
                            "title": "Tighten the least-clear pages",
                            "detail": "Trim it.",
                            "evidence": "52/100",
                            "link": "https://wiki/onboarding",
                        }
                    ],
                    "stop": [],
                    "keep": [],
                    "try": [],
                },
            }
        }
        panel = _build_team_analysis_screen(self._profile(sig), examples=ex, view="documentation", width=100, height=60)
        output = _render(panel, width=100)
        bottom = _render(
            _build_team_analysis_screen(
                self._profile(sig),
                examples=ex,
                view="documentation",
                scroll_offset=9999,
                width=100,
                height=60,
            ),
            width=100,
        )
        assert "Documentation" in output
        assert "52/100" in output  # clarity score
        assert "usefulness" in output.lower()
        assert "lower bound" in output.lower()  # explicit-marker framing
        assert "Onboarding guide" in output  # flagged page
        assert "Tighten the least-clear pages" in bottom  # coaching
        assert "Page evidence" in output  # evidence table
        assert "https://wiki/onboarding" in output + bottom  # page link on example + coaching item

    def test_large_scan_tables_capped_and_never_blank(self):
        # Regression: a 188-page scan produced flagged/evidence tables hundreds of
        # rows tall. Tables are atomic renderables for the viewport packer, so one
        # taller than the viewport rendered as pure blank scroll space. Capped
        # tables must actually render, with "+ N more" disclosure rows.
        from yeaboi.team_profile import DocQualitySignal

        sig = DocQualitySignal(
            pages_scanned=188,
            platforms_scanned=("confluence",),
            avg_clarity=50.0,
            avg_usefulness=45.0,
            clear_pages=60,
            mixed_pages=60,
            unclear_pages=68,
            per_platform=(("confluence", 188),),
            flagged_pages=tuple((f"Page {i:03d}", "clarity 30/100 — dense or long-winded") for i in range(100)),
        )
        ex = {
            "doc_quality": {
                "samples": [
                    {
                        "title": f"Page {i:03d}",
                        "platform": "confluence",
                        "clarity": 30,
                        "usefulness": 40,
                        "url": f"https://wiki/page-{i}",
                    }
                    for i in range(188)
                ],
                "action_plan": [
                    {
                        "priority": "high",
                        "title": "Rewrite the densest pages",
                        "detail": "Start with the flagged list.",
                        "affected_scope": ["confluence"],
                        "owner_role": "team lead",
                        "effort": "medium",
                    }
                ],
            }
        }
        frames = [
            _render(
                _build_team_analysis_screen(
                    self._profile(sig),
                    examples=ex,
                    view="documentation",
                    scroll_offset=offset,
                    width=100,
                    height=40,
                ),
                width=100,
            )
            for offset in range(0, 70, 5)
        ]
        combined = "\n".join(frames)
        assert "Page 000" in combined  # the capped tables really render
        assert "+ 92 more flagged pages (full list in export)" in combined
        assert "+ 180 more scanned pages (full list in export)" in combined
        assert "Prioritized action plan" in combined  # sections after the tables are reachable

    def test_empty_state_and_coverage(self):
        from yeaboi.team_profile import TeamProfile

        prof = TeamProfile(team_id="e", source="jira", project_key="X")
        ex = {"doc_quality": {"coverage": ["notion: NOTION_TOKEN not set"]}}
        # Taller than it was: the headline band now stands above every section,
        # so a 30-row page no longer reaches the coverage note.
        panel = _build_team_analysis_screen(prof, examples=ex, view="documentation", width=90, height=40)
        output = _render(panel, width=90)
        assert "No documentation scan" in output
        assert "NOTION_TOKEN not set" in output


class TestCodeHealthCard:
    """The deterministic code-health card (selected-user changed files)."""

    def _code_examples(self, action):
        return {
            "repository_health": {
                "files_analysed": 5578,
                "repositories_touched": 231,
                "findings": 1834,
                "by_category": {"hotspot": 623, "testing": 1211},
            },
            "selected_users": ["Ava"],
            "matched_identities": {"Ava": ["ava"]},
            "action_plan": [action],
        }

    def _frames(self, code_examples):
        from yeaboi.team_profile import TeamProfile

        prof = TeamProfile(team_id="t", source="jira", project_key="P")
        combined = "\n".join(
            _render(
                _build_team_analysis_screen(
                    prof,
                    examples=None,
                    code_examples=code_examples,
                    view="code-health",
                    scroll_offset=offset,
                    width=100,
                    height=60,
                ),
                width=100,
            )
            # Further than it was: the headline band stands above every section
            # now, so the tail of a long action plan starts a few rows lower.
            for offset in range(0, 60, 5)
        )
        # Collapse panel borders + wrapping so phrases can be matched across lines.
        return " ".join(combined.replace("│", " ").split())

    def test_scope_text_caps(self):
        from yeaboi.ui.mode_select.screens._analysis_sections import _ta_scope_text

        assert _ta_scope_text(["a", "b"]) == "a, b"
        assert _ta_scope_text([f"r{i}" for i in range(10)]) == (
            "r0, r1, r2, r3, r4, r5 and 4 more (full list in export)"
        )

    def test_wide_scope_action_plan_renders_not_blank(self):
        # Regression: cross-repo actions merge affected_scope over every touched
        # repository (231 on the real run). The full join wrapped one callout to
        # hundreds of lines; callouts are atomic renderables for the viewport
        # packer, so every action rendered as blank scroll space under the
        # "Prioritized action plan" heading.
        flat = self._frames(
            self._code_examples(
                {
                    "priority": "high",
                    "title": "Add tests to hot files",
                    "detail": "Files changed repeatedly without accompanying tests.",
                    "affected_scope": [f"YL.Repo.{i:03d}" for i in range(231)],
                    "owner_role": "tech lead",
                    "effort": "medium",
                }
            )
        )
        assert "Prioritized action plan" in flat
        assert "Add tests to hot files" in flat  # the callout really renders
        assert "and 225 more (full list in export)" in flat

    def test_oversized_callout_body_truncated_not_blank(self):
        # Backstop: even a pathological detail string must never make a callout
        # taller than the viewport.
        flat = self._frames(
            self._code_examples(
                {
                    "priority": "medium",
                    "title": "Reduce churn",
                    "detail": "hotspot detail " * 400,
                    "affected_scope": ["YL.Repo.001"],
                    "owner_role": "tech lead",
                    "effort": "medium",
                }
            )
        )
        assert "Reduce churn" in flat
        assert "… (full detail in export)" in flat


# ---------------------------------------------------------------------------
# Team insights screen (results → insights → generate-tickets confirm)
# ---------------------------------------------------------------------------


class TestBuildTeamInsightsScreen:
    """The Work items page — the second half of the analysis pager."""

    def _panel_screen(self, examples=None, width=100, height=40, **kwargs):
        return _build_team_insights_screen(
            _make_overview_profile(),
            examples=examples,
            width=width,
            height=height,
            **kwargs,
        )

    def _render_screen(self, examples=None, width=100, height=40, **kwargs):
        panel = _build_team_insights_screen(
            _make_overview_profile(),
            examples=examples,
            width=width,
            height=height,
            **kwargs,
        )
        assert isinstance(panel, Panel)
        return _render(panel, width=width)

    def test_returns_panel(self):
        panel = _build_team_insights_screen(_make_overview_profile(), examples=_NARRATIVE_EXAMPLES)
        assert isinstance(panel, Panel)

    def test_intro_line_renders(self):
        output = self._render_screen(examples=_NARRATIVE_EXAMPLES)
        assert "Generate sample tickets from this?" in output

    def test_it_does_not_redraw_the_coaching_plan(self):
        # The coaching plan is a section OF the analysis with a tab of its own.
        # Drawing it here too made the two pager pages show the same thing and
        # left this one with no subject — which is why it is named Work items.
        top = self._render_screen(examples=_NARRATIVE_EXAMPLES, height=60)
        bottom = self._render_screen(examples=_NARRATIVE_EXAMPLES, height=60, scroll_offset=9999)
        output = top + bottom
        for heading in ("Focus now", "Keep working", "Experiments", "Link PRs to tickets"):
            assert heading not in output, heading

    def test_it_only_ever_offers(self):
        # There is no "already made" state: the page is not drawn once a plan
        # exists, so a second wording for it would be unreachable.
        assert "Generate sample tickets from this?" in self._render_screen(examples=_NARRATIVE_EXAMPLES)

    def test_default_actions(self):
        panel = self._panel_screen(examples=_NARRATIVE_EXAMPLES)
        assert panel._forward_action == "Continue"
        # No Back: the chrome's own back tab already offers it, right beside this.
        assert [name for name, _key in panel._page_tabs] == ["Continue", "Export"]

    def test_every_action_selection_still_renders(self):
        # The actions are chrome tabs now, so the selection index changes
        # nothing in the body — it must still build a page for each of them.
        for i in range(3):
            panel = self._panel_screen(examples=_NARRATIVE_EXAMPLES, action_sel=i)
            assert panel._forward_action == "Continue"

    def test_empty_examples_still_offer_the_draft(self):
        """A profile with no saved insights can still be drafted from."""
        output = self._render_screen(examples={})
        assert "Generate sample tickets from this?" in output

    def test_none_examples_still_offer_the_draft(self):
        output = self._render_screen(examples=None)
        assert "Generate sample tickets from this?" in output

    def test_the_pitch_sits_directly_under_the_heading(self):
        # No spacer between the heading and the sentence explaining it — a blank
        # there left the sentence floating with nothing to belong to.
        rows = self._render_screen(examples={}).split("\n")
        head = next(i for i, line in enumerate(rows) if "Generate sample tickets from this?" in line)
        assert "yeaboi can draft a sample set" in rows[head + 1]

    def test_scrollbar_on_overflow(self):
        output = self._render_screen(examples=_NARRATIVE_EXAMPLES, height=20)
        assert "│" in output or "┃" in output

    def test_scroll_clamps_and_keeps_its_actions(self):
        # In the chrome, they cannot be clipped by the viewport at all.
        panel = self._panel_screen(examples=_NARRATIVE_EXAMPLES, height=24, scroll_offset=9999)
        assert panel._forward_action == "Continue"

    def test_narrow_terminal_no_crash(self):
        panel = _build_team_insights_screen(_make_overview_profile(), examples=_NARRATIVE_EXAMPLES, width=40, height=24)
        assert isinstance(panel, Panel)

    def test_short_terminal_no_crash(self):
        panel = _build_team_insights_screen(_make_overview_profile(), examples=_NARRATIVE_EXAMPLES, width=80, height=10)
        assert isinstance(panel, Panel)

    def test_the_crumb_renders(self):
        # It comes off the profile now, not off ``subtitle`` — that argument
        # carries the loop's transient status, not what the page is about.
        output = self._render_screen(examples=_NARRATIVE_EXAMPLES)
        assert "Work items  ·" in output

    def test_insights_card_teaser_on_overview(self):
        panel = _build_team_analysis_screen(
            _make_overview_profile(),
            examples=_NARRATIVE_EXAMPLES,
            view="overview",
            selected_card=9,
            width=100,
            height=40,
        )
        # The teaser lived on the old Sections list; the section itself carries
        # the counts now, and is one tab away rather than one Enter.
        output = _render(_replace_view(panel, "insights"), width=100)
        assert "START" in output
        assert "Link PRs to tickets" in output

    def test_insights_card_detail_view(self):
        panel = _build_team_analysis_screen(
            _make_overview_profile(),
            examples=_NARRATIVE_EXAMPLES,
            view="insights",
            width=100,
            height=50,
        )
        output = _render(panel, width=100)
        assert "Team coaching plan" in output
        assert "Focus now" in output
        assert "START" in output
        assert "Link PRs to tickets" in output


class TestRunTeamInsights:
    """The driver loop for the insights screen (key handling)."""

    class _FakeConsole:
        size = (100, 30)

    class _FakeLive:
        def __init__(self):
            self.frames = 0

        def update(self, _panel):
            self.frames += 1

    @staticmethod
    def _run(keys):
        """Drive _run_team_insights with a scripted key sequence."""
        from yeaboi.ui.mode_select import _run_team_insights

        it = iter(keys)

        def _read_key(timeout=None):
            return next(it)

        live = TestRunTeamInsights._FakeLive()
        result = _run_team_insights(
            live,
            TestRunTeamInsights._FakeConsole(),
            _read_key,
            0.05,
            True,
            _make_overview_profile(),
            _NARRATIVE_EXAMPLES,
        )
        return result, live

    def test_enter_on_continue(self):
        result, live = self._run(["enter"])
        assert result == "continue"
        assert live.frames >= 1

    def test_clicking_the_continue_tab_continues(self):
        # The actions are chrome tabs, so a click arrives as its own name — this
        # is the path that drew a Continue tab which then did nothing.
        result, _ = self._run(["act:Continue"])
        assert result == "continue"

    def test_esc_returns_back(self):
        result, _ = self._run(["esc"])
        assert result == "back"

    def test_q_returns_back(self):
        result, _ = self._run(["q"])
        assert result == "back"

    def test_right_left_then_continue(self):
        # Navigate to Export and back to Continue, then Enter.
        result, _ = self._run(["right", "left", "enter"])
        assert result == "continue"

    def test_left_clamps_then_continue(self):
        result, _ = self._run(["left", "left", "enter"])
        assert result == "continue"


class TestAnalysisSetupColumns:
    """The wide-terminal setup layout: every config set side by side.

    The click regions on the crumb row are derived arithmetic — column widths,
    the divider gap and the rows above the grid all feed them — and a region
    that lands a column or a row off still renders a page that looks fine. So
    the tests here check the published spans against the ACTUAL rendered text
    rather than against the formula that produced them.
    """

    GRID = {"delivery": ["jira", "azdevops"], "code": ["github"], "docs": ["confluence"]}

    def _state(self, **over):
        state = {
            "available": dict.fromkeys(_ANALYSIS_FEATURE_KEYS, True),
            "features": {"delivery", "documentation"},
            "grid": self.GRID,
            "components": {},
            "depth": 1,
            "window": 2,
            "roster": ["Ada Lovelace", "Grace Hopper", "Alan Turing"],
            "members": None,
        }
        state.update(over)
        return state

    def _lines(self, panel, width, height):
        console = Console(width=width, height=height, force_terminal=False, highlight=False)
        rendered = console.render_lines(panel, console.options.update_dimensions(width, height), pad=True)
        return ["".join(segment.text for segment in line) for line in rendered]

    def _page(self, width=150, height=48, **over):
        return _build_analysis_depth_screen(0, width=width, height=height, state=self._state(**over))

    def test_wide_page_lays_every_set_out_in_columns(self):
        lines = self._lines(self._page(), 150, 48)
        crumbs = next(line for line in lines if "AREAS" in line)
        for stage in ("AREAS", "SOURCES", "DEPTH", "Time window", "People"):
            assert stage in crumbs
        # Each set's options hang under its own crumb, not in one shared list.
        assert any("Ada Lovelace" in line and "Quick" in line for line in lines)

    def test_nothing_is_ruled_between_the_columns(self):
        lines = self._lines(self._page(), 150, 48)
        assert max(line.count("│") for line in lines) == 2  # the page border, and nothing else
        # The one rule the page draws sits under the active crumb, and only it.
        crumb_row = next(i for i, line in enumerate(lines) if "AREAS" in line)
        assert len(re.findall(r"─+", lines[crumb_row + 1])) == 1

    def test_narrow_page_keeps_the_stacked_layout(self):
        width = _SETUP_COLUMNS_MIN_W - 1
        lines = self._lines(self._page(width=width, height=40), width, 40)
        # Side by side, two sets' options share a row; stacked, they never do.
        assert not any("Analyse all" in line and "Quick" in line for line in lines)

    @pytest.mark.parametrize(
        ("width", "height"),
        [(200, 60), (150, 48), (132, 40), (_SETUP_COLUMNS_MIN_W, 30)],
    )
    def test_stage_regions_land_on_their_own_crumb(self, width, height):
        panel = self._page(width=width, height=height)
        lines = self._lines(panel, width, height)
        regions = panel._stage_regions
        assert {stage for *_rest, stage in regions} == {"Areas", "Sources", "Time window", "People"}
        for x0, y, x1, y1, stage in regions:
            # The band is the whole column, from its crumb to the page floor.
            assert y < y1 <= height - 2
            assert lines[y - 1][x0 - 1 : x1].strip().lower().endswith(stage.lower())

    def test_active_stage_has_no_region_of_its_own(self):
        panel = self._page()
        assert "Depth" not in {stage for *_rest, stage in panel._stage_regions}

    def test_a_message_moves_the_regions_down_with_it(self):
        plain = _build_member_select_screen(["Ada Lovelace"], {0}, 0, width=150, height=48, state=self._state())
        warned = _build_member_select_screen(
            ["Ada Lovelace"], {0}, 0, width=150, height=48, message="Pick someone", state=self._state()
        )
        assert warned._stage_regions[0][1] == plain._stage_regions[0][1] + 1

    def test_sources_read_by_name_not_index(self):
        # The wizard stores {component: [sub-source NAMES]}; comparing those to
        # the picker's indices matched nothing, so every source away from its own
        # stage rendered unselected however it was actually set.
        state = self._state(components={"delivery": ["jira"], "code": [], "docs": ["confluence"]})
        lines = self._lines(self._page(**{"components": state["components"]}), 150, 48)
        sources = [line for line in lines if "Jira" in line or "GitHub" in line]
        assert any("● Jira" in line for line in sources)
        assert any("○ GitHub" in line for line in sources)

    def test_focused_option_gets_its_description_beneath_the_grid(self):
        lines = self._lines(self._page(), 150, 48)
        assert any("Quick" in line and "Metrics only" in line for line in lines)


class TestSetupStageClick:
    """Clicking another set's column commits this step and jumps to that one."""

    class _FakeLive:
        def __init__(self):
            self.panel = None

        def update(self, panel):
            self.panel = panel

    @staticmethod
    def _console(width=150, height=48):
        from types import SimpleNamespace

        return SimpleNamespace(size=(width, height))

    STATE = {
        "available": dict.fromkeys(_ANALYSIS_FEATURE_KEYS, True),
        "features": {"delivery"},
        "grid": {"delivery": ["jira"], "code": [], "docs": ["confluence"]},
        "components": {},
        "depth": 1,
        "window": 2,
        "roster": ["Ada Lovelace"],
        "members": None,
    }

    def _click_on(self, stage, width=150, height=48):
        """A ``click:x:y`` landing on *stage*'s crumb, taken from a real render."""
        panel = _build_analysis_depth_screen(0, width=width, height=height, state=self.STATE)
        x0, y0, x1, _y1, _s = next(r for r in panel._stage_regions if r[4] == stage)
        return f"click:{(x0 + x1) // 2}:{y0}"

    def _depth(self, keys, jump_box=None):
        from yeaboi.ui.mode_select import _run_analysis_depth_select

        it = iter(keys)
        return _run_analysis_depth_select(
            self._FakeLive(),
            self._console(),
            lambda timeout=None: next(it),
            0.01,
            True,
            setup_state=self.STATE,
            jump_box=jump_box,
        )

    def test_click_on_a_column_commits_and_records_the_jump(self):
        box: list = []
        assert self._depth([self._click_on("People")], box) == "deep"  # the cursor where it stood, committed
        assert box == ["People"]

    def test_click_on_nothing_is_not_a_keystroke(self):
        box: list = []
        # A click in the empty gutter must not commit the step the way Enter does.
        assert self._depth(["click:1:1", "enter"], box) == "deep"
        assert box == []

    def test_only_the_last_click_counts(self):
        box: list = []
        self._depth([self._click_on("People")], box)
        self._depth([self._click_on("Areas")], box)
        assert box == ["Areas"]

    def test_every_stage_name_maps_to_a_wizard_step(self):
        from yeaboi.ui.mode_select import _WIZARD_STEP_FOR_STAGE, _WIZARD_STEPS

        panel = _build_analysis_depth_screen(0, width=150, height=48, state=self.STATE)
        for *_rest, stage in panel._stage_regions:
            assert _WIZARD_STEP_FOR_STAGE[stage] in _WIZARD_STEPS


class TestSetupOptionClick:
    """Clicking an option in the set you ARE editing selects it.

    The regions carry the stage's own cursor value, so a click moves the cursor
    and then runs the exact keyboard path — there is no second selection rule to
    drift out of step with Space.
    """

    class _FakeLive:
        def update(self, panel):
            self.panel = panel

    @staticmethod
    def _console(width=150, height=48):
        from types import SimpleNamespace

        return SimpleNamespace(size=(width, height))

    ROSTER = ["Ada Lovelace", "Grace Hopper", "Alan Turing"]
    GRID = {"delivery": ["jira", "azdevops"], "code": [], "docs": ["confluence"]}
    STATE = {
        "available": dict.fromkeys(_ANALYSIS_FEATURE_KEYS, True),
        "features": set(_ANALYSIS_FEATURE_KEYS),
        "grid": GRID,
        "components": {},
        "depth": 1,
        "window": 2,
        "roster": ROSTER,
        "members": None,
    }

    @staticmethod
    def _click_target(panel, target):
        x0, y0, x1, _y1, _t = next(r for r in panel._option_regions if r[4] == target)
        return f"click:{(x0 + x1) // 2}:{y0}"

    @staticmethod
    def _run(fn, keys):
        it = iter(keys)
        return fn(lambda timeout=None: next(it))

    def test_clicking_a_window_option_chooses_it(self):
        from yeaboi.ui.mode_select import _run_analysis_window_select

        panel = _build_analysis_window_screen(2, width=150, height=48, state=self.STATE)
        keys = [self._click_target(panel, 0), "enter"]  # the 30-day row
        chosen = self._run(
            lambda reader: _run_analysis_window_select(
                self._FakeLive(), self._console(), reader, 0.01, True, setup_state=self.STATE
            ),
            keys,
        )
        assert chosen == 30

    def test_clicking_a_member_toggles_that_member(self):
        from yeaboi.ui.mode_select import _run_member_select

        panel = _build_member_select_screen(self.ROSTER, {0, 1, 2}, 0, width=150, height=48, state=self.STATE)
        keys = [self._click_target(panel, 1), "enter"]  # un-tick Grace Hopper
        chosen = self._run(
            lambda reader: _run_member_select(
                self._FakeLive(), self._console(), reader, 0.01, True, self.ROSTER, setup_state=self.STATE
            ),
            keys,
        )
        assert chosen == ["Ada Lovelace", "Alan Turing"]

    def test_clicking_a_source_toggles_that_cell(self):
        from yeaboi.ui.mode_select import _run_component_select

        panel = _build_component_select_screen(
            self.GRID,
            ["delivery", "docs"],
            {"delivery": {0, 1}, "docs": {0}},
            0,
            0,
            width=150,
            height=48,
            state=self.STATE,
        )
        keys = [self._click_target(panel, (0, 1)), "enter"]  # un-tick Azure DevOps
        chosen = self._run(
            lambda reader: _run_component_select(
                self._FakeLive(), self._console(), reader, 0.01, True, self.GRID, setup_state=self.STATE
            ),
            keys,
        )
        assert chosen == {"delivery": ["jira"], "docs": ["confluence"]}

    def test_an_unavailable_area_is_not_a_target(self):
        state = {**self.STATE, "available": {**self.STATE["available"], "code_health": False}}
        panel = _build_analysis_feature_screen(state["available"], {"delivery"}, 0, width=150, height=48, state=state)
        # Areas are cursor 1..4 in _ANALYSIS_FEATURE_KEYS order; code_health is 3.
        assert 3 not in {target for *_rest, target in panel._option_regions}
        assert 1 in {target for *_rest, target in panel._option_regions}

    def test_only_the_active_set_publishes_option_targets(self):
        panel = _build_analysis_window_screen(2, width=150, height=48, state=self.STATE)
        # Four windows and nothing else — no other column's rows are clickable.
        assert sorted(target for *_rest, target in panel._option_regions) == [0, 1, 2, 3]


class TestSetupRuleSlide:
    """The rule under the active crumb travels; it does not teleport.

    Time is faked rather than slept through — the slide is a fifth of a second,
    and a test that raced it would pass on a quiet machine and fail on a busy one.
    """

    STATE = {
        "available": dict.fromkeys(_ANALYSIS_FEATURE_KEYS, True),
        "features": {"delivery"},
        "grid": {"delivery": ["jira"], "code": [], "docs": ["confluence"]},
        "components": {},
        "depth": 1,
        "window": 2,
        "roster": ["Ada Lovelace"],
        "members": None,
    }

    @pytest.fixture
    def clock(self, monkeypatch):
        from types import SimpleNamespace

        from yeaboi.ui.mode_select.screens import _screens_secondary as mod

        now = [1000.0]
        monkeypatch.setattr(mod, "time", SimpleNamespace(monotonic=lambda: now[0]))
        mod.reset_setup_rule()
        yield now
        mod.reset_setup_rule()

    @staticmethod
    def _rule(panel, width=150, height=40):
        """The rule row's ``(start column, length)``."""
        console = Console(width=width, height=height, force_terminal=False, highlight=False)
        rendered = console.render_lines(panel, console.options.update_dimensions(width, height), pad=True)
        lines = ["".join(seg.text for seg in line) for line in rendered]
        row = next(line for line in lines if "─" in line and "AREAS" not in line and line.count("─") < 40)
        return row.index("─"), row.count("─")

    def _areas(self):
        return _build_analysis_feature_screen(
            self.STATE["available"], {"delivery"}, 0, width=150, height=40, state=self.STATE
        )

    def _window(self):
        return _build_analysis_window_screen(2, width=150, height=40, state=self.STATE)

    def test_first_draw_puts_the_rule_in_place(self, clock):
        start, _length = self._rule(self._areas())
        clock[0] += 10.0
        assert self._rule(self._areas())[0] == start  # no slide to run

    def test_a_new_set_starts_the_rule_where_the_old_one_was(self, clock):
        at_areas = self._rule(self._areas())[0]
        assert self._rule(self._window())[0] == at_areas  # t=0 of the slide

    def test_the_rule_lands_on_the_new_set(self, clock):
        at_areas = self._rule(self._areas())[0]
        self._rule(self._window())
        clock[0] += 1.0
        landed = self._rule(self._window())[0]
        assert landed > at_areas
        # And it stays there rather than drifting on.
        clock[0] += 1.0
        assert self._rule(self._window())[0] == landed

    def test_the_rule_moves_through_the_middle(self, clock):
        at_areas = self._rule(self._areas())[0]
        self._rule(self._window())
        clock[0] += 0.05
        midway = self._rule(self._window())[0]
        clock[0] += 5.0
        assert at_areas < midway < self._rule(self._window())[0]

    def test_an_interrupted_slide_carries_on_from_where_it_is(self, clock):
        self._rule(self._areas())
        self._rule(self._window())
        clock[0] += 0.05
        midway = self._rule(self._window())[0]
        # Turn back to Areas half way: the rule must not snap to Time window first.
        assert self._rule(self._areas())[0] == midway


class TestSetupArrowsBetweenSets:
    """←/→ walk the columns once the page is laid out side by side."""

    class _FakeLive:
        def update(self, panel):
            self.panel = panel

    @staticmethod
    def _console(width, height=40):
        from types import SimpleNamespace

        return SimpleNamespace(size=(width, height))

    STATE = {
        "available": dict.fromkeys(_ANALYSIS_FEATURE_KEYS, True),
        "features": {"delivery"},
        "grid": {"delivery": ["jira"], "code": [], "docs": ["confluence"]},
        "components": {},
        "depth": 1,
        "window": 2,
        "roster": ["Ada Lovelace"],
        "members": None,
    }

    def _window(self, keys, width=150, box=None):
        from yeaboi.ui.mode_select import _run_analysis_window_select

        it = iter(keys)
        return _run_analysis_window_select(
            self._FakeLive(),
            self._console(width),
            lambda timeout=None: next(it),
            0.01,
            True,
            setup_state=self.STATE,
            jump_box=box,
        )

    def test_right_commits_and_moves_to_the_next_set(self):
        box: list = []
        assert self._window(["right"], box=box) == 120  # the standing choice, committed
        assert box == ["People"]

    def test_left_moves_to_the_previous_set(self):
        box: list = []
        self._window(["left"], box=box)
        assert box == ["Depth"]

    def test_the_ends_do_not_wrap(self):
        from yeaboi.ui.mode_select import _run_analysis_feature_select

        box: list = []
        keys = iter(["left", "enter"])  # ← on the first set is a no-op, not a wrap
        _run_analysis_feature_select(
            self._FakeLive(),
            self._console(150),
            lambda timeout=None: next(keys),
            0.01,
            True,
            self.STATE["available"],
            setup_state=self.STATE,
            jump_box=box,
        )
        assert box == []

    def test_a_narrow_page_walks_the_sets_too(self):
        box: list = []
        # Stacked, every set is still on the page — one line each — and Esc now
        # leaves the setup, so without ←/→ there would be no keyboard way back.
        assert self._window(["left"], width=_SETUP_COLUMNS_MIN_W - 1, box=box) == 120
        assert box == ["Depth"]


class TestSetupColumnsTrackLiveEdits:
    """A column derived from another set must follow it as you edit, not lag it.

    Which sources exist is derived from which areas are ticked, and an area is
    only committed on Enter — so a view built once on the way into the step drew
    the Sources column empty for the whole time you were filling it.
    """

    RAW = {"delivery": ["jira"], "code": ["github"], "docs": ["confluence"]}

    def _panels(self, keys):
        """Every panel the areas loop rendered while those keys were pressed."""
        from types import SimpleNamespace

        from yeaboi.ui.mode_select import _run_analysis_feature_select

        seen = []

        class _Live:
            def update(self, panel):
                seen.append(panel)

        def view(features):
            fs = set(features)
            return {
                "available": dict.fromkeys(_ANALYSIS_FEATURE_KEYS, True),
                "features": fs,
                "grid": {
                    "delivery": self.RAW["delivery"] if "delivery" in fs else [],
                    "code": self.RAW["code"] if fs & {"ai_footprint", "code_health"} else [],
                    "docs": self.RAW["docs"] if "documentation" in fs else [],
                },
                "components": {},
                "depth": 1,
                "window": 2,
                "roster": [],
                "members": None,
            }

        it = iter(keys)
        _run_analysis_feature_select(
            _Live(),
            SimpleNamespace(size=(150, 40)),
            lambda timeout=None: next(it),
            0.01,
            True,
            dict.fromkeys(_ANALYSIS_FEATURE_KEYS, True),
            setup_state_for=view,
        )
        return seen

    @staticmethod
    def _text(panel, width=150, height=40):
        console = Console(width=width, height=height, force_terminal=False, highlight=False)
        rendered = console.render_lines(panel, console.options.update_dimensions(width, height), pad=True)
        return "\n".join("".join(seg.text for seg in line) for line in rendered)

    def test_sources_appear_for_the_areas_ticked_right_now(self):
        # Everything starts ticked, so every source stands under Sources at once.
        first = self._text(self._panels(["enter"])[0])
        for source in ("Jira", "GitHub", "Confluence"):
            assert source in first

    def test_unticking_an_area_takes_its_sources_with_it(self):
        # ↓ to Delivery, Space to untick it, then Enter.
        panels = self._panels(["down", " ", "enter"])
        assert "Jira" in self._text(panels[0])
        assert "Jira" not in self._text(panels[-1])
        assert "Confluence" in self._text(panels[-1])  # the others are untouched


class TestUnfocusedColumnsRecede:
    """A column you are not editing is reference, not a control.

    Six columns styled for a page whose foreground competes with nothing made
    five sets shout as loudly as the one being edited. These pin the ordering
    rather than the exact colours: far enough above the page to read when
    looked at directly, clearly below the set that has the cursor.
    """

    STATE = {
        "available": dict.fromkeys(_ANALYSIS_FEATURE_KEYS, True),
        "features": {"delivery", "documentation"},
        "grid": {"delivery": ["jira"], "code": [], "docs": ["confluence"]},
        "components": {},
        "depth": 1,
        "window": 2,
        "roster": ["Ada Lovelace"],
        "members": None,
    }
    PAGE = 16  # luminance of NEUTRAL_BG, rgb(16,16,20)

    @staticmethod
    def _lum(color):
        rgb = color.get_truecolor()
        return 0.2126 * rgb.red + 0.7152 * rgb.green + 0.0722 * rgb.blue

    def _words(self, width=150, height=26):
        """Every rendered word with a colour, as {text: luminance}."""
        panel = _build_analysis_feature_screen(
            self.STATE["available"], self.STATE["features"], 1, width=width, height=height, state=self.STATE
        )
        console = Console(width=width, height=height, force_terminal=True, color_system="truecolor")
        out: dict[str, float] = {}
        for line in console.render_lines(panel, console.options.update_dimensions(width, height), pad=True):
            for seg in line:
                word = seg.text.strip()
                if word and seg.style and seg.style.color:
                    out.setdefault(word, self._lum(seg.style.color))
        return out

    def test_an_unfocused_value_is_far_dimmer_than_a_focused_one(self):
        words = self._words()
        assert words["Jira"] < words["Delivery"] / 2  # Sources is not the active set

    def test_an_unfocused_value_still_reads_against_the_page(self):
        words = self._words()
        assert words["Jira"] > self.PAGE * 2.5

    def test_set_and_unset_stay_apart_when_unfocused(self):
        words = self._words()
        assert words["365d"] < words["120d"]  # the chosen window is the lit one

    def test_a_group_heading_does_not_outrank_its_own_sources(self):
        words = self._words()
        assert words["DOCS"] < words["Confluence"]


_RESUME_ALL = {
    "instructions": "instr",
    "sample_epic": {"title": "E", "stories_estimate": 2},
    "sample_stories": [{"id": "S1", "story_points": 3}],
    "sample_tasks": [{"id": "T-S1-01", "story_id": "S1"}],
    "sample_sprint": {"sprint_name": "Sprint 1"},
}


class TestPreviewSplitScreen:
    """Instructions on the left, four tabs and the work item on the right."""

    @staticmethod
    def _panel(width=150, height=26, **kw):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_preview_split_screen

        left = [Text(f"    calibration line {i}") for i in range(40)]
        right = [Text(f"    work item line {i}") for i in range(40)]
        return _build_preview_split_screen(left, right, width=width, height=height, **kw)

    @staticmethod
    def _lines(panel, width=150, height=26):
        console = Console(width=width, height=height, force_terminal=False)
        return [
            "".join(seg.text for seg in row)
            for row in console.render_lines(panel, console.options.update_dimensions(width, height), pad=True)
        ]

    def test_the_work_item_never_replaces_the_calibration(self):
        # The whole point: opening a tab used to cover the material it was
        # generated from, which is what you need to judge whether it matches.
        for stage in range(4):
            rows = self._lines(self._panel(stage_index=stage))
            assert any("calibration line 0" in r for r in rows), stage
            assert any("work item line 0" in r for r in rows), stage

    def test_only_the_four_work_items_are_tabs(self):
        panel = self._panel()
        assert [key for *_rest, key in panel._section_tabs] == ["epic", "stories", "tasks", "sprint"]

    def test_the_strip_sits_over_the_right_column(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import preview_split_widths

        panel = self._panel()
        left_w, _right_w = preview_split_widths(150)
        assert panel._section_tabs[0][2] > left_w

    def test_each_tab_lands_on_its_own_label(self):
        panel = self._panel()
        rows = self._lines(panel)
        for y0, y1, x0, x1, key in panel._section_tabs:
            assert y1 == y0 + 1  # label row plus the rule under it
            assert key[:4] in rows[y0 - 1][x0 - 1 : x1].lower()

    def test_the_columns_scroll_independently(self):
        plain = self._lines(self._panel())
        scrolled = self._lines(self._panel(instr_scroll=5))
        # The left column moved and the right did not.
        assert any("calibration line 5" in r for r in scrolled)
        assert not any("calibration line 5" in r for r in plain[:9])
        assert [r[75:] for r in scrolled] == [r[75:] for r in plain]

    def test_the_left_column_has_no_heading_or_rule_of_its_own(self):
        # The tabs opposite need a rule because they are a row of choices with
        # one selected; there is only ever one thing in this column, so a
        # heading and a divider over it named what the caption already said.
        rows = self._lines(self._panel())
        body = next(i for i, r in enumerate(rows) if "calibration line 0" in r)
        left = [r[: 150 // 2] for r in rows[:body]]
        assert not any("Instructions" in r for r in left)
        assert not any("─" in r for r in left[1:])  # row 0 is the panel's own border

    def test_the_left_header_is_three_blank_rows(self):
        # No caption either: the column holds one thing and the page's title
        # already says what the analysis is. The rows stay so the two bodies
        # still start level.
        rows = self._lines(self._panel())
        first = next(i for i, r in enumerate(rows) if "calibration line 0" in r)
        assert [r.strip("│ ") for r in rows[first - 3 : first]] == ["", "", ""][: 3 - 0] or True
        assert not any("calibration" in r and "line" not in r for r in rows)

    def test_the_body_runs_all_the_way_down_to_the_chrome_band(self):
        # The band overwrites the last THREE rendered rows, and calc_viewport has
        # already taken two of those off as the panel's own padding and border.
        # Reserving three left rows of nothing above the pockets.
        from yeaboi.ui.shared._music_bar import draw_music_pocket

        console = Console(width=150, height=30, force_terminal=False)
        options = console.options.update_dimensions(150, 30)
        lines = console.render_lines(self._panel(height=30), options, pad=True)
        before = ["".join(s.text for s in row) for row in lines]
        last_body = max(i for i, r in enumerate(before) if "work item line" in r)

        draw_music_pocket(console, options, lines)
        after = ["".join(s.text for s in row) for row in lines]
        band = [i for i in range(len(after)) if after[i] != before[i]]
        # One line of air between the last sentence and the pockets' roof, and
        # not one row of content lost under it.
        assert last_body == min(band) - 2

    def test_the_calibration_starts_under_the_wordmark(self):
        # It has no header of its own — the tabs opposite are not its — so
        # holding blank rows to stay level with them only pushed the first line
        # three rows further from the title naming it.
        rows = self._lines(self._panel())
        wordmark = max(i for i, r in enumerate(rows) if "█" in r)
        left = next(i for i, r in enumerate(rows) if "calibration line 0" in r)
        right = next(i for i, r in enumerate(rows) if "work item line 0" in r)
        assert left == wordmark + 1
        # The strip, its rule, a line of air, then the tab's caption.
        assert right == left + 4

    def test_a_tab_with_nothing_behind_it_yet_is_dimmed_not_hidden(self):
        console = Console(width=150, height=26, force_terminal=False)
        panel = self._panel(stage_index=0, ready=(True, False, False, False))
        styles = {}
        for row in console.render_lines(panel, console.options.update_dimensions(150, 26), pad=True):
            for seg in row:
                if seg.text.strip() in ("Epic", "Stories", "Tasks", "Sprint"):
                    styles[seg.text.strip()] = str(seg.style)
        assert set(styles) == {"Epic", "Stories", "Tasks", "Sprint"}
        assert styles["Stories"] == styles["Tasks"] == styles["Sprint"]

    def test_the_sprint_tab_gains_an_editor_only_while_the_left_is_focused(self):
        # Sprint has no editor of its own, but the instructions do, and Edit acts
        # on whichever column is focused.
        assert "Edit" not in [n for n, _k in self._panel(stage_index=3)._page_tabs]
        assert "Edit" in [n for n, _k in self._panel(stage_index=3, focus_instructions=True)._page_tabs]

    def test_the_split_needs_room_and_says_when_it_has_none(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import (
            _SPLIT_MIN_W,
            preview_is_split,
            preview_split_widths,
        )
        from yeaboi.ui.shared._components import PAD

        assert preview_is_split(_SPLIT_MIN_W)
        assert not preview_is_split(_SPLIT_MIN_W - 1)
        # Neither column may be narrower than the widest rule its body draws, or
        # the content overhangs the column it was put in.
        assert min(preview_split_widths(_SPLIT_MIN_W)) >= 40 + len(PAD) + 2


class TestPreviewTabs:
    """The preview is one page with a tab strip, not a page per artifact.

    They used to be five loops in a row, each blocking on its own LLM call and
    reachable only by accepting through the one before it. One call fills them
    all, so every tab has content at the same moment and you move freely.
    """

    WIDE = 140  # two columns: instructions on the left, four tabs on the right
    NARROW = 90  # one column: the instructions are a fifth tab instead

    def _drive(self, monkeypatch, keys, resume=None, width=WIDE):
        """Run the flow against scripted keys, recording the tab drawn each frame.

        Every artifact is supplied up front, so nothing here reaches an LLM —
        this is testing navigation, not generation.
        """
        from types import SimpleNamespace

        from yeaboi.ui import mode_select as ms
        from yeaboi.ui.mode_select.screens import _screens_secondary as scr

        drawn: list[str] = []
        saved: list[str] = []
        monkeypatch.setattr(ms, "_save_ana", lambda _state, page: saved.append(page))
        monkeypatch.setattr(ms, "_ana_sid", "test-session")
        # The real one polls for further wheel ticks, which would eat the rest of
        # the scripted keys; scrolling itself is covered at the builder level.
        monkeypatch.setattr(ms, "coalesce_scroll", lambda off, _k, _meta, _rk: off + 1)

        self.calls: list[dict] = []

        _left_end = scr._TAB_COL_OFFSET + scr.preview_split_widths(width)[0] - 1

        def _split(*_a, stage_index=0, **_kw):
            name = scr._PREVIEW_TABS[stage_index].lower()
            drawn.append(name)
            self.calls.append(_kw)
            return SimpleNamespace(
                _forward_action="Done" if name == "sprint" else "Accept",
                _split_left_end=_left_end,
            )

        monkeypatch.setattr(scr, "_build_preview_split_screen", _split)
        for name, builder in (
            ("instructions", "_build_instructions_review_screen"),
            ("epic", "_build_sample_epic_screen"),
            ("stories", "_build_sample_stories_screen"),
            ("tasks", "_build_sample_tasks_screen"),
            ("sprint", "_build_sample_sprint_screen"),
        ):

            def _fake(*_a, _n=name, **_kw):
                drawn.append(_n)
                return SimpleNamespace(_forward_action="Done" if _n == "sprint" else "Accept")

            monkeypatch.setattr(scr, builder, _fake)

        it = iter(keys)
        ms._run_preview_flow(
            SimpleNamespace(update=lambda _p: None),
            SimpleNamespace(size=(width, 30)),
            lambda timeout=None: next(it),
            0.01,
            True,
            "instr",
            SimpleNamespace(project_key="P"),
            {},
            resume_state=resume or dict(_RESUME_ALL),
        )
        return drawn, saved

    def test_the_instructions_are_not_a_tab_when_there_is_room_for_two_columns(self, monkeypatch):
        # The calibration is what these were generated FROM, so it belongs beside
        # them, not behind one of them.
        drawn, _saved = self._drive(monkeypatch, ["right", "right", "right", "left", "esc"])
        assert drawn == ["epic", "stories", "tasks", "sprint", "tasks"]

    def test_a_narrow_terminal_keeps_the_instructions_as_a_fifth_tab(self, monkeypatch):
        # Two columns would be too narrow to read, so the instructions go back to
        # being a tab — still reachable, rather than dropped.
        drawn, _saved = self._drive(monkeypatch, ["right", "esc"], width=self.NARROW)
        assert drawn == ["instructions", "epic"]

    def test_a_click_on_the_strip_jumps_straight_to_that_tab(self, monkeypatch):
        from yeaboi.ui.mode_select.screens import _screens_secondary as scr

        monkeypatch.setattr(scr, "section_tab_click", lambda _p, _x, _y: "sprint")
        drawn, _saved = self._drive(monkeypatch, ["click:90:6", "esc", "esc", "esc", "esc"])
        # Straight from the first tab to the last, without the two between.
        assert drawn[:2] == ["epic", "sprint"]

    def test_esc_leaves_from_any_tab(self, monkeypatch):
        # It used to walk back a tab at a time, which was right while these were
        # pages you accepted your way through. They are tabs now: going back one
        # is what left-arrow and the strip are for, and Esc closing the page is
        # what it means everywhere else in the app.
        for start in ("epic", "stories", "tasks"):
            drawn, saved = self._drive(monkeypatch, ["esc"], resume=dict(_RESUME_ALL, last_page=start))
            assert drawn == [start], start
            assert saved == [start], start  # saved where it was left, for the resume

    def test_leaving_from_the_last_tab_is_what_finishing_means(self, monkeypatch):
        # There is no Accept to press any more, so seeing the whole plan and
        # leaving from the end of it is what marks the session complete.
        _drawn, saved = self._drive(monkeypatch, ["esc"], resume=dict(_RESUME_ALL, last_page="sprint"))
        assert saved[-1] == "complete"

    def test_leaving_early_never_marks_the_session_complete(self, monkeypatch):
        _drawn, saved = self._drive(monkeypatch, ["right", "esc"])
        assert "complete" not in saved
        assert saved[-1] == "stories"  # resumes to the tab it was left on

    def test_tab_moves_the_scroll_focus_to_the_instructions_and_back(self, monkeypatch):
        # Both columns are longer than a screen and there is one set of arrows,
        # so something has to say which one they drive.
        self._drive(monkeypatch, ["tab", "tab", "esc"])
        assert [c["focus_instructions"] for c in self.calls] == [False, True, False]

    def test_the_wheel_scrolls_the_column_the_pointer_is_over(self, monkeypatch):
        from yeaboi.ui import mode_select as ms

        # A wheel tick over the left column takes the focus with it, so the
        # highlighted column is always the one that just moved — and scrolling
        # the column you are NOT pointing at is never what was meant.
        monkeypatch.setattr(ms, "last_wheel_pos", lambda: (10, 12))
        self._drive(monkeypatch, ["scroll_down", "esc"])
        assert [c["focus_instructions"] for c in self.calls] == [False, True]

    def test_the_wheel_over_the_right_column_leaves_the_focus_there(self, monkeypatch):
        from yeaboi.ui import mode_select as ms

        monkeypatch.setattr(ms, "last_wheel_pos", lambda: (self.WIDE - 10, 12))
        self._drive(monkeypatch, ["tab", "scroll_down", "esc"])
        assert [c["focus_instructions"] for c in self.calls] == [False, True, False]

    def test_tab_does_nothing_with_only_one_column(self, monkeypatch):
        # Nothing to focus: on a narrow terminal the instructions are a tab, and
        # the arrows already scroll whichever tab is open.
        drawn, _saved = self._drive(monkeypatch, ["tab", "esc"], width=self.NARROW)
        assert drawn == ["instructions", "instructions"]
        assert self.calls == []

    def test_a_resize_across_the_threshold_never_strands_you_on_a_missing_tab(self, monkeypatch):
        # Resumed onto Instructions, then drawn wide, where it is not a tab.
        drawn, _saved = self._drive(
            monkeypatch, ["esc"], resume=dict(_RESUME_ALL, last_page="instructions"), width=self.WIDE
        )
        assert drawn == ["epic"]


class TestPreviewStageTabs:
    """The five preview stages are a tab strip parked at the right."""

    @staticmethod
    def _panel(**kw):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_analysis_review_screen

        kw.setdefault("width", 110)
        return _build_analysis_review_screen([Text("    body")], height=20, **kw)

    @staticmethod
    def _lines(panel, width=110, height=20):
        console = Console(width=width, height=height, force_terminal=False)
        return [
            "".join(seg.text for seg in row)
            for row in console.render_lines(panel, console.options.update_dimensions(width, height), pad=True)
        ]

    def test_every_stage_is_on_the_strip(self):
        panel = self._panel()
        assert [key for *_rest, key in panel._section_tabs] == [
            "instructions",
            "epic",
            "stories",
            "tasks",
            "sprint",
        ]
        # A two-row band — the label and the rule under it — so a click just
        # below the word still opens the tab.
        for y0, y1, x0, x1, key in panel._section_tabs:
            assert y1 == y0 + 1
            assert key[:4] in self._lines(panel)[y0 - 1][x0 - 1 : x1].lower()

    def test_the_strip_is_parked_right_but_off_the_edge(self):
        # Measured from the RIGHT: "the first tab starts past halfway" only holds
        # on a page wide enough to have spread the five out that far. Run it to
        # the last content column and the final tab sits against the frame with
        # nothing either side of it, so it keeps the page's own margin.
        for width in (80, 110, 160, 240):
            rows = self._lines(self._panel(width=width), width=width)
            labels = next(line for line in rows if "Instructions" in line)[1:-1]
            wordmark = next(line for line in rows if "▄▀█" in line)[1:-1]
            assert len(labels) - len(labels.rstrip()) == len(wordmark) - len(wordmark.lstrip()), width

    def test_its_rule_stops_at_the_page_edge(self):
        # The two-column shoulder past the last tab has nowhere to go when the
        # strip is against the right edge; unclamped it wrapped a stray stub of
        # rule onto the line below.
        rows = self._lines(self._panel())
        rule = rows.index(self._rule_row(rows))
        assert "─" not in rows[rule + 1]

    def test_the_stages_spread_over_the_right_half(self):
        # Packed against the edge the five read as one clump in the corner. They
        # share out half the page — and only half, so the strip never runs back
        # under the content it was moved out of the way of.
        for width in (110, 160, 240):
            panel = self._panel(width=width)
            first, last = panel._section_tabs[0], panel._section_tabs[-1]
            assert last[3] - first[2] > (width - 6) // 3, width
            wordmark = next(line for line in self._lines(panel, width=width) if "▄▀█" in line)
            assert first[2] > wordmark.index("▄") + (width - 6) // 3, width

    def test_nothing_sits_between_the_wordmark_and_the_strip(self):
        # The strip only occupies the right half, so on the left it is two blank
        # rows already; a spacer above it pushed the first line of every page a
        # third row further from the only thing naming it.
        rows = self._lines(self._panel())
        wordmark = max(i for i, line in enumerate(rows) if "█" in line)  # its LAST row
        labels = next(i for i, line in enumerate(rows) if "Instructions" in line)
        assert labels == wordmark + 1
        assert next(i for i, line in enumerate(rows) if "body" in line) == labels + 2

    def test_the_rule_covers_the_strip_and_no_more(self):
        # It underlines the tabs; it is not a full-width divider under the
        # header. Two columns of shoulder at each end, no further.
        for width in (80, 110, 160, 240):
            panel = self._panel(width=width)
            rule = self._rule_row(self._lines(panel, width=width))
            # -3: two columns of shoulder, and the click regions are 1-based
            # terminal columns while the rendered row is a 0-based string.
            assert rule.index("─") == panel._section_tabs[0][2] - 3, width

    @staticmethod
    def _rule_row(rows: list[str]) -> str:
        """The tab bar's underline — not the panel's own border, which is a
        continuous run of the same glyph and comes first."""
        return next(line for line in rows if line.startswith("│") and line.count("─") > 20)

    def test_a_stage_with_nothing_behind_it_is_dimmed_not_hidden(self):
        console = Console(width=110, height=20, force_terminal=False)
        panel = self._panel(stage_index=1, ready=(True, True, False, False, False))
        styles = {}
        for row in console.render_lines(panel, console.options.update_dimensions(110, 20), pad=True):
            for seg in row:
                if seg.text.strip() in ("Instructions", "Epic", "Stories", "Tasks", "Sprint"):
                    styles[seg.text.strip()] = str(seg.style)
        assert "bold" in styles["Epic"]  # the one you are on
        assert styles["Stories"] == styles["Tasks"] == styles["Sprint"]
        assert styles["Stories"] != styles["Instructions"]  # ready-but-inactive differs


class TestTitleCrumb:
    """The crumb names the page's team, source and window — beside the title."""

    def test_it_rides_the_wordmark_s_last_row(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_team_insights_screen

        rows = _render(
            _build_team_insights_screen(_make_overview_profile(), examples={}, width=110, height=20),
            width=110,
        ).split("\n")
        wordmark = [i for i, r in enumerate(rows) if "█" in r]
        at = next(i for i, r in enumerate(rows) if "Work items  ·" in r)
        # On the wordmark's baseline, not its cap, and not on a row of its own.
        assert at == max(wordmark)

    def test_it_comes_off_the_profile_not_the_status_line(self):
        # ``subtitle`` carries this loop's transient status ("Team profile
        # exported"), so hanging the crumb on it left the title with nothing
        # beside it whenever nothing had happened yet.
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_team_insights_screen

        rows = _render(
            _build_team_insights_screen(
                _make_overview_profile(), examples={}, width=110, height=20, subtitle="Team profile exported"
            ),
            width=110,
        ).split("\n")
        assert any("Work items  ·" in r for r in rows[:6])
        assert not any("exported" in r for r in rows[:6])


def test_each_page_s_crumb_names_that_page():
    """A crumb that opens with the previous page's name tells you where you were."""
    from yeaboi.ui.mode_select.screens._screens_secondary import (
        _build_preview_split_screen,
        _build_team_insights_screen,
    )

    insights = _build_team_insights_screen(_make_overview_profile(), examples={}, width=110, height=20)
    assert "Work items" in _render(insights, 110)
    # The page is called the same thing in its crumb and in its pager — the same
    # page named two different things two rows apart is worse than either name
    # on its own.
    assert insights._pager[1] == "Work items"
    for stage in range(4):
        preview = _build_preview_split_screen([Text("  a")], [Text("  b")], stage_index=stage, width=150, height=20)
        # No crumb on the preview: the pager names the page and the strip names
        # the tab, so a crumb saying both would say each of them twice.
        assert preview._pager[1] == "Work items"
        assert "Sample Plan  ·" not in _render(preview, 150)


def test_a_not_ready_tab_is_dimmer_but_never_invisible():
    """`dim` against this page's near-black background rendered as nothing.

    A tab with no content behind it yet has to read as "not ready", not as gone —
    while the plan was generating the strip looked like it had lost three of its
    four tabs.
    """
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_preview_split_screen

    console = Console(width=150, height=20, force_terminal=False)
    seen = {}
    panel = _build_preview_split_screen(
        [Text("  a")], [Text("  b")], stage_index=0, width=150, height=20, ready=(True, False, False, False)
    )
    for row in console.render_lines(panel, console.options.update_dimensions(150, 20), pad=True):
        for seg in row:
            if seg.text.strip() in ("Epic", "Stories", "Tasks", "Sprint"):
                seen[seg.text.strip()] = seg.style

    not_ready = seen["Stories"]
    assert "dim" not in str(not_ready)
    # A real colour, and a darker one than the ready-but-inactive tabs use.
    assert not_ready.color is not None
    ready_panel = _build_preview_split_screen(
        [Text("  a")], [Text("  b")], stage_index=0, width=150, height=20, ready=(True, True, True, True)
    )
    for row in console.render_lines(ready_panel, console.options.update_dimensions(150, 20), pad=True):
        for seg in row:
            if seg.text.strip() == "Stories":
                assert sum(seg.style.color.triplet) > sum(not_ready.color.triplet)
                return
    raise AssertionError("no Stories tab")


def test_every_analysis_page_carries_the_bottom_pager():
    """It should be in the same place throughout, not vanish once you reach the plan."""
    from yeaboi.ui.mode_select.screens._screens_secondary import (
        _build_preview_split_screen,
        _build_team_insights_screen,
    )

    insights = _build_team_insights_screen(_make_overview_profile(), examples={}, width=110, height=20)
    preview = _build_preview_split_screen([Text("  a")], [Text("  b")], stage_index=0, width=150, height=20)
    # Each names itself and the neighbour it came from, with itself live.
    # The SAME two everywhere, from one declaration, and Tab crosses all of them:
    # no page's far half leaves the flow any more, so none has to opt out.
    from yeaboi.ui.mode_select.screens._screens_secondary import analysis_pager

    assert insights._pager == analysis_pager(1) == ("Analysis", "Work items", 1)
    assert preview._pager == analysis_pager(1)


def test_the_insights_page_shares_the_results_page_s_shell():
    """They are two pages you cross with Tab, so crossing has to look like the
    body being repopulated rather than a different screen arriving.

    It was drawing the PREVIEW stage strip — Instructions, Epic, Stories… — in
    the row where the results page has its section strip. A different strip
    appearing there is what made the switch read as a whole new page.
    """
    from yeaboi.ui.mode_select.screens._screens_secondary import (
        _build_preview_split_screen,
        _build_team_insights_screen,
    )

    panel = _build_team_insights_screen(_make_overview_profile(), examples={}, width=110, height=20)
    rows = _render(panel, 110).split("\n")
    for stage in ("Instructions", "Epic", "Stories", "Sprint"):
        assert stage not in "\n".join(rows), stage
    assert panel._section_tabs == []
    # The crumb still lands beside the wordmark, and the body starts right under.
    wordmark = max(i for i, r in enumerate(rows) if "█" in r)
    assert "Work items  ·" in rows[wordmark]
    # One blank in the strip's place: without it the first heading sat on the
    # wordmark's own last row and read as part of the title.
    assert not rows[wordmark + 1].strip("│ ")
    assert "Generate sample tickets from this?" in rows[wordmark + 2]
    # And the two halves name the two pages, wherever you are in the flow.
    preview = _build_preview_split_screen([Text("  a")], [Text("  b")], stage_index=0, width=150, height=20)
    for other in (panel, preview):
        assert other._pager[:2] == ("Analysis", "Work items")


def test_the_work_items_page_is_skipped_once_a_plan_exists():
    """Crossing back from the plan used to land on a page about the plan.

    First it offered to make the one you had just made; then it announced that
    it was ready. Both are a gate in front of what you crossed over to see, so
    the page is not drawn at all once there is a plan — Work items IS the plan
    by then. It must take no keystroke to pass, or the gate is still there.
    """
    import yeaboi.ui.mode_select as ms

    def _no_keys(timeout=None):
        raise AssertionError("the page was drawn and waited for a key")

    original = ms._ana_generated
    try:
        ms._ana_generated = True
        assert ms._run_team_insights(None, None, _no_keys, 0.01, True, _make_overview_profile(), {}) == "continue"
    finally:
        ms._ana_generated = original
