"""Tests for ship's cross-store plan source (ship/plans.py).

The two stores are stubbed: the interactive project store via monkeypatched
``persistence`` functions, the SQLite side via a real temporary SessionStore.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from yeaboi.agent.state import UserStory
from yeaboi.sessions import SessionStore
from yeaboi.ship import plans


def _story(sid: str = "US-1") -> UserStory:
    return UserStory(
        id=sid,
        feature_id="F1",
        persona="dev",
        goal="a limiter",
        benefit="safety",
        acceptance_criteria=[],
        story_points=3,
        priority="high",
        title="Add a limiter",
    )


@pytest.fixture(autouse=True)
def _isolate_project_store(monkeypatch):
    # Default: no interactive projects and no project state, so a test that does
    # not set them up never reads the real ~/.yeaboi store.
    monkeypatch.setattr("yeaboi.persistence.load_projects", lambda: [])
    monkeypatch.setattr("yeaboi.persistence.load_graph_state", lambda _id: None)


def _session_db(tmp_path, sid, state):
    db = tmp_path / "sessions.db"
    with SessionStore(db) as store:
        store.create_session(sid, "Proj")
        store.save_state(sid, {**state, "messages": []})
    return db


class TestLatestPlan:
    def test_prefers_the_interactive_project_store(self, tmp_path, monkeypatch):
        proj = SimpleNamespace(id="proj-1", name="Todo App", story_count=1)
        monkeypatch.setattr("yeaboi.persistence.load_projects", lambda: [proj])
        monkeypatch.setattr(
            "yeaboi.persistence.load_graph_state",
            lambda _id: {"stories": [_story("US-P")]} if _id == "proj-1" else None,
        )
        stories, pid, name = plans.latest_plan_with_stories(db_path=tmp_path / "none.db")
        assert pid == "proj-1"
        assert name == "Todo App"
        assert stories[0].id == "US-P"

    def test_falls_back_to_the_session_store(self, tmp_path):
        db = _session_db(tmp_path, "new-abc-2026", {"stories": [_story("US-S")], "project_name": "Legacy"})
        picked = plans.latest_plan_with_stories(db_path=db)
        assert picked is not None
        stories, sid, name = picked
        assert sid == "new-abc-2026"
        assert stories[0].id == "US-S"
        assert name == "Legacy"

    def test_none_when_no_store_has_stories(self, tmp_path):
        db = _session_db(tmp_path, "new-empty", {"questionnaire": None})  # intake-only shape
        assert plans.latest_plan_with_stories(db_path=db) is None

    def test_a_project_with_zero_story_count_is_skipped(self, tmp_path, monkeypatch):
        proj = SimpleNamespace(id="p0", name="Intake only", story_count=0)
        monkeypatch.setattr("yeaboi.persistence.load_projects", lambda: [proj])
        db = _session_db(tmp_path, "new-s", {"stories": [_story("US-S")]})
        # Skips the zero-story project, falls through to the session with stories.
        _, sid, _ = plans.latest_plan_with_stories(db_path=db)
        assert sid == "new-s"


class TestLoadPlanState:
    def test_resolves_a_project_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.persistence.load_graph_state",
            lambda _id: {"stories": [_story("US-P")], "tasks": [1, 2]} if _id == "proj-9" else None,
        )
        state = plans.load_plan_state("proj-9", db_path=tmp_path / "none.db")
        assert len(state["stories"]) == 1
        assert len(state["tasks"]) == 2

    def test_resolves_a_session_id(self, tmp_path):
        db = _session_db(tmp_path, "new-xyz", {"stories": [_story("US-S")]})
        state = plans.load_plan_state("new-xyz", db_path=db)
        assert state["stories"][0].id == "US-S"

    def test_empty_identifier_is_none(self, tmp_path):
        assert plans.load_plan_state("", db_path=tmp_path / "none.db") is None

    def test_a_broken_store_never_raises(self, tmp_path, monkeypatch):
        def _boom(_id):
            raise RuntimeError("store on fire")

        monkeypatch.setattr("yeaboi.persistence.load_graph_state", _boom)
        # Falls through to the (absent) session store and returns None, not a raise.
        assert plans.load_plan_state("whatever", db_path=tmp_path / "none.db") is None


class TestEngineUsesBothStores:
    def test_load_story_reads_a_project_plan(self, tmp_path, monkeypatch):
        from yeaboi.ship import engine

        monkeypatch.setattr(
            "yeaboi.persistence.load_graph_state",
            lambda _id: {"stories": [_story("US-E")], "tasks": []} if _id == "proj-e" else None,
        )
        story, tasks, resolved = engine._load_story("proj-e", "US-E", tmp_path / "none.db")
        assert story.id == "US-E"
        assert resolved == "proj-e"
