"""Tests for ship run persistence and the gate CAS (ship/store.py).

The gate protocol's whole value is that resolution happens exactly once, in
the database, whoever asks — so the race test uses two independent store
connections the way two real surfaces would.
"""

from __future__ import annotations

import threading

import pytest

from yeaboi.agent.state import ShipPhase, ShipRun, ShipValidation
from yeaboi.ship.store import ShipStore, _dict_to_run


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "sessions.db"


def _full_run(run_id="run-1", status="running"):
    return ShipRun(
        run_id=run_id,
        story_id="US-001",
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
