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

from yeaboi.agent.state import Ceremony
from yeaboi.ceremonies.store import CeremonyStore
from yeaboi.slack.apply import apply_event
from yeaboi.slack.grammar import ACT_CONTROL, ACT_CORRECTION, ACT_VERDICT
from yeaboi.slack.store import InboundEvent, SlackAnchor

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
        applied, detail = apply_event(_event("pause"), db_path=db)
        assert applied
        assert "job is unchanged" in detail
        with CeremonyStore(db) as store:
            assert store.get("s1", "morning").enabled is False

    def test_resume_puts_it_back(self, db):
        apply_event(_event("pause"), db_path=db)
        applied, _ = apply_event(_event("resume"), db_path=db)
        assert applied
        with CeremonyStore(db) as store:
            assert store.get("s1", "morning").enabled is True

    def test_reacting_to_say_what_is_already_true_is_not_a_failure(self, db):
        applied, detail = apply_event(_event("resume"), db_path=db)
        assert applied
        assert "already running" in detail

    def test_skip_names_a_date_rather_than_setting_a_flag(self, db):
        applied, detail = apply_event(_event("skip"), db_path=db)
        assert applied
        with CeremonyStore(db) as store:
            skip = store.get("s1", "morning").skip_next
        # A date, because launchd can deliver the slot the following morning.
        assert len(skip) == len("2026-08-18")
        assert skip in detail

    def test_skipping_leaves_the_ceremony_enabled(self, db):
        apply_event(_event("skip"), db_path=db)
        with CeremonyStore(db) as store:
            assert store.get("s1", "morning").enabled is True


class TestRefusals:
    def test_an_event_with_no_anchor_does_nothing(self, db):
        applied, detail = apply_event(InboundEvent(event_key="k", act=ACT_CONTROL, intent="pause"), db_path=db)
        assert not applied
        assert "anchor" in detail

    def test_a_post_that_is_not_about_a_ceremony_is_refused(self, db):
        applied, detail = apply_event(_event("pause", ceremony=""), db_path=db)
        assert not applied
        assert "not about a ceremony" in detail

    def test_a_ceremony_that_has_since_been_removed_is_refused_by_name(self, db):
        applied, detail = apply_event(_event("pause", ceremony="gone"), db_path=db)
        assert not applied
        assert "'gone'" in detail

    def test_an_unknown_intent_is_refused_rather_than_guessed(self, db):
        applied, detail = apply_event(_event("explode"), db_path=db)
        assert not applied
        assert "explode" in detail

    @pytest.mark.parametrize("act", [ACT_VERDICT, ACT_CORRECTION])
    def test_an_act_that_is_not_built_yet_says_so(self, db, act):
        # Recording the refusal with its reason beats a silent no-op nobody can
        # explain later.
        applied, detail = apply_event(_event(act=act, intent="up"), db_path=db)
        assert not applied
        assert "not handled yet" in detail
