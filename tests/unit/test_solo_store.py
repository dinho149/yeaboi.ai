"""Tests for the Weekly Review store (solo/store.py)."""

from __future__ import annotations

import json
import sqlite3

import pytest

from yeaboi.agent.state import DeliveredItem, ReviewAction, WeeklyReview
from yeaboi.solo.store import WeeklyReviewStore, _dict_to_weekly_review, _review_to_json


def _review(week="2026-W36", session_id="s1", project_id="proj-1", **kw) -> WeeklyReview:
    base = dict(
        week_label=week,
        week_start="2026-08-31",
        week_end="2026-09-04",
        project_id=project_id,
        project_name="Apollo",
        session_id=session_id,
        my_name="Dinho",
        standup_dates=("2026-08-31", "2026-09-01"),
        standup_lines=("Mon: closed S-1", "Tue: started S-2 — blocked: keys"),
        confidence_start=60,
        confidence_end=72,
        confidence_label="On track",
        sprint_name="Sprint 1",
        sprint_day=2,
        sprint_total_days=5,
        delivered_items=(DeliveredItem(key="S-1", title="Login", status="Done", source="jira", assignee="Dinho"),),
        planned_story_count=2,
        plan_status="on_track",
        plan_line="Day 2/5 …",
        summary="A good week.",
        went_well=("Shipped S-1",),
        to_change=("Ask for keys earlier",),
        actions=(ReviewAction(id="a1", text="Split S-2", week_label=week),),
        carried_actions=(
            ReviewAction(id="c1", text="Write docs", status="done", origin="carryover", week_label="2026-W35"),
        ),
        warnings=("w",),
        generated_at="2026-09-04T17:00:00+00:00",
    )
    base.update(kw)
    return WeeklyReview(**base)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "sessions.db"


class TestSerialisation:
    def test_round_trip_keeps_types(self):
        original = _review()
        restored = _dict_to_weekly_review(json.loads(_review_to_json(original)))
        assert restored == original
        assert isinstance(restored.actions[0], ReviewAction)
        assert isinstance(restored.delivered_items[0], DeliveredItem)
        assert isinstance(restored.standup_lines, tuple)

    def test_missing_keys_backfill(self):
        restored = _dict_to_weekly_review({"week_label": "2026-W36"})
        assert restored.week_label == "2026-W36"
        assert restored.actions == () and restored.carried_actions == ()
        assert restored.plan_status == "" and restored.sprint_day == 0

    def test_bad_action_rows_are_skipped(self):
        restored = _dict_to_weekly_review({"actions": ["not a dict", {"id": "x", "text": "ok"}]})
        assert [a.id for a in restored.actions] == ["x"]
        assert restored.actions[0].status == "pending"


class TestStore:
    def test_schema_created_on_open(self, db):
        with WeeklyReviewStore(db):
            pass
        names = {r[0] for r in sqlite3.connect(str(db)).execute("SELECT name FROM sqlite_master")}
        assert "weekly_review_history" in names

    def test_record_and_reload(self, db):
        with WeeklyReviewStore(db) as store:
            run_id = store.record_run(_review())
            assert run_id > 0
            assert store.get_run_by_id(run_id) == _review()
            assert store.get_latest_report() == _review()

    def test_history_rows_carry_metadata(self, db):
        with WeeklyReviewStore(db) as store:
            store.record_run(_review())
            (row,) = store.get_history("s1")
        assert row["week_label"] == "2026-W36"
        assert row["project_name"] == "Apollo"
        assert row["action_count"] == 1
        assert row["project_id"] == "proj-1"

    def test_newest_first(self, db):
        with WeeklyReviewStore(db) as store:
            store.record_run(_review(week="2026-W35"))
            store.record_run(_review(week="2026-W36"))
            assert store.get_latest_report().week_label == "2026-W36"
            assert [r.week_label for r in store.get_recent_reports()] == ["2026-W36", "2026-W35"]
            assert [r["week_label"] for r in store.get_all_history()] == ["2026-W36", "2026-W35"]

    def test_session_ids_is_a_hard_filter(self, db):
        with WeeklyReviewStore(db) as store:
            store.record_run(_review(session_id="s1"))
            store.record_run(_review(session_id="s2", week="2026-W37"))
            assert store.get_latest_report(session_ids=("s1",)).session_id == "s1"
            assert store.get_latest_report(session_ids=()) is None
            assert store.get_recent_reports(session_ids=()) == []
            assert store.get_all_history(session_ids=("s2",))[0]["session_id"] == "s2"
            assert store.get_latest_report(session_ids=None).session_id == "s2"

    def test_delete(self, db):
        with WeeklyReviewStore(db) as store:
            run_id = store.record_run(_review())
            assert store.delete_run(run_id) is True
            assert store.delete_run(run_id) is False
            assert store.get_run_by_id(run_id) is None

    def test_corrupt_row_reads_as_none(self, db):
        with WeeklyReviewStore(db) as store:
            run_id = store.record_run(_review())
            store._conn.execute("UPDATE weekly_review_history SET report_json = '{not json' WHERE id = ?", (run_id,))
            assert store.get_run_by_id(run_id) is None
            assert store.get_recent_reports() == []
