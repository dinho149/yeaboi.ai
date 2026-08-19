"""Ship — yeaboi ships code from its own sprint plan.

The back half of planning: a user story from a saved plan is handed to a
supervised coding agent (Claude Code headless) in an isolated git worktree,
its output is validated deterministically, and a human approval gate stands
between the diff and the pull request. Story → worktree → implement →
validate → approve → PR, natively in Python — the pipeline shape follows
archon's plan-to-PR workflow and the ops rails (budget fuse, worktree
coordinator, headless supervision) port ruflo's hard-won lessons; see each
module's docstring for what was kept and why.

Public API is re-exported here; submodules keep heavy/optional work inside
functions so importing this package is always cheap — mirrors the standup /
retro / roadmap packages.
"""

from __future__ import annotations

from yeaboi.ship.budget import BudgetDecision, BudgetStatus, release, reserve

__all__ = [
    "BudgetDecision",
    "BudgetStatus",
    "release",
    "reserve",
]
