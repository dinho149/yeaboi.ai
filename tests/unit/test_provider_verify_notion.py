"""Tests for the Notion token verification used by the setup wizard's Docs step."""

from unittest.mock import MagicMock

from yeaboi.provider_verification import _verify_notion


def _resp(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    return r


class TestVerifyNotion:
    def test_200_ok(self, monkeypatch):
        monkeypatch.setattr("httpx.get", lambda *a, **k: _resp(200))
        ok, msg = _verify_notion("ntn_token")
        assert ok is True
        assert "verified" in msg.lower()

    def test_401_invalid(self, monkeypatch):
        monkeypatch.setattr("httpx.get", lambda *a, **k: _resp(401))
        ok, msg = _verify_notion("bad")
        assert ok is False
        assert "invalid" in msg.lower()

    def test_403_lacks_access(self, monkeypatch):
        monkeypatch.setattr("httpx.get", lambda *a, **k: _resp(403))
        ok, msg = _verify_notion("scoped")
        assert ok is False
        assert "access" in msg.lower()

    def test_unexpected_status(self, monkeypatch):
        monkeypatch.setattr("httpx.get", lambda *a, **k: _resp(500))
        ok, msg = _verify_notion("t")
        assert ok is False
        assert "500" in msg

    def test_sends_notion_version_header(self, monkeypatch):
        captured = {}

        def fake_get(url, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            return _resp(200)

        monkeypatch.setattr("httpx.get", fake_get)
        _verify_notion("ntn_token")
        assert captured["url"].endswith("/v1/users/me")
        assert captured["headers"]["Notion-Version"]
        assert captured["headers"]["Authorization"] == "Bearer ntn_token"

    def test_connection_error_handled(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr("httpx.get", boom)
        ok, msg = _verify_notion("t")
        assert ok is False
        assert "connection error" in msg.lower()
