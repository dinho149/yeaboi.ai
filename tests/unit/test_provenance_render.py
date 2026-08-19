"""Tests for src/yeaboi/provenance/render.py — audit + trace rendering."""

from rich.console import Console

from yeaboi.agent.state import ProvenanceAuditReport, ProvenanceDecisionRow, ProvenanceTrace
from yeaboi.provenance.render import (
    format_audit_lines,
    format_audit_rich,
    format_trace_lines,
    format_trace_rich,
)

ROW = ProvenanceDecisionRow(
    entity_id="standup:2026-08-16:practice:wip-sprawl:alice",
    entity_type="practice-signal",
    agent_id="habits.wip-sprawl",
    role="generator",
    timestamp="2026-08-16T10:00:00+00:00",
    detail="five changes in flight",
    inputs=("pr|https://g/41",),
    sequence_id=3,
)


def _render(renderable) -> str:
    console = Console(width=120, force_terminal=False)
    with console.capture() as cap:
        console.print(renderable)
    return cap.get()


class TestAuditRender:
    def test_intact_chain_reads_as_intact(self):
        report = ProvenanceAuditReport(
            chain_valid=True,
            total_records=5,
            window_records=3,
            records_by_type=(("practice-signal", 4), ("conflict", 1)),
            recent=(ROW,),
        )
        lines = format_audit_lines(report)
        text = "\n".join(lines)
        assert "intact" in text
        assert "practice-signal: 4" in text
        assert ROW.entity_id in text
        assert "TAMPERED" not in text
        assert _render(format_audit_rich(report))  # renders without raising

    def test_tampered_chain_shouts(self):
        report = ProvenanceAuditReport(
            chain_valid=False,
            total_records=5,
            breaks=((2, "e2", "checksum_mismatch"),),
            warnings=("Chain verification FAILED: 1 break(s) detected.",),
        )
        text = "\n".join(format_audit_lines(report))
        assert "TAMPERED" in text
        assert "seq 2: e2 — checksum_mismatch" in text
        assert "FAILED" in _render(format_audit_rich(report))

    def test_deleted_row_break_names_the_gap_not_an_empty_string(self):
        report = ProvenanceAuditReport(chain_valid=False, total_records=2, breaks=((3, "", "chain_break"),))
        text = "\n".join(format_audit_lines(report))
        assert "(row missing)" in text

    def test_empty_chain_is_calm(self):
        report = ProvenanceAuditReport(warnings=("No decisions recorded yet — run a standup.",))
        text = "\n".join(format_audit_lines(report))
        assert "empty" in text
        assert "No decisions recorded" in text


class TestTraceRender:
    def test_trail_shows_who_when_and_evidence(self):
        trace = ProvenanceTrace(entity_id=ROW.entity_id, found=True, records=(ROW,))
        text = "\n".join(format_trace_lines(trace))
        assert "habits.wip-sprawl (generator)" in text
        assert "evidence: pr|https://g/41" in text
        assert _render(format_trace_rich(trace))

    def test_retraction_is_marked(self):
        tombstone = ProvenanceDecisionRow(
            entity_id=ROW.entity_id, record_kind="invalidation", agent_id="alice", role="invalidator"
        )
        trace = ProvenanceTrace(entity_id=ROW.entity_id, found=True, records=(tombstone,))
        assert "[retracted]" in "\n".join(format_trace_lines(trace))

    def test_not_found_relays_the_warning(self):
        trace = ProvenanceTrace(entity_id="ghost", found=False, warnings=("No decision recorded for 'ghost'.",))
        text = "\n".join(format_trace_lines(trace))
        assert "No decision recorded" in text
