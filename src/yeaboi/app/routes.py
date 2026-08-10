"""The route table.

One module so the application's whole surface can be read top to bottom, and so
``test_app_router.py`` can assert the property that matters: every route under
``/api/`` requires a session except the handful that cannot
(:data:`PUBLIC_ROUTES`). Auth is declared beside the path rather than checked
inside the handler, because a missing check inside a handler is invisible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from yeaboi.app.page import render_app_page
from yeaboi.app.router import HTTPError, Request, Response, Router, json_response
from yeaboi.app.sessions import SESSION_COOKIE, clear_cookie_headers, cookie_headers

if TYPE_CHECKING:
    from yeaboi.app.server import AppServer

#: Paths that are reachable without a session, and why. Anything not here needs
#: one; the test asserts this list is exhaustive.
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/health"),  # liveness — must answer before anyone is signed in
        ("POST", "/api/auth/session"),  # sign-in: the request that creates the cookie
    }
)


#: Paths that serve the shell document. Listed rather than a catch-all: a
#: typo'd URL should 404 like a missing page, not silently render the app.
SHELL_ROUTES: tuple[str, ...] = (
    "/",
    "/projects",
    "/projects/{project_id}",
    "/projects/{project_id}/artifacts/{artifact_id}",
    "/settings",
)


def _project_or_404(app: AppServer, request: Request):
    project = app.store.project(request.params["project_id"], request.user_id or "")
    if project is None:
        # 404 rather than 403 on purpose: "this project exists but is not yours"
        # is itself information, and the two cases are indistinguishable here.
        raise HTTPError(404, "not found")
    return project


def build_router(app: AppServer) -> Router:
    router = Router()

    # ── health ─────────────────────────────────────────────────────────

    def health(_: Request) -> Response:
        return json_response({"status": "ok"})

    router.get("/api/health", health, auth=False)

    # ── auth ───────────────────────────────────────────────────────────

    def sign_in(request: Request) -> Response:
        """Create a session for an email address.

        TODO(auth): this trusts the address. It is the seam a real verifier
        (magic link, OAuth, SSO) drops into, and everything downstream — cookie,
        CSRF, membership — is already shaped for it. Deliberately not a password
        store: adding one would be a security surface the project has to keep
        forever, and the plan is to federate instead.
        """
        payload = request.json()
        email = str(payload.get("email", "")).strip()
        if not email or "@" not in email:
            raise HTTPError(400, "a valid email is required")
        user = app.store.create_user(email, str(payload.get("name", "")))
        issued = app.sessions.issue(user.id)
        return json_response(
            {"user": {"id": user.id, "email": user.email, "name": user.name}, "csrf": issued.csrf},
            headers=cookie_headers(issued, secure=app.secure_cookies),
        )

    router.post("/api/auth/session", sign_in, auth=False)

    def sign_out(request: Request) -> Response:
        app.sessions.revoke(request.cookie(SESSION_COOKIE))
        return json_response({"ok": True}, headers=clear_cookie_headers(secure=app.secure_cookies))

    router.delete("/api/auth/session", sign_out)

    def me(request: Request) -> Response:
        user = app.store.user(request.user_id or "")
        if user is None:
            raise HTTPError(401, "unauthorized")
        return json_response({"id": user.id, "email": user.email, "name": user.name})

    router.get("/api/auth/me", me)

    # ── projects ───────────────────────────────────────────────────────

    def list_projects(request: Request) -> Response:
        rows = [
            {"id": p.id, "name": p.name, "role": p.role, "updated_at": p.updated_at}
            for p in app.store.projects_for(request.user_id or "")
        ]
        return json_response({"projects": rows})

    router.get("/api/projects", list_projects)

    def create_project(request: Request) -> Response:
        payload = request.json()
        project = app.store.create_project(str(payload.get("name", "")), request.user_id or "")
        return json_response({"id": project.id, "name": project.name, "role": project.role}, 201)

    router.post("/api/projects", create_project)

    def get_project(request: Request) -> Response:
        project = _project_or_404(app, request)
        return json_response(
            {
                "id": project.id,
                "name": project.name,
                "role": project.role,
                "created_at": project.created_at,
                "updated_at": project.updated_at,
                "members": app.store.members(project.id, request.user_id or ""),
            }
        )

    router.get("/api/projects/{project_id}", get_project)

    def rename_project(request: Request) -> Response:
        _project_or_404(app, request)
        payload = request.json()
        renamed = app.store.rename_project(
            request.params["project_id"], request.user_id or "", str(payload.get("name", ""))
        )
        if renamed is None:
            raise HTTPError(403, "read-only")
        return json_response({"id": renamed.id, "name": renamed.name, "role": renamed.role})

    router.post("/api/projects/{project_id}", rename_project)

    def delete_project(request: Request) -> Response:
        _project_or_404(app, request)
        if not app.store.delete_project(request.params["project_id"], request.user_id or ""):
            raise HTTPError(403, "only the owner may delete a project")
        return json_response({"ok": True})

    router.delete("/api/projects/{project_id}", delete_project)

    def add_member(request: Request) -> Response:
        _project_or_404(app, request)
        payload = request.json()
        email = str(payload.get("email", "")).strip()
        if not email or "@" not in email:
            raise HTTPError(400, "a valid email is required")
        invitee = app.store.create_user(email)
        ok = app.store.add_member(
            request.params["project_id"], request.user_id or "", invitee.id, str(payload.get("role", "editor"))
        )
        if not ok:
            raise HTTPError(403, "only the owner may add members")
        return json_response({"ok": True, "user_id": invitee.id}, 201)

    router.post("/api/projects/{project_id}/members", add_member)

    # ── artifacts ──────────────────────────────────────────────────────
    #
    # An artifact is a report payload: the same mapping of text and numbers an
    # exporter hands to `export_page`, which the front end's `Report` switch
    # already knows how to draw. Storing that shape rather than rendered HTML is
    # what lets one renderer serve the export, the share and the app.

    def list_artifacts(request: Request) -> Response:
        _project_or_404(app, request)
        rows = [
            {"id": a.id, "kind": a.kind, "title": a.title, "created_at": a.created_at}
            for a in app.store.artifacts_for(request.params["project_id"], request.user_id or "")
        ]
        return json_response({"artifacts": rows})

    router.get("/api/projects/{project_id}/artifacts", list_artifacts)

    def create_artifact(request: Request) -> Response:
        _project_or_404(app, request)
        payload = request.json()
        report = payload.get("payload")
        if not isinstance(report, dict):
            raise HTTPError(400, "payload must be an object")
        artifact = app.store.create_artifact(
            request.params["project_id"],
            request.user_id or "",
            str(payload.get("kind", "")),
            str(payload.get("title", "")),
            report,
        )
        if artifact is None:
            raise HTTPError(403, "read-only")
        return json_response({"id": artifact.id, "kind": artifact.kind, "title": artifact.title}, 201)

    router.post("/api/projects/{project_id}/artifacts", create_artifact)

    def get_artifact(request: Request) -> Response:
        artifact = app.store.artifact(request.params["artifact_id"], request.user_id or "")
        if artifact is None:
            raise HTTPError(404, "not found")
        return json_response(
            {
                "id": artifact.id,
                "kind": artifact.kind,
                "title": artifact.title,
                "created_at": artifact.created_at,
                "project_id": artifact.project_id,
                "payload": artifact.payload,
            }
        )

    router.get("/api/artifacts/{artifact_id}", get_artifact)

    def delete_artifact(request: Request) -> Response:
        if not app.store.delete_artifact(request.params["artifact_id"], request.user_id or ""):
            raise HTTPError(404, "not found")
        return json_response({"ok": True})

    router.delete("/api/artifacts/{artifact_id}", delete_artifact)

    # ── the shell ──────────────────────────────────────────────────────
    #
    # Registered last and matching every path the API did not claim, so a hard
    # refresh on /projects/prj_123 serves the app rather than a 404. The client
    # router then reads the URL it was loaded at.

    def shell(request: Request) -> Response:
        html = render_app_page(app.store, request.user_id)
        return Response(code=200, body=html.encode("utf-8"), content_type="text/html; charset=utf-8")

    for template in SHELL_ROUTES:
        router.get(template, shell, auth=False)

    return router
