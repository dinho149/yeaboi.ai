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

from yeaboi.app.auth import Deliverer, InsecureDelivererError, LogDeliverer, LoginTokens
from yeaboi.app.router import UNSAFE_METHODS, Request, Response, Router, json_response, parse_request
from yeaboi.app.sessions import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, SessionStore
from yeaboi.app.store import AppStore
from yeaboi.web.security import policy, send_document, send_headers

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

    def _handle(self, method: str, *, body: bool = True) -> None:
        peer = self.client_address[0] if isinstance(self.client_address, tuple) else ""
        request = parse_request(method, self.path, dict(self.headers), self._read_body(), client_host=str(peer))
        response = self._app.handle(request)
        csp = response.csp if response.csp is not None else APP_CSP
        if body:
            send_document(self, response.code, response.body, response.content_type, csp=csp, extra=response.headers)
            return
        # HEAD: the same headers, including the Content-Length the body would
        # have had, and no body. Built with send_headers rather than by adding
        # a flag to send_document, because web/security.py is the security
        # workstream's file and one caller's convenience is a poor reason to
        # widen the one place every response's headers come from.
        send_headers(
            self,
            response.code,
            csp=csp,
            extra=(
                ("Content-Type", response.content_type),
                ("Content-Length", str(len(response.body))),
                *response.headers,
            ),
        )
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        self._handle("GET")

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib signature
        """Answer HEAD as GET-without-a-body.

        Monitors and load balancers probe with HEAD, and the stdlib handler
        answers 501 for any verb it has no method for — so a health check that
        works in a browser reported the service as broken.
        """
        self._handle("GET", body=False)

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
        deliverer: Deliverer | None = None,
    ) -> None:
        self.store = store if store is not None else AppStore()
        self.sessions = SessionStore(self.store)
        self.logins = LoginTokens(self.store)
        self.secure_cookies = secure_cookies
        # `secure_cookies` means the deployment believes it is behind TLS,
        # which is the closest available signal for "not a laptop". Writing
        # sign-in links to a log there would put a live credential in whatever
        # ships logs off the box, so it is refused rather than warned about.
        if deliverer is None and secure_cookies:
            raise InsecureDelivererError(
                "refusing to log sign-in links in a secure deployment - pass a real Deliverer"
            )
        self.deliverer: Deliverer = deliverer if deliverer is not None else LogDeliverer()
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
                client_host=request.client_host,
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


def build_deliverer() -> Deliverer | None:
    """The mail deliverer, when the environment has enough to build one.

    ``None`` means "fall back to the dev deliverer", which is correct on a
    laptop and refused by :class:`AppServer` when cookies are marked secure. A
    misconfiguration is logged rather than raised: a server that will not start
    because email is half-configured is worse than one that starts and says
    sign-in cannot be delivered yet.
    """
    import os  # noqa: PLC0415

    base_url = os.getenv("YEABOI_APP_BASE_URL", "").strip()
    if not base_url:
        return None
    try:
        from yeaboi.app.auth import SmtpDeliverer  # noqa: PLC0415

        return SmtpDeliverer(base_url)
    except ValueError as exc:
        logger.warning("sign-in email is not configured (%s); falling back to the dev deliverer", exc)
        return None


def serve(
    host: str = "127.0.0.1",
    port: int = 5599,
    *,
    db_path: Path | None = None,
    secure_cookies: bool = False,
) -> ThreadingHTTPServer:
    """Bind and return the server. The caller owns ``serve_forever``.

    Returned rather than run so a test, the TUI, and a ``__main__`` can each
    decide about threads — the same shape ``RetroServer`` uses.
    """
    app = AppServer(AppStore(db_path), secure_cookies=secure_cookies, deliverer=build_deliverer())
    httpd = ThreadingHTTPServer((host, port), AppRequestHandler)
    httpd.app = app  # type: ignore[attr-defined]
    return httpd
