"""Tests for sync_naming — board-aware sprint numbering shared by the Jira/AzDO syncs."""

from yeaboi.sync_naming import (
    BoardNumbering,
    advance_past_closed,
    derive_board_numbering,
    resolve_starting_number,
)


class TestDeriveBoardNumbering:
    def test_empty_board(self):
        numbering = derive_board_numbering([])
        assert numbering == BoardNumbering()

    def test_single_convention(self):
        numbering = derive_board_numbering(
            [("PSOT Sprint 105", "closed"), ("PSOT Sprint 106", "active"), ("PSOT Sprint 107", "future")]
        )
        assert numbering.prefix == "PSOT Sprint "
        assert numbering.max_number == 107

    def test_consensus_beats_outlier_with_higher_number(self):
        # "Hardening 2024" carries the highest trailing integer but must not
        # hijack the convention from the fifty real sprints.
        pairs = [(f"PSOT Sprint {n}", "closed") for n in range(60, 108)] + [("Hardening 2024", "closed")]
        numbering = derive_board_numbering(pairs)
        assert numbering.prefix == "PSOT Sprint "
        assert numbering.max_number == 107

    def test_tie_breaks_toward_live_sequence(self):
        pairs = [("Alpha 1", "closed"), ("Alpha 2", "closed"), ("Beta 8", "active"), ("Beta 9", "future")]
        numbering = derive_board_numbering(pairs)
        assert numbering.prefix == "Beta "
        assert numbering.max_number == 9

    def test_trailing_digit_run_captured_whole(self):
        # Greedy digit capture: "PI 2024.3" → prefix "PI 2024.", number 3 —
        # but a mixed digit name must not split mid-run.
        numbering = derive_board_numbering([("Sprint 107", "active"), ("Sprint 108", "future")])
        assert numbering.max_number == 108

    def test_names_without_trailing_number_ignored_for_prefix(self):
        numbering = derive_board_numbering([("Hardening", "closed"), ("Backlog grooming", "closed")])
        assert numbering.prefix == ""
        assert numbering.max_number == 0
        assert numbering.closed_names == frozenset({"Hardening", "Backlog grooming"})

    def test_closed_names_collected_regardless_of_numbering(self):
        numbering = derive_board_numbering([("Sprint 5", "closed"), ("Sprint 6", "active")])
        assert numbering.closed_names == frozenset({"Sprint 5"})


class TestResolveStartingNumber:
    def test_positive_configured_wins(self):
        numbering = BoardNumbering(prefix="Sprint ", max_number=107)
        assert resolve_starting_number(42, numbering) == 42

    def test_minus_one_sentinel_falls_through_to_board(self):
        # The intake writes -1 when no tracker sprint was picked; it must never
        # become a sprint number.
        numbering = BoardNumbering(prefix="Sprint ", max_number=107)
        assert resolve_starting_number(-1, numbering) == 108

    def test_zero_falls_through_to_board(self):
        numbering = BoardNumbering(prefix="Sprint ", max_number=3)
        assert resolve_starting_number(0, numbering) == 4

    def test_unnumbered_board_yields_zero(self):
        assert resolve_starting_number(-1, BoardNumbering()) == 0
        assert resolve_starting_number(0, BoardNumbering()) == 0


class TestAdvancePastClosed:
    def test_no_collision_unchanged(self):
        numbering = BoardNumbering(prefix="Sprint ", max_number=10, closed_names=frozenset({"Sprint 5"}))
        start, warning = advance_past_closed(11, 2, numbering)
        assert start == 11
        assert warning == ""

    def test_collision_shifts_whole_batch(self):
        numbering = BoardNumbering(
            prefix="Sprint ",
            max_number=107,
            closed_names=frozenset({"Sprint 105", "Sprint 106"}),
        )
        start, warning = advance_past_closed(105, 3, numbering)
        assert start == 108
        assert "Sprint 105" in warning and "Sprint 108" in warning

    def test_zero_start_or_no_prefix_noop(self):
        assert advance_past_closed(0, 3, BoardNumbering(closed_names=frozenset({"Sprint 1"}))) == (0, "")
        numbering = BoardNumbering(prefix="", max_number=0, closed_names=frozenset({"Sprint 1"}))
        assert advance_past_closed(1, 1, numbering) == (1, "")
