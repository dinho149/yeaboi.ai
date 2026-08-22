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
from yeaboi.artifacts.store import ArtifactEditStore, artifact_ref
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


class TestCommitKeepsTheRunFindable:
    """A corrected row nobody can look up is the same as no correction at all."""

    def test_a_corrected_reporting_run_keeps_its_session(self, tmp_path):
        """`session_id` is a column on reporting_history, not a field on the report.

        Standup and retro artifacts carry their own, so their committers do not
        need it and this was easy to miss. `ReportingStore.get_latest_report` and
        `get_history` both filter on the column: written empty, the corrected row
        exists in the table and the reporting hub goes on showing the
        uncorrected one.
        """
        from yeaboi.agent.state import DeliveryReport
        from yeaboi.artifacts.session import EditableSession
        from yeaboi.reporting.store import ReportingStore

        db = tmp_path / "sessions.db"
        report = DeliveryReport(headline="Original headline.", period_label="Week 1")
        with ReportingStore(db) as store:
            run_id = store.record_run(report, session_id="s9")

        session = EditableSession(report, kind="reporting", db_path=db, run_id=run_id, session_id="s9")
        session.share.document.apply(
            Edit(edit_id="e1", op="set", path="headline", value="Corrected headline.", base="Original headline.")
        )
        session.commit()

        with ReportingStore(db) as store:
            assert store.get_latest_report("s9").headline == "Corrected headline."
            assert len(store.get_history("s9")) == 2


class TestUnappliedEditsAreShown:
    def test_a_correction_the_artifact_can_no_longer_take_is_kept_and_marked(self, tmp_path):
        """`edits.py` promises a stale correction is "shown as unapplied".

        Replay caught the refusal and dropped the edit, so the reader who wrote
        it saw a document without their change and a history that had never
        heard of it — the exact disappearance the compare-and-swap exists to
        prevent. Here the base is re-generated with different prose, so the
        recorded edit's CAS no longer matches.
        """
        db = tmp_path / "sessions.db"
        original = StandupReport(session_id="s1", date="2026-08-01", team_summary="The original sentence.")
        with StandupStore(db) as store:
            run_id = store.record_run(original)

        session = EditableSession(original, kind="standup", db_path=db, run_id=run_id)
        edit = session.share.document.apply(
            Edit(edit_id="e1", op="set", path="team_summary", value="Corrected.", base="The original sentence.")
        )
        session.persist(session.share, edit, "")

        # The standup is re-run: same paths, different prose underneath them.
        rerun = StandupReport(session_id="s1", date="2026-08-01", team_summary="Something else entirely.")
        reopened = EditableSession(rerun, kind="standup", db_path=db, run_id=run_id)

        assert reopened.share.document.current().team_summary == "Something else entirely."
        assert reopened.share.document.edits() == ()
        unapplied = reopened.share.document.unapplied()
        assert [e.edit_id for e, _ in unapplied] == ["e1"]

        rows = reopened.share.snapshot("")["edits"]
        assert [(r["id"], r["applied"]) for r in rows] == [("e1", False)]
        assert rows[0]["reason"]


class TestTheLease:
    """One writer per document, extended to a writer the TUI cannot see.

    The TUI already obeys this rule by hand — the live standup share is
    deliberately not practice-votable, because a verdict written straight to the
    run beneath an open share is resurrected by the next commit. A lease is that
    same rule, held where a *second process* can read it.
    """

    def _ref(self, run_id):
        return artifact_ref("standup", run_id=run_id)

    def test_opening_a_share_takes_the_lease(self, db):
        path, run_id = db
        EditableSession(report(), kind="standup", db_path=path, run_id=run_id)
        with ArtifactEditStore(path) as store:
            assert store.lease_held("standup", self._ref(run_id))

    def test_committing_releases_it(self, db):
        path, run_id = db
        session = EditableSession(report(), kind="standup", db_path=path, run_id=run_id)
        session.commit()
        with ArtifactEditStore(path) as store:
            assert not store.lease_held("standup", self._ref(run_id))

    def test_closing_without_committing_releases_it(self, db):
        # The case a commit-only release would leak on, and by far the common
        # one: a share opened, read, and closed with Esc.
        path, run_id = db
        session = EditableSession(report(), kind="standup", db_path=path, run_id=run_id)
        session.close()
        with ArtifactEditStore(path) as store:
            assert not store.lease_held("standup", self._ref(run_id))

    def test_it_is_a_context_manager(self, db):
        path, run_id = db
        with EditableSession(report(), kind="standup", db_path=path, run_id=run_id):
            pass
        with ArtifactEditStore(path) as store:
            assert not store.lease_held("standup", self._ref(run_id))

    def test_closing_twice_is_not_an_error(self, db):
        path, run_id = db
        session = EditableSession(report(), kind="standup", db_path=path, run_id=run_id)
        session.commit()
        session.close()
        session.close()

    def test_a_kind_with_no_history_table_still_releases(self, db, tmp_path):
        # `commit` returns 0 early for these; the lease must not ride on the
        # path that happens to have a committer behind it.
        path, _ = db
        session = EditableSession(_profile(), kind="analysis", db_path=path)
        session.commit()
        with ArtifactEditStore(path) as store:
            assert not store.lease_held("analysis", session.ref)

    def test_a_lease_the_store_cannot_write_is_swallowed(self, db):
        # A lease exists to defer somebody else's *cosmetic* write. Losing it is
        # nowhere near worth losing this reader's ability to correct the report
        # in front of them, so every lease call swallows and carries on.
        path, run_id = db
        with ArtifactEditStore(path) as store:
            store._conn.close()  # every statement from here raises
            store.take_lease("standup", self._ref(run_id))  # must not raise
            store.release_lease("standup", self._ref(run_id))  # must not raise
            # And an unreadable lease reads as free, never as held: deferring
            # on a store error would weaken a vote for a reason nobody sees.
            assert store.lease_held("standup", self._ref(run_id)) is False


def _profile():
    from yeaboi.team_profile import TeamProfile

    return TeamProfile(team_id="t1", source="jira", project_key="YB")
