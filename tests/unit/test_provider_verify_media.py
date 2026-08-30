"""Tests for the ElevenLabs/Tavus key verification behind the voice & face setup."""

from unittest.mock import MagicMock

from yeaboi.provider_verification import _verify_elevenlabs, _verify_tavus


def _resp(status_code: int, body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = body or {}
    return r


class TestVerifyElevenlabs:
    def test_200_ok_names_the_tier(self, monkeypatch):
        monkeypatch.setattr("httpx.get", lambda *a, **k: _resp(200, {"subscription": {"tier": "free"}}))
        ok, msg = _verify_elevenlabs("xi-key")
        assert ok is True
        assert "free tier" in msg.lower()

    def test_200_without_tier_still_verifies(self, monkeypatch):
        r = _resp(200)
        r.json.side_effect = ValueError("not json")
        monkeypatch.setattr("httpx.get", lambda *a, **k: r)
        ok, msg = _verify_elevenlabs("xi-key")
        assert ok is True
        assert "verified" in msg.lower()

    def test_401_invalid(self, monkeypatch):
        monkeypatch.setattr("httpx.get", lambda *a, **k: _resp(401))
        ok, msg = _verify_elevenlabs("bad")
        assert ok is False
        assert "invalid" in msg.lower()

    def test_403_restricted(self, monkeypatch):
        monkeypatch.setattr("httpx.get", lambda *a, **k: _resp(403))
        ok, msg = _verify_elevenlabs("restricted")
        assert ok is False
        assert "restricted" in msg.lower()

    def test_unexpected_status(self, monkeypatch):
        monkeypatch.setattr("httpx.get", lambda *a, **k: _resp(500))
        ok, msg = _verify_elevenlabs("t")
        assert ok is False
        assert "500" in msg

    def test_sends_xi_api_key_header(self, monkeypatch):
        captured = {}

        def fake_get(url, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            return _resp(200)

        monkeypatch.setattr("httpx.get", fake_get)
        _verify_elevenlabs("xi-key")
        assert captured["url"].endswith("/v1/user")
        assert captured["headers"]["xi-api-key"] == "xi-key"

    def test_connection_error_handled(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr("httpx.get", boom)
        ok, msg = _verify_elevenlabs("t")
        assert ok is False
        assert "connection error" in msg.lower()


class TestVerifyTavus:
    def test_200_ok(self, monkeypatch):
        monkeypatch.setattr("httpx.get", lambda *a, **k: _resp(200))
        ok, msg = _verify_tavus("tv-key")
        assert ok is True
        assert "verified" in msg.lower()

    def test_401_invalid(self, monkeypatch):
        monkeypatch.setattr("httpx.get", lambda *a, **k: _resp(401))
        ok, msg = _verify_tavus("bad")
        assert ok is False
        assert "invalid" in msg.lower()

    def test_403_invalid(self, monkeypatch):
        monkeypatch.setattr("httpx.get", lambda *a, **k: _resp(403))
        ok, msg = _verify_tavus("bad")
        assert ok is False
        assert "invalid" in msg.lower()

    def test_unexpected_status(self, monkeypatch):
        monkeypatch.setattr("httpx.get", lambda *a, **k: _resp(500))
        ok, msg = _verify_tavus("t")
        assert ok is False
        assert "500" in msg

    def test_sends_x_api_key_header(self, monkeypatch):
        captured = {}

        def fake_get(url, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            return _resp(200)

        monkeypatch.setattr("httpx.get", fake_get)
        _verify_tavus("tv-key")
        assert captured["url"].endswith("/v2/replicas")
        assert captured["headers"]["x-api-key"] == "tv-key"

    def test_connection_error_handled(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr("httpx.get", boom)
        ok, msg = _verify_tavus("t")
        assert ok is False
        assert "connection error" in msg.lower()
