"""Pin the live snapshot shapes the TypeScript boards are written against.

``types/board.ts`` is hand-written, because a state shape carries meaning a
codegen cannot express — which fields are per-viewer, which one deliberately
does not bump ``revision``, which is a command rather than a value. The cost of
hand-writing it is that nothing stops the server from quietly dropping a field
the bundle still reads, and the symptom of that is a board that renders
``undefined`` to a teammate on a tunnel while every test on both sides passes.

So this drives a **real board through a real round** and writes the snapshots to
``frontend/src/test/fixtures/``. ``wire.ts`` then asserts each one ``satisfies``
its interface, and ``npm run typecheck`` fails if the server stopped sending
something the board promises.

Two directions, only one of them guarded, and deliberately so:

* A **removed or renamed** field fails the TypeScript build — the fixture no
  longer has what the interface requires.
* An **added** field does not, because TypeScript excess-property-checks only
  fresh object literals and an imported JSON module is not one.

That is the useful direction: adding a field breaks nothing, removing one breaks
a board. The check here (regenerate, compare, fail on difference) is the same
shape as the committed-bundle staleness check in ``make web-check`` — the
fixture is a build artifact under review, not a hand-written expectation that
can invent its own contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from yeaboi.poker.board import PokerBoard
from yeaboi.retro.board import RetroBoard

FIXTURES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "test" / "fixtures"

pytestmark = pytest.mark.skipif(
    not FIXTURES.parent.is_dir(), reason="frontend sources are not part of an installed wheel"
)


def _retro_snapshot() -> dict:
    """A retro mid-ceremony: cards from two people, a reaction, presence, a timer."""
    board = RetroBoard("wire-retro", project_name="yeaboi", sprint_name="Sprint 42")
    board.heartbeat("pid-a", name="Ada", avatar="🦊")
    board.heartbeat("pid-b", name="Grace", avatar="🐙")
    mine = board.add_card(grid="went_well", text="Shipped the tunnel", author="Ada", origin="web", pid="pid-a")
    board.add_card(grid="action_items", text="Alert on staging", author="Grace", origin="web", pid="pid-b")
    # A carried-over item exercises the `status` field, which is `''` on every
    # authoring-grid card and so would otherwise never appear in the fixture.
    board.add_card(grid="action_items", text="From last sprint", author="Ada", origin="carryover", pid="pid-a")
    assert mine is not None
    board.toggle_reaction(mine.id, "👍", "pid-b")
    board.start_timer(300)
    board.set_broadcast_theme("forest")
    # Per-viewer: `mine` must be true for Ada's own card, which is the field the
    # edit and delete controls hang off.
    return board.state_snapshot(viewer_pid="pid-a")


def _poker_snapshots() -> dict[str, dict]:
    """A poker round in each phase, plus the peek projection.

    Three snapshots rather than one, because the poker payload is genuinely
    phase-shaped: vote secrecy means ``votes[].value`` does not exist at all
    while voting, and ``duel`` is null until the floor opens. A single fixture
    would pin whichever phase it happened to capture and leave the others
    unguarded.
    """
    tickets = [
        {
            "key": "YB-1",
            "summary": "Long-poll the board",
            "description_text": "The tunnel buffers SSE.",
            "acceptance_text": "Updates land in under a second.",
            "type": "Story",
            "state": "To Do",
            "assignee": "Ada",
            "url": "https://example.invalid/browse/YB-1",
            "story_points": None,
            "source": "jira",
        },
        {"key": "YB-2", "summary": "Quantise the duck", "story_points": 3},
    ]
    board = PokerBoard("wire-poker", project_name="yeaboi", source="jira", tickets=tickets)
    board.heartbeat("pid-a", name="Ada", avatar="🦊")
    board.heartbeat("pid-b", name="Grace", avatar="🐙")
    board.cast_vote("pid-a", "3")
    board.cast_vote("pid-b", "13")
    board.start_timer(120)

    voting = board.state_snapshot(viewer_pid="pid-a")

    board.reveal()
    board.set_ai_note("Two calls to an unfamiliar API.", suggested=5.0, confidence="medium", evidence=["No SDK yet."])
    revealed = board.state_snapshot(viewer_pid="pid-a")

    opened, error = board.open_duel(90)
    assert opened, error
    # Viewed as the low voter, so `mine_role` is exercised as something other
    # than the empty string a bystander sees.
    duel = board.state_snapshot(viewer_pid="pid-a")

    peek = board.ticket_view(1)
    assert peek is not None

    return {"poker.voting": voting, "poker.revealed": revealed, "poker.duel": duel, "ticket.peek": peek}


def _fixtures() -> dict[str, dict]:
    return {"retro": _retro_snapshot(), **_poker_snapshots()}


def _normalise(payload: dict) -> dict:
    """Pin the values that differ between two identical runs.

    Clocks move, and card ids are random hex. Committing either would make this
    test rewrite its own fixtures every run, and a fixture that always changes
    reports nothing when the thing it watches finally does.

    Pinned to a constant of the same kind rather than deleted: the guard reads
    *types*, so a number has to stay a number and a null has to stay a null. A
    dropped field would read as "the server stopped sending this", which is
    exactly the signal being protected.
    """
    frozen = json.loads(json.dumps(payload))
    counter = 0

    def walk(node: object) -> object:
        nonlocal counter
        if isinstance(node, dict):
            out: dict = {}
            for key, value in node.items():
                if key in ("now_epoch", "end_epoch") and isinstance(value, (int, float)):
                    out[key] = 0
                elif key == "created_at" and isinstance(value, str):
                    out[key] = "2026-01-01T00:00:00+00:00"
                elif key == "id" and isinstance(value, str):
                    # Card ids only — `reaction_events[].id` is an int and is a
                    # monotonic counter, so it is already stable.
                    counter += 1
                    out[key] = f"card{counter:04d}"
                else:
                    out[key] = walk(value)
            return out
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node

    return walk(frozen)  # type: ignore[return-value]


def _write(name: str, payload: dict) -> str:
    text = json.dumps(_normalise(payload), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    (FIXTURES / f"{name}.json").write_text(text, encoding="utf-8")
    return text


class TestWireFixtures:
    def test_committed_fixtures_match_the_live_snapshots(self):
        """Regenerate and compare. A difference means the wire shape moved.

        The fix is never to edit the JSON: run this test, commit what it wrote,
        and then make ``types/board.ts`` agree with it — in that order, so the
        TypeScript follows the server rather than the other way round.
        """
        FIXTURES.mkdir(parents=True, exist_ok=True)
        stale: list[str] = []
        for name, payload in _fixtures().items():
            path = FIXTURES / f"{name}.json"
            before = path.read_text(encoding="utf-8") if path.exists() else ""
            after = _write(name, payload)
            if before != after:
                stale.append(name)
        assert not stale, (
            f"wire fixtures regenerated: {', '.join(stale)}. The snapshot shape changed — "
            "commit frontend/src/test/fixtures/ and check types/board.ts still describes it."
        )

    def test_voting_snapshot_carries_no_vote_values(self):
        """The property the fixture exists to make legible, asserted directly.

        A `satisfies` check would happily accept a `value` on every seat; only
        this says the field must be *absent* while the round is open. Vote
        secrecy is the one poker invariant a rendering bug cannot recover from.
        """
        voting = _poker_snapshots()["poker.voting"]
        assert voting["phase"] == "voting"
        assert voting["votes"], "fixture must have seats, or this asserts nothing"
        for seat in voting["votes"]:
            assert "value" not in seat
            assert set(seat) == {"name", "avatar", "voted"}
        assert voting["distribution"] == {}
        assert voting["median"] is None and voting["suggestion"] is None
        # …and the viewer still sees their own, which is what makes the deck
        # able to show a selected card without leaking anyone else's.
        assert voting["mine_value"] == "3"

    def test_peek_projection_omits_round_internals(self):
        peek = _poker_snapshots()["ticket.peek"]
        for internal in ("accepted_votes", "ai_note", "duel_transcript", "initial_points", "source"):
            assert internal not in peek
