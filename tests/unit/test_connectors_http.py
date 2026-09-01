"""The guard between a stored setting and a request leaving the machine.

A connector's base URL can be user-owned (self-hosted Grafana, self-hosted
Sentry), which is a reach the vendor-fixed tools never had. These are the tests
that keep it from reaching the cloud metadata endpoint.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from yeaboi.connectors.http import UnsafeUrlError, assert_safe_url, probe_status


class TestScheme:
    def test_https_is_required(self):
        with pytest.raises(UnsafeUrlError, match="https"):
            assert_safe_url("http://api.datadoghq.com/v1")

    def test_a_public_https_url_passes(self, monkeypatch):
        monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
        assert assert_safe_url("https://api.datadoghq.com/v1") == "https://api.datadoghq.com/v1"

    def test_embedded_credentials_are_refused(self):
        with pytest.raises(UnsafeUrlError, match="credentials"):
            assert_safe_url("https://user:pw@api.datadoghq.com/v1")

    def test_a_url_with_no_host_is_refused(self):
        with pytest.raises(UnsafeUrlError):
            assert_safe_url("https:///v1")


class TestBlockedHosts:
    @pytest.mark.parametrize("host", ["localhost", "metadata", "metadata.google.internal", "app.localhost"])
    def test_named_internal_hosts(self, host):
        with pytest.raises(UnsafeUrlError):
            assert_safe_url(f"https://{host}/v1")


class TestLiterals:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",  # loopback
            "10.0.0.1",  # RFC1918
            "192.168.1.5",
            "172.16.0.9",
            "169.254.169.254",  # cloud metadata — the one that steals credentials
            "0.0.0.0",
            "[::1]",
        ],
    )
    def test_private_literals_are_refused(self, ip):
        with pytest.raises(UnsafeUrlError, match="not a public address"):
            assert_safe_url(f"https://{ip}/v1")

    def test_a_public_literal_passes(self):
        assert assert_safe_url("https://93.184.216.34/v1")


class TestResolution:
    def test_a_name_resolving_into_the_private_range_is_refused(self, monkeypatch):
        # The interesting attack: a public-looking name whose DNS answer is
        # internal. The literal check alone would let this through.
        monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("169.254.169.254", 443))])
        with pytest.raises(UnsafeUrlError, match="private address"):
            assert_safe_url("https://totally-fine.example.com/v1")

    def test_an_unresolvable_name_is_allowed_through(self, monkeypatch):
        # A name that resolves to nothing can reach nothing, and failing here
        # would turn a DNS blip into a confusing configuration error.
        def boom(*a, **k):
            raise OSError("no such host")

        monkeypatch.setattr("socket.getaddrinfo", boom)
        assert assert_safe_url("https://nope.example.com/v1")


class TestProbeStatus:
    def test_a_completed_request_reports_its_status(self, monkeypatch):
        monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
        resp = MagicMock()
        resp.status_code = 403
        monkeypatch.setattr("httpx.get", lambda *a, **k: resp)
        assert probe_status("https://api.example.com/v1", headers={}) == (403, "")

    def test_an_unsafe_url_never_reaches_httpx(self, monkeypatch):
        def fail(*a, **k):
            raise AssertionError("httpx.get must not be called for a blocked URL")

        monkeypatch.setattr("httpx.get", fail)
        status, message = probe_status("https://169.254.169.254/v1", headers={})
        assert status == 0
        assert "not a public address" in message

    def test_a_transport_error_is_redacted(self, monkeypatch):
        monkeypatch.setenv("DATADOG_API_KEY", "dd-secret-value-abcdefgh")
        monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])

        def boom(*a, **k):
            raise RuntimeError("failed on https://api.example.com/v1?key=dd-secret-value-abcdefgh")

        monkeypatch.setattr("httpx.get", boom)
        status, message = probe_status("https://api.example.com/v1", headers={})
        assert status == 0
        assert "dd-secret-value-abcdefgh" not in message
