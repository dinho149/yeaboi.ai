"""The /api/ambience, /api/beta, /api/feedback and /api/consent routes.

Socketless, over ``AppServer.handle()``. The subject is the wire; the decisions
underneath belong to ``ambience.py``, ``beta.py``, ``feedback.py`` and
``fs_policy.py`` and are tested there.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yeaboi import config
from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer

TOKEN = "test-token"


@pytest.fixture
def app():
    return AppServer(token=TOKEN)


@pytest.fixture
def env(monkeypatch):
    """Preferences that write to os.environ only — no ~/.env is touched."""
    monkeypatch.setattr(config, "set_config_value", lambda _k, _v: Path("/tmp/.env"))
    for key in ("DUCK_ENABLED", "MUSIC_ENABLED", "MUSIC_CHANNEL", "PET_ENABLED", "BETA_NOTICES_ACK"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("YEABOI_FORCE_BETA_NOTICE", raising=False)
    return monkeypatch


def request(app: AppServer, method: str, path: str, payload: dict | None = None, *, authed: bool = True):
    headers = {"Authorization": f"Bearer {TOKEN}"} if authed else {}
    body_bytes = json.dumps(payload).encode() if payload is not None else b""
    return app.handle(parse_request(method, path, headers, body_bytes))


def body(response) -> dict:
    assert response.code == 200, response.body
    return json.loads(response.body)


class TestAmbience:
    def test_the_page_carries_the_preferences_and_the_catalogue(self, app, env):
        payload = body(request(app, "GET", "/api/ambience"))
        assert payload["duck"]["enabled"] is True
        assert payload["duck"]["quips"]["standup_done"]
        assert payload["music"]["channels"]
        assert payload["saver"]["idle_seconds"] > 0
        assert payload["pet"]["enabled"] is False

    def test_a_preference_write_answers_with_the_new_state(self, app, env):
        payload = body(request(app, "POST", "/api/ambience", {"pet_enabled": True, "music_channel": 1}))
        assert payload["pet"]["enabled"] is True
        assert payload["music"]["channel"] == 1

    def test_an_unknown_preference_is_a_400(self, app, env):
        response = request(app, "POST", "/api/ambience", {"volume": 11})
        assert response.code == 400
        assert "unknown ambience setting" in json.loads(response.body)["error"]

    def test_a_channel_off_the_end_is_refused(self, app, env):
        assert request(app, "POST", "/api/ambience", {"music_channel": 99}).code == 400

    def test_it_needs_the_token(self, app, env):
        assert request(app, "GET", "/api/ambience", authed=False).code == 401


class TestBetaGate:
    def test_every_gate_carries_its_copy_and_whether_it_is_spent(self, app, env):
        payload = body(request(app, "GET", "/api/beta"))
        assert payload["label"] == "BETA"
        assert set(payload["gates"]) == {
            "performance",
            "ship",
            "agent-usage",
            "agent-advisor",
            "agent-standup",
            "agent-security",
        }
        gate = payload["gates"]["ship"]
        assert gate["headline"].endswith("in beta.")
        assert gate["body"]
        assert gate["seen"] is False

    def test_acknowledging_one_marks_only_that_one_seen(self, app, env):
        assert body(request(app, "POST", "/api/beta/ship/ack"))["seen"] is True
        gates = body(request(app, "GET", "/api/beta"))["gates"]
        assert gates["ship"]["seen"] is True
        assert gates["performance"]["seen"] is False

    def test_a_mode_with_no_gate_is_a_404(self, app, env):
        response = request(app, "POST", "/api/beta/standup/ack")
        assert response.code == 404
        assert "no beta gate" in json.loads(response.body)["error"]

    def test_the_acknowledgement_is_the_one_the_terminal_reads(self, app, env):
        request(app, "POST", "/api/beta/performance/ack")
        assert config.is_beta_notice_seen("performance") is True


class TestFeedback:
    def test_options_serve_the_vocabularies(self, app):
        payload = body(request(app, "GET", "/api/feedback/options"))
        assert "Bug" in payload["types"]
        assert "planning" in payload["areas"]
        assert "/" in payload["repo"]

    def test_a_submission_reaches_the_engine_with_the_draft(self, app, monkeypatch):
        seen = {}

        def _submit(kind, area, title, description, image_paths=None):
            seen.update(kind=kind, area=area, title=title, description=description)
            from yeaboi.feedback import FeedbackResult

            return FeedbackResult(ok=True, via="api", url="https://example/1", message="Issue #1 created!")

        monkeypatch.setattr("yeaboi.feedback.submit_feedback", _submit)
        payload = body(
            request(
                app,
                "POST",
                "/api/feedback",
                {"kind": "Bug", "area": "planning", "title": "It hums", "description": "Loudly."},
            )
        )
        assert payload["ok"] is True
        assert payload["url"] == "https://example/1"
        assert seen == {"kind": "Bug", "area": "planning", "title": "It hums", "description": "Loudly."}

    def test_an_unknown_type_is_refused_before_anything_is_filed(self, app, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.feedback.submit_feedback",
            lambda *a, **k: pytest.fail("nothing should be filed for an unknown type"),
        )
        response = request(
            app, "POST", "/api/feedback", {"kind": "Rant", "area": "planning", "title": "t", "description": "d"}
        )
        assert response.code == 400
        assert "unknown feedback type" in json.loads(response.body)["error"]

    def test_an_empty_description_is_refused(self, app):
        response = request(
            app, "POST", "/api/feedback", {"kind": "Bug", "area": "planning", "title": "t", "description": "  "}
        )
        assert response.code == 400
        assert "needs a description" in json.loads(response.body)["error"]

    def test_polish_hands_back_the_rewrite(self, app, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.feedback.polish_feedback",
            lambda *a, **k: (("Hum on start", "The fan spins up."), "AI polished your draft — review below."),
        )
        payload = body(
            request(
                app,
                "POST",
                "/api/feedback/polish",
                {"kind": "Bug", "area": "planning", "title": "It hums", "description": "Loudly."},
            )
        )
        assert payload["polished"] == {"title": "Hum on start", "description": "The fan spins up."}
        assert "polished" in payload["status"]

    def test_polish_without_an_llm_is_a_status_not_an_error(self, app, monkeypatch):
        # Keeping the user's own draft is the designed fallback, not a failure.
        monkeypatch.setattr("yeaboi.feedback.polish_feedback", lambda *a, **k: (None, "AI unavailable (no key)."))
        payload = body(
            request(
                app,
                "POST",
                "/api/feedback/polish",
                {"kind": "Bug", "area": "planning", "title": "t", "description": "d"},
            )
        )
        assert payload["polished"] is None
        assert "unavailable" in payload["status"]


class TestConsentRoutes:
    @pytest.fixture(autouse=True)
    def _outside_the_sandbox(self, monkeypatch):
        # conftest whitelists the whole pytest basetemp so exports work; a test
        # about denials has to take that back.
        from yeaboi import fs_policy

        monkeypatch.setenv("YEABOI_ALLOWED_PATHS", "")
        yield
        fs_policy.clear_session_grants()
        fs_policy.set_interactive(False)
        fs_policy.pop_pending_denials()

    def _queue(self, app, tmp_path):
        from yeaboi import fs_policy

        fs_policy.set_interactive(True)
        try:
            with pytest.raises(fs_policy.SandboxViolationError):
                fs_policy.resolve_and_check(tmp_path / "outside" / "f.txt", context="read_codebase")
        finally:
            fs_policy.set_interactive(False)
        return app.consent.drain()

    def test_a_pending_request_is_readable_by_a_window_that_reloaded(self, app, tmp_path):
        self._queue(app, tmp_path)
        payload = body(request(app, "GET", "/api/consent"))
        assert len(payload["requests"]) == 1
        assert payload["requests"][0]["context"] == "read_codebase"
        assert payload["choices"] == ["allow_once", "allow_always", "deny"]

    def test_allowing_once_grants_and_closes_the_request(self, app, tmp_path):
        from yeaboi import fs_policy

        events = self._queue(app, tmp_path)
        req_id = events[0]["req_id"]
        payload = body(request(app, "POST", f"/api/consent/{req_id}", {"choice": "allow_once"}))
        assert payload == {"req_id": req_id, "choice": "allow_once", "granted": True}
        assert fs_policy.is_allowed(tmp_path / "outside" / "f.txt")
        assert body(request(app, "GET", "/api/consent"))["requests"] == []

    def test_denying_answers_granted_false(self, app, tmp_path):
        events = self._queue(app, tmp_path)
        payload = body(request(app, "POST", f"/api/consent/{events[0]['req_id']}", {"choice": "deny"}))
        assert payload["granted"] is False

    def test_an_unknown_choice_is_a_400(self, app, tmp_path):
        events = self._queue(app, tmp_path)
        response = request(app, "POST", f"/api/consent/{events[0]['req_id']}", {"choice": "maybe"})
        assert response.code == 400
        assert "unknown consent choice" in json.loads(response.body)["error"]

    def test_answering_twice_is_a_404_not_a_second_grant(self, app, tmp_path):
        events = self._queue(app, tmp_path)
        req_id = events[0]["req_id"]
        request(app, "POST", f"/api/consent/{req_id}", {"choice": "deny"})
        response = request(app, "POST", f"/api/consent/{req_id}", {"choice": "allow_always"})
        assert response.code == 404
