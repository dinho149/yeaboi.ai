"""The desktop backend server.

A ``ThreadingHTTPServer`` like the board servers, and for the same reason: the
standard library is the only web server that ships with every install. What
sits on top differs — a route table instead of an if-chain, and a bearer token
instead of a query token (see ``auth.py``).

Headers come from ``web/security.py``, the only module allowed to decide them.
:class:`AppServer` is kept separate from the HTTP plumbing so the whole
surface can be driven in tests by calling :meth:`AppServer.handle` with a
:class:`Request` — no socket, no port, no thread.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from yeaboi.app.auth import check_bearer
from yeaboi.app.chats import ChatSupervisor
from yeaboi.app.consent import ConsentDesk
from yeaboi.app.events import EventBus
from yeaboi.app.ops import OperationTable
from yeaboi.app.router import Request, Response, Router, parse_request
from yeaboi.app.ships import ShipSupervisor
from yeaboi.app.supervisor import BoardSupervisor
from yeaboi.web.security import policy, send_document, send_headers

logger = logging.getLogger(__name__)

#: JSON responses carry it harmlessly; any future HTML document gets a real
#: policy. Same-origin API calls are all the app ever needs.
APP_CSP = policy(connect_src="'self'", form_action="'none'")

#: The largest body the app will read; an unbounded ``Content-Length`` is a
#: free way to exhaust memory. It has to clear the biggest thing a route
#: advertises — a pasted image, base64'd (``routes_chat.MAX_IMAGE_BYTES``,
#: which grows by a third on the wire) — or the cap here rejects an attachment
#: the handler promised to accept, before the handler ever sees it.
MAX_BODY_BYTES = 8 * 1024 * 1024


class AppRequestHandler(BaseHTTPRequestHandler):
    """Adapts the socket to :class:`AppServer`."""

    server_version = "yeaboi"
    sys_version = ""  # do not advertise the Python version
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        logger.debug("%s - %s", self.address_string(), format % args)

    @property
    def _app(self) -> AppServer:
        return self.server.app  # type: ignore[attr-defined]

    def _read_body(self) -> bytes:
        self._oversize = False
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return b""
        if length <= 0:
            return b""
        if length > MAX_BODY_BYTES:
            # An over-cap body is never drained, so this connection cannot be
            # reused: on HTTP/1.1 the unread megabytes would be parsed as the
            # next request line and fail a call the client did nothing wrong in.
            self._oversize = True
            self.close_connection = True
            return b""
        return self.rfile.read(length)

    def _handle(self, method: str, *, body: bool = True) -> None:
        raw = self._read_body()
        if self._oversize:
            send_document(
                self,
                413,
                json.dumps({"error": f"request body too large (max {MAX_BODY_BYTES // (1024 * 1024)} MB)"}).encode(),
                "application/json",
                csp=APP_CSP,
            )
            return
        request = parse_request(method, self.path, dict(self.headers), raw)
        response = self._app.handle(request)
        if response.stream is not None:
            if not body:
                # HEAD must not open a stream: subscribing costs one of the
                # feed's few slots and nothing would ever read it.
                response.stream.close()
                send_headers(self, response.code, csp=APP_CSP, extra=(("Content-Type", response.content_type),))
                self.end_headers()
                return
            self._write_stream(response)
            return
        if body:
            send_document(
                self, response.code, response.body, response.content_type, csp=APP_CSP, extra=response.headers
            )
            return
        # HEAD: the same headers, including the Content-Length the body would
        # have had, and no body.
        send_headers(
            self,
            response.code,
            csp=APP_CSP,
            extra=(
                ("Content-Type", response.content_type),
                ("Content-Length", str(len(response.body))),
                *response.headers,
            ),
        )
        self.end_headers()

    def _write_stream(self, response: Response) -> None:
        """Write a streaming response (SSE / NDJSON), flushing per chunk.

        The connection closes when the generator ends. A streamed body has no
        ``Content-Length``, so on HTTP/1.1 keep-alive the client would have no
        way to know the last line was the last line — it would keep reading a
        finished run forever.

        A broken pipe means the client went away — the stream generator's
        ``finally`` handles its own cleanup (e.g. the event bus unsubscribe),
        so it is closed, not re-raised.
        """
        self.close_connection = True
        send_headers(
            self,
            response.code,
            csp=APP_CSP,
            extra=(("Content-Type", response.content_type), ("Connection", "close"), *response.headers),
        )
        self.end_headers()
        assert response.stream is not None
        try:
            for chunk in response.stream:
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            logger.info("stream client disconnected")
        finally:
            response.stream.close()

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        self._handle("GET")

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib signature
        self._handle("GET", body=False)

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        self._handle("POST")


class AppServer:
    """Router + auth + the shared services the routes need.

    ``dispatcher`` may be ``None`` (mcp extra missing) — tool routes answer
    503 and everything else keeps working. ``on_shutdown`` is invoked (once)
    when a client POSTs ``/api/shutdown``; the process entry point wires it to
    the HTTP server's own stop.
    """

    def __init__(
        self,
        *,
        token: str,
        dispatcher=None,
        bus: EventBus | None = None,
        ops: OperationTable | None = None,
        router: Router | None = None,
        chats: ChatSupervisor | None = None,
        boards: BoardSupervisor | None = None,
        ships: ShipSupervisor | None = None,
        consent: ConsentDesk | None = None,
        on_shutdown=None,
    ) -> None:
        self.token = token
        self.dispatcher = dispatcher
        self.bus = bus if bus is not None else EventBus()
        self.ops = ops if ops is not None else OperationTable()
        # The one subscription sign-in session (routes_settings) — a running
        # `claude setup-token` child, driven a poll at a time over the API.
        self.signin = None
        self.signin_lock = threading.Lock()
        # The open planning conversations (routes_chat) — sessions live here
        # so a reloaded window rejoins the one it left.
        self.chats = chats if chats is not None else ChatSupervisor()
        # The live boards and open shares (routes_boards) — they outlive every
        # window, and `stop_all` at shutdown is what keeps a tunnel from
        # outliving the app.
        self.boards = boards if boards is not None else BoardSupervisor()
        # The live ship runs (routes_ship) — a run lasts tens of minutes and
        # stops halfway to ask a human, so it must not belong to a window.
        self.ships = ships if ships is not None else ShipSupervisor()
        # The open sandbox-consent requests (routes_consent) — a denial can come
        # from any thread, so the desk drains the queue rather than a handler.
        self.consent = consent if consent is not None else ConsentDesk(self.bus)
        self._on_shutdown = on_shutdown
        self._shutdown_once = threading.Event()
        if router is not None:
            self.router = router
        else:
            from yeaboi.app.registry import build_router  # noqa: PLC0415 - avoids an import cycle

            self.router = build_router(self)

    def handle(self, request: Request) -> Response:
        """Verify the bearer token, then dispatch. The router enforces auth."""
        from dataclasses import replace

        return self.router.dispatch(replace(request, authed=check_bearer(request.headers, self.token)))

    def request_shutdown(self) -> None:
        """Trigger the configured shutdown exactly once, off-thread.

        Off-thread so the handler can finish writing its response before the
        HTTP server stops accepting; once, so a double-POST cannot race two
        teardowns.
        """
        if self._shutdown_once.is_set() or self._on_shutdown is None:
            return
        self._shutdown_once.set()
        threading.Thread(target=self._on_shutdown, name="app-shutdown", daemon=True).start()


def serve(host: str, port: int, *, app: AppServer) -> ThreadingHTTPServer:
    """Bind and return the server. The caller owns ``serve_forever``.

    Returned rather than run so a test and the CLI entry point can each decide
    about threads — the same shape ``RetroServer`` uses.
    """
    httpd = ThreadingHTTPServer((host, port), AppRequestHandler)
    httpd.daemon_threads = True  # an open SSE stream must not block process exit
    httpd.app = app  # type: ignore[attr-defined]
    logger.info("app server bound: %s:%d", *httpd.server_address[:2])
    return httpd
