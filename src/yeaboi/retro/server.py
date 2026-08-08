"""Collaboration server for the Retro board — stdlib ``http.server`` only.

A retro needs the whole team, but the app runs locally in a terminal. So the host
starts a retro and this module spins up a tiny HTTP server; teammates open the
board in any browser (no install) and add cards live. We use the standard-library
``http.server`` — NOT FastAPI/Flask — to match the codebase's stdlib-only
networking ethos (``standup/delivery.py`` uses ``smtplib``/``urllib``).

Design (see plan "Retro Mode"):
  * ``ThreadingHTTPServer`` runs on a background daemon thread; each request gets
    its own thread. The shared :class:`~yeaboi.retro.board.RetroBoard` is the
    single source of truth and is itself lock-guarded.
  * Access is gated by a per-session random token (``secrets.token_urlsafe``)
    checked with ``secrets.compare_digest`` (constant-time). ``GET /`` serves the
    harmless board page; every ``/api/*`` call requires the token.
  * The server binds **loopback only**. Teammates reach it exclusively through a
    Cloudflare quick tunnel (:mod:`yeaboi.retro.tunnel`), which the TUI starts
    automatically when the board opens and which fronts it with HTTPS. There is
    no LAN address to hand out: a board used to advertise its Wi-Fi IP as well,
    which only worked for people in the same room, put the host's admin secret on
    the wire in plaintext, and left the port open to everyone on the network.
    Same model as :mod:`yeaboi.sharing.server` (Share Online).

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

import json
import logging
import secrets
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from yeaboi.redaction import log_safe
from yeaboi.retro.board import RetroBoard
from yeaboi.retro.page import build_board_html
from yeaboi.sharing.access import JoinLimiter as _SharedJoinLimiter
from yeaboi.sharing.access import invite_payload, invite_url, make_join_code, make_token, participant_url
from yeaboi.sharing.events import ChangeWatcher, EventHub
from yeaboi.sharing.live import parse_wait, serve_state
from yeaboi.web.security import BOARD_CSP, send_document

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
        logger.debug("retro-http %s %s -> %s", log_safe(self.command), log_safe(urlparse(self.path).path), code)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003 - stdlib signature
        logger.debug("retro-http %s", log_safe(fmt % args if args else fmt))

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

    def _send(self, code: int, body: bytes, content_type: str, *, csp: str | None = None) -> None:
        # The board used to send a Cache-Control and nothing else, while the
        # share server — whose documents are inert — carried a full protective
        # set. Backwards: this is the surface a stranger with the tunnel URL can
        # type into. Both now go through one place. See yeaboi/web/security.py.
        send_document(self, code, body, content_type, csp=csp)

    def _send_json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            # The policy rides on the document only. It governs what the *page*
            # may load and reach, so putting it on the JSON poll — the busiest
            # response the board sends — would spend bytes on every request for
            # nothing. See BOARD_CSP for what each loose directive buys.
            self._send(200, self.server.page_html.encode(), "text/html; charset=utf-8", csp=BOARD_CSP)  # type: ignore[attr-defined]
            return
        if path == "/api/state":  # the browser's unified live poll
            if not self._authed():
                self._send_json(403, {"error": "forbidden"})
                return
            self._serve_state()
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
        if path == "/api/invite":  # the link + code to hand to a teammate
            if not self._authed():
                self._send_json(403, {"error": "forbidden"})
                return
            self._send_invite()
            return
        self._send_json(404, {"error": "not found"})

    def _send_invite(self) -> None:
        """Answer ``GET /api/invite`` with what a participant needs to join.

        Why an endpoint and not the boot payload: ``GET /`` is unauthenticated, so
        everything in the JSON island is readable by any LAN peer without a token
        (``retro/page.py`` says so at the top of ``board_config``). The join code
        put there would be the gate handing out its own key.

        Gated on the plain token rather than the admin secret. Anyone asking has
        already typed this code to get in, so returning it to them reveals nothing
        — and the alternative, admin-only, would mean the one person who does not
        need the invite is the only one who can copy it.

        The host link is deliberately absent. It carries the admin secret, and
        every participant can read anything this endpoint returns.
        """
        fallback = f"{self.server.server_address[0]}:{self.server.server_address[1]}"  # type: ignore[attr-defined]
        # RetroServer.display_code is an alias for join_code; the handler only
        # ever sees the ThreadingHTTPServer, which carries the latter.
        self._send_json(
            200,
            invite_payload(
                self.headers,
                fallback,
                self._join_code,
                self.server.public_url,  # type: ignore[attr-defined]
            ),
        )

    def _serve_state(self) -> None:
        """Answer ``GET /api/state``, holding the request when ``?wait=`` is set.

        Long-polling, not SSE: a Cloudflare quick tunnel buffers a streaming
        body until the origin finishes it, so an endless response delivers
        nothing to a remote teammate. See :mod:`yeaboi.sharing.live` for the
        experiment that established that. Each response here is complete, so it
        flushes through the edge immediately.
        """
        serve_state(
            self,
            self.server.event_hub,  # type: ignore[attr-defined]
            lambda: self._board.state_snapshot(self._query("pid")),
            wait_seconds=parse_wait(self._query("wait")),
        )

    def _send_qr(self) -> None:
        """Render a QR of the one-link invite as inline SVG.

        Exactly what ``/api/invite`` hands out — the tunnel address once there is
        one, the request's own host before that (see :func:`participant_url`),
        with the join code in the fragment. A phone scanning this is by
        definition not on the host's machine, so encoding the loopback address it
        would otherwise see would produce a QR that resolves to the scanner's own
        device.

        The code is *in* the QR now, so a scan lands on the board rather than on
        the gate. That is the point — the alternative is asking someone holding a
        phone to retype eight characters they can see on a wall. It is not the
        disclosure it looks like: this endpoint is token-gated, so the only
        people who can see this QR are people already inside, and the code was
        already rendered in plain text beside it. What it does change is that a
        screenshot of the invite panel is now machine-readable entry, which the
        panel's own copy says out loud.

        Still token-free in the sense that matters: no board *token* is encoded,
        so the scanner goes through ``POST /api/join`` and the limiter like
        everyone else. Best-effort — 501 if segno is unavailable, 503 before
        there is any URL to encode.
        """
        fallback = f"{self.server.server_address[0]}:{self.server.server_address[1]}"  # type: ignore[attr-defined]
        # invite_url passes "" straight through, so the no-tunnel-yet 503 below
        # still fires on exactly the same condition it always did.
        url = invite_url(
            participant_url(self.headers, fallback, self.server.public_url),  # type: ignore[attr-defined]
            self._join_code,
        )
        if not url:
            # No tunnel yet. A QR of nothing is worse than no QR: it scans to
            # something, and the something would be this machine.
            self._send_json(503, {"error": "link not ready"})
            return
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
            # ?quiet=1: a client on the long-poll already gets state the moment
            # anything changes, so echoing ~40 KB back on every heartbeat is pure
            # waste. It still has to send the heartbeat itself — presence and
            # typing are carried by this request, not by /api/state.
            if self._query("quiet") == "1":
                self._send_json(200, {"ok": True})
                return
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
        # theme / timer / board-lock). It is never in the shared join flow or the
        # participant link — so a join-code teammate is never an admin.
        self.admin_token = make_token()
        self.join_code = make_join_code()
        self.join_limiter = JoinLimiter()
        self.port = port
        # The Cloudflare tunnel URL, once the TUI has one. Empty until then, and
        # empty forever if the tunnel could not start — which is exactly the state
        # in which this board has no shareable address at all.
        self.public_url = ""
        # Live-update plumbing. Built here rather than in start() so stop() is
        # safe on a server that was never started.
        self.event_hub = EventHub()
        self._watcher = ChangeWatcher(self.event_hub, self._change_probe, name="retro-live-watch")
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _change_probe(self) -> tuple:
        """The value the watcher diffs to decide whether to release parked polls.

        ``revision`` alone is not enough: presence and typing deliberately do NOT
        bump it (heartbeats fire ~1/s and bumping would defeat change detection —
        see :meth:`RetroBoard.heartbeat`). Without the two lists here, the
        who's-here row and the "… is typing" hint would only refresh when
        something unrelated happened to change.
        """
        # revision() is a METHOD on both boards, not a property — calling it is
        # load-bearing. Comparing the bound method instead always reports "equal"
        # and silently blinds the watcher to every card, timer and lock change.
        return (self.board.revision(), self.board.presence_list(), self.board.typing_list())

    def set_public_url(self, url: str) -> None:
        """Record the tunnel URL, and push it to the running server object.

        Two writes because the handler never sees ``self``: like ``token`` and
        ``join_code``, it reaches shared state through the ``ThreadingHTTPServer``
        instance (see :meth:`start`). Setting only the attribute here would leave
        ``/api/invite`` still deriving its answer from the request — which, on a
        loopback bind, is the host's own ``127.0.0.1``.
        """
        self.public_url = url
        if self._httpd is not None:
            self._httpd.public_url = url  # type: ignore[attr-defined]

    @property
    def url(self) -> str:
        """The host's private direct link (carries the token — do not share).

        This is the host's own convenience link and the value logged on startup;
        anyone opening it is let straight in AND granted admin controls (the
        ``admin`` secret). Teammates get :attr:`share_url` instead and must enter
        the join code — they never receive the admin secret.

        Over the tunnel once there is one, so the host can drive their own board
        from a second device and the admin secret travels under HTTPS rather than
        in the clear. Loopback before that — usable immediately, by the host only.
        """
        base = self.public_url.rstrip("/") if self.public_url else f"http://127.0.0.1:{self.port}"
        return f"{base}/?token={self.token}&admin={self.admin_token}"

    @property
    def share_url(self) -> str:
        """The token-free URL to hand out — recipients must type the join code.

        Empty until the tunnel is up. There is no second answer to fall back on:
        the server binds loopback, so the only address that means anything to a
        teammate is the tunnel's. Callers must render the waiting state rather
        than a link.
        """
        return self.public_url

    @property
    def display_code(self) -> str:
        """The short, typable join code shown in the TUI (resolved by ``/api/join``)."""
        return self.join_code

    def start(self) -> None:
        """Bind loopback (walking ports on conflict) and serve on a daemon thread."""
        # The served page is token-FREE: GET / is unauthenticated, so baking the
        # token in would leak it to anyone who reaches the board. The client reads
        # the token from its own URL (?token=) or via the join code (/api/join).
        page_html = build_board_html(self.board.sprint_name, self.board.project_name)
        httpd: ThreadingHTTPServer | None = None
        for candidate in range(self.port, self.port + _PORT_WALK):
            try:
                # Loopback only. cloudflared runs on this same machine and forwards
                # to http://localhost:<port>, so the tunnel is unaffected — but the
                # board stops being reachable by anyone else on the network, and the
                # OS stops asking to accept incoming connections.
                httpd = ThreadingHTTPServer(("127.0.0.1", candidate), _RetroHandler)
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
        httpd.event_hub = self.event_hub  # type: ignore[attr-defined]
        # Always present so the invite/QR handlers can read it unconditionally;
        # set_public_url() fills it in when the tunnel comes up.
        httpd.public_url = self.public_url  # type: ignore[attr-defined]
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="retro-http", daemon=True)
        self._thread.start()
        self._watcher.start()  # begins releasing parked long-polls on board changes
        # Never log any part of the token — even a 6-char prefix is real
        # entropy loss on a short join token, and truncation happens before
        # the redaction layer could catch it.
        logger.info("retro server up on %s (token_len=%d)", self.url.split("?")[0], len(self.token))

    def stop(self) -> None:
        """Stop serving and free the socket. Safe to call from the TUI thread."""
        # Retire the watcher and wake every parked request BEFORE touching the
        # socket: daemon_threads = True means shutdown() never joins handler
        # threads, so a request held on the hub for its 25 s deadline would
        # otherwise linger holding a thread until the process exits.
        self._watcher.stop()
        self.event_hub.close()
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
