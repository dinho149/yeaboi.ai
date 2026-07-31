"""Unit tests for the live RetroBoard, its lock-guarded mutations, and snapshots."""

import threading

from yeaboi.agent.state import RetroCard, RetroReport
from yeaboi.retro.board import (
    AVATARS,
    REACTION_EMOJIS,
    RETRO_GRID_LABELS,
    RETRO_GRIDS,
    RetroBoard,
    board_to_report,
)


class TestAddCard:
    def test_happy_path(self):
        b = RetroBoard("s", "Proj")
        card = b.add_card(grid="went_well", text="CI is fast", author="Sam")
        assert card is not None
        assert card.grid == "went_well" and card.text == "CI is fast" and card.author == "Sam"
        assert card.origin == "web" and len(card.id) == 12
        assert b.total() == 1

    def test_invalid_grid_rejected(self):
        b = RetroBoard("s")
        assert b.add_card(grid="bogus", text="x", author="y") is None
        assert b.total() == 0

    def test_empty_text_rejected(self):
        b = RetroBoard("s")
        assert b.add_card(grid="demos", text="   ", author="y") is None

    def test_blank_author_defaults_to_anon(self):
        b = RetroBoard("s")
        card = b.add_card(grid="demos", text="new UI", author="   ")
        assert card is not None and card.author == "anon"

    def test_text_and_author_capped(self):
        b = RetroBoard("s")
        card = b.add_card(grid="demos", text="x" * 5000, author="a" * 500)
        assert card is not None
        assert len(card.text) == 500 and len(card.author) == 60

    def test_max_cards_enforced(self):
        b = RetroBoard("s")
        for i in range(505):
            b.add_card(grid="demos", text=f"c{i}", author="a")
        assert b.total() == 500


class TestAiCards:
    def test_ai_cards_go_to_action_items(self):
        b = RetroBoard("s")
        added = b.add_ai_cards(["Fix flaky tests", "  ", "Document deploy"])
        assert added == 2  # blank skipped
        grids = b.cards_by_grid()
        assert len(grids["action_items"]) == 2
        assert all(c.origin == "ai" and c.author == "AI" for c in grids["action_items"])


class TestSnapshot:
    def test_revision_bumps_on_mutation(self):
        b = RetroBoard("s")
        assert b.revision() == 0
        b.add_card(grid="demos", text="a", author="x")
        assert b.revision() == 1

    def test_snapshot_is_a_copy(self):
        b = RetroBoard("s")
        b.add_card(grid="demos", text="a", author="x")
        _, cards = b.snapshot()
        cards.clear()  # mutating the copy must not affect the board
        assert b.total() == 1

    def test_cards_by_grid_has_all_keys(self):
        b = RetroBoard("s")
        grids = b.cards_by_grid()
        assert set(grids) == set(RETRO_GRIDS)
        assert set(RETRO_GRID_LABELS) == set(RETRO_GRIDS)


class TestThreadSafety:
    def test_concurrent_adds_are_all_recorded(self):
        b = RetroBoard("s")

        def worker():
            for i in range(50):
                b.add_card(grid="demos", text=f"c{i}", author="t")

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 4 threads × 50 = 200 cards, all under the 500 cap → none lost to a race.
        assert b.total() == 200


class TestBoardToReport:
    def test_participants_exclude_ai(self):
        b = RetroBoard("s", "Proj", "Sprint 5")
        b.add_card(grid="went_well", text="ci", author="Sam")
        b.add_card(grid="didnt_go_well", text="flaky", author="Rae")
        b.add_card(grid="went_well", text="again", author="Sam")  # dup author
        b.add_ai_cards(["do the thing"])
        rep = board_to_report(b)
        assert isinstance(rep, RetroReport)
        assert rep.participants == ("Sam", "Rae")
        assert rep.sprint_name == "Sprint 5"
        assert len(rep.cards) == 4

    def test_sprint_name_override(self):
        b = RetroBoard("s")
        rep = board_to_report(b, sprint_name="Sprint 9")
        assert rep.sprint_name == "Sprint 9"

    def test_by_grid_groups_report_cards(self):
        b = RetroBoard("s")
        b.add_card(grid="demos", text="d", author="x")
        rep = board_to_report(b)
        by = rep.by_grid()
        assert len(by["demos"]) == 1 and by["went_well"] == []


class TestRetroCardDefaults:
    def test_defaults_for_backward_compat(self):
        c = RetroCard()
        assert c.id == "" and c.grid == "" and c.origin == "web"
        assert c.reactions == ()


class TestReactions:
    def test_emoji_set_is_pinned_and_append_only(self):
        # Nothing else pins this tuple: the codegen equality check in
        # test_web_frontend_guards only proves enums.ts matches whatever is here,
        # so an edit would otherwise sail through with zero review signal. The
        # original eight must stay — store.py rehydrates persisted reactions
        # without re-validating membership, so a removal makes old boards render
        # an emoji the live counter no longer counts.
        assert REACTION_EMOJIS[:8] == ("👍", "👎", "❤️", "🎉", "😂", "🔥", "😢", "🚀")
        assert len(REACTION_EMOJIS) == 16
        assert len(set(REACTION_EMOJIS)) == 16
        # The rejection case below leans on this: 🦖 is an avatar, not a reaction.
        assert not set(REACTION_EMOJIS) & set(AVATARS)

    def test_toggle_on_off(self):
        b = RetroBoard("s")
        c = b.add_card(grid="went_well", text="ci", author="Sam")
        assert b.toggle_reaction(c.id, "👍", "p1") is True
        assert b.toggle_reaction(c.id, "👍", "p2") is True
        assert b.reaction_counts(c.id) == {"👍": 2}
        assert b.toggle_reaction(c.id, "👍", "p1") is False  # same pid toggles off
        assert b.reaction_counts(c.id) == {"👍": 1}

    def test_unknown_emoji_rejected(self):
        b = RetroBoard("s")
        c = b.add_card(grid="demos", text="x", author="a")
        assert b.toggle_reaction(c.id, "🦖", "p1") is False  # not in REACTION_EMOJIS
        assert b.reaction_counts(c.id) == {}

    def test_missing_card_and_blank_pid_rejected(self):
        b = RetroBoard("s")
        assert b.toggle_reaction("nope", "👍", "p1") is False
        c = b.add_card(grid="demos", text="x", author="a")
        assert b.toggle_reaction(c.id, "👍", "") is False

    def test_report_carries_reaction_counts(self):
        b = RetroBoard("s")
        c = b.add_card(grid="didnt_go_well", text="flaky", author="Rae")
        b.toggle_reaction(c.id, "👍", "p1")
        b.toggle_reaction(c.id, "🔥", "p2")
        rep = board_to_report(b)
        assert dict(rep.cards[0].reactions) == {"👍": 1, "🔥": 1}


class TestReactionEvents:
    def test_add_queues_event_remove_does_not(self):
        # Adding a reaction broadcasts an event; toggling it back off does not.
        b = RetroBoard("s")
        c = b.add_card(grid="went_well", text="ci", author="Sam")
        b.toggle_reaction(c.id, "👍", "p1")  # add -> one event
        events = b.state_snapshot()["reaction_events"]
        assert events == [{"id": 0, "emoji": "👍"}]
        b.toggle_reaction(c.id, "👍", "p1")  # remove -> no new event
        assert b.state_snapshot()["reaction_events"] == [{"id": 0, "emoji": "👍"}]

    def test_ids_are_monotonic_across_cards(self):
        b = RetroBoard("s")
        c = b.add_card(grid="went_well", text="a", author="x")
        b.toggle_reaction(c.id, "👍", "p1")
        b.toggle_reaction(c.id, "🔥", "p2")
        ids = [e["id"] for e in b.state_snapshot()["reaction_events"]]
        assert ids == [0, 1]

    def test_rejected_reaction_queues_nothing(self):
        b = RetroBoard("s")
        c = b.add_card(grid="demos", text="x", author="a")
        b.toggle_reaction(c.id, "🦖", "p1")  # unknown emoji
        b.toggle_reaction("nope", "👍", "p1")  # missing card
        assert b.state_snapshot()["reaction_events"] == []

    def test_event_buffer_caps_at_25(self):
        b = RetroBoard("s")
        c = b.add_card(grid="went_well", text="a", author="x")
        # 30 distinct reactors adding 👍 -> 30 add-events, buffer keeps the last 25.
        for i in range(30):
            b.toggle_reaction(c.id, "👍", f"p{i}")
        events = b.state_snapshot()["reaction_events"]
        assert len(events) == 25
        assert events[0]["id"] == 5 and events[-1]["id"] == 29


class TestPresenceAndTyping:
    def test_heartbeat_and_presence_list(self):
        b = RetroBoard("s")
        b.heartbeat("p1", name="Sam", avatar="🤠", typing_grid="went_well")
        b.heartbeat("p2", name="Rae", avatar="👻", typing_grid="")
        pres = b.presence_list()
        assert {"name": "Sam", "avatar": "🤠"} in pres and len(pres) == 2

    def test_typing_list_only_active_typers(self):
        b = RetroBoard("s")
        b.heartbeat("p1", name="Sam", avatar="🤠", typing_grid="went_well")
        b.heartbeat("p2", name="Rae", avatar="👻", typing_grid="")
        assert b.typing_list() == [{"name": "Sam", "grid": "went_well"}]

    def test_invalid_avatar_and_grid_sanitised(self):
        b = RetroBoard("s")
        b.heartbeat("p1", name="X", avatar="NOTANAVATAR", typing_grid="bogus")
        p = b.presence_list()[0]
        assert p["avatar"] == "" and b.typing_list() == []

    def test_presence_ttl_expiry(self, monkeypatch):
        import yeaboi.retro.board as board_mod

        clock = {"t": 1000.0}
        monkeypatch.setattr(board_mod.time, "monotonic", lambda: clock["t"])
        b = RetroBoard("s")
        b.heartbeat("p1", name="Sam", avatar="🤠")
        assert len(b.presence_list()) == 1
        clock["t"] += 100  # far past the TTL
        assert b.presence_list() == []

    def test_heartbeat_does_not_bump_revision(self):
        b = RetroBoard("s")
        r0 = b.revision()
        b.heartbeat("p1", name="Sam", avatar="🤠")
        assert b.revision() == r0  # heartbeats fire constantly — must not churn revision


class TestTimer:
    def test_start_stop(self):
        b = RetroBoard("s")
        b.start_timer(120)
        t = b.state_snapshot()["timer"]
        assert t["running"] and t["end_epoch"] > t["now_epoch"] and t["duration"] == 120
        b.stop_timer()
        assert b.state_snapshot()["timer"]["running"] is False

    def test_duration_clamped(self):
        b = RetroBoard("s")
        b.start_timer(999999)
        assert b.state_snapshot()["timer"]["duration"] == 3600
        b.start_timer(0)
        assert b.state_snapshot()["timer"]["duration"] == 1


class TestStateSnapshot:
    def test_shape(self):
        b = RetroBoard("s")
        c = b.add_card(grid="went_well", text="ci", author="Sam")
        b.toggle_reaction(c.id, "👍", "p1")
        b.heartbeat("p1", name="Sam", avatar="🤠", typing_grid="went_well")
        snap = b.state_snapshot()
        assert set(snap) == {
            "revision",
            "cards",
            "carried",
            "presence",
            "typing",
            "timer",
            "reaction_events",
            "broadcast",
            "locked",
        }
        assert snap["cards"][0]["reactions"] == {"👍": 1}
        assert snap["presence"] and snap["typing"][0]["grid"] == "went_well"

    def test_mine_flag(self):
        b = RetroBoard("s")
        b.add_card(grid="demos", text="x", author="Sam", pid="p1")
        assert b.state_snapshot("p1")["cards"][0]["mine"] is True
        assert b.state_snapshot("p2")["cards"][0]["mine"] is False
        assert b.state_snapshot()["cards"][0]["mine"] is False  # no viewer → never mine


class TestEditDeleteMove:
    def test_edit_author_only(self):
        b = RetroBoard("s")
        c = b.add_card(grid="went_well", text="ci", author="Sam", pid="p1")
        assert b.edit_card(c.id, "ci fixed", "p2") is False  # not the author
        assert b.edit_card(c.id, "ci fixed", "p1") is True
        assert b.cards_by_grid()["went_well"][0].text == "ci fixed"

    def test_edit_rejects_empty(self):
        b = RetroBoard("s")
        c = b.add_card(grid="demos", text="x", author="a", pid="p1")
        assert b.edit_card(c.id, "   ", "p1") is False

    def test_delete_author_only_and_clears_reactions(self):
        b = RetroBoard("s")
        c = b.add_card(grid="demos", text="x", author="a", pid="p1")
        b.toggle_reaction(c.id, "👍", "p9")
        assert b.delete_card(c.id, "p2") is False
        assert b.delete_card(c.id, "p1") is True
        assert b.total() == 0 and b.reaction_counts(c.id) == {}

    def test_move_reorders_within_grid(self):
        b = RetroBoard("s")
        a = b.add_card(grid="went_well", text="A", author="x", pid="pa")
        bb = b.add_card(grid="went_well", text="B", author="x", pid="pb")
        assert b.move_card(bb.id, "went_well", 0, "anyone") is True  # open to anyone
        assert [c.text for c in b.cards_by_grid()["went_well"]] == ["B", "A"]
        assert a  # keep ref

    def test_move_across_grids(self):
        b = RetroBoard("s")
        a = b.add_card(grid="went_well", text="A", author="x", pid="pa")
        b.add_card(grid="didnt_go_well", text="C", author="x", pid="pc")
        assert b.move_card(a.id, "didnt_go_well", 0, "z") is True
        assert [c.text for c in b.cards_by_grid()["didnt_go_well"]] == ["A", "C"]
        assert b.cards_by_grid()["went_well"] == []

    def test_move_rejects_bad_grid_and_missing_card(self):
        b = RetroBoard("s")
        c = b.add_card(grid="demos", text="x", author="a", pid="p1")
        assert b.move_card(c.id, "bogus", 0) is False
        assert b.move_card("nope", "demos", 0) is False


class TestCarriedActionItems:
    def _seed(self):
        b = RetroBoard("s")
        b.seed_carried(
            [
                RetroCard(id="k1", grid="action_items", text="ship docs", author="Sam", origin="ai"),
                RetroCard(id="k2", grid="action_items", text="  ", author="AI"),  # blank dropped
            ]
        )
        return b

    def test_seed_resets_status_and_origin_and_drops_blanks(self):
        b = self._seed()
        carried = b.carried_snapshot()
        assert [c.id for c in carried] == ["k1"]
        assert carried[0].status == "pending" and carried[0].origin == "carryover"
        # Preserves text/author from the prior report.
        assert carried[0].text == "ship docs" and carried[0].author == "Sam"

    def test_set_status_valid_and_bumps_revision(self):
        b = self._seed()
        rev = b.revision()
        assert b.set_carried_status("k1", "done") is True
        assert b.carried_snapshot()[0].status == "done"
        assert b.revision() > rev

    def test_set_status_rejects_unknown_status_or_missing_item(self):
        b = self._seed()
        assert b.set_carried_status("k1", "bogus") is False
        assert b.set_carried_status("nope", "done") is False
        assert b.carried_snapshot()[0].status == "pending"  # unchanged

    def test_state_snapshot_includes_carried(self):
        b = self._seed()
        b.set_carried_status("k1", "carried_over")
        snap = b.state_snapshot()
        assert snap["carried"][0]["id"] == "k1"
        assert snap["carried"][0]["status"] == "carried_over"

    def test_board_to_report_carries_them_through(self):
        b = self._seed()
        b.set_carried_status("k1", "not_relevant")
        report = board_to_report(b)
        assert len(report.carried_action_items) == 1
        assert report.carried_action_items[0].status == "not_relevant"

    def test_add_carryover_cards_dedupes_against_grid(self):
        b = RetroBoard("s")
        b.add_ai_cards(["ship docs"])
        added = b.add_carryover_cards(["ship docs", "new one"])
        assert added == 1  # "ship docs" already present → skipped
        texts = [c.text for c in b.cards_by_grid()["action_items"]]
        assert texts == ["ship docs", "new one"]
        carry = [c for c in b.cards_by_grid()["action_items"] if c.origin == "carryover"]
        assert carry and carry[0].text == "new one"


class TestHostBroadcast:
    def test_theme_accepts_known_and_rejects_unknown(self):
        b = RetroBoard("s")
        assert b.set_broadcast_theme("synthwave") is True
        assert b.state_snapshot()["broadcast"]["theme"] == "synthwave"
        assert b.set_broadcast_theme("chartreuse") is False  # not a real theme
        assert b.state_snapshot()["broadcast"]["theme"] == "synthwave"  # unchanged

    def test_music_validates_channel_and_bumps_seq(self):
        b = RetroBoard("s")
        assert b.set_broadcast_music(playing=True, channel=0) is True
        first = b.state_snapshot()["broadcast"]["music"]
        assert first["playing"] is True and first["channel"] == 0 and first["seq"] == 1
        assert b.set_broadcast_music(playing=False, channel=0) is True
        assert b.state_snapshot()["broadcast"]["music"]["seq"] == 2  # each command is unique
        assert b.set_broadcast_music(playing=True, channel=9999) is False  # out of range
        assert b.set_broadcast_music(playing=True, channel="x") is False  # non-int

    def test_lock_freezes_add_edit_move_delete(self):
        b = RetroBoard("s")
        c = b.add_card(grid="went_well", text="ci", author="Sam", pid="p1")
        b.set_locked(True)
        assert b.state_snapshot()["locked"] is True
        assert b.add_card(grid="went_well", text="blocked", author="Sam", pid="p1") is None
        assert b.edit_card(c.id, "nope", "p1") is False
        assert b.move_card(c.id, "demos", 0, "p1") is False
        assert b.delete_card(c.id, "p1") is False
        b.set_locked(False)
        assert b.edit_card(c.id, "now ok", "p1") is True
