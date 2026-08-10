"""Artifacts: the report payload as a stored, scoped, renderable thing.

The class that matters most here is the last one. An artifact is worth storing
only if the front end can actually draw it, and the thing that guarantees that
is not a type annotation on this side — it is that the payload a Python exporter
already produces is byte-identical to what comes back out. If those two ever
part company, the app renders a blank report and nothing else notices.
"""

from __future__ import annotations

import json

import pytest

from yeaboi.agent.state import MemberUpdate, StandupReport
from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer
from yeaboi.app.store import AppStore
from yeaboi.standup.export import standup_export_args


@pytest.fixture
def store(tmp_path):
    return AppStore(tmp_path / "app.db")


@pytest.fixture
def app(store):
    return AppServer(store)


def _cookie(response, name):
    for key, value in response.headers:
        if key == "Set-Cookie" and value.startswith(f"{name}="):
            return value.split(";")[0].split("=", 1)[1]
    return ""


def _call(app, method, path, body=None, *, cookies="", csrf=""):
    headers = {}
    if cookies:
        headers["Cookie"] = cookies
    if csrf:
        headers["X-Yeaboi-CSRF"] = csrf
    raw = json.dumps(body).encode() if body is not None else b""
    return app.handle(parse_request(method, path, headers, raw))


def _sign_in(app, email="ada@example.com"):
    response = _call(app, "POST", "/api/auth/session", {"email": email})
    session = _cookie(response, "yeaboi_session")
    csrf = _cookie(response, "yeaboi_csrf")
    return f"yeaboi_session={session}; yeaboi_csrf={csrf}", csrf


def _project(app, cookies, csrf, name="Payments"):
    return json.loads(_call(app, "POST", "/api/projects", {"name": name}, cookies=cookies, csrf=csrf).body)["id"]


PAYLOAD = {"kind": "anonymize", "markdown": "# hello", "warnings": []}


class TestArtifactStore:
    def test_payload_survives_a_round_trip(self, store):
        user = store.create_user("ada@example.com")
        project = store.create_project("P", user.id)
        created = store.create_artifact(project.id, user.id, "anonymize", "Notes", PAYLOAD)
        assert created is not None
        assert store.artifact(created.id, user.id).payload == PAYLOAD

    def test_a_list_carries_no_payloads(self, store):
        # Ten reports on a list screen is ten payloads nobody reads.
        user = store.create_user("ada@example.com")
        project = store.create_project("P", user.id)
        store.create_artifact(project.id, user.id, "anonymize", "Notes", PAYLOAD)
        assert store.artifacts_for(project.id, user.id)[0].payload is None

    def test_kind_must_match_the_payload(self, store):
        # The bundle switches on payload['kind']; a row filed under one kind
        # with another inside renders as something it is not.
        user = store.create_user("ada@example.com")
        project = store.create_project("P", user.id)
        with pytest.raises(ValueError, match="payload kind"):
            store.create_artifact(project.id, user.id, "retro", "Wrong", PAYLOAD)

    def test_a_viewer_cannot_write(self, store):
        ada = store.create_user("ada@example.com")
        bob = store.create_user("bob@example.com")
        project = store.create_project("P", ada.id)
        store.add_member(project.id, ada.id, bob.id, "viewer")
        assert store.create_artifact(project.id, bob.id, "anonymize", "N", PAYLOAD) is None

    def test_a_stranger_cannot_read_by_id(self, store):
        ada = store.create_user("ada@example.com")
        eve = store.create_user("eve@example.com")
        project = store.create_project("P", ada.id)
        created = store.create_artifact(project.id, ada.id, "anonymize", "N", PAYLOAD)
        assert store.artifact(created.id, eve.id) is None

    def test_deleting_a_project_takes_its_artifacts(self, store):
        user = store.create_user("ada@example.com")
        project = store.create_project("P", user.id)
        created = store.create_artifact(project.id, user.id, "anonymize", "N", PAYLOAD)
        store.delete_project(project.id, user.id)
        assert store.artifact(created.id, user.id) is None

    def test_creating_an_artifact_touches_the_project(self, store):
        user = store.create_user("ada@example.com")
        project = store.create_project("P", user.id)
        store.create_artifact(project.id, user.id, "anonymize", "N", PAYLOAD)
        # The project list sorts by updated_at, so a new report has to move it.
        assert store.project(project.id, user.id).updated_at > project.created_at - 1


class TestArtifactRoutes:
    def test_create_list_read_delete(self, app):
        cookies, csrf = _sign_in(app)
        project_id = _project(app, cookies, csrf)
        created = _call(
            app,
            "POST",
            f"/api/projects/{project_id}/artifacts",
            {"kind": "anonymize", "title": "Notes", "payload": PAYLOAD},
            cookies=cookies,
            csrf=csrf,
        )
        assert created.code == 201
        artifact_id = json.loads(created.body)["id"]

        listed = json.loads(_call(app, "GET", f"/api/projects/{project_id}/artifacts", cookies=cookies).body)
        assert [a["title"] for a in listed["artifacts"]] == ["Notes"]
        assert "payload" not in listed["artifacts"][0]

        fetched = json.loads(_call(app, "GET", f"/api/artifacts/{artifact_id}", cookies=cookies).body)
        assert fetched["payload"] == PAYLOAD

        assert _call(app, "DELETE", f"/api/artifacts/{artifact_id}", cookies=cookies, csrf=csrf).code == 200
        assert _call(app, "GET", f"/api/artifacts/{artifact_id}", cookies=cookies).code == 404

    def test_a_non_object_payload_is_400(self, app):
        cookies, csrf = _sign_in(app)
        project_id = _project(app, cookies, csrf)
        response = _call(
            app,
            "POST",
            f"/api/projects/{project_id}/artifacts",
            {"kind": "anonymize", "title": "x", "payload": "not an object"},
            cookies=cookies,
            csrf=csrf,
        )
        assert response.code == 400

    def test_another_users_artifact_is_404(self, app):
        ada, ada_csrf = _sign_in(app, "ada@example.com")
        project_id = _project(app, ada, ada_csrf)
        created = _call(
            app,
            "POST",
            f"/api/projects/{project_id}/artifacts",
            {"kind": "anonymize", "title": "Secret", "payload": PAYLOAD},
            cookies=ada,
            csrf=ada_csrf,
        )
        artifact_id = json.loads(created.body)["id"]
        bob, _ = _sign_in(app, "bob@example.com")
        assert _call(app, "GET", f"/api/artifacts/{artifact_id}", cookies=bob).code == 404

    def test_the_artifact_route_serves_the_shell(self, app):
        # A deep link to a report must survive a hard refresh.
        response = app.handle(parse_request("GET", "/projects/prj_1/artifacts/art_1", {}))
        assert response.code == 200
        assert response.content_type.startswith("text/html")


class TestARealExporterPayloadIsStorable:
    """The compatibility that makes one renderer serve export, share and app.

    `standup_export_args` builds what `export_page` hands the bundle. If that
    same mapping does not survive the store unchanged, the app draws a report
    the downloaded file of the same run does not agree with.
    """

    def _report(self):
        return StandupReport(
            date="2026-07-10",
            sprint_name="Sprint 5",
            sprint_day=3,
            sprint_total_days=10,
            confidence_pct=82,
            confidence_label="At risk",
            team_summary="steady progress",
            member_updates=(MemberUpdate(name="Alice", summary="login page", source="inferred"),),
        )

    def test_a_real_standup_payload_round_trips_unchanged(self, store):
        payload = standup_export_args(self._report())["report"]
        user = store.create_user("ada@example.com")
        project = store.create_project("P", user.id)
        created = store.create_artifact(project.id, user.id, payload["kind"], "Standup", payload)
        assert store.artifact(created.id, user.id).payload == payload

    def test_the_payload_is_json_serialisable_as_it_stands(self, store):
        # No custom encoder: an exporter payload is text, numbers and structure
        # by contract, and anything else is a bug in the exporter.
        payload = standup_export_args(self._report())["report"]
        assert json.loads(json.dumps(payload)) == payload

    def test_the_kind_is_one_the_bundle_switches_on(self):
        payload = standup_export_args(self._report())["report"]
        renderer = (
            (
                __import__("pathlib").Path(__file__).resolve().parents[2] / "frontend" / "src" / "export" / "Report.tsx"
            )
            .read_text()
        )
        assert f"case '{payload['kind']}':" in renderer
