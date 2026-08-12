"""Unit tests for the Retro Cloudflare tunnel helper (hermetic — no network)."""

import hashlib
import platform
import stat
import time
import urllib.error
import urllib.request

import pytest

from yeaboi.retro import tunnel


class _FakeResp:
    """Minimal context-manager stand-in for urllib's urlopen response."""

    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestChecksumVerification:
    def test_matching_hash_passes(self, monkeypatch):
        data = b"legit cloudflared bytes"
        monkeypatch.setitem(tunnel._ASSET_SHA256, "asset-x", hashlib.sha256(data).hexdigest())
        tunnel._verify_sha256("asset-x", data)  # must not raise

    def test_mismatched_hash_raises(self, monkeypatch):
        monkeypatch.setitem(tunnel._ASSET_SHA256, "asset-x", "0" * 64)
        with pytest.raises(OSError, match="checksum mismatch"):
            tunnel._verify_sha256("asset-x", b"tampered")

    def test_unknown_asset_is_refused(self):
        with pytest.raises(OSError, match="no pinned checksum"):
            tunnel._verify_sha256("asset-never-pinned", b"x")

    def test_release_base_is_pinned_not_latest(self):
        assert "latest" not in tunnel._RELEASE_BASE
        assert tunnel._CLOUDFLARED_VERSION in tunnel._RELEASE_BASE


class TestDownloadIntegrity:
    def test_tampered_payload_never_lands_on_disk(self, tmp_path, monkeypatch):
        dest = tmp_path / "cloudflared"
        monkeypatch.setattr(tunnel, "_asset_name", lambda *a: ("cloudflared-linux-amd64", False))

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResp(b"malicious"))
        with pytest.raises(OSError, match="checksum mismatch"):
            tunnel._download_cloudflared(dest)
        assert not dest.exists()
        assert not dest.with_suffix(dest.suffix + ".part").exists()

    def test_valid_payload_is_installed_owner_execute_only(self, tmp_path, monkeypatch):
        dest = tmp_path / "cloudflared"
        data = b"valid-binary"
        monkeypatch.setattr(tunnel, "_asset_name", lambda *a: ("asset-ok", False))
        monkeypatch.setitem(tunnel._ASSET_SHA256, "asset-ok", hashlib.sha256(data).hexdigest())

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResp(data))
        out = tunnel._download_cloudflared(dest)
        assert out == dest and dest.read_bytes() == data
        mode = dest.stat().st_mode
        assert mode & stat.S_IXUSR  # owner can execute
        assert not (mode & stat.S_IXGRP) and not (mode & stat.S_IXOTH)  # group/other cannot


class TestAssetName:
    def test_darwin_arm64_is_tgz(self):
        name, is_tgz = tunnel._asset_name("Darwin", "arm64")
        assert name == "cloudflared-darwin-arm64.tgz" and is_tgz is True

    def test_linux_amd64_is_raw(self):
        name, is_tgz = tunnel._asset_name("Linux", "x86_64")
        assert name == "cloudflared-linux-amd64" and is_tgz is False

    def test_windows_amd64_exe(self):
        name, is_tgz = tunnel._asset_name("Windows", "AMD64")
        assert name == "cloudflared-windows-amd64.exe" and is_tgz is False

    def test_unsupported_platform_raises(self):
        with pytest.raises(OSError):
            tunnel._asset_name("Plan9", "sparc")


class TestUrlRegex:
    def test_matches_banner_line(self):
        line = "2026-07-10 INF |  https://calm-tree-1234.trycloudflare.com  |"
        m = tunnel._URL_RE.search(line)
        assert m and m.group(0) == "https://calm-tree-1234.trycloudflare.com"

    def test_no_match_on_unrelated(self):
        assert tunnel._URL_RE.search("registered tunnel connection") is None


class TestEnsureCloudflared:
    def test_env_override_wins(self, tmp_path, monkeypatch):
        fake = tmp_path / "cf"
        fake.write_text("x")
        monkeypatch.setenv("CLOUDFLARED_PATH", str(fake))
        assert tunnel.ensure_cloudflared() == fake

    def test_uses_binary_on_path(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLOUDFLARED_PATH", raising=False)
        monkeypatch.setattr(tunnel.shutil, "which", lambda name: "/usr/local/bin/cloudflared")
        assert str(tunnel.ensure_cloudflared()) == "/usr/local/bin/cloudflared"

    def test_uses_cached_copy(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLOUDFLARED_PATH", raising=False)
        monkeypatch.setattr(tunnel.shutil, "which", lambda name: None)
        cached = tmp_path / "cloudflared"
        cached.write_text("x")
        monkeypatch.setattr(tunnel, "_cached_binary_path", lambda: cached)
        # _download_cloudflared must NOT be called when the cache exists.
        monkeypatch.setattr(tunnel, "_download_cloudflared", lambda *a, **k: pytest.fail("should not download"))
        assert tunnel.ensure_cloudflared() == cached

    def test_download_failure_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLOUDFLARED_PATH", raising=False)
        monkeypatch.setattr(tunnel.shutil, "which", lambda name: None)
        monkeypatch.setattr(tunnel, "_cached_binary_path", lambda: tmp_path / "nope")

        def _boom(*a, **k):
            raise OSError("network down")

        monkeypatch.setattr(tunnel, "_download_cloudflared", _boom)
        assert tunnel.ensure_cloudflared() is None


def _fake_cloudflared(tmp_path, *, emit_url: bool, register: bool = True) -> "object":
    """Write a fake cloudflared shell script that mimics stderr output.

    ``register`` controls whether it also emits the ``Registered tunnel connection``
    line — the real readiness signal. URL-without-registration is exactly the state
    that serves Cloudflare error 1033 to visitors.
    """
    script = tmp_path / "cloudflared"
    if emit_url:
        lines = ['echo "INF |  https://fake-tunnel-abcd.trycloudflare.com  |" >&2']
        if register:
            lines.append('echo "INF Registered tunnel connection connIndex=0 protocol=quic" >&2')
        # sleep 1, not longer: stop() joins the reader thread, which only ends when the
        # fake exits and releases the stderr fd — a long sleep is pure test wall-clock.
        body = "#!/bin/sh\n" + "\n".join(lines) + "\nsleep 1\n"
    else:
        body = "#!/bin/sh\necho 'INF starting' >&2\nexit 0\n"
    script.write_text(body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.mark.skipif(platform.system() == "Windows", reason="fake sh script is POSIX-only")
class TestCloudflareTunnel:
    def test_start_returns_url_then_stops(self, tmp_path, monkeypatch):
        # Skip the real DNS-liveness poll (the fake URL never resolves).
        monkeypatch.setattr(tunnel.CloudflareTunnel, "_wait_dns_live", lambda self, host, *, deadline: True)
        binary = _fake_cloudflared(tmp_path, emit_url=True)
        t = tunnel.CloudflareTunnel(5173, binary=binary)
        url = t.start(timeout=10)
        assert url == "https://fake-tunnel-abcd.trycloudflare.com"
        assert t.public_url == url
        t.stop()
        assert t._proc is None

    def test_start_returns_none_when_no_url(self, tmp_path):
        binary = _fake_cloudflared(tmp_path, emit_url=False)
        t = tunnel.CloudflareTunnel(5173, binary=binary)
        assert t.start(timeout=5) is None

    def test_url_without_registration_is_a_failure(self, tmp_path, monkeypatch, caplog):
        # The URL banner prints before cloudflared has connected to the edge; if no
        # connection ever registers, visitors get Cloudflare error 1033. start() must
        # treat that as a failure, not hand out a dead URL.
        monkeypatch.setattr(tunnel, "_REGISTER_GRACE", 1.0)
        binary = _fake_cloudflared(tmp_path, emit_url=True, register=False)
        t = tunnel.CloudflareTunnel(5173, binary=binary)
        with caplog.at_level("WARNING", logger="yeaboi.retro.tunnel"):
            assert t.start(timeout=1) is None
        assert t.public_url == ""
        assert any("1033" in r.getMessage() for r in caplog.records)

    def test_stop_during_start_prevents_region_retry(self, tmp_path, monkeypatch):
        # A teardown that lands mid-handshake must cancel the whole start(), never be
        # followed by the --region retry launching a cloudflared nobody will stop.
        launches = []

        def fake_attempt(self, binary, *, deadline, extra_args=()):
            launches.append(extra_args)
            self._log_tail.append("ERR expected at least 2 Cloudflare Regions regions, but SRV only returned 1")
            self.stop()  # the owner's finally fires while the attempt is in flight
            return None

        monkeypatch.setattr(tunnel.CloudflareTunnel, "_attempt", fake_attempt)
        t = tunnel.CloudflareTunnel(5173, binary=tmp_path / "unused")
        assert t.start(timeout=5) is None
        assert launches == [()]

    def test_region_srv_failure_retries_pinned_to_us(self, tmp_path, monkeypatch):
        # A broken local resolver (ISP router / filtering DNS) makes cloudflared exit with
        # "expected at least 2 Cloudflare Regions…" right after the URL banner — permanent
        # 1033 for visitors. start() must retry pinned to --region us, which works there.
        monkeypatch.setattr(tunnel.CloudflareTunnel, "_wait_dns_live", lambda self, host, *, deadline: True)
        script = tmp_path / "cloudflared"
        marker = tmp_path / "launches"
        script.write_text(
            "#!/bin/sh\n"
            f'echo run >> "{marker}"\n'
            'case "$*" in\n'
            "  *--region\\ us*)\n"
            '    echo "INF |  https://fake-tunnel-abcd.trycloudflare.com  |" >&2\n'
            '    echo "INF Registered tunnel connection connIndex=0 protocol=quic" >&2\n'
            "    sleep 1;;\n"
            "  *)\n"
            '    echo "INF |  https://dead-tunnel-abcd.trycloudflare.com  |" >&2\n'
            "    echo 'ERR Initiating shutdown error=\"expected at least 2 Cloudflare Regions"
            " regions, but SRV only returned 1\"' >&2\n"
            "    exit 1;;\n"
            "esac\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        t = tunnel.CloudflareTunnel(5173, binary=script)
        assert t.start(timeout=10) == "https://fake-tunnel-abcd.trycloudflare.com"
        t.stop()
        assert marker.read_text().count("run") == 2

    def test_other_failures_do_not_retry(self, tmp_path):
        # The region retry is targeted: any other exit reason still fails once, cleanly.
        script = tmp_path / "cloudflared"
        marker = tmp_path / "launches"
        script.write_text(f'#!/bin/sh\necho run >> "{marker}"\necho "ERR some other failure" >&2\nexit 1\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        t = tunnel.CloudflareTunnel(5173, binary=script)
        assert t.start(timeout=5) is None
        assert marker.read_text().count("run") == 1

    def test_start_none_when_binary_unavailable(self, monkeypatch):
        monkeypatch.setattr(tunnel, "ensure_cloudflared", lambda: None)
        t = tunnel.CloudflareTunnel(5173)
        assert t.start(timeout=2) is None

    def test_stderr_is_captured_and_surfaced_on_failure(self, tmp_path, caplog):
        # cloudflared's own output must be logged + kept, so a failure is diagnosable
        # (previously every non-URL line was silently discarded).
        script = tmp_path / "cloudflared"
        script.write_text("#!/bin/sh\necho 'ERR failed to connect to edge: QUIC blocked' >&2\nexit 1\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        t = tunnel.CloudflareTunnel(5173, binary=script)
        with caplog.at_level("DEBUG", logger="yeaboi.retro.tunnel"):
            assert t.start(timeout=5) is None
        assert any("QUIC blocked" in line for line in t._log_tail)
        # And it's surfaced at warning level (visible without DEBUG).
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any("QUIC blocked" in m for m in warnings)

    def test_stderr_logged_on_success(self, tmp_path, caplog, monkeypatch):
        monkeypatch.setattr(tunnel.CloudflareTunnel, "_wait_dns_live", lambda self, host, *, deadline: True)
        binary = _fake_cloudflared(tmp_path, emit_url=True)
        t = tunnel.CloudflareTunnel(5173, binary=binary)
        with caplog.at_level("DEBUG", logger="yeaboi.retro.tunnel"):
            url = t.start(timeout=10)
        t.stop()
        assert url
        assert any("cloudflared:" in r.getMessage() for r in caplog.records)

    def test_auto_expiry_stops_tunnel_and_calls_on_expire(self, tmp_path, monkeypatch):
        # A tiny (fractional-minute) budget so the test doesn't wait a full timeout.
        monkeypatch.setattr("yeaboi.config.get_tunnel_timeout_minutes", lambda: 0.0005)  # ~0.03s
        monkeypatch.setattr(tunnel.CloudflareTunnel, "_wait_dns_live", lambda self, host, *, deadline: True)
        binary = _fake_cloudflared(tmp_path, emit_url=True)
        expired = []
        t = tunnel.CloudflareTunnel(5173, binary=binary, on_expire=lambda: expired.append(True))
        url = t.start(timeout=10)
        assert url
        assert t._expire_timer is not None
        # Poll rather than sleep-a-fixed-amount: the timer fires in its own thread.
        deadline = time.monotonic() + 5
        while not expired and time.monotonic() < deadline:
            time.sleep(0.01)
        assert expired == [True]
        assert t._proc is None  # stop() already ran from inside _expire()

    def test_stop_before_expiry_cancels_timer(self, tmp_path, monkeypatch):
        # A manual close must never leave a timer that fires afterwards and calls
        # on_expire — the host already knows the tunnel is gone.
        monkeypatch.setattr("yeaboi.config.get_tunnel_timeout_minutes", lambda: 60)
        monkeypatch.setattr(tunnel.CloudflareTunnel, "_wait_dns_live", lambda self, host, *, deadline: True)
        binary = _fake_cloudflared(tmp_path, emit_url=True)
        expired = []
        t = tunnel.CloudflareTunnel(5173, binary=binary, on_expire=lambda: expired.append(True))
        assert t.start(timeout=10)
        timer = t._expire_timer
        assert timer is not None
        t.stop()
        assert t._expire_timer is None
        assert not timer.is_alive()
        assert expired == []

    def test_zero_timeout_never_schedules_a_timer(self, tmp_path, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_tunnel_timeout_minutes", lambda: 0)
        monkeypatch.setattr(tunnel.CloudflareTunnel, "_wait_dns_live", lambda self, host, *, deadline: True)
        binary = _fake_cloudflared(tmp_path, emit_url=True)
        t = tunnel.CloudflareTunnel(5173, binary=binary)
        assert t.start(timeout=10)
        assert t._expire_timer is None
        t.stop()

    def test_time_until_expiry_none_before_a_timer_is_armed(self, tmp_path, monkeypatch):
        binary = _fake_cloudflared(tmp_path, emit_url=True)
        t = tunnel.CloudflareTunnel(5173, binary=binary)
        assert t.time_until_expiry() is None  # never started

    def test_time_until_expiry_counts_down_from_the_configured_minutes(self, tmp_path, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_tunnel_timeout_minutes", lambda: 10)
        monkeypatch.setattr(tunnel.CloudflareTunnel, "_wait_dns_live", lambda self, host, *, deadline: True)
        binary = _fake_cloudflared(tmp_path, emit_url=True)
        t = tunnel.CloudflareTunnel(5173, binary=binary)
        assert t.start(timeout=10)
        remaining = t.time_until_expiry()
        assert remaining is not None
        assert 0 < remaining <= 10 * 60
        t.stop()

    def test_time_until_expiry_none_when_disabled_or_after_stop(self, tmp_path, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_tunnel_timeout_minutes", lambda: 0)
        monkeypatch.setattr(tunnel.CloudflareTunnel, "_wait_dns_live", lambda self, host, *, deadline: True)
        binary = _fake_cloudflared(tmp_path, emit_url=True)
        t = tunnel.CloudflareTunnel(5173, binary=binary)
        assert t.start(timeout=10)
        assert t.time_until_expiry() is None  # 0 = disabled, nothing armed

        monkeypatch.setattr("yeaboi.config.get_tunnel_timeout_minutes", lambda: 10)
        binary2 = _fake_cloudflared(tmp_path, emit_url=True)
        t2 = tunnel.CloudflareTunnel(5173, binary=binary2)
        assert t2.start(timeout=10)
        t2.stop()
        assert t2.time_until_expiry() is None  # cancelled on manual stop


class TestDnsLiveGate:
    """The DoH DNS-liveness gate that stops us handing out a not-yet-live tunnel URL."""

    def _doh(self, payload: dict):
        import json

        return lambda *a, **k: _FakeResp(json.dumps(payload).encode())

    def test_live_when_doh_has_answer(self, monkeypatch):
        t = tunnel.CloudflareTunnel(5173)
        monkeypatch.setattr(urllib.request, "urlopen", self._doh({"Status": 0, "Answer": [{"data": "104.16.0.1"}]}))
        assert t._wait_dns_live("x-y-z.trycloudflare.com", deadline=time.monotonic() + 5) is True

    def test_not_live_on_nxdomain_times_out_and_warns(self, monkeypatch, caplog):
        t = tunnel.CloudflareTunnel(5173)
        # NXDOMAIN → Status 3, no Answer → never "live".
        monkeypatch.setattr(urllib.request, "urlopen", self._doh({"Status": 3}))
        with caplog.at_level("WARNING", logger="yeaboi.retro.tunnel"):
            # Already-passed deadline → no polling loop, straight to the timeout path.
            assert t._wait_dns_live("nope.trycloudflare.com", deadline=time.monotonic()) is False
        assert any("not resolvable via public DNS" in r.getMessage() for r in caplog.records)

    def test_doh_errors_are_swallowed(self, monkeypatch):
        t = tunnel.CloudflareTunnel(5173)

        def _raise(*a, **k):
            raise urllib.error.URLError("doh down")

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        # A DoH outage must not raise — just returns False after the deadline.
        assert t._wait_dns_live("x.trycloudflare.com", deadline=time.monotonic()) is False
