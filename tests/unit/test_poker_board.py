"""Tests for the live PokerBoard state machine (poker/board.py)."""

from yeaboi.poker.board import (
    PHASE_REVEALED,
    PHASE_VOTING,
    POKER_DECK,
    PokerBoard,
    board_to_report,
    median_of,
    snap_to_deck,
)


def _board(n_tickets: int = 3) -> PokerBoard:
    tickets = [
        {
            "source": "demo",
            "key": f"T-{i}",
            "summary": f"Ticket {i}",
            "description": f"Desc {i}",
            "description_text": f"Desc {i}",
            "story_points": 5.0 if i == 1 else None,
            "state": "To Do",
            "assignee": "",
            "url": "",
        }
        for i in range(n_tickets)
    ]
    return PokerBoard("sess-1", "Proj", source="demo", scope_label="Backlog", tickets=tickets)


class TestDeckHelpers:
    def test_snap_to_deck_exact(self):
        assert snap_to_deck(5.0) == 5.0

    def test_snap_to_deck_nearest(self):
        assert snap_to_deck(6.0) == 5.0
        assert snap_to_deck(11.0) == 13.0

    def test_snap_ties_round_up(self):
        # 6.5 is equidistant between 5 and 8 — planning poker rounds up.
        assert snap_to_deck(6.5) == 8.0
        assert snap_to_deck(4.0) == 5.0

    def test_median(self):
        assert median_of([]) is None
        assert median_of([3.0]) == 3.0
        assert median_of([1.0, 8.0, 3.0]) == 3.0
        assert median_of([2.0, 8.0]) == 5.0


class TestVoting:
    def test_cast_and_snapshot_masking(self):
        b = _board()
        b.heartbeat("p1", name="Alex", avatar="🦊")
        b.heartbeat("p2", name="Sam", avatar="🐙")
        assert b.cast_vote("p1", "5")
        snap = b.state_snapshot("p2")
        # Pre-reveal: only voted flags, never values; viewer sees their own value only.
        assert snap["phase"] == PHASE_VOTING
        voted = {v["name"]: v["voted"] for v in snap["votes"]}
        assert voted == {"Alex": True, "Sam": False}
        assert all("value" not in v for v in snap["votes"])
        assert snap["mine_value"] == ""
        assert snap["distribution"] == {}
        assert b.state_snapshot("p1")["mine_value"] == "5"

    def test_invalid_votes_rejected(self):
        b = _board()
        assert not b.cast_vote("p1", "4")  # not a deck card
        assert not b.cast_vote("", "5")  # no pid
        assert b.cast_vote("p1", "☕")  # coffee is a valid card

    def test_vote_rejected_when_locked(self):
        b = _board()
        b.set_locked(True)
        assert not b.cast_vote("p1", "5")
        b.set_locked(False)
        assert b.cast_vote("p1", "5")

    def test_vote_rejected_after_reveal(self):
        b = _board()
        b.cast_vote("p1", "5")
        assert b.reveal()
        assert not b.cast_vote("p2", "8")

    def test_no_tickets_no_votes(self):
        b = PokerBoard("sess-1", tickets=[])
        assert not b.cast_vote("p1", "5")
        assert not b.reveal()

    def test_clear_vote(self):
        b = _board()
        b.cast_vote("p1", "5")
        assert b.clear_vote("p1")
        assert not b.clear_vote("p1")  # nothing to clear
        assert b.state_snapshot("p1")["mine_value"] == ""

    def test_revision_bumps_on_mutations(self):
        b = _board()
        r0 = b.revision()
        b.cast_vote("p1", "5")
        r1 = b.revision()
        assert r1 > r0
        b.reveal()
        assert b.revision() > r1

    def test_heartbeat_does_not_bump_revision(self):
        b = _board()
        r0 = b.revision()
        b.heartbeat("p1", name="Alex")
        assert b.revision() == r0


class TestRevealAndDistribution:
    def test_reveal_exposes_values_and_distribution(self):
        b = _board()
        b.heartbeat("p1", name="Alex", avatar="🦊")
        b.heartbeat("p2", name="Sam", avatar="🐙")
        b.cast_vote("p1", "5")
        b.cast_vote("p2", "8")
        b.reveal()
        snap = b.state_snapshot("p1")
        assert snap["phase"] == PHASE_REVEALED
        values = {v["name"]: v["value"] for v in snap["votes"]}
        assert values == {"Alex": "5", "Sam": "8"}
        assert snap["distribution"] == {"5": 1, "8": 1}
        assert snap["median"] == 6.5
        assert snap["suggestion"] == 8.0  # ties round up

    def test_suggested_points_ignores_non_numeric(self):
        b = _board()
        b.cast_vote("p1", "3")
        b.cast_vote("p2", "?")
        b.cast_vote("p3", "☕")
        b.cast_vote("p4", "5")
        assert b.suggested_points() == 5.0  # median(3,5)=4 → snaps up to 5

    def test_suggested_points_none_without_numeric_votes(self):
        b = _board()
        b.cast_vote("p1", "?")
        assert b.suggested_points() is None

    def test_double_reveal_rejected(self):
        b = _board()
        b.cast_vote("p1", "5")
        assert b.reveal()
        assert not b.reveal()

    def test_restart_vote_clears_round_and_ai(self):
        b = _board()
        b.cast_vote("p1", "5")
        b.reveal()
        b.set_ai_note("Big spread", 8.0)
        assert b.restart_vote()
        snap = b.state_snapshot("p1")
        assert snap["phase"] == PHASE_VOTING
        assert snap["mine_value"] == ""
        assert snap["ai"] == {"pending": False, "note": "", "suggested": None, "confidence": "", "evidence": []}


class TestFinalize:
    def test_finalize_stamps_and_advances(self):
        b = _board()
        b.heartbeat("p1", name="Alex", avatar="🦊")
        b.cast_vote("p1", "5")
        b.reveal()
        b.set_ai_note("Looks like a 5", 5.0)
        assert b.finalize_current(5)
        snap = b.state_snapshot("p1")
        assert snap["ticket_index"] == 1  # advanced
        assert snap["phase"] == PHASE_VOTING
        assert snap["progress"] == {"estimated": 1, "total": 3}
        first = b.tickets_snapshot()[0]
        assert first["estimated"] is True
        assert first["final_points"] == 5.0
        assert first["story_points"] == 5.0
        assert first["initial_points"] is None  # unchanged — what the tracker held before
        assert first["ai_note"] == "Looks like a 5"
        assert first["accepted_votes"] == [{"voter": "Alex", "avatar": "🦊", "value": "5"}]

    def test_finalize_folds_evidence_and_confidence_into_note(self):
        b = _board()
        b.cast_vote("p1", "5")
        b.reveal()
        b.set_ai_note("Looks like a 5", 5.0, confidence="high", evidence=("5-pt avg 4.2 days", "PROJ-87 shipped"))
        assert b.finalize_current(5)
        note = b.tickets_snapshot()[0]["ai_note"]
        assert note.startswith("Looks like a 5")
        assert "Evidence: 5-pt avg 4.2 days; PROJ-87 shipped" in note
        assert "(AI confidence: high)" in note

    def test_finalize_empty_note_stays_empty(self):
        b = _board()
        b.cast_vote("p1", "5")
        b.reveal()
        assert b.finalize_current(5)
        assert b.tickets_snapshot()[0]["ai_note"] == ""  # no evidence/confidence appended to nothing

    def test_finalize_requires_reveal(self):
        b = _board()
        b.cast_vote("p1", "5")
        assert not b.finalize_current(5)

    def test_finalize_bad_points_rejected(self):
        b = _board()
        b.cast_vote("p1", "5")
        b.reveal()
        assert not b.finalize_current("not-a-number")

    def test_finalize_last_ticket_stays_on_it(self):
        b = _board(n_tickets=1)
        b.cast_vote("p1", "3")
        b.reveal()
        assert b.finalize_current(3)
        assert b.state_snapshot()["ticket_index"] == 0
        assert b.progress() == (1, 1)


class TestNavigationAndEdits:
    def test_goto_resets_round(self):
        b = _board()
        b.cast_vote("p1", "5")
        b.reveal()
        assert b.goto_ticket(2)
        snap = b.state_snapshot("p1")
        assert snap["ticket_index"] == 2
        assert snap["phase"] == PHASE_VOTING
        assert snap["mine_value"] == ""

    def test_goto_out_of_range(self):
        b = _board()
        assert not b.goto_ticket(99)
        assert not b.goto_ticket(-1)
        assert not b.goto_ticket("x")

    def test_goto_same_index_is_noop_success(self):
        b = _board()
        b.cast_vote("p1", "5")
        assert b.goto_ticket(0)
        assert b.state_snapshot("p1")["mine_value"] == "5"  # round untouched

    def test_apply_ticket_edit(self):
        b = _board()
        assert b.apply_ticket_edit("T-1", summary="New title", description="New body", story_points=8)
        t = b.tickets_snapshot()[1]
        assert t["summary"] == "New title"
        assert t["description"] == "New body"
        assert t["description_text"] == "New body"
        assert t["story_points"] == 8.0

    def test_apply_ticket_edit_unknown_key(self):
        b = _board()
        assert not b.apply_ticket_edit("NOPE-1", summary="x")


class TestTicketPeek:
    def test_ticket_view_sanitized(self):
        b = _board()
        view = b.ticket_view(1)
        # Exhaustive set equality: adding an internal field here by accident
        # must fail loudly — this endpoint is readable by every token-holder.
        assert set(view) == {
            "index",
            "rev",
            "key",
            "summary",
            "description_text",
            "acceptance_text",
            "type",
            "story_points",
            "state",
            "assignee",
            "url",
            "estimated",
            "final_points",
        }
        assert view["key"] == "T-1"
        assert view["description_text"] == "Desc 1"
        assert view["index"] == 1
        assert view["rev"] == 0

    def test_ticket_view_bad_index(self):
        b = _board()
        assert b.ticket_view(99) is None
        assert b.ticket_view(-1) is None
        assert b.ticket_view("x") is None
        assert b.ticket_view(None) is None

    def test_rev_bumps_on_edit_only_for_touched_ticket(self):
        b = _board()
        assert b.apply_ticket_edit("T-1", description="New body")
        metas = b.state_snapshot()["tickets_meta"]
        assert [m["rev"] for m in metas] == [0, 1, 0]
        assert b.ticket_view(1)["rev"] == 1

    def test_rev_bumps_on_finalize(self):
        b = _board()
        b.cast_vote("p1", "5")
        b.reveal()
        assert b.finalize_current(5)
        metas = b.state_snapshot()["tickets_meta"]
        assert metas[0]["rev"] == 1
        assert metas[1]["rev"] == 0

    def test_rev_unchanged_by_votes(self):
        b = _board()
        b.cast_vote("p1", "5")
        b.cast_vote("p2", "8")
        b.reveal()
        assert all(m["rev"] == 0 for m in b.state_snapshot()["tickets_meta"])


class TestAiState:
    def test_pending_guard(self):
        b = _board()
        assert b.set_ai_pending(True)
        assert not b.set_ai_pending(True)  # already in flight — double-click guard
        b.set_ai_note("done", 5.0)
        assert b.set_ai_pending(True)  # note landed, a new request is fine

    def test_note_visible_in_snapshot(self):
        b = _board()
        b.set_ai_note("Talk through the 13", 8.0)
        assert b.state_snapshot()["ai"] == {
            "pending": False,
            "note": "Talk through the 13",
            "suggested": 8.0,
            "confidence": "",
            "evidence": [],
        }

    def test_confidence_and_evidence_in_snapshot(self):
        b = _board()
        b.set_ai_note("Grounded take", 5.0, confidence="high", evidence=("5-pt stories avg 4.2 days", "PROJ-87"))
        ai = b.state_snapshot()["ai"]
        assert ai["confidence"] == "high"
        assert ai["evidence"] == ["5-pt stories avg 4.2 days", "PROJ-87"]  # JSON-friendly list on the wire

    def test_invalid_confidence_dropped(self):
        b = _board()
        b.set_ai_note("x", 5.0, confidence="certain", evidence=())
        assert b.state_snapshot()["ai"]["confidence"] == ""

    def test_evidence_sanitized_and_capped(self):
        b = _board()
        b.set_ai_note("x", 5.0, confidence="low", evidence=("a", "  ", "b" * 500, "c", "d"))
        evidence = b.state_snapshot()["ai"]["evidence"]
        assert len(evidence) == 3  # blank dropped, capped at 3
        assert len(evidence[1]) == 200  # long entry truncated

    def test_goto_resets_confidence_and_evidence(self):
        b = _board()
        b.cast_vote("p1", "5")
        b.reveal()
        b.set_ai_note("x", 5.0, confidence="high", evidence=("cal",))
        b.goto_ticket(2)
        ai = b.state_snapshot()["ai"]
        assert ai["confidence"] == "" and ai["evidence"] == []

    def test_current_ticket_and_votes_resolves_names(self):
        b = _board()
        b.heartbeat("p1", name="Alex")
        b.cast_vote("p1", "5")
        ticket, votes = b.current_ticket_and_votes()
        assert ticket["key"] == "T-0"
        assert votes == {"Alex": "5"}

    def test_notice(self):
        b = _board()
        b.set_notice("Jira write failed")
        assert b.state_snapshot()["notice"] == "Jira write failed"


def _dueling_board(votes=(("p1", "Alex", "2"), ("p2", "Sam", "8"))) -> PokerBoard:
    b = _board()
    for pid, name, value in votes:
        b.heartbeat(pid, name=name)
        b.cast_vote(pid, value)
    b.reveal()
    return b


class TestDuel:
    def test_open_requires_revealed(self):
        b = _board()
        ok, err = b.open_duel(60)
        assert ok is False
        assert "reveal" in err

    def test_open_requires_two_distinct_numeric_votes(self):
        b = _dueling_board(votes=(("p1", "Alex", "5"), ("p2", "Sam", "5"), ("p3", "Kim", "?")))
        ok, err = b.open_duel(60)
        assert ok is False
        assert "two different numeric votes" in err

    def test_open_rejected_while_locked(self):
        b = _dueling_board()
        b.set_locked(True)
        assert b.open_duel(60)[0] is False

    def test_open_rejected_while_already_dueling(self):
        b = _dueling_board()
        assert b.open_duel(60) == (True, "")
        assert b.open_duel(60)[0] is False

    def test_open_picks_low_and_high_and_starts_timer(self):
        b = _dueling_board()
        ok, err = b.open_duel(90)
        assert (ok, err) == (True, "")
        state = b.state_snapshot()
        assert state["phase"] == "duel"
        duel = state["duel"]
        assert duel["status"] == "live"
        assert duel["turn"] == "low" and duel["turn_no"] == 1
        assert duel["low"] == {"name": "Alex", "avatar": "", "value": "2"}
        assert duel["high"] == {"name": "Sam", "avatar": "", "value": "8"}
        assert duel["turn_seconds"] == 90
        assert state["timer"]["running"] is True
        assert state["timer"]["duration"] == 90

    def test_open_clamps_turn_seconds(self):
        b = _dueling_board()
        b.open_duel(1)
        assert b.state_snapshot()["duel"]["turn_seconds"] == 15  # _DUEL_TURN_MIN

    def test_tie_break_uses_secrets_choice(self, monkeypatch):
        import yeaboi.poker.board as board_mod

        picked: list = []

        def _choice(seq):
            picked.append(list(seq))
            return seq[-1]

        monkeypatch.setattr(board_mod.secrets, "choice", _choice)
        b = _dueling_board(votes=(("p1", "Alex", "2"), ("p2", "Sam", "2"), ("p3", "Kim", "8")))
        assert b.open_duel(60) == (True, "")
        # Low candidates were both 2-voters (sorted pids); the stubbed choice took the last.
        assert picked[0] == ["p1", "p2"]
        assert b.state_snapshot()["duel"]["low"]["name"] == "Sam"

    def test_snapshot_hides_pids_and_carries_mine_role(self):
        b = _dueling_board()
        b.open_duel(60)
        duel = b.state_snapshot(viewer_pid="p1")["duel"]
        assert "low_pid" not in duel and "high_pid" not in duel
        assert duel["mine_role"] == "low"
        assert b.state_snapshot(viewer_pid="p2")["duel"]["mine_role"] == "high"
        assert b.state_snapshot(viewer_pid="p9")["duel"]["mine_role"] == ""
        assert b.state_snapshot()["duel"]["mine_role"] == ""

    def test_votes_stay_visible_during_duel(self):
        b = _dueling_board()
        b.open_duel(60)
        state = b.state_snapshot()
        assert {v["name"]: v["value"] for v in state["votes"]} == {"Alex": "2", "Sam": "8"}
        assert state["suggestion"] is not None

    def test_cast_vote_rejected_during_duel(self):
        b = _dueling_board()
        b.open_duel(60)
        assert b.cast_vote("p1", "13") is False

    def test_finalize_rejected_during_duel_phase(self):
        b = _dueling_board()
        b.open_duel(60)
        assert b.finalize_current(5) is False

    def test_advance_turn_flips_high_and_restarts_timer(self):
        b = _dueling_board()
        b.open_duel(60)
        assert b.advance_turn() is True
        duel = b.state_snapshot()["duel"]
        assert duel["turn"] == "high" and duel["turn_no"] == 2
        assert b.state_snapshot()["timer"]["running"] is True
        # Only one handover: low → high.
        assert b.advance_turn() is False

    def test_advance_turn_requires_live_duel(self):
        assert _dueling_board().advance_turn() is False

    def test_close_returns_to_revealed_and_transcribing(self):
        b = _dueling_board()
        b.open_duel(60)
        info = b.close_duel()
        assert info is not None
        # The worker copy carries what the snapshot must not: pids + ticket index.
        assert info["low_pid"] == "p1" and info["high_pid"] == "p2"
        assert info["ticket_index"] == 0
        state = b.state_snapshot()
        assert state["phase"] == "revealed"
        assert state["duel"]["status"] == "transcribing"
        assert state["duel"]["recording"] == {"host": False, "low": False, "high": False}
        assert state["timer"]["running"] is False
        assert b.close_duel() is None  # already closed

    def test_recording_flags_only_while_live(self):
        b = _dueling_board()
        b.open_duel(60)
        assert b.set_duel_recording("host", True) is True
        assert b.set_duel_recording("low", True) is True
        assert b.set_duel_recording("nope", True) is False
        assert b.state_snapshot()["duel"]["recording"] == {"host": True, "low": True, "high": False}
        b.close_duel()
        assert b.set_duel_recording("high", True) is False

    def test_duel_pid_role(self):
        b = _dueling_board()
        b.open_duel(60)
        assert b.duel_pid_role("p1") == "low"
        assert b.duel_pid_role("p2") == "high"
        assert b.duel_pid_role("p9") == ""
        assert b.duel_pid_role("") == ""

    def test_transcript_lands_capped_and_done(self):
        b = _dueling_board()
        b.open_duel(60)
        b.close_duel()
        b.set_duel_transcript("x" * 10_000)
        duel = b.state_snapshot()["duel"]
        assert duel["status"] == "done"
        assert len(duel["transcript"]) == 6000  # _MAX_TRANSCRIPT
        assert b.current_duel_transcript() == "x" * 6000

    def test_empty_transcript_means_failed(self):
        b = _dueling_board()
        b.open_duel(60)
        b.close_duel()
        b.set_duel_transcript("", error="mic exploded")
        duel = b.state_snapshot()["duel"]
        assert duel["status"] == "failed"
        assert duel["error"] == "mic exploded"
        assert b.current_duel_transcript() == ""

    def test_transcript_noop_after_cancel(self):
        b = _dueling_board()
        b.open_duel(60)
        b.close_duel()
        b.restart_vote()  # cancels the duel while the "worker" is transcribing
        b.set_duel_transcript("late result")
        assert b.state_snapshot()["duel"] is None

    def test_restart_and_goto_cancel_duel(self):
        b = _dueling_board()
        b.open_duel(60)
        b.restart_vote()
        state = b.state_snapshot()
        assert state["duel"] is None and state["phase"] == "voting"

        b2 = _dueling_board()
        b2.open_duel(60)
        b2.goto_ticket(1)
        state2 = b2.state_snapshot()
        assert state2["duel"] is None and state2["phase"] == "voting"
        assert state2["timer"]["running"] is False

    def test_finalize_stamps_duel_fields_and_clears(self):
        b = _dueling_board()
        b.open_duel(60)
        b.close_duel()
        b.set_duel_transcript("Alex said 2 is enough; Sam disagreed.")
        assert b.finalize_current(5) is True
        assert b.state_snapshot()["duel"] is None
        ticket = b.tickets_snapshot()[0]
        assert ticket["duel_transcript"] == "Alex said 2 is enough; Sam disagreed."
        assert ticket["duel_low"] == "Alex (2)"
        assert ticket["duel_high"] == "Sam (8)"

    def test_finalize_while_transcribing_drops_transcript(self):
        b = _dueling_board()
        b.open_duel(60)
        b.close_duel()
        # No transcript landed yet — a raw-HTTP finalize loses it (UI prevents this).
        assert b.finalize_current(5) is True
        ticket = b.tickets_snapshot()[0]
        assert ticket["duel_transcript"] == ""
        assert b.state_snapshot()["duel"] is None


class TestBoardToReport:
    def test_report_roundtrip(self):
        b = _board()
        b.heartbeat("p1", name="Alex", avatar="🦊")
        b.heartbeat("p2", name="Sam", avatar="🐙")
        b.cast_vote("p1", "5")
        b.cast_vote("p2", "8")
        b.reveal()
        b.finalize_current(8)
        report = board_to_report(b)
        assert report.session_id == "sess-1"
        assert report.source == "demo"
        assert report.scope_label == "Backlog"
        assert len(report.tickets) == 3
        first = report.tickets[0]
        assert first.estimated is True
        assert first.final_points == 8.0
        assert {v.voter for v in first.votes} == {"Alex", "Sam"}
        assert report.tickets[1].estimated is False
        assert report.participants == ("Alex", "Sam")

    def test_duel_fields_reach_report(self):
        b = _dueling_board()
        b.open_duel(60)
        b.close_duel()
        b.set_duel_transcript("the debate")
        b.finalize_current(5)
        first = board_to_report(b).tickets[0]
        assert first.duel_transcript == "the debate"
        assert first.duel_low == "Alex (2)"
        assert first.duel_high == "Sam (8)"
        assert board_to_report(b).tickets[1].duel_transcript == ""

    def test_participants_survive_presence_expiry(self):
        b = _board()
        b.heartbeat("p1", name="Alex")
        # Even if presence expires later, the report still credits Alex.
        assert board_to_report(b).participants == ("Alex",)


class TestDeckConstant:
    def test_deck_shape(self):
        assert POKER_DECK == ("0", "1", "2", "3", "5", "8", "13", "21", "?", "☕")
