"""Performance mode — per-engineer 1:1 prep, 1:1 completion, and 6-month reviews.

A team lead picks an engineer (roster derived from Jira / Azure DevOps assignees)
and runs one of three connected workflows. The 1:1 Prep ↔ Completion loop closes
via action items; the whole store feeds Planning & Analysis via
``gather_performance_context`` so the system becomes person-aware.

Public API is re-exported here; submodules lazy-import their optional/heavy deps
(LLM, tracker SDKs) inside functions, so importing this package is always cheap and
safe — mirrors the standup / retro packages.

# See docs: "Performance Mode"
"""

from __future__ import annotations

from yeaboi.performance.context import PerformanceContext, gather_performance_context
from yeaboi.performance.roster import fetch_roster
from yeaboi.performance.store import PerformanceStore

__all__ = [
    "PerformanceContext",
    "PerformanceStore",
    "complete_one_on_one",
    "fetch_roster",
    "gather_engineer_activity",
    "gather_performance_context",
    "run_one_on_one_prep",
    "run_six_month_review",
]


def __getattr__(name: str):
    """Lazy-load engine/activity entry points to keep package import cheap.

    ``run_one_on_one_prep`` etc. pull in the LLM stack; deferring their import means
    ``import yeaboi.performance`` (done by sessions.py's v8 migration for the
    schema constant) never drags in langchain.
    """
    if name in ("run_one_on_one_prep", "complete_one_on_one", "run_six_month_review"):
        from yeaboi.performance import engine

        return getattr(engine, name)
    if name == "gather_engineer_activity":
        from yeaboi.performance.activity import gather_engineer_activity

        return gather_engineer_activity
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
