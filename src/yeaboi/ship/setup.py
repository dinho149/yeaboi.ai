"""What a Ship launcher must decide before the engine is called.

The picker's two questions — *which story*, and *which repository* — are the
same on every surface, and both have answers a UI must not invent: a plan lives
in one of two stores, and the path a run actually writes to is the git toplevel,
not whatever the user typed. Both live here so the terminal and the desktop
agree; the engine, the gate and the store are already surface-neutral.

Deliberately ``setup.py`` and not ``engine.py``: the engine glob in the parity
test registers every public name in ``engine.py`` as a capability of its own.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

#: What the approver may answer at the gate. The store arbitrates whoever
#: answers first, so a second surface offering a fifth verb would be lying.
GATE_RESOLUTIONS = ("approved", "rejected")

NO_PLAN_MESSAGE = "No saved plan with stories yet — plan a project first."


def load_plan() -> tuple[dict, str, str, str]:
    """``(state, plan_id, project_name, problem)`` for the latest saved plan.

    Reads across BOTH plan stores (the chat's project store and the SQLite
    session store) via :mod:`yeaboi.ship.plans`, and picks the latest plan that
    actually has work in it, so a completed plan is never shadowed by a newer
    empty session and a chat-built plan is not invisible. Never raises: an
    unreadable store yields a problem string.
    """
    from yeaboi.ship import plans

    try:
        picked = plans.latest_plan_with_work()
    except Exception as exc:  # noqa: BLE001 — an unreadable store must not crash the picker
        logger.warning("ship setup: could not load plans: %s", exc)
        return {}, "", "", "Could not read saved plans — see logs."
    if picked is None:
        return {}, "", "", ""
    state, plan_id, name = picked
    return state, plan_id, name, ""


def load_stories() -> tuple[list, str, str, str]:
    """:func:`load_plan`, narrowed to the story level.

    A plan can be shipped an epic, a story or a task at a time; a surface that
    only offers stories asks for them directly rather than filtering an outline.
    """
    state, plan_id, name, problem = load_plan()
    return list(state.get("stories") or []), plan_id, name, problem


def resolve_target(repo: str) -> tuple[str, str]:
    """``(the git toplevel this run will touch, a user-facing problem or "")``.

    The toplevel, not the typed path, is what every later write targets —
    ``git worktree add`` writes into ``<toplevel>/.git`` and the push runs from
    there. Consent is checked with ``is_relative_to`` containment, so granting a
    *subdirectory* would not grant the toplevel: resolving first is what keeps
    the consent prompt honest about what is about to be touched.
    """
    from yeaboi.ship import worktree

    try:
        top = worktree.resolve_repo(Path(repo).expanduser())
    except worktree.WorktreeError as exc:
        return "", str(exc)
    try:
        if worktree.is_dirty(top):
            return str(top), f"{top} has uncommitted changes — commit or stash first"
    except worktree.WorktreeError as exc:
        return str(top), str(exc)
    return str(top), ""


def story_options(stories) -> list[dict]:
    """The picker's rows — the same three facts every surface shows per story.

    ``title`` falls back to the story's goal, exactly as the terminal row does:
    stories saved before titles existed have only the persona/goal template.
    """
    rows: list[dict] = []
    for story in stories:
        points = getattr(story, "story_points", None)
        rows.append(
            {
                "id": getattr(story, "id", ""),
                "title": getattr(story, "title", "") or getattr(story, "goal", ""),
                "points": int(points) if points is not None else 0,
                "criteria": len(getattr(story, "acceptance_criteria", ()) or ()),
            }
        )
    return rows
