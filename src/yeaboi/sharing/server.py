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

logger = logging.getLogger(__name__)

_MAX_BODY = 1024

_GATE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Shared with yeaboi</title>
  <style>
    :root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;min-height:100vh;
    display:grid;place-items:center;background:#090d14;color:#e6edf3;font:16px system-ui,sans-serif}
    main{width:min(92vw,430px);padding:2rem;border:1px solid #30363d;border-radius:14px;
    background:#111722;box-shadow:0 20px 70px #0008}h1{margin:.2rem 0 .5rem;font-size:1.45rem}
    p{color:#9da7b3;line-height:1.5}label{display:block;margin:1.4rem 0 .45rem;font-weight:650}
    input{width:100%;padding:.85rem 1rem;border:1px solid #465166;border-radius:9px;background:#090d14;
    color:#fff;font:700 1.15rem ui-monospace,monospace;text-transform:uppercase;letter-spacing:.12em}
    button{width:100%;margin-top:.8rem;padding:.8rem;border:0;border-radius:9px;background:#648cff;
    color:#fff;font-weight:750;cursor:pointer}#status{min-height:1.4rem;color:#ff9b9b;font-size:.9rem}
  </style>
</head>
<body><main><div>🤙 yeaboi.ai</div><h1>Someone shared an output with you</h1>
<p>Enter the access code shown by the host. This temporary page disappears when they stop sharing.</p>
<form id="join"><label for="code">Access code</label><input id="code" maxlength="9"
placeholder="XXXX-XXXX" autocomplete="one-time-code" autofocus><button>View output</button>
<p id="status" role="alert"></p></form></main>
<script>
const form=document.getElementById('join'),status=document.getElementById('status');
form.addEventListener('submit',async e=>{e.preventDefault();status.textContent='Checking…';
try{const r=await fetch('/api/join',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({code:document.getElementById('code').value})});
const d=await r.json();
if(!r.ok)throw new Error(r.status===429?'Too many attempts — try again later.':'That code did not match.');
location.replace('/?token='+encodeURIComponent(d.token));}
catch(err){status.textContent=err.message||'Could not join.';}});
</script></body></html>"""


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

    def _send(self, code: int, body: bytes, content_type: str, *, artifact: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        if artifact:
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; font-src data:; connect-src 'none'",
            )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path not in ("/", "/index.html"):
            self._json(404, {"error": "not found"})
            return
        if self._authed():
            document = self.server.document  # type: ignore[attr-defined]
            self._send(200, document.html.encode(), "text/html; charset=utf-8", artifact=True)
            return
        self._send(200, _GATE_HTML.encode(), "text/html; charset=utf-8")

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
