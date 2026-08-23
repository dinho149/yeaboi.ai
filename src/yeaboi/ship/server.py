"""Collaboration server for the Ship board — stdlib ``http.server`` only.

The same loopback-bound, tunnel-fronted board the retro and poker modes run,
pointed at one supervised ship run so teammates can *watch* it: the phase
checklist, the agent's live activity, the diff, and the validation verdict, over
the authenticated ``/api/state`` long poll.

**Read-only, by phase.** This server lets a visitor watch and announce presence;
it does not resolve the approval gate. The gate is still driven from the host's
terminal (``ui/mode_select/_ship.py``) — a browser tier that can approve, reject
and steer is deliberately a later step, because a remote approve pushes to
``origin`` and a remote steer becomes the agent's next prompt. The admin secret
is minted here so that step is only route-gating, not new plumbing.

Design mirrors :mod:`yeaboi.retro.server` exactly — see it for the concurrency
pitfalls (``daemon_threads``, ``shutdown()`` from another thread, keep-alive
``Content-Length``); the notes are not repeated here.

# See docs: "Guardrails" — token gating / input validation
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from yeaboi.redaction import log_safe
from yeaboi.sharing.access import JoinLimiter as _SharedJoinLimiter
from yeaboi.sharing.access import (
    client_key,
    invite_payload,
    invite_url,
    make_join_code,
    make_token,
    participant_url,
    secret_equal,
)
from yeaboi.sharing.events import ChangeWatcher, EventHub
from yeaboi.sharing.identity import effective_pid, enforce_identity, identity_required, verified_user
from yeaboi.sharing.live import parse_wait, serve_state
from yeaboi.ship.board import ShipBoard
from yeaboi.ship.page import build_board_html
from yeaboi.web.security import BOARD_CSP, send_document

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 5473
_PORT_WALK = 20
_MAX_BODY = 4096


class JoinLimiter(_SharedJoinLimiter):
    """Ship-compatible wrapper over the shared failed-code limiter."""

    def __init__(self) -> None:
        super().__init__(clock=lambda: time.monotonic())


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class _ShipHandler(BaseHTTPRequestHandler):
    """Routes board reads. Holds no state — reaches the shared board via ``self.server``."""

    server_version = "ScrumShip/1"
    protocol_version = "HTTP/1.1"

    def log_request(self, code: object = "-", size: object = "-") -> None:  # noqa: N802 - stdlib signature
        # Never log the query string — it carries the token (and, later, admin).
        logger.debug("ship-http %s %s -> %s", log_safe(self.command), log_safe(urlparse(self.path).path), code)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003 - stdlib signature
        logger.debug("ship-http %s", log_safe(fmt % args if args else fmt))

    @property
    def _board(self) -> ShipBoard:
        return self.server.board  # type: ignore[attr-defined]

    @property
    def _token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

    @property
    def _join_code(self) -> str:
        return self.server.join_code  # type: ignore[attr-defined]

    @property
    def _join_limiter(self) -> JoinLimiter:
        return self.server.join_limiter  # type: ignore[attr-defined]

    def _query(self, key: str) -> str:
        return parse_qs(urlparse(self.path).query).get(key, [""])[0]

    def _authed(self) -> bool:
        # Both factors in the Access tier: the ambient JWT (cookie / edge
        # header) is forgeable by a cross-site form POST, so the unguessable
        # ``?token=`` stays required as the CSRF barrier — same rule as the
        # retro/poker/share servers.
        if identity_required(self) and verified_user(self) is None:
            return False
        return secret_equal(self._query("token"), self._token)

    def _send(self, code: int, body: bytes, content_type: str, *, csp: str | None = None) -> None:
        send_document(self, code, body, content_type, csp=csp)

    def _send_json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, self.server.page_html.encode(), "text/html; charset=utf-8", csp=BOARD_CSP)  # type: ignore[attr-defined]
            return
        if path == "/api/state":
            if not self._authed():
                self._send_json(403, {"error": "forbidden"})
                return
            self._serve_state()
            return
        if path == "/api/qr":
            if not self._authed():
                self._send_json(403, {"error": "forbidden"})
                return
            self._send_qr()
            return
        if path == "/api/invite":
            if not self._authed():
                self._send_json(403, {"error": "forbidden"})
                return
            self._send_invite()
            return
        self._send_json(404, {"error": "not found"})

    def _serve_state(self) -> None:
        """Answer ``GET /api/state``, long-polling when ``?wait=`` is set.

        Long-polling, not SSE: a Cloudflare quick tunnel buffers a streaming
        body until the origin finishes it (see :mod:`yeaboi.sharing.live`).
        """
        serve_state(
            self,
            self.server.event_hub,  # type: ignore[attr-defined]
            lambda: self._board.state_snapshot(effective_pid(self, self._query("pid"))),
            wait_seconds=parse_wait(self._query("wait")),
        )

    def _send_invite(self) -> None:
        """What a teammate needs to join — the link + code, never the host link."""
        fallback = f"{self.server.server_address[0]}:{self.server.server_address[1]}"  # type: ignore[attr-defined]
        self._send_json(
            200,
            invite_payload(self.headers, fallback, self._join_code, self.server.public_url),  # type: ignore[attr-defined]
        )

    def _send_qr(self) -> None:
        """Inline-SVG QR of the one-link invite (token-gated → no leak, best-effort)."""
        fallback = f"{self.server.server_address[0]}:{self.server.server_address[1]}"  # type: ignore[attr-defined]
        url = invite_url(
            participant_url(self.headers, fallback, self.server.public_url),  # type: ignore[attr-defined]
            self._join_code,
        )
        if not url:
            self._send_json(503, {"error": "link not ready"})
            return
        try:
            import io  # noqa: PLC0415

            import segno  # noqa: PLC0415

            buf = io.BytesIO()
            segno.make(url, error="m").save(buf, kind="svg", scale=5, dark="#0d1117", light="#ffffff")
            self._send(200, buf.getvalue(), "image/svg+xml")
        except Exception as e:  # noqa: BLE001 — QR is a convenience, never fatal
            logger.warning("ship: QR generation failed: %s", e)
            self._send_json(501, {"error": "qr unavailable"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            # A non-numeric Content-Length is a client error; answer 400 rather
            # than let the cast raise out of do_POST, and drop the connection —
            # the undeclared body would poison a keep-alive reuse.
            self.close_connection = True
            self._send_json(400, {"error": "bad length"})
            return
        if length > _MAX_BODY:
            self._send_json(413, {"error": "too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"error": "bad json"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "bad json"})
            return

        # /api/join is the ONLY POST that does not require the board token: it
        # exchanges the short join code for it. Everything else needs the token.
        if path == "/api/join":
            # In the Access tier the code gate sits *behind* identity, matching
            # the retro/poker/share servers: only a verified visitor may even
            # attempt a code, so a stranger cannot spend the join-limiter budget
            # or brute the code over the tunnel.
            if identity_required(self) and verified_user(self) is None:
                self._send_json(403, {"error": "verification required"})
                return
            ip = client_key(self, trust_forwarded=bool(getattr(self.server, "public_url", "")))
            if self._join_limiter.blocked(ip):
                self._send_json(429, {"error": "too many attempts"})
                return
            code = str(payload.get("code", "")).strip().upper()
            if code and secret_equal(code, self._join_code):
                self._join_limiter.record_success(ip)
                self._send_json(200, {"ok": True, "token": self._token})
            else:
                self._join_limiter.record_failure(ip)
                self._send_json(403, {"error": "bad code"})
            return

        if path != "/api/presence" or not self._authed():
            self._send_json(403, {"error": "forbidden"})
            return

        # In the Access tier the claimed pid and name are replaced by the
        # verified identity — the same rule as every other board's write path,
        # and what keeps /api/presence's snapshot consistent with /api/state's
        # effective_pid view of "mine".
        pid, name = enforce_identity(self, str(payload.get("pid", "")), str(payload.get("name", "")))
        self._board.heartbeat(pid, name=name, avatar=str(payload.get("avatar", "")))
        if self._query("quiet") == "1":
            self._send_json(200, {"ok": True})
            return
        self._send_json(200, self._board.state_snapshot(pid))


# ---------------------------------------------------------------------------
# Server lifecycle wrapper
# ---------------------------------------------------------------------------


class ShipServer:
    """Owns the ``ThreadingHTTPServer`` + its background thread for one ship board."""

    def __init__(self, board: ShipBoard, *, port: int = _DEFAULT_PORT) -> None:
        self.board = board
        self.token = make_token()
        # A second, stronger secret that only rides in the host's private link
        # (:attr:`url`). Minted now so the driver tier (Phase 4) is route-gating,
        # not new plumbing; on a read-only board it grants nothing extra yet.
        self.admin_token = make_token()
        self.join_code = make_join_code()
        self.join_limiter = JoinLimiter()
        self.port = port
        self.public_url = ""
        # None unless the Cloudflare Access tier is on; see set_access_gate.
        self.access_gate: object | None = None
        self.event_hub = EventHub()
        self._watcher = ChangeWatcher(self.event_hub, self._change_probe, name="ship-live-watch")
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _change_probe(self) -> tuple:
        """The value the watcher diffs to release parked polls.

        ``revision()`` refreshes the cached run from the store and folds its
        status/updated_at into the counter, so a store-side change (diff
        attached, gate resolved by the host) wakes watchers. Presence is a
        separate term so a teammate joining or leaving refreshes the who's-here
        row, but a bare 1 Hz heartbeat within the same set does not.
        """
        return (self.board.revision(), self.board.present_pids())

    def set_access_gate(self, gate: object | None) -> None:
        """Arm Cloudflare Access verification for tunnel-borne requests."""
        self.access_gate = gate
        if self._httpd is not None:
            self._httpd.access_gate = gate  # type: ignore[attr-defined]

    def set_public_url(self, url: str) -> None:
        """Record the tunnel URL and push it to the running server object."""
        self.public_url = url
        if self._httpd is not None:
            self._httpd.public_url = url  # type: ignore[attr-defined]

    @property
    def url(self) -> str:
        """The host's private direct link (carries the token — do not share)."""
        base = self.public_url.rstrip("/") if self.public_url else f"http://127.0.0.1:{self.port}"
        return f"{base}/?token={self.token}&admin={self.admin_token}"

    @property
    def share_url(self) -> str:
        """The token-free URL to hand out — recipients must type the join code."""
        return self.public_url

    @property
    def display_code(self) -> str:
        """The short, typable join code shown in the TUI (resolved by ``/api/join``)."""
        return self.join_code

    def start(self) -> None:
        """Bind loopback (walking ports on conflict) and serve on a daemon thread."""
        page_html = build_board_html(self.board.story_title, self.board.project_name)
        httpd: ThreadingHTTPServer | None = None
        for candidate in range(self.port, self.port + _PORT_WALK):
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", candidate), _ShipHandler)
                self.port = candidate
                break
            except OSError:
                continue
        if httpd is None:
            raise OSError(f"no free port in {self.port}..{self.port + _PORT_WALK}")

        httpd.daemon_threads = True
        httpd.board = self.board  # type: ignore[attr-defined]
        httpd.token = self.token  # type: ignore[attr-defined]
        httpd.admin_token = self.admin_token  # type: ignore[attr-defined]
        httpd.join_code = self.join_code  # type: ignore[attr-defined]
        httpd.join_limiter = self.join_limiter  # type: ignore[attr-defined]
        httpd.page_html = page_html  # type: ignore[attr-defined]
        httpd.event_hub = self.event_hub  # type: ignore[attr-defined]
        httpd.public_url = self.public_url  # type: ignore[attr-defined]
        httpd.access_gate = self.access_gate  # type: ignore[attr-defined]
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="ship-http", daemon=True)
        self._thread.start()
        self._watcher.start()
        logger.info("ship server up on %s (token_len=%d)", self.url.split("?")[0], len(self.token))

    def stop(self) -> None:
        """Stop serving and free the socket. Safe to call from the TUI thread."""
        self._watcher.stop()
        self.event_hub.close()
        if self._httpd is None:
            return
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        finally:
            self._httpd = None
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        logger.info("ship server stopped")
