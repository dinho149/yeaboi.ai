"""Routing: one table from (method, path) to a handler.

Auth is a property of the *route*, declared where the route is, and
:meth:`Router.dispatch` is the only thing that reads it — the failure mode of
a forgotten ``if not self._authed()`` line in a handler chain is a silently
public endpoint, and this shape makes that unwritable.

Handlers are plain functions of a :class:`Request` returning a
:class:`Response`. They never touch the socket, which is what lets the whole
surface be tested without binding a port — see ``tests/unit/test_app_router.py``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

# A path segment placeholder: ``/api/ops/{op_id}``.
_PARAM_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
# What a captured segment may contain. Deliberately not ``.+``: a parameter is
# one segment, so a value carrying ``/`` must not silently match a longer path.
_SEGMENT = r"([^/]+)"


@dataclass(frozen=True)
class Request:
    """One inbound request, already parsed and detached from the socket."""

    method: str
    path: str
    query: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    params: Mapping[str, str] = field(default_factory=dict)
    #: Set by the server once the bearer token has been verified.
    authed: bool = False

    def json(self) -> dict[str, Any]:
        """Parse the body as a JSON object.

        Returns an empty dict for an empty body, and raises :class:`ValueError`
        for anything that is not a JSON *object* — a bare list or string
        reaching a handler that expects ``payload["name"]`` is a 500 waiting to
        happen, so it is turned into a 400 here instead.
        """
        if not self.body:
            return {}
        parsed = json.loads(self.body.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("expected a JSON object")
        return parsed


@dataclass(frozen=True)
class Response:
    """One outbound response, before it becomes bytes on a socket.

    ``stream`` turns the response into a chunked one: the server sends the
    headers, then writes each yielded chunk and flushes it. ``body`` must stay
    empty when a stream is set. Used by the SSE event feed and the NDJSON
    engine streams; a plain JSON response never sets it.
    """

    code: int = 200
    body: bytes = b""
    content_type: str = "application/json"
    headers: tuple[tuple[str, str], ...] = ()
    stream: Iterator[bytes] | None = None


def json_response(obj: object, code: int = 200, **kw: Any) -> Response:
    """A JSON response. ``separators`` keeps the bytes stable for tests."""
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
    #: Whether dispatch requires a verified bearer token.
    auth: bool
    #: Literal path template, kept for introspection and error messages.
    template: str


class Router:
    """A method+path table.

    Registration is explicit rather than decorator-magic so that the full
    route table can be listed by a test — ``test_app_router.py`` asserts that
    every route under ``/api/`` is ``auth=True`` unless named in a short
    allowlist, which is the check that makes "someone forgot the auth line"
    impossible to ship.
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

    @property
    def routes(self) -> tuple[Route, ...]:
        return tuple(self._routes)

    @staticmethod
    def _param_names(template: str) -> list[str]:
        return _PARAM_RE.findall(template)

    def dispatch(self, request: Request) -> Response:
        """Find the route and run it, or answer 401/404/405.

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
            if route.auth and not request.authed:
                return json_response({"error": "unauthorized"}, 401)
            # Percent-decoded: a path parameter is a URL-encoded segment by
            # definition, and a handler that looked one up raw would miss every
            # value with a space in it — which is most people's names.
            params = {
                name: unquote(value)
                for name, value in zip(self._param_names(route.template), match.groups(), strict=True)
            }
            try:
                return route.handler(replace(request, params=params))
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
    )
