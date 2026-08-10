"""The route table.

One module so the application's whole surface can be read top to bottom, and so
``test_app_router.py`` can assert the property that matters: every route under
``/api/`` requires a session except the handful that cannot
(:data:`PUBLIC_ROUTES`). Auth is declared beside the path rather than checked
inside the handler, because a missing check inside a handler is invisible.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from yeaboi.app.auth import looks_like_email, normalise_email
from yeaboi.app.importer import import_plan
from yeaboi.app.page import render_app_page
from yeaboi.app.router import HTTPError, Request, Response, Router, json_response
from yeaboi.app.sessions import SESSION_COOKIE, clear_cookie_headers, cookie_headers

if TYPE_CHECKING:
    from yeaboi.app.server import AppServer

#: Paths that are reachable without a session, and why. Anything not here needs
#: one; the test asserts this list is exhaustive.
logger = logging.getLogger(__name__)

PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/api/health"),  # liveness — must answer before anyone is signed in
        ("GET", "/api/auth/first-run"),  # is this instance unclaimed? asked before anyone exists
        ("POST", "/api/auth/claim"),  # claims an unclaimed local instance
        ("POST", "/api/auth/request"),  # asks for a sign-in link; proves nothing yet
        ("POST", "/api/auth/session"),  # redeems the token: the request that creates the cookie
    }
)


#: Paths that serve the shell document. Listed rather than a catch-all: a
#: typo'd URL should 404 like a missing page, not silently render the app.
SHELL_ROUTES: tuple[str, ...] = (
    "/",
    "/signin",
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

    def first_run_available(app_server, request: Request) -> bool:
        """Whether this instance may be claimed from the browser, with no email.

        Three conditions, all required, and each closing a different door:

        * **No users yet.** A claim is how the *first* account is made; once one
          exists this is off forever, so it can never be a way in past sign-in.
        * **The request came from this machine**, read off the socket rather
          than a header — ``X-Forwarded-For`` is caller-supplied and would make
          this claimable by anyone who says the right words.
        * **Cookies are not marked secure**, i.e. this is not a TLS deployment.
          A hosted instance must go through email, or the first stranger to find
          the URL owns it.

        The reason it exists at all: sign-in links are delivered by email, and a
        laptop has no SMTP. Without this, a browser-only user of a fresh local
        instance cannot get in at all — the link is printed to a terminal they
        were told they would not need.
        """
        return (
            app_server.store.count_users() == 0
            and request.is_loopback
            and not app_server.secure_cookies
        )

    def first_run(request: Request) -> Response:
        return json_response({"available": first_run_available(app, request)})

    router.get("/api/auth/first-run", first_run, auth=False)

    def claim(request: Request) -> Response:
        """Create the first account and sign in, without email."""
        if not first_run_available(app, request):
            # One answer for "already claimed", "not local" and "hosted": the
            # caller has no business knowing which.
            raise HTTPError(403, "this instance cannot be claimed")
        payload = request.json()
        email = normalise_email(str(payload.get("email", "")))
        if not looks_like_email(email):
            raise HTTPError(400, "a valid email is required")
        user = app.store.create_user(email, str(payload.get("name", "")))
        issued = app.sessions.issue(user.id)
        logger.warning("instance claimed by %s from %s", email, request.client_host)
        return json_response(
            {"user": {"id": user.id, "email": user.email, "name": user.name}, "csrf": issued.csrf},
            201,
            headers=cookie_headers(issued, secure=app.secure_cookies),
        )

    router.post("/api/auth/claim", claim, auth=False)

    def request_login(request: Request) -> Response:
        """Ask for a sign-in link.

        Answers 202 for **every** syntactically valid address, whether or not
        an account exists and whether or not the rate limit swallowed it. The
        alternative turns this endpoint into a way to ask which addresses have
        accounts here, and — for a product used by named teams — that is a
        roster, not a nuisance.
        """
        payload = request.json()
        email = normalise_email(str(payload.get("email", "")))
        if not looks_like_email(email):
            raise HTTPError(400, "a valid email is required")
        login = app.logins.request(email)
        if login is not None:
            app.deliverer.deliver(login)
        return json_response({"status": "sent"}, 202)

    router.post("/api/auth/request", request_login, auth=False)

    def sign_in(request: Request) -> Response:
        """Redeem a sign-in token for a session.

        The user row is created here rather than when the link was requested:
        until a token comes back, nobody has proved anything, and minting an
        account per request would let a stranger fill the table with addresses
        they do not own.
        """
        payload = request.json()
        email = app.logins.consume(str(payload.get("token", "")))
        if email is None:
            # One answer for expired, spent, forged and absent. Saying which
            # tells an attacker whether they are close.
            raise HTTPError(401, "that sign-in link is not valid")
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

    # ── import ─────────────────────────────────────────────────────────

    def import_tui_plan(request: Request) -> Response:
        """Copy a plan out of the local TUI store into the app.

        Local-only by nature: it reads ``~/.yeaboi`` on the machine the server
        runs on. That is right for the single-tenant case this ships as, and it
        is the first thing that has to change if the app is ever hosted for
        someone else's projects — there is no such directory on a server.
        """
        payload = request.json()
        tui_project_id = str(payload.get("tui_project_id", "")).strip()
        if not tui_project_id:
            raise HTTPError(400, "tui_project_id is required")
        result = import_plan(
            app.store,
            request.user_id or "",
            tui_project_id,
            into_project_id=str(payload.get("project_id", "")).strip(),
        )
        if result is None:
            raise HTTPError(404, "no such plan, or it could not be imported")
        project, artifact_id = result
        return json_response({"project_id": project.id, "artifact_id": artifact_id}, 201)

    router.post("/api/import/plan", import_tui_plan)

    # ── rooms (the live archetype) ─────────────────────────────────────
    #
    # A registry, not a port. The retro and poker boards stay their own servers
    # with their own state; this records where one is so a teammate can find it.
    # That is the cheapest of the three options in docs/app-plan.md and it
    # forecloses neither of the others - embedding or porting both need this
    # table first, because either way something has to know a room exists.

    def list_rooms(request: Request) -> Response:
        _project_or_404(app, request)
        rows = [
            {
                "id": room.id,
                "kind": room.kind,
                "title": room.title,
                "invite_url": room.invite_url,
                "join_code": room.join_code,
                "opened_at": room.opened_at,
            }
            for room in app.store.rooms_for(request.params["project_id"], request.user_id or "")
        ]
        return json_response({"rooms": rows})

    router.get("/api/projects/{project_id}/rooms", list_rooms)

    def open_room(request: Request) -> Response:
        _project_or_404(app, request)
        payload = request.json()
        room = app.store.open_room(
            request.params["project_id"],
            request.user_id or "",
            str(payload.get("kind", "")),
            str(payload.get("invite_url", "")),
            title=str(payload.get("title", "")),
            join_code=str(payload.get("join_code", "")),
        )
        if room is None:
            raise HTTPError(403, "read-only")
        return json_response({"id": room.id, "kind": room.kind, "invite_url": room.invite_url}, 201)

    router.post("/api/projects/{project_id}/rooms", open_room)

    def close_room(request: Request) -> Response:
        if not app.store.close_room(request.params["room_id"], request.user_id or ""):
            raise HTTPError(404, "not found")
        return json_response({"ok": True})

    router.delete("/api/rooms/{room_id}", close_room)

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
