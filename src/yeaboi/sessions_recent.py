"""The cross-mode recent-sessions list.

``sessions_meta`` holds planning and analysis sessions only; standup, retro,
reporting, ship and weekly-review runs live in their own stores, keyed by a
planning session id. This module unions every store that already lists its
runs into one newest-first list, so the TUI's project sessions page and the
``/api/sessions/recent`` and ``/api/projects/{id}/sessions`` routes read one
function. A mode with no listing is simply absent — no rows are invented.

Wire mode keys: ``planning``, ``analysis``, ``standup``, ``retro``,
``reporting``, ``ship``, ``review``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Every mode key a row can carry, in the order the adapters run.
MODES: tuple[str, ...] = ("planning", "analysis", "standup", "retro", "reporting", "ship", "review")

# How many rows each store is asked for when the caller sets no limit.
_STORE_LIMIT = 100


@dataclass(frozen=True)
class RecentSession:
    """One run or session, whichever store it came from."""

    session_id: str
    run_id: str  # the store's own row id; "" for a planning/analysis session
    mode: str
    title: str
    created_at: str
    last_modified: str
    project_id: str


def recent_sessions(
    *,
    limit: int = 20,
    mode: str = "",
    project_id: str = "",
    db_path: Path | None = None,
) -> list[RecentSession]:
    """The newest runs across every mode, optionally one mode or one project.

    ``project_id`` scopes through :func:`resolve_scope` where a store filters
    by ``session_ids``, and by the stored planning session id elsewhere. An
    unknown project yields an empty list, never an error; a store that cannot
    be read is skipped with a warning. ``limit`` 0 means every row.
    """
    from yeaboi.paths import get_db_path

    path = db_path or get_db_path()
    if not Path(path).exists():
        return []
    if mode and mode not in MODES:
        raise ValueError(f"unknown mode {mode!r} — one of {', '.join(MODES)}")

    session_ids: tuple[str, ...] | None = None
    if project_id:
        from yeaboi.projects.scope import resolve_scope

        scope = resolve_scope(project_id, db_path=path)
        session_ids = scope.session_ids if scope is not None and scope.project_id else ()

    per_store = limit if limit > 0 else _STORE_LIMIT
    projects_by_session = _project_ids_by_session(path)
    rows: list[RecentSession] = []
    for name, adapter in _ADAPTERS.items():
        if mode and mode != name:
            continue
        try:
            rows.extend(adapter(path, per_store, session_ids, projects_by_session))
        except Exception:  # noqa: BLE001 — one unreadable store must not empty the list
            logger.warning("recent_sessions: %s listing failed", name, exc_info=True)
    rows.sort(key=lambda r: r.last_modified, reverse=True)
    if limit > 0:
        rows = rows[:limit]
    logger.info("recent_sessions: %d row(s) (mode=%s project=%s)", len(rows), mode or "-", project_id or "-")
    return rows


def _project_ids_by_session(path: Path) -> dict[str, str]:
    """session id → project id, for the stores that carry no project column."""
    from yeaboi.sessions import SessionStore

    with SessionStore(path) as store:
        return store.session_project_ids()


def _keep(session_id: str, session_ids: tuple[str, ...] | None) -> bool:
    return session_ids is None or session_id in session_ids


Adapter = Callable[[Path, int, "tuple[str, ...] | None", dict[str, str]], list[RecentSession]]


def _meta_sessions(path: Path, limit: int, session_ids, mode: str) -> list[RecentSession]:
    """Planning and analysis rows straight from ``sessions_meta``."""
    from yeaboi.sessions import SessionStore, make_display_name

    with SessionStore(path) as store:
        rows = store.list_sessions(mode=mode, limit=limit)
    return [
        RecentSession(
            session_id=row["session_id"],
            run_id="",
            mode=mode,
            title=make_display_name(row),
            created_at=row["created_at"] or "",
            last_modified=row["last_modified"] or "",
            project_id=row["project_id"] or "",
        )
        for row in rows
        if _keep(row["session_id"], session_ids)
    ]


def _planning(path, limit, session_ids, projects):
    return _meta_sessions(path, limit, session_ids, "planning")


def _analysis(path, limit, session_ids, projects):
    return _meta_sessions(path, limit, session_ids, "analysis")


def _standup(path, limit, session_ids, projects):
    from yeaboi.standup.store import StandupStore

    with StandupStore(path) as store:
        rows = store.get_all_history(limit, session_ids=session_ids)
    return [
        RecentSession(
            session_id=r.get("session_id", ""),
            run_id=str(r["id"]),
            mode="standup",
            title=f"Standup — {r.get('standup_date') or r['run_at'][:10]}",
            created_at=r["run_at"],
            last_modified=r["run_at"],
            project_id=projects.get(r.get("session_id", ""), ""),
        )
        for r in rows
    ]


def _retro(path, limit, session_ids, projects):
    from yeaboi.retro.store import RetroStore

    with RetroStore(path) as store:
        rows = store.get_all_history(limit, session_ids=session_ids)
    return [
        RecentSession(
            session_id=r.get("session_id", ""),
            run_id=str(r["id"]),
            mode="retro",
            title=f"Retro — {r.get('retro_date') or r['run_at'][:10]}",
            created_at=r["run_at"],
            last_modified=r["run_at"],
            project_id=projects.get(r.get("session_id", ""), ""),
        )
        for r in rows
    ]


def _reporting(path, limit, session_ids, projects):
    from yeaboi.reporting.store import ReportingStore

    with ReportingStore(path) as store:
        rows = store.get_all_history(limit)
    return [
        RecentSession(
            session_id=r.get("session_id", ""),
            run_id=str(r["id"]),
            mode="reporting",
            title=f"Report — {r.get('period') or r['run_at'][:10]}",
            created_at=r["run_at"],
            last_modified=r["run_at"],
            project_id=projects.get(r.get("session_id", ""), ""),
        )
        for r in rows
        if _keep(r.get("session_id", ""), session_ids)
    ]


def _ship(path, limit, session_ids, projects):
    from yeaboi.ship.store import ShipStore

    with ShipStore(path) as store:
        runs = store.list_runs(limit=limit)
    return [
        RecentSession(
            session_id=run.session_id,
            run_id=run.run_id,
            mode="ship",
            title=f"Ship — {run.item_id or run.run_id} · {run.status}",
            created_at=run.created_at,
            last_modified=run.updated_at or run.created_at,
            project_id=projects.get(run.session_id, ""),
        )
        for run in runs
        if _keep(run.session_id, session_ids)
    ]


def _review(path, limit, session_ids, projects):
    from yeaboi.solo.store import WeeklyReviewStore

    with WeeklyReviewStore(path) as store:
        rows = store.get_all_history(limit, session_ids=session_ids)
    return [
        RecentSession(
            session_id=r.get("session_id", ""),
            run_id=str(r["id"]),
            mode="review",
            title=f"Week {r.get('week_label') or r['run_at'][:10]}",
            created_at=r["run_at"],
            last_modified=r["run_at"],
            project_id=r.get("project_id") or projects.get(r.get("session_id", ""), ""),
        )
        for r in rows
    ]


_ADAPTERS: dict[str, Adapter] = {
    "planning": _planning,
    "analysis": _analysis,
    "standup": _standup,
    "retro": _retro,
    "reporting": _reporting,
    "ship": _ship,
    "review": _review,
}
