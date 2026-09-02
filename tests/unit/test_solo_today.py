"""Tests for the Solo welcome's Today snapshot (solo/today.py)."""

from __future__ import annotations

from dataclasses import fields
from datetime import date

import pytest

from yeaboi.agent.state import MemberUpdate, Sprint, StandupReport, UserStory
from yeaboi.agentwatch.store import AgentWatchStore
from yeaboi.projects.store import ProjectStore
from yeaboi.sessions import SessionStore
from yeaboi.solo.today import TodaySnapshot, build_today_snapshot
from yeaboi.standup.store import StandupStore

TODAY = date(2026, 9, 2)  # a Wednesday; Monday is the 31st


@pytest.fixture(autouse=True)
def _no_interactive_projects(monkeypatch):
    # latest_plan_with_work scans the interactive project store first; keep the
    # tests on the SQLite plan they seed.
    monkeypatch.setattr("yeaboi.persistence.load_projects", lambda: [])


def _story(sid: str, title: str) -> UserStory:
    from yeaboi.agent.state import Priority, StoryPointValue

    return UserStory(
        id=sid,
        feature_id="F-1",
        persona="dev",
        goal="ship it",
        benefit="",
        acceptance_criteria=(),
        story_points=StoryPointValue.THREE,
        priority=Priority.HIGH,
        title=title,
    )


def _plan_state(*, start: str = "2026-08-31") -> dict:
    return {
        "project_name": "Apollo",
        "sprint_start_date": start,
        "sprint_length_weeks": 1,
        "stories": [_story("S-1", "Wire the login form"), _story("S-2", "Add the audit log")],
        "sprints": [
            Sprint(id="SP-1", name="Sprint 1", goal="", capacity_points=8, story_ids=("S-1",)),
            Sprint(id="SP-2", name="Sprint 2", goal="", capacity_points=8, story_ids=("S-2",)),
        ],
    }


def _report(session_id: str, *, my_name: str = "Dinho") -> StandupReport:
    return StandupReport(
        date="2026-09-01",
        session_id=session_id,
        sprint_name="Sprint 1",
        sprint_day=2,
        sprint_total_days=5,
        confidence_pct=72,
        confidence_label="On track",
        confidence_trend="improving",
        my_name=my_name,
        member_updates=(
            MemberUpdate(name="Someone Else", summary="Reviewed PRs"),
            MemberUpdate(name=my_name, summary="Closed S-0; started S-1", blockers="waiting on API keys"),
        ),
    )


def _seed(tmp_path, *, with_project: bool = True):
    db = tmp_path / "sessions.db"
    pid = ""
    with ProjectStore(db) as projects:
        if with_project:
            pid = projects.create("Apollo")["project_id"]
    with SessionStore(db) as sessions:
        sessions.create_session("plan-1", "Apollo", project_id=pid)
        sessions.save_state("plan-1", _plan_state())
    with StandupStore(db) as standups:
        standups.record_run(_report("plan-1"))
    with AgentWatchStore(db) as agents:
        for i, ended in enumerate(
            ("2026-08-31T09:00:00+00:00", "2026-09-02T09:00:00+00:00", "2026-08-28T09:00:00+00:00")
        ):
            agents.upsert_session(
                f"s{i}",
                source="claude-code",
                source_path=f"/x/{i}.jsonl",
                project_path="/x",
                git_branch="main",
                cli_version="1",
                started_at=ended,
                ended_at=ended,
                turns=3,
                model_usage={"claude-sonnet-4-5": {"input": 1000, "output": 200}},
                tool_counts={},
            )
    return db, pid


class TestEmptyStates:
    def test_missing_db_is_an_empty_snapshot(self, tmp_path):
        snap = build_today_snapshot(db_path=tmp_path / "sessions.db", today=TODAY)
        assert snap == TodaySnapshot()
        assert not (tmp_path / "sessions.db").exists()

    def test_fresh_db_is_empty_without_warnings(self, tmp_path):
        db = tmp_path / "sessions.db"
        with SessionStore(db):
            pass
        snap = build_today_snapshot(db_path=db, today=TODAY)
        assert snap.standup_date == "" and snap.next_story_id == "" and snap.spend_sessions == 0
        assert snap.warnings == ()

    def test_every_field_is_defaulted(self):
        # The wire serialises the dataclass verbatim; a required field would
        # make the honest empty state unrepresentable.
        assert all(f.default is not f.default_factory for f in fields(TodaySnapshot))
        assert TodaySnapshot().warnings == ()


class TestSeeded:
    def test_reads_the_standup_plan_and_spend(self, tmp_path):
        db, pid = _seed(tmp_path)
        snap = build_today_snapshot(project_id=pid, db_path=db, today=TODAY)
        assert snap.project_name == "Apollo"
        assert snap.standup_date == "2026-09-01"
        # The user's own card, not the first member's.
        assert snap.standup_summary == "Closed S-0; started S-1"
        assert snap.standup_blockers == "waiting on API keys"
        assert (snap.sprint_day, snap.sprint_total_days) == (2, 5)
        assert (snap.confidence_pct, snap.confidence_label, snap.confidence_trend) == (72, "On track", "improving")
        # Wednesday the 2nd falls in the plan's first one-week sprint.
        assert (snap.next_story_id, snap.next_story_title, snap.next_sprint_name) == (
            "S-1",
            "Wire the login form",
            "Sprint 1",
        )
        assert snap.plan_session_id == "plan-1" and snap.plan_scoped is True
        # Two of the three agent sessions ended on or after Monday the 31st.
        assert snap.spend_sessions == 2 and snap.spend_usd > 0 and snap.spend_known is True
        assert snap.warnings == ()

    def test_the_current_sprint_follows_the_date(self, tmp_path):
        db, pid = _seed(tmp_path)
        snap = build_today_snapshot(project_id=pid, db_path=db, today=date(2026, 9, 9))
        assert (snap.next_story_id, snap.next_sprint_name) == ("S-2", "Sprint 2")

    def test_unscoped_falls_back_to_the_newest_plan(self, tmp_path):
        db, _ = _seed(tmp_path, with_project=False)
        snap = build_today_snapshot(db_path=db, today=TODAY)
        assert snap.next_story_id == "S-1" and snap.plan_scoped is False
        assert snap.standup_date == "2026-09-01"

    def test_a_scoped_project_with_no_runs_reads_nothing(self, tmp_path):
        db, _ = _seed(tmp_path)
        with ProjectStore(db) as projects:
            other = projects.create("Zephyr")["project_id"]
        snap = build_today_snapshot(project_id=other, db_path=db, today=TODAY)
        assert snap.project_name == "Zephyr"
        assert snap.standup_date == "" and snap.next_story_id == ""
        assert snap.warnings == ()

    def test_a_failed_standup_run_is_skipped(self, tmp_path):
        db, pid = _seed(tmp_path)
        with StandupStore(db) as standups:
            standups.record_run(_report("plan-1").__class__(date="2026-09-02", session_id="plan-1"), status="failed")
        snap = build_today_snapshot(project_id=pid, db_path=db, today=TODAY)
        assert snap.standup_date == "2026-09-01"


class TestNeverRaises:
    def test_a_broken_source_becomes_a_warning(self, tmp_path, monkeypatch):
        db, pid = _seed(tmp_path)

        def boom(*a, **k):
            raise RuntimeError("locked")

        monkeypatch.setattr("yeaboi.standup.store.StandupStore.get_all_history", boom)
        snap = build_today_snapshot(project_id=pid, db_path=db, today=TODAY)
        assert snap.standup_date == ""
        assert "could not read the latest standup" in snap.warnings
        # The other sources still answered.
        assert snap.next_story_id == "S-1" and snap.spend_sessions == 2

    def test_a_directory_for_a_db_never_raises(self, tmp_path):
        snap = build_today_snapshot(db_path=tmp_path, today=TODAY)
        assert isinstance(snap, TodaySnapshot)
