"""The /api/settings routes — socketless, over AppServer.handle().

The engine's own behaviour lives in test_settings_engine.py; here the subject
is the wire: request parsing, auth, the sign-in session lifecycle, and the
no-raw-secret guarantee on every settings response.
"""

from __future__ import annotations

import json

import pytest

from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer

TOKEN = "test-token"


def request(app: AppServer, method: str, path: str, payload: dict | None = None, *, authed: bool = True):
    headers = {"Authorization": f"Bearer {TOKEN}"} if authed else {}
    body = json.dumps(payload).encode() if payload is not None else b""
    return app.handle(parse_request(method, path, headers, body))


@pytest.fixture
def app():
    return AppServer(token=TOKEN)


class TestSettingsRead:
    def test_requires_auth(self, app):
        assert request(app, "GET", "/api/settings", authed=False).code == 401
        assert request(app, "GET", "/api/settings/providers", authed=False).code == 401

    def test_snapshot_shape_and_masking(self, app, monkeypatch):
        secret = "ghp_super-secret-raw-token-value"
        monkeypatch.setenv("GITHUB_TOKEN", secret)
        resp = request(app, "GET", "/api/settings")
        assert resp.code == 200
        assert secret not in resp.body.decode()
        payload = json.loads(resp.body)
        assert {"fields", "sections", "config_path", "voice"} == set(payload)
        github = next(f for f in payload["fields"] if f["env"] == "GITHUB_TOKEN")
        assert github["secret"] and github["is_set"]
        assert github["value"].startswith("ghp_") and "•" in github["value"]

    def test_providers_catalog(self, app):
        payload = json.loads(request(app, "GET", "/api/settings/providers").body)
        assert {"providers", "anthropic_auth_modes", "token_help"} == set(payload)


class TestConnectionsSignInRow:
    def test_the_catalog_reports_a_signin_without_its_token(self, app, monkeypatch):
        monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "AQD-refresh-token-value")
        monkeypatch.setenv("SPOTIFY_ACCOUNT", "dinho")
        resp = request(app, "GET", "/api/connections?all=1")
        assert resp.code == 200
        assert "AQD-refresh-token-value" not in resp.body.decode()
        spotify = next(c for c in json.loads(resp.body)["connectors"] if c["key"] == "spotify")
        assert spotify["signin"] == {"signed_in": True, "account": "dinho"}
        token = next(f for f in spotify["fields"] if f["env"] == "SPOTIFY_REFRESH_TOKEN")
        assert token["action"] == "signin" and token["secret"] and token["is_set"]
        apple = next(c for c in json.loads(resp.body)["connectors"] if c["key"] == "apple_music")
        assert apple["signin"] is None

    def test_the_settings_snapshot_masks_the_token(self, app, monkeypatch):
        monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "AQD-refresh-token-value-long-enough")
        resp = request(app, "GET", "/api/settings")
        assert "AQD-refresh-token-value-long-enough" not in resp.body.decode()
        row = next(f for f in json.loads(resp.body)["fields"] if f["env"] == "SPOTIFY_REFRESH_TOKEN")
        assert row["secret"] and row["is_set"] and row["action"] == "signin"


class TestSettingsWrites:
    @pytest.fixture(autouse=True)
    def _no_disk(self, monkeypatch):
        self.applied: dict[str, str] = {}
        monkeypatch.setattr("yeaboi.config.apply_config_value", lambda k, v: self.applied.__setitem__(k, v))

    def test_set_round_trip(self, app):
        resp = request(app, "POST", "/api/settings/set", {"key": "JIRA_EMAIL", "value": "a@b.c"})
        assert resp.code == 200
        payload = json.loads(resp.body)
        assert payload["ok"] and payload["restart_required"] is False
        assert self.applied == {"JIRA_EMAIL": "a@b.c"}

    def test_bad_shapes_are_400(self, app):
        assert request(app, "POST", "/api/settings/set", {"value": "x"}).code == 400
        assert request(app, "POST", "/api/settings/set", {"key": "JIRA_EMAIL", "value": 3}).code == 400
        assert request(app, "POST", "/api/settings/set", {"key": "NOT_A_KEY", "value": "x"}).code == 400

    def test_allowed_paths_delegates(self, app, monkeypatch):
        saved: list[list[str]] = []
        monkeypatch.setattr("yeaboi.config.set_allowed_paths", lambda paths: saved.append(list(paths)))
        monkeypatch.setattr("yeaboi.config.get_allowed_paths", lambda: ("/a",))
        resp = request(app, "POST", "/api/settings/allowed-paths", {"paths": ["/a"]})
        assert resp.code == 200 and saved == [["/a"]]
        assert request(app, "POST", "/api/settings/allowed-paths", {"paths": "nope"}).code == 400

    def test_data_dir_reports_restart(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.config.set_data_dir", lambda v: None)
        resp = request(app, "POST", "/api/settings/data-dir", {"value": "/tmp/x"})
        assert json.loads(resp.body)["restart_required"] is True


class TestProviderProbes:
    def test_verify_requires_provider(self, app):
        assert request(app, "POST", "/api/settings/provider/verify", {"credential": "k"}).code == 400

    def test_verify_and_models_delegate(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.provider_verification._verify_api_key", lambda card, key: (True, "ok"))
        monkeypatch.setattr("yeaboi.provider_verification.fetch_available_models", lambda card, key: ["m1"])
        verify = request(app, "POST", "/api/settings/provider/verify", {"provider": "anthropic", "credential": "k"})
        assert json.loads(verify.body) == {"ok": True, "message": "ok"}
        models = request(app, "POST", "/api/settings/provider/models", {"provider": "anthropic", "credential": "k"})
        assert json.loads(models.body)["models"][0] == "m1"


class TestConnectionVerify:
    def test_requires_auth_and_kind(self, app):
        assert request(app, "POST", "/api/settings/connection/verify", {"kind": "github"}, authed=False).code == 401
        assert request(app, "POST", "/api/settings/connection/verify", {"token": "t"}).code == 400

    def test_unknown_kind_is_400(self, app):
        resp = request(app, "POST", "/api/settings/connection/verify", {"kind": "gopher"})
        assert resp.code == 400
        assert "unknown connection kind" in json.loads(resp.body)["error"]

    def test_delegates_and_never_echoes_the_token(self, app, monkeypatch):
        secret = "ghp_super-secret-raw-token-value"
        monkeypatch.setattr("yeaboi.provider_verification._verify_vc_token", lambda vc, token: (True, "authenticated"))
        resp = request(app, "POST", "/api/settings/connection/verify", {"kind": "github", "token": secret})
        assert json.loads(resp.body) == {"ok": True, "message": "authenticated"}
        assert secret not in resp.body.decode()

    def test_missing_field_is_400(self, app, monkeypatch):
        monkeypatch.delenv("NOTION_TOKEN", raising=False)
        resp = request(app, "POST", "/api/settings/connection/verify", {"kind": "notion"})
        assert resp.code == 400
        assert "needs token" in json.loads(resp.body)["error"]


class FakeSignIn:
    """A SubscriptionSignIn stand-in with a scriptable lifecycle."""

    def __init__(self, *, starts: bool = True):
        self.starts = starts
        self.url = ""
        self.token = ""
        self.error = ""
        self.code_sent = ""
        self.cancelled = False
        self.polls = 0

    def start(self) -> bool:
        return self.starts

    def poll(self) -> None:
        self.polls += 1

    def send_code(self, code: str) -> None:
        self.code_sent = code

    def cancel(self) -> None:
        self.cancelled = True

    @property
    def awaiting_code(self) -> bool:
        return bool(self.url) and not self.token

    @property
    def done(self) -> bool:
        return bool(self.token or self.error)

    @property
    def message(self) -> str:
        return "Signed in — subscription token saved" if self.token else (self.error or "Sign-in cancelled")


class TestSignIn:
    @pytest.fixture(autouse=True)
    def _fake(self, monkeypatch):
        self.session = FakeSignIn()
        monkeypatch.setattr("yeaboi.claude_auth.SubscriptionSignIn", lambda: self.session)
        self.applied: dict[str, str] = {}
        monkeypatch.setattr("yeaboi.config.apply_config_value", lambda k, v: self.applied.__setitem__(k, v))

    def test_status_without_session(self, app):
        assert json.loads(request(app, "GET", "/api/settings/signin").body) == {"active": False}

    def test_start_then_poll(self, app):
        assert json.loads(request(app, "POST", "/api/settings/signin/start").body)["started"] is True
        self.session.url = "https://claude.ai/oauth"
        payload = json.loads(request(app, "GET", "/api/settings/signin").body)
        assert payload["active"] and payload["url"] == "https://claude.ai/oauth"
        assert payload["awaiting_code"] and not payload["done"]

    def test_failed_start_reports_message(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.claude_auth.SubscriptionSignIn", lambda: FakeSignIn(starts=False))
        payload = json.loads(request(app, "POST", "/api/settings/signin/start").body)
        assert payload["started"] is False

    def test_token_is_persisted_once_and_never_served(self, app):
        request(app, "POST", "/api/settings/signin/start")
        self.session.token = "sk-ant-oat-secret-token"
        first = json.loads(request(app, "GET", "/api/settings/signin").body)
        assert first["done"] and first["ok"] and first["saved"]
        assert self.applied == {
            "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-secret-token",
            "ANTHROPIC_AUTH_MODE": "subscription",
        }
        self.applied.clear()
        second = request(app, "GET", "/api/settings/signin")
        assert self.applied == {}, "the token must be persisted exactly once"
        assert "sk-ant-oat-secret-token" not in second.body.decode()

    def test_code_submission(self, app):
        assert request(app, "POST", "/api/settings/signin/code", {"code": "abc"}).code == 404
        request(app, "POST", "/api/settings/signin/start")
        assert request(app, "POST", "/api/settings/signin/code", {"code": ""}).code == 400
        assert request(app, "POST", "/api/settings/signin/code", {"code": "abc"}).code == 200
        assert self.session.code_sent == "abc"

    def test_cancel_discards_the_session(self, app):
        request(app, "POST", "/api/settings/signin/start")
        assert request(app, "POST", "/api/settings/signin/cancel").code == 200
        assert self.session.cancelled
        assert json.loads(request(app, "GET", "/api/settings/signin").body) == {"active": False}

    def test_restart_cancels_the_previous_session(self, app):
        request(app, "POST", "/api/settings/signin/start")
        old = self.session
        request(app, "POST", "/api/settings/signin/start")
        assert old.cancelled


class TestAccessDoctor:
    """The Cloudflare Access status + probe routes."""

    def test_requires_auth(self, app):
        assert request(app, "GET", "/api/settings/access/state", authed=False).code == 401
        assert request(app, "POST", "/api/settings/access/verify", authed=False).code == 401

    def test_state_shape(self, app):
        payload = json.loads(request(app, "GET", "/api/settings/access/state").body)
        assert {"logged_in", "cert_path", "jwt_installed", "missing_keys"} == set(payload)
        assert isinstance(payload["missing_keys"], list)

    def test_state_names_the_unset_keys(self, app, monkeypatch):
        for key in (
            "CLOUDFLARE_TUNNEL_ID",
            "CLOUDFLARE_TUNNEL_CREDENTIALS",
            "CLOUDFLARE_ACCESS_HOSTNAME",
            "CLOUDFLARE_ACCESS_TEAM",
            "CLOUDFLARE_ACCESS_AUD",
        ):
            monkeypatch.delenv(key, raising=False)
        payload = json.loads(request(app, "GET", "/api/settings/access/state").body)
        assert payload["missing_keys"] == [
            "CLOUDFLARE_TUNNEL_ID",
            "CLOUDFLARE_TUNNEL_CREDENTIALS",
            "CLOUDFLARE_ACCESS_HOSTNAME",
            "CLOUDFLARE_ACCESS_TEAM",
            "CLOUDFLARE_ACCESS_AUD",
        ]

    def test_state_never_resolves_the_binary(self, app, monkeypatch):
        """A settings page opening must not trigger cloudflared's ~38 MB download."""

        def _boom() -> None:
            raise AssertionError("access/state resolved the cloudflared binary")

        monkeypatch.setattr("yeaboi.retro.tunnel.ensure_cloudflared", _boom)
        assert request(app, "GET", "/api/settings/access/state").code == 200

    def test_state_reports_the_cert(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.sharing.access_setup.find_cert", lambda: "")
        payload = json.loads(request(app, "GET", "/api/settings/access/state").body)
        assert payload["logged_in"] is False and payload["cert_path"] == ""

        monkeypatch.setattr("yeaboi.sharing.access_setup.find_cert", lambda: "/x/cert.pem")
        payload = json.loads(request(app, "GET", "/api/settings/access/state").body)
        assert payload["logged_in"] is True and payload["cert_path"] == "/x/cert.pem"

    def test_verify_reports_the_preflight_verdict(self, app, monkeypatch):
        seen: list[bool] = []

        def _preflight(gate, verdict):
            def _run(surface, *, assume_mode=False):
                seen.append(assume_mode)
                return gate, verdict

            return _run

        monkeypatch.setattr("yeaboi.sharing.identity.preflight", _preflight(None, "nope"))
        payload = json.loads(request(app, "POST", "/api/settings/access/verify").body)
        assert payload == {"ok": False, "message": "nope"}

        monkeypatch.setattr("yeaboi.sharing.identity.preflight", _preflight(object(), ""))
        payload = json.loads(request(app, "POST", "/api/settings/access/verify").body)
        assert payload["ok"] is True and payload["message"]

        # The point of the route: it answers before Share Mode is switched on.
        assert seen == [True, True]


class TestConnectionsCatalog:
    """``GET /api/connections`` — the read-only integration catalog."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        from yeaboi.connectors import registry

        for env in registry.all_envs():
            monkeypatch.delenv(env, raising=False)

    def test_requires_auth(self, app):
        assert request(app, "GET", "/api/connections", authed=False).code == 401

    def test_lists_nothing_when_nothing_is_connected(self, app):
        body = json.loads(request(app, "GET", "/api/connections").body)
        assert body["connectors"] == []
        assert body["connected"] == []

    def test_all_shows_the_catalog(self, app):
        body = json.loads(request(app, "GET", "/api/connections?all=1").body)
        from yeaboi.connectors import registry

        # The browse view is the whole roster: connectors plus the built-in
        # integrations as managed_by:"credentials" rows.
        expected = {c.key for c in registry.all_connectors()} | {c.key for c in registry.legacy_entries()}
        assert {c["key"] for c in body["connectors"]} == expected
        assert body["connectors"][0]["connected"] is False
        managed = {c["key"]: c["managed_by"] for c in body["connectors"]}
        assert managed["datadog"] == "connections"
        assert managed["github"] == "credentials"

    def test_never_carries_a_field_value(self, app, monkeypatch):
        secret = "dd-api-key-never-on-the-wire"
        monkeypatch.setenv("DATADOG_API_KEY", secret)
        monkeypatch.setenv("DATADOG_APP_KEY", "app-key-never-on-the-wire")
        raw = request(app, "GET", "/api/connections").body.decode()
        assert secret not in raw
        assert "app-key-never-on-the-wire" not in raw
        assert '"is_set": true' in raw or '"is_set":true' in raw
