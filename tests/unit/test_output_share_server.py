"""Tests for the temporary code-gated output server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.client import HTTPConnection

import pytest

from yeaboi.sharing.server import OutputShareServer, ShareDocument


@pytest.fixture
def share_server():
    server = OutputShareServer(
        ShareDocument(title="Sprint plan", html="<html><body>SECRET OUTPUT</body></html>", source_mode="planning")
    )
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _post(server, code: str):
    request = urllib.request.Request(
        server.local_url + "api/join",
        data=json.dumps({"code": code}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=2)  # noqa: S310 - loopback test server


def test_gate_hides_artifact_until_code_is_exchanged(share_server):
    with urllib.request.urlopen(share_server.local_url, timeout=2) as response:  # noqa: S310
        gate = response.read().decode()
        gate_csp = response.headers["Content-Security-Policy"]
    assert "Enter the access code" in gate
    assert "SECRET OUTPUT" not in gate
    # The gate is served over a public tunnel URL and runs a script bundle, so
    # it needs a policy of its own — 'self' only, for the join POST.
    assert "default-src 'none'" in gate_csp
    assert "connect-src 'self'" in gate_csp

    with _post(share_server, share_server.display_code) as response:
        token = json.loads(response.read())["token"]
    with urllib.request.urlopen(f"{share_server.local_url}?token={token}", timeout=2) as response:  # noqa: S310
        assert "SECRET OUTPUT" in response.read().decode()
        assert response.headers["Cache-Control"].startswith("no-store")
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]


def test_join_code_goes_on_the_wire_exactly_as_displayed(share_server):
    """The dash is part of the code, not a display affordance.

    ``make_join_code`` issues ``XXXX-XXXX`` and the handler compares the posted
    string against it with ``compare_digest``, so a client that strips the dash
    for readability gets a 403 on a code the visitor typed correctly — which is
    exactly the regression the React gate shipped with. Unit tests on either
    side of this seam both passed; only the two together pin the contract.

    Mirrored by "sends the code in the XXXX-XXXX form the server issued" in
    frontend/src/shared/JoinGate.test.tsx.
    """
    displayed = share_server.display_code
    assert "-" in displayed, "the fixture below is meaningless if the code has no dash"

    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(share_server, displayed.replace("-", ""))
    assert exc.value.code == 403

    with _post(share_server, displayed) as response:
        assert json.loads(response.read())["token"]


def test_wrong_code_is_rejected(share_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(share_server, "AAAA-BBBB")
    assert exc.value.code == 403


def test_failed_code_attempts_are_rate_limited(share_server):
    for _ in range(8):
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(share_server, "AAAA-BBBB")
        assert exc.value.code == 403
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(share_server, share_server.display_code)
    assert exc.value.code == 429


def test_stop_is_idempotent():
    server = OutputShareServer(ShareDocument("t", "<html></html>", "analysis"))
    server.start()
    assert server.port > 0
    server.stop()
    server.stop()


@pytest.mark.parametrize(
    ("label", "path"),
    [
        ("not editable", "/api/edit"),
        ("no token", "/api/presence"),
        ("unknown route", "/api/nope"),
        ("admin without the secret", "/api/admin/lock"),
    ],
)
def test_a_rejected_post_does_not_poison_the_connection(share_server, label, path):
    """A refused POST must still take its body off the socket.

    ``protocol_version`` is HTTP/1.1, so the browser reuses one connection for
    every call it makes. An early return that answers before reading the body
    leaves those bytes queued, and the *next* request on that connection begins
    mid-JSON — ``BaseHTTPRequestHandler`` reads ``{"pid":…}GET`` as a method
    name and answers ``501 Unsupported method``.

    That makes a refusal contagious: one heartbeat rejected for a stale token
    replaces the reader's whole page with a server error, and nothing in the
    failing response says so. ``urllib`` cannot see it at all — it opens a fresh
    connection per request, which is why this reaches for ``HTTPConnection``.
    """
    conn = HTTPConnection("127.0.0.1", share_server.port, timeout=5)
    try:
        conn.request(
            "POST",
            path,
            body=json.dumps({"pid": "p" * 32, "op": "set", "path": "team_summary"}),
            headers={"Content-Type": "application/json"},
        )
        refused = conn.getresponse()
        refused.read()
        assert refused.status in (403, 404), label

        # The connection has to still be usable, and usable for what it says.
        conn.request("GET", "/")
        following = conn.getresponse()
        body = following.read()
        assert following.status == 200, f"{label} poisoned the connection: {body[:200]!r}"
    finally:
        conn.close()


def test_the_drain_survives_an_earlier_request_on_the_same_connection(share_server):
    """The refusal that poisons a connection is rarely the first request on it.

    One handler instance serves every request on a keep-alive connection, so
    per-request state on ``self`` outlives the request that set it. A first POST
    whose body *is* read leaves the "already read" flag set, and the next
    request — refused before its body is read — then skips the drain and leaks
    its whole body into the request line after it.

    This is the shape the browser actually produces: heartbeats succeed for a
    while, one is refused, and the page dies with a 501 that names a method
    nobody sent. Sibling tests that open a fresh connection per case cannot see
    it, because there the refusal is always request number one.
    """
    body = json.dumps({"pid": "p" * 32, "code": "NOPE"})
    conn = HTTPConnection("127.0.0.1", share_server.port, timeout=5)
    try:
        # 1. A POST whose body the server does read.
        conn.request("POST", "/api/join", body=body, headers={"Content-Type": "application/json"})
        conn.getresponse().read()

        # 2. A POST refused before the body is looked at, on the same connection.
        conn.request("POST", "/api/edit", body=body, headers={"Content-Type": "application/json"})
        refused = conn.getresponse()
        refused.read()
        assert refused.status == 404

        # 3. Which must leave the connection usable.
        conn.request("GET", "/")
        following = conn.getresponse()
        page = following.read()
        assert following.status == 200, f"stale per-request state disabled the drain: {page[:200]!r}"
    finally:
        conn.close()


def test_a_body_that_lies_about_its_length_closes_the_connection(share_server):
    """A mis-framed request has to end the connection, not be answered on it.

    Here the remainder cannot be drained: the header that says how much to read
    is the one that was wrong, so there is no way to find where this request ends
    and the next begins. Answering 400 and reading on would hand the next request
    a few bytes of the last one. Hanging up is the only honest resynchronisation,
    and it is what distinguishes this from the refusals above — those keep the
    connection because the server knows exactly how much to discard.
    """
    body = json.dumps({"pid": "p" * 32, "name": "Ada"}).encode()
    conn = HTTPConnection("127.0.0.1", share_server.port, timeout=5)
    try:
        conn.putrequest("POST", "/api/join")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(body) - 5))  # the lie
        conn.endheaders()
        conn.send(body)
        answered = conn.getresponse()
        answered.read()
        assert answered.status == 400

        with pytest.raises(Exception):  # noqa: B017, PT011 - any disconnect will do
            conn.request("GET", "/")
            conn.getresponse().read()
    finally:
        conn.close()
