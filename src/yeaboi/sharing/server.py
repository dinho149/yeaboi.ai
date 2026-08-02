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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from yeaboi.artifacts.edits import Edit, EditError
from yeaboi.artifacts.paths import PathError
from yeaboi.sharing.access import JoinLimiter, invite_payload, make_join_code, make_token
from yeaboi.sharing.editable import ConflictError, EditableShare
from yeaboi.sharing.events import ChangeWatcher, EventHub
from yeaboi.sharing.gate import ARTIFACT_CSP, GATE_CSP, render_gate_page
from yeaboi.sharing.live import parse_wait, serve_state
from yeaboi.web.security import EDIT_CSP, send_document

logger = logging.getLogger(__name__)

# A join code is eight characters; an edit is a sentence someone retyped. The
# two POSTs on this server now differ by three orders of magnitude in what they
# legitimately carry, so the cap is per-route rather than shared — leaving the
# old 1 KB in place would have refused ordinary corrections, and raising it for
# everything would have widened the unauthenticated route for no reason.
_MAX_BODY = 1024
_MAX_EDIT_BODY = 8192


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

    @property
    def _share(self) -> EditableShare | None:
        return self.server.editable  # type: ignore[attr-defined]

    def _pid(self) -> str:
        return self._query("pid")[:64]

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            self._api_get(path)
            return
        if path not in ("/", "/index.html"):
            self._json(404, {"error": "not found"})
            return
        if self._authed():
            share = self._share
            if share is not None:
                # Rendered per request from current state, not once at start:
                # a reader who opens the link after three corrections must see
                # the corrected document, not the generated one plus a catch-up.
                page = self.server.render_editable(share, self._pid())  # type: ignore[attr-defined]
                self._send(200, page.encode(), "text/html; charset=utf-8", csp=EDIT_CSP)
                return
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

    # ── Read side ─────────────────────────────────────────────────────────

    def _api_get(self, path: str) -> None:
        """Serve the editable document's read routes. All token-gated."""
        share = self._share
        if share is None or not self._authed():
            self._json(404, {"error": "not found"})
            return
        if path == "/api/state":
            # parse_wait, not a hand-rolled float(): `?wait=nan` survives a bare
            # try/except, and NaN then makes every comparison in serve_state
            # false — the deadline never passes, Event.wait returns instantly,
            # and the request spins a core until the server stops. Both boards
            # already call this helper; this one was the outlier.
            serve_state(
                self,
                self.server.hub,  # type: ignore[attr-defined]
                lambda: share.snapshot(self._pid()),
                wait_seconds=parse_wait(self._query("wait")),
            )
            return
        if path == "/api/invite":
            self._json(200, invite_payload(self.headers, "", self.server.public_url))  # type: ignore[attr-defined]
            return
        self._json(404, {"error": "not found"})

    def _read_body(self, cap: int) -> dict | None:
        """Read and parse a JSON body, answering the client on any failure."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > cap:
            self._json(413, {"error": "too large"})
            return None
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (json.JSONDecodeError, ValueError):
            self._json(400, {"error": "bad json"})
            return None
        if not isinstance(payload, dict):
            self._json(400, {"error": "bad json"})
            return None
        return payload

    def _admin(self, payload: dict) -> bool:
        """True when this request carries the host's private admin secret.

        Compared with ``compare_digest`` and read from the body rather than the
        query, matching ``runtime/api.ts`` which merges ``admin`` into every
        POST. The browser's own notion of being admin is cosmetic — it decides
        what renders; this decides what is allowed.
        """
        supplied = str(payload.get("admin", ""))
        expected = self.server.admin_token  # type: ignore[attr-defined]
        return bool(supplied) and secrets.compare_digest(supplied, expected)

    def _edit(self, payload: dict, share: EditableShare) -> None:
        """Apply one correction and answer with the whole fresh document."""
        pid = str(payload.get("pid", ""))[:64]
        try:
            if_revision = int(payload.get("if_revision", -1))
        except (TypeError, ValueError):
            if_revision = -1
        edit = Edit(
            edit_id=str(payload.get("edit_id", ""))[:64],
            op=str(payload.get("op", "")),
            path=str(payload.get("path", "")),
            value=str(payload.get("value", "")),
            base=str(payload.get("base", "")),
            label=str(payload.get("label", "")),
            target=str(payload.get("target", ""))[:64],
            author=str(payload.get("author", "")),
            avatar=str(payload.get("avatar", ""))[:8],
            pid=pid,
            at=self.server.now(),  # type: ignore[attr-defined]
        )
        try:
            stored = share.document.apply(edit, if_revision=if_revision)
        except ConflictError as exc:
            # 409 with current state: the browser has everything it needs to
            # show the newer text and let the editor decide again.
            self._json(409, {"error": str(exc), "state": share.snapshot(pid)})
            return
        except (EditError, PathError) as exc:
            self._json(400, {"error": str(exc), "state": share.snapshot(pid)})
            return
        self.server.persist(share, stored, self.client_address[0])  # type: ignore[attr-defined]
        self._json(200, {"ok": True, "state": share.snapshot(pid)})

    def _presence(self, payload: dict, share: EditableShare) -> None:
        """Record a heartbeat. Answers ok only — the long poll carries state."""
        share.document.heartbeat(
            str(payload.get("pid", ""))[:64],
            name=str(payload.get("name", "")),
            avatar=str(payload.get("avatar", "")),
            editing=str(payload.get("editing", "")),
        )
        self._json(200, {"ok": True})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        share = self._share

        if path == "/api/edit":
            if share is None or not self._authed():
                self._json(404, {"error": "not found"})
                return
            if (payload := self._read_body(_MAX_EDIT_BODY)) is not None:
                self._edit(payload, share)
            return

        if path == "/api/presence":
            if share is None or not self._authed():
                self._json(404, {"error": "not found"})
                return
            if (payload := self._read_body(_MAX_BODY)) is not None:
                self._presence(payload, share)
            return

        if path.startswith("/api/admin/"):
            if share is None or not self._authed():
                self._json(404, {"error": "not found"})
                return
            payload = self._read_body(_MAX_BODY)
            if payload is None:
                return
            if not self._admin(payload):
                self._json(403, {"error": "not permitted"})
                return
            if path == "/api/admin/lock":
                share.document.set_locked(bool(payload.get("locked", True)))
            elif path == "/api/admin/revert":
                share.document.drop_last()
            else:
                self._json(404, {"error": "not found"})
                return
            self._json(200, {"ok": True, "state": share.snapshot(str(payload.get("pid", ""))[:64])})
            return

        if path != "/api/join":
            self._json(404, {"error": "not found"})
            return
        payload = self._read_body(_MAX_BODY)
        if payload is None:
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
    """Own a loopback HTTP server and background thread for one shared document.

    Two modes behind one server. Without ``editable`` it is exactly what it has
    always been: a code gate in front of one immutable HTML snapshot, no writes,
    ``ARTIFACT_CSP``. With it, the same gate fronts a *correctable* document that
    also answers ``/api/state``, ``/api/edit``, ``/api/presence`` and the
    admin-gated host routes, under ``EDIT_CSP``.

    One server rather than two because everything around the document is
    identical — the join code, the rate limiter, the tunnel, the TUI screen that
    starts it — and the difference is entirely about what the document *is*. A
    second server would have been a copy of this one with a different middle.
    """

    def __init__(
        self,
        document: ShareDocument,
        *,
        port: int = 0,
        editable: EditableShare | None = None,
        on_edit: Callable[[EditableShare, Edit, str], None] | None = None,
    ) -> None:
        self.document = document
        self.editable = editable
        self.port = port
        self.token = make_token()
        self.join_code = make_join_code()
        # A second secret, in the host's private link only. It never travels
        # through the join flow, so a teammate who typed the code can never
        # reach an admin route however their browser is persuaded to render.
        self.admin_token = make_token()
        self.join_limiter = JoinLimiter()
        self.public_url = ""
        self._on_edit = on_edit
        self._hub = EventHub()
        self._watcher: ChangeWatcher | None = None
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ── Collaborators the handler reaches through ─────────────────────────

    @property
    def hub(self) -> EventHub:
        """Where long-polled requests park until something changes."""
        return self._hub

    def now(self) -> str:
        """Timestamp for a newly accepted edit, in UTC."""
        return datetime.now(UTC).isoformat()

    def set_public_url(self, url: str) -> None:
        """Record the tunnel URL, so the invite is the address teammates can open.

        Written onto the running server too, not just onto self: the handler
        reads it off ``self.server``, which was snapshotted at ``start()``. Both
        boards do the same, and for the same reason — the host's own browser
        arrives on 127.0.0.1, so an invite derived from that request hands a
        teammate their own machine.
        """
        self.public_url = url
        if self._httpd is not None:
            self._httpd.public_url = url  # type: ignore[attr-defined]

    def persist(self, share: EditableShare, edit: Edit, ip: str) -> None:
        """Hand an accepted edit to whoever owns durability.

        A callback rather than a store import: the server's job is to accept the
        correction and answer, and an in-memory document that nobody has wired a
        store to should still work — which is exactly what the dev shells and the
        tests want.
        """
        if self._on_edit is None:
            return
        try:
            self._on_edit(share, edit, ip)
        except Exception:  # noqa: BLE001 — durability must never break the response
            logger.exception("Could not persist edit seq=%d", edit.seq)

    def render_editable(self, share: EditableShare, pid: str) -> str:
        """Render the current corrected document as a self-contained page."""
        from yeaboi.sharing.documents import render_editable_page

        return render_editable_page(share, pid)

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
        httpd.editable = self.editable  # type: ignore[attr-defined]
        httpd.token = self.token  # type: ignore[attr-defined]
        httpd.admin_token = self.admin_token  # type: ignore[attr-defined]
        httpd.join_code = self.join_code  # type: ignore[attr-defined]
        httpd.join_limiter = self.join_limiter  # type: ignore[attr-defined]
        httpd.hub = self._hub  # type: ignore[attr-defined]
        httpd.now = self.now  # type: ignore[attr-defined]
        httpd.persist = self.persist  # type: ignore[attr-defined]
        httpd.render_editable = self.render_editable  # type: ignore[attr-defined]
        httpd.public_url = self.public_url  # type: ignore[attr-defined]
        self._httpd = httpd
        if self.editable is not None:
            # Watches rather than being told: it catches presence, which
            # deliberately does not bump the revision, and it keeps the document
            # itself free of any knowledge that anyone is listening.
            self._watcher = ChangeWatcher(self._hub, self.editable.document.change_probe, name="share-watch")
            self._watcher.start()
        self._thread = threading.Thread(target=httpd.serve_forever, name="output-share-http", daemon=True)
        self._thread.start()
        logger.info("output share server started (mode=%s, port=%d)", self.document.source_mode, self.port)

    def stop(self) -> None:
        """Stop serving and release the socket; safe and idempotent."""
        if self._httpd is None:
            return
        if self._watcher is not None:
            self._watcher.stop()
            self._watcher = None
        self._hub.close()
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        finally:
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        logger.info("output share server stopped (mode=%s)", self.document.source_mode)
