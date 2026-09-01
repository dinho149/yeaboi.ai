"""The webhook receiver: one loopback server, one route shape, no outbound.

``POST /hooks/<key>`` is the whole surface. The rules, in the order a request
meets them: loopback bind (never another interface), method and content-type
gates, a 64 KB body cap, per-connection auth (a static token header or a
Stripe-shaped HMAC with a ±5 minute replay window), then the declared mapping
into :class:`~yeaboi.ops.events.OpsEvent` and the store.

What can never happen here: a response echoing any part of the request, a
payload reaching a log line, a request leaving this process (the receiver makes
none), or an unknown key answering differently from a bad token — a scanner
must not learn which connections exist.

The port is FIXED (default 8642, ``YEABOI_WEBHOOK_PORT`` overrides) and a
conflict is a hard error rather than a walk: a webhook URL a user pasted into
a vendor's console must stay true.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8642

#: Events are identifiers, not payload dumps — but a batch of mapped rows
#: needs more headroom than the retro board's 4 KB.
MAX_BODY_BYTES = 64 * 1024

#: HMAC deliveries older (or newer) than this many seconds are refused.
REPLAY_WINDOW_SECONDS = 300

#: Failed-auth lockout: this many misses inside the window locks the client out
#: for the window's length.
_AUTH_MISS_LIMIT = 5
_AUTH_MISS_WINDOW = 60.0

#: Accepted-delivery budget per connection per minute — a runaway sender gets
#: 429s, not an unbounded table.
_ACCEPT_LIMIT_PER_MINUTE = 120

_TOKEN_HEADER = "X-Yeaboi-Token"  # noqa: S105 — a header NAME, not a credential
_SIGNATURE_HEADER = "X-Yeaboi-Signature"


def receiver_port() -> int:
    """The port the receiver binds — fixed, because pasted URLs must stay true."""
    raw = os.environ.get("YEABOI_WEBHOOK_PORT", "").strip()
    try:
        return int(raw) if raw else DEFAULT_PORT
    except ValueError:
        return DEFAULT_PORT


def mint_secret() -> str:
    """A fresh delivery secret — same strength as the app wire's bearer token."""
    return secrets.token_urlsafe(32)


def _secret_for(key: str) -> str:
    from yeaboi.connectors.custom import spec_by_key

    spec = spec_by_key(key)
    if spec is None or spec.kind != "webhook":
        return ""
    return os.environ.get(f"{spec.env_stem}_WEBHOOK_SECRET", "").strip()


def _verify_delivery(key: str, headers, raw_body: bytes) -> bool:
    """Whether one delivery authenticates. Constant-time on every compare.

    An unknown key takes the same token-compare path against an empty secret
    and fails the same way — the answer never says whether the key exists.
    """
    from yeaboi.connectors.custom import spec_by_key

    secret = _secret_for(key)
    spec = spec_by_key(key)
    mode = spec.webhook_verify if spec is not None else "token"
    if mode == "hmac":
        raw = str(headers.get(_SIGNATURE_HEADER, "") or "")
        parts = dict(part.split("=", 1) for part in raw.split(",") if "=" in part)
        timestamp, signature = parts.get("t", ""), parts.get("v1", "")
        if not timestamp.isdigit():
            return False
        if abs(time.time() - int(timestamp)) > REPLAY_WINDOW_SECONDS:
            return False
        expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()
        # Bytes on both sides: the str overload raises TypeError on non-ASCII,
        # which would let an unauthenticated sender crash the request thread.
        return bool(secret) and hmac.compare_digest(signature.encode(), expected.encode())
    supplied = str(headers.get(_TOKEN_HEADER, "") or "")
    return bool(secret) and hmac.compare_digest(supplied.encode(), secret.encode())


def map_delivery(spec, body) -> tuple:
    """One delivery's JSON into OpsEvents through the declared mapping.

    ``items_key`` names the row array; absent, the body itself is one row (or
    already an array). A row with no title is dropped, never raised — one
    malformed row must not lose the batch.
    """
    from yeaboi.connectors.custom import _dig
    from yeaboi.connectors.fetching import PAGE_LIMIT
    from yeaboi.ops.events import OpsEvent, clean_severity, clean_title, iso, parse_ts

    mapping = spec.events
    if mapping is None:
        return ()
    if mapping.items_key:
        found = _dig(body, mapping.items_key) if isinstance(body, dict) else None
        rows = found if isinstance(found, list) else []
    elif isinstance(body, list):
        rows = body
    else:
        rows = [body]

    events = []
    for row in rows[:PAGE_LIMIT]:
        if not isinstance(row, dict):
            continue
        title = clean_title(str(_dig(row, mapping.title_path) or ""))
        if not title:
            continue
        url = str(_dig(row, mapping.url_path) or "")
        events.append(
            OpsEvent(
                kind=mapping.kind,
                source=spec.key,
                ref=str(_dig(row, mapping.ref_path) or ""),
                title=title,
                service=str(_dig(row, mapping.service_path) or ""),
                severity=clean_severity(str(_dig(row, mapping.severity_path) or "")),
                status=str(_dig(row, mapping.status_path) or ""),
                started_at=iso(parse_ts(str(_dig(row, mapping.started_at_path) or ""))),
                url=url if url.startswith("https://") else "",
            )
        )
    return tuple(events)


class _Limiter:
    """Failed-auth lockout per client + accepted-delivery budget per key."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._misses: dict[str, list[float]] = {}
        self._accepts: dict[str, list[float]] = {}

    def _trim(self, stamps: list[float], window: float) -> list[float]:
        cutoff = time.monotonic() - window
        return [s for s in stamps if s >= cutoff]

    def locked_out(self, client: str) -> bool:
        with self._lock:
            misses = self._trim(self._misses.get(client, []), _AUTH_MISS_WINDOW)
            self._misses[client] = misses
            return len(misses) >= _AUTH_MISS_LIMIT

    def record_miss(self, client: str) -> None:
        with self._lock:
            self._misses.setdefault(client, []).append(time.monotonic())

    def over_budget(self, key: str) -> bool:
        with self._lock:
            accepts = self._trim(self._accepts.get(key, []), 60.0)
            self._accepts[key] = accepts
            return len(accepts) >= _ACCEPT_LIMIT_PER_MINUTE

    def record_accept(self, key: str) -> None:
        with self._lock:
            self._accepts.setdefault(key, []).append(time.monotonic())


class _Handler(BaseHTTPRequestHandler):
    server_version = "yeaboi-webhooks"
    sys_version = ""

    def log_message(self, format, *args):  # noqa: A002 — BaseHTTPRequestHandler's name
        # The default writes to stderr with the raw request line. Paths are
        # ours to log at DEBUG; a query string or body never appears.
        logger.debug("webhooks: %s", format % args)

    def _respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler's convention
        if self.path == "/health":
            self._respond(200, {"ok": True})
            return
        self._respond(405, {"error": "method not allowed"})

    def do_POST(self):  # noqa: N802
        from yeaboi.connectors.custom import spec_by_key
        from yeaboi.connectors.webhook_store import record_delivery

        limiter: _Limiter = self.server.limiter  # type: ignore[attr-defined]
        client = self.client_address[0] if self.client_address else "?"

        path = self.path.split("?", 1)[0]
        if not path.startswith("/hooks/"):
            self._respond(404, {"error": "not found"})
            return
        key = path.removeprefix("/hooks/").strip("/")

        if limiter.locked_out(client):
            self._respond(429, {"error": "too many attempts"})
            return

        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._respond(415, {"error": "unsupported media type"})
            return
        raw_length = self.headers.get("Content-Length") or ""
        if not raw_length.isdigit():
            self.close_connection = True
            self._respond(400, {"error": "bad request"})
            return
        length = int(raw_length)
        if length > MAX_BODY_BYTES:
            self._respond(413, {"error": "payload too large"})
            return
        raw_body = self.rfile.read(length)

        if not _verify_delivery(key, self.headers, raw_body):
            limiter.record_miss(client)
            # Identical body whether the key exists or the credential failed.
            self._respond(401, {"error": "unauthorized"})
            return

        spec = spec_by_key(key)
        if spec is None or spec.kind != "webhook":
            # Authenticated but not a webhook connection — unreachable in
            # practice (no secret exists), kept as the same generic answer.
            self._respond(401, {"error": "unauthorized"})
            return
        if limiter.over_budget(key):
            self._respond(429, {"error": "too many deliveries"})
            return

        try:
            body = json.loads(raw_body)
        except ValueError:
            self._respond(400, {"error": "bad request"})
            return
        events = map_delivery(spec, body)
        if not events:
            # The reason stays local: the response must not describe the body.
            logger.info("webhooks: %s delivery mapped to no events", key)
            self._respond(400, {"error": "bad request"})
            return

        limiter.record_accept(key)
        delivery_hash = hashlib.sha256(raw_body).hexdigest()
        record_delivery(key, delivery_hash, events)
        self._respond(202, {"ok": True, "accepted": len(events)})


# One receiver per process — module state, guarded by _state_lock.
_state_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None
_thread: threading.Thread | None = None
_started_at: str = ""
_tunnel = None
_tunnel_url: str = ""


def start_server(port: int | None = None) -> dict:
    """Bind the receiver on loopback and serve on a daemon thread.

    Returns the status dict; raises OSError when the fixed port is taken —
    a hard error on purpose, never a port walk.
    """
    global _server, _thread, _started_at
    with _state_lock:
        if _server is not None:
            return server_status()
        bind_port = port or receiver_port()
        server = ThreadingHTTPServer(("127.0.0.1", bind_port), _Handler)
        server.limiter = _Limiter()  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, name="webhook-receiver", daemon=True)
        thread.start()
        _server, _thread = server, thread
        _started_at = datetime.now(timezone.utc).isoformat()
        logger.info("webhooks: receiver listening on 127.0.0.1:%d", bind_port)
    return server_status()


def stop_server() -> None:
    global _server, _thread, _started_at
    stop_share()
    with _state_lock:
        if _server is None:
            return
        _server.shutdown()
        _server.server_close()
        _server, _thread, _started_at = None, None, ""
        logger.info("webhooks: receiver stopped")


def start_share() -> str:
    """Expose the receiver through a cloudflared quick tunnel; returns its URL.

    The honest limits, which every surface printing the URL repeats: the
    hostname ROTATES on every share and expires on its own — fit for testing a
    sender, not for a durable endpoint pasted into a vendor's console.
    """
    global _tunnel, _tunnel_url
    with _state_lock:
        if _tunnel_url:
            return _tunnel_url
        port = _server.server_address[1] if _server is not None else receiver_port()
    from yeaboi.retro.tunnel import CloudflareTunnel

    tunnel = CloudflareTunnel(port)
    url = tunnel.start()
    with _state_lock:
        if url:
            _tunnel, _tunnel_url = tunnel, url
            logger.info("webhooks: share tunnel up")
    return url or ""


def stop_share() -> None:
    global _tunnel, _tunnel_url
    with _state_lock:
        tunnel, _tunnel, _tunnel_url = _tunnel, None, ""
    if tunnel is not None:
        try:
            tunnel.stop()
        except Exception:
            logger.debug("webhooks: tunnel stop failed", exc_info=True)


def server_status() -> dict:
    """What a surface renders: running, where, since when, and per-key liveness."""
    from yeaboi.connectors.custom import load_specs
    from yeaboi.connectors.webhook_store import last_received_at

    with _state_lock:
        running = _server is not None
        port = _server.server_address[1] if _server is not None else receiver_port()
        started = _started_at
        tunnel_url = _tunnel_url
    connections = [
        {"key": spec.key, "label": spec.label, "last_received_at": last_received_at(spec.key)}
        for spec in load_specs()
        if spec.kind == "webhook"
    ]
    return {
        "running": running,
        "port": port,
        "started_at": started,
        "tunnel_url": tunnel_url,
        "connections": connections,
    }


def connection_url(key: str) -> dict | None:
    """Where one connection's deliveries go, and how they authenticate.

    The ONE place the secret comes back whole — showing it is a local,
    deliberate act, the same posture as a board's host URL. None for a key
    that is not a webhook connection.
    """
    from yeaboi.connectors.custom import spec_by_key
    from yeaboi.connectors.webhook_store import last_received_at

    spec = spec_by_key(key)
    if spec is None or spec.kind != "webhook":
        return None
    status = server_status()
    path = f"/hooks/{spec.key}"
    return {
        "key": spec.key,
        "url": f"http://127.0.0.1:{status['port']}{path}",
        "tunnel_url": f"{status['tunnel_url']}{path}" if status["tunnel_url"] else "",
        "verify": spec.webhook_verify,
        "header": _SIGNATURE_HEADER if spec.webhook_verify == "hmac" else _TOKEN_HEADER,
        "secret": _secret_for(key),
        "running": status["running"],
        "last_received_at": last_received_at(spec.key),
    }


def send_test_delivery(key: str) -> dict:
    """POST one synthetic, correctly-authenticated delivery to the local receiver.

    Proves the whole pipeline — auth, mapping, store — without an external
    sender. The payload is built FROM the declared mapping, so whatever dot
    paths the user wrote are the ones exercised.
    """
    import urllib.request

    from yeaboi.connectors.custom import spec_by_key

    spec = spec_by_key(key)
    if spec is None or spec.kind != "webhook" or spec.events is None:
        return {"ok": False, "message": f"{key!r} is not a webhook connection"}
    status = server_status()
    if not status["running"]:
        return {"ok": False, "message": "the receiver is not running — start it with `yeaboi webhooks serve`"}

    def _plant(target: dict, dotted: str, value) -> None:
        parts = [p for p in dotted.split(".") if p]
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        if parts:
            target[parts[-1]] = value

    row: dict = {}
    _plant(row, spec.events.title_path, "yeaboi test delivery")
    if spec.events.ref_path:
        _plant(row, spec.events.ref_path, "test-1")
    body: dict | list = row
    if spec.events.items_key:
        wrapper: dict = {}
        _plant(wrapper, spec.events.items_key, [row])
        body = wrapper
    raw = json.dumps(body).encode()

    secret = _secret_for(key)
    headers = {"Content-Type": "application/json"}
    if spec.webhook_verify == "hmac":
        timestamp = str(int(time.time()))
        signature = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw, hashlib.sha256).hexdigest()
        headers[_SIGNATURE_HEADER] = f"t={timestamp},v1={signature}"
    else:
        headers[_TOKEN_HEADER] = secret

    request = urllib.request.Request(
        f"http://127.0.0.1:{status['port']}/hooks/{spec.key}", data=raw, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as resp:  # noqa: S310 — loopback, our own receiver
            payload = json.loads(resp.read().decode() or "{}")
            return {"ok": True, "message": f"accepted {payload.get('accepted', 0)} event(s)"}
    except Exception as exc:
        return {"ok": False, "message": f"test delivery failed: {exc}"}
