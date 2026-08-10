"""The application server.

A ``ThreadingHTTPServer`` like the three ceremonies, and for the same reason:
the standard library is the only web server that ships with every install, and
adding a framework would change what ``pip install yeaboi`` pulls onto a
machine. The difference is what sits on top — a route table instead of an
if-chain, a cookie session instead of a query token, and one place where CSRF is
checked rather than one per handler.

Headers and CSP come from ``web/security.py``, which is the only module allowed
to decide them. This server adds no header of its own beyond cookies.
"""

from __future__ import annotations

import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from yeaboi.app.router import UNSAFE_METHODS, Request, Response, Router, json_response, parse_request
from yeaboi.app.sessions import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, SessionStore
from yeaboi.app.store import AppStore
from yeaboi.web.security import policy, send_document

logger = logging.getLogger(__name__)

#: The shell is a real application: it talks to its own origin and, unlike an
#: export, it is allowed to. Everything else stays as tight as the artifact CSP.
APP_CSP = policy(connect_src="'self'", form_action="'self'")

#: The largest body the app will read. A JSON API has no business accepting
#: more, and an unbounded ``Content-Length`` is a free way to exhaust memory.
MAX_BODY_BYTES = 2 * 1024 * 1024


class AppRequestHandler(BaseHTTPRequestHandler):
    """Adapts the socket to :class:`~yeaboi.app.router.Router`."""

    server_version = "yeaboi"
    sys_version = ""  # do not advertise the Python version

    # ── plumbing ───────────────────────────────────────────────────────

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        logger.debug("%s - %s", self.address_string(), format % args)

    @property
    def _app(self) -> AppServer:
        return self.server.app  # type: ignore[attr-defined]

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return b""
        if length <= 0:
            return b""
        if length > MAX_BODY_BYTES:
            return b""
        return self.rfile.read(length)

    def _handle(self, method: str) -> None:
        request = parse_request(method, self.path, dict(self.headers), self._read_body())
        response = self._app.handle(request)
        csp = response.csp if response.csp is not None else APP_CSP
        send_document(self, response.code, response.body, response.content_type, csp=csp, extra=response.headers)

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib signature
        self._handle("DELETE")


class AppServer:
    """Router + store + sessions, with the request pipeline that joins them.

    Kept separate from the ``ThreadingHTTPServer`` so the whole surface can be
    driven in tests by calling :meth:`handle` with a :class:`Request`, with no
    socket, no port, and no thread. ``tests/unit/test_app_server.py`` never binds
    anything.
    """

    def __init__(
        self,
        store: AppStore | None = None,
        *,
        router: Router | None = None,
        secure_cookies: bool = False,
    ) -> None:
        self.store = store if store is not None else AppStore()
        self.sessions = SessionStore(self.store)
        self.secure_cookies = secure_cookies
        if router is not None:
            self.router = router
        else:
            from yeaboi.app.routes import build_router  # noqa: PLC0415 - avoids an import cycle

            self.router = build_router(self)

    def handle(self, request: Request) -> Response:
        """Resolve the session, enforce CSRF, then dispatch.

        Order matters: CSRF is checked only for a request that *is*
        cookie-authenticated. An unauthenticated POST (sign-in) has no cookie to
        double-submit and must not be rejected for failing to echo one.
        """
        user_id = self.sessions.resolve(request.cookie(SESSION_COOKIE))
        if user_id and request.method in UNSAFE_METHODS and not self._csrf_ok(request):
            return json_response({"error": "csrf check failed"}, 403)
        return self.router.dispatch(
            Request(
                method=request.method,
                path=request.path,
                query=request.query,
                headers=request.headers,
                body=request.body,
                params=request.params,
                user_id=user_id,
            )
        )

    @staticmethod
    def _csrf_ok(request: Request) -> bool:
        sent = request.headers.get(CSRF_HEADER, "") or request.headers.get(CSRF_HEADER.lower(), "")
        cookie = request.cookie(CSRF_COOKIE)
        # `compare_digest` needs both sides non-empty to mean anything: two
        # missing values are equal, which would make "sent nothing" a pass.
        if not sent or not cookie:
            return False
        import secrets  # noqa: PLC0415 - trivial, and keeps the module header short

        return secrets.compare_digest(sent, cookie)


def serve(host: str = "127.0.0.1", port: int = 5599, *, db_path: Path | None = None) -> ThreadingHTTPServer:
    """Bind and return the server. The caller owns ``serve_forever``.

    Returned rather than run so a test, the TUI, and a ``__main__`` can each
    decide about threads — the same shape ``RetroServer`` uses.
    """
    app = AppServer(AppStore(db_path))
    httpd = ThreadingHTTPServer((host, port), AppRequestHandler)
    httpd.app = app  # type: ignore[attr-defined]
    return httpd
