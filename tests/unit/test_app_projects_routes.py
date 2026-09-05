"""The projects routes and the cross-mode sessions list on the desktop wire."""

from __future__ import annotations

import json

import pytest

from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer

TOKEN = "test-token"

ROW_KEYS = {"project_id", "name", "description", "settings", "created_at", "last_active", "archived"}
SESSION_KEYS = {"session_id", "run_id", "mode", "title", "created_at", "last_modified", "project_id"}


def request(app: AppServer, method: str, path: str, *, authed: bool = True, body: dict | None = None):
    headers = {"Authorization": f"Bearer {TOKEN}"} if authed else {}
    raw = json.dumps(body).encode() if body is not None else b""
    if body is not None:
        headers["Content-Type"] = "application/json"
    return app.handle(parse_request(method, path, headers, raw))


def payload(response) -> dict:
    assert response.code == 200, response.body
    return json.loads(response.body)


@pytest.fixture
def app():
    return AppServer(token=TOKEN)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: path)
    return path


def _create(app, name="Apollo") -> dict:
    return payload(request(app, "POST", "/api/projects", body={"name": name}))


class TestAuth:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/api/projects"),
            ("POST", "/api/projects"),
            ("GET", "/api/projects/proj-11112222"),
            ("GET", "/api/projects/proj-11112222/sessions"),
            ("POST", "/api/projects/proj-11112222/defaults"),
            ("GET", "/api/sessions/recent"),
        ],
    )
    def test_every_route_requires_a_token(self, app, db, method, path):
        assert request(app, method, path, authed=False, body={} if method == "POST" else None).code == 401


class TestList:
    def test_empty_before_any_project(self, app, db):
        assert payload(request(app, "GET", "/api/projects")) == {"projects": []}

    def test_rows_carry_the_session_count(self, app, db):
        created = _create(app)
        rows = payload(request(app, "GET", "/api/projects"))["projects"]
        assert [r["project_id"] for r in rows] == [created["project_id"]]
        assert set(rows[0]) == ROW_KEYS | {"session_count"}
        assert rows[0]["session_count"] == 0

    def test_archived_rows_are_hidden_unless_asked_for(self, app, db):
        from yeaboi.projects.store import ProjectStore

        created = _create(app)
        with ProjectStore(db) as store:
            store.archive(created["project_id"])
        assert payload(request(app, "GET", "/api/projects"))["projects"] == []
        rows = payload(request(app, "GET", "/api/projects?include_archived=1"))["projects"]
        assert rows[0]["archived"] is True


class TestCreateAndGet:
    def test_create_then_get(self, app, db):
        created = _create(app)
        assert created["project_id"].startswith("proj-") and set(created) == ROW_KEYS
        got = payload(request(app, "GET", f"/api/projects/{created['project_id']}"))
        assert got["name"] == "Apollo" and got["session_ids"] == []

    def test_a_blank_name_is_400(self, app, db):
        assert request(app, "POST", "/api/projects", body={"name": "   "}).code == 400

    def test_an_unknown_project_is_404(self, app, db):
        assert request(app, "GET", "/api/projects/proj-00000000").code == 404

    def test_get_lists_the_linked_sessions(self, app, db):
        from yeaboi.sessions import SessionStore

        created = _create(app)
        with SessionStore(db) as store:
            store.create_session("p1", "Apollo", project_id=created["project_id"])
        got = payload(request(app, "GET", f"/api/projects/{created['project_id']}"))
        assert got["session_ids"] == ["p1"]


class TestDefaults:
    def test_merges_and_returns_the_settings(self, app, db):
        created = _create(app)
        resp = payload(
            request(
                app,
                "POST",
                f"/api/projects/{created['project_id']}/defaults",
                body={"defaults": {"repo_path": "/srv/app"}},
            )
        )
        assert resp == {"project_id": created["project_id"], "settings": {"repo_path": "/srv/app"}}

    @pytest.mark.parametrize("bad", ["srv/app", "/", ""])
    def test_a_repo_path_that_is_not_absolute_is_400(self, app, db, bad):
        created = _create(app)
        resp = request(
            app, "POST", f"/api/projects/{created['project_id']}/defaults", body={"defaults": {"repo_path": bad}}
        )
        assert resp.code == 400

    def test_an_unknown_key_is_400(self, app, db):
        created = _create(app)
        resp = request(app, "POST", f"/api/projects/{created['project_id']}/defaults", body={"defaults": {"nope": 1}})
        assert resp.code == 400

    @pytest.mark.parametrize("body", [{}, {"defaults": {}}, {"defaults": "repo_path=/x"}])
    def test_a_malformed_body_is_400(self, app, db, body):
        created = _create(app)
        assert request(app, "POST", f"/api/projects/{created['project_id']}/defaults", body=body).code == 400

    def test_an_unknown_project_is_404(self, app, db):
        resp = request(app, "POST", "/api/projects/proj-00000000/defaults", body={"defaults": {"repo_path": "/x"}})
        assert resp.code == 404


@pytest.fixture
def runs(app, db):
    """Two projects; Apollo owns a planning session with a standup, Borealis nothing."""
    from yeaboi.agent.state import StandupReport
    from yeaboi.sessions import SessionStore
    from yeaboi.standup.store import StandupStore

    apollo = _create(app, "Apollo")
    borealis = _create(app, "Borealis")
    with SessionStore(db) as store:
        store.create_session("p1", "Apollo", project_id=apollo["project_id"])
        store.create_session("p2", "Loose")
    with StandupStore(db) as store:
        store.record_run(StandupReport(session_id="p1", date="2026-09-01"))
        store.record_run(StandupReport(session_id="p2", date="2026-09-02"))
    return {"apollo": apollo["project_id"], "borealis": borealis["project_id"]}


class TestProjectSessions:
    def test_lists_only_the_projects_runs(self, app, db, runs):
        rows = payload(request(app, "GET", f"/api/projects/{runs['apollo']}/sessions"))["sessions"]
        assert {r["mode"] for r in rows} == {"planning", "standup"}
        assert all(r["session_id"] == "p1" and r["project_id"] == runs["apollo"] for r in rows)
        assert all(set(r) == SESSION_KEYS for r in rows)

    def test_mode_and_limit_filter(self, app, db, runs):
        rows = payload(request(app, "GET", f"/api/projects/{runs['apollo']}/sessions?mode=standup"))["sessions"]
        assert [r["mode"] for r in rows] == ["standup"]
        rows = payload(request(app, "GET", f"/api/projects/{runs['apollo']}/sessions?limit=1"))["sessions"]
        assert len(rows) == 1

    def test_a_project_with_nothing_is_an_empty_list(self, app, db, runs):
        assert payload(request(app, "GET", f"/api/projects/{runs['borealis']}/sessions")) == {"sessions": []}

    def test_an_unknown_project_is_404(self, app, db, runs):
        assert request(app, "GET", "/api/projects/proj-00000000/sessions").code == 404

    @pytest.mark.parametrize("query", ["mode=poker", "limit=lots", "limit=-1"])
    def test_bad_filters_are_400(self, app, db, runs, query):
        assert request(app, "GET", f"/api/projects/{runs['apollo']}/sessions?{query}").code == 400


class TestRecentSessions:
    def test_machine_wide_by_default(self, app, db, runs):
        rows = payload(request(app, "GET", "/api/sessions/recent"))["sessions"]
        assert {(r["mode"], r["session_id"]) for r in rows} == {
            ("planning", "p1"),
            ("planning", "p2"),
            ("standup", "p1"),
            ("standup", "p2"),
        }

    def test_project_mode_and_limit_narrow_it(self, app, db, runs):
        rows = payload(request(app, "GET", f"/api/sessions/recent?project_id={runs['apollo']}"))["sessions"]
        assert all(r["session_id"] == "p1" for r in rows) and len(rows) == 2
        rows = payload(request(app, "GET", "/api/sessions/recent?mode=standup&limit=1"))["sessions"]
        assert len(rows) == 1 and rows[0]["mode"] == "standup"

    def test_an_unknown_project_is_an_empty_list(self, app, db, runs):
        assert payload(request(app, "GET", "/api/sessions/recent?project_id=proj-00000000")) == {"sessions": []}

    def test_an_unknown_mode_is_400(self, app, db, runs):
        assert request(app, "GET", "/api/sessions/recent?mode=poker").code == 400


class TestRegistry:
    def test_routes_belong_to_the_capabilities(self):
        from yeaboi.app.registry import ROUTES

        owned = {(r.method, r.path) for r in ROUTES if r.capability == "projects"}
        assert owned == {
            ("GET", "/api/projects"),
            ("POST", "/api/projects"),
            ("GET", "/api/projects/{project_id}"),
            ("GET", "/api/projects/{project_id}/sessions"),
            ("POST", "/api/projects/{project_id}/defaults"),
        }
        assert {(r.method, r.path) for r in ROUTES if r.capability == "sessions"} == {("GET", "/api/sessions/recent")}
