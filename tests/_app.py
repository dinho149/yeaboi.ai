"""Driving the app from a test, without a socket.

Every app test needs the same three things: a request built from parts, a signed
-in session, and a project to hang something off. They lived in five copies
across the app test modules until the sign-in flow changed and all five had to
be edited at once — which is the argument for one implementation.

`sign_in` performs the *real* two-step flow: ask for a link, take the token out
of the dev deliverer, redeem it. Short-cutting it — reaching into the store to
mint a session — would mean the suite stopped exercising the only path a real
user has, and the flow could break with every test still green.
"""

from __future__ import annotations

import json
from typing import Any

from yeaboi.app.router import parse_request
from yeaboi.app.sessions import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE


def call(app, method: str, path: str, body: Any = None, *, cookies: str = "", csrf: str = ""):
    """One request. `body` is JSON-encoded when given."""
    headers: dict[str, str] = {}
    if cookies:
        headers["Cookie"] = cookies
    if csrf:
        headers[CSRF_HEADER] = csrf
    raw = json.dumps(body).encode() if body is not None else b""
    return app.handle(parse_request(method, path, headers, raw))


def cookie_value(response, name: str) -> str:
    for key, value in response.headers:
        if key == "Set-Cookie" and value.startswith(f"{name}="):
            return value.split(";")[0].split("=", 1)[1]
    return ""


def sign_in(app, email: str = "ada@example.com", name: str = "") -> tuple[str, str]:
    """Complete a real sign-in. Returns ``(cookie_header, csrf_token)``.

    Reads the token out of ``app.deliverer`` — the dev deliverer keeps what it
    "sent" — so the test travels the same road a user does.
    """
    requested = call(app, "POST", "/api/auth/request", {"email": email})
    assert requested.code == 202, requested.body
    token = app.deliverer.delivered[-1].token
    body: dict[str, str] = {"token": token}
    if name:
        body["name"] = name
    response = call(app, "POST", "/api/auth/session", body)
    assert response.code == 200, response.body
    session = cookie_value(response, SESSION_COOKIE)
    csrf = cookie_value(response, CSRF_COOKIE)
    return f"{SESSION_COOKIE}={session}; {CSRF_COOKIE}={csrf}", csrf


def make_project(app, cookies: str, csrf: str, name: str = "Payments") -> str:
    response = call(app, "POST", "/api/projects", {"name": name}, cookies=cookies, csrf=csrf)
    assert response.code == 201, response.body
    return json.loads(response.body)["id"]
