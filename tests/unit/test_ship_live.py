"""Tests for the ship board's lifecycle orchestrator (ship/live.py).

The tunnel is stubbed, so the whole start → publish → route → stop path is
exercised without a network or a cloudflared binary.
"""

from __future__ import annotations

import time

import pytest

from yeaboi.ship.live import ShipBoardSession


def _wait(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def session(tmp_path, monkeypatch):
    # Keep the board's store read off the real DB.
    s = ShipBoardSession(
        "run-x",
        db_path=tmp_path / "sessions.db",
        story_title="Story",
        project_name="Proj",
        tunnel_factory=lambda port: f"https://ship-{port}.example",
    )
    try:
        yield s
    finally:
        s.stop()


def test_start_binds_loopback_and_publishes_the_tunnel_url(session):
    session.start()
    # The host link always carries both secrets.
    assert "token=" in session.host_url and "admin=" in session.host_url
    # The tunnel thread publishes the share URL shortly after start.
    assert _wait(lambda: session.share_url.startswith("https://ship-"))
    assert "example" in session.share_url


def test_display_code_is_present(session):
    session.start()
    assert session.display_code  # a join code exists for teammates


def test_callbacks_route_into_the_board(session):
    session.start()
    session.note_component({"component_id": "ship-implement", "label": "Implementing", "status": "running"})
    session.note_agent_line('{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}')
    snap = session.board.state_snapshot()
    assert any(p["component_id"] == "ship-implement" for p in snap["phases"])
    assert [a["kind"] for a in snap["activity"]] == ["text"]


def test_stop_is_idempotent(session):
    session.start()
    session.stop()
    session.stop()  # must not raise


def test_tunnels_disabled_stays_loopback(tmp_path, monkeypatch):
    monkeypatch.setattr("yeaboi.config.tunnels_disabled", lambda: True)
    s = ShipBoardSession("run-y", db_path=tmp_path / "s.db", tunnel_factory=None)
    try:
        s.start()
        # No factory and tunnels disabled → no share URL is ever set.
        assert not _wait(lambda: bool(s.share_url), timeout=0.5)
        assert s.host_url.startswith("http://127.0.0.1:")
    finally:
        s.stop()
