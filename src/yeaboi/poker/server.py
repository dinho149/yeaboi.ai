"""LAN collaboration server for the Poker board — stdlib ``http.server`` only.

Planning poker needs the whole team, but the app runs locally in a terminal. So
the host starts a session and this module spins up a tiny HTTP server on the
LAN; teammates open the printed URL in any browser (no install) and vote live.
Standard-library ``http.server`` — NOT FastAPI/Flask — to match the codebase's
stdlib-only networking ethos (same as retro/server.py, which this mirrors).

Design (identical to the retro blueprint):
  * ``ThreadingHTTPServer`` on a background daemon thread; each request gets its
    own thread. The shared :class:`~yeaboi.poker.board.PokerBoard` is the single
    source of truth and is itself lock-guarded.
  * Access is gated by a per-session random token (``secrets.token_urlsafe``)
    checked with ``secrets.compare_digest`` (constant-time). ``GET /`` serves
    the harmless page; every ``/api/*`` call requires the token. Admin routes
    additionally require the admin secret that only rides in the host's link.
  * The server binds ``0.0.0.0`` so LAN peers can reach it. LAN-trust model —
    no TLS. Do NOT port-forward it to the public internet.

Poker-specific threading rule: **tracker writes run synchronously in the
per-request handler thread** (finalize/edit — the admin must know the write
succeeded before the session advances; other participants' polls are unaffected
because every request has its own thread), while the **AI perspective runs on a
worker thread** (an LLM call can take 10-30 s; the result lands on the board
and every client picks it up on its next poll). Duel ("open the floor")
transcription follows the same pattern: a ``poker-duel-stt`` worker thread
transcribes the captured audio (local Whisper can take a while, especially the
first model download) and lands the transcript via ``set_duel_transcript``.
The board lock is never held across any of this I/O. The live audio hardware
(:class:`_DuelCapture`) is owned by the SERVER, never the board — the board is
pure snapshot-able state; a recorder holds an open mic stream.

# See docs: "Guardrails" — token gating / input validation
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from yeaboi.poker import tickets as tickets_mod
from yeaboi.poker.board import PokerBoard
from yeaboi.poker.page import build_poker_html

# Reuse retro's LAN/share-code primitives verbatim — one implementation, two modes.
from yeaboi.retro.server import encode_share_code, get_lan_ip
from yeaboi.sharing.access import JoinLimiter as _SharedJoinLimiter
from yeaboi.sharing.access import make_join_code, make_token
from yeaboi.sharing.events import ChangeWatcher, EventHub
from yeaboi.sharing.live import parse_wait, serve_state

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 5273  # clear of retro's 5173..5193 walk range (see config.py)
_PORT_WALK = 20
_MAX_BODY = 8192  # POST body cap (bytes) — ticket description edits can be longer than retro cards
# Duel audio uploads bypass _MAX_BODY with their own cap: ~90 s of browser
# opus is ~0.3 MB; 4 MB leaves headroom for Safari's fatter mp4 blobs.
_MAX_AUDIO_BODY = 4 * 1024 * 1024
# After the floor closes, a duelist's browser still has to stop its recorder
# and upload the final blob — accept uploads for this many extra seconds.
_DUEL_UPLOAD_GRACE = 5.0


class JoinLimiter(_SharedJoinLimiter):
    """Poker-compatible wrapper over the shared failed-code limiter."""

    def __init__(self) -> None:
        # Late clock lookup so tests can replace ``poker.server.time.monotonic``.
        super().__init__(clock=lambda: time.monotonic())


# ---------------------------------------------------------------------------
# Duel audio capture
# ---------------------------------------------------------------------------


class _DuelCapture:
    """Audio for one duel: the host-mic Recorder plus uploaded browser segments.

    Owned by :class:`PokerServer` (attached to the httpd like the board/token)
    — NEVER by the board, which is pure lock-guarded state; this object holds a
    live hardware stream and accumulating audio buffers. All methods are
    thread-safe; the actual mic start/stop I/O happens outside the lock's
    critical work where possible. Audio bytes are transcribed and discarded —
    never persisted, never logged (counts only).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._recorder = None  # yeaboi.voice.Recorder while the host mic is hot
        self._live = False
        self._closed_at: float | None = None  # monotonic close time (grace clock)
        self._segments: dict[str, tuple[int, bytes]] = {}  # role -> (turn_no, blob)

    def start(self) -> str:
        """Begin a capture round; try to start the host mic.

        Returns "" on success or a short human-readable reason when the host
        mic could not start (voice extra missing, mic/permission error). The
        duel proceeds either way — duelists' browser mics may still cover it.
        """
        from yeaboi.voice import is_voice_available

        with self._lock:
            self._segments = {}
            self._closed_at = None
            self._live = True
        available, reason = is_voice_available()
        if not available:
            logger.info("poker: duel host mic skipped — %s", reason)
            return reason
        try:
            from yeaboi.voice import Recorder

            recorder = Recorder()
        except Exception as exc:  # mic permission / device errors must never 500
            logger.warning("poker: duel host mic failed to start: %s", exc)
            return "microphone could not start (see logs)"
        with self._lock:
            self._recorder = recorder
        return ""

    def _accepting_locked(self) -> bool:
        if self._live:
            return True
        return self._closed_at is not None and (time.monotonic() - self._closed_at) <= _DUEL_UPLOAD_GRACE

    def accepting(self) -> bool:
        """True while the duel is live or within the post-close upload grace."""
        with self._lock:
            return self._accepting_locked()

    def add_segment(self, role: str, turn_no: int, data: bytes) -> bool:
        """Store one duelist's browser recording (keyed by role → a retry overwrites)."""
        if role not in ("low", "high") or not data or len(data) > _MAX_AUDIO_BODY:
            return False
        with self._lock:
            if not self._accepting_locked():
                return False
            self._segments[role] = (int(turn_no), bytes(data))
        logger.info("poker: duel audio segment stored — role=%s bytes=%d", role, len(data))
        return True

    def close(self) -> bytes:
        """Stop the host mic and return its WAV take; starts the upload grace clock."""
        with self._lock:
            self._live = False
            self._closed_at = time.monotonic()
            recorder, self._recorder = self._recorder, None
        if recorder is None:
            return b""
        try:
            return recorder.stop()
        except Exception as exc:
            logger.warning("poker: duel host mic stop failed: %s", exc)
            return b""

    def take_segments(self) -> dict[str, tuple[int, bytes]]:
        """Hand the uploaded browser segments to the STT worker (single consumer)."""
        with self._lock:
            segments, self._segments = self._segments, {}
            return segments

    def abort(self) -> None:
        """Stop the mic and discard everything (re-vote / ticket change / shutdown)."""
        with self._lock:
            self._live = False
            self._closed_at = None
            self._segments = {}
            recorder, self._recorder = self._recorder, None
        if recorder is not None:
            try:
                recorder.stop()
            except Exception:  # already-closed streams are fine — we're discarding anyway
                logger.debug("poker: duel capture abort — recorder stop failed", exc_info=True)
        logger.info("poker: duel capture aborted")


def _run_ai(board: PokerBoard) -> None:
    """Run the AI perspective synchronously on the calling worker thread.

    Shared by the admin AI button's ``poker-ai`` worker and the duel STT
    worker's auto-trigger. Reads the round atomically off the board (including
    any finished duel transcript), lands the result via ``set_ai_note``, and
    never raises — a dead worker thread would leave the pending flag stuck.
    """
    try:
        from yeaboi.poker.engine import get_poker_perspective

        ticket, votes = board.current_ticket_and_votes()
        # project_name scopes the cross-mode history gather (retro/standup
        # reads are project-first) — see poker/context.py.
        result = get_poker_perspective(
            ticket or {},
            votes,
            project_name=board.project_name,
            debate_transcript=board.current_duel_transcript(),
        )
        note = result.get("note", "")
        if result.get("warnings"):
            note = f"{note}\n({result['warnings'][0]})" if note else result["warnings"][0]
        board.set_ai_note(
            note,
            result.get("suggested_points"),
            confidence=result.get("confidence", ""),
            evidence=tuple(result.get("evidence") or ()),
        )
    except Exception as exc:  # engine never raises, but the thread must never die loudly
        logger.warning("poker: AI perspective worker failed: %s", exc)
        board.set_ai_note("AI perspective failed — see logs.", None)


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------


class _PokerHandler(BaseHTTPRequestHandler):
    """Routes votes/admin actions. Holds no state — reaches the shared board via ``self.server``."""

    server_version = "ScrumPoker/1"
    protocol_version = "HTTP/1.1"  # keep-alive; every response sets Content-Length

    # Route the default noisy stderr access log into our logger at DEBUG, and never
    # log the query string — it carries the token AND the admin secret.
    def log_request(self, code: object = "-", size: object = "-") -> None:  # noqa: N802 - stdlib signature
        logger.debug("poker-http %s %s -> %s", self.command, urlparse(self.path).path, code)

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003 - stdlib signature
        logger.debug("poker-http %s", fmt % args if args else fmt)

    @property
    def _board(self) -> PokerBoard:
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

    @property
    def _capture(self) -> _DuelCapture:
        return self.server.duel_capture  # type: ignore[attr-defined]

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
            # Token-FREE page: GET / is unauthenticated, so baking the token into
            # the HTML would leak it to any LAN peer (see retro/server.py).
            self._send(200, self.server.page_html.encode(), "text/html; charset=utf-8")  # type: ignore[attr-defined]
            return
        if path == "/api/state":  # the browser's unified live poll
            if not self._authed():
                self._send_json(403, {"error": "forbidden"})
                return
            self._serve_state()
            return
        if path == "/api/ticket":  # read-only peek — any token-holder may read any ticket
            if not self._authed():
                self._send_json(403, {"error": "forbidden"})
                return
            view = self._board.ticket_view(self._query("i"))
            if view is None:  # board re-validated the index: garbage/out-of-range
                self._send_json(404, {"error": "not found"})
                return
            self._send_json(200, view)
            return
        if path == "/api/qr":  # invite QR of the join URL (token-gated → no leak)
            if not self._authed():
                self._send_json(403, {"error": "forbidden"})
                return
            self._send_qr()
            return
        self._send_json(404, {"error": "not found"})

    def _serve_state(self) -> None:
        """Answer ``GET /api/state``, holding the request when ``?wait=`` is set.

        Same contract as retro's — see :mod:`yeaboi.sharing.live` for why this is
        long-polling rather than SSE. Vote secrecy is preserved for free: every
        response is built by ``state_snapshot(pid)``, the same function the plain
        poll uses, so a waiting client can never see more than a polling one.
        """
        serve_state(
            self,
            self.server.event_hub,  # type: ignore[attr-defined]
            lambda: self._board.state_snapshot(self._query("pid")),
            wait_seconds=parse_wait(self._query("wait")),
        )

    def _send_qr(self) -> None:
        """Render a QR of the token-free join URL as inline SVG (see retro/server.py).

        The Host header keeps it correct over both LAN and the Cloudflare tunnel;
        the QR is token-free so scanning it lands on the code gate.
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
            logger.warning("poker: QR generation failed: %s", e)
            self._send_json(501, {"error": "qr unavailable"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        if path == "/api/duel/audio":
            # Raw audio bytes, NOT JSON — routed before the JSON/size logic with
            # its own (much larger) body cap. pid/turn ride as query params: the
            # body is opaque, and the query string is already the established
            # token channel that log_request never logs.
            self._duel_audio(length)
            return
        if length > _MAX_BODY:
            # The oversized body stays unread — close the keep-alive connection
            # so the leftover bytes can't desync the next request on it.
            self.close_connection = True
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
            "/api/presence",
            "/api/vote",
            "/api/vote/clear",
            "/api/timer",
            "/api/admin/reveal",
            "/api/admin/revote",
            "/api/admin/goto",
            "/api/admin/finalize",
            "/api/admin/ticket/edit",
            "/api/admin/ai",
            "/api/admin/duel/open",
            "/api/admin/duel/next",
            "/api/admin/duel/close",
            "/api/duel/mic",
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
        if path.startswith("/api/admin/") or path == "/api/timer":
            if not self._admin_authed(admin):
                self._send_json(403, {"error": "admin only"})
                return

        if path == "/api/presence":
            # The ~1 s tick: record presence AND return the live state in one round-trip.
            self._board.heartbeat(pid, name=str(payload.get("name", "")), avatar=str(payload.get("avatar", "")))
            # ?quiet=1: a client on the long-poll already gets state pushed to it,
            # so echoing the whole snapshot back on every heartbeat is waste. It
            # still has to send the heartbeat — presence rides on this request,
            # not on the stream.
            if self._query("quiet") == "1":
                self._send_json(200, {"ok": True})
                return
            self._send_json(200, _state())
            return

        if path == "/api/vote":
            ok = self._board.cast_vote(pid, str(payload.get("value", "")))
            self._send_json(200 if ok else 400, {"ok": ok, "state": _state()})
            return

        if path == "/api/vote/clear":
            ok = self._board.clear_vote(pid)
            self._send_json(200 if ok else 400, {"ok": ok, "state": _state()})
            return

        if path == "/api/admin/reveal":
            ok = self._board.reveal()
            self._send_json(200 if ok else 400, {"ok": ok, "state": _state()})
            return

        if path == "/api/admin/revote":
            # A new round invalidates any duel in flight — kill the mic first.
            self._capture.abort()
            ok = self._board.restart_vote()
            self._send_json(200 if ok else 400, {"ok": ok, "state": _state()})
            return

        if path == "/api/admin/goto":
            self._capture.abort()
            ok = self._board.goto_ticket(payload.get("index", -1))
            self._send_json(200 if ok else 400, {"ok": ok, "state": _state()})
            return

        if path == "/api/admin/finalize":
            self._finalize(payload, pid)
            return

        if path == "/api/admin/ticket/edit":
            self._ticket_edit(payload, pid)
            return

        if path == "/api/admin/ai":
            self._spawn_ai(pid)
            return

        if path == "/api/admin/duel/open":
            self._duel_open(payload, pid)
            return

        if path == "/api/admin/duel/next":
            ok = self._board.advance_turn()
            self._send_json(200 if ok else 400, {"ok": ok, "state": _state()})
            return

        if path == "/api/admin/duel/close":
            self._duel_close(pid)
            return

        if path == "/api/duel/mic":
            # Any duelist (not just the admin) flags their own browser mic —
            # the board maps their pid to a role; non-duelists are rejected.
            role = self._board.duel_pid_role(pid)
            ok = bool(role) and self._board.set_duel_recording(role, bool(payload.get("on")))
            self._send_json(200 if ok else 400, {"ok": ok, "state": _state()})
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
            self._send_json(200 if (ok and applied) else 400, {"ok": ok and applied, "state": _state()})
            return

        if path == "/api/admin/lock":
            self._board.set_locked(bool(payload.get("locked")))
            self._send_json(200, {"ok": True, "state": _state()})
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

    # ── Tracker write-backs (synchronous — see module docstring) ──────────

    def _finalize(self, payload: dict, pid: str) -> None:
        """Write the agreed points to the tracker, then stamp + advance the board.

        Tracker first, board second: the board must never claim an estimate the
        real board doesn't have. On failure the ticket does NOT advance and the
        error is surfaced (admin toast + TUI notice).
        """
        try:
            points = float(payload.get("points"))
        except (TypeError, ValueError):
            self._send_json(400, {"ok": False, "error": "bad points value", "state": self._board.state_snapshot(pid)})
            return
        ticket, _votes = self._board.current_ticket_and_votes()
        if ticket is None:
            self._send_json(400, {"ok": False, "error": "no ticket", "state": self._board.state_snapshot(pid)})
            return
        # Phase pre-check BEFORE the tracker write (mirrors _spawn_ai): the
        # board would reject a non-revealed finalize anyway, but by then the
        # points would already be on the real board — inverting the "tracker
        # first, board second" invariant this handler exists to uphold.
        if self._board.state_snapshot().get("phase") != "revealed":
            self._send_json(
                400, {"ok": False, "error": "reveal the votes first", "state": self._board.state_snapshot(pid)}
            )
            return
        ok, err = tickets_mod.update_ticket(self._board.source, ticket, story_points=points)
        if not ok:
            logger.warning("poker: finalize write-back failed for %s: %s", ticket.get("key"), err)
            self._board.set_notice(err)
            self._send_json(200, {"ok": False, "error": err, "state": self._board.state_snapshot(pid)})
            return
        finalized = self._board.finalize_current(points)
        self._send_json(
            200 if finalized else 400,
            {"ok": finalized, "state": self._board.state_snapshot(pid)},
        )

    def _ticket_edit(self, payload: dict, pid: str) -> None:
        """Push admin field edits to the tracker, then mirror them onto the board."""
        summary = payload.get("summary")
        description = payload.get("description")
        points = payload.get("points")
        summary = str(summary).strip() if summary is not None else None
        description = str(description) if description is not None else None
        if points is not None:
            try:
                points = float(points)
            except (TypeError, ValueError):
                self._send_json(
                    400, {"ok": False, "error": "bad points value", "state": self._board.state_snapshot(pid)}
                )
                return
        if summary is None and description is None and points is None:
            self._send_json(400, {"ok": False, "error": "nothing to update", "state": self._board.state_snapshot(pid)})
            return
        key = str(payload.get("key", ""))
        ticket = next((t for t in self._board.tickets_snapshot() if t.get("key") == key), None)
        if ticket is None:
            self._send_json(400, {"ok": False, "error": "unknown ticket", "state": self._board.state_snapshot(pid)})
            return
        ok, err = tickets_mod.update_ticket(
            self._board.source, ticket, summary=summary, description=description, story_points=points
        )
        if not ok:
            logger.warning("poker: ticket edit write-back failed for %s: %s", key, err)
            self._board.set_notice(err)
            self._send_json(200, {"ok": False, "error": err, "state": self._board.state_snapshot(pid)})
            return
        self._board.apply_ticket_edit(key, summary=summary, description=description, story_points=points)
        self._send_json(200, {"ok": True, "state": self._board.state_snapshot(pid)})

    # ── AI perspective (worker thread — see module docstring) ─────────────

    def _spawn_ai(self, pid: str) -> None:
        """Kick off the AI perspective on a daemon thread and return immediately.

        ``set_ai_pending`` is the double-click guard: a second click while a
        request is in flight is answered without spawning another worker. The
        result lands via ``set_ai_note`` and reaches every browser on its next
        poll — holding this HTTP response open for a 10-30 s LLM call would be
        fragile over the Cloudflare tunnel. The actual call lives in
        :func:`_run_ai` (shared with the duel STT worker's auto-trigger) and
        includes any finished duel transcript automatically.
        """
        board = self._board
        if board.state_snapshot().get("phase") != "revealed":
            self._send_json(400, {"ok": False, "error": "reveal votes first", "state": board.state_snapshot(pid)})
            return
        if not board.set_ai_pending(True):
            self._send_json(200, {"ok": True, "pending": True, "state": board.state_snapshot(pid)})
            return
        threading.Thread(target=_run_ai, args=(board,), name="poker-ai", daemon=True).start()
        self._send_json(200, {"ok": True, "pending": True, "state": board.state_snapshot(pid)})

    # ── Duel: open the floor (admin) + audio uploads (duelists) ───────────

    def _duel_open(self, payload: dict, pid: str) -> None:
        """Open the floor: board picks the duelists, then the host mic starts.

        A host-mic failure is a notice, never an error — the duel proceeds
        (duelists' browser mics may still capture it, and the debate has value
        even unrecorded).
        """
        try:
            seconds = int(payload.get("seconds", 90))
        except (TypeError, ValueError):
            seconds = 90
        ok, err = self._board.open_duel(seconds)
        if not ok:
            self._send_json(400, {"ok": False, "error": err, "state": self._board.state_snapshot(pid)})
            return
        reason = self._capture.start()
        if reason:
            self._board.set_notice(f"Host mic unavailable: {reason}")
        else:
            self._board.set_duel_recording("host", True)
        self._send_json(200, {"ok": True, "state": self._board.state_snapshot(pid)})

    def _duel_close(self, pid: str) -> None:
        """Close the floor and hand the captured audio to the STT worker.

        The worker sleeps through the upload grace window first (a duelist's
        browser uploads its final blob AFTER the recorder's onstop fires), then
        transcribes each source separately — one bad blob must not kill the
        rest — and finally auto-runs the AI perspective when the same ticket is
        still on the table.
        """
        board = self._board
        capture = self._capture
        info = board.close_duel()
        if info is None:
            self._send_json(400, {"ok": False, "error": "no duel to close", "state": board.state_snapshot(pid)})
            return
        host_wav = capture.close()  # stopping the stream is fast; STT is not

        def _worker() -> None:
            try:
                time.sleep(_DUEL_UPLOAD_GRACE)  # let in-flight browser uploads land
                parts: list[str] = []
                for role, (turn_no, blob) in sorted(capture.take_segments().items(), key=lambda kv: kv[1][0]):
                    who = info[role]
                    try:
                        from yeaboi.voice import transcribe_media

                        text = transcribe_media(blob)
                    except Exception as exc:  # e.g. PyAV choking on a fragmented Safari mp4
                        logger.warning("poker: duel segment transcription failed (role=%s): %s", role, exc)
                        continue
                    if text:
                        parts.append(f"{who['name']} (voted {who['value']}) — turn {turn_no}:\n{text}")
                if host_wav:
                    try:
                        from yeaboi.voice import transcribe

                        room = transcribe(host_wav)
                        if room:
                            # Always included, even alongside browser segments — the
                            # room take catches cross-talk neither turn-mic heard.
                            parts.append(f"Room recording (host mic, full duel):\n{room}")
                    except Exception as exc:
                        logger.warning("poker: duel room-take transcription failed: %s", exc)
                transcript = "\n\n".join(parts)
                if not transcript:
                    board.set_duel_transcript(
                        "", error="Transcription produced nothing — check the host mic / voice extra."
                    )
                    return
                board.set_duel_transcript(transcript)
                # Auto-run the AI now that it has the debate — but only if the
                # session is still on this ticket in the revealed phase (an
                # admin may have re-voted or moved on while STT ran), and only
                # if no AI request is already in flight (set_ai_pending guard).
                state = board.state_snapshot()
                if (
                    state.get("phase") == "revealed"
                    and state.get("ticket_index") == info.get("ticket_index")
                    and board.set_ai_pending(True)
                ):
                    _run_ai(board)
            except Exception as exc:  # the worker must never die loudly
                logger.warning("poker: duel STT worker failed: %s", exc)
                board.set_duel_transcript("", error="Transcription failed — see logs.")

        threading.Thread(target=_worker, name="poker-duel-stt", daemon=True).start()
        self._send_json(200, {"ok": True, "state": board.state_snapshot(pid)})

    def _duel_audio(self, length: int) -> None:
        """Accept one duelist's raw browser recording (called before JSON parsing).

        Auth: session token (query) + the uploading pid must map to a duel role
        (the board answers that — the duelists' pids never leave it), and the
        capture must still be accepting (live or within the post-close grace).

        Every early rejection closes the keep-alive connection: the (up to
        4 MB) body is left unread, and its bytes would otherwise be parsed as
        the next request on the reused connection, desyncing that client.
        """
        if not self._authed():
            self.close_connection = True
            self._send_json(403, {"error": "forbidden"})
            return
        if length <= 0:
            self._send_json(400, {"error": "empty body"})
            return
        if length > _MAX_AUDIO_BODY:
            self.close_connection = True
            self._send_json(413, {"error": "too large"})
            return
        role = self._board.duel_pid_role(self._query("pid"))
        if not role:
            self.close_connection = True
            self._send_json(403, {"error": "forbidden"})
            return
        if not self._capture.accepting():
            self.close_connection = True
            self._send_json(409, {"error": "floor closed"})
            return
        data = self.rfile.read(length)
        try:
            turn = int(self._query("turn") or 0)
        except ValueError:
            turn = 0
        ok = self._capture.add_segment(role, turn, data)
        self._send_json(200 if ok else 409, {"ok": ok})


# ---------------------------------------------------------------------------
# Server lifecycle wrapper
# ---------------------------------------------------------------------------


class PokerServer:
    """Owns the ``ThreadingHTTPServer`` + its background thread for one poker session."""

    def __init__(self, board: PokerBoard, *, port: int = _DEFAULT_PORT) -> None:
        self.board = board
        self.token = make_token()
        # A second, stronger secret that ONLY rides in the host's private link
        # (:attr:`url`). Whoever opens that link becomes the session's admin
        # (reveal / finalize / edit / AI / music / theme / timer / lock). It is
        # never in the shared join flow, the share code, or the tunnel URL — so
        # a join-code teammate is never an admin.
        self.admin_token = make_token()
        self.join_code = make_join_code()
        self.join_limiter = JoinLimiter()
        self.duel_capture = _DuelCapture()
        self.ip = get_lan_ip()
        self.port = port
        # Live-update plumbing. Built here rather than in start() so stop() is
        # safe on a server that was never started.
        self.event_hub = EventHub()
        self._watcher = ChangeWatcher(self.event_hub, self._change_probe, name="poker-live-watch")
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _change_probe(self) -> tuple:
        """The value the watcher diffs to decide whether to release parked polls.

        ``revision`` covers every board mutation (votes, reveals, duels, the AI
        worker's ``set_ai_note``), but presence deliberately does NOT bump it —
        heartbeats fire ~1/s and bumping would defeat change detection. Without
        the presence list here, the who's-here row and the voting-phase "voted"
        dots would only refresh when something unrelated changed.
        """
        # revision() is a METHOD, not a property — see retro/server.py for why
        # comparing the bound method silently blinds the watcher.
        return (self.board.revision(), self.board.presence_list())

    @property
    def url(self) -> str:
        """The host's private direct link (token + admin secret — do not share)."""
        return f"http://{self.ip}:{self.port}/?token={self.token}&admin={self.admin_token}"

    @property
    def share_url(self) -> str:
        """The token-free URL to hand out — recipients must type the join code."""
        return f"http://{self.ip}:{self.port}/"

    @property
    def share_code(self) -> str:
        """The full ip+port+token share code (decodable by retro's decode_share_code)."""
        return encode_share_code(self.ip, self.port, self.token)

    @property
    def display_code(self) -> str:
        """The short, typable join code shown in the TUI (resolved by ``/api/join``)."""
        return self.join_code

    def start(self) -> None:
        """Bind ``0.0.0.0`` (walking ports on conflict) and serve on a daemon thread."""
        # Built once, here: the page is a constant for the life of the server,
        # and everything that changes reaches the browser through /api/state.
        page_html = build_poker_html(self.board.project_name, self.board.scope_label)
        httpd: ThreadingHTTPServer | None = None
        for candidate in range(self.port, self.port + _PORT_WALK):
            try:
                # Bind all interfaces so LAN teammates can reach the board (see module docstring).
                httpd = ThreadingHTTPServer(("0.0.0.0", candidate), _PokerHandler)  # noqa: S104
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
        httpd.duel_capture = self.duel_capture  # type: ignore[attr-defined]
        httpd.page_html = page_html  # type: ignore[attr-defined]
        httpd.event_hub = self.event_hub  # type: ignore[attr-defined]
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="poker-http", daemon=True)
        self._thread.start()
        self._watcher.start()  # begins releasing parked long-polls on board changes
        # Never log any part of the token (see retro/server.py — same rationale).
        logger.info("poker server up on %s (token_len=%d)", self.url.split("?")[0], len(self.token))

    def stop(self) -> None:
        """Stop serving and free the socket. Safe to call from the TUI thread."""
        self.duel_capture.abort()  # never leave a mic stream open past the session
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
            # is on poker-http) — we're on the TUI thread here, so this is safe.
            self._httpd.shutdown()
            self._httpd.server_close()
        finally:
            self._httpd = None
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        logger.info("poker server stopped")
