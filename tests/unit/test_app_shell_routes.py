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
            "weekly-review",
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

    def test_options_say_which_route_submit_will_take(self, app, monkeypatch):
        # The form can only name the button honestly if it is told this.
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "ghp_x")
        assert body(request(app, "GET", "/api/feedback/options"))["has_github_token"] is True
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "")
        assert body(request(app, "GET", "/api/feedback/options"))["has_github_token"] is False

    def test_options_carry_the_attachment_caps_and_colours(self, app):
        payload = body(request(app, "GET", "/api/feedback/options"))
        assert "image/png" in payload["image_mimes"]
        assert "text/plain" in payload["text_mimes"]
        assert payload["max_image_bytes"] > payload["max_text_bytes"]
        assert payload["max_attachments"] == 6
        assert payload["area_colors"]["planning"].startswith("rgb(")
        assert payload["version"] and payload["platform"]

    def test_a_submission_reaches_the_engine_with_the_draft(self, app, monkeypatch):
        seen = {}

        def _submit(kind, area, title, description, image_paths=None, text_paths=None):
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


class TestFeedbackAttachments:
    """Base64 in, a path out — and only that path is accepted back."""

    @pytest.fixture
    def attachments(self, tmp_path, monkeypatch):
        root = tmp_path / "attachments" / "feedback"
        root.mkdir(parents=True)
        monkeypatch.setattr("yeaboi.paths.get_attachments_dir", lambda _scope: root)
        return root

    def _attach(self, app, mime, raw, name="app.log"):
        import base64

        return request(
            app,
            "POST",
            "/api/feedback/attachments",
            {"name": name, "mime": mime, "data": base64.b64encode(raw).decode()},
        )

    def test_a_log_is_saved_and_counted(self, app, attachments):
        payload = body(self._attach(app, "text/plain", b"one\ntwo\n"))
        assert payload["kind"] == "text"
        assert payload["name"] == "app.log"
        assert payload["lines"] == 2
        assert Path(payload["path"]).read_bytes() == b"one\ntwo\n"

    def test_the_stored_name_keeps_the_reporter_s(self, app, attachments):
        # It is the stored name, not the reply's, that the issue body shows.
        payload = body(self._attach(app, "text/plain", b"x"))
        assert Path(payload["path"]).name.startswith("app-")
        assert Path(payload["path"]).suffix == ".log"

    def test_a_screenshot_is_saved_without_a_line_count(self, app, attachments):
        payload = body(self._attach(app, "image/png", b"\x89PNG fake", name="shot.png"))
        assert payload["kind"] == "image"
        assert "lines" not in payload
        assert Path(payload["path"]).suffix == ".png"

    def test_a_directory_in_the_name_never_reaches_the_issue(self, app, attachments):
        payload = body(self._attach(app, "text/plain", b"x", name="../../etc/passwd"))
        assert payload["name"] == "passwd"
        assert Path(payload["path"]).parent == attachments

    def test_an_unsupported_type_is_refused(self, app, attachments):
        response = self._attach(app, "application/zip", b"PK\x03\x04")
        assert response.code == 400
        assert "unsupported file type" in json.loads(response.body)["error"]

    def test_an_oversized_log_is_refused(self, app, attachments):
        from yeaboi.feedback import MAX_TEXT_ATTACHMENT_BYTES

        response = self._attach(app, "text/plain", b"x" * (MAX_TEXT_ATTACHMENT_BYTES + 1))
        assert response.code == 413
        assert "Too large" in json.loads(response.body)["error"]

    def test_something_that_is_not_base64_is_refused(self, app, attachments):
        response = request(
            app, "POST", "/api/feedback/attachments", {"name": "a.log", "mime": "text/plain", "data": "not base64!!"}
        )
        assert response.code == 400

    def test_a_saved_path_round_trips_into_a_submission(self, app, attachments, monkeypatch):
        seen = {}

        def _submit(kind, area, title, description, image_paths=None, text_paths=None):
            seen.update(images=image_paths, texts=text_paths)
            from yeaboi.feedback import FeedbackResult

            return FeedbackResult(ok=True, via="api", url="https://example/1", message="ok")

        monkeypatch.setattr("yeaboi.feedback.submit_feedback", _submit)
        saved = body(self._attach(app, "text/plain", b"boom\n"))["path"]
        request(
            app,
            "POST",
            "/api/feedback",
            {"kind": "Bug", "area": "planning", "title": "t", "description": "d", "text_paths": [saved]},
        )
        assert seen == {"images": [], "texts": [str(Path(saved).resolve())]}

    def test_a_path_outside_the_attachments_directory_is_refused(self, app, attachments, tmp_path, monkeypatch):
        # polish reads these files and sends them to a model, so this is the
        # difference between an attachment and an arbitrary-file read.
        monkeypatch.setattr(
            "yeaboi.feedback.submit_feedback",
            lambda *a, **k: pytest.fail("nothing should be filed for a path we did not hand out"),
        )
        secret = tmp_path / "id_rsa"
        secret.write_text("PRIVATE KEY")
        response = request(
            app,
            "POST",
            "/api/feedback",
            {"kind": "Bug", "area": "planning", "title": "t", "description": "d", "text_paths": [str(secret)]},
        )
        assert response.code == 400
        assert "one this app returned" in json.loads(response.body)["error"]

    def test_traversal_out_of_the_attachments_directory_is_refused(self, app, attachments):
        response = request(
            app,
            "POST",
            "/api/feedback",
            {
                "kind": "Bug",
                "area": "planning",
                "title": "t",
                "description": "d",
                "image_paths": [str(attachments / ".." / ".." / "id_rsa")],
            },
        )
        assert response.code == 400

    def test_a_file_deleted_after_attaching_is_dropped_not_fatal(self, app, attachments, monkeypatch):
        seen = {}

        def _submit(kind, area, title, description, image_paths=None, text_paths=None):
            seen.update(texts=text_paths)
            from yeaboi.feedback import FeedbackResult

            return FeedbackResult(ok=True, via="api", url="u", message="ok")

        monkeypatch.setattr("yeaboi.feedback.submit_feedback", _submit)
        saved = body(self._attach(app, "text/plain", b"boom\n"))["path"]
        Path(saved).unlink()
        response = request(
            app,
            "POST",
            "/api/feedback",
            {"kind": "Bug", "area": "planning", "title": "t", "description": "d", "text_paths": [saved]},
        )
        assert response.code == 200
        assert seen == {"texts": []}

    def test_a_log_sent_as_an_image_is_refused(self, app, attachments):
        # attach validates the mime; this is what stops a stored file being
        # replayed into the wrong list, where it would reach a vision model.
        saved = body(self._attach(app, "text/plain", b"boom\n"))["path"]
        response = request(
            app,
            "POST",
            "/api/feedback",
            {"kind": "Bug", "area": "planning", "title": "t", "description": "d", "image_paths": [saved]},
        )
        assert response.code == 400
        assert "not a image attachment" in json.loads(response.body)["error"]

    def test_a_screenshot_sent_as_a_log_is_refused(self, app, attachments):
        saved = body(self._attach(app, "image/png", b"\x89PNG", name="shot.png"))["path"]
        response = request(
            app,
            "POST",
            "/api/feedback",
            {"kind": "Bug", "area": "planning", "title": "t", "description": "d", "text_paths": [saved]},
        )
        assert response.code == 400

    def test_the_same_file_twice_is_attached_once(self, app, attachments, monkeypatch):
        seen = {}

        def _submit(kind, area, title, description, image_paths=None, text_paths=None):
            seen.update(texts=text_paths)
            from yeaboi.feedback import FeedbackResult

            return FeedbackResult(ok=True, via="api", url="u", message="ok")

        monkeypatch.setattr("yeaboi.feedback.submit_feedback", _submit)
        saved = body(self._attach(app, "text/plain", b"boom\n"))["path"]
        request(
            app,
            "POST",
            "/api/feedback",
            {"kind": "Bug", "area": "planning", "title": "t", "description": "d", "text_paths": [saved, saved]},
        )
        assert seen["texts"] == [str(Path(saved).resolve())]

    def test_polish_carries_the_attachments_too(self, app, attachments, monkeypatch):
        # The route where the containment check is actually load-bearing: this
        # is the call that reads the files and sends them to a model.
        seen = {}

        def _polish(kind, area, title, description, image_paths=None, text_paths=None):
            seen.update(images=image_paths, texts=text_paths)
            return None, "AI unavailable (no key)."

        monkeypatch.setattr("yeaboi.feedback.polish_feedback", _polish)
        saved = body(self._attach(app, "text/plain", b"boom\n"))["path"]
        request(
            app,
            "POST",
            "/api/feedback/polish",
            {"kind": "Bug", "area": "planning", "title": "t", "description": "d", "text_paths": [saved]},
        )
        assert seen == {"images": [], "texts": [str(Path(saved).resolve())]}

    def test_polish_refuses_a_path_it_did_not_hand_out(self, app, attachments, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.feedback.polish_feedback",
            lambda *a, **k: pytest.fail("nothing outside the attachments directory should be read"),
        )
        secret = tmp_path / "id_rsa"
        secret.write_text("PRIVATE KEY")
        response = request(
            app,
            "POST",
            "/api/feedback/polish",
            {"kind": "Bug", "area": "planning", "title": "t", "description": "d", "text_paths": [str(secret)]},
        )
        assert response.code == 400

    def test_more_than_the_ceiling_is_refused(self, app, attachments):
        saved = [body(self._attach(app, "text/plain", b"x", name=f"{i}.log"))["path"] for i in range(7)]
        response = request(
            app,
            "POST",
            "/api/feedback",
            {
                "kind": "Bug",
                "area": "planning",
                "title": "t",
                "description": "d",
                "text_paths": saved,
            },
        )
        assert response.code == 400
        assert "too many attachments" in json.loads(response.body)["error"]

    def test_paths_must_be_a_list(self, app, attachments):
        response = request(
            app,
            "POST",
            "/api/feedback",
            {"kind": "Bug", "area": "planning", "title": "t", "description": "d", "image_paths": "a.png"},
        )
        assert response.code == 400


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
