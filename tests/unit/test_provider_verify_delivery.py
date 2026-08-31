"""Verification for the delivery-family connectors.

Linear is the one GraphQL probe in the set; Trello is the one whose credentials
ride the query string. Both facts are asserted here so a refactor cannot
silently change what leaves the machine.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from yeaboi.provider_verification import INVALID_KEY, _verify_linear, _verify_trello


def _resp(status_code: int, body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.content = b"{}"
    r.json.return_value = body if body is not None else {}
    return r


class TestLinear:
    def test_it_posts_the_viewer_query_with_the_key_as_authorization(self, monkeypatch):
        capture = MagicMock(return_value=_resp(200, {"data": {"viewer": {"id": "u1"}}}))
        monkeypatch.setattr("httpx.post", capture)
        ok, msg = _verify_linear("lin_api_key")
        assert (ok, msg) == (True, "Linear verified")
        url = capture.call_args.args[0]
        kwargs = capture.call_args.kwargs
        assert url == "https://api.linear.app/graphql"
        assert kwargs["headers"]["Authorization"] == "lin_api_key"
        assert kwargs["json"] == {"query": "{ viewer { id } }"}

    @pytest.mark.parametrize("status", [400, 401, 403])
    def test_a_rejected_key_is_the_invalid_key_message(self, monkeypatch, status):
        monkeypatch.setattr("httpx.post", MagicMock(return_value=_resp(status)))
        ok, msg = _verify_linear("bad")
        assert (ok, msg) == (False, INVALID_KEY)

    def test_a_graphql_error_body_is_a_rejection_even_on_200(self, monkeypatch):
        # Linear answers 200 with an errors array for an unauthenticated query.
        monkeypatch.setattr("httpx.post", MagicMock(return_value=_resp(200, {"errors": [{"message": "auth"}]})))
        ok, msg = _verify_linear("bad")
        assert (ok, msg) == (False, INVALID_KEY)

    def test_a_transport_failure_is_redacted(self, monkeypatch):
        # The redactor scrubs the values of configured secret envs, and
        # LINEAR_API_KEY is one — a transport error quoting the request must
        # not hand the key back to the screen.
        monkeypatch.setenv("LINEAR_API_KEY", "lin_api_key_value")

        def boom(*a, **k):
            raise OSError("connect to api.linear.app failed for key lin_api_key_value")

        monkeypatch.setattr("httpx.post", boom)
        ok, msg = _verify_linear("lin_api_key_value")
        assert ok is False
        assert "lin_api_key_value" not in msg


class TestTrello:
    def test_it_sends_both_credentials_on_the_query_string(self, monkeypatch):
        capture = MagicMock(return_value=_resp(200))
        monkeypatch.setattr("httpx.get", capture)
        ok, msg = _verify_trello("k123", "t456")
        assert (ok, msg) == (True, "Trello verified")
        url = capture.call_args.args[0]
        assert url.startswith("https://api.trello.com/1/members/me?")
        assert "key=k123" in url and "token=t456" in url

    def test_credentials_are_urlencoded_not_interpolated(self, monkeypatch):
        capture = MagicMock(return_value=_resp(200))
        monkeypatch.setattr("httpx.get", capture)
        _verify_trello("k&x=1", "t#frag")
        url = capture.call_args.args[0]
        assert "key=k%26x%3D1" in url and "token=t%23frag" in url

    @pytest.mark.parametrize("status", [401, 403])
    def test_rejected_credentials_are_the_invalid_key_message(self, monkeypatch, status):
        monkeypatch.setattr("httpx.get", MagicMock(return_value=_resp(status)))
        ok, msg = _verify_trello("k", "t")
        assert (ok, msg) == (False, INVALID_KEY)

    def test_no_failure_message_carries_a_credential(self, monkeypatch):
        # The URL holds both secrets, so a message that quoted it would leak.
        monkeypatch.setattr("httpx.get", MagicMock(return_value=_resp(500)))
        ok, msg = _verify_trello("k123", "t456")
        assert ok is False
        assert "k123" not in msg and "t456" not in msg
