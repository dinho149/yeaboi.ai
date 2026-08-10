"""The application substrate: router, store, sessions, and the pipeline.

Nothing here binds a port. ``AppServer.handle`` takes a ``Request`` and returns a
``Response``, so the entire HTTP surface is exercised as function calls — which
is why this file runs in ``test-fast`` alongside the TUI unit tests.
"""

from __future__ import annotations

import json

import pytest

from yeaboi.app.router import HTTPError, Request, Router, json_response, parse_request
from yeaboi.app.routes import PUBLIC_ROUTES
from yeaboi.app.server import AppServer
from yeaboi.app.sessions import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, SessionStore
from yeaboi.app.store import AppStore


@pytest.fixture
def store(tmp_path):
    return AppStore(tmp_path / "app.db")


@pytest.fixture
def app(store):
    return AppServer(store)


def _cookie_value(response, name: str) -> str:
    for key, value in response.headers:
        if key == "Set-Cookie" and value.startswith(f"{name}="):
            return value.split(";")[0].split("=", 1)[1]
    return ""


def _call(app, method, path, body=None, *, cookies="", csrf=""):
    headers = {}
    if cookies:
        headers["Cookie"] = cookies
    if csrf:
        headers[CSRF_HEADER] = csrf
    raw = json.dumps(body).encode() if body is not None else b""
    return app.handle(parse_request(method, path, headers, raw))


def _sign_in(app, email="ada@example.com", name="Ada"):
    """Sign in and return ``(cookie_header, csrf_value)`` ready to re-send."""
    response = _call(app, "POST", "/api/auth/session", {"email": email, "name": name})
    assert response.code == 200
    session = _cookie_value(response, SESSION_COOKIE)
    csrf = _cookie_value(response, CSRF_COOKIE)
    return f"{SESSION_COOKIE}={session}; {CSRF_COOKIE}={csrf}", csrf


class TestRouter:
    def test_matches_a_parameter_segment(self):
        router = Router()
        router.get("/api/projects/{project_id}", lambda r: json_response({"id": r.params["project_id"]}), auth=False)
        response = router.dispatch(Request(method="GET", path="/api/projects/prj_1"))
        assert json.loads(response.body) == {"id": "prj_1"}

    def test_a_parameter_does_not_swallow_a_slash(self):
        # `.+` here would make /api/projects/a/b match with project_id="a/b",
        # which silently routes a nested path to the wrong handler.
        router = Router()
        router.get("/api/projects/{project_id}", lambda r: json_response({}), auth=False)
        assert router.dispatch(Request(method="GET", path="/api/projects/a/b")).code == 404

    def test_known_path_wrong_method_is_405_not_404(self):
        router = Router()
        router.get("/api/thing", lambda r: json_response({}), auth=False)
        assert router.dispatch(Request(method="POST", path="/api/thing")).code == 405

    def test_unknown_path_is_404(self):
        assert Router().dispatch(Request(method="GET", path="/nope")).code == 404

    def test_authed_route_without_a_user_is_401(self):
        router = Router()
        router.get("/api/private", lambda r: json_response({}))
        assert router.dispatch(Request(method="GET", path="/api/private")).code == 401

    def test_http_error_becomes_its_status(self):
        router = Router()

        def boom(_):
            raise HTTPError(418, "teapot")

        router.get("/api/boom", boom, auth=False)
        response = router.dispatch(Request(method="GET", path="/api/boom"))
        assert response.code == 418
        assert json.loads(response.body) == {"error": "teapot"}

    def test_value_error_becomes_400(self):
        router = Router()

        def boom(_):
            raise ValueError("bad field")

        router.get("/api/boom", boom, auth=False)
        assert router.dispatch(Request(method="GET", path="/api/boom")).code == 400

    def test_query_is_parsed_and_scalar(self):
        request = parse_request("GET", "/api/x?a=1&a=2&b=hi", {})
        assert request.query == {"a": "1", "b": "hi"}
        assert request.path == "/api/x"

    def test_json_body_rejects_a_non_object(self):
        with pytest.raises(ValueError, match="JSON object"):
            Request(method="POST", path="/", body=b'["a"]').json()

    def test_empty_body_is_an_empty_dict(self):
        assert Request(method="POST", path="/").json() == {}

    def test_malformed_cookie_header_is_no_cookie(self):
        assert Request(method="GET", path="/", headers={"Cookie": "=====;;"}).cookie("x") == ""


class TestRouteTableIsClosed:
    """The check that makes a forgotten auth line impossible to ship."""

    def test_every_api_route_requires_a_session_unless_listed(self, app):
        unprotected = {
            (route.method, route.template)
            for route in app.router.routes
            if route.template.startswith("/api/") and not route.auth
        }
        assert unprotected == set(PUBLIC_ROUTES), (
            "a new /api/ route is public. Add auth=True, or add it to PUBLIC_ROUTES with a reason."
        )

    def test_public_routes_all_exist(self, app):
        declared = {(route.method, route.template) for route in app.router.routes}
        assert set(PUBLIC_ROUTES) <= declared


class TestAuth:
    def test_sign_in_sets_an_httponly_session_and_a_readable_csrf_cookie(self, app):
        response = _call(app, "POST", "/api/auth/session", {"email": "ada@example.com"})
        cookies = [value for key, value in response.headers if key == "Set-Cookie"]
        session = next(c for c in cookies if c.startswith(SESSION_COOKIE))
        csrf = next(c for c in cookies if c.startswith(CSRF_COOKIE))
        # The asymmetry is the double-submit design, not an oversight.
        assert "HttpOnly" in session
        assert "HttpOnly" not in csrf
        assert "SameSite=Lax" in session

    def test_secure_flag_follows_the_deployment(self, store):
        secure_app = AppServer(store, secure_cookies=True)
        response = _call(secure_app, "POST", "/api/auth/session", {"email": "a@b.com"})
        assert all("Secure" in value for key, value in response.headers if key == "Set-Cookie")

    def test_email_is_normalised(self, app):
        _call(app, "POST", "/api/auth/session", {"email": "  ADA@Example.COM "})
        assert app.store.user_by_email("ada@example.com") is not None

    def test_sign_in_is_idempotent_on_email(self, app):
        _call(app, "POST", "/api/auth/session", {"email": "ada@example.com"})
        _call(app, "POST", "/api/auth/session", {"email": "ada@example.com"})
        assert len(app.store.projects_for("nobody")) == 0
        assert app.store.user_by_email("ada@example.com") is not None

    def test_bad_email_is_400(self, app):
        assert _call(app, "POST", "/api/auth/session", {"email": "nope"}).code == 400

    def test_me_returns_the_signed_in_user(self, app):
        cookies, _ = _sign_in(app)
        assert json.loads(_call(app, "GET", "/api/auth/me", cookies=cookies).body)["email"] == "ada@example.com"

    def test_sign_out_revokes_the_session(self, app):
        cookies, csrf = _sign_in(app)
        assert _call(app, "DELETE", "/api/auth/session", cookies=cookies, csrf=csrf).code == 200
        assert _call(app, "GET", "/api/auth/me", cookies=cookies).code == 401

    def test_an_expired_session_does_not_resolve(self, app, store, monkeypatch):
        import yeaboi.app.sessions as sessions_module

        issued = SessionStore(store).issue(store.create_user("x@y.com").id)
        monkeypatch.setattr(sessions_module.time, "time", lambda: issued.expires_at + 1)
        assert SessionStore(store).resolve(issued.token) is None

    def test_a_garbage_token_does_not_resolve(self, app):
        assert app.sessions.resolve("not-a-token") is None


class TestCSRF:
    def test_unsafe_method_without_the_header_is_403(self, app):
        cookies, _ = _sign_in(app)
        assert _call(app, "POST", "/api/projects", {"name": "P"}, cookies=cookies).code == 403

    def test_unsafe_method_with_a_mismatched_token_is_403(self, app):
        cookies, _ = _sign_in(app)
        assert _call(app, "POST", "/api/projects", {"name": "P"}, cookies=cookies, csrf="wrong").code == 403

    def test_unsafe_method_with_the_echoed_token_passes(self, app):
        cookies, csrf = _sign_in(app)
        assert _call(app, "POST", "/api/projects", {"name": "P"}, cookies=cookies, csrf=csrf).code == 201

    def test_safe_method_needs_no_token(self, app):
        cookies, _ = _sign_in(app)
        assert _call(app, "GET", "/api/projects", cookies=cookies).code == 200

    def test_sign_in_is_not_blocked_by_csrf(self, app):
        # The request that creates the cookie cannot be expected to echo it.
        assert _call(app, "POST", "/api/auth/session", {"email": "new@example.com"}).code == 200


class TestProjects:
    def test_create_then_list(self, app):
        cookies, csrf = _sign_in(app)
        _call(app, "POST", "/api/projects", {"name": "Payments"}, cookies=cookies, csrf=csrf)
        listed = json.loads(_call(app, "GET", "/api/projects", cookies=cookies).body)["projects"]
        assert [p["name"] for p in listed] == ["Payments"]
        assert listed[0]["role"] == "owner"

    def test_a_blank_name_is_400(self, app):
        cookies, csrf = _sign_in(app)
        assert _call(app, "POST", "/api/projects", {"name": "   "}, cookies=cookies, csrf=csrf).code == 400

    def test_another_users_project_is_404_not_403(self, app):
        # 403 would confirm the id exists, which is itself a leak.
        ada, ada_csrf = _sign_in(app, "ada@example.com")
        created = json.loads(_call(app, "POST", "/api/projects", {"name": "Secret"}, cookies=ada, csrf=ada_csrf).body)
        bob, _ = _sign_in(app, "bob@example.com")
        assert _call(app, "GET", f"/api/projects/{created['id']}", cookies=bob).code == 404

    def test_owner_can_rename(self, app):
        cookies, csrf = _sign_in(app)
        created = json.loads(_call(app, "POST", "/api/projects", {"name": "Old"}, cookies=cookies, csrf=csrf).body)
        renamed = _call(app, "POST", f"/api/projects/{created['id']}", {"name": "New"}, cookies=cookies, csrf=csrf)
        assert json.loads(renamed.body)["name"] == "New"

    def test_owner_can_delete_and_it_is_gone(self, app):
        cookies, csrf = _sign_in(app)
        created = json.loads(_call(app, "POST", "/api/projects", {"name": "Doomed"}, cookies=cookies, csrf=csrf).body)
        assert _call(app, "DELETE", f"/api/projects/{created['id']}", cookies=cookies, csrf=csrf).code == 200
        assert _call(app, "GET", f"/api/projects/{created['id']}", cookies=cookies).code == 404

    def test_a_member_sees_the_project_but_a_viewer_cannot_rename(self, app):
        ada, ada_csrf = _sign_in(app, "ada@example.com")
        created = json.loads(_call(app, "POST", "/api/projects", {"name": "Shared"}, cookies=ada, csrf=ada_csrf).body)
        added = _call(
            app,
            "POST",
            f"/api/projects/{created['id']}/members",
            {"email": "bob@example.com", "role": "viewer"},
            cookies=ada,
            csrf=ada_csrf,
        )
        assert added.code == 201
        bob, bob_csrf = _sign_in(app, "bob@example.com")
        assert _call(app, "GET", f"/api/projects/{created['id']}", cookies=bob).code == 200
        blocked = _call(app, "POST", f"/api/projects/{created['id']}", {"name": "Nope"}, cookies=bob, csrf=bob_csrf)
        assert blocked.code == 403

    def test_an_editor_cannot_delete(self, app):
        ada, ada_csrf = _sign_in(app, "ada@example.com")
        created = json.loads(_call(app, "POST", "/api/projects", {"name": "Shared"}, cookies=ada, csrf=ada_csrf).body)
        _call(
            app,
            "POST",
            f"/api/projects/{created['id']}/members",
            {"email": "bob@example.com", "role": "editor"},
            cookies=ada,
            csrf=ada_csrf,
        )
        bob, bob_csrf = _sign_in(app, "bob@example.com")
        assert _call(app, "DELETE", f"/api/projects/{created['id']}", cookies=bob, csrf=bob_csrf).code == 403

    def test_a_non_owner_cannot_add_members(self, app):
        ada, ada_csrf = _sign_in(app, "ada@example.com")
        created = json.loads(_call(app, "POST", "/api/projects", {"name": "Shared"}, cookies=ada, csrf=ada_csrf).body)
        _call(
            app,
            "POST",
            f"/api/projects/{created['id']}/members",
            {"email": "bob@example.com", "role": "editor"},
            cookies=ada,
            csrf=ada_csrf,
        )
        bob, bob_csrf = _sign_in(app, "bob@example.com")
        blocked = _call(
            app,
            "POST",
            f"/api/projects/{created['id']}/members",
            {"email": "eve@example.com"},
            cookies=bob,
            csrf=bob_csrf,
        )
        assert blocked.code == 403


class TestStore:
    def test_deleting_a_project_cascades_to_memberships(self, store):
        ada = store.create_user("ada@example.com")
        project = store.create_project("P", ada.id)
        assert store.delete_project(project.id, ada.id) is True
        assert store.projects_for(ada.id) == []

    def test_an_unknown_role_is_rejected(self, store):
        ada = store.create_user("ada@example.com")
        bob = store.create_user("bob@example.com")
        project = store.create_project("P", ada.id)
        with pytest.raises(ValueError, match="unknown role"):
            store.add_member(project.id, ada.id, bob.id, "admin")

    def test_adding_an_existing_member_updates_the_role(self, store):
        ada = store.create_user("ada@example.com")
        bob = store.create_user("bob@example.com")
        project = store.create_project("P", ada.id)
        store.add_member(project.id, ada.id, bob.id, "viewer")
        store.add_member(project.id, ada.id, bob.id, "editor")
        roles = {m["email"]: m["role"] for m in store.members(project.id, ada.id)}
        assert roles["bob@example.com"] == "editor"

    def test_a_project_for_an_unknown_owner_is_refused(self, store):
        with pytest.raises(ValueError, match="no such user"):
            store.create_project("P", "usr_nope")

    def test_state_survives_a_new_store_over_the_same_file(self, tmp_path):
        first = AppStore(tmp_path / "app.db")
        ada = first.create_user("ada@example.com")
        first.create_project("Durable", ada.id)
        second = AppStore(tmp_path / "app.db")
        assert [p.name for p in second.projects_for(ada.id)] == ["Durable"]
