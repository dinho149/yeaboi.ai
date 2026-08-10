"""The app over a real socket.

Every other app test calls ``AppServer.handle`` directly, which is fast, runs in
``test-fast``, and is blind to everything between that method and the wire. Two
bugs shipped through exactly that gap and were found by starting the server by
hand:

* the dev sign-in link went to a logger nothing had configured, so signing in
  was impossible on a laptop — the default path, with the whole unit suite green;
* ``HEAD`` returned 501, so a monitor probing ``/api/health`` called the service
  down while a browser saw it fine.

Both live in ``AppRequestHandler``, above the seam. This module binds a port and
speaks HTTP so that layer has tests of its own. It is deliberately small: the
authorisation rules are unit-tested and do not need a socket, and what is
exercised here is only what a socket can break.
"""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator

import pytest

from yeaboi.app.server import AppServer, AppRequestHandler
from yeaboi.app.store import AppStore


@pytest.fixture
def server(tmp_path) -> Iterator[tuple[str, int, AppServer]]:
    """A real ThreadingHTTPServer on an ephemeral port."""
    from http.server import ThreadingHTTPServer

    app = AppServer(AppStore(tmp_path / "app.db"))
    # Port 0: the OS picks a free one, so parallel runs cannot collide.
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), AppRequestHandler)
    httpd.app = app  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[0], httpd.server_address[1]
    try:
        yield str(host), int(port), app
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _request(host, port, method, path, body=None, headers=None):
    """One request. Returns ``(status, headers, cookies, body)``.

    ``Set-Cookie`` is returned separately as a list because it is the one header
    that legally repeats, and ``dict(getheaders())`` keeps only the last — which
    silently drops the session cookie and makes every following request look
    unauthenticated. That cost a debugging round here; hence the separate value
    rather than a comment on the dict.
    """
    conn = http.client.HTTPConnection(host, port, timeout=5)
    payload = json.dumps(body) if body is not None else None
    head = {"Content-Type": "application/json"} if payload else {}
    head.update(headers or {})
    conn.request(method, path, body=payload, headers=head)
    response = conn.getresponse()
    raw = response.read()
    cookies = [value for name, value in response.getheaders() if name.lower() == "set-cookie"]
    result = (response.status, dict(response.getheaders()), cookies, raw)
    conn.close()
    return result


def _jar(cookies: list[str]) -> str:
    """A Cookie header from a list of Set-Cookie values."""
    return "; ".join(value.split(";")[0].strip() for value in cookies)


class TestItActuallySpeaksHTTP:
    def test_health_answers(self, server):
        host, port, _ = server
        status, _, _, body = _request(host, port, "GET", "/api/health")
        assert status == 200
        assert json.loads(body) == {"status": "ok"}

    def test_head_is_not_501(self, server):
        # Regression: the stdlib handler 501s any verb it has no method for.
        host, port, _ = server
        status, headers, _, body = _request(host, port, "HEAD", "/api/health")
        assert status == 200
        assert headers["Content-Length"] == "15"
        assert body == b""

    def test_the_shell_is_served_with_its_security_headers(self, server):
        host, port, _ = server
        status, headers, _, body = _request(host, port, "GET", "/projects")
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        # The policy rides on the document. A missing CSP is invisible locally
        # and only shows up for the person on the other end of a tunnel.
        assert "Content-Security-Policy" in headers
        assert b"<!DOCTYPE html>" in body[:200] or b"<!doctype html>" in body[:200]

    def test_an_unknown_path_is_404(self, server):
        host, port, _ = server
        assert _request(host, port, "GET", "/nope")[0] == 404

    def test_the_python_version_is_not_advertised(self, server):
        # BaseHTTPRequestHandler puts it in Server: by default.
        host, port, _ = server
        _, headers, _, _ = _request(host, port, "GET", "/api/health")
        assert "Python" not in headers.get("Server", "")


class TestSignInOverTheWire:
    """The whole flow, as a browser would do it.

    This is the test that would have caught the dev-deliverer bug: it takes the
    token from where a developer takes it, rather than from the store.
    """

    def test_request_redeem_read_and_write(self, server):
        host, port, app = server

        status, _, _, _ = _request(host, port, "POST", "/api/auth/request", {"email": "ada@example.com"})
        assert status == 202

        # Exactly where a developer reads it: what the deliverer delivered.
        token = app.deliverer.delivered[-1].token

        status, _, cookies, body = _request(host, port, "POST", "/api/auth/session", {"token": token})
        assert status == 200
        assert any("HttpOnly" in value for value in cookies)
        csrf = json.loads(body)["csrf"]
        jar = _jar(cookies)

        status, _, _, body = _request(host, port, "GET", "/api/projects", headers={"Cookie": jar})
        assert status == 200, body
        assert json.loads(body) == {"projects": []}

        status, _, _, body = _request(
            host,
            port,
            "POST",
            "/api/projects",
            {"name": "Payments"},
            headers={"Cookie": jar, "X-Yeaboi-CSRF": csrf},
        )
        assert status == 201, body
        assert json.loads(body)["name"] == "Payments"

    def test_a_write_without_the_csrf_header_is_refused_over_the_wire(self, server):
        host, port, app = server
        _request(host, port, "POST", "/api/auth/request", {"email": "ada@example.com"})
        token = app.deliverer.delivered[-1].token
        _, _, cookies, _ = _request(host, port, "POST", "/api/auth/session", {"token": token})
        jar = _jar(cookies)
        status, _, _, _ = _request(host, port, "POST", "/api/projects", {"name": "X"}, headers={"Cookie": jar})
        assert status == 403


class TestTheDevLinkReachesAHuman:
    def test_the_sign_in_link_is_printed_without_logging_configured(self, server, capfd):
        """Regression, at the level the bug actually lived.

        `yeaboi app` calls no basicConfig, so a link delivered only through the
        logging module is never seen. Asserting on captured stderr is the
        closest a test gets to "the developer can read it".
        """
        host, port, _ = server
        _request(host, port, "POST", "/api/auth/request", {"email": "ada@example.com"})
        captured = capfd.readouterr()
        assert "SIGN-IN LINK" in captured.err
        assert "/signin?token=" in captured.err


class TestBodyLimits:
    def test_an_oversized_body_does_not_take_the_server_down(self, server):
        # MAX_BODY_BYTES exists so an unbounded Content-Length cannot exhaust
        # memory; what matters here is that the process is still answering.
        host, port, _ = server
        status, _, _, _ = _request(host, port, "POST", "/api/auth/request", {"email": "a@b.com", "pad": "x" * 100_000})
        assert status in (202, 400)
        assert _request(host, port, "GET", "/api/health")[0] == 200
