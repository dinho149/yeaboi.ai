"""The board and share lifecycles (app/supervisor.py).

No sockets and no cloudflared: the servers are fakes and the secure link is
driven by an injected tunnel factory, which is what lets the whole lifecycle —
open, snapshot, flush, close — be asserted in-process.
"""

from __future__ import annotations

import json

import pytest

from yeaboi.app.supervisor import BoardSession, BoardSupervisor, ShareSession
from yeaboi.sharing.link import SecureLink


class FakeServer:
    def __init__(self, port: int = 5173) -> None:
        self.port = port
        self.url = f"http://127.0.0.1:{port}/?token=secret&admin=alsosecret"
        self.share_url = ""
        self.display_code = "DUCK-42"
        self.stopped = False

    def set_public_url(self, url: str) -> None:
        self.share_url = url

    def set_access_gate(self, gate) -> None:
        pass

    def stop(self) -> None:
        self.stopped = True


class FakeRetroBoard:
    def cards_by_grid(self):
        return {"went_well": [], "to_improve": []}

    def carried_snapshot(self):
        return []


class FakePokerBoard:
    def state_snapshot(self):
        return {"tickets": [], "revealed": False}


def _board(kind="retro", *, board_id="b1", server=None) -> BoardSession:
    server = server or FakeServer()
    return BoardSession(
        board_id=board_id,
        kind=kind,
        title="Apollo",
        session_id="s1",
        board=FakeRetroBoard() if kind == "retro" else FakePokerBoard(),
        server=server,
        link=SecureLink(server, surface=kind),
        started_at="2026-08-23T10:00:00+00:00",
    )


class TestBoardSnapshot:
    def test_carries_the_code(self):
        assert _board().snapshot()["display_code"] == "DUCK-42"

    def test_the_host_link_is_never_in_a_snapshot(self):
        # It carries the admin secret, and a snapshot is drawn by anything
        # that lists boards. Only the host_url property serves it.
        board = _board()
        assert "host_url" not in board.snapshot()
        assert "admin=" not in json.dumps(board.snapshot())
        assert "admin=" in board.host_url

    def test_retro_state_is_its_cards(self):
        assert set(_board().snapshot()["state"]) == {"grids", "carried"}

    def test_poker_state_is_its_table(self):
        assert _board("poker").snapshot()["state"] == {"tickets": [], "revealed": False}

    def test_link_state_starts_idle(self):
        assert _board().snapshot()["link"]["state"] == "idle"


class TestBoardStop:
    def test_flushes_then_tears_down(self, monkeypatch, tmp_path):
        session = _board()
        monkeypatch.setattr(type(session), "_flush", lambda _self, _db: 12)
        assert session.stop(db_path=tmp_path / "db") == 12
        assert session.server.stopped

    def test_a_second_stop_does_not_flush_again(self, monkeypatch, tmp_path):
        session = _board()
        calls: list[int] = []
        monkeypatch.setattr(type(session), "_flush", lambda _self, _db: calls.append(1) or 12)
        session.stop(db_path=tmp_path / "db")
        assert session.stop(db_path=tmp_path / "db") == 0
        assert calls == [1]

    def test_a_failed_flush_still_closes_the_server(self, monkeypatch, tmp_path):
        session = _board()

        def boom(_self, _db):
            raise RuntimeError("disk full")

        monkeypatch.setattr(type(session), "_flush", boom)
        assert session.stop(db_path=tmp_path / "db") == 0
        assert session.server.stopped


class TestSupervisorBoards:
    def test_starts_empty(self):
        assert BoardSupervisor().boards() == []

    def test_stopping_an_unknown_board_reports_it(self):
        assert BoardSupervisor().stop_board("nope") is None

    def test_stop_forgets_the_board(self, monkeypatch, tmp_path):
        supervisor = BoardSupervisor(db_path=tmp_path / "db")
        session = _board()
        monkeypatch.setattr(type(session), "_flush", lambda _self, _db: 3)
        supervisor._boards["b1"] = session
        assert supervisor.stop_board("b1") == 3
        assert supervisor.board("b1") is None

    def test_start_retro_refuses_without_a_session(self, monkeypatch, tmp_path):
        import yeaboi.retro.setup as retro_setup
        from yeaboi.retro.setup import RetroTarget

        monkeypatch.setattr(retro_setup, "resolve_session", lambda **_k: RetroTarget())
        with pytest.raises(ValueError, match="no project session"):
            BoardSupervisor(db_path=tmp_path / "db").start_retro()

    def test_start_poker_refuses_an_empty_table(self, tmp_path):
        with pytest.raises(ValueError, match="no tickets"):
            BoardSupervisor(db_path=tmp_path / "db").start_poker(source="demo", scope_label="Demo", tickets=[])


class FakeDocument:
    def __init__(self, edits=()) -> None:
        self._edits = list(edits)

    def edits(self):
        return tuple(self._edits)

    def editors(self):
        return ("Ada",) if self._edits else ()

    def add(self, edit) -> None:
        self._edits.append(edit)


class FakeEditing:
    def __init__(self, edits=()) -> None:
        self.share = type("S", (), {"document": FakeDocument(edits)})()
        self.committed = 0
        self.closed = False

    def commit(self) -> int:
        self.committed += 1
        return 99

    def close(self) -> None:
        self.closed = True


def _share(editing=None, *, share_id="s1") -> ShareSession:
    server = FakeServer(port=5473)
    return ShareSession(
        share_id=share_id,
        kind="standup",
        title="Daily Standup",
        session_id="sess",
        run_id=4,
        server=server,
        link=SecureLink(server, surface="share"),
        started_at="2026-08-23T10:00:00+00:00",
        editing=editing,
        baseline_edits=len(editing.share.document.edits()) if editing is not None else 0,
    )


class TestShareEdits:
    def test_a_read_only_share_counts_nothing(self):
        assert _share().edits == 0

    def test_the_count_is_the_delta_not_the_total(self):
        """A reopened share replays its whole log before anyone joins, so the
        total is non-zero the moment it opens — reporting it would append a
        duplicate corrected row once per open-and-close cycle."""
        editing = FakeEditing(edits=["old-1", "old-2"])
        session = _share(editing)
        assert session.edits == 0
        editing.share.document.add("new-1")
        assert session.edits == 1

    def test_snapshot_names_the_editors(self):
        editing = FakeEditing(edits=["e"])
        assert _share(editing).snapshot()["editors"] == ["Ada"]

    def test_snapshot_says_read_only(self):
        assert _share().snapshot()["editable"] is False


class TestShareStop:
    def test_commit_is_never_something_teardown_decides(self):
        editing = FakeEditing()
        session = _share(editing)
        editing.share.document.add("new-1")
        assert session.stop(commit=False) == 0
        assert editing.committed == 0
        assert editing.closed

    def test_commit_with_edits_appends_a_corrected_row(self):
        editing = FakeEditing()
        session = _share(editing)
        editing.share.document.add("new-1")
        assert session.stop(commit=True) == 99
        assert editing.closed

    def test_commit_with_nothing_recorded_writes_nothing(self):
        editing = FakeEditing()
        assert _share(editing).stop(commit=True) == 0
        assert editing.committed == 0

    def test_the_lease_is_released_even_when_the_commit_raises(self):
        editing = FakeEditing()
        session = _share(editing)
        editing.share.document.add("new-1")

        def boom() -> int:
            raise RuntimeError("store is locked")

        editing.commit = boom
        with pytest.raises(RuntimeError):
            session.stop(commit=True)
        assert editing.closed

    def test_a_second_stop_is_a_no_op(self):
        editing = FakeEditing()
        session = _share(editing)
        session.stop(commit=False)
        assert session.stop(commit=True) == 0


class TestStopAll:
    def test_closes_boards_and_shares_and_never_commits(self, monkeypatch, tmp_path):
        supervisor = BoardSupervisor(db_path=tmp_path / "db")
        board = _board()
        monkeypatch.setattr(type(board), "_flush", lambda _self, _db: 1)
        editing = FakeEditing()
        share = _share(editing)
        editing.share.document.add("new-1")
        supervisor._boards["b1"] = board
        supervisor._shares["s1"] = share
        supervisor.stop_all()
        assert board.server.stopped and share.server.stopped
        # Nobody decided to keep them, so they are not kept.
        assert editing.committed == 0
        assert supervisor.boards() == [] and supervisor.shares() == []

    def test_one_bad_board_does_not_strand_the_others(self, monkeypatch, tmp_path):
        supervisor = BoardSupervisor(db_path=tmp_path / "db")
        good = _board(board_id="good")
        monkeypatch.setattr(type(good), "_flush", lambda _self, _db: 1)
        bad = _board(board_id="bad")
        bad.stop = lambda **_kw: (_ for _ in ()).throw(RuntimeError("nope"))
        supervisor._boards.update({"bad": bad, "good": good})
        supervisor.stop_all()
        assert good.server.stopped

    def test_stop_all_on_an_empty_supervisor(self):
        BoardSupervisor().stop_all()
