"""The persistent web application.

The three servers that came before this one — ``retro/server.py``,
``poker/server.py``, ``sharing/server.py`` — are each a *session*: one host
starts one, a handful of people join it over a tunnel, it holds its state in
memory, and when the host closes the TUI it is gone. That is the right shape for
a ceremony and the wrong shape for an application.

This package is the other thing: a server that outlives the process that started
it, holds many projects for many people, and knows who is asking. It shares the
parts that were always general — ``web/assets.py`` for documents,
``web/security.py`` for headers and CSP — and adds the three the ceremonies
never needed: a router, a durable store, and sessions.

Nothing here imports the TUI, and the TUI does not import this. They meet at
``persistence``/``paths`` and nowhere else.
"""

from yeaboi.app.router import Request, Response, Router, json_response
from yeaboi.app.server import AppServer, serve
from yeaboi.app.sessions import SessionStore
from yeaboi.app.store import AppStore

__all__ = [
    "AppServer",
    "AppStore",
    "Request",
    "Response",
    "Router",
    "SessionStore",
    "json_response",
    "serve",
]
