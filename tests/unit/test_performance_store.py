"""Unit tests for PerformanceStore — round-trips, action-item loop, notes."""

from dataclasses import fields

import pytest

from yeaboi.agent.state import Annotation, OneOnOnePrep, OneOnOneRecord, SixMonthReview
from yeaboi.performance.store import PerformanceStore


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sessions.db"


def _assert_every_field_populated(artifact) -> None:
    """Fail if the fixture left any field of ``artifact`` at its dataclass default.

    The full-artifact round-trip tests assert equality, which only proves what the
    fixture actually populated: a field added to the dataclass later and dropped by
    the store's deserializer would compare default-to-default and pass silently.
    This keeps those fixtures honest as the artifacts grow — a new field fails here
    until it is given a distinct value.
    """
    unset = [f.name for f in fields(artifact) if getattr(artifact, f.name) == f.default]
    assert not unset, f"{type(artifact).__name__} fixture leaves fields at their default: {unset}"


class TestPrepRoundTrip:
    def test_record_and_get_latest_prep(self, db_path):
        prep = OneOnOnePrep(
            engineer="Ada",
            date="2026-07-12",
            talking_points=("a", "b"),
            goals=("ship auth",),
            carried_action_items=("write tests",),
        )
        with PerformanceStore(db_path) as store:
            store.record_prep(prep, session_id="s1")
            got = store.get_latest_prep("Ada")
        assert got is not None
        assert got.talking_points == ("a", "b")
        assert got.goals == ("ship auth",)
        assert got.carried_action_items == ("write tests",)

    def test_every_prep_field_round_trips(self, db_path):
        """Every OneOnOnePrep field survives the store's JSON round trip.

        A prep is what a lead reads back before walking into the 1:1, so a field
        the deserializer drops is a talking point, a gap or a caveat that silently
        does not make it to the meeting.
        """
        prep = OneOnOnePrep(
            engineer="Ada",
            date="2026-07-12",
            talking_points=("Auth rollout went out a sprint early", "Wants more review time"),
            feedback=("Unblocked Bob on the migration", "Reviews land late in the day"),
            goals=("Own the billing service", "Mentor one new joiner"),
            gaps=("Little exposure to the deployment pipeline",),
            improvements=("Pair on one deploy per sprint",),
            carried_action_items=("Write the auth runbook",),
            activity_summary="Closed 7 stories across PROJ-101..PROJ-118, mostly auth.",
            warnings=("Only one sprint of history — treat trends as provisional",),
            annotations=(
                Annotation(
                    kind="field",
                    anchor="goals",
                    label="Promo target",
                    text="Senior in H2",
                    author="Lead",
                    avatar="🦊",
                    at="2026-07-12T09:30:00+00:00",
                ),
            ),
        )
        _assert_every_field_populated(prep)
        with PerformanceStore(db_path) as store:
            store.record_prep(prep, session_id="s1")
            got = store.get_latest_prep("Ada")
        assert got == prep

    def test_get_latest_prep_none_when_absent(self, db_path):
        with PerformanceStore(db_path) as store:
            assert store.get_latest_prep("Nobody") is None


class TestCompletionLoop:
    def test_open_action_items_from_latest_completion(self, db_path):
        with PerformanceStore(db_path) as store:
            store.record_completion(OneOnOneRecord(engineer="Ada", date="2026-07-01", action_items=("old",)))
            store.record_completion(OneOnOneRecord(engineer="Ada", date="2026-07-12", action_items=("new1", "new2")))
            # Newest completion's actions win (this is what the next prep carries).
            assert store.get_open_action_items("Ada") == ("new1", "new2")

    def test_open_action_items_empty_when_no_completion(self, db_path):
        with PerformanceStore(db_path) as store:
            assert store.get_open_action_items("Ada") == ()

    def test_recent_completions_newest_first(self, db_path):
        with PerformanceStore(db_path) as store:
            store.record_completion(OneOnOneRecord(engineer="Ada", date="2026-07-01", highlights=("h1",)))
            store.record_completion(OneOnOneRecord(engineer="Ada", date="2026-07-12", highlights=("h2",)))
            recents = store.get_recent_completions("Ada")
        assert [r.date for r in recents] == ["2026-07-12", "2026-07-01"]


class TestCompletionRoundTrip:
    """The completion artifact itself — TestCompletionLoop covers the action-item loop."""

    def test_every_completion_field_round_trips(self, db_path):
        """Every OneOnOneRecord field survives the store's JSON round trip.

        The transcript and the email subject are the durable record of what was
        actually said in a 1:1 — a lead may quote either months later, so neither
        may quietly deserialize back as an empty string.
        """
        record = OneOnOneRecord(
            engineer="Ada",
            date="2026-07-12",
            transcript="Lead: how did the auth rollout land?\nAda: a sprint early, but the runbook is thin.",
            email_subject="1:1 summary — Ada, 12 Jul 2026",
            email_summary="Thanks for the chat. Agreed: you own billing next, runbook lands this week.",
            action_items=("Write the auth runbook", "Draft the billing design"),
            highlights=("Auth shipped early", "Wants deployment exposure"),
            warnings=("Transcript was partial — summary covers the second half only",),
            annotations=(
                Annotation(
                    kind="note",
                    anchor="highlights",
                    text="Raised again in the sprint retro.",
                    author="Lead",
                    avatar="🦊",
                    at="2026-07-12T10:05:00+00:00",
                ),
            ),
        )
        _assert_every_field_populated(record)
        with PerformanceStore(db_path) as store:
            store.record_completion(record, session_id="s1")
            (got,) = store.get_recent_completions("Ada")
        assert got == record


class TestReviewRoundTrip:
    def test_record_and_get_latest_review(self, db_path):
        review = SixMonthReview(
            engineer="Ada",
            period_start="2026-01-12",
            period_end="2026-07-12",
            strengths=("ownership",),
            overall="Strong half.",
            framework_used="default",
        )
        with PerformanceStore(db_path) as store:
            store.record_review(review)
            got = store.get_latest_review("Ada")
        assert got is not None
        assert got.strengths == ("ownership",)
        assert got.overall == "Strong half."

    def test_every_review_field_round_trips(self, db_path):
        """Every SixMonthReview field survives the store's JSON round trip.

        The review is the most quotable artifact this mode writes about a person:
        the period bounds and the framework used are what make a judgement
        attributable, so losing either leaves prose with no scope behind it.
        """
        review = SixMonthReview(
            engineer="Ada",
            period_start="2026-01-12",
            period_end="2026-07-12",
            strengths=("Ownership of the auth rollout", "Clear written design docs"),
            areas_for_improvement=("Reviews queue up late in the sprint",),
            achievements=("Shipped auth a sprint early", "Cut onboarding time to two days"),
            goals=("Own the billing service", "Mentor one new joiner"),
            overall="Strong half, grounded in 14 delivered stories across two teams.",
            framework_used="acme-engineering-ladder-v3",
            warnings=("Half covers 4 sprints of data — narrower than a usual review window",),
            annotations=(
                Annotation(
                    kind="field",
                    anchor="achievements",
                    label="Calibration",
                    text="Reviewed with the staff engineer on 10 Jul.",
                    author="Lead",
                    avatar="🐙",
                    at="2026-07-12T11:00:00+00:00",
                ),
            ),
        )
        _assert_every_field_populated(review)
        with PerformanceStore(db_path) as store:
            store.record_review(review, session_id="s1")
            got = store.get_latest_review("Ada")
        assert got == review


class TestNotes:
    def test_add_and_get_notes_newest_first(self, db_path):
        with PerformanceStore(db_path) as store:
            store.add_note("Ada", "first")
            store.add_note("Ada", "second")
            notes = store.get_notes("Ada")
        assert [n["note_text"] for n in notes] == ["second", "first"]
        assert all("id" in n for n in notes)  # saved-runs hub needs per-note id

    def test_delete_note(self, db_path):
        with PerformanceStore(db_path) as store:
            nid = store.add_note("Ada", "gone")
            store.add_note("Ada", "kept")
            assert store.delete_note(nid) is True
            assert [n["note_text"] for n in store.get_notes("Ada")] == ["kept"]


class TestSavedRunsHub:
    """Per-id getters/deletes + merged history — power the per-engineer saved-artifacts hub."""

    def test_get_engineer_history_merges_all_kinds(self, db_path):
        with PerformanceStore(db_path) as store:
            store.record_prep(OneOnOnePrep(engineer="Ada", date="2026-07-01"))
            store.record_completion(OneOnOneRecord(engineer="Ada", date="2026-07-05"))
            store.record_review(SixMonthReview(engineer="Ada", overall="x"))
            store.add_note("Ada", "a note")
            store.record_prep(OneOnOnePrep(engineer="Bob", date="2026-07-01"))
            rows = store.get_engineer_history("Ada")
        kinds = sorted(r["kind"] for r in rows)
        assert kinds == ["completion", "note", "prep", "review"]
        assert all({"id", "created_at", "title"} <= set(r) for r in rows)

    def test_one_on_one_by_id_dispatches_on_kind(self, db_path):
        with PerformanceStore(db_path) as store:
            pid = store.record_prep(OneOnOnePrep(engineer="Ada", date="2026-07-01", goals=("g",)))
            cid = store.record_completion(OneOnOneRecord(engineer="Ada", date="2026-07-05", highlights=("h",)))
            pk, prep = store.get_one_on_one_by_id(pid)
            ck, comp = store.get_one_on_one_by_id(cid)
        assert pk == "prep" and prep.goals == ("g",)
        assert ck == "completion" and comp.highlights == ("h",)

    def test_get_by_id_missing_and_corrupt(self, db_path):
        with PerformanceStore(db_path) as store:
            rid = store.record_review(SixMonthReview(engineer="Ada", overall="x"))
            assert store.get_review_by_id(rid) is not None
            assert store.get_review_by_id(999) is None
            assert store.get_one_on_one_by_id(999) is None
            store._conn.execute("UPDATE performance_reviews SET report_json='{bad' WHERE id=?", (rid,))
            assert store.get_review_by_id(rid) is None

    def test_delete_one_on_one_and_review(self, db_path):
        with PerformanceStore(db_path) as store:
            pid = store.record_prep(OneOnOnePrep(engineer="Ada", date="2026-07-01"))
            rid = store.record_review(SixMonthReview(engineer="Ada", overall="x"))
            assert store.delete_one_on_one(pid) is True
            assert store.delete_review(rid) is True
            assert store.get_engineer_history("Ada") == []


class TestTeamWide:
    def test_all_open_action_items_latest_per_engineer(self, db_path):
        with PerformanceStore(db_path) as store:
            store.record_completion(OneOnOneRecord(engineer="Ada", date="2026-07-01", action_items=("a-old",)))
            store.record_completion(OneOnOneRecord(engineer="Ada", date="2026-07-12", action_items=("a-new",)))
            store.record_completion(OneOnOneRecord(engineer="Bob", date="2026-07-10", action_items=("b1",)))
            allitems = store.get_all_open_action_items()
        assert allitems["Ada"] == ("a-new",)
        assert allitems["Bob"] == ("b1",)

    def test_recent_reviews(self, db_path):
        with PerformanceStore(db_path) as store:
            store.record_review(SixMonthReview(engineer="Ada", overall="x"))
            store.record_review(SixMonthReview(engineer="Bob", overall="y"))
            reviews = store.get_recent_reviews()
        assert {r.engineer for r in reviews} == {"Ada", "Bob"}


class TestProvenanceSelfHeal:
    """Performance tables missing the v21 provenance columns heal on store open.

    The v21 schema-version collision could leave a shared DB stamped past 21
    without origin/edited_from_id, and the CLI and MCP tools open this store
    without ever constructing a SessionStore (whose v26 migration is the other
    repair path).
    """

    def test_pre_v21_tables_heal_on_open(self, db_path):
        import sqlite3

        PerformanceStore(db_path).close()
        conn = sqlite3.connect(str(db_path))
        for table in ("performance_one_on_ones", "performance_reviews"):
            keep = ", ".join(
                r[1] for r in conn.execute(f"PRAGMA table_info({table})") if r[1] not in ("origin", "edited_from_id")
            )
            conn.executescript(
                f"CREATE TABLE pre AS SELECT {keep} FROM {table};DROP TABLE {table};ALTER TABLE pre RENAME TO {table};"
            )
        conn.close()
        with PerformanceStore(db_path) as store:
            for table in ("performance_one_on_ones", "performance_reviews"):
                cols = {r[1] for r in store._conn.execute(f"PRAGMA table_info({table})")}
                assert {"origin", "edited_from_id"} <= cols, table
