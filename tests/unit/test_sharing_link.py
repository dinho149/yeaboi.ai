"""Tests for the shared secure-link lifecycle (sharing/link.py)."""

from __future__ import annotations

import threading
import time

import pytest

from yeaboi.sharing.link import (
    STATE_FAILED,
    STATE_OFF,
    STATE_READY,
    SecureLink,
)


class FakeServer:
    """The board-server surface a SecureLink touches, and nothing else."""

    def __init__(self, port: int = 5173) -> None:
        self.port = port
        self.public_url = ""
        self.gate = "unset"
        self.share_states: list[str] = []

    def set_public_url(self, url: str) -> None:
        self.public_url = url

    def set_access_gate(self, gate) -> None:
        self.gate = gate

    def set_share_state(self, state: str) -> None:
        self.share_states.append(state)


class BareServer:
    """The poker board's server: no invite panel, so no set_share_state."""

    def __init__(self, port: int = 5273) -> None:
        self.port = port
        self.public_url = ""

    def set_public_url(self, url: str) -> None:
        self.public_url = url

    def set_access_gate(self, gate) -> None:
        pass


def _settle(link: SecureLink, *, timeout: float = 2.0) -> None:
    """Wait for the worker thread to leave the starting state."""
    deadline = time.monotonic() + timeout
    while link.starting and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not link.starting, "link never left the starting state"


@pytest.fixture(autouse=True)
def _tunnels_enabled(monkeypatch):
    monkeypatch.delenv("YEABOI_NO_TUNNEL", raising=False)


class TestStart:
    def test_publishes_the_url_and_tells_the_server(self):
        server = FakeServer()
        link = SecureLink(server, surface="retro", tunnel_factory=lambda port: f"https://x-{port}.example")
        link.start()
        _settle(link)
        assert link.state == STATE_READY
        assert link.url == "https://x-5173.example/"
        assert server.public_url == "https://x-5173.example/"

    def test_trailing_slash_is_normalised_once(self):
        link = SecureLink(FakeServer(), surface="poker", tunnel_factory=lambda _p: "https://x.example/")
        link.start()
        _settle(link)
        assert link.url == "https://x.example/"

    def test_on_ready_fires(self):
        # Waited on directly rather than through _settle: the link publishes its
        # state before it calls back, so "no longer starting" does not mean the
        # callback has run yet.
        fired = threading.Event()
        link = SecureLink(
            FakeServer(),
            surface="retro",
            on_ready=fired.set,
            tunnel_factory=lambda _p: "https://x.example",
        )
        link.start()
        assert fired.wait(2.0), "on_ready never fired"

    def test_a_failing_on_ready_never_breaks_the_link(self):
        def boom() -> None:
            raise RuntimeError("duck exploded")

        link = SecureLink(FakeServer(), surface="retro", on_ready=boom, tunnel_factory=lambda _p: "https://x.example")
        link.start()
        _settle(link)
        assert link.state == STATE_READY

    def test_no_url_is_a_failure_not_a_crash(self):
        link = SecureLink(FakeServer(), surface="share", tunnel_factory=lambda _p: None)
        link.start()
        _settle(link)
        assert link.state == STATE_FAILED
        assert link.failed and "did not start" in link.status

    def test_a_raising_factory_becomes_a_status(self):
        def boom(_port: int) -> str:
            raise RuntimeError("no network")

        link = SecureLink(FakeServer(), surface="retro", tunnel_factory=boom)
        link.start()
        _settle(link)
        assert link.failed and "no network" in link.status

    def test_share_state_tracks_the_outcome(self):
        server = FakeServer()
        link = SecureLink(server, surface="retro", tunnel_factory=lambda _p: "https://x.example")
        link.start()
        _settle(link)
        assert server.share_states == ["pending"]

    def test_a_server_without_share_state_is_fine(self):
        server = BareServer()
        link = SecureLink(server, surface="poker", tunnel_factory=lambda _p: "https://x.example")
        link.start()
        _settle(link)
        assert link.state == STATE_READY


class TestTunnelsDisabled:
    def test_no_tunnel_env_refuses_before_any_worker(self, monkeypatch):
        monkeypatch.setenv("YEABOI_NO_TUNNEL", "1")
        server = FakeServer()
        link = SecureLink(server, surface="retro")
        link.start()
        assert link.state == STATE_OFF
        assert "Sharing is off" in link.status
        assert server.share_states == ["off"]

    def test_an_injected_factory_still_runs(self, monkeypatch):
        """Tests inject a factory precisely to avoid the network — the opt-out
        for real tunnels must not disable the stub as well."""
        monkeypatch.setenv("YEABOI_NO_TUNNEL", "1")
        link = SecureLink(FakeServer(), surface="retro", tunnel_factory=lambda _p: "https://x.example")
        link.start()
        _settle(link)
        assert link.state == STATE_READY


class TestSnapshot:
    def test_idle_snapshot_is_all_empty(self):
        link = SecureLink(FakeServer(), surface="retro")
        assert link.snapshot() == {
            "state": "idle",
            "status": "",
            "url": "",
            "failed": False,
            "expired": False,
            "starting": False,
            "notice": "",
        }

    def test_ready_snapshot_carries_the_url(self):
        link = SecureLink(FakeServer(), surface="retro", tunnel_factory=lambda _p: "https://x.example")
        link.start()
        _settle(link)
        snapshot = link.snapshot()
        assert snapshot["state"] == "ready"
        assert snapshot["url"] == "https://x.example/"


class TestExpiryNotice:
    class _Tunnel:
        def __init__(self, remaining) -> None:
            self._remaining = remaining

        def time_until_expiry(self):
            return self._remaining

        def stop(self) -> None:
            pass

    def test_silent_with_no_tunnel(self):
        assert SecureLink(FakeServer(), surface="retro").expiry_notice() == ""

    def test_silent_while_there_is_time(self):
        link = SecureLink(FakeServer(), surface="retro")
        link._tunnel = self._Tunnel(3600)
        assert link.expiry_notice() == ""

    def test_warns_inside_the_window(self):
        link = SecureLink(FakeServer(), surface="retro")
        link._tunnel = self._Tunnel(90)
        assert link.expiry_notice() == "Secure link expires in ~2 min — reconnecting will need a fresh invite."

    def test_never_says_zero_minutes(self):
        link = SecureLink(FakeServer(), surface="retro")
        link._tunnel = self._Tunnel(5)
        assert "~1 min" in link.expiry_notice()

    def test_expiry_unpublishes_and_reports(self):
        server = FakeServer()
        link = SecureLink(server, surface="retro", tunnel_factory=lambda _p: "https://x.example")
        link.start()
        _settle(link)
        link._on_expired()
        assert link.expired and link.failed
        assert server.public_url == ""
        assert server.share_states == ["pending", "failed"]
        # The notice must carry the expiry text itself, not a countdown.
        assert link.expiry_notice() == link.status


class TestStop:
    def test_stops_the_tunnel_once(self):
        stops: list[int] = []

        class Tunnel:
            def stop(self) -> None:
                stops.append(1)

        link = SecureLink(FakeServer(), surface="retro")
        link._tunnel = Tunnel()
        link.stop()
        link.stop()
        assert stops == [1]

    def test_a_raising_stop_is_swallowed(self):
        class Tunnel:
            def stop(self) -> None:
                raise RuntimeError("already gone")

        link = SecureLink(FakeServer(), surface="retro")
        link._tunnel = Tunnel()
        link.stop()  # must not raise

    def test_stop_with_nothing_up_is_a_no_op(self):
        SecureLink(FakeServer(), surface="retro").stop()


class TestRestart:
    def test_retry_after_failure_can_succeed(self):
        attempts: list[int] = []

        def factory(_port: int):
            attempts.append(1)
            return None if len(attempts) == 1 else "https://x.example"

        link = SecureLink(FakeServer(), surface="retro", tunnel_factory=factory)
        link.start()
        _settle(link)
        assert link.failed
        link.start()
        _settle(link)
        assert link.state == STATE_READY
