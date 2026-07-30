"""Tests for the temporary code-gated output server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

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
