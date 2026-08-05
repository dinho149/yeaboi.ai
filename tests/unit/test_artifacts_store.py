"""Tests for the append-only edit log and the v21 migration.

Two claims are load-bearing and both are asserted against a real database here,
not reasoned about: a retried POST must not double-apply a correction, and an
edited report must supersede its generated parent everywhere without any
read-path knowing that edits exist.
"""

from __future__ import annotations

import sqlite3

import pytest

from yeaboi.agent.state import StandupReport
from yeaboi.artifacts.edits import Edit
from yeaboi.artifacts.store import ArtifactEditStore, artifact_ref, base_hash, hash_ip

KIND = "standup"
REF = "standup:7"


@pytest.fixture
def store(tmp_path):
    with ArtifactEditStore(tmp_path / "sessions.db") as s:
        yield s


def edit(edit_id="e1", **kw) -> Edit:
    return Edit(edit_id=edit_id, op=kw.pop("op", "set"), path=kw.pop("path", "team_summary"), **kw)


class TestRef:
    def test_a_run_id_wins(self):
        assert artifact_ref("standup", run_id=7, session_id="s") == "standup:7"

    def test_an_engineer_names_a_performance_artifact(self):
        assert artifact_ref("performance_prep", engineer="Ada") == "performance_prep:engineer:Ada"

    def test_a_session_is_the_fallback(self):
        assert artifact_ref("analysis", session_id="abc") == "analysis:session:abc"

    def test_two_runs_of_one_mode_do_not_collide(self):
        assert artifact_ref("standup", run_id=7) != artifact_ref("standup", run_id=8)


class TestBaseHash:
    def test_the_same_artifact_hashes_the_same(self):
        assert base_hash(StandupReport(date="2026-08-01")) == base_hash(StandupReport(date="2026-08-01"))

    def test_a_changed_artifact_hashes_differently(self):
        # This is the whole point: a re-run standup must not look like the one a
        # log was written against, or a stale correction replays onto new prose.
        assert base_hash(StandupReport(team_summary="a")) != base_hash(StandupReport(team_summary="b"))


class TestHashIp:
    def test_the_address_itself_is_never_stored(self):
        assert "10.0.0.4" not in hash_ip("10.0.0.4", "salt")

    def test_the_same_address_is_recognisable_within_one_share(self):
        assert hash_ip("10.0.0.4", "salt") == hash_ip("10.0.0.4", "salt")

    def test_a_different_salt_breaks_the_link_across_shares(self):
        assert hash_ip("10.0.0.4", "one") != hash_ip("10.0.0.4", "two")

    def test_an_empty_address_hashes_to_nothing(self):
        assert hash_ip("", "salt") == ""


class TestRecording:
    def test_an_edit_comes_back_out(self, store):
        store.record(edit(value="corrected"), kind=KIND, ref=REF)
        (row,) = store.list_edits(KIND, REF)
        assert (row.edit_id, row.value, row.seq) == ("e1", "corrected", 1)

    def test_sequence_numbers_increment_per_artifact(self, store):
        store.record(edit("e1"), kind=KIND, ref=REF)
        store.record(edit("e2"), kind=KIND, ref=REF)
        assert [e.seq for e in store.list_edits(KIND, REF)] == [1, 2]

    def test_two_artifacts_number_independently(self, store):
        store.record(edit("e1"), kind=KIND, ref="standup:1")
        store.record(edit("e2"), kind=KIND, ref="standup:2")
        assert store.list_edits(KIND, "standup:2")[0].seq == 1

    def test_a_retried_post_does_not_double_apply(self, store):
        # A dropped tunnel or a backgrounded phone retries a request it never saw
        # the answer to. Appending twice would append the bullet twice.
        first = store.record(edit("e1", op="append", path="highlights[-]", value="one"), kind=KIND, ref=REF)
        second = store.record(edit("e1", op="append", path="highlights[-]", value="one"), kind=KIND, ref=REF)
        assert first == second
        assert store.count_edits(KIND, REF) == 1

    def test_the_uniqueness_is_a_database_fact_not_a_check(self, store):
        # Two request threads can pass a "does it exist" check at the same time;
        # only the index actually stops the second insert.
        store.record(edit("e1"), kind=KIND, ref=REF)
        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute(
                "INSERT INTO artifact_edits (edit_id, artifact_kind, artifact_ref) VALUES (?, ?, ?)",
                ("e1", KIND, REF),
            )

    def test_every_field_of_an_edit_round_trips(self, store):
        original = Edit(
            edit_id="e9",
            op="field",
            path="member_updates[name=Ada]",
            value="Grace",
            base="before",
            label="Risk owner",
            target="",
            author="Ada",
            avatar="🦊",
            pid="pid-123",
            at="2026-08-01T09:00:00+00:00",
        )
        store.record(original, kind=KIND, ref=REF)
        (row,) = store.list_edits(KIND, REF)
        assert row == Edit(**{**original.__dict__, "seq": 1})

    def test_ordering_is_by_sequence_not_by_clock(self, store):
        # Two edits inside one clock tick must still replay in accept order, or
        # materialisation stops being deterministic.
        same = "2026-08-01T09:00:00+00:00"
        store.record(edit("e1", value="first", at=same), kind=KIND, ref=REF)
        store.record(edit("e2", value="second", at=same), kind=KIND, ref=REF)
        assert [e.value for e in store.list_edits(KIND, REF)] == ["first", "second"]

    def test_the_base_hash_is_pinned_by_the_first_edit(self, store):
        store.record(edit("e1"), kind=KIND, ref=REF, base="hash-a")
        store.record(edit("e2"), kind=KIND, ref=REF, base="hash-b")
        assert store.recorded_base_hash(KIND, REF) == "hash-a"

    def test_no_base_hash_recorded_yet_is_empty(self, store):
        assert store.recorded_base_hash(KIND, "nothing:0") == ""

    def test_editors_are_listed_once_each(self, store):
        store.record(edit("e1", author="Ada"), kind=KIND, ref=REF)
        store.record(edit("e2", author="Ada"), kind=KIND, ref=REF)
        store.record(edit("e3", author="Grace"), kind=KIND, ref=REF)
        assert store.editors(KIND, REF) == ("Ada", "Grace")

    def test_the_log_offers_no_way_to_delete(self, store):
        # Reverting appends. A history you can quietly remove rows from is not
        # the thing anyone asked for.
        assert not any(name.startswith("delete") for name in dir(store))


def _strip_provenance(conn, table):
    """Remove the v21 columns by rebuilding the table.

    Not ``ALTER TABLE … DROP COLUMN``: that rewrites the stored CREATE TABLE
    text and chokes on the inline comments some schemas carry ("incomplete
    input"), so a swallowed error would leave the column in place and make the
    migration test vacuous. The result is asserted so setup cannot silently
    no-op.
    """
    keep = ", ".join(
        row[1] for row in conn.execute(f"PRAGMA table_info({table})") if row[1] not in ("origin", "edited_from_id")
    )
    conn.executescript(
        f"CREATE TABLE {table}_pre AS SELECT {keep} FROM {table};"
        f"DROP TABLE {table};"
        f"ALTER TABLE {table}_pre RENAME TO {table};"
    )
    remaining = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    assert not {"origin", "edited_from_id"} & remaining, table


class TestMigration:
    """A v20 database must reach v21 cleanly, and twice must be the same as once."""

    def _v20_db(self, tmp_path):
        from yeaboi.sessions import SessionStore

        path = tmp_path / "sessions.db"
        SessionStore(path).close()  # creates at CURRENT_SCHEMA_VERSION
        conn = sqlite3.connect(str(path))
        conn.execute("UPDATE schema_info SET schema_version = 20")
        conn.execute("DROP TABLE IF EXISTS artifact_edits")
        for table in ("standup_history", "retro_history", "reporting_history"):
            _strip_provenance(conn, table)
        conn.commit()
        conn.close()
        return path

    def _columns(self, path, table):
        conn = sqlite3.connect(str(path))
        try:
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        finally:
            conn.close()

    def test_v20_upgrades_to_v21(self, tmp_path):
        from yeaboi.sessions import CURRENT_SCHEMA_VERSION, SessionStore

        path = self._v20_db(tmp_path)
        store = SessionStore(path)
        try:
            assert not store.schema_mismatch
        finally:
            store.close()
        conn = sqlite3.connect(str(path))
        try:
            assert conn.execute("SELECT schema_version FROM schema_info").fetchone()[0] == CURRENT_SCHEMA_VERSION
            assert conn.execute("SELECT COUNT(*) FROM artifact_edits").fetchone()[0] == 0
        finally:
            conn.close()

    @pytest.mark.parametrize("table", ["standup_history", "retro_history", "reporting_history"])
    def test_provenance_columns_are_added(self, tmp_path, table):
        from yeaboi.sessions import SessionStore

        path = self._v20_db(tmp_path)
        SessionStore(path).close()
        assert {"origin", "edited_from_id"} <= self._columns(path, table)

    def test_running_it_twice_is_harmless(self, tmp_path):
        from yeaboi.sessions import SessionStore

        path = self._v20_db(tmp_path)
        SessionStore(path).close()
        SessionStore(path).close()  # already migrated — must not raise

    def test_a_fresh_database_matches_a_migrated_one(self, tmp_path):
        """The two paths to the same schema must agree.

        A fresh DB is built from each mode's own `_SCHEMA` string; a migrated one
        from the ALTER ladder. If they drift, a bug only reproduces for users who
        upgraded — the hardest kind to be handed.
        """
        from yeaboi.sessions import SessionStore

        fresh = tmp_path / "fresh.db"
        SessionStore(fresh).close()
        migrated = self._v20_db(tmp_path)
        SessionStore(migrated).close()
        for table in ("standup_history", "retro_history", "reporting_history", "artifact_edits"):
            assert self._columns(fresh, table) == self._columns(migrated, table), table


class TestVersionCollisionRepair:
    """A DB stamped past 21 by the colliding lineage must still gain the columns.

    A pre-rebase branch used version 21 for a different migration and stamped
    shared databases with it, so main's v21 (edit provenance) was skipped while
    the DB migrated on to v25. Migration v26 re-runs the idempotent body.
    """

    _TABLES = (
        "standup_history",
        "retro_history",
        "reporting_history",
        "roadmap_history",
        "performance_one_on_ones",
        "performance_reviews",
    )

    def _collided_db(self, tmp_path):
        from yeaboi.sessions import SessionStore

        path = tmp_path / "sessions.db"
        SessionStore(path).close()  # creates at CURRENT_SCHEMA_VERSION
        conn = sqlite3.connect(str(path))
        conn.execute("UPDATE schema_info SET schema_version = 25")
        conn.execute("DROP TABLE IF EXISTS artifact_edits")
        for table in self._TABLES:
            _strip_provenance(conn, table)
        conn.commit()
        conn.close()
        return path

    def _columns(self, path, table):
        conn = sqlite3.connect(str(path))
        try:
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        finally:
            conn.close()

    def test_collided_db_upgrades_to_current(self, tmp_path):
        from yeaboi.sessions import CURRENT_SCHEMA_VERSION, SessionStore

        path = self._collided_db(tmp_path)
        store = SessionStore(path)
        try:
            assert not store.schema_mismatch
        finally:
            store.close()
        conn = sqlite3.connect(str(path))
        try:
            assert conn.execute("SELECT schema_version FROM schema_info").fetchone()[0] == CURRENT_SCHEMA_VERSION
            assert conn.execute("SELECT COUNT(*) FROM artifact_edits").fetchone()[0] == 0
        finally:
            conn.close()

    @pytest.mark.parametrize(
        "table",
        [
            "standup_history",
            "retro_history",
            "reporting_history",
            "roadmap_history",
            "performance_one_on_ones",
            "performance_reviews",
        ],
    )
    def test_provenance_columns_repaired(self, tmp_path, table):
        from yeaboi.sessions import SessionStore

        path = self._collided_db(tmp_path)
        SessionStore(path).close()
        assert {"origin", "edited_from_id"} <= self._columns(path, table)

    def test_existing_rows_backfill_generated(self, tmp_path):
        path = self._collided_db(tmp_path)
        conn = sqlite3.connect(str(path))
        conn.execute(
            "INSERT INTO standup_history (session_id, run_at, standup_date) VALUES ('s1', 'now', '2026-08-01')"
        )
        conn.commit()
        conn.close()

        from yeaboi.sessions import SessionStore

        SessionStore(path).close()
        conn = sqlite3.connect(str(path))
        try:
            row = conn.execute("SELECT origin, edited_from_id FROM standup_history").fetchone()
            assert row == ("generated", 0)
        finally:
            conn.close()

    def test_repair_twice_is_harmless(self, tmp_path):
        from yeaboi.sessions import SessionStore

        path = self._collided_db(tmp_path)
        SessionStore(path).close()
        SessionStore(path).close()  # already repaired — must not raise

    def test_newer_stamp_still_heals(self, tmp_path):
        # A future lineage stamping past 26 must not re-create the trap: the
        # schema_mismatch branch runs no migrations, so it applies the
        # idempotent repair directly.
        from yeaboi.sessions import CURRENT_SCHEMA_VERSION, SessionStore

        path = self._collided_db(tmp_path)
        conn = sqlite3.connect(str(path))
        conn.execute("UPDATE schema_info SET schema_version = ?", (CURRENT_SCHEMA_VERSION + 5,))
        conn.commit()
        conn.close()

        store = SessionStore(path)
        try:
            assert store.schema_mismatch  # the warning still fires
        finally:
            store.close()
        for table in self._TABLES:
            assert {"origin", "edited_from_id"} <= self._columns(path, table)


class TestEditedRunsSupersede:
    """Appending an edited row must beat its parent with no read-path change."""

    def test_history_series_keeps_the_edited_row_for_that_date(self, tmp_path):
        from yeaboi.html_theme import history_series
        from yeaboi.standup.store import StandupStore

        report = StandupReport(session_id="s", date="2026-08-01", confidence_pct=60)
        with StandupStore(tmp_path / "sessions.db") as store:
            parent = store.record_run(report)
            corrected = StandupReport(session_id="s", date="2026-08-01", confidence_pct=75)
            store.record_run(corrected, origin="edited", edited_from_id=parent)
            history = store.get_history("s")

        series = history_series(history, date_key="standup_date", value_key="confidence_pct")
        assert series == [("2026-08-01", 75)], "the corrected row should win for its date"

    def test_the_generated_original_survives(self, tmp_path):
        from yeaboi.standup.store import StandupStore

        with StandupStore(tmp_path / "sessions.db") as store:
            parent = store.record_run(StandupReport(session_id="s", date="2026-08-01"))
            store.record_run(StandupReport(session_id="s", date="2026-08-01"), origin="edited", edited_from_id=parent)
            assert len(store.get_history("s")) == 2
            assert store.get_run_by_id(parent) is not None

    def test_the_latest_report_is_the_corrected_one(self, tmp_path):
        from yeaboi.standup.store import StandupStore

        with StandupStore(tmp_path / "sessions.db") as store:
            store.record_run(StandupReport(session_id="s", date="2026-08-01", team_summary="wrong"))
            store.record_run(
                StandupReport(session_id="s", date="2026-08-01", team_summary="right"),
                origin="edited",
                edited_from_id=1,
            )
            assert store.get_latest_report("s").team_summary == "right"

    def test_status_is_untouched_so_yesterday_still_resolves(self, tmp_path):
        """Provenance goes in `origin`, never in `status`.

        `get_previous_report` filters `status IN ('success','partial')`. Marking
        an edited row with a third status would drop every corrected standup out
        of the next day's day-over-day comparison — silently, and only for teams
        who actually used the feature.
        """
        from yeaboi.standup.store import StandupStore

        with StandupStore(tmp_path / "sessions.db") as store:
            store.record_run(
                StandupReport(session_id="s", date="2026-07-31", team_summary="corrected"), origin="edited"
            )
            previous = store.get_previous_report("s", before_date="2026-08-01")
        assert previous is not None and previous.team_summary == "corrected"


class TestConcurrentWriters:
    """Two threads must not be able to take the same sequence number.

    Materialisation was never at risk — it orders by seq then id — but a
    duplicate seq makes the edit history lie about what happened first, which is
    the one thing this table exists to be trusted about.
    """

    def test_parallel_records_all_get_distinct_sequences(self, tmp_path):
        import threading

        path = tmp_path / "sessions.db"
        ArtifactEditStore(path).close()  # create the schema once, up front

        # Every wait here is bounded. A barrier with no timeout turns one thread
        # failing early into a test that hangs the whole suite rather than one
        # that fails — which is exactly what it did the first time this was
        # written.
        ready = threading.Barrier(8, timeout=20)
        results: list[int] = []
        failures: list[str] = []
        lock = threading.Lock()

        def writer(index: int) -> None:
            try:
                with ArtifactEditStore(path) as store:
                    ready.wait()
                    seq = store.record(edit(f"e{index}"), kind=KIND, ref=REF)
                with lock:
                    results.append(seq)
            except Exception as exc:  # noqa: BLE001 — reported, not swallowed
                ready.abort()  # release the others rather than stranding them
                with lock:
                    failures.append(f"{type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=writer, args=(i,), daemon=True) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert not [t for t in threads if t.is_alive()], "a writer never finished"
        assert not failures, failures
        assert sorted(results) == list(range(1, 9)), f"duplicate or missing sequences: {sorted(results)}"

    def test_the_unique_index_is_what_enforces_it(self, store):
        store.record(edit("e1"), kind=KIND, ref=REF)
        with pytest.raises(sqlite3.IntegrityError):
            store._conn.execute(
                "INSERT INTO artifact_edits (edit_id, artifact_kind, artifact_ref, seq) VALUES (?, ?, ?, ?)",
                ("different-id", KIND, REF, 1),
            )
