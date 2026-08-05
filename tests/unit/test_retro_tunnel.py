"""Unit tests for the Retro Cloudflare tunnel helper (hermetic — no network)."""

import hashlib
import io
import platform
import stat
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

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


def _fake_cloudflared(tmp_path, *, emit_url: bool) -> "object":
    """Write a fake cloudflared shell script that mimics stderr output."""
    script = tmp_path / "cloudflared"
    if emit_url:
        body = '#!/bin/sh\necho "INF |  https://fake-tunnel-abcd.trycloudflare.com  |" >&2\nsleep 5\n'
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


class _FakeProc:
    """Stand-in for a spawned cloudflared — records teardown, runs nothing."""

    def __init__(self, stderr_text: str = "") -> None:
        self.terminated = False
        self.terminate_calls = 0  # a count, not a flag: a double stop() must not double-terminate
        self.killed = False
        self.returncode = 0
        # Drains immediately; emits a URL only when a test asks for one.
        self.stderr = io.StringIO(stderr_text)

    def terminate(self) -> None:
        self.terminated = True
        self.terminate_calls += 1

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout=None) -> int:
        return 0

    def poll(self) -> int:
        return 0


class TestStartStopRace:
    """A stop() landing before start() reaches Popen must not leave a tunnel behind.

    The TUI publishes the tunnel object *before* calling start() so the retro page's
    `finally` can tear down a setup still in flight. Before the stop-requested latch,
    a stop() in the window between construction and Popen() found `_proc is None`,
    returned having done nothing, and the worker went on to spawn a cloudflared with
    nobody left to stop it — a public URL still forwarding at a port a later session
    could reuse.
    """

    def _no_spawn(self, monkeypatch) -> list:
        """Replace Popen with a recorder, so a spawn is an observable event."""
        spawned: list = []

        def _fake_popen(*args, **kwargs):
            spawned.append(args)
            return _FakeProc()

        monkeypatch.setattr(tunnel.subprocess, "Popen", _fake_popen)
        return spawned

    def test_stop_before_start_prevents_spawn(self, tmp_path, monkeypatch, caplog):
        spawned = self._no_spawn(monkeypatch)
        t = tunnel.CloudflareTunnel(5173, binary=tmp_path / "cloudflared")

        t.stop()  # the page's finally, arriving first
        with caplog.at_level("WARNING", logger="yeaboi.retro.tunnel"):
            assert t.start(timeout=5) is None

        assert spawned == []
        assert t._proc is None
        assert any("aborted by a concurrent stop" in r.getMessage() for r in caplog.records)

    def test_stop_during_binary_resolution_prevents_spawn(self, monkeypatch):
        # The real window is wide: ensure_cloudflared() may download ~40 MB. Land the
        # stop inside it, exactly as a host closing the board mid-setup would.
        spawned = self._no_spawn(monkeypatch)
        t = tunnel.CloudflareTunnel(5173)

        def _resolve_then_stopped() -> Path:
            t.stop()
            return Path("/nonexistent/cloudflared")

        monkeypatch.setattr(tunnel, "ensure_cloudflared", _resolve_then_stopped)
        assert t.start(timeout=5) is None
        assert spawned == []

    def test_stop_racing_the_spawn_tears_the_process_down(self, monkeypatch):
        # The one case where a process does get spawned: stop() arrives while Popen is
        # mid-flight. It blocks on the lock start() holds across the spawn, then finds
        # the new process and terminates it. Nothing is left running either way.
        procs: list[_FakeProc] = []
        in_popen = threading.Event()
        release = threading.Event()

        def _slow_popen(*args, **kwargs):
            in_popen.set()
            release.wait(timeout=5)
            proc = _FakeProc()
            procs.append(proc)
            return proc

        monkeypatch.setattr(tunnel.subprocess, "Popen", _slow_popen)
        t = tunnel.CloudflareTunnel(5173, binary=Path("/nonexistent/cloudflared"))

        result: dict = {}
        worker = threading.Thread(target=lambda: result.update(url=t.start(timeout=1)))
        worker.start()
        assert in_popen.wait(timeout=5)

        stopper = threading.Thread(target=t.stop)
        stopper.start()
        release.set()
        stopper.join(timeout=10)
        worker.join(timeout=10)

        assert result["url"] is None
        assert procs and procs[0].terminated  # spawned, but never left running
        assert t._proc is None

    def test_stop_racing_the_reader_launch_tears_down_exactly_once(self, monkeypatch):
        # The narrowest window in start(): the drain thread has been constructed but is
        # not running yet. It is reachable — the share flow's cancel path stops the
        # tunnel from the TUI thread while the setup worker is still inside start(), then
        # stops it a second time once that worker is joined (two stop() call sites in
        # ui/shared/_output_share.py). Publishing ``_reader`` *before* starting it made
        # the first stop() join a thread that had never run — "RuntimeError: cannot join
        # thread before it is started", raised straight out of the page's finally — and
        # claiming the process handle separately from the reader let two stops terminate
        # the same child twice. start() therefore assigns ``_reader`` only after
        # ``reader.start()``, and stop() claims and blanks both handles in one breath.
        procs: list[_FakeProc] = []

        def _fake_popen(*args, **kwargs):
            proc = _FakeProc()
            procs.append(proc)
            return proc

        monkeypatch.setattr(tunnel.subprocess, "Popen", _fake_popen)
        t = tunnel.CloudflareTunnel(5173, binary=Path("/nonexistent/cloudflared"))

        class _StopMidLaunch(threading.Thread):
            """A drain thread that takes a stop() in the instant before it starts."""

            def start(self) -> None:
                if self.name == "retro-tunnel":  # never interfere with anyone else's threads
                    t.stop()  # the page's finally, landing inside the window
                super().start()

        monkeypatch.setattr(tunnel.threading, "Thread", _StopMidLaunch)

        assert t.start(timeout=1) is None  # the in-window stop must not raise out of start()
        t.stop()  # and the second stop, after the worker is joined, is a clean no-op

        assert len(procs) == 1
        assert procs[0].terminate_calls == 1  # torn down once, not once per stop()
        assert t._proc is None and t._reader is None

    def test_stop_at_reader_launch_publishes_no_reader_handle(self, monkeypatch):
        # Same window as above, but the tunnel goes on to succeed: cloudflared emits a URL,
        # so start() never reaches its own failure-path stop(). Nothing else would blank
        # ``_reader``, so this is what pins the publish-only-if-not-stopped rule: a stop()
        # that has already claimed both handles and returned must not have a live reader
        # handed back to it afterwards.
        url_line = "INF |  https://fake-tunnel-abcd.trycloudflare.com  |\n"
        monkeypatch.setattr(tunnel.subprocess, "Popen", lambda *a, **k: _FakeProc(url_line))
        monkeypatch.setattr(tunnel.CloudflareTunnel, "_wait_dns_live", lambda self, host, *, deadline: True)
        t = tunnel.CloudflareTunnel(5173, binary=Path("/nonexistent/cloudflared"))

        class _StopMidLaunch(threading.Thread):
            def start(self) -> None:
                if self.name == "retro-tunnel":  # never interfere with anyone else's threads
                    super().start()
                    t.stop()  # the page's finally, landing the instant the drain thread runs
                else:
                    super().start()

        monkeypatch.setattr(tunnel.threading, "Thread", _StopMidLaunch)

        assert t.start(timeout=1) is None
        assert t._reader is None and t._proc is None

    def test_stop_during_the_dns_wait_reports_no_url(self, monkeypatch, caplog):
        # The longest window in start(): _wait_dns_live polls for up to 30 s plus a 3 s
        # settle, all outside the lock. A stop() landing there tore the process down
        # correctly — but start() still returned the URL, so the board advertised a link to
        # a tunnel that was already dead.
        url_line = "INF |  https://fake-tunnel-abcd.trycloudflare.com  |\n"
        monkeypatch.setattr(tunnel.subprocess, "Popen", lambda *a, **k: _FakeProc(url_line))

        def _stop_mid_wait(self, host, *, deadline):
            self.stop()  # the host closes the board while DNS is still propagating
            return True

        monkeypatch.setattr(tunnel.CloudflareTunnel, "_wait_dns_live", _stop_mid_wait)
        t = tunnel.CloudflareTunnel(5173, binary=Path("/nonexistent/cloudflared"))

        with caplog.at_level("WARNING", logger="yeaboi.retro.tunnel"):
            assert t.start(timeout=5) is None
        assert t.public_url  # the URL was seen…
        assert any("stopped while waiting for DNS" in r.getMessage() for r in caplog.records)  # …never handed out
        assert t._proc is None


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
