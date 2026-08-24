"""The desktop backend — a loopback HTTP server the yeaboi desktop app drives.

This package is the sixth surface's half of the wire: the Electron shell spawns
``yeaboi app``, reads one handshake line off stdout, and from then on talks
JSON over ``127.0.0.1`` with a bearer token. Everything it can do is either an
MCP tool served through the in-memory dispatcher (``dispatch.py``) or a native
route registered in ``registry.py`` — there is no third path, and in
particular the shell NEVER touches ``~/.yeaboi``'s databases directly (the Go
sidecar mirrors ``CURRENT_SCHEMA_VERSION`` already; a third reader is the
drift this boundary exists to prevent).

The transport skeleton (Router/Request/Response, the socketless
``AppServer.handle()``) is deliberately testable without binding a port — see
``tests/unit/test_app_server.py``.
"""

from yeaboi.app.router import HTTPError, Request, Response, Router, json_response, parse_request
from yeaboi.app.server import AppServer, serve

__all__ = [
    "AppServer",
    "HTTPError",
    "Request",
    "Response",
    "Router",
    "json_response",
    "parse_request",
    "serve",
]
