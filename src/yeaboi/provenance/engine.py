"""The provenance audit engine: verify the chain, summarise what it holds.

Headless pipeline (surface-parity contract): the TUI, CLI, and MCP are thin
adapters over ``run_provenance_audit`` and ``trace_entity``. Deliberately
LLM-free end to end — a trust report that needed a model to read the tamper
log would undermine the thing it reports on — so there is no fallback tier:
the deterministic path is the only path.

What the audit answers, in order of importance:

1. **Has anyone edited the record?** ``chain_valid`` re-verifies the whole
   chain (checksums, links, sequence arithmetic), never just the window.
2. **What was decided lately?** The window's records, newest first, capped.
3. **What kinds of decisions exist at all?** Whole-chain counts by type.

``trace_entity`` is the click-through "why": one entity's records plus the
latest record behind each of its inputs.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from yeaboi.agent.state import ProvenanceAuditReport, ProvenanceDecisionRow, ProvenanceTrace
from yeaboi.provenance.records import DecisionRecord
from yeaboi.provenance.store import ProvenanceChain

logger = logging.getLogger(__name__)

_RECENT_CAP = 50


def _resolve_db_path(db_path):
    if db_path is not None:
        return db_path
    from yeaboi.paths import get_db_path

    return get_db_path()


def _row(record: DecisionRecord) -> ProvenanceDecisionRow:
    return ProvenanceDecisionRow(
        entity_id=record.entity_id,
        entity_type=record.entity_type,
        record_kind=record.record_kind,
        agent_id=record.agent_id,
        role=record.role,
        timestamp=record.timestamp,
        detail=record.detail,
        inputs=record.inputs,
        sequence_id=record.sequence_id,
    )


def run_provenance_audit(
    *,
    window_days: int = 30,
    db_path=None,
    today: date | None = None,
) -> ProvenanceAuditReport:
    """Verify the decision chain and report the window's activity."""
    today = today or date.today()
    since = (today - timedelta(days=window_days)).isoformat()
    logger.info("run_provenance_audit: window_days=%d since=%s", window_days, since)

    warnings: list[str] = []
    with ProvenanceChain(_resolve_db_path(db_path)) as chain:
        verdict = chain.verify()
        total = verdict.total_records
        recent = chain.records(since=since, limit=_RECENT_CAP)
        window_count = chain.count_since(since)
        by_type = chain.counts_by_type()

    if not verdict.valid:
        warnings.append(
            f"Chain verification FAILED: {len(verdict.broken)} break(s) detected. "
            "A record was edited, deleted, or renumbered after it was written."
        )
    if total == 0:
        warnings.append(
            "No decisions recorded yet — run a standup or a performance workflow and the audit trail starts itself."
        )
    # The recent-list cap is NOT a warning: it is structural (window_records
    # vs len(recent)) and the renderers announce it. `--strict` promises to
    # fire on a broken or empty chain, and a healthy busy chain crosses the
    # cap within days — a truncation notice in `warnings` would make strict
    # mode fail exactly when the feature is being used.

    report = ProvenanceAuditReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        window_days=window_days,
        chain_valid=verdict.valid,
        total_records=total,
        window_records=window_count,
        records_by_type=tuple(by_type.items()),
        recent=tuple(_row(r) for r in recent),
        breaks=tuple((b.sequence_id, b.entity_id, b.reason) for b in verdict.broken),
        warnings=tuple(warnings),
    )
    logger.info(
        "run_provenance_audit complete: valid=%s total=%d window=%d breaks=%d",
        report.chain_valid,
        report.total_records,
        report.window_records,
        len(report.breaks),
    )
    return report


def trace_entity(entity_id: str, *, depth: int = 2, db_path=None) -> ProvenanceTrace:
    """The "why" trail behind one recorded decision."""
    logger.info("trace_entity: %s depth=%d", entity_id, depth)
    with ProvenanceChain(_resolve_db_path(db_path)) as chain:
        records = chain.trace(entity_id, depth=depth)
    if not records:
        return ProvenanceTrace(
            entity_id=entity_id,
            found=False,
            warnings=(f"No decision recorded for '{entity_id}'. Entity ids are listed by the audit.",),
        )
    return ProvenanceTrace(entity_id=entity_id, found=True, records=tuple(_row(r) for r in records))
