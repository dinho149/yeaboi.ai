"""Tests for apply_size_switch() — the bidirectional mid-chat size switch.

apply_epic_switch() (Small → Large) keeps its own regression coverage in
test_small_project.py; these tests pin the generalization: delegation,
the new Large → Small direction, and answer preservation across a round trip.
"""

import pytest

from yeaboi.agent.nodes import apply_epic_switch, apply_size_switch
from yeaboi.agent.state import QuestionnaireState


def _large_state() -> dict:
    """A mid-session Large state with capacity machinery populated."""
    qs = QuestionnaireState(intake_mode="smart", completed=True)
    qs.answers = {2: "Greenfield", 3: "solve X", 6: "3 engineers", 27: "Sprint 4"}
    qs._planned_leave_entries = [{"person": "Sam", "start_date": "2026-08-10", "end_date": "2026-08-14"}]
    qs._velocity_override = 25
    qs._detected_bank_holiday_days = 2
    qs._detected_bank_holidays = [{"date": "2026-08-31", "name": "Bank holiday"}]
    return {
        "_intake_mode": "smart",
        "questionnaire": qs,
        "project_analysis": object(),
        "features": ["f"],
        "stories": ["s"],
        "tasks": ["t"],
        "sprints": ["sp"],
        "pending_review": "sprint_planner",
        "capacity_bank_holiday_days": 2,
        "capacity_planned_leave_days": 5,
        "net_velocity_per_sprint": 18,
        "velocity_source": "jira",
        "sprint_start_date": "2026-08-17",
        "sprint_capacities": [{"sprint": 1, "capacity": 18}],
        "planned_leave_entries": [{"person": "Sam"}],
    }


class TestApplySizeSwitch:
    def test_epic_switch_delegates_to_size_switch(self):
        state = _large_state()
        state["questionnaire"].intake_mode = "small_project"
        state["_intake_mode"] = "small_project"
        apply_epic_switch(state)
        assert state["_intake_mode"] == "smart"
        assert state["questionnaire"].intake_mode == "smart"
        assert state["questionnaire"]._reopen_for_epic is True

    def test_large_to_small_clears_capacity_state(self):
        state = _large_state()
        apply_size_switch(state, "small_project")
        qs = state["questionnaire"]
        assert qs.intake_mode == "small_project"
        assert state["_intake_mode"] == "small_project"
        assert qs.completed is False
        assert qs._reopen_for_epic is True
        # Smart-only capacity transients gone — stale values must not leak
        # into the leaner plan (small mode never asks these).
        assert qs._planned_leave_entries == []
        assert qs._velocity_override is None
        assert qs._awaiting_leave_input is False
        assert qs._detected_bank_holiday_days == 0
        assert qs._detected_bank_holidays == []
        for key in (
            "capacity_bank_holiday_days",
            "capacity_planned_leave_days",
            "net_velocity_per_sprint",
            "velocity_source",
            "sprint_start_date",
            "sprint_capacities",
            "planned_leave_entries",
        ):
            assert key not in state, key

    def test_small_to_large_keeps_capacity_transients(self):
        # Switching UP must not throw away PTO/velocity data already gathered.
        state = _large_state()
        state["questionnaire"].intake_mode = "small_project"
        state["_intake_mode"] = "small_project"
        apply_size_switch(state, "smart")
        qs = state["questionnaire"]
        assert qs._planned_leave_entries  # untouched
        assert qs._velocity_override == 25
        assert "capacity_bank_holiday_days" in state

    def test_artifacts_cleared_both_directions(self):
        for target in ("smart", "small_project"):
            state = _large_state()
            apply_size_switch(state, target)
            for key in ("project_analysis", "features", "stories", "tasks", "sprints", "pending_review"):
                assert key not in state, f"{target}: {key}"

    def test_round_trip_preserves_answers(self):
        state = _large_state()
        original_answers = dict(state["questionnaire"].answers)
        apply_size_switch(state, "small_project")
        apply_size_switch(state, "smart")
        assert state["questionnaire"].answers == original_answers

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError, match="quick"):
            apply_size_switch(_large_state(), "quick")

    def test_missing_questionnaire_is_tolerated(self):
        # Pre-intake /large: only the mode key exists yet.
        state = {"_intake_mode": "small_project"}
        apply_size_switch(state, "smart")
        assert state["_intake_mode"] == "smart"

    def test_prior_art_resets_in_both_directions(self):
        """The guard skips a stage that is already open, but the
        confirmation-gate handler still claims the turn — so a switch made
        mid-loop would eat the user's first reply at the new summary and answer
        a card about a repository they can no longer see."""
        for target in ("small_project", "smart"):
            state = _large_state()
            state["_intake_mode"] = "smart" if target == "small_project" else "small_project"
            qs = state["questionnaire"]
            qs._prior_art_stage = "ask"
            qs._prior_art_candidates = [{"key": "github:acme/auth", "name": "acme/auth"}]
            qs._prior_art_index = 0
            qs._prior_art_accepted = [{"key": "github:acme/auth"}]
            qs._prior_art_rejected = [{"key": "github:acme/pay"}]
            apply_size_switch(state, target)
            assert qs._prior_art_stage == "", target
            assert qs._prior_art_candidates == [], target
            assert qs._prior_art_accepted == [], target
            assert qs._prior_art_rejected == [], target
