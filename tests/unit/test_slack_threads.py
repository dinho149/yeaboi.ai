"""The signal replies: one message per answerable thing, and its anchor.

Two properties carry this module. **The cap names what it held back** — a
thread that quietly stops at twelve reads as "these are all of them", which is
the one thing it must not say. And **nothing here can fail a delivery**: the
standup landing in the channel is the product, and the thread under it is a
convenience that must never be able to take the product with it.
"""

from __future__ import annotations

import pytest

from yeaboi.agent.state import MemberUpdate, PracticeSignal, StandupReport
from yeaboi.slack import threads
from yeaboi.slack.store import KIND_SIGNAL, SlackStore
from yeaboi.slack.threads import MAX_SIGNAL_REPLIES, post_signal_anchors
from yeaboi.tools.slack import SlackResponse


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-1")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C123")
    # A signal reply invites a gesture, so it is only posted when somebody could
    # actually make one — see TestTheInvitationIsOnlyMadeWhenItCanBeAnswered.
    monkeypatch.setenv("SLACK_ALLOWED_MEMBER_IDS", "U0123456789")


class Ref:
    kind = "slack"
    channel = "C123"
    ts = "1723800000.0001"
    permalink = ""


class FakeApi:
    """Records what was posted, and hands back a distinct ts each time."""

    def __init__(self, *, fail_on=()):
        self.posts: list[tuple[str, str, str]] = []
        self._fail_on = set(fail_on)
        self._n = 0

    def post_message(self, channel, text, *, thread_ts="", token="", budget=None):
        self.posts.append((channel, text, thread_ts))
        if text in self._fail_on:
            return SlackResponse(ok=False, error="msg_too_long")
        self._n += 1
        return SlackResponse(ok=True, data={"channel": channel, "ts": f"1723800001.{self._n:04d}"})


@pytest.fixture(autouse=True)
def _api(monkeypatch):
    api = FakeApi()
    monkeypatch.setattr("yeaboi.tools.slack.post_message", api.post_message)
    return api


def _signal(rule="untracked-work", title="Untracked work", handles=("url:https://x/pull/42",)) -> PracticeSignal:
    return PracticeSignal(rule=rule, title=title, detail="…", handles=handles)


def _report(members: dict) -> StandupReport:
    return StandupReport(
        session_id="s1",
        date="2026-08-17",
        member_updates=tuple(MemberUpdate(name=name, practices=tuple(sigs)) for name, sigs in members.items()),
    )


def _post(artifact, db, **kw) -> int:
    return post_signal_anchors(
        Ref(),
        artifact,
        session_id="s1",
        ceremony="morning",
        mode="standup",
        artifact_kind=kw.pop("artifact_kind", "standup"),
        run_id=kw.pop("run_id", 7),
        db_path=db,
    )


@pytest.fixture
def db(tmp_path):
    return tmp_path / "sessions.db"


class TestPosting:
    def test_each_signal_gets_its_own_reply_and_its_own_anchor(self, db, _api):
        report = _report({"Ada": [_signal()], "Ben": [_signal(rule="wip-sprawl", title="WIP sprawl")]})
        assert _post(report, db) == 2

        with SlackStore(db) as store:
            anchors = store.thread("C123", Ref.ts)
        assert [(a.member, a.rule) for a in anchors] == [("Ada", "untracked-work"), ("Ben", "wip-sprawl")]
        assert all(a.kind == KIND_SIGNAL and a.run_id == 7 and a.root_ts == Ref.ts for a in anchors)

    def test_the_anchor_carries_exactly_apply_verdicts_signature(self, db):
        # session_id, run_id, member, rule — which is the whole reason a thumb
        # on one of these needs no inference at all.
        _post(_report({"Ada": [_signal()]}), db)
        with SlackStore(db) as store:
            anchor = store.thread("C123", Ref.ts)[0]
        assert (anchor.session_id, anchor.run_id, anchor.member, anchor.rule) == ("s1", 7, "Ada", "untracked-work")

    def test_every_reply_hangs_under_the_post(self, db, _api):
        _post(_report({"Ada": [_signal()]}), db)
        assert all(thread_ts == Ref.ts for _c, _t, thread_ts in _api.posts)

    def test_the_reply_is_one_line_because_the_post_already_has_the_detail(self, db, _api):
        _post(_report({"Ada": [_signal()]}), db)
        text = _api.posts[0][1]
        assert "\n" not in text
        assert "Ada" in text and "Untracked work" in text


class TestWhatIsSkipped:
    def test_a_handleless_signal_gets_no_reply_at_all(self, db, _api):
        # `votable` drops it: a thumbs-down would hide it today and remember
        # nothing, so it would be back tomorrow looking answered.
        assert _post(_report({"Ada": [_signal(handles=())]}), db) == 0
        assert _api.posts == []

    def test_a_report_with_no_signals_posts_nothing(self, db, _api):
        assert _post(_report({"Ada": []}), db) == 0
        assert _api.posts == []

    def test_an_artifact_kind_with_no_votable_items_posts_nothing(self, db, _api):
        assert _post(_report({"Ada": [_signal()]}), db, artifact_kind="reporting") == 0
        assert _api.posts == []

    def test_a_run_with_no_stored_id_posts_nothing(self, db, _api):
        # Without a run id a verdict would have nothing to apply to, so the
        # reply would be an invitation to press a button that does nothing.
        assert _post(_report({"Ada": [_signal()]}), db, run_id=0) == 0
        assert _api.posts == []

    def test_no_token_posts_nothing(self, db, monkeypatch, _api):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "")
        assert _post(_report({"Ada": [_signal()]}), db) == 0
        assert _api.posts == []


class TestTheCap:
    def _many(self, count: int) -> StandupReport:
        rules = ["untracked-work", "wip-sprawl", "large-change", "no-pull-request"]
        return _report(
            {f"M{i}": [_signal(rule=rules[i % len(rules)], title=f"T{i}")] for i in range(count)},
        )

    def test_it_stops_at_the_cap(self, db, _api):
        assert _post(self._many(MAX_SIGNAL_REPLIES + 5), db) == MAX_SIGNAL_REPLIES

    def test_the_remainder_is_named_rather_than_dropped(self, db, _api):
        _post(self._many(MAX_SIGNAL_REPLIES + 3), db)
        assert "3 more signals" in _api.posts[-1][1]
        assert "in the report above" in _api.posts[-1][1]

    def test_one_held_back_reads_as_one(self, db, _api):
        _post(self._many(MAX_SIGNAL_REPLIES + 1), db)
        assert "1 more signal is" in _api.posts[-1][1]

    def test_nothing_held_back_says_nothing(self, db, _api):
        _post(self._many(2), db)
        assert "more signal" not in _api.posts[-1][1]

    def test_the_summary_line_gets_no_anchor(self, db):
        # It is not answerable, so a reaction on it must resolve to nothing.
        _post(self._many(MAX_SIGNAL_REPLIES + 2), db)
        with SlackStore(db) as store:
            assert len(store.thread("C123", Ref.ts)) == MAX_SIGNAL_REPLIES


class TestItNeverCostsTheDelivery:
    def test_one_reply_that_will_not_post_does_not_stop_the_rest(self, db, monkeypatch):
        api = FakeApi(fail_on=["Ada · Untracked work — 👍 if that's right, 👎 if it isn't"])
        monkeypatch.setattr("yeaboi.tools.slack.post_message", api.post_message)
        report = _report({"Ada": [_signal()], "Ben": [_signal(rule="wip-sprawl", title="WIP sprawl")]})
        assert _post(report, db) == 1
        with SlackStore(db) as store:
            assert [a.member for a in store.thread("C123", Ref.ts)] == ["Ben"]

    def test_a_slack_that_raises_is_swallowed(self, db, monkeypatch):
        def _boom(*_a, **_kw):
            raise OSError("network gone")

        monkeypatch.setattr("yeaboi.tools.slack.post_message", _boom)
        assert _post(_report({"Ada": [_signal()]}), db) == 0

    def test_an_artifact_of_an_unexpected_shape_is_swallowed(self, db):
        assert _post(object(), db) == 0

    def test_a_receipt_with_no_ts_posts_nothing(self, db, _api):
        class Empty:
            channel = "C123"
            ts = ""

        assert (
            post_signal_anchors(Empty(), _report({"Ada": [_signal()]}), artifact_kind="standup", run_id=7, db_path=db)
            == 0
        )
        assert _api.posts == []


def test_a_second_votable_mode_is_one_entry_in_a_dict():
    # The shape that keeps the ceremonies engine from learning report shapes.
    assert set(threads._SIGNALS) == {"standup"}


class TestTheInvitationIsOnlyMadeWhenItCanBeAnswered:
    """No allowlist ⇒ no signal replies, though the post itself still goes out.

    A signal reply says "👍 right / 👎 wrong", which is a promise the gesture
    lands somewhere. With an empty or voided allowlist the poll never calls
    Slack at all, so every one of those thumbs is discarded in silence — the
    gesture-with-no-consequence this package refuses to make anywhere else,
    repeated up to twelve times per standup.
    """

    @pytest.mark.parametrize("value", ["", "   ", "not-an-id"])
    def test_no_usable_allowlist_posts_no_replies(self, value, monkeypatch, db, _api):
        monkeypatch.setenv("SLACK_ALLOWED_MEMBER_IDS", value)
        report = _report({"Ada": [_signal()], "Ben": [_signal(rule="wip-sprawl", title="WIP sprawl")]})
        assert _post(report, db) == 0
        assert _api.posts == [], "nothing invites a vote nobody is authorised to cast"

    def test_one_malformed_entry_voids_it_and_so_withholds_the_replies(self, monkeypatch, db, _api):
        # The allowlist's own rule: a half-filled list is the more dangerous of
        # the two because it looks configured. The replies follow it rather than
        # inviting votes only some of the thread can cast.
        monkeypatch.setenv("SLACK_ALLOWED_MEMBER_IDS", "U0123456789,nonsense")
        assert _post(_report({"Ada": [_signal()]}), db) == 0
        assert _api.posts == []
