"""Unit tests for RetroStore persistence + serialization round-trips."""

from yeaboi.agent.state import RetroCard, RetroReport
from yeaboi.retro.store import RetroStore, _dict_to_retro_report, _retro_report_to_json


def _report(session_id="sess-1"):
    return RetroReport(
        date="2026-07-10",
        session_id=session_id,
        project_name="Demo",
        sprint_name="Sprint 5",
        cards=(
            RetroCard(
                id="a1",
                grid="went_well",
                text="ci",
                author="Sam",
                created_at="t",
                origin="web",
                reactions=(("👍", 3), ("🔥", 1)),
            ),
            RetroCard(id="a2", grid="action_items", text="fix", author="AI", created_at="t", origin="ai"),
        ),
        participants=("Sam",),
        generated_at="t2",
    )


class TestSerialization:
    def test_json_roundtrip(self):
        rep = _report()
        got = _dict_to_retro_report(__import__("json").loads(_retro_report_to_json(rep)))
        assert got == rep

    def test_missing_keys_deserialize_with_defaults(self):
        # An older/partial payload (no participants, card missing origin/reactions) still loads.
        d = {"session_id": "x", "cards": [{"grid": "demos", "text": "hi"}]}
        rep = _dict_to_retro_report(d)
        assert rep.session_id == "x"
        assert rep.cards[0].origin == "web" and rep.cards[0].author == ""
        assert rep.cards[0].reactions == ()
        assert rep.participants == ()

    def test_reactions_round_trip(self):
        rep = _report()
        got = _dict_to_retro_report(__import__("json").loads(_retro_report_to_json(rep)))
        assert dict(got.cards[0].reactions) == {"👍": 3, "🔥": 1}


class TestCarriedActionItems:
    def test_status_and_carried_round_trip(self):
        rep = RetroReport(
            session_id="s",
            cards=(RetroCard(id="c1", grid="action_items", text="fix ci", origin="ai"),),
            carried_action_items=(
                RetroCard(id="k1", grid="action_items", text="last one", origin="carryover", status="done"),
                RetroCard(id="k2", grid="action_items", text="still going", origin="carryover", status="carried_over"),
            ),
        )
        got = _dict_to_retro_report(__import__("json").loads(_retro_report_to_json(rep)))
        assert got == rep
        assert got.carried_action_items[0].status == "done"
        assert got.carried_action_items[1].status == "carried_over"

    def test_old_row_without_carried_or_status_deserializes(self):
        # A report serialized before the carry-forward feature (no carried_action_items,
        # cards missing `status`) must still load with defaults.
        d = {
            "session_id": "x",
            "cards": [{"grid": "action_items", "text": "hi", "origin": "ai"}],
        }
        rep = _dict_to_retro_report(d)
        assert rep.carried_action_items == ()
        assert rep.cards[0].status == ""


class TestStore:
    def test_record_and_get_latest(self, tmp_path):
        db = tmp_path / "sessions.db"
        with RetroStore(db) as store:
            rid = store.record_run(_report())
            assert rid >= 1
            got = store.get_latest_report("sess-1")
        assert got is not None and len(got.cards) == 2 and got.cards[0].text == "ci"

    def test_get_latest_none_when_empty(self, tmp_path):
        with RetroStore(tmp_path / "sessions.db") as store:
            assert store.get_latest_report("nope") is None

    def test_latest_wins(self, tmp_path):
        db = tmp_path / "sessions.db"
        with RetroStore(db) as store:
            store.record_run(_report())
            newer = RetroReport(session_id="sess-1", date="2026-07-11", cards=(RetroCard(text="newer"),))
            store.record_run(newer)
            got = store.get_latest_report("sess-1")
        assert got.date == "2026-07-11"

    def test_history(self, tmp_path):
        db = tmp_path / "sessions.db"
        with RetroStore(db) as store:
            store.record_run(_report())
            hist = store.get_history("sess-1")
        assert hist and hist[0]["card_count"] == 2 and hist[0]["project_name"] == "Demo"
        assert "id" in hist[0]  # saved-runs hub needs the row id


class TestSavedRunsHub:
    """get_all_history / get_run_by_id / delete_run — power the TUI saved-runs hub."""

    def test_get_all_history_carries_id_and_session(self, tmp_path):
        with RetroStore(tmp_path / "sessions.db") as store:
            store.record_run(_report())
            rows = store.get_all_history()
        assert rows and "id" in rows[0] and rows[0]["session_id"] == "sess-1"

    def test_get_run_by_id_round_trips_and_missing(self, tmp_path):
        with RetroStore(tmp_path / "sessions.db") as store:
            rid = store.record_run(_report())
            got = store.get_run_by_id(rid)
            assert got is not None and len(got.cards) == 2
            assert store.get_run_by_id(999) is None

    def test_get_run_by_id_corrupt_returns_none(self, tmp_path):
        with RetroStore(tmp_path / "sessions.db") as store:
            rid = store.record_run(_report())
            store._conn.execute("UPDATE retro_history SET report_json='{bad' WHERE id=?", (rid,))
            assert store.get_run_by_id(rid) is None

    def test_delete_run_removes_only_that_row(self, tmp_path):
        with RetroStore(tmp_path / "sessions.db") as store:
            keep = store.record_run(_report())
            drop = store.record_run(RetroReport(session_id="sess-2", date="2026-07-12", cards=(RetroCard(text="x"),)))
            assert store.delete_run(drop) is True
            assert store.delete_run(drop) is False
            assert {r["id"] for r in store.get_all_history()} == {keep}


class TestProvenanceSelfHeal:
    """A retro_history missing the v21 provenance columns heals on store open.

    The v21 schema-version collision could leave a shared DB stamped past 21
    without origin/edited_from_id, and the CLI and MCP tools open this store
    without ever constructing a SessionStore (whose v26 migration is the other
    repair path).
    """

    def test_pre_v21_table_heals_on_open(self, tmp_path):
        import sqlite3

        path = tmp_path / "sessions.db"
        RetroStore(path).close()
        conn = sqlite3.connect(str(path))
        keep = ", ".join(
            r[1] for r in conn.execute("PRAGMA table_info(retro_history)") if r[1] not in ("origin", "edited_from_id")
        )
        conn.executescript(
            f"CREATE TABLE pre AS SELECT {keep} FROM retro_history;"
            "DROP TABLE retro_history;"
            "ALTER TABLE pre RENAME TO retro_history;"
        )
        conn.close()
        with RetroStore(path) as store:
            cols = {r[1] for r in store._conn.execute("PRAGMA table_info(retro_history)")}
            assert {"origin", "edited_from_id"} <= cols
            assert store.record_run(_report()) > 0  # the INSERT that names origin
