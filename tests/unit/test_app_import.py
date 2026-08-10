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

from yeaboi.app.importer import import_plan
from yeaboi.app.router import parse_request
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
    def _sign_in(self, app):
        response = app.handle(
            parse_request("POST", "/api/auth/session", {}, json.dumps({"email": "ada@example.com"}).encode())
        )
        cookies = {}
        for key, value in response.headers:
            if key == "Set-Cookie":
                name, _, rest = value.partition("=")
                cookies[name] = rest.split(";")[0]
        header = "; ".join(f"{k}={v}" for k, v in cookies.items())
        return header, cookies["yeaboi_csrf"]

    def test_imports_and_returns_both_ids(self, store, tui):
        app = AppServer(store)
        cookies, csrf = self._sign_in(app)
        response = app.handle(
            parse_request(
                "POST",
                "/api/import/plan",
                {"Cookie": cookies, "X-Yeaboi-CSRF": csrf},
                json.dumps({"tui_project_id": "tui_1"}).encode(),
            )
        )
        assert response.code == 201
        assert set(json.loads(response.body)) == {"project_id", "artifact_id"}

    def test_a_missing_id_is_400(self, store, tui):
        app = AppServer(store)
        cookies, csrf = self._sign_in(app)
        response = app.handle(
            parse_request("POST", "/api/import/plan", {"Cookie": cookies, "X-Yeaboi-CSRF": csrf}, b"{}")
        )
        assert response.code == 400

    def test_it_needs_a_session(self, store, tui):
        app = AppServer(store)
        response = app.handle(
            parse_request("POST", "/api/import/plan", {}, json.dumps({"tui_project_id": "tui_1"}).encode())
        )
        assert response.code == 401
