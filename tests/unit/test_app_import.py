"""Importing a plan out of the TUI store and into the app.

The property under test is that this *copies*. The TUI keeps working offline
with its own whole-file JSON, the app keeps its SQLite, and neither becomes a
dependency of the other — which is why an import is a snapshot rather than a
migration, and why re-importing files a second artifact instead of mutating the
first.
"""

from __future__ import annotations

import json

import pytest

from tests._app import call, sign_in
from yeaboi.app.importer import import_plan
from yeaboi.app.server import AppServer
from yeaboi.app.store import AppStore


@pytest.fixture
def store(tmp_path):
    return AppStore(tmp_path / "app.db")


@pytest.fixture
def graph_state():
    """A real graph state — dataclasses, as the pipeline produces them.

    Built from `yeaboi.agent.state` rather than dicts because that is what
    `plan_export_args` reads; a dict fixture passes nothing but itself.
    """
    from yeaboi.agent.state import Discipline, Priority, StoryPointValue, UserStory

    return {
        "project_description": "Take money",
        "stories": [
            UserStory(
                id="S-1",
                feature_id="F-1",
                persona="user",
                goal="pay",
                benefit="value",
                acceptance_criteria=(),
                story_points=StoryPointValue(5),
                priority=Priority.HIGH,
                title="Checkout",
                discipline=Discipline.BACKEND,
            )
        ],
        "features": [],
    }


@pytest.fixture
def tui(monkeypatch, graph_state):
    """Stand in for the TUI's file store, without writing to ~/.yeaboi."""
    import yeaboi.app.importer as importer

    monkeypatch.setattr(importer, "load_graph_state", lambda pid: graph_state if pid == "tui_1" else None)
    return importer


class TestImportPlan:
    def test_creates_a_project_and_an_artifact(self, store, tui):
        user = store.create_user("ada@example.com")
        result = import_plan(store, user.id, "tui_1")
        assert result is not None
        project, artifact_id = result
        assert store.artifact(artifact_id, user.id).payload["kind"] == "plan"
        assert [p.id for p in store.projects_for(user.id)] == [project.id]

    def test_can_import_into_an_existing_project(self, store, tui):
        user = store.create_user("ada@example.com")
        existing = store.create_project("Existing", user.id)
        result = import_plan(store, user.id, "tui_1", into_project_id=existing.id)
        assert result is not None and result[0].id == existing.id
        assert len(store.projects_for(user.id)) == 1

    def test_an_unknown_tui_project_returns_none(self, store, tui):
        user = store.create_user("ada@example.com")
        assert import_plan(store, user.id, "nope") is None

    def test_a_stranger_cannot_import_into_someone_elses_project(self, store, tui):
        ada = store.create_user("ada@example.com")
        eve = store.create_user("eve@example.com")
        project = store.create_project("Ada's", ada.id)
        assert import_plan(store, eve.id, "tui_1", into_project_id=project.id) is None

    def test_a_viewer_cannot_import_into_a_project(self, store, tui):
        ada = store.create_user("ada@example.com")
        bob = store.create_user("bob@example.com")
        project = store.create_project("Shared", ada.id)
        store.add_member(project.id, ada.id, bob.id, "viewer")
        assert import_plan(store, bob.id, "tui_1", into_project_id=project.id) is None

    def test_importing_twice_files_two_artifacts_rather_than_mutating_one(self, store, tui):
        # Two people looking at "the plan" and seeing different documents is
        # worse than two dated ones.
        user = store.create_user("ada@example.com")
        project, first = import_plan(store, user.id, "tui_1")
        _, second = import_plan(store, user.id, "tui_1", into_project_id=project.id)
        assert first != second
        assert len(store.artifacts_for(project.id, user.id)) == 2

    def test_a_payload_with_no_kind_is_refused(self, store, monkeypatch):
        # An exporter that stopped emitting a kind would file an artifact the
        # bundle cannot draw, and a blank report is found by whoever opens it.
        import yeaboi.app.importer as importer

        monkeypatch.setattr(importer, "load_graph_state", lambda pid: {"project_name": "P"})
        monkeypatch.setattr(importer, "plan_export_args", lambda state: {"report": {}, "title": "P"})
        user = store.create_user("ada@example.com")
        assert import_plan(store, user.id, "tui_1") is None

    def test_the_tui_store_is_not_written_to(self, store, tui, graph_state):
        # The import reads. If it ever starts writing, the TUI's file store has
        # a second author and last-writer-wins becomes a data-loss bug.
        # repr rather than json: a graph state holds dataclasses, which the
        # TUI never serialises this way either.
        before = repr(graph_state)
        user = store.create_user("ada@example.com")
        import_plan(store, user.id, "tui_1")
        assert repr(graph_state) == before


class TestImportRoute:
    def test_imports_and_returns_both_ids(self, store, tui):
        app = AppServer(store)
        cookies, csrf = sign_in(app)
        response = call(app, "POST", "/api/import/plan", {"tui_project_id": "tui_1"}, cookies=cookies, csrf=csrf)
        assert response.code == 201
        assert set(json.loads(response.body)) == {"project_id", "artifact_id"}

    def test_a_missing_id_is_400(self, store, tui):
        app = AppServer(store)
        cookies, csrf = sign_in(app)
        assert call(app, "POST", "/api/import/plan", {}, cookies=cookies, csrf=csrf).code == 400

    def test_it_needs_a_session(self, store, tui):
        app = AppServer(store)
        assert call(app, "POST", "/api/import/plan", {"tui_project_id": "tui_1"}).code == 401


class TestImportableProjects:
    """Discovery, so a browser never needs to know an id."""

    def test_it_lists_what_the_tui_has(self, monkeypatch):
        import yeaboi.app.importer as importer
        from yeaboi.persistence import ProjectSummary

        summary = ProjectSummary(
            name="Payments",
            id="tui_1",
            created="2 days ago",
            status="Complete",
            feature_count=3,
            story_count=12,
            task_count=20,
            sprint_count=2,
            jira_summary="",
            progress=1.0,
            updated_at="2026-08-01T00:00:00",
        )
        monkeypatch.setattr(importer, "load_projects", lambda: [summary])
        rows = importer.importable_projects()
        assert rows == [
            {
                "id": "tui_1",
                "name": "Payments",
                "status": "Complete",
                "stories": 12,
                "updated_at": "2026-08-01T00:00:00",
            }
        ]

    def test_a_project_with_no_id_is_skipped(self, monkeypatch):
        # It cannot be imported, so offering it is offering a dead button.
        import yeaboi.app.importer as importer
        from yeaboi.persistence import ProjectSummary

        blank = ProjectSummary(
            name="Broken",
            id="",
            created="",
            status="",
            feature_count=0,
            story_count=0,
            task_count=0,
            sprint_count=0,
            jira_summary="",
            progress=0.0,
            updated_at="",
        )
        monkeypatch.setattr(importer, "load_projects", lambda: [blank])
        assert importer.importable_projects() == []

    def test_an_unreadable_store_is_nothing_to_import_not_a_crash(self, monkeypatch):
        # On a hosted instance there is no ~/.yeaboi at all; an empty list is
        # the honest answer and a 500 is not.
        import yeaboi.app.importer as importer

        def boom():
            raise OSError("no such directory")

        monkeypatch.setattr(importer, "load_projects", boom)
        assert importer.importable_projects() == []

    def test_the_endpoint_needs_a_session(self, store, tui):
        app = AppServer(store)
        assert call(app, "GET", "/api/import/candidates").code == 401

    def test_the_endpoint_returns_the_candidates(self, store, tui, monkeypatch):
        import yeaboi.app.importer as importer

        monkeypatch.setattr(importer, "importable_projects", lambda: [{"id": "tui_1", "name": "P"}])
        # The route closed over the module function at build time, so patch the
        # name the route actually calls.
        import yeaboi.app.routes as routes_module

        monkeypatch.setattr(routes_module, "importable_projects", lambda: [{"id": "tui_1", "name": "P"}])
        app = AppServer(store)
        cookies, csrf = sign_in(app)
        body = json.loads(call(app, "GET", "/api/import/candidates", cookies=cookies).body)
        assert body == {"projects": [{"id": "tui_1", "name": "P"}]}
