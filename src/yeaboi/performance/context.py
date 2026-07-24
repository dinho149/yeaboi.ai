"""Per-engineer performance signal for Planning & Analysis.

Mirrors ``agent/ceremony_history.py``: a graceful I/O entry point
(``gather_performance_context``) that never raises, distilling the Performance
store into a compact markdown block the planning analyzer / sprint planner can
consume. This is what makes the whole system *person-aware* — open 1:1 action
items, growth goals, and the freshest review headline flow into how work is
scoped and assigned.

Only lightweight, already-summarised signals are surfaced (open actions + review
strengths/growth areas) — never raw transcripts — so nothing sensitive leaks into
a planning prompt.

# See docs: "Session Management" — SQLite persistence
# See docs: "Prompt Construction" — ARC framework (optional context sections)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Cap how much we inject so a large team can't blow up the prompt.
_MAX_ENGINEERS = 8
_MAX_ITEMS_EACH = 4


@dataclass
class PerformanceContext:
    """Distilled per-engineer signal. Transient — never persisted.

    ``summary_md`` is the block injected into the planning prompts.
    """

    summary_md: str = ""
    engineers_with_actions: int = 0
    review_count: int = 0

    @property
    def is_empty(self) -> bool:
        return self.engineers_with_actions == 0 and self.review_count == 0


def _bullets(items) -> str:
    return "\n".join(f"  - {it}" for it in items)


def gather_performance_context() -> PerformanceContext:
    """Read the team's recent 1:1 actions + reviews and distil them (team-wide).

    Graceful: a missing DB / empty tables / any error yields an empty context, so
    planning and analysis behave exactly as before Performance mode existed.
    """
    try:
        from yeaboi.config import get_sessions_db
        from yeaboi.performance.store import PerformanceStore

        db_path = get_sessions_db()
        if not db_path.exists():
            return PerformanceContext()

        with PerformanceStore(db_path) as store:
            open_actions = store.get_all_open_action_items()
            reviews = store.get_recent_reviews(_MAX_ENGINEERS)
    except Exception:  # noqa: BLE001 — performance context is best-effort; never abort a plan
        logger.debug("gather_performance_context failed (non-fatal)", exc_info=True)
        return PerformanceContext()

    # Drop engineers whose latest 1:1 had no open actions.
    open_actions = {eng: acts for eng, acts in open_actions.items() if acts}

    parts: list[str] = []
    if open_actions:
        lines = ["**Open 1:1 action items by engineer:**"]
        for eng, acts in list(open_actions.items())[:_MAX_ENGINEERS]:
            lines.append(f"- {eng}:")
            lines.append(_bullets(list(acts)[:_MAX_ITEMS_EACH]))
        parts.append("\n".join(lines))

    if reviews:
        lines = ["**Recent per-engineer focus areas (from reviews):**"]
        for r in reviews[:_MAX_ENGINEERS]:
            growth = "; ".join(list(r.areas_for_improvement)[:_MAX_ITEMS_EACH])
            if growth:
                lines.append(f"- {r.engineer}: {growth}")
        if len(lines) > 1:
            parts.append("\n".join(lines))

    ctx = PerformanceContext(
        summary_md="\n\n".join(parts),
        engineers_with_actions=len(open_actions),
        review_count=len(reviews),
    )
    logger.info(
        "performance_context: %d engineer(s) with open actions, %d review(s)",
        ctx.engineers_with_actions,
        ctx.review_count,
    )
    return ctx
