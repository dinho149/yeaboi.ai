"""Render tests for the Ship mode screens (_screens_ship.py).

Every ``_build_*_screen`` renders through a real Console — the assertion is
that the load-bearing facts (story ids, validation verdicts, findings, the
gate's warning states) are actually visible, and that masking rules hold
(the diff stat and validation tail are capped, never dumped whole).
"""

from __future__ import annotations

import io

from rich.console import Console

from yeaboi.agent.state import (
    AcceptanceCriterion,
    Priority,
    ShipPhase,
    ShipRun,
    ShipValidation,
    StoryPointValue,
    UserStory,
)
from yeaboi.ui.mode_select.screens._screens_ship import (
    _build_ship_gate_screen,
    _build_ship_pick_screen,
    _build_ship_progress_screen,
    _build_ship_result_screen,
)


def _render(panel, width: int = 100) -> str:
    console = Console(file=io.StringIO(), width=width, height=40)
    console.print(panel)
    return console.file.getvalue()


def _story(story_id="US-001", title="Ship pipeline"):
    return UserStory(
        id=story_id,
        feature_id="F-1",
        persona="dev",
        goal="ship",
        benefit="speed",
        acceptance_criteria=(AcceptanceCriterion(given="g", when="w", then="t"),),
        story_points=StoryPointValue.THREE,
        priority=Priority.HIGH,
        title=title,
    )


class TestPickScreen:
    def test_lists_stories_with_points_and_fields(self):
        out = _render(
            _build_ship_pick_screen(
                [_story(), _story("US-002", "Second story")],
                0,
                repo="/home/dev/proj",
                check_command="make test",
            )
        )
        assert "US-001" in out
        assert "Ship pipeline" in out
        assert "3 pts" in out
        assert "/home/dev/proj" in out
        assert "make test" in out

    def test_empty_plan_names_the_next_step(self):
        out = _render(_build_ship_pick_screen([], 0, repo="/p", check_command=""))
        assert "No stories found" in out
        assert "Planning" in out

    def test_story_overflow_is_counted_not_dumped(self):
        stories = [_story(f"US-{n:03d}") for n in range(12)]
        out = _render(_build_ship_pick_screen(stories, 0, repo="/p", check_command=""))
        assert "US-007" in out
        assert "US-011" not in out
        assert "and 4 more" in out

    def test_the_window_follows_a_late_selection(self):
        # Story 9+ must be reachable, not just counted — the window slides.
        stories = [_story(f"US-{n:03d}") for n in range(12)]
        out = _render(_build_ship_pick_screen(stories, 11, repo="/p", check_command=""))
        assert "US-011" in out
        assert "▸ US-011" in out
        assert "earlier" in out

    def test_edit_mode_shows_the_live_buffer(self):
        out = _render(
            _build_ship_pick_screen(
                [_story()], 0, repo="/old", check_command="", edit_field="repo", edit_buf="/typed/so/far"
            )
        )
        assert "/typed/so/far" in out


class TestProgressScreen:
    def test_checklist_shows_pending_and_running_phases(self):
        events = [
            {
                "kind": "analysis_component",
                "component_id": "ship-setup",
                "label": "Preparing isolated worktree",
                "status": "completed",
                "detail": "",
            },
            {
                "kind": "analysis_component",
                "component_id": "ship-implement",
                "label": "Implementing",
                "status": "running",
                "detail": "",
            },
        ]
        out = _render(_build_ship_progress_screen(events, tick=3.0))
        assert "Prepare isolated worktree" in out
        assert "Run the coding agent" in out
        assert "Await your approval" in out  # pending rows render too
        assert "esc cancels the run" in out


class TestGateScreen:
    def _run(self, **overrides):
        base = ShipRun(
            run_id="run-1",
            story_id="US-001",
            branch="ship/run-1",
            status="awaiting_approval",
            diff_stat="src/app.py | 10 ++++\n2 files changed, 12 insertions(+)",
            validation=ShipValidation(configured=True, command="make test", passed=True, exit_code=0),
            cost_usd=0.42,
        )
        return ShipRun(**{**base.__dict__, **overrides})

    def test_shows_diff_validation_and_cost(self):
        out = _render(_build_ship_gate_screen(self._run()))
        assert "US-001" in out
        assert "ship/run-1" in out
        assert "2 files changed" in out
        assert "make test" in out
        assert "passed" in out
        assert "$0.42" in out

    def test_failed_validation_is_loud_with_its_tail(self):
        run = self._run(
            validation=ShipValidation(
                configured=True, command="make test", passed=False, exit_code=2, output_tail="FAILED test_x"
            )
        )
        out = _render(_build_ship_gate_screen(run))
        assert "FAILED" in out
        assert "FAILED test_x" in out

    def test_no_validation_is_a_visible_warning_not_silence(self):
        run = self._run(validation=ShipValidation())
        out = _render(_build_ship_gate_screen(run))
        assert "nothing was proven" in out

    def test_transcript_findings_surface_as_labels_only(self):
        run = self._run(transcript_findings=(("secret", "critical", "anthropic api key"),))
        out = _render(_build_ship_gate_screen(run))
        assert "anthropic api key" in out
        assert "1 transcript finding" in out

    def test_rejection_comment_editor_renders(self):
        out = _render(_build_ship_gate_screen(self._run(), comment_edit="wrong file"))
        assert "Why reject?" in out
        assert "wrong file" in out


class TestResultScreen:
    def test_approved_run_shows_pr_and_phases(self):
        run = ShipRun(
            run_id="run-1",
            story_id="US-001",
            branch="ship/run-1",
            status="approved",
            pr_url="https://github.com/o/r/pull/7",
            cost_usd=1.2,
            phases=(
                ShipPhase(name="setup", status="completed", duration_s=2.0),
                ShipPhase(name="implement", status="completed", duration_s=120.0),
            ),
        )
        out = _render(_build_ship_result_screen(run))
        assert "Shipped" in out
        assert "github.com/o/r/pull/7" in out
        assert "implement" in out
        assert "$1.20" in out

    def test_failed_run_carries_its_warnings(self):
        run = ShipRun(run_id="run-1", status="failed", warnings=("the agent produced no changes",))
        out = _render(_build_ship_result_screen(run))
        assert "Run failed" in out
        assert "produced no changes" in out
