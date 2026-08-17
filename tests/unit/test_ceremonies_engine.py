"""Tests for firing a ceremony (ceremonies/engine.py).

The engine is mostly guards, and every one of them exists because an unattended
run raises a question a deliberate one does not: the machine was asleep, nobody
is watching the spend, the store and the OS have drifted. So the tests are
mostly about what gets *declined*, and about the ledger row that always follows.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from yeaboi.agent.state import AgentUsageReport, Ceremony, StandupReport
from yeaboi.ceremonies import engine
from yeaboi.ceremonies.store import CeremonyStore


@pytest.fixture()
def store(tmp_path):
    with CeremonyStore(db_path=tmp_path / "sessions.db") as s:
        yield s


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "sessions.db"


@pytest.fixture(autouse=True)
def no_delivery(monkeypatch):
    """Channels are exercised in test_ceremonies_delivery; here they only record."""
    sent = []
    monkeypatch.setattr(
        "yeaboi.ceremonies.delivery.deliver",
        lambda dispatch, channels: (sent.append((dispatch, channels)), {c: True for c in channels})[1],
    )
    monkeypatch.setattr("yeaboi.ceremonies.delivery.notify_desktop", lambda t, b: True)
    return sent


def _ceremony(**overrides) -> Ceremony:
    base = {
        "session_id": "s1",
        "name": "morning-standup",
        "mode": "standup",
        "channels": ("terminal",),
        "at": "09:00",
    }
    return Ceremony(**{**base, **overrides})


def _fake_engine(monkeypatch, mode_key="standup", artifact=None, raises=None):
    """Point one catalogue entry's engine at a stub."""
    calls = []

    def _run(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return artifact if artifact is not None else StandupReport(date="2026-08-17", team_summary="all good")

    real_lookup = engine.catalog.lookup

    def _callable(mode):
        return _run if mode.key == mode_key else engine.catalog.engine_callable(mode)

    monkeypatch.setattr(engine.catalog, "engine_callable", _callable)
    monkeypatch.setattr(engine.catalog, "lookup", real_lookup)
    return calls


class TestHappyPath:
    def test_a_run_delivers_and_records(self, store, db, monkeypatch, no_delivery):
        _fake_engine(monkeypatch)
        store.save(_ceremony())
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db)
        assert run.outcome == "ok"
        assert run.delivery == (("terminal", True),)
        assert run.detail == "all good"
        assert no_delivery[0][1] == ["terminal"]

    def test_the_declared_args_reach_the_engine(self, store, db, monkeypatch):
        calls = _fake_engine(monkeypatch)
        store.save(_ceremony(args=(("days", "3"),)))
        engine.run_ceremony("morning-standup", session_id="s1", db_path=db)
        assert calls[0]["days"] == 3
        assert calls[0]["session_id"] == "s1"
        # The ceremony owns delivery; the engine must not also do it.
        assert calls[0]["deliver"] is False

    def test_a_successful_run_stamps_last_fired(self, store, db, monkeypatch):
        _fake_engine(monkeypatch)
        store.save(_ceremony())
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db)
        assert store.get("s1", "morning-standup").last_fired_at == run.fired_at

    def test_an_unknown_ceremony_raises_before_anything_happens(self, db):
        # Nothing to record a run against, so this is the one hard failure.
        with pytest.raises(engine.CeremonyNotFoundError):
            engine.run_ceremony("nope", session_id="s1", db_path=db)


class TestFailuresBecomeRows:
    def test_an_engine_that_raises_is_recorded_not_propagated(self, store, db, monkeypatch):
        _fake_engine(monkeypatch, raises=RuntimeError("jira 401"))
        store.save(_ceremony())
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db)
        assert run.outcome == "failed"
        assert "jira 401" in run.error
        assert store.runs("s1")[0].error == run.error

    def test_a_failed_run_does_not_stamp_last_fired(self, store, db, monkeypatch):
        # Otherwise a ceremony that fails every day looks like one that ran.
        _fake_engine(monkeypatch, raises=RuntimeError("boom"))
        store.save(_ceremony())
        engine.run_ceremony("morning-standup", session_id="s1", db_path=db)
        assert store.get("s1", "morning-standup").last_fired_at == ""

    def test_a_failed_delivery_does_not_make_the_run_a_failure(self, store, db, monkeypatch):
        # The report exists and is in its mode's own history; which channels
        # took it is a column, so a dead webhook is visible without being fatal.
        _fake_engine(monkeypatch)
        monkeypatch.setattr("yeaboi.ceremonies.delivery.deliver", lambda d, ch: {c: False for c in ch})
        store.save(_ceremony())
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db)
        assert run.outcome == "ok"
        assert run.delivery == (("terminal", False),)

    def test_a_mode_the_catalog_no_longer_has_is_recorded(self, store, db, monkeypatch):
        store.save(_ceremony())
        monkeypatch.setattr(engine.catalog, "lookup", lambda key: None)
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db)
        assert run.outcome == "failed"
        assert "standup" in run.error


class TestScheduledGuards:
    def _at(self, hhmm: str) -> datetime:
        hour, minute = (int(p) for p in hhmm.split(":"))
        return datetime(2026, 8, 17, hour, minute)

    def test_a_late_fire_is_skipped_and_says_why(self, store, db, monkeypatch, no_delivery):
        # launchd coalesces missed intervals and fires once at wake, so a 09:00
        # standup can arrive at 14:00 when the laptop lid opens.
        _fake_engine(monkeypatch)
        store.save(_ceremony(stale_after_min=120))
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db, scheduled=True, now=self._at("14:00"))
        assert run.outcome == "skipped_stale"
        assert "300 min" in run.detail
        assert no_delivery == []  # nothing was posted

    def test_an_on_time_fire_runs(self, store, db, monkeypatch):
        _fake_engine(monkeypatch)
        store.save(_ceremony(stale_after_min=120))
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db, scheduled=True, now=self._at("09:04"))
        assert run.outcome == "ok"

    def test_staleness_can_be_switched_off(self, store, db, monkeypatch):
        _fake_engine(monkeypatch)
        store.save(_ceremony(stale_after_min=0))
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db, scheduled=True, now=self._at("23:00"))
        assert run.outcome == "ok"

    def test_a_manual_run_is_never_too_late(self, store, db, monkeypatch):
        # A human who asks for it at 14:00 means it.
        _fake_engine(monkeypatch)
        store.save(_ceremony(stale_after_min=120))
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db, now=self._at("14:00"))
        assert run.outcome == "ok"

    def test_the_monthly_cap_declines_a_scheduled_run(self, store, db, monkeypatch):
        _fake_engine(monkeypatch)
        store.save(_ceremony(monthly_cap_usd=1.0))
        from yeaboi.agent.state import CeremonyRun

        store.record_run(
            CeremonyRun(
                ceremony="morning-standup",
                session_id="s1",
                outcome="ok",
                cost_usd=1.20,
                fired_at=datetime.now().isoformat(timespec="seconds"),
            )
        )
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db, scheduled=True, now=self._at("09:00"))
        assert run.outcome == "skipped_over_cap"
        assert "$1.20" in run.detail

    def test_a_manual_run_is_not_capped(self, store, db, monkeypatch):
        _fake_engine(monkeypatch)
        store.save(_ceremony(monthly_cap_usd=0.01))
        from yeaboi.agent.state import CeremonyRun

        store.record_run(
            CeremonyRun(
                ceremony="morning-standup",
                session_id="s1",
                outcome="ok",
                cost_usd=9.0,
                fired_at=datetime.now().isoformat(timespec="seconds"),
            )
        )
        assert engine.run_ceremony("morning-standup", session_id="s1", db_path=db).outcome == "ok"

    def test_an_unreadable_ledger_declines_rather_than_spends(self, store, db, monkeypatch):
        # A ceremony that cannot account for its spend does not spend.
        _fake_engine(monkeypatch)
        store.save(_ceremony(monthly_cap_usd=5.0))
        monkeypatch.setattr(
            CeremonyStore, "month_spend", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db locked"))
        )
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db, scheduled=True, now=self._at("09:00"))
        assert run.outcome == "skipped_over_cap"
        assert "could not be read" in run.detail

    def test_no_cap_means_no_ledger_read_at_all(self, store, db, monkeypatch):
        _fake_engine(monkeypatch)
        store.save(_ceremony(monthly_cap_usd=0.0))
        monkeypatch.setattr(
            CeremonyStore, "month_spend", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be asked"))
        )
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db, scheduled=True, now=self._at("09:00"))
        assert run.outcome == "ok"

    def test_a_job_firing_for_a_paused_ceremony_is_recorded_as_drift(self, store, db, monkeypatch, no_delivery):
        _fake_engine(monkeypatch)
        store.save(replace(_ceremony(), enabled=False))
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db, scheduled=True, now=self._at("09:00"))
        assert run.outcome == "skipped_paused"
        assert no_delivery == []

    def test_every_skip_notifies_somebody(self, store, db, monkeypatch):
        # A guard that declines silently is indistinguishable from a feature
        # that quietly stopped working.
        told = []
        monkeypatch.setattr("yeaboi.ceremonies.delivery.notify_desktop", lambda t, b: told.append((t, b)) or True)
        _fake_engine(monkeypatch)
        store.save(_ceremony(stale_after_min=1))
        engine.run_ceremony("morning-standup", session_id="s1", db_path=db, scheduled=True, now=self._at("14:00"))
        assert told and "morning-standup" in told[0][0]


class TestCosting:
    def test_the_run_is_priced_from_yeabois_own_tokens_not_the_artifacts(self, store, db, monkeypatch):
        # AgentUsageReport.total_cost_usd is what the CODING AGENTS spent.
        # Billing the ceremony for the spend it reports on would blow any cap on
        # the first run.
        _fake_engine(monkeypatch, mode_key="agents-usage", artifact=AgentUsageReport(total_cost_usd=412.0))
        store.save(_ceremony(name="agent-cost", mode="agents-usage"))
        run = engine.run_ceremony("agent-cost", session_id="s1", db_path=db)
        assert run.outcome == "ok"
        assert run.cost_usd < 1.0

    def test_a_costing_failure_does_not_fail_the_run(self, store, db, monkeypatch):
        _fake_engine(monkeypatch)
        monkeypatch.setattr(
            "yeaboi.pricing.estimate_cost", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no table"))
        )
        store.save(_ceremony())
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db)
        assert run.outcome == "ok"
        assert run.cost_usd == 0.0
