"""Decision records for performance workflows — the compliance trail.

Performance is where an auditable "why" matters most: a 1:1 prep, a completed
1:1, and a six-month review are decisions *about a person*, and six months
later "what evidence was this review based on?" deserves a better answer than
a prompt nobody kept. Each workflow appends one ``DecisionRecord`` to the
tamper-evident chain (yeaboi.provenance) naming the evidence it rested on —
ticket keys, carried action items, the 1:1 history — never the content of a
transcript or a note, which stays in the performance store where it belongs.

Best-effort by the same contract as the standup's audit trail: the engine
catches a failed chain write and appends a warning to the artifact, because a
review must never fail over its own audit trail — and an audit trail that
fails silently is not one.
"""

from __future__ import annotations

import logging

from yeaboi.agent.state import EngineerActivity, OneOnOnePrep, OneOnOneRecord, SixMonthReview
from yeaboi.provenance import DecisionRecord, ProvenanceChain

logger = logging.getLogger(__name__)

_SOURCE = "performance"


def _activity_inputs(activity: EngineerActivity | None) -> tuple[str, ...]:
    """The ticket keys the decision rested on — keys, never ticket content."""
    if activity is None:
        return ()
    return tuple(f"{story.source}:{story.key}" for story in activity.stories if story.key)


def _append(db_path, record: DecisionRecord) -> None:
    with ProvenanceChain(db_path) as chain:
        chain.append(record)
    logger.info("performance provenance: chained %s", record.entity_id)


def record_prep(db_path, prep: OneOnOnePrep, *, activity: EngineerActivity | None, used_llm: bool) -> None:
    """One record per generated 1:1 prep, naming its evidence."""
    _append(
        db_path,
        DecisionRecord(
            entity_id=f"performance:{prep.engineer}:prep:{prep.date}",
            entity_type="one-on-one-prep",
            activity_id=f"performance-run:{prep.date}",
            agent_id="performance.prep" if used_llm else "performance.prep-fallback",
            role="generator",
            source_document=_SOURCE,
            detail=f"1:1 prep for {prep.engineer}: {len(prep.talking_points)} talking point(s), "
            f"{len(prep.carried_action_items)} carried action item(s).",
            # Ticket keys only. Carried action items are distilled from a 1:1
            # transcript, and the chain is the one store that can never be
            # scrubbed — they ride as a count, exactly like record_completion.
            inputs=_activity_inputs(activity),
            extras=(
                ("engineer", prep.engineer),
                ("talking_points", str(len(prep.talking_points))),
                ("carried_actions", str(len(prep.carried_action_items))),
                ("llm", "yes" if used_llm else "fallback"),
            ),
        ),
    )


def record_completion(db_path, record: OneOnOneRecord, *, used_llm: bool) -> None:
    """One record per completed 1:1. The transcript itself never enters the
    chain — only that a meeting happened and what came out of it, by count."""
    _append(
        db_path,
        DecisionRecord(
            entity_id=f"performance:{record.engineer}:one-on-one:{record.date}",
            entity_type="one-on-one",
            activity_id=f"performance-run:{record.date}",
            agent_id="performance.completion" if used_llm else "performance.completion-fallback",
            role="generator",
            source_document=_SOURCE,
            detail=f"1:1 with {record.engineer} completed: {len(record.action_items)} action item(s), "
            f"{len(record.highlights)} highlight(s).",
            inputs=(f"performance:{record.engineer}:prep:{record.date}",),
            extras=(
                ("engineer", record.engineer),
                ("action_items", str(len(record.action_items))),
                ("llm", "yes" if used_llm else "fallback"),
            ),
        ),
    )


def record_review(
    db_path,
    review: SixMonthReview,
    *,
    delivery: EngineerActivity | None,
    one_on_one_dates: tuple[str, ...],
    used_llm: bool,
) -> None:
    """One record per six-month review, naming every evidence stream it read."""
    inputs = _activity_inputs(delivery) + tuple(
        f"performance:{review.engineer}:one-on-one:{d}" for d in one_on_one_dates
    )
    _append(
        db_path,
        DecisionRecord(
            entity_id=f"performance:{review.engineer}:review:{review.period_end}",
            entity_type="six-month-review",
            activity_id=f"performance-run:{review.period_end}",
            agent_id="performance.review" if used_llm else "performance.review-fallback",
            role="generator",
            source_document=_SOURCE,
            detail=f"Six-month review for {review.engineer} ({review.period_start} → {review.period_end}), "
            f"framework: {review.framework_used or 'default'}.",
            inputs=inputs,
            extras=(
                ("engineer", review.engineer),
                ("period_start", review.period_start),
                ("period_end", review.period_end),
                ("framework", review.framework_used),
                ("llm", "yes" if used_llm else "fallback"),
            ),
        ),
    )
