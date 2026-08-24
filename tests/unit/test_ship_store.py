"""Tests for ship run persistence and the gate CAS (ship/store.py).

The gate protocol's whole value is that resolution happens exactly once, in
the database, whoever asks — so the race test uses two independent store
connections the way two real surfaces would.
"""

from __future__ import annotations

import json
import os
import threading

import pytest

from yeaboi.agent.state import ShipPhase, ShipRun, ShipValidation
from yeaboi.ship.store import ShipRunBusyError, ShipStore, _dict_to_run


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "sessions.db"


def _full_run(run_id="run-1", status="running"):
    return ShipRun(
        run_id=run_id,
        item_id="US-001",
        session_id="sess-1",
        agent_session_id="agent-sess",
        repo="/tmp/proj",
        branch=f"ship/{run_id}",
        worktree="/tmp/wt",
        base_sha="a" * 40,
        status=status,
        phases=(ShipPhase(name="setup", status="completed", detail="ok", duration_s=1.5),),
        validation=ShipValidation(configured=True, command="make test", passed=True, exit_code=0, output_tail="ok"),
        diff_stat="2 files changed",
        diff_text="@@ -1 +1 @@\n-old\n+new\n",
        cost_usd=0.42,
        transcript_findings=(("secret", "critical", "api key"),),
        transcript_path="/tmp/t.jsonl",
        warnings=("one warning",),
    )


class TestRoundTrip:
    def test_record_and_get_preserve_the_whole_artifact(self, db_path):
        with ShipStore(db_path) as store:
            recorded = store.record_run(_full_run())
            loaded = store.get_run("run-1")
        assert loaded == recorded
        assert loaded.phases[0].duration_s == 1.5
        assert loaded.validation.command == "make test"
        assert loaded.transcript_findings == (("secret", "critical", "api key"),)
        assert loaded.diff_text == "@@ -1 +1 @@\n-old\n+new\n"  # the patch survives history

    def test_list_runs_newest_first_with_limit(self, db_path):
        with ShipStore(db_path) as store:
            for n in range(5):
                store.record_run(ShipRun(run_id=f"run-{n}", status="failed", created_at=f"2026-08-1{n}T00:00:00"))
            runs = store.list_runs(limit=3)
        assert [r.run_id for r in runs] == ["run-4", "run-3", "run-2"]

    def test_dict_to_run_tolerates_missing_keys(self):
        run = _dict_to_run({"run_id": "x"})
        assert run.run_id == "x"
        assert run.status == "planned"
        assert run.validation == ShipValidation()


class TestSaveRun:
    def test_cas_write_lands_only_on_the_expected_status(self, db_path):
        with ShipStore(db_path) as store:
            store.record_run(_full_run(status="running"))
            moved = ShipRun(run_id="run-1", status="awaiting_approval")
            assert store.save_run(moved, expect_status="running")
            # A stale writer expecting the old status loses.
            assert not store.save_run(ShipRun(run_id="run-1", status="failed"), expect_status="running")
            assert store.get_run("run-1").status == "awaiting_approval"

    def test_unconditional_save_of_an_unrecorded_run_inserts_it(self, db_path):
        # A setup failure aborts before record_run; its terminal artifact must
        # still reach history — an UPDATE matching zero rows must not count
        # as "persisted".
        with ShipStore(db_path) as store:
            assert store.save_run(ShipRun(run_id="run-x", status="failed", warnings=("dirty repo",)))
            stored = store.get_run("run-x")
        assert stored is not None
        assert stored.status == "failed"
        assert stored.warnings == ("dirty repo",)

    def test_unknown_status_is_rejected_loudly(self, db_path):
        with ShipStore(db_path) as store:
            with pytest.raises(ValueError, match="unknown ship status"):
                store.save_run(ShipRun(run_id="run-1", status="banana"))


class TestGate:
    def _awaiting(self, store):
        store.record_run(_full_run(status="awaiting_approval"))

    def test_resolve_approves_once_and_writes_the_audit_event(self, db_path):
        with ShipStore(db_path) as store:
            self._awaiting(store)
            assert store.resolve_gate("run-1", "approved", "ship it")
            run = store.get_run("run-1")
            assert run.gate_resolution == "approved"
            assert run.gate_comment == "ship it"
            assert run.status == "awaiting_approval"  # resolve ≠ resume
            assert store.gate_events("run-1") == [("approved", "ship it", run.updated_at)]

    def test_second_resolution_loses_cleanly(self, db_path):
        with ShipStore(db_path) as store:
            self._awaiting(store)
            assert store.resolve_gate("run-1", "approved")
            assert not store.resolve_gate("run-1", "rejected", "too late")
            assert store.get_run("run-1").gate_resolution == "approved"

    def test_rejection_counts_attempts(self, db_path):
        with ShipStore(db_path) as store:
            self._awaiting(store)
            assert store.resolve_gate("run-1", "rejected", "wrong file")
            run = store.get_run("run-1")
            assert run.gate_resolution == "rejected"
            assert run.rejection_count == 1

    def test_gate_on_a_non_awaiting_run_refuses(self, db_path):
        with ShipStore(db_path) as store:
            store.record_run(_full_run(status="running"))
            assert not store.resolve_gate("run-1", "approved")
            assert not store.resolve_gate("run-404", "approved")

    def test_invalid_resolution_raises(self, db_path):
        with ShipStore(db_path) as store:
            self._awaiting(store)
            with pytest.raises(ValueError):
                store.resolve_gate("run-1", "maybe")

    def test_two_surfaces_racing_resolve_exactly_once(self, db_path):
        with ShipStore(db_path) as store:
            self._awaiting(store)
        results: list[bool] = []
        barrier = threading.Barrier(2)

        def _approver(resolution):
            with ShipStore(db_path) as surface:
                barrier.wait()
                results.append(surface.resolve_gate("run-1", resolution, f"from {resolution}"))

        threads = [threading.Thread(target=_approver, args=(r,)) for r in ("approved", "rejected")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) == [False, True]
        with ShipStore(db_path) as store:
            events = store.gate_events("run-1")
        assert len(events) == 1  # the loser wrote no audit row either


class TestOwnerPid:
    def test_record_run_stamps_the_owning_process(self, tmp_path):
        with ShipStore(tmp_path / "s.db") as store:
            stored = store.record_run(ShipRun(run_id="r1", item_id="US-1"))
        assert stored.owner_pid == os.getpid()

    def test_an_explicit_owner_is_not_overwritten(self, tmp_path):
        with ShipStore(tmp_path / "s.db") as store:
            stored = store.record_run(ShipRun(run_id="r1", item_id="US-1", owner_pid=4242))
        assert stored.owner_pid == 4242

    def test_owner_pid_survives_a_round_trip(self, tmp_path):
        db = tmp_path / "s.db"
        with ShipStore(db) as store:
            store.record_run(ShipRun(run_id="r1", item_id="US-1", owner_pid=4242))
        with ShipStore(db) as store:
            assert store.get_run("r1").owner_pid == 4242

    def test_a_legacy_row_without_the_field_reads_as_unowned(self, tmp_path):
        """Rows written before owner_pid existed must still load."""
        db = tmp_path / "s.db"
        with ShipStore(db) as store:
            store.record_run(ShipRun(run_id="r1", item_id="US-1"))
            store._conn.execute(
                "UPDATE ship_runs SET run_json = ? WHERE run_id = ?",
                (json.dumps({"run_id": "r1", "story_id": "US-1", "status": "planned"}), "r1"),
            )
        with ShipStore(db) as store:
            assert store.get_run("r1").owner_pid == 0


class TestDeleteRun:
    def test_delete_removes_the_row_and_its_gate_trail(self, tmp_path):
        db = tmp_path / "s.db"
        with ShipStore(db) as store:
            store.record_run(ShipRun(run_id="r1", item_id="US-1", status="awaiting_approval"))
            store.resolve_gate("r1", "approved", "ship it")
            assert store.gate_events("r1")
            assert store.delete_run("r1") is True
            assert store.get_run("r1") is None
            assert store.gate_events("r1") == []

    def test_deleting_an_unknown_run_reports_that_nothing_went(self, tmp_path):
        with ShipStore(tmp_path / "s.db") as store:
            assert store.delete_run("nope") is False

    def test_delete_leaves_other_runs_alone(self, tmp_path):
        db = tmp_path / "s.db"
        with ShipStore(db) as store:
            store.record_run(ShipRun(run_id="r1", item_id="US-1", status="awaiting_approval"))
            store.record_run(ShipRun(run_id="r2", item_id="US-2", status="awaiting_approval"))
            store.resolve_gate("r2", "rejected", "no")
            store.delete_run("r1")
            assert store.get_run("r2") is not None
            assert [e[0] for e in store.gate_events("r2")] == ["rejected"]

    def test_a_live_owner_at_the_gate_refuses_the_delete(self, tmp_path, monkeypatch):
        """The owning process polls its own row and would wait on a deleted one forever."""
        from yeaboi.ship import budget, worktree

        removed = []
        monkeypatch.setattr(worktree, "remove", lambda run_id, **kw: removed.append(run_id))
        monkeypatch.setattr(budget, "process_alive", lambda pid: True)
        with ShipStore(tmp_path / "s.db") as store:
            store.record_run(
                ShipRun(run_id="r1", item_id="US-1", status="awaiting_approval", owner_pid=os.getpid() + 1)
            )
            with pytest.raises(ShipRunBusyError):
                store.delete_run("r1")
            assert store.get_run("r1") is not None
            assert removed == []  # the checkout it is driving is still there

    def test_a_dead_owner_does_not_block_the_delete(self, tmp_path, monkeypatch):
        from yeaboi.ship import budget, worktree

        monkeypatch.setattr(worktree, "remove", lambda run_id, **kw: None)
        monkeypatch.setattr(budget, "process_alive", lambda pid: False)
        with ShipStore(tmp_path / "s.db") as store:
            store.record_run(
                ShipRun(run_id="r1", item_id="US-1", status="awaiting_approval", owner_pid=os.getpid() + 1)
            )
            assert store.delete_run("r1") is True

    def test_our_own_run_is_never_busy(self, tmp_path, monkeypatch):
        """Deleting from the hub that owns the run is the ordinary case."""
        from yeaboi.ship import budget, worktree

        monkeypatch.setattr(worktree, "remove", lambda run_id, **kw: None)
        monkeypatch.setattr(budget, "process_alive", lambda pid: True)
        with ShipStore(tmp_path / "s.db") as store:
            store.record_run(ShipRun(run_id="r1", item_id="US-1", status="awaiting_approval", owner_pid=os.getpid()))
            assert store.delete_run("r1") is True

    def test_a_finished_run_is_deletable_whoever_owns_it(self, tmp_path, monkeypatch):
        """Only a run parked at the gate has anyone waiting on it."""
        from yeaboi.ship import budget, worktree

        monkeypatch.setattr(worktree, "remove", lambda run_id, **kw: None)
        monkeypatch.setattr(budget, "process_alive", lambda pid: True)
        with ShipStore(tmp_path / "s.db") as store:
            store.record_run(ShipRun(run_id="r1", item_id="US-1", status="approved", owner_pid=os.getpid() + 1))
            assert store.delete_run("r1") is True

    def test_a_stuck_worktree_does_not_fail_the_delete(self, tmp_path, monkeypatch):
        from yeaboi.ship import worktree

        def _boom(run_id, **kw):
            raise worktree.WorktreeError("worktree is locked")

        monkeypatch.setattr(worktree, "remove", _boom)
        with ShipStore(tmp_path / "s.db") as store:
            store.record_run(ShipRun(run_id="r1", item_id="US-1"))
            assert store.delete_run("r1") is True
            assert store.get_run("r1") is None


class TestLegacyItemId:
    def test_a_row_written_as_story_id_still_loads(self, tmp_path):
        """Runs recorded before ship could target an epic or a task."""
        db = tmp_path / "s.db"
        with ShipStore(db) as store:
            store.record_run(ShipRun(run_id="r1", item_id="US-1"))
            store._conn.execute(
                "UPDATE ship_runs SET run_json = ? WHERE run_id = ?",
                (json.dumps({"run_id": "r1", "story_id": "US-9", "status": "planned"}), "r1"),
            )
        with ShipStore(db) as store:
            run = store.get_run("r1")
        assert run.item_id == "US-9"
        assert run.level == "story"  # the only level that existed then

    def test_item_id_wins_when_both_keys_are_present(self, tmp_path):
        db = tmp_path / "s.db"
        with ShipStore(db) as store:
            store.record_run(ShipRun(run_id="r1", item_id="F1", level="epic"))
            store._conn.execute(
                "UPDATE ship_runs SET run_json = ? WHERE run_id = ?",
                (json.dumps({"run_id": "r1", "item_id": "F1", "story_id": "US-1", "level": "epic"}), "r1"),
            )
        with ShipStore(db) as store:
            assert store.get_run("r1").item_id == "F1"

    def test_a_listing_still_emits_the_legacy_key(self, tmp_path):
        # ship_history's payload and the plugin skill both document story_id.
        from yeaboi.ship.store import listing_dict

        payload = listing_dict(ShipRun(run_id="r1", item_id="F1", level="epic"))
        assert payload["item_id"] == "F1"
        assert payload["story_id"] == "F1"
        assert payload["level"] == "epic"

    def test_the_level_and_batch_fields_survive_a_round_trip(self, tmp_path):
        db = tmp_path / "s.db"
        run = ShipRun(
            run_id="r1", item_id="US-1", level="story", batch_id="b1", batch_item_id="F1", batch_index=2, batch_total=5
        )
        with ShipStore(db) as store:
            store.record_run(run)
        with ShipStore(db) as store:
            back = store.get_run("r1")
        assert (back.level, back.batch_id, back.batch_item_id, back.batch_index, back.batch_total) == (
            "story",
            "b1",
            "F1",
            2,
            5,
        )


def _member(run_id, story_id, index, *, batch="b1", epic="F1", status="approved", repo="/tmp/proj"):
    return ShipRun(
        run_id=run_id,
        item_id=story_id,
        level="story",
        repo=repo,
        status=status,
        batch_id=batch,
        batch_item_id=epic,
        batch_index=index,
        batch_total=3,
        created_at=f"2026-08-2{index}T10:00:00",
    )


class TestBatchReads:
    def test_members_come_back_in_batch_order(self, tmp_path):
        with ShipStore(tmp_path / "s.db") as store:
            for index, sid in enumerate(("US-1", "US-2", "US-3"), start=1):
                store.record_run(_member(f"r{index}", sid, index))
            assert [m.item_id for m in store.batch_runs("b1")] == ["US-1", "US-2", "US-3"]

    def test_another_batchs_runs_are_not_included(self, tmp_path):
        with ShipStore(tmp_path / "s.db") as store:
            store.record_run(_member("r1", "US-1", 1))
            store.record_run(_member("r2", "US-9", 1, batch="b2", epic="F2"))
            assert [m.run_id for m in store.batch_runs("b1")] == ["r1"]

    def test_an_unfinished_batch_is_found_by_its_epic(self, tmp_path):
        # A member's own item_id is its story, so the epic has to travel too or
        # relaunching would open a second batch over the same stories.
        with ShipStore(tmp_path / "s.db") as store:
            store.record_run(_member("r1", "US-1", 1))
            store.record_run(_member("r2", "US-2", 2, status="rejected"))
            found, members = store.open_batch("F1", "/tmp/proj", ("US-1", "US-2", "US-3"))
            assert found == "b1"
            assert [m.run_id for m in members] == ["r1", "r2"]  # oldest first

    def test_a_batch_with_members_still_unstarted_is_unfinished(self, tmp_path):
        with ShipStore(tmp_path / "s.db") as store:
            store.record_run(_member("r1", "US-1", 1))  # batch_total is 3
            assert store.open_batch("F1", "/tmp/proj", ("US-1", "US-2", "US-3"))[0] == "b1"

    def test_a_fully_approved_batch_is_finished(self, tmp_path):
        with ShipStore(tmp_path / "s.db") as store:
            for index, sid in enumerate(("US-1", "US-2", "US-3"), start=1):
                store.record_run(_member(f"r{index}", sid, index))
            assert store.open_batch("F1", "/tmp/proj", ("US-1", "US-2", "US-3")) == ("", [])

    def test_a_batch_whose_epic_grew_a_story_is_unfinished_again(self, tmp_path):
        # Done is measured against the stories the epic has NOW, so a plan that
        # gained one continues the batch instead of re-shipping every story.
        with ShipStore(tmp_path / "s.db") as store:
            for index, sid in enumerate(("US-1", "US-2", "US-3"), start=1):
                store.record_run(_member(f"r{index}", sid, index))
            assert store.open_batch("F1", "/tmp/proj", ("US-1", "US-2", "US-3", "US-4"))[0] == "b1"

    def test_a_rejected_member_does_not_wedge_the_batch_open_forever(self, tmp_path):
        # The rejected story is retried on the relaunch, and once it is approved
        # the batch closes — counting members would have kept it open.
        with ShipStore(tmp_path / "s.db") as store:
            store.record_run(_member("r1", "US-1", 1))
            store.record_run(_member("r2", "US-2", 2, status="rejected"))
            store.record_run(_member("r3", "US-3", 3))
            assert store.open_batch("F1", "/tmp/proj", ("US-1", "US-2", "US-3"))[0] == "b1"
            store.record_run(_member("r4", "US-2", 2))  # the retry lands approved
            assert store.open_batch("F1", "/tmp/proj", ("US-1", "US-2", "US-3")) == ("", [])

    def test_a_batch_in_another_repo_is_not_adopted(self, tmp_path):
        with ShipStore(tmp_path / "s.db") as store:
            store.record_run(_member("r1", "US-1", 1, status="rejected"))
            assert store.open_batch("F1", "/somewhere/else", ("US-1",)) == ("", [])

    def test_no_batch_at_all_is_the_empty_string(self, tmp_path):
        with ShipStore(tmp_path / "s.db") as store:
            assert store.open_batch("F1", "/tmp/proj", ("US-1",)) == ("", [])
            assert store.open_batch("", "/tmp/proj", ("US-1",)) == ("", [])
            assert store.batch_runs("") == []
