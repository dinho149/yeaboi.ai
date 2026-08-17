"""Tests for the poker exporter (poker/export.py) — the Markdown, and the payload.

The HTML is drawn by ``frontend/src/export`` from a JSON island, so what this
module can assert about it is the *payload*: that every field of the session
reached it, correctly shaped. How a skipped ticket looks, or how the votes lay
out, is asserted in ``Poker.test.tsx``, where the component actually runs.
"""

import json
import logging

from tests._pages import assert_self_contained, island
from yeaboi.agent.state import PokerReport, PokerTicketResult, PokerVote
from yeaboi.poker import export as poker_export
from yeaboi.poker.export import (
    build_poker_export,
    build_poker_export_inputs,
    build_poker_html,
    build_poker_markdown,
    export_poker,
    go_build_poker_export,
)


def _report() -> PokerReport:
    return PokerReport(
        date="2026-07-25",
        session_id="sess-1",
        project_name="Proj",
        source="jira",
        scope_label="Sprint 42",
        tickets=(
            PokerTicketResult(
                key="PROJ-1",
                url="https://x.atlassian.net/browse/PROJ-1",
                summary="Add <script>alert(1)</script> login",
                description="Details",
                initial_points=None,
                final_points=5.0,
                estimated=True,
                votes=(PokerVote("Alex", "🦊", "5"), PokerVote("Sam", "🐙", "8")),
                ai_note="The 8 voter sees <b>risk</b>",
                duel_transcript="Alex (voted 5) — turn 1:\nIt's a <script>simple</script> endpoint.",
                duel_low="Alex (5)",
                duel_high="Sam (8)",
            ),
            PokerTicketResult(key="PROJ-2", summary="Skipped one", initial_points=3.0),
        ),
        participants=("Alex", "Sam"),
        generated_at="2026-07-25T10:00:00+00:00",
    )


class TestMarkdown:
    def test_content(self):
        md = build_poker_markdown(_report())
        assert "# Planning Poker — Sprint 42" in md
        assert "**Estimated:** 1/2 tickets" in md
        assert "[PROJ-1](https://x.atlassian.net/browse/PROJ-1)" in md
        assert "Alex 5 · Sam 8" in md
        assert "_skipped_" in md  # unestimated ticket
        assert "| 3 |" in md  # initial points rendered without trailing .0
        assert "## AI perspectives" in md
        assert "Alex, Sam" in md

    def test_no_ai_section_when_no_notes(self):
        report = PokerReport(tickets=(PokerTicketResult(key="X-1"),))
        assert "AI perspectives" not in build_poker_markdown(report)

    def test_duel_section(self):
        md = build_poker_markdown(_report())
        assert "## Duels" in md
        assert "**PROJ-1** — Alex (5) vs Sam (8)" in md
        # Transcript is block-quoted, line by line.
        assert "> Alex (voted 5) — turn 1:" in md

    def test_no_duel_section_without_transcripts(self):
        report = PokerReport(tickets=(PokerTicketResult(key="X-1"),))
        assert "## Duels" not in build_poker_markdown(report)


class TestHtml:
    def test_untrusted_text_travels_as_data_not_markup(self):
        html = build_poker_html(_report())
        # The probe reaches the payload, JSON-escaped — `<` inside a JSON island
        # is written `<` so the string can never close the <script> around it.
        assert "<script>alert(1)</script>" not in html
        ticket = island(html)["report"]["tickets"][0]
        assert ticket["summary"] == "Add <script>alert(1)</script> login"
        assert ticket["aiNote"] == "The 8 voter sees <b>risk</b>"
        assert "<script>simple</script>" in ticket["duel"]["transcript"]

    def test_structure(self):
        html = build_poker_html(_report())
        assert html.startswith("<!DOCTYPE html>")
        boot = island(html)
        assert boot["chrome"]["title"] == "Planning Poker — Sprint 42"
        assert boot["chrome"]["wordmark"] == "poker"
        assert boot["report"]["kind"] == "poker"

    def test_facts_name_the_session(self):
        facts = dict(tuple(f) for f in island(build_poker_html(_report()))["chrome"]["facts"])
        assert facts == {
            "SOURCE": "jira",
            "SCOPE": "Sprint 42",
            "DATE": "2026-07-25",
            "ESTIMATED": "1/2",
        }

    def test_noscript_names_the_markdown_twin(self):
        # The page draws client-side; with scripting off the sibling .md is the
        # whole content, so the note has to name a file that actually exists.
        assert "poker-2026-07-25.md" in build_poker_html(_report())

    def test_duel_payload(self):
        ticket = island(build_poker_html(_report()))["report"]["tickets"][0]
        assert ticket["duel"]["low"] == "Alex (5)"
        assert ticket["duel"]["high"] == "Sam (8)"

    def test_skipped_ticket_carries_no_final(self):
        # A number beside "skipped" is a contradiction; the payload never offers one.
        skipped = island(build_poker_html(_report()))["report"]["tickets"][1]
        assert skipped["estimated"] is False
        assert skipped["final"] is None
        assert skipped["before"] == 3.0

    def test_unsafe_ticket_url_is_dropped(self):
        report = PokerReport(tickets=(PokerTicketResult(key="X-1", url="javascript:alert(1)"),))
        assert "url" not in island(build_poker_html(report))["report"]["tickets"][0]

    def test_votes_without_a_value_are_not_votes(self):
        report = PokerReport(
            tickets=(PokerTicketResult(key="X-1", votes=(PokerVote("Alex", "🦊", ""), PokerVote("Sam", "🐙", "5"))),)
        )
        votes = island(build_poker_html(report))["report"]["tickets"][0]["votes"]
        assert votes == [{"voter": "Sam", "value": "5"}]


class TestExportPoker:
    def test_writes_both_files(self, tmp_path, monkeypatch):
        out_dir = tmp_path / "exports"
        monkeypatch.setattr("yeaboi.paths.get_poker_export_dir", lambda key: out_dir / key)
        (out_dir / "proj").mkdir(parents=True)
        paths = export_poker(_report())
        assert paths["markdown"].name == "poker-2026-07-25.md"
        assert paths["html"].name == "poker-2026-07-25.html"
        assert paths["markdown"].read_text(encoding="utf-8").startswith("# Planning Poker")
        assert "<!DOCTYPE html>" in paths["html"].read_text(encoding="utf-8")


def _history(*rows):
    """Newest-first rows mirroring PokerStore.get_history output."""
    return [
        {
            "id": i,
            "run_at": f"{d}T15:00:00",
            "poker_date": d,
            "project_name": "Proj",
            "source": "jira",
            "scope_label": "Sprint",
            "ticket_count": t,
            "estimated_count": e,
        }
        for i, (d, t, e) in enumerate(rows)
    ]


class TestSharedDesignSystem:
    def test_poker_html_uses_shared_theme(self):
        html = build_poker_html(_report())
        assert 'data-theme="midnight"' in html
        assert 'data-mode="poker"' in html  # the accent, set before first paint
        assert_self_contained(html)

    def test_nav_lists_only_the_sections_that_exist(self):
        nav = [tuple(entry) for entry in island(build_poker_html(_report()))["chrome"]["nav"]]
        assert nav == [
            ("overview", "Overview"),
            ("tickets", "Tickets"),
            ("ai", "AI perspectives"),
            ("duels", "Duels"),
        ]

    def test_nav_drops_absent_sections(self):
        report = PokerReport(tickets=(PokerTicketResult(key="X-1"),))
        nav = [tuple(entry) for entry in island(build_poker_html(report))["chrome"]["nav"]]
        assert nav == [("overview", "Overview"), ("tickets", "Tickets")]


class TestTrend:
    def test_trend_from_history(self):
        history = _history(("2026-07-25", 8, 5), ("2026-07-11", 10, 9), ("2026-06-27", 7, 7))
        trend = island(build_poker_html(_report(), history=history))["report"]["trend"]
        assert trend["title"] == "Estimation trend"
        assert trend["points"][0] == ["2026-06-27", 7]  # oldest first

    def test_no_history_no_trend(self):
        # None, not an absent key — "the server decided there is no chart" has
        # to be distinguishable from "the field is missing".
        assert island(build_poker_html(_report()))["report"]["trend"] is None

    def test_self_contained_with_history(self):
        html = build_poker_html(_report(), history=_history(("2026-07-25", 8, 5), ("2026-07-11", 10, 9)))
        assert_self_contained(html)

    def test_export_forwards_history(self, tmp_path, monkeypatch):
        out_dir = tmp_path / "exports"
        monkeypatch.setattr("yeaboi.paths.get_poker_export_dir", lambda key: out_dir / key)
        (out_dir / "proj").mkdir(parents=True)
        paths = export_poker(_report(), history=_history(("2026-07-25", 8, 5), ("2026-07-11", 10, 9)))
        assert island(paths["html"].read_text(encoding="utf-8"))["report"]["trend"] is not None


# ---------------------------------------------------------------------------
# The build-export seam
# ---------------------------------------------------------------------------


def _inputs(report=None, **kwargs):
    return build_poker_export_inputs(report if report is not None else _report(), **kwargs)


class TestExportInputs:
    def test_timestamps_are_captured_once_and_agree(self):
        inputs = _inputs()
        # Both stamps come from one datetime.now(); the date must be the ts's
        # own day, or a document could carry a footer from the day before.
        assert inputs["generated_ts"].startswith(inputs["generated_date"])

    def test_inputs_are_plain_json(self):
        # The json round-trip is what makes the wire params byte-identical to
        # what a stored report deserializes from — tuples become lists here.
        inputs = _inputs()
        assert inputs == json.loads(json.dumps(inputs))
        assert isinstance(inputs["report"]["tickets"], list)
        assert inputs["report"]["tickets"][0]["votes"][0]["voter"] == "Alex"

    def test_poker_has_no_editable_flag(self):
        # Poker has no editable share, so the param never existed on this side.
        assert "editable" not in _inputs()

    def test_history_rows_travel_verbatim(self):
        history = _history(("2026-07-25", 8, 5), ("2026-07-11", 10, 9))
        assert _inputs(history=history)["history"] == history


class TestReferenceImplementation:
    def test_round_trip_reproduces_the_document(self):
        # The reference implementation rebuilds through the store deserializer;
        # what it renders must match rendering the report directly.
        report = _report()
        result = build_poker_export(_inputs(report))
        assert result["markdown"].splitlines()[0] == build_poker_markdown(report).splitlines()[0]
        assert result["args"]["report"]["kind"] == "poker"

    def test_pinned_timestamps_make_the_document_a_golden(self):
        inputs = {**_inputs(), "generated_ts": "2026-07-25 14:05", "generated_date": "2026-07-25"}
        first = build_poker_export(inputs)
        assert first == build_poker_export(inputs)  # deterministic, byte for byte
        assert "· 2026-07-25 14:05_" in first["markdown"]
        assert first["args"]["footer"] == "Generated by yeaboi.ai • 2026-07-25"

    def test_result_carries_the_contract_version(self):
        from yeaboi.gocore.client import CONTRACT_VERSION

        assert build_poker_export(_inputs())["contract_version"] == CONTRACT_VERSION

    def test_key_order_is_contractual(self):
        # `args` is json.dumps-ed into the page boot payload, so key order is
        # part of the wire, not a detail of this dict.
        result = build_poker_export(_inputs())
        assert list(result) == ["contract_version", "markdown", "args"]
        assert list(result["args"]["report"]) == ["kind", "tickets", "participants", "trend"]

    def test_float_points_survive_the_round_trip(self):
        # int-valued floats render without the trailing .0, and the json
        # round-trip must not turn 5.0 into 5 on the way through.
        report = PokerReport(
            date="2026-07-25",
            tickets=(PokerTicketResult(key="X-1", initial_points=0.5, final_points=13.0, estimated=True),),
        )
        result = build_poker_export(_inputs(report))
        assert result["args"]["report"]["tickets"][0]["final"] == 13.0
        assert "| 0.5 | 13 |" in result["markdown"]


class TestGoDispatch:
    """poker.build_export dispatch: Go results win; any failure → Python."""

    def test_no_client_means_python_path(self, monkeypatch):
        monkeypatch.setattr("yeaboi.gocore.get_client", lambda: None)
        assert go_build_poker_export(_inputs()) is None

    def test_client_construction_failure_returns_none(self, monkeypatch):
        def boom():
            raise RuntimeError("discovery exploded")

        monkeypatch.setattr("yeaboi.gocore.get_client", boom)
        assert go_build_poker_export(_inputs()) is None

    def test_core_error_returns_none_for_fallback(self, monkeypatch):
        from yeaboi.gocore import CoreError

        class BrokenClient:
            def request(self, *args, **kwargs):
                raise CoreError("sidecar exploded")

        monkeypatch.setattr("yeaboi.gocore.get_client", lambda: BrokenClient())
        assert go_build_poker_export(_inputs()) is None

    def test_empty_markdown_is_malformed(self, monkeypatch):
        canned = build_poker_export(_inputs())

        class FakeClient:
            def request(self, method, params, on_progress=None, timeout=None):
                return {**canned, "markdown": ""}

        monkeypatch.setattr("yeaboi.gocore.get_client", lambda: FakeClient())
        assert go_build_poker_export(_inputs()) is None

    def test_wrong_report_kind_is_malformed(self, monkeypatch):
        canned = build_poker_export(_inputs())
        wrong = {**canned, "args": {**canned["args"], "report": {**canned["args"]["report"], "kind": "retro"}}}

        class FakeClient:
            def request(self, method, params, on_progress=None, timeout=None):
                return wrong

        monkeypatch.setattr("yeaboi.gocore.get_client", lambda: FakeClient())
        assert go_build_poker_export(_inputs()) is None

    def test_skewed_args_key_set_is_malformed(self, monkeypatch):
        # The args are splatted into keyword-only `export_page`: an unknown key
        # would TypeError after the .md was written, a missing one would drop
        # chrome. Either shape reads as malformed and falls back to Python.
        canned = build_poker_export(_inputs())
        extra = {**canned["args"], "surprise": "1"}
        short = {k: v for k, v in canned["args"].items() if k != "nav"}
        for args in (extra, short):

            class FakeClient:
                def request(self, method, params, on_progress=None, timeout=None):
                    return {**canned, "args": args}

            monkeypatch.setattr("yeaboi.gocore.get_client", lambda client=FakeClient(): client)
            assert go_build_poker_export(_inputs()) is None

    def test_ticket_count_mismatch_is_malformed(self, monkeypatch):
        canned = build_poker_export(_inputs())
        report = {**canned["args"]["report"], "tickets": canned["args"]["report"]["tickets"][:1]}

        class FakeClient:
            def request(self, method, params, on_progress=None, timeout=None):
                return {**canned, "args": {**canned["args"], "report": report}}

        monkeypatch.setattr("yeaboi.gocore.get_client", lambda: FakeClient())
        # A short ticket list would render a different session, not a slower one.
        assert go_build_poker_export(_inputs()) is None

    def test_good_results_are_returned_verbatim(self, monkeypatch, caplog):
        canned = build_poker_export(_inputs())

        class FakeClient:
            def request(self, method, params, on_progress=None, timeout=None):
                assert method == "poker.build_export"
                return canned

        monkeypatch.setattr("yeaboi.gocore.get_client", lambda: FakeClient())
        with caplog.at_level(logging.INFO, logger="yeaboi.poker.export"):
            assert go_build_poker_export(_inputs()) == canned
        assert "poker.build_export served by the sidecar" in caplog.text

    def test_the_page_renders_from_a_sidecar_document(self, monkeypatch):
        # The seam's whole point: a served document draws the same page.
        canned = build_poker_export(_inputs())

        class FakeClient:
            def request(self, method, params, on_progress=None, timeout=None):
                return canned

        monkeypatch.setattr("yeaboi.gocore.get_client", lambda: FakeClient())
        html = build_poker_html(_report())
        assert island(html)["report"]["tickets"] == canned["args"]["report"]["tickets"]

    def test_export_falls_back_without_a_sidecar(self, tmp_path, monkeypatch):
        # The Python path stays complete: no binary, both artifacts still written.
        out_dir = tmp_path / "exports"
        monkeypatch.setattr("yeaboi.gocore.get_client", lambda: None)
        monkeypatch.setattr("yeaboi.paths.get_poker_export_dir", lambda key: out_dir / key)
        (out_dir / "proj").mkdir(parents=True)
        paths = export_poker(_report())
        assert paths["markdown"].read_text(encoding="utf-8").startswith("# Planning Poker")
        assert "<!DOCTYPE html>" in paths["html"].read_text(encoding="utf-8")

    def test_both_artifacts_come_from_one_dispatch(self, tmp_path, monkeypatch):
        # One seam call per export — the .md and the .html can never disagree
        # about which side built them.
        calls = []
        out_dir = tmp_path / "exports"

        def _counting(report, *, history=()):
            calls.append(report)
            return build_poker_export(build_poker_export_inputs(report, history=history))

        monkeypatch.setattr(poker_export, "_export_doc", _counting)
        monkeypatch.setattr("yeaboi.paths.get_poker_export_dir", lambda key: out_dir / key)
        (out_dir / "proj").mkdir(parents=True)
        export_poker(_report())
        assert len(calls) == 1
