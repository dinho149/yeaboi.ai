"""Anchors — the row that says which run a Slack message is about.

The anchor is the whole semantic layer of the two-way lane: every inbound event
resolves through one rather than through anything a human typed. So the tests
that matter here are about *identity* (the right run, the right member+rule) and
about the two ways an anchor can be absent — never written, or expired.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from yeaboi.agent.state import MessageRef
from yeaboi.slack.store import (
    ANCHOR_TTL_DAYS,
    KIND_POST,
    KIND_SIGNAL,
    SlackAnchor,
    SlackStore,
    record_post,
)


@pytest.fixture
def store(tmp_path):
    with SlackStore(tmp_path / "sessions.db") as s:
        yield s


def _post(**kw) -> SlackAnchor:
    base = {
        "channel": "C123",
        "ts": "1723800000.000100",
        "session_id": "new-abc-2026-08-17",
        "ceremony": "morning",
        "mode": "standup",
        "artifact_kind": "standup",
        "run_id": 42,
    }
    return SlackAnchor(**{**base, **kw})


class TestRecordAndRead:
    def test_round_trips_the_run_identity(self, store):
        store.record_anchor(_post())
        found = store.anchor("C123", "1723800000.000100")
        assert found is not None
        # The four fields an inbound event will hand to a write path.
        assert (found.session_id, found.artifact_kind, found.run_id, found.ceremony) == (
            "new-abc-2026-08-17",
            "standup",
            42,
            "morning",
        )

    def test_a_message_we_did_not_post_has_no_anchor(self, store):
        store.record_anchor(_post())
        assert store.anchor("C123", "9999999999.999999") is None
        assert store.anchor("COTHER", "1723800000.000100") is None

    def test_recording_the_same_message_twice_is_a_retry_not_a_duplicate(self, store):
        store.record_anchor(_post())
        store.record_anchor(_post(run_id=43))
        assert store.anchor("C123", "1723800000.000100").run_id == 43
        assert len(store.anchors_since("2000-01-01T00:00:00+00:00")) == 1

    def test_signal_anchors_carry_the_member_and_rule(self, store):
        store.record_anchor(_post())
        store.record_anchor(
            _post(
                ts="1723800001.000200", root_ts="1723800000.000100", kind=KIND_SIGNAL, member="Ada", rule="wip-sprawl"
            )
        )
        signals = store.thread("C123", "1723800000.000100")
        assert [(s.member, s.rule) for s in signals] == [("Ada", "wip-sprawl")]
        assert signals[0].is_signal

    def test_the_post_itself_is_not_in_its_own_thread(self, store):
        store.record_anchor(_post())
        assert store.thread("C123", "1723800000.000100") == []


class TestExpiry:
    """A reaction on an ancient post must resolve to nothing.

    apply_verdict would refuse a stale run on its own, but a stale *ceremony
    control* would happily fire — pausing a ceremony because someone scrolled
    back through last month's channel.
    """

    def test_a_fresh_anchor_is_live(self, store):
        stamped = store.record_anchor(_post())
        assert not stamped.expired()

    def test_the_ttl_is_stamped_on_write(self, store):
        now = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
        stamped = store.record_anchor(_post(), now=now)
        assert stamped.expires_at == (now + timedelta(days=ANCHOR_TTL_DAYS)).isoformat(timespec="seconds")

    def test_past_the_ttl_it_is_expired(self, store):
        now = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
        stamped = store.record_anchor(_post(), now=now)
        assert stamped.expired(now + timedelta(days=ANCHOR_TTL_DAYS, seconds=1))

    def test_an_unparseable_expiry_reads_as_expired(self):
        # Fail closed: an anchor we cannot date is one we must not act on.
        assert SlackAnchor(expires_at="not-a-date").expired()

    def test_no_expiry_at_all_never_expires(self):
        assert not SlackAnchor().expired()


class TestPrune:
    def test_drops_old_anchors_and_keeps_recent_ones(self, store):
        now = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
        store.record_anchor(_post(ts="old"), now=now - timedelta(days=60))
        store.record_anchor(_post(ts="new"), now=now)
        assert store.prune(keep_days=30, now=now) == 1
        assert store.anchor("C123", "old") is None
        assert store.anchor("C123", "new") is not None


class TestRecordPost:
    """The wrapper the delivering engines call. It must never raise."""

    def test_records_from_a_message_ref(self, tmp_path):
        db = tmp_path / "sessions.db"
        ref = MessageRef(channel="C123", ts="1723800000.000100")
        anchor = record_post(ref, session_id="s1", ceremony="morning", mode="standup", run_id=7, db_path=db)
        assert anchor is not None and anchor.kind == KIND_POST
        with SlackStore(db) as store:
            assert store.anchor("C123", "1723800000.000100").run_id == 7

    @pytest.mark.parametrize("ref", [MessageRef(), MessageRef(channel="C123"), MessageRef(ts="123.456")])
    def test_a_ref_with_no_identity_records_nothing(self, ref, tmp_path):
        # A webhook post has no ts. That is not an error — it is a message that
        # simply cannot be answered, and the delivery still succeeded.
        assert record_post(ref, db_path=tmp_path / "sessions.db") is None

    def test_a_store_failure_is_swallowed(self, tmp_path, monkeypatch):
        # Recording an anchor buys the ability to answer a message. Failing to
        # record one must never cost the team the message itself.
        import yeaboi.slack.store as store_mod

        def boom(*_a, **_kw):
            raise OSError("disk is gone")

        monkeypatch.setattr(store_mod, "SlackStore", boom)
        assert record_post(MessageRef(channel="C1", ts="1.2"), db_path=tmp_path / "x.db") is None
