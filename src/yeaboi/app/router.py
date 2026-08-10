"""Routing: one table from (method, path) to a handler.

The ceremonies route with an if-chain on ``urlparse(self.path).path`` inside
``do_GET``. That is fine for six endpoints and untenable for an application,
for a reason that is not aesthetic: every branch in those chains re-implements
auth (``if not self._authed()``), and the failure mode of a forgotten line is a
silently public endpoint. Here auth is a property of the *route*, declared where
the route is, and :meth:`Router.dispatch` is the only thing that reads it.

Handlers are plain functions of a :class:`Request` returning a :class:`Response`.
They never touch the socket, which is what lets the whole surface be tested
without binding a port — see ``tests/unit/test_app_router.py``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlparse

# A path segment placeholder: ``/api/projects/{project_id}``.
_PARAM_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
# What a captured segment may contain. Deliberately not ``.+``: a parameter is
# one segment, so a value carrying ``/`` must not silently match a longer path.
_SEGMENT = r"([^/]+)"

#: Methods that must carry a CSRF token when the request is cookie-authenticated.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True)
class Request:
    """One inbound request, already parsed and detached from the socket."""

    method: str
    path: str
    query: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    params: Mapping[str, str] = field(default_factory=dict)
    #: Set by the server once a session cookie has been resolved to a user.
    user_id: str | None = None
    #: The peer's address, from the socket rather than from a header.
    #: `X-Forwarded-For` is caller-supplied and therefore useless as a
    #: security signal — the whole point of `is_loopback` is that it cannot be
    #: claimed by someone who is not there.
    client_host: str = ""

    @property
    def is_loopback(self) -> bool:
        """True when the request came from this machine.

        Used to gate first-run setup. Not a general authorisation mechanism:
        anything on the box can reach loopback, so this only ever narrows a
        decision that is already limited some other way.
        """
        return self.client_host in ("127.0.0.1", "::1", "localhost")

    def json(self) -> dict[str, Any]:
        """Parse the body as a JSON object.

        Returns an empty dict for an empty body, and raises :class:`ValueError`
        for anything that is not a JSON *object* — a bare list or string reaching
        a handler that expects ``payload["name"]`` is a 500 waiting to happen, so
        it is turned into a 400 here instead.
        """
        if not self.body:
            return {}
        parsed = json.loads(self.body.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("expected a JSON object")
        return parsed

    def cookie(self, name: str) -> str:
        """One cookie value, or ``""``. Never raises on a malformed header."""
        jar: SimpleCookie = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except Exception:  # noqa: BLE001 - a malformed cookie is "no cookie"
            return ""
        morsel = jar.get(name)
        return morsel.value if morsel else ""


@dataclass(frozen=True)
class Response:
    """One outbound response, before it becomes bytes on a socket."""

    code: int = 200
    body: bytes = b""
    content_type: str = "application/json"
    #: Extra headers. ``Set-Cookie`` may legally repeat, hence a tuple of pairs.
    headers: tuple[tuple[str, str], ...] = ()
    #: ``None`` means "the server decides" — documents get one, JSON does not.
    csp: str | None = None


def json_response(obj: object, code: int = 200, **kw: Any) -> Response:
    """A JSON response. ``separators`` keeps the bytes stable for etag/tests."""
    body = json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return Response(code=code, body=body, content_type="application/json", **kw)


class HTTPError(Exception):
    """Raised by a handler to answer with a status instead of a traceback."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Route:
    method: str
    pattern: re.Pattern[str]
    handler: Callable[[Request], Response]
    #: Whether dispatch requires a resolved ``user_id``.
    auth: bool
    #: Literal path, kept for introspection and error messages.
    template: str


class Router:
    """A method+path table.

    Registration is explicit rather than decorator-magic so that the full route
    table can be listed by a test — ``test_app_router.py`` asserts that every
    route under ``/api/`` is either ``auth=True`` or named in an allowlist, which
    is the check that makes "someone forgot the auth line" impossible to ship.
    """

    def __init__(self) -> None:
        self._routes: list[Route] = []

    def add(self, method: str, template: str, handler: Callable[[Request], Response], *, auth: bool = True) -> None:
        # re.escape() escapes the braces too, so unescape them before the
        # placeholder substitution can see them.
        literal = re.escape(template).replace(r"\{", "{").replace(r"\}", "}")
        pattern = re.compile("^" + _PARAM_RE.sub(_SEGMENT, literal) + "$")
        self._routes.append(Route(method.upper(), pattern, handler, auth, template))

    def get(self, template: str, handler: Callable[[Request], Response], *, auth: bool = True) -> None:
        self.add("GET", template, handler, auth=auth)

    def post(self, template: str, handler: Callable[[Request], Response], *, auth: bool = True) -> None:
        self.add("POST", template, handler, auth=auth)

    def delete(self, template: str, handler: Callable[[Request], Response], *, auth: bool = True) -> None:
        self.add("DELETE", template, handler, auth=auth)

    @property
    def routes(self) -> tuple[Route, ...]:
        return tuple(self._routes)

    def _param_names(self, template: str) -> list[str]:
        return _PARAM_RE.findall(template)

    def dispatch(self, request: Request) -> Response:
        """Find the route and run it, or answer 404/405/401.

        405 rather than 404 when the path matches under a different method: a
        client that POSTs to a GET route has a bug that a 404 hides.
        """
        path_matched = False
        for route in self._routes:
            match = route.pattern.match(request.path)
            if not match:
                continue
            path_matched = True
            if route.method != request.method:
                continue
            params = dict(zip(self._param_names(route.template), match.groups(), strict=True))
            if route.auth and not request.user_id:
                return json_response({"error": "unauthorized"}, 401)
            scoped = Request(
                method=request.method,
                path=request.path,
                query=request.query,
                headers=request.headers,
                body=request.body,
                params=params,
                user_id=request.user_id,
                client_host=request.client_host,
            )
            try:
                return route.handler(scoped)
            except HTTPError as exc:
                return json_response({"error": exc.message}, exc.code)
            except ValueError as exc:
                # Malformed JSON and bad field values are the client's problem.
                return json_response({"error": str(exc)}, 400)
        if path_matched:
            return json_response({"error": "method not allowed"}, 405)
        return json_response({"error": "not found"}, 404)


def parse_request(
    method: str,
    raw_path: str,
    headers: Mapping[str, str],
    body: bytes = b"",
    client_host: str = "",
) -> Request:
    """Split a raw request line into a :class:`Request`.

    Only the first value of a repeated query key is kept — every consumer here
    wants a scalar, and ``?a=1&a=2`` reaching code that expects a string is the
    kind of thing that reads as working until it does not.
    """
    parsed = urlparse(raw_path)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items() if values}
    return Request(
        method=method.upper(),
        path=parsed.path,
        query=query,
        headers=dict(headers),
        body=body,
        client_host=client_host,
    )
