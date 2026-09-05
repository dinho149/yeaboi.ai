"""The music connectors' probes — keyless, so they ask only whether the vendor answers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from yeaboi.provider_verification import _verify_apple_music, _verify_spotify, _verify_youtube_music


def _resp(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.content = b"{}"
    r.json.return_value = {}
    return r


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])


PROBES = [
    (_verify_spotify, "https://open.spotify.com/oembed?url=", "Spotify's player is reachable"),
    (_verify_apple_music, "https://itunes.apple.com/lookup?id=", "Apple Music's catalogue is reachable"),
    (_verify_youtube_music, "https://www.youtube.com/oembed?url=", "YouTube's player is reachable"),
]


@pytest.mark.parametrize(("probe", "prefix", "verified"), PROBES, ids=lambda p: getattr(p, "__name__", str(p)))
class TestMusicProbes:
    def test_a_200_from_the_public_endpoint_verifies(self, monkeypatch, probe, prefix, verified):
        capture = MagicMock(return_value=_resp(200))
        monkeypatch.setattr("httpx.get", capture)
        assert probe() == (True, verified)
        assert capture.call_args.args[0].startswith(prefix)
        # No credential exists, so none may ride the request.
        assert capture.call_args.kwargs["headers"] == {}

    def test_anything_else_is_named_by_status(self, monkeypatch, probe, prefix, verified):
        monkeypatch.setattr("httpx.get", MagicMock(return_value=_resp(503)))
        assert probe() == (False, "Unexpected response: 503")

    def test_a_transport_failure_is_reported_not_raised(self, monkeypatch, probe, prefix, verified):
        monkeypatch.setattr("httpx.get", MagicMock(side_effect=ConnectionError("boom")))
        ok, message = probe()
        assert ok is False
        assert message


class TestTheProbeUrls:
    def test_the_share_link_rides_the_query_encoded(self, monkeypatch):
        capture = MagicMock(return_value=_resp(200))
        monkeypatch.setattr("httpx.get", capture)
        _verify_spotify()
        url = capture.call_args.args[0]
        assert "url=https%3A%2F%2Fopen.spotify.com%2Fplaylist%2F" in url

    def test_youtube_asks_for_json(self, monkeypatch):
        capture = MagicMock(return_value=_resp(200))
        monkeypatch.setattr("httpx.get", capture)
        _verify_youtube_music()
        assert capture.call_args.args[0].endswith("&format=json")
