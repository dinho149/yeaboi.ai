"""Tests for src/yeaboi/sessions_recent.py — the cross-mode recent list."""

from __future__ import annotations

import sqlite3
from dataclasses import fields

import pytest

from yeaboi.agent.state import DeliveryReport, RetroReport, ShipRun, StandupReport, WeeklyReview
from yeaboi.sessions_recent import MODES, RecentSession, recent_sessions


@pytest.fixture
def db(tmp_path):
    return tmp_path / "sessions.db"


@pytest.fixture
def seeded(db):
    """One run per mode: the planning session ``p1`` is linked to a project,
    the analysis session and the retro belong to nothing."""
    from yeaboi.projects.engine import create_project
    from yeaboi.reporting.store import ReportingStore
    from yeaboi.retro.store import RetroStore
    from yeaboi.sessions import SessionStore
    from yeaboi.ship.store import ShipStore
    from yeaboi.solo.store import WeeklyReviewStore
    from yeaboi.standup.store import StandupStore

    pid = create_project("Apollo", db_path=db)["project_id"]
    with SessionStore(db) as store:
        store.create_session("p1", "Apollo", project_id=pid)
        store.create_session("a1", "Apollo", mode="analysis")
    with StandupStore(db) as store:
        store.record_run(StandupReport(session_id="p1", date="2026-09-01"))
    with RetroStore(db) as store:
        store.record_run(RetroReport(session_id="other", date="2026-08-30"))
    with ReportingStore(db) as store:
        store.record_run(DeliveryReport(period_label="Last week"), session_id="p1")
    with ShipStore(db) as store:
        store.record_run(ShipRun(run_id="r1", item_id="S-1", session_id="p1"))
    with WeeklyReviewStore(db) as store:
        store.record_run(WeeklyReview(session_id="p1", week_label="2026-W35"))
    return {"db": db, "pid": pid}


class TestUnion:
    def test_every_store_contributes(self, seeded):
        rows = recent_sessions(db_path=seeded["db"])
        assert {r.mode for r in rows} == set(MODES)
        assert all(isinstance(r, RecentSession) for r in rows)

    def test_row_shape_is_pinned(self):
        assert [f.name for f in fields(RecentSession)] == [
            "session_id",
            "run_id",
            "mode",
            "title",
            "created_at",
            "last_modified",
            "project_id",
        ]

    def test_titles_and_run_ids(self, seeded):
        by_mode = {r.mode: r for r in recent_sessions(db_path=seeded["db"])}
        assert by_mode["standup"].title == "Standup — 2026-09-01" and by_mode["standup"].run_id == "1"
        assert by_mode["retro"].title == "Retro — 2026-08-30"
        assert by_mode["reporting"].title == "Report — Last week"
        assert by_mode["ship"].title.startswith("Ship — S-1") and by_mode["ship"].run_id == "r1"
        assert by_mode["review"].title == "Week 2026-W35"
        assert by_mode["planning"].title.startswith("apollo-") and by_mode["planning"].run_id == ""
        assert by_mode["analysis"].session_id == "a1"

    def test_absent_modes_are_absent_not_invented(self, db):
        from yeaboi.sessions import SessionStore

        with SessionStore(db) as store:
            store.create_session("p1", "Apollo")
        rows = recent_sessions(db_path=db)
        assert [r.mode for r in rows] == ["planning"]

    def test_missing_database_is_empty(self, tmp_path):
        assert recent_sessions(db_path=tmp_path / "nope.db") == []


class TestOrdering:
    def test_newest_first(self, seeded):
        conn = sqlite3.connect(seeded["db"])
        conn.execute("UPDATE retro_history SET run_at = '2030-01-01T00:00:00+00:00'")
        conn.execute("UPDATE sessions_meta SET last_modified = '2000-01-01T00:00:00+00:00' WHERE session_id = 'a1'")
        conn.commit()
        conn.close()
        rows = recent_sessions(db_path=seeded["db"])
        assert rows[0].mode == "retro"
        assert rows[-1].mode == "analysis"

    def test_limit_caps_the_union(self, seeded):
        assert len(recent_sessions(limit=2, db_path=seeded["db"])) == 2

    def test_limit_zero_means_everything(self, seeded):
        assert len(recent_sessions(limit=0, db_path=seeded["db"])) == len(MODES)


class TestFilters:
    def test_project_scopes_every_store(self, seeded):
        rows = recent_sessions(project_id=seeded["pid"], db_path=seeded["db"])
        assert {r.mode for r in rows} == {"planning", "standup", "reporting", "ship", "review"}
        assert all(r.session_id == "p1" for r in rows)
        assert all(r.project_id == seeded["pid"] for r in rows)

    def test_unknown_project_is_empty_not_an_error(self, seeded):
        assert recent_sessions(project_id="proj-00000000", db_path=seeded["db"]) == []

    def test_mode_narrows_to_one_store(self, seeded):
        rows = recent_sessions(mode="standup", db_path=seeded["db"])
        assert [r.mode for r in rows] == ["standup"]

    def test_unknown_mode_is_a_value_error(self, seeded):
        with pytest.raises(ValueError, match="unknown mode"):
            recent_sessions(mode="poker", db_path=seeded["db"])

    def test_project_rows_carry_the_project_id_from_the_session_link(self, seeded):
        unscoped = {r.mode: r.project_id for r in recent_sessions(db_path=seeded["db"])}
        assert unscoped["standup"] == seeded["pid"]
        assert unscoped["retro"] == ""
        assert unscoped["analysis"] == ""


class TestResilience:
    def test_the_project_map_never_reads_the_state_blobs(self, seeded, monkeypatch):
        from yeaboi import sessions_recent
        from yeaboi.sessions import SessionStore

        def boom(self, **kw):
            raise AssertionError("list_sessions selects every session_state blob")

        monkeypatch.setattr(SessionStore, "list_sessions", boom)
        assert sessions_recent._project_ids_by_session(seeded["db"]) == {"p1": seeded["pid"], "a1": ""}

    def test_a_broken_store_is_skipped(self, seeded, monkeypatch):
        from yeaboi import sessions_recent

        def boom(*args):
            raise RuntimeError("table gone")

        monkeypatch.setitem(sessions_recent._ADAPTERS, "retro", boom)
        rows = recent_sessions(db_path=seeded["db"])
        assert "retro" not in {r.mode for r in rows}
        assert "standup" in {r.mode for r in rows}
