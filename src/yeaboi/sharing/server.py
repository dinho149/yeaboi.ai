"""Code-gated HTTP server for one immutable, self-contained HTML artifact.

The server binds loopback only: it is not a LAN file server. A Cloudflare quick
tunnel forwards the public HTTPS URL to it while the TUI's sharing view is open.
The public root initially serves a harmless code gate; the artifact is returned
only when the browser presents the strong token obtained from ``/api/join``.

# See docs: "Guardrails" — access control and untrusted browser input
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from yeaboi.sharing.access import JoinLimiter, make_join_code, make_token
from yeaboi.sharing.gate import ARTIFACT_CSP, GATE_CSP, render_gate_page
from yeaboi.web.security import send_document

logger = logging.getLogger(__name__)

_MAX_BODY = 1024


@dataclass(frozen=True)
class ShareDocument:
    """One immutable HTML snapshot exposed by :class:`OutputShareServer`."""

    title: str
    html: str
    source_mode: str


class _OutputHandler(BaseHTTPRequestHandler):
    server_version = "YeaboiShare/1"
    protocol_version = "HTTP/1.1"

    def log_request(self, code: object = "-", size: object = "-") -> None:  # noqa: N802
        logger.debug("output-share-http %s %s -> %s", self.command, urlparse(self.path).path, code)

    def log_message(self, fmt: str, *args: object) -> None:
        logger.debug("output-share-http %s", fmt % args if args else fmt)

    def _query(self, key: str) -> str:
        return parse_qs(urlparse(self.path).query).get(key, [""])[0]

    def _authed(self) -> bool:
        supplied = self._query("token")
        token = self.server.token  # type: ignore[attr-defined]
        return bool(supplied) and secrets.compare_digest(supplied, token)

    def _send(self, code: int, body: bytes, content_type: str, *, csp: str | None = None) -> None:
        send_document(self, code, body, content_type, csp=csp)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path not in ("/", "/index.html"):
            self._json(404, {"error": "not found"})
            return
        if self._authed():
            document = self.server.document  # type: ignore[attr-defined]
            self._send(200, document.html.encode(), "text/html; charset=utf-8", csp=ARTIFACT_CSP)
            return
        # The gate ran without a CSP until now — an omission, not a decision.
        # It is the one page here that executes a bundle *and* talks back to the
        # server, so it is the one that most wanted a policy.
        #
        # `source_mode` is the only thing the gate learns about the share, and
        # the only thing it says: see the module docstring in sharing/gate.py
        # for what stays withheld.
        document = self.server.document  # type: ignore[attr-defined]
        gate = render_gate_page(document.source_mode).encode()
        self._send(200, gate, "text/html; charset=utf-8", csp=GATE_CSP)

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/join":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > _MAX_BODY:
            self._json(413, {"error": "too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, ValueError):
            self._json(400, {"error": "bad json"})
            return
        if not isinstance(payload, dict):
            self._json(400, {"error": "bad json"})
            return
        ip = self.client_address[0]
        limiter = self.server.join_limiter  # type: ignore[attr-defined]
        if limiter.blocked(ip):
            self._json(429, {"error": "too many attempts"})
            return
        code = str(payload.get("code", "")).strip().upper()
        expected = self.server.join_code  # type: ignore[attr-defined]
        if code and secrets.compare_digest(code, expected):
            limiter.record_success(ip)
            self._json(200, {"ok": True, "token": self.server.token})  # type: ignore[attr-defined]
            return
        limiter.record_failure(ip)
        self._json(403, {"error": "bad code"})


class OutputShareServer:
    """Own a loopback HTTP server and background thread for one HTML snapshot."""

    def __init__(self, document: ShareDocument, *, port: int = 0) -> None:
        self.document = document
        self.port = port
        self.token = make_token()
        self.join_code = make_join_code()
        self.join_limiter = JoinLimiter()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    @property
    def display_code(self) -> str:
        return self.join_code

    def start(self) -> None:
        """Bind loopback, choosing an ephemeral port by default, and start serving."""
        if self._httpd is not None:
            return
        httpd = ThreadingHTTPServer(("127.0.0.1", self.port), _OutputHandler)
        httpd.daemon_threads = True
        self.port = int(httpd.server_address[1])
        httpd.document = self.document  # type: ignore[attr-defined]
        httpd.token = self.token  # type: ignore[attr-defined]
        httpd.join_code = self.join_code  # type: ignore[attr-defined]
        httpd.join_limiter = self.join_limiter  # type: ignore[attr-defined]
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="output-share-http", daemon=True)
        self._thread.start()
        logger.info("output share server started (mode=%s, port=%d)", self.document.source_mode, self.port)

    def stop(self) -> None:
        """Stop serving and release the socket; safe and idempotent."""
        if self._httpd is None:
            return
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        finally:
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        logger.info("output share server stopped (mode=%s)", self.document.source_mode)
