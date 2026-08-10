"""Rooms: the live archetype, as a registry rather than a port.

The security shape is the point of most of these. A room row is readable by
every member of a project, so what may go in it is exactly what a teammate is
allowed to have: the participant link and the join code. The host link, which
carries the admin secret, must never appear — and the stored URL is rendered as
an `href`, so a scheme that is not http(s) is stored XSS with a label on it.
"""

from __future__ import annotations

import json

import pytest

from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer
from yeaboi.app.store import ROOM_KINDS, AppStore

URL = "http://127.0.0.1:5173/?token=abc"


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
    return (
        f"yeaboi_session={_cookie(response, 'yeaboi_session')}; yeaboi_csrf={_cookie(response, 'yeaboi_csrf')}",
        _cookie(response, "yeaboi_csrf"),
    )


class TestRoomStore:
    def test_open_and_list(self, store):
        user = store.create_user("ada@example.com")
        project = store.create_project("P", user.id)
        store.open_room(project.id, user.id, "retro", URL, title="Sprint 5 retro", join_code="tidy-otter")
        rooms = store.rooms_for(project.id, user.id)
        assert [room.kind for room in rooms] == ["retro"]
        assert rooms[0].live is True

    @pytest.mark.parametrize("kind", ROOM_KINDS)
    def test_every_declared_kind_is_accepted(self, store, kind):
        user = store.create_user("ada@example.com")
        project = store.create_project("P", user.id)
        assert store.open_room(project.id, user.id, kind, URL) is not None

    def test_an_unknown_kind_is_refused(self, store):
        user = store.create_user("ada@example.com")
        project = store.create_project("P", user.id)
        with pytest.raises(ValueError, match="unknown room kind"):
            store.open_room(project.id, user.id, "standup", URL)

    @pytest.mark.parametrize(
        "url",
        ["javascript:alert(1)", "data:text/html,<script>", "file:///etc/passwd", "not-a-url", ""],
    )
    def test_a_non_http_invite_url_is_refused(self, store, url):
        # The value is rendered as an href a member clicks.
        user = store.create_user("ada@example.com")
        project = store.create_project("P", user.id)
        with pytest.raises(ValueError, match="http"):
            store.open_room(project.id, user.id, "retro", url)

    def test_a_viewer_cannot_open_a_room(self, store):
        ada = store.create_user("ada@example.com")
        bob = store.create_user("bob@example.com")
        project = store.create_project("P", ada.id)
        store.add_member(project.id, ada.id, bob.id, "viewer")
        assert store.open_room(project.id, bob.id, "retro", URL) is None

    def test_a_stranger_sees_no_rooms(self, store):
        ada = store.create_user("ada@example.com")
        eve = store.create_user("eve@example.com")
        project = store.create_project("P", ada.id)
        store.open_room(project.id, ada.id, "retro", URL)
        assert store.rooms_for(project.id, eve.id) == []

    def test_closing_is_soft_so_the_history_survives(self, store):
        user = store.create_user("ada@example.com")
        project = store.create_project("P", user.id)
        room = store.open_room(project.id, user.id, "retro", URL)
        assert store.close_room(room.id, user.id) is True
        assert store.rooms_for(project.id, user.id) == []
        closed = store.rooms_for(project.id, user.id, live_only=False)
        assert len(closed) == 1 and closed[0].live is False

    def test_a_viewer_cannot_close(self, store):
        ada = store.create_user("ada@example.com")
        bob = store.create_user("bob@example.com")
        project = store.create_project("P", ada.id)
        store.add_member(project.id, ada.id, bob.id, "viewer")
        room = store.open_room(project.id, ada.id, "retro", URL)
        assert store.close_room(room.id, bob.id) is False

    def test_deleting_a_project_takes_its_rooms(self, store):
        user = store.create_user("ada@example.com")
        project = store.create_project("P", user.id)
        store.open_room(project.id, user.id, "retro", URL)
        store.delete_project(project.id, user.id)
        assert store.rooms_for(project.id, user.id) == []


class TestRoomRoutes:
    def test_open_list_close(self, app):
        cookies, csrf = _sign_in(app)
        created = _call(app, "POST", "/api/projects", {"name": "P"}, cookies=cookies, csrf=csrf)
        project_id = json.loads(created.body)["id"]
        opened = _call(
            app,
            "POST",
            f"/api/projects/{project_id}/rooms",
            {"kind": "retro", "invite_url": URL, "title": "Sprint 5", "join_code": "tidy-otter"},
            cookies=cookies,
            csrf=csrf,
        )
        assert opened.code == 201
        room_id = json.loads(opened.body)["id"]

        listed = json.loads(_call(app, "GET", f"/api/projects/{project_id}/rooms", cookies=cookies).body)
        assert listed["rooms"][0]["join_code"] == "tidy-otter"

        assert _call(app, "DELETE", f"/api/rooms/{room_id}", cookies=cookies, csrf=csrf).code == 200
        assert json.loads(_call(app, "GET", f"/api/projects/{project_id}/rooms", cookies=cookies).body)["rooms"] == []

    def test_a_bad_url_is_400_not_500(self, app):
        cookies, csrf = _sign_in(app)
        created = _call(app, "POST", "/api/projects", {"name": "P"}, cookies=cookies, csrf=csrf)
        project_id = json.loads(created.body)["id"]
        response = _call(
            app,
            "POST",
            f"/api/projects/{project_id}/rooms",
            {"kind": "retro", "invite_url": "javascript:alert(1)"},
            cookies=cookies,
            csrf=csrf,
        )
        assert response.code == 400

    def test_rooms_of_another_project_are_404(self, app):
        ada, ada_csrf = _sign_in(app, "ada@example.com")
        created = _call(app, "POST", "/api/projects", {"name": "P"}, cookies=ada, csrf=ada_csrf)
        project_id = json.loads(created.body)["id"]
        bob, _ = _sign_in(app, "bob@example.com")
        assert _call(app, "GET", f"/api/projects/{project_id}/rooms", cookies=bob).code == 404

    def test_no_room_field_can_carry_the_host_secret(self, app):
        """The registry's whole security story, as one assertion.

        Every member of a project can read this payload, so the set of keys it
        returns is the set of things a teammate may have. An admin token or a
        host link appearing here later would be a silent privilege escalation.
        """
        cookies, csrf = _sign_in(app)
        created = _call(app, "POST", "/api/projects", {"name": "P"}, cookies=cookies, csrf=csrf)
        project_id = json.loads(created.body)["id"]
        _call(
            app,
            "POST",
            f"/api/projects/{project_id}/rooms",
            {"kind": "retro", "invite_url": URL},
            cookies=cookies,
            csrf=csrf,
        )
        room = json.loads(_call(app, "GET", f"/api/projects/{project_id}/rooms", cookies=cookies).body)["rooms"][0]
        assert set(room) == {"id", "kind", "title", "invite_url", "join_code", "opened_at"}
