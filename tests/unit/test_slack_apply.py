"""What an authorised event actually does.

The invariant worth guarding above all the others: **a Slack message never
touches launchd or crontab**. The CLI and the TUI both uninstall the OS job
when they pause a ceremony; a chat reaction must not, because an OS write
driven from a channel is the sharpest privilege this lane could hold and it
does not need to exist. A store-only pause is enough — the engine's guard
already turns the resulting drift into a recorded `skipped_paused` and every
listing shows it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from yeaboi.agent.state import Ceremony, MemberUpdate, PracticeSignal, StandupReport
from yeaboi.artifacts.store import ArtifactEditStore, artifact_ref
from yeaboi.ceremonies.store import CeremonyStore
from yeaboi.slack.apply import apply_event
from yeaboi.slack.grammar import ACT_CONTROL, ACT_CORRECTION, ACT_VERDICT
from yeaboi.slack.store import KIND_SIGNAL, OUTCOME_DEFERRED, InboundEvent, SlackAnchor
from yeaboi.standup import habits
from yeaboi.standup.store import StandupStore

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "sessions.db"
    with CeremonyStore(path) as store:
        store.save(
            Ceremony(
                session_id="s1",
                name="morning",
                mode="standup",
                channels=("slack",),
                at="09:00",
                weekdays="1-5",
            )
        )
    return path


@pytest.fixture(autouse=True)
def _no_scheduler(monkeypatch):
    """Fail loudly if anything in this lane reaches for the OS scheduler."""
    import yeaboi.ceremonies.scheduler as sched

    def _forbidden(*_a, **_kw):
        raise AssertionError("the Slack lane must never install or remove an OS job")

    for name in ("install_ceremony", "remove_ceremony", "install_schedule", "remove_schedule"):
        monkeypatch.setattr(sched, name, _forbidden)


def _event(intent="pause", act=ACT_CONTROL, **anchor_kw) -> InboundEvent:
    base = {
        "channel": "C123",
        "ts": "1723800000.0001",
        "session_id": "s1",
        "ceremony": "morning",
        "mode": "standup",
        "run_id": 7,
    }
    return InboundEvent(
        event_key="k",
        channel="C123",
        anchor_ts="1723800000.0001",
        act=act,
        intent=intent,
        slack_user="U0123456789",
        anchor=SlackAnchor(**{**base, **anchor_kw}),
    )


class TestControl:
    def test_pause_sets_the_flag_and_leaves_the_job_alone(self, db):
        result = apply_event(_event("pause"), db_path=db)
        assert result.applied
        assert "job is unchanged" in result.detail
        with CeremonyStore(db) as store:
            assert store.get("s1", "morning").enabled is False

    def test_resume_puts_it_back(self, db):
        apply_event(_event("pause"), db_path=db)
        result = apply_event(_event("resume"), db_path=db)
        assert result.applied
        with CeremonyStore(db) as store:
            assert store.get("s1", "morning").enabled is True

    def test_reacting_to_say_what_is_already_true_is_not_a_failure(self, db):
        result = apply_event(_event("resume"), db_path=db)
        assert result.applied
        assert "already running" in result.detail

    def test_skip_names_a_date_rather_than_setting_a_flag(self, db):
        result = apply_event(_event("skip"), db_path=db)
        assert result.applied
        with CeremonyStore(db) as store:
            skip = store.get("s1", "morning").skip_next
        # A date, because launchd can deliver the slot the following morning.
        assert len(skip) == len("2026-08-18")
        assert skip in result.detail

    def test_skipping_leaves_the_ceremony_enabled(self, db):
        apply_event(_event("skip"), db_path=db)
        with CeremonyStore(db) as store:
            assert store.get("s1", "morning").enabled is True


class TestRefusals:
    def test_an_event_with_no_anchor_does_nothing(self, db):
        result = apply_event(InboundEvent(event_key="k", act=ACT_CONTROL, intent="pause"), db_path=db)
        assert not result.applied
        assert "anchor" in result.detail

    def test_a_post_that_is_not_about_a_ceremony_is_refused(self, db):
        result = apply_event(_event("pause", ceremony=""), db_path=db)
        assert not result.applied
        assert "not about a ceremony" in result.detail

    def test_a_ceremony_that_has_since_been_removed_is_refused_by_name(self, db):
        result = apply_event(_event("pause", ceremony="gone"), db_path=db)
        assert not result.applied
        assert "'gone'" in result.detail

    def test_an_unknown_intent_is_refused_rather_than_guessed(self, db):
        result = apply_event(_event("explode"), db_path=db)
        assert not result.applied
        assert "explode" in result.detail

    def test_an_act_that_is_not_built_yet_says_so_but_does_not_say_it_aloud(self, db):
        # Recording the refusal with its reason beats a silent no-op nobody can
        # explain later — but every ordinary sentence in the thread lands here,
        # and a bot that answers all of them is one nobody leaves switched on.
        result = apply_event(_event(act=ACT_CORRECTION, intent="note"), db_path=db)
        assert not result.applied
        assert "not handled yet" in result.detail
        assert not result.speak


# ── Verdicts ──────────────────────────────────────────────────────────────


def _signal(rule="untracked-work", handles=("url:https://x/pull/42",)) -> PracticeSignal:
    return PracticeSignal(
        rule=rule,
        title="Untracked work",
        detail="PR #42 carries no ticket reference.",
        evidence=(("#42", "https://x/pull/42"),),
        handles=handles,
    )


@pytest.fixture
def voted(db):
    """A stored standup run carrying one votable signal for Ada."""
    signals = (_signal(),)
    report = StandupReport(
        session_id="s1",
        date="2026-08-17",
        member_updates=(MemberUpdate(name="Ada", practices=signals),),
        practice_rollup=habits.rollup({"Ada": signals}),
    )
    with StandupStore(db) as store:
        run_id = store.record_run(report)
    return db, run_id


def _verdict_event(run_id, *, intent="down", member="Ada", rule="untracked-work", kind=KIND_SIGNAL) -> InboundEvent:
    return InboundEvent(
        event_key="k",
        channel="C123",
        anchor_ts="1723800001.0001",
        act=ACT_VERDICT,
        intent=intent,
        slack_user="U0123456789",
        anchor=SlackAnchor(
            channel="C123",
            ts="1723800001.0001",
            root_ts="1723800000.0001",
            kind=kind,
            session_id="s1",
            ceremony="morning",
            mode="standup",
            artifact_kind="standup",
            run_id=run_id,
            member=member,
            rule=rule,
        ),
    )


class TestVerdict:
    def test_a_thumbs_down_remembers_the_change_and_drops_the_signal(self, voted):
        db, run_id = voted
        result = apply_event(_verdict_event(run_id), db_path=db)
        assert result.applied
        with StandupStore(db) as store:
            ledger = practice_feedback_ledger(store)
            assert store.get_run_by_id(run_id).member_updates[0].practices == ()
        assert ("untracked-work", "url:https://x/pull/42") in ledger.excused

    def test_a_thumbs_up_confirms_without_removing_anything(self, voted):
        db, run_id = voted
        result = apply_event(_verdict_event(run_id, intent="up"), db_path=db)
        assert result.applied
        with StandupStore(db) as store:
            assert len(store.get_run_by_id(run_id).member_updates[0].practices) == 1

    def test_the_member_and_rule_come_from_the_anchor_never_from_the_voter(self, voted, monkeypatch):
        # The single most important property of this act. A signal is a claim
        # about a *change*, so the person reacting is not its subject — and
        # nothing about them may reach the write path.
        db, run_id = voted
        seen: dict = {}

        def _spy(_store, **kwargs):
            seen.update(kwargs)
            return True

        monkeypatch.setattr("yeaboi.standup.practice_feedback.apply_verdict", _spy)
        apply_event(_verdict_event(run_id, member="Ada", rule="untracked-work"), db_path=db)
        assert seen["member"] == "Ada"
        assert seen["rule"] == "untracked-work"
        assert seen["run_id"] == run_id
        assert "U0123456789" not in str(seen.values())

    def test_a_thumb_on_the_post_is_refused_with_a_line_that_teaches_the_gesture(self, voted):
        db, run_id = voted
        result = apply_event(_verdict_event(run_id, kind="post"), db_path=db)
        assert not result.applied
        assert result.speak
        assert "signal's own reply" in result.detail

    def test_a_signal_already_answered_is_refused_rather_than_recorded_twice(self, voted):
        db, run_id = voted
        apply_event(_verdict_event(run_id), db_path=db)
        again = apply_event(_verdict_event(run_id), db_path=db)
        assert not again.applied
        assert "no longer in the report" in again.detail

    def test_a_signal_with_no_stored_run_behind_it_is_refused(self, voted):
        db, _ = voted
        result = apply_event(_verdict_event(0), db_path=db)
        assert not result.applied
        assert "not attached to a stored run" in result.detail


class TestLease:
    """The YEA-80 race: one writer per document."""

    def test_a_held_lease_keeps_the_permanent_half_and_defers_the_cosmetic_one(self, voted):
        db, run_id = voted
        ref = artifact_ref("standup", run_id=run_id, session_id="s1")
        with ArtifactEditStore(db) as edits:
            edits.take_lease("standup", ref, holder="a-share")

        with StandupStore(db) as store:
            before = store.get_run_by_id(run_id)

        result = apply_event(_verdict_event(run_id), db_path=db)

        assert result.applied
        assert result.outcome == OUTCOME_DEFERRED
        assert result.speak
        with StandupStore(db) as store:
            # The excusal is permanent; today's report is byte-identical.
            assert store.get_run_by_id(run_id) == before
            assert ("untracked-work", "url:https://x/pull/42") in practice_feedback_ledger(store).excused

    def test_a_released_lease_stops_deferring(self, voted):
        db, run_id = voted
        ref = artifact_ref("standup", run_id=run_id, session_id="s1")
        with ArtifactEditStore(db) as edits:
            edits.take_lease("standup", ref)
            edits.release_lease("standup", ref)
        result = apply_event(_verdict_event(run_id), db_path=db)
        assert result.outcome != OUTCOME_DEFERRED
        with StandupStore(db) as store:
            assert store.get_run_by_id(run_id).member_updates[0].practices == ()

    def test_an_expired_lease_does_not_defer_forever(self, voted):
        db, run_id = voted
        ref = artifact_ref("standup", run_id=run_id, session_id="s1")
        with ArtifactEditStore(db) as edits:
            edits.take_lease("standup", ref, ttl_minutes=1)
            assert edits.lease_held("standup", ref)
            assert not edits.lease_held("standup", ref, now=datetime(2099, 1, 1, tzinfo=UTC))

    def test_a_lease_on_another_run_is_not_this_run_s_problem(self, voted):
        db, run_id = voted
        with ArtifactEditStore(db) as edits:
            edits.take_lease("standup", artifact_ref("standup", run_id=run_id + 999))
        result = apply_event(_verdict_event(run_id), db_path=db)
        assert result.outcome != OUTCOME_DEFERRED


def practice_feedback_ledger(store):
    from yeaboi.standup import practice_feedback

    return practice_feedback.load(store, "s1")
