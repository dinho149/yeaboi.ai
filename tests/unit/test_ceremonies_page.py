"""Tests for the Ceremonies TUI page loop (ui/mode_select/_ceremonies.py).

Driven with a scripted ``read_key`` and a fake Live, the same shape the other
page-loop tests use — no terminal, no threads of our own.

Two things carry the weight. The loop must route scrolling through the shared
``_scroll`` helpers rather than a hand-rolled offset (``read_key`` emits
``pageup``/``pagedown``, and a loop spelling them ``pgup``/``pgdn`` simply never
fires). And pausing must take the OS job down while keeping the declaration —
a paused ceremony that still fires is the thing users report as a bug.
"""

from __future__ import annotations

import pytest

from yeaboi.agent.state import Ceremony, CeremonyRun
from yeaboi.ceremonies.store import CeremonyStore
from yeaboi.ui.mode_select import _ceremonies


class _Live:
    """Captures whatever the loop renders; the last frame is the assertion."""

    def __init__(self):
        self.frames: list = []

    def update(self, renderable):
        self.frames.append(renderable)


class _Console:
    size = (84, 40)


def _keys(*sequence):
    """A read_key that plays a script; timeout=0.0 polls drain as empty."""
    remaining = list(sequence)

    def _read(timeout=None):
        if timeout == 0.0:
            return ""  # nothing buffered — ends a coalesced scroll burst
        return remaining.pop(0) if remaining else "esc"

    return _read


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A throwaway store, a fixed session, and a scheduler that only records."""
    db = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
    monkeypatch.setattr(_ceremonies, "_session", lambda: "s1")
    installed: set[str] = set()
    monkeypatch.setattr(
        _ceremonies.scheduler, "install_ceremony", lambda sid, name, at, wd: (installed.add(name), "installed")[1]
    )
    monkeypatch.setattr(
        _ceremonies.scheduler, "remove_ceremony", lambda sid, name: (installed.discard(name), "removed")[1]
    )
    monkeypatch.setattr(_ceremonies.scheduler, "installed_ceremonies", lambda sid: sorted(installed))
    return {"db": db, "installed": installed}


def _save(db, **overrides) -> Ceremony:
    base = {
        "session_id": "s1",
        "name": "morning-standup",
        "mode": "standup",
        "at": "09:00",
        "channels": ("terminal",),
    }
    with CeremonyStore(db) as store:
        return store.save(Ceremony(**{**base, **overrides}))


def _run(keys, dry_run: bool = False) -> _Live:
    live = _Live()
    _ceremonies.run_ceremonies_page(_Console(), live, keys, 0.05, True, dry_run=dry_run)
    return live


def _render(panel, width: int = 84, height: int = 40) -> str:
    import io

    from rich.console import Console

    console = Console(file=io.StringIO(), width=width, height=height)
    console.print(panel)
    return console.file.getvalue()


class TestLoad:
    def test_reports_a_declaration_with_no_job(self, env):
        _save(env["db"])  # declared, never installed
        _, _, _, drift = _ceremonies._load("s1")
        assert any("no scheduled job" in line for line in drift)

    def test_reports_a_job_with_no_declaration(self, env):
        env["installed"].add("ghost")
        _, _, _, drift = _ceremonies._load("s1")
        assert any("ghost" in line for line in drift)

    def test_reports_a_paused_ceremony_whose_job_survived(self, env):
        _save(env["db"], enabled=False)
        env["installed"].add("morning-standup")
        _, _, _, drift = _ceremonies._load("s1")
        assert any("still installed" in line for line in drift)

    def test_a_clean_setup_has_no_drift(self, env):
        _save(env["db"])
        env["installed"].add("morning-standup")
        assert _ceremonies._load("s1")[3] == []

    def test_no_session_loads_nothing_rather_than_raising(self):
        assert _ceremonies._load("") == ([], {}, {}, [])

    def test_the_last_run_and_the_months_spend_come_back(self, env):
        _save(env["db"])
        with CeremonyStore(env["db"]) as store:
            store.record_run(
                CeremonyRun(
                    ceremony="morning-standup",
                    session_id="s1",
                    outcome="ok",
                    cost_usd=0.25,
                    fired_at="2026-08-17T09:00:00+00:00",
                )
            )
        _, last, spend, _ = _ceremonies._load("s1")
        assert last["morning-standup"].outcome == "ok"
        assert spend["morning-standup"] == pytest.approx(0.25)


class TestKeys:
    def test_esc_closes_the_page(self, env):
        _save(env["db"])
        live = _run(_keys("esc"))
        assert live.frames  # it painted before reading

    def test_the_back_button_closes_it(self, env):
        _save(env["db"])
        _run(_keys("right", "right", "enter"))  # Run now → Pause → Back

    @pytest.mark.parametrize("key", ["down", "pagedown", "end", "scroll_down"])
    def test_every_scroll_key_reaches_the_helpers(self, env, key, monkeypatch):
        # pgup/pgdn is the trap: read_key emits pageup/pagedown, and a loop
        # spelling them the other way simply never scrolls. Routing through
        # SCROLL_KEYS makes the whole family work at once.
        for n in range(40):
            _save(env["db"], name=f"c-{n:02d}")
        seen = []
        real = _ceremonies.coalesce_scroll
        monkeypatch.setattr(
            _ceremonies, "coalesce_scroll", lambda off, k, meta, rk: (seen.append(k), real(off, k, meta, rk))[1]
        )
        _run(_keys(key, "esc"))
        assert seen == [key]

    def test_up_and_down_move_the_selection(self, env):
        _save(env["db"], name="a-first")
        _save(env["db"], name="b-second")
        live = _run(_keys("down", "esc"))
        assert "▸" in _render(live.frames[-1])


class TestPauseAndResume:
    def test_pause_takes_the_job_down_and_keeps_the_declaration(self, env):
        _save(env["db"])
        env["installed"].add("morning-standup")
        _run(_keys("right", "enter", "esc"))  # → Pause, Enter
        assert env["installed"] == set()
        with CeremonyStore(env["db"]) as store:
            assert store.get("s1", "morning-standup").enabled is False

    def test_resume_puts_the_job_back(self, env):
        _save(env["db"], enabled=False)
        _run(_keys("right", "enter", "esc"))  # the button reads "Resume" here
        assert env["installed"] == {"morning-standup"}
        with CeremonyStore(env["db"]) as store:
            assert store.get("s1", "morning-standup").enabled is True

    def test_the_button_reads_resume_on_a_paused_row(self, env):
        _save(env["db"], enabled=False)
        live = _run(_keys("esc"))
        out = _render(live.frames[-1])
        assert "Resume" in out
        assert "Pause" not in out


class TestRunNow:
    def test_enter_on_run_now_fires_the_ceremony(self, env, monkeypatch):
        _save(env["db"])
        called = {}

        def _fake(name, **kwargs):
            called.update({"name": name, **kwargs})
            return CeremonyRun(ceremony=name, outcome="ok", cost_usd=0.12, delivery=(("terminal", True),))

        monkeypatch.setattr("yeaboi.ceremonies.engine.run_ceremony", _fake)
        live = _run(_keys("enter", "esc"))
        assert called["name"] == "morning-standup"
        # Never "scheduled": the guards answer questions an unattended fire
        # raises, and a human pressing Run now at 14:00 means it.
        assert called.get("scheduled") in (None, False)
        # The main thread repaints a Live every 100 ms while this runs, and the
        # terminal channel's whole job is printing to that same screen.
        assert called["suppress_terminal"] is True
        assert "ran ($0.12)" in _render(live.frames[-1])

    def test_dry_run_reaches_the_engine(self, env, monkeypatch):
        # `make run-dry` is documented as "no LLM calls". Every other page
        # threads dry_run through; this one is reached by a keycap rather than a
        # card, which is exactly how it got missed. Without it, Run now makes
        # real LLM calls and posts to the real Slack webhook.
        _save(env["db"])
        called = {}
        monkeypatch.setattr(
            "yeaboi.ceremonies.engine.run_ceremony",
            lambda name, **k: (called.update(k), CeremonyRun(ceremony=name, outcome="ok"))[1],
        )
        _run(_keys("enter", "esc"), dry_run=True)
        assert called["dry_run"] is True

    def test_a_live_session_does_not_dry_run(self, env, monkeypatch):
        _save(env["db"])
        called = {}
        monkeypatch.setattr(
            "yeaboi.ceremonies.engine.run_ceremony",
            lambda name, **k: (called.update(k), CeremonyRun(ceremony=name, outcome="ok"))[1],
        )
        _run(_keys("enter", "esc"))
        assert called["dry_run"] is False

    def test_a_declined_run_reports_its_reason(self, env, monkeypatch):
        _save(env["db"])
        monkeypatch.setattr(
            "yeaboi.ceremonies.engine.run_ceremony",
            lambda name, **k: CeremonyRun(ceremony=name, outcome="skipped_over_cap", detail="$5.00 already spent"),
        )
        live = _run(_keys("enter", "esc"))
        assert "already spent" in _render(live.frames[-1])

    def test_an_engine_that_raises_becomes_a_message_not_a_traceback(self, env, monkeypatch):
        _save(env["db"])
        monkeypatch.setattr(
            "yeaboi.ceremonies.engine.run_ceremony",
            lambda name, **k: (_ for _ in ()).throw(RuntimeError("jira 401")),
        )
        live = _run(_keys("enter", "esc"))
        assert "jira 401" in _render(live.frames[-1])

    def test_running_with_nothing_declared_says_so(self, env):
        live = _run(_keys("enter", "esc"))
        assert "Nothing scheduled" in _render(live.frames[-1])
