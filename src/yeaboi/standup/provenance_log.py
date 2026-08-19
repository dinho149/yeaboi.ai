"""Decision records for one standup run — the audit trail behind the report.

Every deterministic signal the standup surfaces (or suppresses) becomes one
``DecisionRecord`` in the tamper-evident chain (yeaboi.provenance): practice
signals with their evidence handles, blocker signals, the confidence verdict
with the adjustments its rationale names, the practice cases an LLM
adjudicator excused, and any cross-source conflict cards. "Why did it say
that?" — and "why did it NOT say that?" — both get durable, verifiable
answers.

Engine-layer only, built from the aggregate's wire output, so the
Go-mirrored deterministic core is untouched. Recording is best-effort by
contract: the engine appends a report warning when the chain write fails,
because a standup must never fail over its own audit trail — but an audit
trail that fails silently is not one.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from yeaboi.agent.state import ConflictCard
from yeaboi.provenance import DecisionRecord, ProvenanceChain

logger = logging.getLogger(__name__)

_SOURCE = "standup"


def _entity(date_str: str, *parts: str) -> str:
    return ":".join(("standup", date_str, *[p for p in parts if p]))


def build_decision_records(
    *,
    result: Mapping,
    date_str: str,
    session_id: str,
    dropped_case_ids: Sequence[str] = (),
    adjudicator_id: str = "",
    conflict_cards: Sequence[ConflictCard] = (),
) -> list[DecisionRecord]:
    """One record per surfaced or suppressed signal, deterministic order."""
    activity = f"standup-run:{session_id}:{date_str}"
    records: list[DecisionRecord] = []

    # Practice signals — the rule is the agent, the change handles and
    # evidence labels are the inputs the accusation rests on.
    for member in sorted(result.get("practices") or {}):
        for signal in result["practices"][member] or ():
            rule = str(signal.get("rule") or "")
            if not rule:
                continue
            records.append(
                DecisionRecord(
                    entity_id=_entity(date_str, "practice", rule, member),
                    entity_type="practice-signal",
                    activity_id=activity,
                    agent_id=f"habits.{rule}",
                    role="generator",
                    source_document=_SOURCE,
                    detail=str(signal.get("detail") or ""),
                    inputs=tuple(str(h) for h in (signal.get("handles") or ()))
                    or tuple(str(e[0]) for e in (signal.get("evidence") or ()) if len(e) == 2),
                    extras=(("member", member), ("repeat", str(bool(signal.get("repeat"))).lower())),
                )
            )

    # Blocker signals — prose lines from insights.detect_blocker_signals.
    for member in sorted(result.get("blocker_signals") or {}):
        lines = tuple(str(line) for line in result["blocker_signals"][member] or ())
        if not lines:
            continue
        records.append(
            DecisionRecord(
                entity_id=_entity(date_str, "blocker", member),
                entity_type="blocker-signal",
                activity_id=activity,
                agent_id="insights.blocker-signals",
                role="generator",
                source_document=_SOURCE,
                detail=" • ".join(lines),
                extras=(("member", member), ("signals", str(len(lines)))),
            )
        )

    # The confidence verdict — the one signal whose silent adjustments
    # (silence penalty, decline dampening) only ever lived in prose. The
    # rationale IS the prose; the extras carry the arithmetic outcome.
    progress = result.get("progress") or {}
    if progress:
        records.append(
            DecisionRecord(
                entity_id=_entity(date_str, "confidence"),
                entity_type="confidence",
                activity_id=activity,
                agent_id="confidence.compute",
                role="generator",
                source_document=_SOURCE,
                confidence=min(1.0, max(0.0, int(progress.get("confidence_pct") or 0) / 100)),
                detail=str(progress.get("confidence_rationale") or ""),
                extras=(
                    ("pct", str(int(progress.get("confidence_pct") or 0))),
                    ("label", str(progress.get("confidence_label") or "")),
                    ("delta", str(int(progress.get("confidence_delta") or 0))),
                    ("trend", str(progress.get("confidence_trend") or "")),
                ),
            )
        )

    # Adjudication drops — the practice cases an LLM excused. Recording the
    # suppression is the point: a signal that quietly never fired is exactly
    # the decision a reader could never audit before.
    for case_id in dropped_case_ids:
        records.append(
            DecisionRecord(
                entity_id=_entity(date_str, "adjudication", str(case_id)),
                entity_type="adjudication-drop",
                activity_id=activity,
                agent_id=adjudicator_id or "practice-adjudicator",
                role="suppressor",
                source_document=_SOURCE,
                detail="Practice case excused by the adjudicator, generalising from the team's past feedback.",
                inputs=(str(case_id),),
            )
        )

    # Conflict cards — both claims ride as inputs (their evidence refs).
    for card in conflict_cards:
        records.append(
            DecisionRecord(
                entity_id=_entity(date_str, "conflict", card.fingerprint),
                entity_type="conflict",
                activity_id=activity,
                agent_id="conflicts.status",
                role="generator",
                source_document=_SOURCE,
                detail=card.detail,
                inputs=tuple(ref for _, _, _, ref in card.claims if ref),
                extras=(("severity", card.severity), ("entity", card.entity_id)),
            )
        )

    return records


def record_run(
    db_path,
    *,
    result: Mapping,
    date_str: str,
    session_id: str,
    dropped_case_ids: Sequence[str] = (),
    adjudicator_id: str = "",
    conflict_cards: Sequence[ConflictCard] = (),
) -> int:
    """Append this run's decision records to the chain; returns how many.

    Raises on failure — the engine catches, logs, and turns it into a report
    warning, so the caller decides what a failed audit write means.
    """
    records = build_decision_records(
        result=result,
        date_str=date_str,
        session_id=session_id,
        dropped_case_ids=dropped_case_ids,
        adjudicator_id=adjudicator_id,
        conflict_cards=conflict_cards,
    )
    if not records:
        return 0
    with ProvenanceChain(db_path) as chain:
        chain.append_all(records)
    logger.info("standup provenance: %d decision record(s) chained", len(records))
    return len(records)
