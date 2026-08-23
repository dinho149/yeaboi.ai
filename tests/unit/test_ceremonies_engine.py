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
from yeaboi.ceremonies import engine, scheduler
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
        lambda dispatch, channels, **_kw: (sent.append((dispatch, channels)), {c: True for c in channels})[1],
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
        monkeypatch.setattr("yeaboi.ceremonies.delivery.deliver", lambda d, ch, **_kw: {c: False for c in ch})
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


class TestDryRun:
    def test_an_engine_without_a_dry_run_parameter_is_declined(self, store, db, monkeypatch, no_delivery):
        # Both alternatives are wrong: passing the flag is a TypeError recorded
        # as the ceremony's failure, and *not* passing it makes a "dry" run
        # spend money and post to the real webhook.
        _fake_engine(monkeypatch)
        monkeypatch.setattr(engine.catalog, "accepts_dry_run", lambda mode: False)
        store.save(_ceremony())
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db, dry_run=True)
        assert run.outcome == "failed"
        assert "dry_run" in run.error
        assert no_delivery == []

    def test_the_gate_reflects_the_real_signatures(self):
        # The catalogued engine that has no dry_run today. If reporting gains
        # one this flips, which is a fact worth failing on rather than drifting.
        from yeaboi.ceremonies import catalog

        assert catalog.accepts_dry_run(catalog.lookup("standup")) is True
        assert catalog.accepts_dry_run(catalog.lookup("report")) is False

    def test_a_dry_run_never_delivers(self, store, db, monkeypatch, no_delivery):
        _fake_engine(monkeypatch)
        store.save(_ceremony())
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db, dry_run=True)
        assert run.outcome == "ok"
        assert no_delivery == []


class TestSuppressTerminal:
    """The TUI repaints a Live while the run works on a thread, and the terminal
    channel's whole job is printing to that same screen."""

    def test_the_terminal_channel_is_dropped_not_failed(self, store, db, monkeypatch, no_delivery):
        _fake_engine(monkeypatch)
        store.save(_ceremony(channels=("terminal", "slack")))
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db, suppress_terminal=True)
        assert no_delivery[0][1] == ["slack"]
        # Absent, not recorded as a failure: it did not fail, it was never asked.
        assert dict(run.delivery) == {"slack": True}

    def test_a_terminal_only_ceremony_delivers_nowhere_rather_than_shredding_the_screen(
        self, store, db, monkeypatch, no_delivery
    ):
        _fake_engine(monkeypatch)
        store.save(_ceremony(channels=("terminal",)))
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db, suppress_terminal=True)
        assert run.outcome == "ok"
        assert no_delivery == []
        assert run.delivery == ()

    def test_a_scheduled_run_still_prints(self, store, db, monkeypatch, no_delivery):
        _fake_engine(monkeypatch)
        store.save(_ceremony(channels=("terminal",)))
        engine.run_ceremony("morning-standup", session_id="s1", db_path=db, now=datetime(2026, 8, 17, 9, 0))
        assert no_delivery[0][1] == ["terminal"]


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

    def test_a_fire_that_wakes_the_next_morning_is_still_stale(self, store, db, monkeypatch, no_delivery):
        # The case the guard exists for does not politely end before midnight.
        # A 09:00 Monday job coalesced by launchd and fired at 07:00 Tuesday is
        # 22 hours late; measured against *Tuesday's* 09:00 it looks two hours
        # early and a day-old standup sails into the team channel.
        _fake_engine(monkeypatch)
        store.save(_ceremony(stale_after_min=120))
        tuesday_dawn = datetime(2026, 8, 18, 7, 0)
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db, scheduled=True, now=tuesday_dawn)
        assert run.outcome == "skipped_stale"
        assert "1320 min" in run.detail  # 22h from Monday 09:00, not -120 from Tuesday's
        assert no_delivery == []

    def test_lateness_is_measured_from_the_last_scheduled_day(self, store, db, monkeypatch, no_delivery):
        # A Monday-only report woken on Wednesday is measured from Monday, not
        # from a Wednesday slot it was never scheduled for.
        _fake_engine(monkeypatch)
        store.save(_ceremony(weekdays="1", at="08:00", stale_after_min=120))
        wednesday = datetime(2026, 8, 19, 7, 0)
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db, scheduled=True, now=wednesday)
        assert run.outcome == "skipped_stale"
        assert "2820 min" in run.detail  # 47h from Monday 08:00

    def test_a_fire_that_beats_its_own_slot_is_early_not_stale(self, store, db, monkeypatch):
        # The one way this guard could suppress a run that is perfectly on time:
        # reading an early fire as late for the *previous* occurrence.
        _fake_engine(monkeypatch)
        store.save(_ceremony(stale_after_min=120))
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db, scheduled=True, now=self._at("08:58"))
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


class TestAnchoringADeliveredPost:
    """A post is only answerable if we wrote down which run it was.

    The anchor is the whole semantic layer of the two-way Slack lane: an
    inbound reaction resolves through it rather than through anything a human
    typed. So a ceremony has to carry its run's identity into delivery, and the
    identity has to be the one this run wrote — not "the latest", which is
    whichever run a concurrent session touched last.
    """

    def _slack_receipt(self, monkeypatch, sent):
        """Stand in for a bot post that came back with a (channel, ts)."""
        from yeaboi.agent.state import MessageRef

        def _deliver(dispatch, channels, *, on_receipt=None):
            sent.append((dispatch, channels))
            if on_receipt is not None:
                on_receipt("slack", MessageRef(channel="C123", ts="1723800000.000100"))
            return {c: True for c in channels}

        monkeypatch.setattr("yeaboi.ceremonies.delivery.deliver", _deliver)

    def test_the_anchor_carries_the_run_this_ceremony_just_wrote(self, store, db, monkeypatch):
        calls = _fake_engine(monkeypatch)
        # The standup engine reports its history row through on_run_id.
        original = calls.append

        def _run(**kwargs):
            original(kwargs)
            if "on_run_id" in kwargs:
                kwargs["on_run_id"](77)
            return StandupReport(date="2026-08-17", team_summary="all good")

        monkeypatch.setattr(engine.catalog, "engine_callable", lambda mode: _run)
        self._slack_receipt(monkeypatch, [])
        store.save(_ceremony(channels=("slack",)))
        engine.run_ceremony("morning-standup", session_id="s1", db_path=db)

        from yeaboi.slack.store import SlackStore

        with SlackStore(db) as slack_store:
            anchor = slack_store.anchor("C123", "1723800000.000100")
        assert anchor is not None
        assert (anchor.run_id, anchor.mode, anchor.artifact_kind) == (77, "standup", "standup")
        assert (anchor.ceremony, anchor.session_id) == ("morning-standup", "s1")

    def test_a_mode_that_emits_no_run_id_still_anchors(self, store, db, monkeypatch):
        # An agent-usage post has no editable run to answer. That is an honest
        # anchor with run_id 0, not a missing one — a ceremony control reaction
        # still needs somewhere to resolve.
        _fake_engine(monkeypatch, mode_key="agents-usage", artifact=AgentUsageReport(total_cost_usd=1.0))
        self._slack_receipt(monkeypatch, [])
        store.save(_ceremony(name="agent-cost", mode="agents-usage", channels=("slack",)))
        engine.run_ceremony("agent-cost", session_id="s1", db_path=db)

        from yeaboi.slack.store import SlackStore

        with SlackStore(db) as slack_store:
            anchor = slack_store.anchor("C123", "1723800000.000100")
        assert anchor is not None
        assert (anchor.run_id, anchor.artifact_kind) == (0, "")
        assert anchor.ceremony == "agent-cost"

    def test_a_run_id_is_only_requested_from_a_mode_that_offers_one(self, store, db, monkeypatch, no_delivery):
        # Passing on_run_id to an engine that does not take it is a TypeError
        # dressed up as a failed ceremony.
        calls = _fake_engine(monkeypatch, mode_key="agents-usage", artifact=AgentUsageReport(total_cost_usd=1.0))
        store.save(_ceremony(name="agent-cost", mode="agents-usage"))
        engine.run_ceremony("agent-cost", session_id="s1", db_path=db)
        assert "on_run_id" not in calls[0]


class TestSkipOnce:
    """One occurrence off — the case a pause is the wrong shape for.

    Pausing uninstalls the OS job at every surface that offers it, so "not
    tomorrow" must not go through it: a one-day intent should never churn a
    plist, and a crash between uninstall and reinstall leaves the ceremony
    permanently dead.
    """

    def test_the_named_occurrence_is_declined_and_recorded_as_its_own_outcome(self, store, db, monkeypatch):
        _fake_engine(monkeypatch)
        store.save(_ceremony(skip_next="2026-08-18"))
        run = engine.run_ceremony(
            "morning-standup",
            session_id="s1",
            db_path=db,
            scheduled=True,
            now=datetime(2026, 8, 18, 9, 0),
        )
        # Not skipped_paused: "somebody asked for tomorrow off" and "the store
        # and the OS have drifted" are different facts.
        assert run.outcome == "skipped_once"
        assert "2026-08-18" in run.detail

    def test_a_different_occurrence_still_fires(self, store, db, monkeypatch):
        _fake_engine(monkeypatch)
        store.save(_ceremony(skip_next="2026-08-18"))
        run = engine.run_ceremony(
            "morning-standup",
            session_id="s1",
            db_path=db,
            scheduled=True,
            now=datetime(2026, 8, 19, 9, 0),
        )
        assert run.outcome == "ok"

    def test_a_coalesced_wake_up_still_skips_the_slot_it_was_asked_to(self, store, db, monkeypatch):
        # The whole reason skip_next is a DATE. launchd coalesces a missed
        # calendar interval into one fire at wake, so Tuesday's 09:00 job can
        # arrive at 07:00 on Wednesday — and it is still Tuesday's run.
        _fake_engine(monkeypatch)
        store.save(_ceremony(stale_after_min=0, skip_next="2026-08-18"))
        run = engine.run_ceremony(
            "morning-standup",
            session_id="s1",
            db_path=db,
            scheduled=True,
            now=datetime(2026, 8, 19, 7, 0),
        )
        assert run.outcome == "skipped_once"

    def test_the_skip_clears_itself_once_spent(self, store, db, monkeypatch):
        # A one-shot skip that outlives its occurrence is a ceremony that
        # silently stopped, and nobody would think to go looking.
        _fake_engine(monkeypatch)
        store.save(_ceremony(skip_next="2026-08-18"))
        engine.run_ceremony(
            "morning-standup", session_id="s1", db_path=db, scheduled=True, now=datetime(2026, 8, 18, 9, 0)
        )
        assert store.get("s1", "morning-standup").skip_next == ""

    def test_a_skip_ahead_of_its_slot_survives_an_earlier_fire(self, store, db, monkeypatch):
        _fake_engine(monkeypatch)
        store.save(_ceremony(skip_next="2026-08-20"))
        engine.run_ceremony(
            "morning-standup", session_id="s1", db_path=db, scheduled=True, now=datetime(2026, 8, 18, 9, 0)
        )
        assert store.get("s1", "morning-standup").skip_next == "2026-08-20"

    def test_a_manual_run_ignores_a_pending_skip(self, store, db, monkeypatch):
        # The guards answer questions an unattended fire raises. A human typing
        # "run it now" means it.
        _fake_engine(monkeypatch)
        store.save(_ceremony(skip_next="2026-08-18"))
        run = engine.run_ceremony("morning-standup", session_id="s1", db_path=db, now=datetime(2026, 8, 18, 9, 0))
        assert run.outcome == "ok"

    def test_pausing_is_still_the_stronger_statement(self, store, db, monkeypatch):
        _fake_engine(monkeypatch)
        store.save(_ceremony(enabled=False, skip_next="2026-08-18"))
        run = engine.run_ceremony(
            "morning-standup", session_id="s1", db_path=db, scheduled=True, now=datetime(2026, 8, 18, 9, 0)
        )
        assert run.outcome == "skipped_paused"


class TestNextOccurrence:
    def test_names_the_next_slot_later_today(self):
        assert scheduler.next_occurrence(_ceremony(), now=datetime(2026, 8, 18, 7, 0)) == "2026-08-18"

    def test_rolls_to_tomorrow_once_todays_slot_has_gone(self):
        assert scheduler.next_occurrence(_ceremony(), now=datetime(2026, 8, 18, 10, 0)) == "2026-08-19"

    def test_respects_the_weekday_spec(self):
        # Friday 10:00 on a weekdays-only ceremony → Monday.
        assert scheduler.next_occurrence(_ceremony(weekdays="1-5"), now=datetime(2026, 8, 21, 10, 0)) == "2026-08-24"

    def test_an_unreadable_cadence_names_nothing_rather_than_guessing(self):
        assert scheduler.next_occurrence(_ceremony(at="not-a-time"), now=datetime(2026, 8, 18, 7, 0)) == ""
