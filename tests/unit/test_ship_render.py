"""Tests for the ship CLI text renderers (ship/render.py).

Rendered through a real Console; the assertions are that the load-bearing
facts appear and that legitimately-empty store values (a blank created_at, a
run with nothing but a status) render instead of crashing.
"""

from __future__ import annotations

import io

from rich.console import Console

from yeaboi.agent.state import ShipRun, ShipValidation
from yeaboi.ship.budget import BudgetStatus
from yeaboi.ship.render import format_budget_rich, format_history_rich, format_run_rich


def _render(renderable, width: int = 120) -> str:
    console = Console(file=io.StringIO(), width=width)
    console.print(renderable)
    return console.file.getvalue()


class TestFormatRun:
    def test_full_run_shows_the_gate_facts(self):
        run = ShipRun(
            run_id="run-1",
            story_id="US-001",
            branch="ship/run-1",
            status="awaiting_approval",
            diff_stat="src/app.py | 10 ++\n2 files changed",
            validation=ShipValidation(configured=True, command="make test", passed=True, exit_code=0),
            cost_usd=0.42,
            transcript_findings=(("secret", "critical", "api key"),),
            pr_url="",
            warnings=("one warning",),
        )
        out = _render(format_run_rich(run))
        assert "US-001" in out
        assert "ship/run-1" in out
        assert "2 files changed" in out
        assert "make test — passed" in out
        assert "$0.42" in out
        assert "critical: api key" in out
        assert "⚠ one warning" in out

    def test_no_validation_is_a_visible_warning(self):
        out = _render(format_run_rich(ShipRun(run_id="r", story_id="US-1", status="failed")))
        assert "nothing was proven" in out

    def test_empty_run_renders_without_crashing(self):
        out = _render(format_run_rich(ShipRun()))
        assert "(no story)" in out

    def test_the_gate_rendering_carries_the_patch_and_the_worktree(self):
        # show_diff is what the CLI gate passes: approving a push on a file
        # count is not review.
        run = ShipRun(
            run_id="run-1",
            story_id="US-001",
            worktree="/tmp/wt/run-1",
            diff_stat="src/app.py | 2 +-\n1 file changed",
            diff_text="@@ -1 +1 @@\n-old = 1\n+new = 2\n",
        )
        out = _render(format_run_rich(run, show_diff=True))
        assert "+new = 2" in out
        assert "-old = 1" in out
        assert "/tmp/wt/run-1" in out
        assert "src/app.py" in out  # the whole stat, not only its last line

    def test_the_summary_rendering_stays_a_summary(self):
        run = ShipRun(run_id="r", story_id="US-1", diff_text="@@ -1 +1 @@\n+leaked into the summary\n")
        assert "leaked into the summary" not in _render(format_run_rich(run))

    def test_an_unreadable_patch_is_a_warning_at_the_gate(self):
        run = ShipRun(run_id="r", story_id="US-1", diff_stat="1 file changed", diff_text="")
        out = _render(format_run_rich(run, show_diff=True))
        assert "could not be read" in out


class TestFormatHistory:
    def test_empty_history_names_the_next_step(self):
        out = _render(format_history_rich([]))
        assert "yeaboi ship run" in out

    def test_rows_carry_story_status_and_pr(self):
        runs = [
            ShipRun(run_id="r2", story_id="US-002", status="approved", created_at="2026-08-17T10:00:00", pr_url="u"),
            ShipRun(run_id="r1", story_id="US-001", status="failed"),  # created_at legitimately empty
        ]
        out = _render(format_history_rich(runs))
        assert "US-002" in out
        assert "approved" in out
        assert "US-001" in out
        assert "failed" in out


class TestFormatBudget:
    def test_counts_and_open_circuit(self):
        status = BudgetStatus(
            active=1,
            launched_last_hour=2,
            launched_last_day=5,
            paused_until=999.0,
            paused_reason="HTTP 429",
        )
        out = _render(format_budget_rich(status))
        assert "active 1/1" in out
        assert "last hour 2/2" in out
        assert "circuit OPEN: HTTP 429" in out

    def test_quiet_budget_has_no_circuit_line(self):
        out = _render(format_budget_rich(BudgetStatus()))
        assert "circuit" not in out
