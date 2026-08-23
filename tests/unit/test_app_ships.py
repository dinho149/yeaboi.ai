"""The ship supervisor: a run that outlives every window, and its gate.

The engine is driven for real (its own ``driver`` seam), so what is asserted is
the supervisor's contract — the run id arriving late, the gate being read by id
and never by "the newest row", and the board never sinking a run.
"""

from __future__ import annotations

import threading
import time

import pytest

from yeaboi.agent.state import ShipRun
from yeaboi.app.ships import ShipSupervisor


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: path)
    return path


def _settle(session, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session.finished:
            return
        time.sleep(0.01)
    raise AssertionError("the run never finished")


class _Engine:
    """Stands in for ``ship.engine.run_ship``, driving the callbacks by hand."""

    def __init__(
        self,
        *,
        run_id: str = "ship-1",
        block: threading.Event | None = None,
        before_id: threading.Event | None = None,
    ):
        self.run_id = run_id
        self.block = block
        self.before_id = before_id
        self.seen: dict = {}

    def __call__(self, story_id, repo, **kw):
        self.seen = dict(kw, story_id=story_id, repo=repo)
        if self.before_id is not None:
            self.before_id.wait(timeout=5)
        if kw.get("on_run_id") is not None:
            kw["on_run_id"](self.run_id)
        kw["on_progress"](
            {"kind": "analysis_component", "component_id": "setup", "label": "Worktree", "status": "completed"}
        )
        if self.block is not None:
            self.block.wait(timeout=5)
        return ShipRun(run_id=self.run_id, story_id=story_id, repo=repo, status="pr_open", pr_url="https://pr/1")


class _Board:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.share_url = ""
        self.display_code = "DUCK-42"
        self.started = False
        self.stopped = False
        self.components: list = []

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def note_component(self, item):
        self.components.append(item)

    def note_agent_line(self, line):
        pass


class TestRun:
    def test_a_finished_run_carries_its_phases_and_result(self, db, monkeypatch):
        monkeypatch.setattr("yeaboi.ship.engine.run_ship", _Engine())
        ships = ShipSupervisor(db_path=db)
        snapshot = ships.start(story_id="US-1", story_title="Search", repo="/r", session_id="s1", check_command="")
        _settle(ships.run(snapshot["key"]))
        latest = ships.snapshot(snapshot["key"])
        assert latest["finished"] is True
        assert [p["component_id"] for p in latest["phases"]] == ["setup"]
        assert latest["result"]["pr_url"] == "https://pr/1"

    def test_the_run_id_is_empty_until_the_engine_mints_it(self, db, monkeypatch):
        # Until it exists there is nothing to read a gate by, so a snapshot
        # taken in that window must offer neither an id nor a gate.
        mint = threading.Event()
        monkeypatch.setattr("yeaboi.ship.engine.run_ship", _Engine(before_id=mint))
        ships = ShipSupervisor(db_path=db)
        key = ships.start(story_id="US-1", story_title="", repo="/r", session_id="", check_command="")["key"]
        early = ships.snapshot(key)
        assert early["run_id"] == ""
        assert early["gate"] is None
        mint.set()
        _settle(ships.run(key))
        assert ships.snapshot(key)["run_id"] == "ship-1"

    def test_a_crash_becomes_a_failure_not_an_exception(self, db, monkeypatch):
        def _boom(story_id, repo, **kw):
            raise RuntimeError("the driver died")

        monkeypatch.setattr("yeaboi.ship.engine.run_ship", _boom)
        ships = ShipSupervisor(db_path=db)
        key = ships.start(story_id="US-1", story_title="", repo="/r", session_id="", check_command="")["key"]
        _settle(ships.run(key))
        latest = ships.snapshot(key)
        assert latest["result"] is None
        assert "stopped unexpectedly" in latest["failure"]

    def test_an_unknown_key_has_no_snapshot(self, db):
        assert ShipSupervisor(db_path=db).snapshot("nope") is None

    def test_every_launched_run_is_listed(self, db, monkeypatch):
        monkeypatch.setattr("yeaboi.ship.engine.run_ship", _Engine())
        ships = ShipSupervisor(db_path=db)
        for i in range(2):
            ships.start(story_id=f"US-{i}", story_title="", repo="/r", session_id="", check_command="")
        assert len(ships.runs()) == 2


class TestCancel:
    def test_cancel_sets_the_event_the_engine_watches(self, db, monkeypatch):
        gate = threading.Event()
        engine = _Engine(block=gate)
        monkeypatch.setattr("yeaboi.ship.engine.run_ship", engine)
        ships = ShipSupervisor(db_path=db)
        key = ships.start(story_id="US-1", story_title="", repo="/r", session_id="", check_command="")["key"]
        session = ships.run(key)
        session.stop()
        assert session.cancel.is_set()
        assert ships.snapshot(key)["cancelling"] is True
        gate.set()
        _settle(session)

    def test_cancel_is_idempotent(self, db, monkeypatch):
        monkeypatch.setattr("yeaboi.ship.engine.run_ship", _Engine())
        ships = ShipSupervisor(db_path=db)
        key = ships.start(story_id="US-1", story_title="", repo="/r", session_id="", check_command="")["key"]
        session = ships.run(key)
        session.stop()
        session.stop()
        assert session.cancel.is_set()

    def test_stop_all_cancels_live_runs_and_closes_boards(self, db, monkeypatch):
        gate = threading.Event()
        monkeypatch.setattr("yeaboi.ship.engine.run_ship", _Engine(block=gate))
        boards: list = []

        def _factory(run_id):
            board = _Board(run_id)
            boards.append(board)
            return board

        ships = ShipSupervisor(db_path=db, board_factory=_factory)
        key = ships.start(story_id="US-1", story_title="", repo="/r", session_id="", check_command="")["key"]
        session = ships.run(key)
        while session.board is None and not session.finished:
            time.sleep(0.01)
        ships.stop_all()
        assert session.cancel.is_set()
        assert boards[0].stopped is True
        gate.set()
        _settle(session)


class TestBoard:
    def test_the_board_starts_from_the_run_id_and_sees_the_phases(self, db, monkeypatch):
        monkeypatch.setattr("yeaboi.ship.engine.run_ship", _Engine())
        boards: list = []

        def _factory(run_id):
            board = _Board(run_id)
            boards.append(board)
            return board

        ships = ShipSupervisor(db_path=db, board_factory=_factory)
        key = ships.start(story_id="US-1", story_title="Search", repo="/r", session_id="", check_command="")["key"]
        _settle(ships.run(key))
        assert boards[0].run_id == "ship-1"
        assert boards[0].started is True
        assert [c["component_id"] for c in boards[0].components] == ["setup"]

    def test_a_board_that_will_not_start_does_not_sink_the_run(self, db, monkeypatch):
        monkeypatch.setattr("yeaboi.ship.engine.run_ship", _Engine())

        def _factory(run_id):
            raise RuntimeError("port in use")

        ships = ShipSupervisor(db_path=db, board_factory=_factory)
        key = ships.start(story_id="US-1", story_title="", repo="/r", session_id="", check_command="")["key"]
        _settle(ships.run(key))
        assert ships.snapshot(key)["result"] is not None

    def test_a_join_code_without_a_link_is_not_offered(self, db, monkeypatch):
        # A code is only meaningful once there is somewhere to type it.
        gate = threading.Event()
        monkeypatch.setattr("yeaboi.ship.engine.run_ship", _Engine(block=gate))
        ships = ShipSupervisor(db_path=db, board_factory=_Board)
        key = ships.start(story_id="US-1", story_title="", repo="/r", session_id="", check_command="")["key"]
        session = ships.run(key)
        while session.board is None and not session.finished:
            time.sleep(0.01)
        assert ships.snapshot(key)["board"] == {"url": "", "code": ""}
        gate.set()
        _settle(session)

    def test_the_board_is_torn_down_when_the_run_ends(self, db, monkeypatch):
        monkeypatch.setattr("yeaboi.ship.engine.run_ship", _Engine())
        boards: list = []
        ships = ShipSupervisor(db_path=db, board_factory=lambda rid: boards.append(_Board(rid)) or boards[-1])
        key = ships.start(story_id="US-1", story_title="", repo="/r", session_id="", check_command="")["key"]
        _settle(ships.run(key))
        assert boards[0].stopped is True


class TestGate:
    def _record(self, db, run_id: str, status: str) -> None:
        from yeaboi.ship.store import ShipStore

        with ShipStore(db) as store:
            store.record_run(ShipRun(run_id=run_id, story_id="US-1", repo="/r", status=status))

    def test_an_open_gate_appears_in_the_snapshot(self, db, monkeypatch):
        gate = threading.Event()
        monkeypatch.setattr("yeaboi.ship.engine.run_ship", _Engine(block=gate))
        ships = ShipSupervisor(db_path=db)
        key = ships.start(story_id="US-1", story_title="", repo="/r", session_id="", check_command="")["key"]
        session = ships.run(key)
        while not session.run_id and not session.finished:
            time.sleep(0.01)
        self._record(db, "ship-1", "awaiting_approval")
        assert ships.snapshot(key)["gate"]["run_id"] == "ship-1"
        gate.set()
        _settle(session)

    def test_another_runs_gate_is_never_this_runs(self, db, monkeypatch):
        # A surface that identified "my run" as "the newest awaiting row" would
        # ask a user to approve a diff they have never seen.
        gate = threading.Event()
        monkeypatch.setattr("yeaboi.ship.engine.run_ship", _Engine(run_id="mine", block=gate))
        ships = ShipSupervisor(db_path=db)
        key = ships.start(story_id="US-1", story_title="", repo="/r", session_id="", check_command="")["key"]
        session = ships.run(key)
        while not session.run_id and not session.finished:
            time.sleep(0.01)
        self._record(db, "someone-elses", "awaiting_approval")
        assert ships.snapshot(key)["gate"] is None
        gate.set()
        _settle(session)

    def test_resolving_hands_the_answer_to_the_store(self, db, monkeypatch):
        monkeypatch.setattr("yeaboi.ship.engine.run_ship", _Engine())
        ships = ShipSupervisor(db_path=db)
        key = ships.start(story_id="US-1", story_title="", repo="/r", session_id="", check_command="")["key"]
        _settle(ships.run(key))
        self._record(db, "ship-1", "awaiting_approval")
        assert ships.resolve_gate(key, "approved") is True

    def test_a_second_answer_is_refused_by_the_stores_swap(self, db, monkeypatch):
        monkeypatch.setattr("yeaboi.ship.engine.run_ship", _Engine())
        ships = ShipSupervisor(db_path=db)
        key = ships.start(story_id="US-1", story_title="", repo="/r", session_id="", check_command="")["key"]
        _settle(ships.run(key))
        self._record(db, "ship-1", "awaiting_approval")
        assert ships.resolve_gate(key, "approved") is True
        assert ships.resolve_gate(key, "rejected", "no") is False

    def test_a_run_with_no_id_yet_cannot_be_gated(self, db, monkeypatch):
        gate = threading.Event()
        monkeypatch.setattr("yeaboi.ship.engine.run_ship", _Engine(block=gate))
        ships = ShipSupervisor(db_path=db)
        key = ships.start(story_id="US-1", story_title="", repo="/r", session_id="", check_command="")["key"]
        session = ships.run(key)
        session.run_id = ""
        assert ships.resolve_gate(key, "approved") is False
        gate.set()
        _settle(session)

    def test_an_unknown_key_cannot_be_gated(self, db):
        assert ShipSupervisor(db_path=db).resolve_gate("nope", "approved") is False
