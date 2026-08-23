"""Unit tests for PerformanceStore — round-trips, action-item loop, notes."""

from dataclasses import fields

import pytest

from yeaboi.agent.state import (
    ActivityEvidence,
    Annotation,
    EngineerActivity,
    EngineerStory,
    EvidenceGroup,
    OneOnOnePrep,
    OneOnOneRecord,
    PerfMetric,
    SixMonthReview,
)
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
            evidence_sources=("standup", "code", "analysis"),
            evidence_coverage=(
                ("code", "covered", "Scanned by 12 of 12 standup run(s)."),
                ("documentation", "not_configured", "No standup run in this period scanned documentation."),
            ),
            metrics=(
                PerfMetric(
                    key="stories_completed",
                    label="Stories completed",
                    value=12.0,
                    denominator=14.0,
                    group="delivery",
                    source="analysis",
                    detail="Closed 12 of the 14 stories assigned in the window.",
                ),
                PerfMetric(
                    key="tests_rate",
                    label="Changes with tests",
                    value=62.0,
                    unit="%",
                    group="practice",
                    source="analysis",
                ),
            ),
            evidence_items=(
                EvidenceGroup(
                    source="code",
                    label="Code activity",
                    note="capped at 1 of 41",
                    items=(
                        ActivityEvidence(
                            kind="pr",
                            key="#91",
                            title="Roll SSO out to every tenant",
                            url="https://github.com/acme/web/pull/91",
                            repository="acme/web",
                            status="merged",
                            timestamp="2026-07-09T11:00:00+00:00",
                            ticket_keys=("PROJ-118",),
                            children=(ActivityEvidence(kind="commit", key="78e4201", title="Add tenant guard"),),
                        ),
                    ),
                ),
            ),
            section_states=(("gaps", "partial", "No analysis run covered the second month."),),
            activity=EngineerActivity(
                engineer="Ada",
                current_sprint="Sprint 14",
                previous_sprint="Sprint 13",
                stories=(
                    EngineerStory(key="PROJ-118", title="SSO rollout", status="Done", kind="issue", source="jira"),
                ),
                total_items=7,
                sources=(("jira", 7),),
            ),
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
            delivery_state="sent",
            evidence_sources=("standup", "code", "analysis"),
            evidence_coverage=(("code", "covered", "Scanned by 12 of 12 standup run(s)."),),
            metrics=(
                PerfMetric(
                    key="stories_completed",
                    label="Stories completed",
                    value=12.0,
                    denominator=14.0,
                    group="delivery",
                    source="analysis",
                    detail="Closed 12 of the 14 stories assigned in the window.",
                ),
                PerfMetric(
                    key="tests_rate",
                    label="Changes with tests",
                    value=62.0,
                    unit="%",
                    group="practice",
                    source="analysis",
                ),
            ),
            evidence_items=(
                EvidenceGroup(
                    source="code",
                    label="Code activity",
                    note="capped at 1 of 41",
                    items=(
                        ActivityEvidence(
                            kind="pr",
                            key="#91",
                            title="Roll SSO out to every tenant",
                            url="https://github.com/acme/web/pull/91",
                            repository="acme/web",
                            status="merged",
                            timestamp="2026-07-09T11:00:00+00:00",
                            ticket_keys=("PROJ-118",),
                            children=(ActivityEvidence(kind="commit", key="78e4201", title="Add tenant guard"),),
                        ),
                    ),
                ),
            ),
            section_states=(("gaps", "partial", "No analysis run covered the second month."),),
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
            evidence_sources=("standup", "code", "analysis"),
            evidence_coverage=(
                ("code", "covered", "Scanned by 12 of 12 standup run(s)."),
                ("documentation", "not_configured", "No standup run in this period scanned documentation."),
            ),
            metrics=(
                PerfMetric(
                    key="stories_completed",
                    label="Stories completed",
                    value=12.0,
                    denominator=14.0,
                    group="delivery",
                    source="analysis",
                    detail="Closed 12 of the 14 stories assigned in the window.",
                ),
                PerfMetric(
                    key="tests_rate",
                    label="Changes with tests",
                    value=62.0,
                    unit="%",
                    group="practice",
                    source="analysis",
                ),
            ),
            evidence_items=(
                EvidenceGroup(
                    source="code",
                    label="Code activity",
                    note="capped at 1 of 41",
                    items=(
                        ActivityEvidence(
                            kind="pr",
                            key="#91",
                            title="Roll SSO out to every tenant",
                            url="https://github.com/acme/web/pull/91",
                            repository="acme/web",
                            status="merged",
                            timestamp="2026-07-09T11:00:00+00:00",
                            ticket_keys=("PROJ-118",),
                            children=(ActivityEvidence(kind="commit", key="78e4201", title="Add tenant guard"),),
                        ),
                    ),
                ),
            ),
            section_states=(("gaps", "partial", "No analysis run covered the second month."),),
            activity=EngineerActivity(
                engineer="Ada",
                current_sprint="Sprint 14",
                previous_sprint="Sprint 13",
                stories=(
                    EngineerStory(key="PROJ-118", title="SSO rollout", status="Done", kind="issue", source="jira"),
                ),
                total_items=7,
                sources=(("jira", 7),),
            ),
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

    def test_get_all_history_spans_every_engineer(self, db_path):
        """The Performance card's landing lists the whole team, each row naming its owner."""
        with PerformanceStore(db_path) as store:
            store.record_prep(OneOnOnePrep(engineer="Ada", date="2026-07-01"))
            store.record_completion(OneOnOneRecord(engineer="Ada", date="2026-07-05"))
            store.record_review(SixMonthReview(engineer="Bob", overall="x"))
            store.add_note("Bob", "a note")
            rows = store.get_all_history()
        assert sorted(r["engineer"] for r in rows) == ["Ada", "Ada", "Bob", "Bob"]
        assert sorted(r["kind"] for r in rows) == ["completion", "note", "prep", "review"]

    def test_get_all_history_agrees_with_the_scoped_read(self, db_path):
        """One engineer's slice of the team-wide read is that engineer's own history.

        The landing hub and the roster's History action must show the same artifact for
        the same person, or deleting from one would appear to leave it in the other.
        """
        with PerformanceStore(db_path) as store:
            store.record_prep(OneOnOnePrep(engineer="Ada", date="2026-07-01"))
            store.add_note("Ada", "a note")
            store.record_review(SixMonthReview(engineer="Bob", overall="x"))
            scoped = store.get_engineer_history("Ada")
            mine = [r for r in store.get_all_history() if r["engineer"] == "Ada"]
        assert [(r["kind"], r["id"], r["title"]) for r in mine] == [(r["kind"], r["id"], r["title"]) for r in scoped]

    def test_get_all_history_is_newest_first_and_respects_limit(self, db_path):
        with PerformanceStore(db_path) as store:
            store.record_prep(OneOnOnePrep(engineer="Ada", date="2026-07-01"))
            store.record_review(SixMonthReview(engineer="Bob", overall="x"))
            store.add_note("Cleo", "a note")
            store.record_completion(OneOnOneRecord(engineer="Ada", date="2026-07-05"))
            stamps = [r["created_at"] for r in store.get_all_history()]
            assert stamps == sorted(stamps, reverse=True)
            assert len(store.get_all_history(limit=2)) == 2

    def test_get_all_history_empty_when_nothing_saved(self, db_path):
        with PerformanceStore(db_path) as store:
            assert store.get_all_history() == []

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


class TestMaskingReachesEveryField:
    """Anonymize rebuilds an artifact through this module's ``_dict_to_*``.

    A field the reconstructor does not read is therefore dropped from every
    masked artifact — silently, and only on the path whose whole purpose is to
    make something safe to publish. This is the guard for that.
    """

    @staticmethod
    def _spine(**extra):
        return dict(
            evidence_sources=("analysis",),
            evidence_coverage=(("code", "covered", "Ada Lovelace was scanned."),),
            metrics=(PerfMetric(key="spill_rate", label="Spill rate", value=18.0, unit="%", source="analysis"),),
            evidence_items=(
                EvidenceGroup(
                    source="code",
                    label="Code activity",
                    note="capped at 1 of 9",
                    items=(
                        ActivityEvidence(
                            kind="pr",
                            key="#91",
                            title="Ada Lovelace ships SSO",
                            children=(ActivityEvidence(kind="commit", key="78e4201", title="Ada Lovelace fixes it"),),
                        ),
                    ),
                ),
            ),
            section_states=(("gaps", "partial", "Only Ada Lovelace was covered."),),
            **extra,
        )

    @pytest.mark.parametrize("kind", ["prep", "record", "review"])
    def test_every_spine_field_survives_a_masking_round_trip(self, kind):
        from yeaboi.anonymize.apply import mask_artifact

        artifact = {
            "prep": lambda: OneOnOnePrep(engineer="Ada Lovelace", **self._spine()),
            "record": lambda: OneOnOneRecord(engineer="Ada Lovelace", delivery_state="sent", **self._spine()),
            "review": lambda: SixMonthReview(engineer="Ada Lovelace", **self._spine()),
        }[kind]()

        masked = mask_artifact(artifact, [("Ada Lovelace", "Engineer A")])

        assert masked.metrics == artifact.metrics  # numbers are not names
        assert masked.evidence_sources == artifact.evidence_sources
        assert len(masked.evidence_items) == 1
        assert masked.evidence_items[0].note == "capped at 1 of 9"
        assert masked.section_states[0][1] == "partial"

    def test_masking_reaches_inside_nested_evidence_rows(self):
        from yeaboi.anonymize.apply import mask_artifact

        prep = OneOnOnePrep(engineer="Ada Lovelace", **self._spine())
        masked = mask_artifact(prep, [("Ada Lovelace", "Engineer A")])

        row = masked.evidence_items[0].items[0]
        assert row.title == "Engineer A ships SSO"
        # A commit folded under its PR is a row a reader sees, so it masks too.
        assert row.children[0].title == "Engineer A fixes it"
        assert masked.section_states[0][2] == "Only Engineer A was covered."
