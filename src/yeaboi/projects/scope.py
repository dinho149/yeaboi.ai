"""Project scope — the pull-based resolver that narrows cross-mode reads.

A ``ProjectScope`` names a project and the sessions linked to it; gatherers
(``gather_ceremony_context``, retro carry-forward, …) take an optional
``scope=`` and hard-filter their store reads to those sessions. ``None`` is
today's team-wide behavior, byte-for-byte — scoping is strictly an opt-in
narrowing, and resolution never raises (a bad id degrades to unscoped).

Deliberately unscoped: PerformanceStore reads. 1:1s and reviews are keyed by
engineer, not project — an engineer's history must not shrink because a
project is active — and ``performance_notes`` has no session column at all.

Naming hazard: this is the ``proj-<8hex>`` id space of ``projects/store.py``,
not the legacy planning-TUI uuid4 "project_id" in ``projects.json``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProjectScope:
    """The sessions a project's context reads are narrowed to."""

    project_id: str
    session_ids: tuple[str, ...]
    # Context-toggle seam: None = every dependency enabled. resolve_scope
    # always sets None today; the incognito/context_deps work assigns it.
    context_deps: frozenset[str] | None = None


def resolve_scope(project_id: str = "", session_id: str = "", *, db_path: Path | None = None) -> ProjectScope | None:
    """Resolve the scope a run operates under. ``None`` = unscoped (team-wide).

    Precedence: an explicit ``project_id`` wins; otherwise the project is
    inherited from ``session_id``'s ``sessions_meta`` row; an unlinked session
    (or neither argument) resolves to ``None``. Never raises.
    """
    try:
        from yeaboi.paths import get_db_path
        from yeaboi.sessions import SessionStore

        path = db_path or get_db_path()
        if not Path(path).exists():
            return None
        with SessionStore(path) as store:
            pid = project_id or (store.session_project_id(session_id) if session_id else "")
            if not pid:
                return None
            ids = tuple(store.session_ids_for_project(pid))
        logger.info("Resolved project scope: project=%s sessions=%d", pid, len(ids))
        return ProjectScope(project_id=pid, session_ids=ids)
    except Exception:  # noqa: BLE001 — scoping is best-effort; a bad id must not break a run
        logger.debug("resolve_scope failed (non-fatal)", exc_info=True)
        return None


def latest_planning_state(scope: ProjectScope | None, *, db_path: Path | None = None) -> tuple[str, dict] | None:
    """The project's newest planning session that carries a sprint plan.

    Returns ``(session_id, state)`` or ``None``; sessions without ``sprints``
    are skipped (an intake that never reached sprint_planner feeds nothing).
    Never raises.
    """
    if scope is None or not scope.project_id:
        return None
    try:
        from yeaboi.paths import get_db_path
        from yeaboi.sessions import SessionStore

        path = db_path or get_db_path()
        if not Path(path).exists():
            return None
        with SessionStore(path) as store:
            for sid in store.session_ids_for_project(scope.project_id, mode="planning"):
                state = store.load_state(sid)
                if state and state.get("sprints"):
                    logger.info("latest_planning_state: project=%s session=%s", scope.project_id, sid)
                    return sid, state
    except Exception:  # noqa: BLE001 — same never-raise contract as resolve_scope
        logger.debug("latest_planning_state failed (non-fatal)", exc_info=True)
        return None
    return None
