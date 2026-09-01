"""Datadog verification — two credentials that fail in two different places."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from yeaboi.connectors.datadog import DEFAULT_SITE, SITES, api_base
from yeaboi.provider_verification import INVALID_KEY, _verify_datadog


def _resp(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    return r


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])


class TestApiBase:
    def test_each_site_gets_its_own_host(self):
        for site in SITES:
            assert api_base(site) == f"https://api.{site}"

    def test_blank_and_unknown_fall_back_to_the_default(self):
        assert api_base("") == f"https://api.{DEFAULT_SITE}"
        assert api_base("evil.example.com") == f"https://api.{DEFAULT_SITE}"


class TestVerify:
    def test_both_keys_good(self, monkeypatch):
        monkeypatch.setattr("httpx.get", lambda *a, **k: _resp(200))
        ok, msg = _verify_datadog("key", "app")
        assert ok is True
        assert "verified" in msg.lower()

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_bad_api_key_is_named_as_such(self, monkeypatch, status):
        monkeypatch.setattr("httpx.get", lambda *a, **k: _resp(status))
        ok, msg = _verify_datadog("bad", "app")
        assert ok is False
        assert msg == INVALID_KEY

    @pytest.mark.parametrize("status", [401, 403])
    def test_a_bad_app_key_is_distinguished_from_a_bad_api_key(self, monkeypatch, status):
        # The whole reason Datadog is the proving vendor: reporting this as
        # "invalid API key" would send the user to re-cut the wrong credential.
        calls = []

        def fake_get(url, headers=None, timeout=None):
            calls.append(url)
            return _resp(200 if len(calls) == 1 else status)

        monkeypatch.setattr("httpx.get", fake_get)
        ok, msg = _verify_datadog("good", "bad-app")
        assert ok is False
        assert "application key" in msg.lower()
        assert msg != INVALID_KEY

    def test_unexpected_status_reports_the_code(self, monkeypatch):
        monkeypatch.setattr("httpx.get", lambda *a, **k: _resp(500))
        ok, msg = _verify_datadog("k", "a")
        assert ok is False
        assert "500" in msg

    def test_a_transport_failure_is_not_a_rejection(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr("httpx.get", boom)
        ok, msg = _verify_datadog("k", "a")
        assert ok is False
        assert msg != INVALID_KEY


class TestRequestShape:
    def test_the_api_key_rides_its_own_header_first(self, monkeypatch):
        seen = []

        def fake_get(url, headers=None, timeout=None):
            seen.append((url, dict(headers or {})))
            return _resp(200)

        monkeypatch.setattr("httpx.get", fake_get)
        _verify_datadog("key-1", "app-1", "datadoghq.eu")

        first_url, first_headers = seen[0]
        assert first_url == "https://api.datadoghq.eu/api/v1/validate"
        assert first_headers["DD-API-KEY"] == "key-1"
        # The validate call must NOT carry the app key: that is what makes the
        # second call able to isolate it.
        assert "DD-APPLICATION-KEY" not in first_headers

        second_url, second_headers = seen[1]
        assert second_url.startswith("https://api.datadoghq.eu/api/v1/monitor")
        assert second_headers["DD-APPLICATION-KEY"] == "app-1"

    def test_an_unknown_site_cannot_redirect_the_credential(self, monkeypatch):
        seen = []

        def fake_get(url, headers=None, timeout=None):
            seen.append(url)
            return _resp(200)

        monkeypatch.setattr("httpx.get", fake_get)
        _verify_datadog("key", "app", "attacker.example.com")
        assert all(u.startswith(f"https://api.{DEFAULT_SITE}") for u in seen)
