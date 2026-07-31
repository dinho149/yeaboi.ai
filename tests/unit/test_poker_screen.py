"""Render tests for _build_poker_screen (all views, width/height sweep)."""

import io

import pytest
from rich.console import Console
from rich.panel import Panel

from yeaboi.poker.board import PokerBoard, board_to_report
from yeaboi.poker.tickets import demo_tickets
from yeaboi.ui.mode_select.screens._screens_secondary import _build_poker_screen


def _render(panel: Panel, width: int = 100) -> str:
    buf = io.StringIO()
    Console(file=buf, width=width, force_terminal=False).print(panel)
    return buf.getvalue()


def _board(vote: bool = True) -> PokerBoard:
    b = PokerBoard("sess", "Proj", source="demo", scope_label="Backlog", tickets=demo_tickets())
    b.heartbeat("p1", name="Alex", avatar="🦊")
    b.heartbeat("p2", name="Sam", avatar="🐙")
    if vote:
        b.cast_vote("p1", "5")
    return b


def _live_data(board: PokerBoard) -> dict:
    return {
        "session_name": "Proj",
        "display_code": "AAAA-BBBB",
        "host_url": "http://127.0.0.1:5273/?token=t&admin=a",
        "public_url": "",
        "message": "Server ready",
        "state": board.state_snapshot(),
        "actions": ["Copy Invite", "Copy Host Link", "Export", "Close"],
    }


class TestLiveView:
    def test_shows_join_info_and_ticket(self):
        out = _render(_build_poker_screen(_live_data(_board()), width=100, height=42))
        assert "AAAA-BBBB" in out
        assert "DEMO-1" in out
        assert "voting" in out

    def test_voted_ticks_but_no_values_pre_reveal(self):
        out = _render(_build_poker_screen(_live_data(_board()), width=100, height=40))
        assert "Alex ✓" in out
        assert "Sam …" in out
        assert "→" not in out  # values only appear post-reveal

    def test_reveal_shows_values_and_suggestion(self):
        b = _board()
        b.cast_vote("p2", "8")
        b.reveal()
        out = _render(_build_poker_screen(_live_data(b), width=110, height=40))
        assert "Alex → 5" in out
        assert "Sam → 8" in out
        assert "Suggested" in out

    def test_acceptance_criteria_shown(self):
        # DEMO-1 ships with ACs, so the live view renders the section.
        out = _render(_build_poker_screen(_live_data(_board()), width=110, height=44))
        assert "Acceptance criteria" in out
        assert "AC1: Sign-in works" in out

    def test_no_acceptance_line_when_absent(self):
        b = _board()
        b.goto_ticket(1)  # DEMO-2 has no ACs
        out = _render(_build_poker_screen(_live_data(b), width=110, height=44))
        assert "Acceptance criteria" not in out

    def test_notice_shown(self):
        b = _board()
        b.set_notice("Jira write failed")
        out = _render(_build_poker_screen(_live_data(b), width=100, height=36))
        assert "Jira write failed" in out

    def test_join_block_waits_for_the_tunnel_rather_than_showing_a_local_url(self):
        # Mirrors the retro board: the server binds loopback, so until the tunnel
        # is up there is no address a teammate could open.
        out = _render(_build_poker_screen(_live_data(_board()), width=100, height=42))
        assert "Same Wi-Fi only" not in out
        # The participant row itself must carry no address — the loopback host
        # link below it is the host's own and is labelled as such.
        participant = next(line for line in out.splitlines() if "Participant link" in line)
        assert "preparing" in participant
        assert "http" not in participant

    def test_join_block_shows_the_tunnel_link_once_there_is_one(self):
        data = _live_data(_board())
        data["public_url"] = "https://calm-tree-1234.trycloudflare.com/"
        out = _render(_build_poker_screen(data, width=100, height=42))
        assert "calm-tree-1234" in out
        assert "Works anywhere" in out
        assert "preparing" not in out

    def test_join_block_stops_promising_a_link_after_a_failure(self):
        data = _live_data(_board())
        data["link_failed"] = True
        out = _render(_build_poker_screen(data, width=100, height=42))
        assert "unavailable" in out
        assert "Retry Link" in out
        assert "a few seconds" not in out

    def test_ai_note_shown(self):
        # Taller than the other live-view tests: the AI note sits below the
        # ticket body, so it is the first thing off the bottom of a short
        # viewport. This asserts the note renders, not where it lands.
        b = _board()
        b.set_ai_note("Talk through the 13", 8.0)
        out = _render(_build_poker_screen(_live_data(b), width=100, height=48))
        assert "Talk through the 13" in out

    def test_ai_confidence_and_evidence_shown(self):
        b = _board()
        b.set_ai_note(
            "Grounded take",
            5.0,
            confidence="high",
            evidence=("5-pt stories avg 4.2 days here", "PROJ-87 shipped as a 5"),
        )
        out = _render(_build_poker_screen(_live_data(b), width=110, height=52))
        assert "AI confidence: high" in out
        assert "5-pt stories avg 4.2 days here" in out
        assert "PROJ-87 shipped as a 5" in out

    @pytest.mark.parametrize("width,height", [(60, 18), (80, 24), (120, 40)])
    def test_size_sweep(self, width, height):
        panel = _build_poker_screen(_live_data(_board()), width=width, height=height)
        assert _render(panel, width=width)


def _duel_board() -> PokerBoard:
    b = _board()
    b.cast_vote("p2", "13")
    b.reveal()
    b.open_duel(90)
    return b


class TestDuelView:
    def test_live_duel_lines(self):
        b = _duel_board()
        b.set_duel_recording("host", True)
        b.set_duel_recording("low", True)
        out = _render(_build_poker_screen(_live_data(b), width=110, height=44))
        assert "duel — the floor is open" in out
        assert "Alex (5) vs Sam (13)" in out
        assert "Alex has the floor" in out
        assert "RECORDING" in out
        assert "host mic on" in out
        # Values stay visible during the duel (post-reveal).
        assert "Alex → 5" in out

    def test_live_duel_without_recording_source(self):
        out = _render(_build_poker_screen(_live_data(_duel_board()), width=110, height=44))
        assert "Not recording — no mic source available." in out

    def test_transcribing(self):
        b = _duel_board()
        b.close_duel()
        out = _render(_build_poker_screen(_live_data(b), width=110, height=42))
        assert "transcribing the debate…" in out

    def test_done_shows_excerpt(self):
        b = _duel_board()
        b.close_duel()
        b.set_duel_transcript("Alex said the endpoint already exists; Sam pointed at the migration.")
        out = _render(_build_poker_screen(_live_data(b), width=110, height=44))
        assert "Duel transcript captured" in out
        assert "Alex said the endpoint already exists" in out

    def test_failed_shows_error(self):
        b = _duel_board()
        b.close_duel()
        b.set_duel_transcript("", error="Transcription produced nothing")
        out = _render(_build_poker_screen(_live_data(b), width=110, height=42))
        assert "Transcription produced nothing" in out


class TestPickView:
    def test_options_and_selection_marker(self):
        data = {
            "pick": {
                "title": "Which sprint?",
                "hint": "Active preselected",
                "options": [("Sprint 41", "closed"), ("Sprint 42", "active")],
                "sel": 1,
            },
            "actions": ["Select", "Back"],
        }
        out = _render(_build_poker_screen(data, width=90, height=24))
        assert "Which sprint?" in out
        assert "► Sprint 42" in out
        assert "Sprint 41" in out

    @pytest.mark.parametrize("width,height", [(60, 16), (100, 30)])
    def test_size_sweep(self, width, height):
        data = {"pick": {"title": "T", "hint": "", "options": [("A", ""), ("B", "sub")], "sel": 0}}
        assert _render(_build_poker_screen(data, width=width, height=height), width=width)


class TestSnapshotView:
    def test_report_replay(self):
        b = _board()
        b.cast_vote("p2", "8")
        b.reveal()
        b.finalize_current(8)
        report = board_to_report(b)
        data = {"report": report, "snapshot": True, "actions": ["Export", "Close"], "session_name": "Proj"}
        out = _render(_build_poker_screen(data, width=110, height=44))
        assert "1/6 estimated" in out
        assert "→ 8 points" in out
        assert "not estimated" in out
        assert "Alex 5" in out

    def test_duel_transcript_shown(self):
        b = _duel_board()
        b.close_duel()
        b.set_duel_transcript("the debate happened")
        b.finalize_current(8)
        report = board_to_report(b)
        data = {"report": report, "snapshot": True, "actions": ["Export", "Close"], "session_name": "Proj"}
        out = _render(_build_poker_screen(data, width=110, height=44))
        assert "⚔ Duel: Alex (5) vs Sam (13)" in out
        assert "the debate happened" in out

    def test_empty_report(self):
        from yeaboi.agent.state import PokerReport

        data = {"report": PokerReport(), "snapshot": True}
        assert _render(_build_poker_screen(data, width=80, height=24))
