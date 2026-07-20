"""LAN collaboration server for the Retro board — stdlib ``http.server`` only.

A retro needs the whole team, but the app runs locally in a terminal. So the host
starts a retro and this module spins up a tiny HTTP server on the LAN; teammates
open the printed URL in any browser (no install) and add cards live. We use the
standard-library ``http.server`` — NOT FastAPI/Flask — to match the codebase's
stdlib-only networking ethos (``standup/delivery.py`` uses ``smtplib``/``urllib``).

Design (see plan "Retro Mode"):
  * ``ThreadingHTTPServer`` runs on a background daemon thread; each request gets
    its own thread. The shared :class:`~yeaboi.retro.board.RetroBoard` is the
    single source of truth and is itself lock-guarded.
  * Access is gated by a per-session random token (``secrets.token_urlsafe``)
    checked with ``secrets.compare_digest`` (constant-time). ``GET /`` serves the
    harmless board page; every ``/api/*`` call requires the token.
  * The server binds ``0.0.0.0`` so LAN peers can reach it. This is a LAN-trust
    model — no TLS. Do NOT port-forward it to the public internet.

Concurrency pitfalls, all handled below:
  * ``daemon_threads = True`` — request threads must not outlive the process.
  * ``shutdown()`` must be called from a DIFFERENT thread than ``serve_forever``
    (we call it from the TUI thread) or it deadlocks; follow with ``server_close()``.
  * HTTP/1.1 keep-alive requires ``Content-Length`` on every response or the
    browser hangs — every ``_send_*`` helper sets it.

# See docs: "Guardrails" — token gating / input validation
# See docs: "Daily Standup" — stdlib-only delivery (same ethos)
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import socket
import struct
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from yeaboi.retro.board import RetroBoard
from yeaboi.retro.page import build_board_html
from yeaboi.sharing.access import JoinLimiter as _SharedJoinLimiter
from yeaboi.sharing.access import make_join_code, make_token

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 5173
_PORT_WALK = 20  # try _DEFAULT_PORT .. _DEFAULT_PORT + _PORT_WALK on conflict
_MAX_BODY = 4096  # POST body cap (bytes) — blunt DoS


class JoinLimiter(_SharedJoinLimiter):
    """Retro-compatible wrapper over the shared failed-code limiter."""

    def __init__(self) -> None:
        # Keep the clock lookup late so existing tests and callers can replace
        # ``retro.server.time.monotonic`` deterministically.
        super().__init__(clock=lambda: time.monotonic())


# ---------------------------------------------------------------------------
# LAN IP + share-code encode/decode
# ---------------------------------------------------------------------------


def get_lan_ip() -> str:
    """Best-effort primary LAN IPv4.

    Connecting a UDP socket sends no packet — it just makes the OS pick the
    outbound interface, whose address we then read. Falls back to loopback when
    offline (a host-only retro still works locally).
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        logger.debug("retro: LAN IP detection failed — falling back to loopback")
        return "127.0.0.1"
    finally:
        s.close()


def encode_share_code(ip: str, port: int, token: str) -> str:
    """Pack ip(4) + port(2, big-endian) + token into a grouped base32 share code."""
    packed = socket.inet_aton(ip) + struct.pack(">H", port) + token.encode()
    b32 = base64.b32encode(packed).decode().rstrip("=")
    return "-".join(b32[i : i + 4] for i in range(0, len(b32), 4))


def decode_share_code(code: str) -> tuple[str, int, str]:
    """Inverse of :func:`encode_share_code` → (ip, port, token)."""
    raw = code.replace("-", "").replace(" ", "").upper()
    raw += "=" * (-len(raw) % 8)  # restore base32 padding
    packed = base64.b32decode(raw)
    ip = socket.inet_ntoa(packed[:4])
    port = struct.unpack(">H", packed[4:6])[0]
    token = packed[6:].decode()
    return ip, port, token


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class _RetroHandler(BaseHTTPRequestHandler):
    """Routes board reads/writes. Holds no state — reaches the shared board via ``self.server``."""

    server_version = "ScrumRetro/1"
    protocol_version = "HTTP/1.1"  # keep-alive; every response sets Content-Length

    # Route the default noisy stderr access log into our logger at DEBUG, and never
    # log the query string — it carries the token AND the admin secret.
    def log_request(self, code: object = "-", size: object = "-") -> None:  # noqa: N802 - stdlib signature
        # The default logs ``self.requestline`` (path WITH query) — that would leak
        # the token/admin secret into the log file at DEBUG. Log method + path only.
        logger.debug("retro-http %s %s -> %s", self.command, urlparse(self.path).path, code)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003 - stdlib signature
        logger.debug("retro-http %s", fmt % args if args else fmt)

    @property
    def _board(self) -> RetroBoard:
        return self.server.board  # type: ignore[attr-defined]

    @property
    def _token(self) -> str:
        return self.server.token  # type: ignore[attr-defined]

    @property
    def _admin_token(self) -> str:
        return self.server.admin_token  # type: ignore[attr-defined]

    @property
    def _join_code(self) -> str:
        return self.server.join_code  # type: ignore[attr-defined]

    @property
    def _join_limiter(self) -> JoinLimiter:
        return self.server.join_limiter  # type: ignore[attr-defined]

    def _query(self, key: str) -> str:
        return parse_qs(urlparse(self.path).query).get(key, [""])[0]

    def _authed(self) -> bool:
        return secrets.compare_digest(self._query("token"), self._token)

    def _admin_authed(self, admin: str) -> bool:
        """True iff ``admin`` matches the host's admin secret (constant-time)."""
        return bool(admin) and secrets.compare_digest(admin, self._admin_token)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, self.server.page_html.encode(), "text/html; charset=utf-8")  # type: ignore[attr-defined]
            return
        if path == "/api/state":  # the browser's unified ~1 s poll
            if not self._authed():
                self._send_json(403, {"error": "forbidden"})
                return
            self._send_json(200, self._board.state_snapshot(self._query("pid")))
            return
        if path == "/api/cards":  # legacy/simple cards-only read
            if not self._authed():
                self._send_json(403, {"error": "forbidden"})
                return
            rev, cards = self._board.snapshot()
            self._send_json(200, {"revision": rev, "cards": [asdict(c) for c in cards]})
            return
        if path == "/api/qr":  # invite QR of the join URL (token-gated → no leak)
            if not self._authed():
                self._send_json(403, {"error": "forbidden"})
                return
            self._send_qr()
            return
        self._send_json(404, {"error": "not found"})

    def _send_qr(self) -> None:
        """Render a QR of the token-free join URL (``scheme://<Host header>/``) as inline SVG.

        Using the request's Host header makes the QR correct for both LAN and the
        Cloudflare tunnel automatically. The QR is token-free so scanning it lands
        on the code gate — a scan alone does not grant access; the visitor still
        types the join code. Best-effort — 501 if segno is unavailable.
        """
        host = self.headers.get("Host") or f"{self.server.server_address[0]}:{self.server.server_address[1]}"  # type: ignore[attr-defined]
        url = f"http://{host}/"
        try:
            import io

            import segno

            buf = io.BytesIO()
            segno.make(url, error="m").save(buf, kind="svg", scale=5, dark="#0d1117", light="#ffffff")
            self._send(200, buf.getvalue(), "image/svg+xml")
        except Exception as e:
            logger.warning("retro: QR generation failed: %s", e)
            self._send_json(501, {"error": "qr unavailable"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
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

        # /api/join is the ONLY unauthenticated POST: it exchanges the short join
        # code for the strong token (the code-entry gate). Everything else needs it.
        if path == "/api/join":
            ip = self.client_address[0]
            if self._join_limiter.blocked(ip):
                self._send_json(429, {"error": "too many attempts"})
                return
            code = str(payload.get("code", "")).strip().upper()
            if code and secrets.compare_digest(code, self._join_code):
                self._join_limiter.record_success(ip)
                self._send_json(200, {"ok": True, "token": self._token})
            else:
                self._join_limiter.record_failure(ip)
                self._send_json(403, {"error": "bad code"})
            return

        authed_paths = (
            "/api/cards",
            "/api/react",
            "/api/presence",
            "/api/timer",
            "/api/card/edit",
            "/api/card/delete",
            "/api/card/move",
            "/api/carried/status",
            "/api/admin/broadcast",
            "/api/admin/lock",
        )
        if path not in authed_paths or not self._authed():
            self._send_json(403, {"error": "forbidden"})
            return

        pid = str(payload.get("pid", ""))
        admin = str(payload.get("admin", ""))

        def _state() -> dict:
            return self._board.state_snapshot(pid)

        # ── Admin-only routes (host link holds the admin secret) ──────────────
        # /api/timer is admin-only too — the shared countdown belongs to the host.
        if path in ("/api/admin/broadcast", "/api/admin/lock", "/api/timer"):
            if not self._admin_authed(admin):
                self._send_json(403, {"error": "admin only"})
                return

        if path == "/api/admin/broadcast":
            ok, applied = True, False
            if "theme" in payload:
                ok = self._board.set_broadcast_theme(str(payload.get("theme", ""))) and ok
                applied = True
            music = payload.get("music")
            if isinstance(music, dict):
                ok = (
                    self._board.set_broadcast_music(playing=bool(music.get("playing")), channel=music.get("channel", 0))
                    and ok
                )
                applied = True
            # An empty/malformed broadcast (neither theme nor music) is a client error,
            # not a silent success.
            self._send_json(200 if (ok and applied) else 400, {"ok": ok and applied, "state": _state()})
            return

        if path == "/api/admin/lock":
            self._board.set_locked(bool(payload.get("locked")))
            self._send_json(200, {"ok": True, "state": _state()})
            return

        if path == "/api/cards":
            card = self._board.add_card(
                grid=str(payload.get("grid", "")),
                text=str(payload.get("text", "")),
                author=str(payload.get("author", "")),
                pid=pid,
            )
            if card is None:
                self._send_json(400, {"error": "invalid card"})
                return
            self._send_json(200, {"ok": True, "card": asdict(card), "state": _state()})
            return

        if path == "/api/react":
            now_set = self._board.toggle_reaction(str(payload.get("card_id", "")), str(payload.get("emoji", "")), pid)
            self._send_json(200, {"ok": True, "reacted": now_set, "state": _state()})
            return

        if path == "/api/card/edit":
            ok = self._board.edit_card(str(payload.get("card_id", "")), str(payload.get("text", "")), pid)
            self._send_json(200 if ok else 403, {"ok": ok, "state": _state()})
            return

        if path == "/api/card/delete":
            ok = self._board.delete_card(str(payload.get("card_id", "")), pid)
            self._send_json(200 if ok else 403, {"ok": ok, "state": _state()})
            return

        if path == "/api/card/move":
            try:
                index = int(payload.get("index", 0))
            except (TypeError, ValueError):
                index = 0
            ok = self._board.move_card(str(payload.get("card_id", "")), str(payload.get("grid", "")), index, pid)
            self._send_json(200 if ok else 400, {"ok": ok, "state": _state()})
            return

        if path == "/api/carried/status":
            # Set the progress status on a carried-over action item (last sprint's
            # actions). Open to any authed peer, like moving a card — reviewing the
            # prior actions is collaborative. board validates the status enum.
            ok = self._board.set_carried_status(str(payload.get("item_id", "")), str(payload.get("status", "")))
            self._send_json(200 if ok else 400, {"ok": ok, "state": _state()})
            return

        if path == "/api/presence":
            # The ~1 s tick: record presence/typing AND return the live state in one round-trip.
            self._board.heartbeat(
                pid,
                name=str(payload.get("name", "")),
                avatar=str(payload.get("avatar", "")),
                typing_grid=str(payload.get("typing_grid", "")),
            )
            self._send_json(200, _state())
            return

        # /api/timer
        if str(payload.get("action", "")) == "start":
            try:
                self._board.start_timer(int(payload.get("duration", 0)))
            except (TypeError, ValueError):
                self._send_json(400, {"error": "bad duration"})
                return
        else:
            self._board.stop_timer()
        self._send_json(200, {"ok": True, "state": _state()})


# ---------------------------------------------------------------------------
# Server lifecycle wrapper
# ---------------------------------------------------------------------------


class RetroServer:
    """Owns the ``ThreadingHTTPServer`` + its background thread for one retro."""

    def __init__(self, board: RetroBoard, *, port: int = _DEFAULT_PORT) -> None:
        self.board = board
        self.token = make_token()
        # A second, stronger secret that ONLY rides in the host's private link
        # (:attr:`url`). Whoever opens that link becomes the retro's admin (music /
        # theme / timer / board-lock). It is never in the shared join flow, the
        # share code, or the tunnel URL — so a join-code teammate is never an admin.
        self.admin_token = make_token()
        self.join_code = make_join_code()
        self.join_limiter = JoinLimiter()
        self.ip = get_lan_ip()
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        """The host's private direct link (carries the token — do not share).

        This is the host's own convenience link and the value logged on startup;
        anyone opening it is let straight in AND granted admin controls (the
        ``admin`` secret). Teammates get :attr:`share_url` instead and must enter
        the join code — they never receive the admin secret.
        """
        return f"http://{self.ip}:{self.port}/?token={self.token}&admin={self.admin_token}"

    @property
    def share_url(self) -> str:
        """The token-free URL to hand out — recipients must type the join code."""
        return f"http://{self.ip}:{self.port}/"

    @property
    def share_code(self) -> str:
        """The full ip+port+token share code (decodable by :func:`decode_share_code`)."""
        return encode_share_code(self.ip, self.port, self.token)

    @property
    def display_code(self) -> str:
        """The short, typable join code shown in the TUI (resolved by ``/api/join``)."""
        return self.join_code

    def start(self) -> None:
        """Bind ``0.0.0.0`` (walking ports on conflict) and serve on a daemon thread."""
        # The served page is token-FREE: GET / is unauthenticated, so baking the
        # token in would leak it to any LAN peer. The client reads the token from
        # its own URL (?token=) or obtains it via the join code (/api/join).
        page_html = build_board_html()
        httpd: ThreadingHTTPServer | None = None
        for candidate in range(self.port, self.port + _PORT_WALK):
            try:
                # Bind all interfaces so LAN teammates can reach the board (see module docstring).
                httpd = ThreadingHTTPServer(("0.0.0.0", candidate), _RetroHandler)  # noqa: S104
                self.port = candidate
                break
            except OSError:
                continue
        if httpd is None:
            raise OSError(f"no free port in {self.port}..{self.port + _PORT_WALK}")

        httpd.daemon_threads = True  # request threads die with the process
        # Attach shared state to the server object so the stateless handler can reach it.
        httpd.board = self.board  # type: ignore[attr-defined]
        httpd.token = self.token  # type: ignore[attr-defined]
        httpd.admin_token = self.admin_token  # type: ignore[attr-defined]
        httpd.join_code = self.join_code  # type: ignore[attr-defined]
        httpd.join_limiter = self.join_limiter  # type: ignore[attr-defined]
        httpd.page_html = page_html  # type: ignore[attr-defined]
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="retro-http", daemon=True)
        self._thread.start()
        logger.info("retro server up on %s (token=%s…)", self.url.split("?")[0], self.token[:6])

    def stop(self) -> None:
        """Stop serving and free the socket. Safe to call from the TUI thread."""
        if self._httpd is None:
            return
        try:
            # shutdown() must run on a different thread than serve_forever() (which
            # is on retro-http) — we're on the TUI thread here, so this is safe.
            self._httpd.shutdown()
            self._httpd.server_close()
        finally:
            self._httpd = None
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        logger.info("retro server stopped")
