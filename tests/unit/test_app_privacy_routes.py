"""The /api/meta/privacy and /api/system/check routes — socketless, over AppServer.handle().

The words themselves are tested in test_privacy.py and the probes in
test_system_check.py; here the subject is the wire: auth, payload shape, and
the serialize-don't-define guarantee.
"""

from __future__ import annotations

import json

import pytest

from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer

TOKEN = "test-token"


def request(app: AppServer, method: str, path: str, *, authed: bool = True):
    headers = {"Authorization": f"Bearer {TOKEN}"} if authed else {}
    return app.handle(parse_request(method, path, headers, b""))


@pytest.fixture
def app():
    return AppServer(token=TOKEN)


class TestPrivacyRoute:
    def test_requires_auth(self, app):
        assert request(app, "GET", "/api/meta/privacy", authed=False).code == 401

    def test_payload_is_the_owner_module_verbatim(self, app):
        from yeaboi.privacy import (
            EGRESS_DISCLOSURES,
            EGRESS_GROUPS,
            EGRESS_SWITCHES,
            PRIVACY_HEADLINE,
            PRIVACY_STATEMENT,
        )

        resp = request(app, "GET", "/api/meta/privacy")
        assert resp.code == 200
        payload = json.loads(resp.body)
        assert set(payload) == {"headline", "statement", "groups", "switches", "egress"}
        assert payload["headline"] == PRIVACY_HEADLINE
        assert payload["statement"] == list(PRIVACY_STATEMENT)
        assert payload["groups"] == list(EGRESS_GROUPS)
        assert payload["switches"] == list(EGRESS_SWITCHES)
        assert payload["egress"] == list(EGRESS_DISCLOSURES)


class TestSystemCheckRoute:
    def test_requires_auth(self, app):
        assert request(app, "GET", "/api/system/check", authed=False).code == 401

    def test_payload_shape(self, app, monkeypatch):
        from yeaboi.system_check import CheckResult, SystemReport

        canned = SystemReport(
            checks=(
                CheckResult("git", "Git", "ok", detail="on PATH", feature="Ship mode"),
                CheckResult("music", "Music (ffplay)", "missing", detail="not on PATH", hint="brew install ffmpeg"),
            )
        )
        monkeypatch.setattr("yeaboi.system_check.run_system_check", lambda: canned)
        resp = request(app, "GET", "/api/system/check")
        assert resp.code == 200
        payload = json.loads(resp.body)
        assert set(payload) == {"summary", "checks"}
        assert payload["summary"] == canned.summary
        assert payload["checks"][0] == {
            "key": "git",
            "label": "Git",
            "status": "ok",
            "detail": "on PATH",
            "hint": "",
            "feature": "Ship mode",
        }
