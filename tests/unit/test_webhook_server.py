"""The webhook receiver, driven over its own loopback socket.

The rules under test are the security posture, verbatim: every compare is
against the connection's own secret, an unknown key answers exactly like a bad
token, nothing about a request comes back in a response, and a payload never
reaches the store unless the mapping produced named events.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request

import pytest

from yeaboi.connectors import custom, webhook_store
from yeaboi.connectors.custom import spec_from_dict
from yeaboi.connectors.webhooks import server as receiver

WEBHOOK_SPEC = {
    "key": "custom_pager",
    "label": "Pager",
    "family": "incidents",
    "summary": "Incoming incident notifications from the team's own pager",
    "glyph": "📟",
    "accent": "rgb(30,30,120)",
    "kind": "webhook",
    "webhook_verify": "token",
    "events": {
        "items_key": "incidents",
        "kind": "incident",
        "title_path": "name",
        "ref_path": "id",
        "severity_path": "impact",
    },
}

SECRET = "delivery-secret-abc123"


@pytest.fixture(autouse=True)
def _world(tmp_path, monkeypatch):
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "sessions.db")
    store = tmp_path / "custom_connectors.json"
    monkeypatch.setattr("yeaboi.connectors.custom._store_path", lambda: store)
    custom.invalidate()
    custom.save_custom(spec_from_dict(WEBHOOK_SPEC))
    monkeypatch.setenv("YEABOI_CUSTOM_PAGER_WEBHOOK_SECRET", SECRET)
    receiver.stop_server()
    status = receiver.start_server(port=0)  # ephemeral: tests must not fight over 8642
    yield status
    receiver.stop_server()
    custom.invalidate()


def _post(port: int, path: str, body: bytes, headers: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


GOOD_BODY = json.dumps({"incidents": [{"id": "i-1", "name": "API down", "impact": "major"}]}).encode()


class TestAuth:
    def test_a_good_token_is_accepted_and_stored(self, _world):
        status, payload = _post(_world["port"], "/hooks/custom_pager", GOOD_BODY, {"X-Yeaboi-Token": SECRET})
        assert (status, payload) == (202, {"ok": True, "accepted": 1})
        (event,) = webhook_store.events_in_window("custom_pager", None, None)
        assert (event.kind, event.title, event.severity) == ("incident", "API down", "high")

    def test_a_non_ascii_credential_is_a_401_not_a_crash(self, _world):
        # hmac.compare_digest raises TypeError on non-ASCII str — an
        # unauthenticated sender must get the uniform 401, not a dropped thread.
        bad = _post(_world["port"], "/hooks/custom_pager", GOOD_BODY, {"X-Yeaboi-Token": "é" * 8})
        assert bad == (401, {"error": "unauthorized"})

    def test_a_bad_token_and_an_unknown_key_answer_identically(self, _world):
        bad = _post(_world["port"], "/hooks/custom_pager", GOOD_BODY, {"X-Yeaboi-Token": "wrong"})
        unknown = _post(_world["port"], "/hooks/custom_nope", GOOD_BODY, {"X-Yeaboi-Token": "wrong"})
        assert bad == unknown == (401, {"error": "unauthorized"})
        assert webhook_store.events_in_window("custom_pager", None, None) == ()

    def test_hmac_mode_signs_over_timestamp_and_body(self, _world, monkeypatch):
        custom.save_custom(spec_from_dict({**WEBHOOK_SPEC, "webhook_verify": "hmac"}))
        timestamp = str(int(time.time()))
        signature = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + GOOD_BODY, hashlib.sha256).hexdigest()
        status, payload = _post(
            _world["port"],
            "/hooks/custom_pager",
            GOOD_BODY,
            {"X-Yeaboi-Signature": f"t={timestamp},v1={signature}"},
        )
        assert (status, payload["accepted"]) == (202, 1)

    def test_a_stale_hmac_timestamp_is_refused(self, _world):
        custom.save_custom(spec_from_dict({**WEBHOOK_SPEC, "webhook_verify": "hmac"}))
        stale = str(int(time.time()) - 3600)
        signature = hmac.new(SECRET.encode(), f"{stale}.".encode() + GOOD_BODY, hashlib.sha256).hexdigest()
        status, _ = _post(
            _world["port"], "/hooks/custom_pager", GOOD_BODY, {"X-Yeaboi-Signature": f"t={stale},v1={signature}"}
        )
        assert status == 401

    def test_repeated_misses_lock_the_client_out(self, _world):
        for _ in range(receiver._AUTH_MISS_LIMIT):
            _post(_world["port"], "/hooks/custom_pager", GOOD_BODY, {"X-Yeaboi-Token": "wrong"})
        status, _ = _post(_world["port"], "/hooks/custom_pager", GOOD_BODY, {"X-Yeaboi-Token": SECRET})
        assert status == 429


class TestShape:
    def test_the_wrong_content_type_is_415(self, _world):
        status, _ = _post(
            _world["port"],
            "/hooks/custom_pager",
            GOOD_BODY,
            {"Content-Type": "text/plain", "X-Yeaboi-Token": SECRET},
        )
        assert status == 415

    def test_an_oversize_body_is_413(self, _world):
        big = json.dumps({"incidents": [{"name": "x" * (receiver.MAX_BODY_BYTES)}]}).encode()
        status, _ = _post(_world["port"], "/hooks/custom_pager", big, {"X-Yeaboi-Token": SECRET})
        assert status == 413

    def test_a_body_mapping_to_nothing_is_a_generic_400(self, _world):
        body = json.dumps({"incidents": [{"id": "no-name"}]}).encode()
        status, payload = _post(_world["port"], "/hooks/custom_pager", body, {"X-Yeaboi-Token": SECRET})
        assert (status, payload) == (400, {"error": "bad request"})

    def test_a_replayed_delivery_changes_nothing(self, _world):
        _post(_world["port"], "/hooks/custom_pager", GOOD_BODY, {"X-Yeaboi-Token": SECRET})
        _post(_world["port"], "/hooks/custom_pager", GOOD_BODY, {"X-Yeaboi-Token": SECRET})
        assert len(webhook_store.events_in_window("custom_pager", None, None)) == 1

    def test_no_response_ever_echoes_the_request(self, _world):
        canary = json.dumps({"incidents": [{"id": "PASSWORD=hunter2", "name": ""}]}).encode()
        status, payload = _post(_world["port"], "/hooks/custom_pager", canary, {"X-Yeaboi-Token": SECRET})
        assert "hunter2" not in json.dumps(payload)


class TestPipeline:
    def test_send_test_delivery_proves_the_whole_path(self, _world):
        outcome = receiver.send_test_delivery("custom_pager")
        assert outcome["ok"] is True, outcome
        (event,) = webhook_store.events_in_window("custom_pager", None, None)
        assert event.title == "yeaboi test delivery"

    def test_gather_reads_what_the_receiver_stored(self, _world):
        _post(_world["port"], "/hooks/custom_pager", GOOD_BODY, {"X-Yeaboi-Token": SECRET})
        from yeaboi.connectors.fetching import gather

        result = gather("custom_pager", since="14d")
        source = next(s for s in result.sources if s.key == "custom_pager")
        assert source.ok is True

    def test_connection_url_carries_the_whole_posture(self, _world):
        info = receiver.connection_url("custom_pager")
        assert info["url"].endswith("/hooks/custom_pager")
        assert info["header"] == "X-Yeaboi-Token"
        assert info["secret"] == SECRET
        assert info["running"] is True
        assert receiver.connection_url("custom_nope") is None
