"""Rendering for provenance audit artifacts — one source of truth per surface.

Plaintext is what the CLI prints (and what ``--format text`` means); the Rich
form is what a TUI page would draw. Mirrors performance/render.py so no
surface re-implements the layout.
"""

from __future__ import annotations

import logging

from rich.console import Group
from rich.text import Text

from yeaboi.agent.state import ProvenanceAuditReport, ProvenanceDecisionRow, ProvenanceTrace

logger = logging.getLogger(__name__)

_ACCENT = "rgb(120,170,220)"  # trust-report blue; deliberately calm

_MAX_LISTED = 20


def _verdict_line(report: ProvenanceAuditReport) -> str:
    if report.total_records == 0:
        return "Chain: empty — nothing recorded yet"
    if report.chain_valid:
        return f"Chain: intact — {report.total_records} record(s), every link verified"
    return f"Chain: TAMPERED — {len(report.breaks)} break(s) across {report.total_records} record(s)"


def _row_line(row: ProvenanceDecisionRow) -> str:
    who = f"{row.agent_id} ({row.role})" if row.role else row.agent_id
    marker = "✕ " if row.record_kind == "invalidation" else ""
    return f"  • {marker}{row.entity_id} — {who}"


def format_audit_lines(report: ProvenanceAuditReport) -> list[str]:
    """Return the audit as plain-text lines (no ANSI)."""
    logger.info("provenance render: audit (plaintext)")
    lines = [
        "Provenance Audit",
        _verdict_line(report),
        f"Window: last {report.window_days} day(s) — {report.window_records} decision(s)",
        "",
    ]
    if report.breaks:
        lines.append("Breaks:")
        lines += [f"  • seq {seq}: {entity or '(row missing)'} — {reason}" for seq, entity, reason in report.breaks]
        lines.append("")
    if report.records_by_type:
        lines.append("Recorded decisions by type:")
        lines += [f"  • {kind}: {count}" for kind, count in report.records_by_type]
        lines.append("")
    if report.recent:
        shown = report.recent[:_MAX_LISTED]
        lines.append("Recent decisions (newest first):")
        lines += [_row_line(row) for row in shown]
        # Announced from window_records, not len(recent): the engine also caps
        # what it carries, and a silently truncated list reads as complete.
        if report.window_records > len(shown):
            lines.append(f"  …and {report.window_records - len(shown)} more in the window")
        lines.append("")
    if report.warnings:
        lines.append("⚠ Notices:")
        lines += [f"  • {w}" for w in report.warnings]
    return lines


def format_audit_rich(report: ProvenanceAuditReport, *, accent: str = _ACCENT) -> Group:
    """Return a Rich renderable for the audit."""
    logger.info("provenance render: audit (rich)")
    verdict_style = "bold green" if report.chain_valid and report.total_records else "bold red"
    if report.total_records == 0:
        verdict_style = "dim"
    body: list[Text] = [
        Text("Provenance Audit", style=f"bold {accent}"),
        Text(_verdict_line(report), style=verdict_style),
        Text(f"Window: last {report.window_days} day(s) — {report.window_records} decision(s)", style="dim"),
        Text(""),
    ]
    if report.breaks:
        body.append(Text("Breaks", style="bold red"))
        for seq, entity, reason in report.breaks:
            body.append(Text(f"  • seq {seq}: {entity or '(row missing)'} — {reason}", style="red"))
        body.append(Text(""))
    if report.records_by_type:
        body.append(Text("Recorded decisions by type", style=f"bold {accent}"))
        for kind, count in report.records_by_type:
            body.append(Text(f"  • {kind}: {count}"))
        body.append(Text(""))
    if report.recent:
        shown = report.recent[:_MAX_LISTED]
        body.append(Text("Recent decisions (newest first)", style=f"bold {accent}"))
        for row in shown:
            body.append(Text(_row_line(row)))
        if report.window_records > len(shown):
            body.append(Text(f"  …and {report.window_records - len(shown)} more in the window", style="dim"))
        body.append(Text(""))
    for w in report.warnings:
        body.append(Text(f"⚠ {w}", style="yellow"))
    return Group(*body)


def format_trace_lines(trace: ProvenanceTrace) -> list[str]:
    """Return a "why" trail as plain-text lines (no ANSI)."""
    logger.info("provenance render: trace (plaintext) — entity=%s", trace.entity_id)
    lines = [f"Why — {trace.entity_id}", ""]
    if not trace.found:
        lines += [f"  {w}" for w in trace.warnings]
        return lines
    for row in trace.records:
        who = f"{row.agent_id} ({row.role})" if row.role else row.agent_id
        kind = "retracted" if row.record_kind == "invalidation" else row.entity_type
        lines.append(f"  #{row.sequence_id} [{kind}] {row.entity_id}")
        lines.append(f"      by {who} at {row.timestamp}")
        if row.detail:
            lines.append(f"      {row.detail}")
        if row.inputs:
            lines.append(f"      evidence: {', '.join(row.inputs)}")
        lines.append("")
    return lines


def format_trace_rich(trace: ProvenanceTrace, *, accent: str = _ACCENT) -> Group:
    """Return a Rich renderable for the "why" trail."""
    logger.info("provenance render: trace (rich) — entity=%s", trace.entity_id)
    body: list[Text] = [Text(f"Why — {trace.entity_id}", style=f"bold {accent}"), Text("")]
    if not trace.found:
        for w in trace.warnings:
            body.append(Text(f"  {w}", style="yellow"))
        return Group(*body)
    for row in trace.records:
        who = f"{row.agent_id} ({row.role})" if row.role else row.agent_id
        kind = "retracted" if row.record_kind == "invalidation" else row.entity_type
        style = "red" if row.record_kind == "invalidation" else ""
        body.append(Text(f"  #{row.sequence_id} [{kind}] {row.entity_id}", style=f"bold {accent}"))
        body.append(Text(f"      by {who} at {row.timestamp}", style="dim"))
        if row.detail:
            body.append(Text(f"      {row.detail}", style=style))
        if row.inputs:
            body.append(Text(f"      evidence: {', '.join(row.inputs)}", style="dim"))
        body.append(Text(""))
    return Group(*body)
