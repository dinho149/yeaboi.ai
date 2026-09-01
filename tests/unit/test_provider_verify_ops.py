"""Verification for the stage-2 ops connectors.

Same shape as the Datadog tests: no cassettes, because the assertion we want of
a hand-written httpx call is "we sent *this* URL with *these* headers", which a
VCR match on method/scheme/host/port/path cannot make.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from yeaboi.connectors.gitlab import DEFAULT_BASE_URL as GITLAB_DEFAULT
from yeaboi.connectors.gitlab import api_base as gitlab_base
from yeaboi.connectors.incidentio import API_BASE
from yeaboi.connectors.sentry import DEFAULT_BASE_URL
from yeaboi.connectors.sentry import api_base as sentry_base
from yeaboi.provider_verification import (
    INVALID_KEY,
    _verify_bitbucket,
    _verify_circleci,
    _verify_gitlab,
    _verify_grafana,
    _verify_incidentio,
    _verify_jenkins,
    _verify_pagerduty,
    _verify_sentry,
    _verify_statuspage,
)


def _resp(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    return r


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])


class _Capture:
    """Records the one request the probe makes."""

    def __init__(self, status: int = 200):
        self.url = ""
        self.headers: dict[str, str] = {}
        self.status = status

    def __call__(self, url, *, headers=None, timeout=None, **kwargs):
        self.url = url
        self.headers = headers or {}
        return _resp(self.status)


class TestGrafana:
    def test_it_asks_the_org_endpoint_with_a_bearer_token(self, monkeypatch):
        capture = _Capture()
        monkeypatch.setattr("httpx.get", capture)
        ok, msg = _verify_grafana("https://team.grafana.net/", "tok")
        assert ok is True
        assert capture.url == "https://team.grafana.net/api/org"
        assert capture.headers["Authorization"] == "Bearer tok"

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_rejected_token_is_named(self, monkeypatch, status):
        monkeypatch.setattr("httpx.get", _Capture(status))
        ok, msg = _verify_grafana("https://team.grafana.net", "bad")
        assert (ok, msg) == (False, INVALID_KEY)

    def test_a_404_blames_the_url_not_the_token(self, monkeypatch):
        # Pointing at something that is not Grafana is the likeliest mistake for
        # the one connector whose host the user types.
        monkeypatch.setattr("httpx.get", _Capture(404))
        ok, msg = _verify_grafana("https://example.com", "tok")
        assert ok is False
        assert "base URL" in msg

    def test_a_private_host_never_leaves_the_machine(self, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr("httpx.get", called)
        ok, msg = _verify_grafana("https://10.0.0.5", "tok")
        assert ok is False
        called.assert_not_called()

    def test_http_is_refused(self, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr("httpx.get", called)
        ok, _ = _verify_grafana("http://team.grafana.net", "tok")
        assert ok is False
        called.assert_not_called()

    def test_the_token_never_appears_in_the_failure(self, monkeypatch):
        monkeypatch.setattr("httpx.get", _Capture(500))
        ok, msg = _verify_grafana("https://team.grafana.net", "super-secret-token")
        assert ok is False
        assert "super-secret-token" not in msg


class TestPagerDuty:
    def test_it_uses_the_token_scheme_and_versioned_accept(self, monkeypatch):
        capture = _Capture()
        monkeypatch.setattr("httpx.get", capture)
        ok, _ = _verify_pagerduty("k")
        assert ok is True
        assert capture.url == "https://api.pagerduty.com/abilities"
        assert capture.headers["Authorization"] == "Token token=k"
        assert "version=2" in capture.headers["Accept"]

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_rejected_key_is_named(self, monkeypatch, status):
        monkeypatch.setattr("httpx.get", _Capture(status))
        assert _verify_pagerduty("bad") == (False, INVALID_KEY)


class _JsonCapture(_Capture):
    """Like :class:`_Capture`, but the probe reads the body."""

    def __init__(self, status: int = 200, payload=None):
        super().__init__(status)
        self.payload = payload if payload is not None else {}

    def __call__(self, url, *, headers=None, timeout=None, **kwargs):
        resp = super().__call__(url, headers=headers, timeout=timeout, **kwargs)
        resp.json.return_value = self.payload
        return resp


class TestIncidentIO:
    def test_it_asks_the_identity_endpoint_with_a_bearer_token(self, monkeypatch):
        capture = _JsonCapture(payload={"identity": {"name": "yeaboi", "roles": []}})
        monkeypatch.setattr("httpx.get", capture)
        ok, msg = _verify_incidentio("k")
        assert ok is True
        assert capture.url == f"{API_BASE}/v1/identity"
        assert capture.headers["Authorization"] == "Bearer k"
        assert msg == "incident.io verified"

    def test_it_reports_the_roles_the_key_carries(self, monkeypatch):
        # A key that can write still verifies — the point is that the user is
        # told, rather than taking read-only on trust.
        monkeypatch.setattr("httpx.get", _JsonCapture(payload={"identity": {"roles": ["viewer", "incident_creator"]}}))
        ok, msg = _verify_incidentio("k")
        assert ok is True
        assert "viewer, incident_creator" in msg

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_rejected_key_is_named(self, monkeypatch, status):
        monkeypatch.setattr("httpx.get", _JsonCapture(status))
        assert _verify_incidentio("bad") == (False, INVALID_KEY)

    def test_an_unreadable_body_still_verifies(self, monkeypatch):
        # The roles are a nicety; a 200 already proves the credential.
        def _raising(url, *, headers=None, timeout=None, **kwargs):
            resp = _resp(200)
            resp.json.side_effect = ValueError("not json")
            return resp

        monkeypatch.setattr("httpx.get", _raising)
        assert _verify_incidentio("k") == (True, "incident.io verified")

    def test_the_token_never_appears_in_the_failure(self, monkeypatch):
        monkeypatch.setattr("httpx.get", _JsonCapture(500))
        ok, msg = _verify_incidentio("super-secret-token")
        assert ok is False
        assert "super-secret-token" not in msg


class TestSentry:
    def test_it_defaults_to_sentry_io(self):
        assert sentry_base("") == DEFAULT_BASE_URL
        assert sentry_base("https://sentry.acme.dev/") == "https://sentry.acme.dev"

    def test_it_scopes_the_call_to_the_org(self, monkeypatch):
        capture = _Capture()
        monkeypatch.setattr("httpx.get", capture)
        ok, _ = _verify_sentry("tok", "acme")
        assert ok is True
        assert capture.url == "https://sentry.io/api/0/organizations/acme/"
        assert capture.headers["Authorization"] == "Bearer tok"

    def test_a_slug_cannot_escape_its_path_segment(self, monkeypatch):
        # A slug is user input landing in a URL path; unquoted, "a/../../x" would
        # reach another endpoint entirely.
        capture = _Capture()
        monkeypatch.setattr("httpx.get", capture)
        _verify_sentry("tok", "acme/../../evil")
        assert capture.url == "https://sentry.io/api/0/organizations/acme%2F..%2F..%2Fevil/"

    def test_an_empty_slug_is_refused_before_the_request(self, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr("httpx.get", called)
        ok, msg = _verify_sentry("tok", "   ")
        assert ok is False
        assert "slug" in msg
        called.assert_not_called()

    def test_a_404_blames_the_slug_not_the_token(self, monkeypatch):
        monkeypatch.setattr("httpx.get", _Capture(404))
        ok, msg = _verify_sentry("tok", "nope")
        assert ok is False
        assert "slug" in msg

    def test_a_self_hosted_url_is_still_guarded(self, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr("httpx.get", called)
        ok, _ = _verify_sentry("tok", "acme", "https://127.0.0.1:9000")
        assert ok is False
        called.assert_not_called()


class TestGitLab:
    def test_it_defaults_to_gitlab_com(self):
        assert gitlab_base("") == GITLAB_DEFAULT
        assert gitlab_base("https://git.acme.dev/") == "https://git.acme.dev"

    def test_it_asks_the_user_endpoint_with_the_private_token_header(self, monkeypatch):
        capture = _Capture()
        monkeypatch.setattr("httpx.get", capture)
        ok, _ = _verify_gitlab("tok")
        assert ok is True
        assert capture.url == "https://gitlab.com/api/v4/user"
        assert capture.headers == {"PRIVATE-TOKEN": "tok"}

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_rejected_token_is_the_invalid_key_message(self, monkeypatch, status):
        monkeypatch.setattr("httpx.get", _Capture(status))
        ok, msg = _verify_gitlab("tok")
        assert (ok, msg) == (False, INVALID_KEY)

    def test_a_404_blames_the_base_url_not_the_token(self, monkeypatch):
        monkeypatch.setattr("httpx.get", _Capture(404))
        ok, msg = _verify_gitlab("tok", "https://intranet.acme.dev")
        assert ok is False
        assert "base URL" in msg

    def test_a_self_hosted_url_is_still_guarded(self, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr("httpx.get", called)
        ok, _ = _verify_gitlab("tok", "https://127.0.0.1:8443")
        assert ok is False
        called.assert_not_called()


class TestBitbucket:
    def test_it_scopes_the_call_to_the_workspace_with_basic_auth(self, monkeypatch):
        capture = _Capture()
        monkeypatch.setattr("httpx.get", capture)
        ok, _ = _verify_bitbucket("dev@acme.test", "tok", "acme")
        assert ok is True
        assert capture.url == "https://api.bitbucket.org/2.0/workspaces/acme"
        assert capture.headers["Authorization"].startswith("Basic ")

    def test_a_workspace_cannot_escape_its_path_segment(self, monkeypatch):
        capture = _Capture()
        monkeypatch.setattr("httpx.get", capture)
        _verify_bitbucket("dev@acme.test", "tok", "acme/../../evil")
        assert capture.url == "https://api.bitbucket.org/2.0/workspaces/acme%2F..%2F..%2Fevil"

    def test_an_empty_workspace_is_refused_before_the_request(self, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr("httpx.get", called)
        ok, msg = _verify_bitbucket("dev@acme.test", "tok", "   ")
        assert ok is False
        assert "workspace" in msg
        called.assert_not_called()

    @pytest.mark.parametrize("status", [401, 403])
    def test_rejected_credentials_are_the_invalid_key_message(self, monkeypatch, status):
        monkeypatch.setattr("httpx.get", _Capture(status))
        ok, msg = _verify_bitbucket("dev@acme.test", "tok", "acme")
        assert (ok, msg) == (False, INVALID_KEY)

    def test_a_404_blames_the_workspace_not_the_credentials(self, monkeypatch):
        monkeypatch.setattr("httpx.get", _Capture(404))
        ok, msg = _verify_bitbucket("dev@acme.test", "tok", "nope")
        assert ok is False
        assert "workspace" in msg


class TestCircleCI:
    def test_it_lists_pipelines_for_the_org_with_the_token_header(self, monkeypatch):
        capture = _Capture()
        monkeypatch.setattr("httpx.get", capture)
        ok, _ = _verify_circleci("tok", "gh/acme")
        assert ok is True
        assert capture.url == "https://circleci.com/api/v2/pipeline?org-slug=gh%2Facme"
        assert capture.headers["Circle-Token"] == "tok"

    def test_a_slug_with_a_query_metacharacter_stays_one_parameter(self, monkeypatch):
        capture = _Capture()
        monkeypatch.setattr("httpx.get", capture)
        _verify_circleci("tok", "gh/acme&page-token=evil")
        assert "org-slug=gh%2Facme%26page-token%3Devil" in capture.url

    def test_an_empty_slug_is_refused_before_the_request(self, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr("httpx.get", called)
        ok, msg = _verify_circleci("tok", "   ")
        assert ok is False
        assert "org slug" in msg
        called.assert_not_called()

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_rejected_token_is_named(self, monkeypatch, status):
        monkeypatch.setattr("httpx.get", _Capture(status))
        assert _verify_circleci("bad", "gh/acme") == (False, INVALID_KEY)

    def test_a_404_blames_the_slug_not_the_token(self, monkeypatch):
        monkeypatch.setattr("httpx.get", _Capture(404))
        ok, msg = _verify_circleci("tok", "acme")
        assert ok is False
        assert "slug" in msg

    def test_the_token_never_appears_in_the_failure(self, monkeypatch):
        monkeypatch.setattr("httpx.get", _Capture(500))
        ok, msg = _verify_circleci("super-secret-token", "gh/acme")
        assert ok is False
        assert "super-secret-token" not in msg


class TestJenkins:
    def test_it_asks_who_am_i_with_basic_auth(self, monkeypatch):
        capture = _Capture()
        monkeypatch.setattr("httpx.get", capture)
        ok, _ = _verify_jenkins("https://ci.acme.test/", "dev", "tok")
        assert ok is True
        assert capture.url == "https://ci.acme.test/me/api/json"
        assert capture.headers["Authorization"].startswith("Basic ")

    @pytest.mark.parametrize("status", [401, 403])
    def test_rejected_credentials_are_the_invalid_key_message(self, monkeypatch, status):
        monkeypatch.setattr("httpx.get", _Capture(status))
        assert _verify_jenkins("https://ci.acme.test", "dev", "bad") == (False, INVALID_KEY)

    def test_a_404_blames_the_url_not_the_token(self, monkeypatch):
        monkeypatch.setattr("httpx.get", _Capture(404))
        ok, msg = _verify_jenkins("https://example.com", "dev", "tok")
        assert ok is False
        assert "base URL" in msg

    def test_a_private_host_never_leaves_the_machine(self, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr("httpx.get", called)
        ok, _ = _verify_jenkins("https://10.0.0.5", "dev", "tok")
        assert ok is False
        called.assert_not_called()

    def test_http_is_refused(self, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr("httpx.get", called)
        ok, _ = _verify_jenkins("http://ci.acme.test", "dev", "tok")
        assert ok is False
        called.assert_not_called()

    def test_the_token_never_appears_in_the_failure(self, monkeypatch):
        monkeypatch.setattr("httpx.get", _Capture(500))
        ok, msg = _verify_jenkins("https://ci.acme.test", "dev", "super-secret-token")
        assert ok is False
        assert "super-secret-token" not in msg


class TestStatuspage:
    def test_it_reads_the_page_with_the_oauth_scheme(self, monkeypatch):
        capture = _Capture()
        monkeypatch.setattr("httpx.get", capture)
        ok, _ = _verify_statuspage("tok", "abc123")
        assert ok is True
        assert capture.url == "https://api.statuspage.io/v1/pages/abc123"
        assert capture.headers["Authorization"] == "OAuth tok"

    def test_a_page_id_cannot_escape_its_path_segment(self, monkeypatch):
        capture = _Capture()
        monkeypatch.setattr("httpx.get", capture)
        _verify_statuspage("tok", "abc/../../evil")
        assert capture.url == "https://api.statuspage.io/v1/pages/abc%2F..%2F..%2Fevil"

    def test_an_empty_page_id_is_refused_before_the_request(self, monkeypatch):
        called = MagicMock()
        monkeypatch.setattr("httpx.get", called)
        ok, msg = _verify_statuspage("tok", "  ")
        assert ok is False
        assert "page ID" in msg
        called.assert_not_called()

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_rejected_key_is_named(self, monkeypatch, status):
        monkeypatch.setattr("httpx.get", _Capture(status))
        assert _verify_statuspage("bad", "abc123") == (False, INVALID_KEY)

    def test_a_404_blames_the_page_not_the_key(self, monkeypatch):
        monkeypatch.setattr("httpx.get", _Capture(404))
        ok, msg = _verify_statuspage("tok", "nope")
        assert ok is False
        assert "page" in msg

    def test_the_key_never_appears_in_the_failure(self, monkeypatch):
        monkeypatch.setattr("httpx.get", _Capture(500))
        ok, msg = _verify_statuspage("super-secret-token", "abc123")
        assert ok is False
        assert "super-secret-token" not in msg
