"""Opening a correctable share, recording what it accepts, and keeping it.

The two halves are deliberately separate and the tests say why: an accepted edit
is persisted at once because losing somebody's correction to a dropped
connection is the worst failure available here, while *committing* the corrected
report is a later decision the host makes, because the share screen's teardown
also runs on Esc and on an exception.
"""

from __future__ import annotations

import pytest

from yeaboi.agent.state import MemberUpdate, StandupReport
from yeaboi.artifacts.edits import Edit
from yeaboi.artifacts.session import EditableSession
from yeaboi.artifacts.store import ArtifactEditStore
from yeaboi.standup.store import StandupStore


def report() -> StandupReport:
    return StandupReport(
        session_id="s1",
        date="2026-08-01",
        team_summary="The team shipped auth.",
        confidence_pct=60,
        member_updates=(MemberUpdate(name="Ada", summary="Landed login.", blockers="staging db"),),
    )


def an_edit(**kw) -> Edit:
    return Edit(
        edit_id=kw.pop("edit_id", "e1"),
        op=kw.pop("op", "set"),
        path=kw.pop("path", "team_summary"),
        author=kw.pop("author", "Grace"),
        **kw,
    )


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "sessions.db"
    with StandupStore(path) as store:
        run_id = store.record_run(report())
    return path, run_id


@pytest.fixture
def session(db):
    path, run_id = db
    return EditableSession(report(), kind="standup", db_path=path, run_id=run_id)


class TestRecording:
    def test_an_accepted_edit_reaches_the_log(self, session, db):
        path, _ = db
        edit = session.share.document.apply(an_edit(value="Corrected."))
        session.persist(session.share, edit, "10.0.0.4")
        with ArtifactEditStore(path) as store:
            assert store.count_edits("standup", session.ref) == 1

    def test_the_log_names_the_run_it_belongs_to(self, session, db):
        _, run_id = db
        assert session.ref == f"standup:{run_id}"

    def test_the_base_is_pinned_by_the_first_edit(self, session, db):
        path, _ = db
        edit = session.share.document.apply(an_edit(value="Corrected."))
        session.persist(session.share, edit, "")
        with ArtifactEditStore(path) as store:
            assert store.recorded_base_hash("standup", session.ref)

    def test_the_raw_address_is_never_stored(self, session, db):
        path, _ = db
        edit = session.share.document.apply(an_edit(value="Corrected."))
        session.persist(session.share, edit, "10.0.0.4")
        with ArtifactEditStore(path) as store:
            row = store._conn.execute("SELECT ip_hash FROM artifact_edits").fetchone()
        assert row["ip_hash"] and "10.0.0.4" not in row["ip_hash"]

    def test_two_shares_of_one_run_cannot_be_linked_by_address(self, db):
        # The salt is per-share, so the same reader hashes differently in two
        # documents — a log copied between machines is not a movement record.
        path, run_id = db
        first = EditableSession(report(), kind="standup", db_path=path, run_id=run_id)
        second = EditableSession(report(), kind="standup", db_path=path, run_id=run_id)
        assert first.share.salt != second.share.salt


class TestCommitting:
    def test_a_corrected_run_is_appended_not_updated(self, session, db):
        path, parent = db
        session.share.document.apply(an_edit(value="Corrected."))
        row_id = session.commit()
        assert row_id != parent
        with StandupStore(path) as store:
            assert len(store.get_history("s1")) == 2
            assert store.get_run_by_id(parent) is not None

    def test_the_corrected_row_becomes_the_latest(self, session, db):
        path, _ = db
        session.share.document.apply(an_edit(value="Corrected."))
        session.commit()
        with StandupStore(path) as store:
            assert store.get_latest_report("s1").team_summary == "Corrected."

    def test_the_corrected_row_supersedes_its_parent_in_a_trend(self, session, db):
        from yeaboi.html_theme import history_series

        path, _ = db
        session.share.document.apply(an_edit(value="Corrected."))
        session.commit()
        with StandupStore(path) as store:
            history = store.get_history("s1")
        # Same date, so the newest wins — which is how "edits become the
        # artifact" happens with no read-path change anywhere.
        assert len(history_series(history, date_key="standup_date", value_key="confidence_pct")) == 1

    def test_committing_records_where_it_came_from(self, session, db):
        path, parent = db
        session.share.document.apply(an_edit(value="Corrected."))
        row_id = session.commit()
        with StandupStore(path) as store:
            row = store._conn.execute(
                "SELECT origin, edited_from_id FROM standup_history WHERE id = ?", (row_id,)
            ).fetchone()
        assert tuple(row) == ("edited", parent)

    def test_a_kind_with_no_history_table_commits_nothing_and_says_so(self, tmp_path):
        # A team profile is an upsert keyed by team; its only history is the
        # edit log, and pretending otherwise would be worse than returning 0.
        from yeaboi.team_profile import TeamProfile

        profile = TeamProfile(team_id="t1", source="jira", project_key="YB")
        session = EditableSession(profile, kind="analysis", db_path=tmp_path / "sessions.db")
        assert session.commit() == 0

    def test_nothing_is_committed_until_asked(self, session, db):
        path, _ = db
        session.share.document.apply(an_edit(value="Corrected."))
        with StandupStore(path) as store:
            assert len(store.get_history("s1")) == 1, "teardown must not write a corrected row by itself"


class TestCommittingNeverRedelivers:
    """Editing changes the artifact, the exports and the history — nothing else.

    The spec called this out explicitly, so it is asserted rather than left as a
    property of what the committers happen to call today. A correction that
    re-sent the standup to Slack every time somebody fixed a typo would be a
    worse feature than not having it.
    """

    def test_no_committer_reaches_the_delivery_module(self, session, db, monkeypatch):
        import yeaboi.standup.delivery as delivery

        def explode(*_args, **_kwargs):
            raise AssertionError("committing a correction must never deliver")

        monkeypatch.setattr(delivery, "deliver", explode)
        session.share.document.apply(an_edit(value="Corrected."))
        session.commit()

    def test_the_committers_call_only_record_run(self):
        # A source-level check as well, because a delivery call added behind a
        # lazy import inside a committer would not be caught by the monkeypatch
        # above unless a test happened to exercise that mode.
        import inspect

        from yeaboi.artifacts import session as module

        for kind, committer in module._COMMITTERS.items():
            body = inspect.getsource(committer)
            assert "record_run" in body, kind
            for forbidden in ("deliver", "smtp", "webhook", "notify"):
                assert forbidden not in body.lower(), f"{kind} committer mentions {forbidden!r}"
