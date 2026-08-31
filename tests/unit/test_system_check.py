"""Tests for the offline system check (src/yeaboi/system_check.py)."""

from __future__ import annotations

import socket

import pytest

from yeaboi import system_check
from yeaboi.system_check import CheckResult, SystemReport, run_system_check

# The loopback hosts the policy allows a probe to reach.
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@pytest.fixture
def _all_probes_stubbed(monkeypatch):
    """Every wrapped probe pinned to a happy, hermetic answer.

    The check functions import their probes at call time, so patching the
    owning modules is enough — no probe touches the real machine.
    """
    monkeypatch.setattr("yeaboi.config.get_llm_provider", lambda: "ollama")
    monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
    monkeypatch.setattr("yeaboi.config.get_ollama_base_url", lambda: "http://localhost:11434")
    monkeypatch.setattr("yeaboi.config.tunnels_disabled", lambda: False)
    monkeypatch.setattr("yeaboi.config.share_mode", lambda: "quick")
    monkeypatch.setattr("yeaboi.ollama_control.is_ollama_installed", lambda: True)
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    monkeypatch.setattr("yeaboi.voice.unsupported_blocker", lambda: "")
    monkeypatch.setattr("yeaboi.music.is_music_available", lambda: (True, ""))
    monkeypatch.setattr("yeaboi.charts.charts_available", lambda: True)
    monkeypatch.setattr("yeaboi.ship.driver.ClaudeCodeDriver.available", lambda self: (True, "claude 2.0.0"))
    monkeypatch.setattr("yeaboi.claude_auth.setup_token_available", lambda: True)
    monkeypatch.setattr(system_check.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(system_check.shutil, "disk_usage", lambda path: type("u", (), {"free": 50_000_000_000})())

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"models": [{"name": "qwen3:8b"}, {"name": "llama3"}]}

    import httpx

    monkeypatch.setattr(httpx, "get", lambda url, timeout: _Resp())

    from pathlib import Path

    monkeypatch.setattr("yeaboi.retro.tunnel.cloudflared_cached", lambda: Path("/nonexistent/cloudflared"))
    monkeypatch.delenv("CLOUDFLARED_PATH", raising=False)


class TestReportShape:
    def test_all_ok_when_everything_is_present(self, _all_probes_stubbed):
        report = run_system_check()
        assert isinstance(report, SystemReport)
        by_key = {c.key: c for c in report.checks}
        # cloudflared is on PATH via the stubbed which(); everything reports ok.
        assert all(c.status == "ok" for c in report.checks), {k: c.status for k, c in by_key.items()}
        assert report.ok_count == len(report.checks)
        assert "optional features ready" in report.summary

    def test_every_check_has_a_stable_key_and_label(self, _all_probes_stubbed):
        report = run_system_check()
        keys = [c.key for c in report.checks]
        assert keys == [
            "provider",
            "ollama-installed",
            "ollama-server",
            "voice",
            "music",
            "charts",
            "cloudflared",
            "access",
            "coding-agent",
            "git",
            "disk",
        ]
        assert all(c.label for c in report.checks)
        assert all(c.status in ("ok", "missing", "unsupported", "unknown") for c in report.checks)

    def test_missing_rows_carry_a_hint(self, _all_probes_stubbed, monkeypatch):
        monkeypatch.setattr("yeaboi.ollama_control.is_ollama_installed", lambda: False)
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "ANTHROPIC_API_KEY not set"))
        report = run_system_check()
        by_key = {c.key: c for c in report.checks}
        assert by_key["ollama-installed"].status == "missing"
        assert by_key["ollama-installed"].hint
        assert by_key["provider"].status == "missing"
        assert "ANTHROPIC_API_KEY" in by_key["provider"].detail

    def test_unsupported_voice_carries_the_blocker(self, _all_probes_stubbed, monkeypatch):
        monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "unsupported")
        monkeypatch.setattr("yeaboi.voice.unsupported_blocker", lambda: "libportaudio2 is missing")
        report = run_system_check()
        voice = next(c for c in report.checks if c.key == "voice")
        assert voice.status == "unsupported"
        assert "libportaudio2" in voice.detail

    def test_low_disk_warns(self, _all_probes_stubbed, monkeypatch):
        monkeypatch.setattr(system_check.shutil, "disk_usage", lambda path: type("u", (), {"free": 200_000_000})())
        report = run_system_check()
        disk = next(c for c in report.checks if c.key == "disk")
        assert disk.status == "missing"
        assert disk.hint


class TestCrashSafety:
    def test_a_crashing_probe_reports_unknown_and_never_raises(self, monkeypatch):
        def _boom() -> CheckResult:
            raise RuntimeError("probe exploded")

        _boom.__name__ = "_check_provider"
        monkeypatch.setattr(system_check, "_CHECKS", (_boom,))
        report = run_system_check()
        assert len(report.checks) == 1
        assert report.checks[0].status == "unknown"
        assert report.checks[0].key == "provider"


class TestOfflinePolicy:
    def test_no_probe_reaches_a_non_loopback_host(self, monkeypatch):
        """Run the real probes with the network fenced to loopback.

        Any attempt to connect beyond this machine fails the test — the policy
        the privacy page advertises, enforced.
        """

        real_connect = socket.socket.connect

        def _guarded(self, address, *args, **kwargs):
            host = address[0] if isinstance(address, tuple) else str(address)
            if host not in _LOOPBACK:
                raise AssertionError(f"system check reached out to {host}")
            return real_connect(self, address, *args, **kwargs)

        monkeypatch.setattr(socket.socket, "connect", _guarded)
        # A child process escapes an in-process socket fence, so the one probe
        # that spawns one (claude --version) is stubbed — which also keeps the
        # test fast on machines with the CLI installed.
        monkeypatch.setattr(
            "yeaboi.ship.driver.ClaudeCodeDriver.available", lambda self: (True, "stubbed for the fence")
        )
        # The download seam must never even be consulted.
        monkeypatch.setattr(
            "yeaboi.retro.tunnel.ensure_cloudflared",
            lambda: (_ for _ in ()).throw(AssertionError("system check called ensure_cloudflared")),
        )
        # Keep the one loopback probe from hanging on a wedged local server.
        import httpx

        monkeypatch.setattr(httpx, "get", lambda url, timeout: (_ for _ in ()).throw(ConnectionError("down")))
        report = run_system_check()
        assert report.checks  # it ran to completion with the fence up

    def test_remote_ollama_base_url_is_not_probed(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_ollama_base_url", lambda: "http://ollama.internal:11434")

        import httpx

        def _fail(url, timeout):
            raise AssertionError(f"probed a non-loopback host: {url}")

        monkeypatch.setattr(httpx, "get", _fail)
        result = system_check._check_ollama_server()
        assert result.status == "unknown"
        assert "not probed" in result.detail
