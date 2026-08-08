"""Tests for the ctrl+U update flow's restart handoff (_run_update_flow).

The flow itself can't reach ``os.execv`` — it runs several frames deep inside the
mode-select ``Live`` context, where exec would strand the terminal in raw mode. It
returns True to unwind, having left a request on ``update_check`` for ``cli.main``
to act on once the terminal is restored. These tests pin that contract.
"""

from __future__ import annotations

import pytest

from yeaboi import update_check
from yeaboi.ui import mode_select


class _FakeConsole:
    size = (100, 30)


class _FakeLive:
    """Captures the kwargs of every frame the flow renders."""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    def update(self, renderable) -> None:  # renderable is the captured kwargs dict
        self.frames.append(renderable)


@pytest.fixture(autouse=True)
def _flow_seams(monkeypatch):
    """Freeze the flow's slow/global edges: no real countdown, no real screens."""
    monkeypatch.setattr(mode_select, "_UPDATE_RESTART_SECONDS", 0.05)
    monkeypatch.setattr(mode_select, "_build_update_screen", lambda w, h, **kw: kw)
    monkeypatch.setattr(update_check, "_restart_to", "")
    monkeypatch.setattr(
        update_check,
        "get_update_status",
        lambda: {
            "current": "2.12.0",
            "latest": "2.13.0",
            "update_available": True,
            "upgrade_command": "uv tool upgrade yeaboi",
            "is_dev": False,
        },
    )


def _run(
    monkeypatch,
    *,
    ok: bool,
    keys: list[str],
    stale: list[str] | None = None,
    relaunch: list[str] | None = None,
):
    """Drive the flow with a canned upgrade result and a scripted key stream.

    ``stale`` is what the user typed *during* the upgrade — still queued on the tty
    when it finishes. The flow drains that before the countdown, so the fake serves
    it first and then reports the buffer empty exactly once; only after that does
    ``keys`` (what they press at the result screen) become readable.
    """
    monkeypatch.setattr(update_check, "run_upgrade", lambda **kw: (ok, "detail line"))
    monkeypatch.setattr(update_check, "resolve_relaunch_command", lambda: relaunch)
    buffered = list(stale or [])
    pending = list(keys)
    drained = False

    def _read_key(timeout=None):
        nonlocal drained
        if not drained:
            if buffered:
                return buffered.pop(0)
            drained = True
            return ""
        return pending.pop(0) if pending else ""

    live = _FakeLive()
    result = mode_select._run_update_flow(_FakeConsole(), live, _read_key, 0.001, True)
    return result, live


class TestRestartHandoff:
    def test_countdown_expiry_requests_the_restart(self, monkeypatch):
        # No keys at all — the countdown runs out and the flow unwinds on its own.
        result, live = _run(monkeypatch, ok=True, keys=[], relaunch=["/bin/yeaboi"])
        assert result is True
        assert update_check.restart_requested() == "2.13.0"
        assert any(f.get("restart_in") is not None for f in live.frames if f.get("done"))

    def test_any_key_restarts_without_waiting(self, monkeypatch):
        result, _live = _run(monkeypatch, ok=True, keys=["enter"], relaunch=["/bin/yeaboi"])
        assert result is True
        assert update_check.restart_requested() == "2.13.0"

    def test_esc_declines_and_stays_put(self, monkeypatch):
        result, _live = _run(monkeypatch, ok=True, keys=["esc"], relaunch=["/bin/yeaboi"])
        assert result is False
        assert update_check.restart_requested() == ""

    def test_q_declines_too(self, monkeypatch):
        result, _live = _run(monkeypatch, ok=True, keys=["q"], relaunch=["/bin/yeaboi"])
        assert result is False
        assert update_check.restart_requested() == ""

    def test_mouse_traffic_does_not_cut_the_countdown_short(self, monkeypatch):
        # A stray wheel nudge must leave the esc window intact — it isn't an answer.
        result, live = _run(
            monkeypatch,
            ok=True,
            keys=["scroll_down", "click:4:9", "scroll_up"],
            relaunch=["/bin/yeaboi"],
        )
        assert result is True
        # More frames than keys means the countdown kept running past them.
        assert len([f for f in live.frames if f.get("done")]) > 3


class TestNoRestartOffered:
    def test_failed_upgrade_never_restarts(self, monkeypatch):
        result, live = _run(monkeypatch, ok=False, keys=["enter"], relaunch=["/bin/yeaboi"])
        assert result is False
        assert update_check.restart_requested() == ""
        assert all(f.get("can_restart") is False for f in live.frames if f.get("done"))

    def test_unresolvable_relaunch_falls_back_to_the_manual_screen(self, monkeypatch):
        result, live = _run(monkeypatch, ok=True, keys=["enter"], relaunch=None)
        assert result is False
        assert update_check.restart_requested() == ""
        done = [f for f in live.frames if f.get("done")]
        assert done and all(f["can_restart"] is False and f["restart_in"] is None for f in done)


class TestBufferedKeystrokes:
    """Keys typed during the upgrade must not answer the screen that follows it."""

    def test_a_stale_key_does_not_cut_the_countdown_short(self, monkeypatch):
        # Without the drain the first read returns this esc and declines a restart
        # the user was never shown — they'd never even see which version landed.
        result, live = _run(monkeypatch, ok=True, keys=[], stale=["esc", "j"], relaunch=["/bin/yeaboi"])
        assert result is True
        assert update_check.restart_requested() == "2.13.0"
        assert len([f for f in live.frames if f.get("done")]) > 1

    def test_live_keys_still_answer_after_the_drain(self, monkeypatch):
        result, _live = _run(monkeypatch, ok=True, keys=["esc"], stale=["j"], relaunch=["/bin/yeaboi"])
        assert result is False
        assert update_check.restart_requested() == ""

    def test_the_drain_is_bounded(self, monkeypatch):
        # Auto-repeat refills the buffer faster than we empty it; the drain has to
        # give up rather than spin. What happens next is the countdown's business.
        monkeypatch.setattr(mode_select, "_UPDATE_DRAIN_LIMIT", 3)
        monkeypatch.setattr(update_check, "run_upgrade", lambda **kw: (True, ""))
        monkeypatch.setattr(update_check, "resolve_relaunch_command", lambda: ["/bin/yeaboi"])
        reads: list = []

        def _read_key(timeout=None):
            reads.append(timeout)
            return "j"

        assert mode_select._run_update_flow(_FakeConsole(), _FakeLive(), _read_key, 0.001, True) is True
        assert reads[:3] == [0, 0, 0]  # exactly _UPDATE_DRAIN_LIMIT drain reads
