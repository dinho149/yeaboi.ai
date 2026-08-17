"""Tests for the ship board's live projection (ship/board.py).

The load-bearing tests here are the two that make a *public* board safe: the
agent-activity allowlist (raw tool inputs and command output must never reach a
watcher) and the scrub of the diff/validation/warnings on the way out.
"""

from __future__ import annotations

import json

from yeaboi.agent.state import ShipRun, ShipValidation
from yeaboi.ship.board import ShipBoard, _summarise_event
from yeaboi.ship.store import ShipStore


class TestActivityAllowlist:
    """`_summarise_event` is the server-side allowlist over stream-json events."""

    def test_assistant_text_is_kept_and_scrubbed(self):
        line = json.dumps(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "  editing the parser  "}]}}
        )
        entry = _summarise_event(line)
        assert entry == {"kind": "text", "text": "editing the parser"}

    def test_tool_use_keeps_the_name_but_never_the_input(self):
        # A Read/Edit input is a path + file contents. Only the name may show.
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/Users/secret/.env"}}]
                },
            }
        )
        entry = _summarise_event(line)
        assert entry == {"kind": "tool", "name": "Read"}
        assert "secret" not in json.dumps(entry)
        assert ".env" not in json.dumps(entry)

    def test_user_tool_result_events_are_dropped(self):
        # tool_result carries command output / file contents before any scrub.
        line = json.dumps(
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "SECRET OUTPUT"}]}}
        )
        assert _summarise_event(line) is None

    def test_result_envelope_is_not_activity(self):
        line = json.dumps({"type": "result", "result": "done", "total_cost_usd": 0.1})
        assert _summarise_event(line) is None

    def test_system_init_surfaces_only_the_model(self):
        assert _summarise_event(json.dumps({"type": "system", "model": "claude-x"})) == {
            "kind": "system",
            "text": "claude-x",
        }

    def test_malformed_json_is_dropped_not_raised(self):
        assert _summarise_event("{not json") is None
        assert _summarise_event("") is None
        assert _summarise_event("[1,2]") is None

    def test_text_snippet_is_bounded(self):
        long = "x" * 5000
        line = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": long}]}})
        entry = _summarise_event(line)
        assert entry is not None
        assert len(entry["text"]) <= 240


class TestSnapshotFromStore:
    def _write_run(self, tmp_path, run: ShipRun) -> str:
        db = tmp_path / "sessions.db"
        store = ShipStore(db)
        try:
            store.record_run(run)
        finally:
            store.close()
        return str(db)

    def test_snapshot_reflects_the_stored_run(self, tmp_path):
        db = self._write_run(
            tmp_path,
            ShipRun(run_id="r1", status="awaiting_approval", diff_stat=" 1 file changed", diff_text="x = 1\n"),
        )
        board = ShipBoard("r1", db_path=tmp_path / "sessions.db")
        _ = db  # db path resolved above; board opens its own connection
        board.revision()  # forces the cached read on the watcher-thread path
        snap = board.state_snapshot("watcher-pid")
        assert snap["status"] == "awaiting_approval"
        assert snap["diff_stat"] == " 1 file changed"
        assert "x = 1" in snap["diff_text"]

    def test_diff_and_tail_are_scrubbed(self, tmp_path):
        secret = "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFF"
        run = ShipRun(
            run_id="r2",
            status="awaiting_approval",
            diff_text=f"+ANTHROPIC_KEY = '{secret}'\n",
            validation=ShipValidation(configured=True, command="make test", passed=False, output_tail=secret),
            warnings=(f"leaked {secret}",),
        )
        self._write_run(tmp_path, run)
        board = ShipBoard("r2", db_path=tmp_path / "sessions.db")
        board.revision()
        snap = board.state_snapshot()
        assert secret not in snap["diff_text"]
        assert secret not in snap["validation"]["output_tail"]
        assert secret not in " ".join(snap["warnings"])

    def test_missing_run_is_starting_not_a_crash(self, tmp_path):
        board = ShipBoard("nope", db_path=tmp_path / "sessions.db")
        snap = board.state_snapshot()
        assert snap["status"] == "starting"
        assert snap["diff_text"] == ""


class TestLiveFeeds:
    def test_note_component_keys_by_id_and_bumps_revision(self, tmp_path):
        board = ShipBoard("r", db_path=tmp_path / "s.db")
        r0 = board.revision()
        board.note_component({"component_id": "ship-implement", "label": "Implementing", "status": "running"})
        board.note_component({"component_id": "ship-implement", "label": "Implementing", "status": "completed"})
        # Monotonic: the browser store's stale-drop guard depends on it.
        assert board.revision() > r0
        snap = board.state_snapshot()
        assert snap["revision"] == board.revision()
        # keyed by id: the second event replaced the first, not appended
        implement = [p for p in snap["phases"] if p["component_id"] == "ship-implement"]
        assert len(implement) == 1
        assert implement[0]["status"] == "completed"

    def test_note_agent_line_appends_only_safe_entries(self):
        board = ShipBoard("r", db_path=None)
        board.note_agent_line(
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}})
        )
        board.note_agent_line(json.dumps({"type": "user", "message": {"content": []}}))  # dropped
        board.note_agent_line("garbage")  # dropped
        snap = board.state_snapshot()
        assert [a["kind"] for a in snap["activity"]] == ["text"]

    def test_presence_ttl_expires(self, monkeypatch):
        import yeaboi.ship.board as board_mod

        clock = {"t": 1000.0}
        monkeypatch.setattr(board_mod.time, "monotonic", lambda: clock["t"])
        board = ShipBoard("r", db_path=None)
        board.heartbeat("p1", name="Ada")
        assert board.present_pids() == ("p1",)
        clock["t"] += board_mod._PRESENCE_TTL_S + 1
        assert board.present_pids() == ()
