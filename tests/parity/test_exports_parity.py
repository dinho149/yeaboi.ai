"""Python ↔ Go parity for retro.build_export + poker.build_export (contracts/v1).

Both implementations run over the same frozen inputs; the whole wire result
must be equal (floats to 1e-9) AND every JSON object's key order must match —
``args`` is json.dumps-ed into the page boot payload, so its order is
contractual. On top of the structural diff, the markdown is compared as an
exact string and ``args`` as exact ``json.dumps`` bytes. Skipped when no
``yeaboi-core`` binary is available; ``make parity`` and CI run it unskipped.

The corpus is deliberately nastier than the unit fixtures: NBSP and U+2028
inside card text, pipes and Markdown emphasis passed through unnormalised, a
Turkish dotted İ and emoji (including a multi-codepoint ZWJ family) in
authors and reactions, an AI-origin card, a card with an unknown grid, an
empty grid, a zero-count reaction, every carried status plus an unknown one,
annotation variants, an editable anchor whose card id needs escape_value,
history rows past the 14-point cap with duplicate dates, a future row past
the cutoff and a null count; poker adds the 0.5/2.5/13.0/int-float/None
points spread, a skipped ticket with a stale final_points, the URL attack
set, a filtered empty vote, a duel transcript with \\r\\n / \\x85 / U+2028
terminators, pipes in summaries, and an empty-tickets report.
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

from tests.parity._diff import approx_equal, key_orders
from yeaboi.agent.state import Annotation, PokerReport, PokerTicketResult, PokerVote, RetroCard, RetroReport
from yeaboi.gocore.client import CoreClient
from yeaboi.poker import export as poker_export
from yeaboi.retro import export as retro_export
from yeaboi.retro.board import CARRIED_STATUSES

BINARY = os.environ.get("YEABOI_CORE_BIN") or shutil.which("yeaboi-core")

# Class-level, not module-level: the corpus self-guards are pure Python and
# must run in the ordinary suite too — a corpus that stops exercising what
# this file claims should fail `make test`, not just the parity job.
needs_binary = pytest.mark.skipif(
    not BINARY or not os.path.isfile(BINARY or ""),
    reason="yeaboi-core binary not available (run `make parity`)",
)


@pytest.fixture
def core():
    client = CoreClient(str(BINARY))
    try:
        client.hello()
        yield client
    finally:
        client.close()


# ── Corpus ────────────────────────────────────────────────────────────────

FAMILY = "\U0001f468‍\U0001f469‍\U0001f467‍\U0001f466"  # multi-codepoint ZWJ emoji


def _retro_report() -> RetroReport:
    return RetroReport(
        date="2026-08-14",
        session_id="sess-1",
        project_name="Apollo",
        sprint_name="",  # empty sprint_name — the title renders without the dash
        cards=(
            RetroCard(
                id="aa11",
                grid="went_well",
                text="uses | pipes *and* _emphasis_ **passthrough**",
                author="İlker",
                reactions=((FAMILY, 2), ("👍", 0)),  # zero-count reaction filtered everywhere
            ),
            RetroCard(id="bb22", grid="went_well", text="NBSP inside and U+2028 inside", author="🦄 Sam"),
            RetroCard(id="cc33", grid="didnt_go_well", text="plain card", author=""),
            RetroCard(id="dd44", grid="action_items", text="AI wrote this", author="AI", origin="ai"),
            RetroCard(id="ee.55 %x", grid="action_items", text="anchor needs escape_value", author="İlker"),
            RetroCard(id="ff66", grid="lost_grid", text="unknown grid — counted, never rendered"),
            RetroCard(id="gg77", grid="", text="empty grid — counted, never rendered"),
            # "demos" stays empty — the empty-grid branch renders "_No cards._".
        ),
        participants=(),  # empty participants — the header renders the dash
        carried_action_items=tuple(
            RetroCard(id=f"ca{i}", grid="action_items", text=f"carried {status or 'blank'}", status=status)
            for i, status in enumerate((*CARRIED_STATUSES, "abandoned", ""))
        ),
        annotations=(
            Annotation(kind="note", anchor="cards[id=aa11]", text="verified in prod", author="Ada", at="2026-08-14"),
            Annotation(kind="field", label="Risk owner", text="Ada", avatar="🦉"),
            Annotation(kind="field", label="", text="field with no label falls back to the note shape"),
            Annotation(kind="note", text=""),  # empty text — dropped from payload and markdown
        ),
    )


def _retro_history() -> list[dict]:
    rows = [{"retro_date": f"2026-07-{day:02d}", "card_count": day} for day in range(31, 14, -1)]  # 17 rows > cap
    return [
        {"retro_date": "2026-09-01", "card_count": 40},  # future — past the cutoff, dropped
        {"retro_date": "2026-08-07", "card_count": 6},
        {"retro_date": "2026-08-07", "card_count": 60},  # duplicate date — newest (first) wins
        {"retro_date": "2026-08-01", "card_count": None},  # null count — dropped
        {"retro_date": "", "card_count": 3},  # empty date — dropped
        *rows,
    ]


def _poker_report(tickets: tuple[PokerTicketResult, ...] | None = None) -> PokerReport:
    if tickets is None:
        tickets = (
            PokerTicketResult(
                key="PROJ-1",
                url="https://jira.example.com/browse/PROJ-1",
                summary="summary | with pipes",
                initial_points=3,  # int-float: widens to 3.0 on the wire
                final_points=5.0,
                estimated=True,
                votes=(
                    PokerVote(voter="İlker", value="5"),
                    PokerVote(voter="Empty", value=""),  # filtered from votes and the votes string
                    PokerVote(voter="🦄 Sam", value="8"),
                ),
                ai_note="Feels like a 5 | maybe an 8.",
            ),
            PokerTicketResult(
                key="PROJ-2",
                url="javascript:alert(1)",
                summary="skipped with stale final",
                initial_points=2.5,
                final_points=13.0,  # stale — the payload must force final to null
                estimated=False,
                duel_transcript="low opens.\r\nNEL line\x85LS line high closes.",
                duel_low="İlker (2)",
                duel_high="🦄 Sam (13)",
            ),
            PokerTicketResult(key="PROJ-3", url="JAVA\tSCRIPT:alert(1)", summary="smuggled scheme", estimated=False),
            PokerTicketResult(key="PROJ-4", url="//evil.example/x", summary="protocol-relative", estimated=False),
            PokerTicketResult(
                key="PROJ-5", url="browse/relative", summary="relative passes", initial_points=0.5, estimated=True
            ),
            PokerTicketResult(key="PROJ-6", url="mailto:pm@example.com", summary="mailto passes", estimated=False),
            PokerTicketResult(key="PROJ-7", url="\x00", summary="control-only", initial_points=13.0, estimated=True),
            PokerTicketResult(key="PROJ-8", url="", summary="no url", initial_points=None, estimated=True),
        )
    return PokerReport(
        date="2026-08-14",
        session_id="sess-2",
        project_name="Apollo",
        source="jira",
        scope_label="Sprint 42",
        tickets=tickets,
        participants=("İlker", "🦄 Sam"),
    )


def _poker_history() -> list[dict]:
    return [
        {"poker_date": "2026-08-07", "estimated_count": 2},
        {"poker_date": "2026-07-31", "estimated_count": 1},
    ]


def retro_inputs(*, history: list[dict] | None = None, editable: bool = True) -> dict:
    rows = _retro_history() if history is None else history
    return retro_export.build_retro_export_inputs(_retro_report(), history=rows, editable=editable)


def poker_inputs(**kwargs) -> dict:
    report = _poker_report(kwargs.pop("tickets", None))
    return poker_export.build_poker_export_inputs(report, history=kwargs.pop("history", _poker_history()))


# ── Comparison ────────────────────────────────────────────────────────────


def _go(core: CoreClient, method: str, inputs: dict) -> dict:
    result = core.request(method, inputs)
    assert result.get("contract_version") == 1
    return result


def _assert_match(py: dict, go: dict) -> None:
    # The exact compares first — they catch byte drift approx_equal forgives
    # (json.dumps renders the widened floats, escapes and key order).
    assert py["markdown"] == go["markdown"]
    assert json.dumps(py["args"], ensure_ascii=True) == json.dumps(go["args"], ensure_ascii=True)
    diffs = approx_equal(py, go, "result")
    assert not diffs, "\n".join(diffs[:40])
    py_orders, go_orders = key_orders(py), key_orders(go)
    order_diffs = [
        f"{path}: {py_orders.get(path)} != {go_orders.get(path)}"
        for path in sorted(set(py_orders) | set(go_orders))
        if py_orders.get(path) != go_orders.get(path)
    ]
    assert not order_diffs, "\n".join(order_diffs[:40])


@needs_binary
class TestRetroBuildExportParity:
    def test_full_corpus_matches(self, core):
        inputs = retro_inputs()
        _assert_match(retro_export.build_retro_export(inputs), _go(core, "retro.build_export", inputs))

    def test_non_editable_matches(self, core):
        inputs = retro_inputs(editable=False)
        _assert_match(retro_export.build_retro_export(inputs), _go(core, "retro.build_export", inputs))

    def test_one_history_row_trend_null_matches(self, core):
        # A same-day rerun: the single history row already carries the report
        # date, so the current point is not appended, one point is not a
        # trend, and the payload carries null (not an omitted key).
        inputs = retro_inputs(history=[{"retro_date": "2026-08-14", "card_count": 6}])
        py = retro_export.build_retro_export(inputs)
        assert py["args"]["report"]["trend"] is None
        _assert_match(py, _go(core, "retro.build_export", inputs))

    def test_empty_report_matches(self, core):
        inputs = retro_export.build_retro_export_inputs(RetroReport(), history=[], editable=False)
        py = retro_export.build_retro_export(inputs)
        assert py["args"]["report"]["trend"] is None  # None, not omitted
        _assert_match(py, _go(core, "retro.build_export", inputs))


@needs_binary
class TestPokerBuildExportParity:
    def test_full_corpus_matches(self, core):
        inputs = poker_inputs()
        _assert_match(poker_export.build_poker_export(inputs), _go(core, "poker.build_export", inputs))

    def test_empty_tickets_matches(self, core):
        inputs = poker_inputs(tickets=(), history=[])
        py = poker_export.build_poker_export(inputs)
        assert py["args"]["report"]["trend"] is None
        _assert_match(py, _go(core, "poker.build_export", inputs))


class TestCorpusSelfGuards:
    """Guard the corpus itself — pure Python, deliberately NOT behind
    ``needs_binary``: if a refactor mutes these signals, the parity runs
    above are no longer covering what this file's docstring says they cover,
    and that must fail the ordinary suite, not just the parity job."""

    def test_retro_corpus_exercises_the_signals_it_claims_to(self):
        inputs = retro_inputs()
        result = retro_export.build_retro_export(inputs)
        md, args = result["markdown"], result["args"]
        assert "uses \\ pipes *and* _emphasis_ **passthrough**" in md, "expected the pipe swap + emphasis passthrough"
        assert "NBSP inside and U+2028 inside" in md, "expected md_table_cell to collapse NBSP and U+2028"
        assert "- [ ] AI wrote this _(AI)_" in md, "expected the AI-origin task-list tag"
        assert "_No cards._" in md, "expected the empty demos grid"
        assert "- **[abandoned]**" in md, "expected the unknown carried status echoed"
        assert "- **[Pending]** carried blank" in md, "expected the blank status defaulted to Pending"
        assert f"{FAMILY} 2" in md and "👍 0" not in md, "expected the zero-count reaction filtered"
        assert "**Participants:** —" in md, "expected the empty-participants dash"
        assert "## Added by the team" in md, "expected annotations rendered"

        report = args["report"]
        assert len(report["columns"]) == 4, "columns are always exactly the four grids"
        rendered = [c["text"] for col in report["columns"] for c in col["cards"]]
        assert "unknown grid — counted, never rendered" not in rendered
        assert "empty grid — counted, never rendered" not in rendered
        assert dict(args["facts"])["CARDS"] == "7", "the unknown-grid and empty-grid cards still count"
        anchors = [c.get("anchor", "") for col in report["columns"] for c in col["cards"]]
        assert "cards[id=ee%2E55 %25x]" not in anchors, "escape_value must also quote the space"
        assert "cards[id=ee%2E55%20%25x]" in anchors, "expected the escape_value anchor"
        statuses = [row["status"] for row in report["carried"]]
        assert statuses == [*CARRIED_STATUSES, "abandoned", "pending"], "every carried status + unknown + defaulted"
        assert len(report["trend"]["points"]) == 14, "expected the 14-point cap over the 17-row history"
        days = [day for day, _ in report["trend"]["points"]]
        assert "2026-09-01" not in days, "the future row must fall to the cutoff"
        assert days == sorted(days), "points render oldest → newest"
        assert ["2026-08-07", 6.0] in report["trend"]["points"], "duplicate dates keep the newest row"
        assert len(report["annotations"]) == 3, "the empty-text annotation is dropped"

    def test_retro_non_editable_carries_no_edit_maps(self):
        result = retro_export.build_retro_export(retro_inputs(editable=False))
        dump = json.dumps(result["args"])
        assert '"edit"' not in dump, "a downloaded report stays byte-identical"

    def test_poker_corpus_exercises_the_signals_it_claims_to(self):
        inputs = poker_inputs()
        result = poker_export.build_poker_export(inputs)
        md, args = result["markdown"], result["args"]
        assert "[PROJ-1](https://jira.example.com/browse/PROJ-1)" in md
        assert "javascript:" not in md.lower(), "no unsafe scheme may survive into the markdown"
        assert "| PROJ-2 |" in md, "the javascript URL must drop to a bare key"
        assert "| PROJ-3 |" in md, "the tab-smuggled scheme must drop to a bare key"
        assert "| PROJ-4 |" in md, "the protocol-relative URL must drop to a bare key"
        assert "[PROJ-5](browse/relative)" in md, "a schemeless reference is inert and passes"
        assert "[PROJ-6](mailto:pm@example.com)" in md
        assert "| PROJ-7 |" in md, "a control-only URL strips to empty and drops"
        assert "summary \\ with pipes" in md, "expected the pipe swap in summaries"
        assert "İlker 5 · 🦄 Sam 8" in md, "expected the empty vote filtered from the votes string"
        assert "_skipped_" in md, "expected the skipped ticket"
        assert "> NEL line\n> LS line" in md, "expected NEL and LS to split duel transcript lines"

        tickets = args["report"]["tickets"]
        by_key = {t["key"]: t for t in tickets}
        assert by_key["PROJ-1"]["before"] == 3.0 and isinstance(by_key["PROJ-1"]["before"], float), "int widens"
        assert by_key["PROJ-2"]["final"] is None, "a skipped ticket's stale final_points must force null"
        assert [v["voter"] for v in by_key["PROJ-1"]["votes"]] == ["İlker", "🦄 Sam"], "empty vote filtered"
        assert "url" not in by_key["PROJ-2"] and "url" not in by_key["PROJ-7"] and "url" not in by_key["PROJ-8"]
        assert by_key["PROJ-5"]["url"] == "browse/relative"
        assert {"before": by_key["PROJ-5"]["before"]} == {"before": 0.5}
        assert by_key["PROJ-8"]["before"] is None
        nav_keys = [key for key, _ in args["nav"]]
        assert nav_keys == ["overview", "tickets", "ai", "duels"], "nav gains ai/duels only when present"
        assert dict(args["facts"])["ESTIMATED"] == "4/8"

    def test_empty_tickets_report_stays_minimal(self):
        result = poker_export.build_poker_export(poker_inputs(tickets=(), history=[]))
        nav_keys = [key for key, _ in result["args"]["nav"]]
        assert nav_keys == ["overview", "tickets"]
        assert dict(result["args"]["facts"])["ESTIMATED"] == "0/0"
