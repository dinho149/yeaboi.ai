"""Projects engine — the headless entry points every surface adapts.

Five verbs over the ``projects`` table in the shared sessions.db: create,
list, get, link a session, set defaults. Anything heavier (scoped context
reads) lives in ``projects/scope.py``; the store itself in
``projects/store.py``. Keep this module's public surface exactly these five
functions — surface parity registers each one.

Naming hazard: these ``proj-<8hex>`` ids are unrelated to the legacy
planning-TUI uuid4 "project_id" in ``projects.json`` (persistence.py).

# See docs: "Architecture" — engine-first, thin adapters
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# The settings keys set_project_defaults accepts. default_context_deps is the
# context-toggle seam — reserved and stored now, read by the incognito work.
_DEFAULT_KEYS = ("default_analysis_profile_id", "default_context_deps")


def _db_path(db_path: Path | None) -> Path:
    from yeaboi.paths import get_db_path

    return db_path or get_db_path()


def create_project(name: str, description: str = "", *, db_path: Path | None = None) -> dict:
    """Create a project and return its row."""
    name = name.strip()
    if not name:
        raise ValueError("name is required — a short human project name.")
    from yeaboi.projects.store import ProjectStore

    with ProjectStore(_db_path(db_path)) as store:
        project = store.create(name, description.strip())
    logger.info("create_project: %s (%s)", project["project_id"], name)
    return project


def list_projects(include_archived: bool = False, *, db_path: Path | None = None) -> list[dict]:
    """All projects, most recently active first, with their session counts."""
    from yeaboi.projects.store import ProjectStore
    from yeaboi.sessions import SessionStore

    path = _db_path(db_path)
    with ProjectStore(path) as store:
        projects = store.list_projects(include_archived=include_archived)
    with SessionStore(path) as sessions:
        for project in projects:
            project["session_count"] = len(sessions.session_ids_for_project(project["project_id"]))
    return projects


def get_project(project_id: str, *, db_path: Path | None = None) -> dict:
    """One project's row plus the ids of the sessions linked to it."""
    from yeaboi.projects.store import ProjectStore
    from yeaboi.sessions import SessionStore

    path = _db_path(db_path)
    with ProjectStore(path) as store:
        project = store.get(project_id)
    if project is None:
        raise ValueError(f"unknown project {project_id!r} — see list_projects.")
    with SessionStore(path) as sessions:
        project["session_ids"] = sessions.session_ids_for_project(project_id)
    return project


def link_session(project_id: str, session_id: str, *, db_path: Path | None = None) -> dict:
    """Link an existing session to a project (the post-hoc scoping lever)."""
    from yeaboi.projects.store import ProjectStore
    from yeaboi.sessions import SessionStore

    path = _db_path(db_path)
    with ProjectStore(path) as store:
        if store.get(project_id) is None:
            raise ValueError(f"unknown project {project_id!r} — see list_projects.")
        with SessionStore(path) as sessions:
            if sessions.get_session(session_id) is None:
                raise ValueError(f"unknown session {session_id!r} — see sessions_list.")
            sessions.set_session_project(session_id, project_id)
        store.touch(project_id)
    logger.info("link_session: %s -> %s", session_id, project_id)
    return {"project_id": project_id, "session_id": session_id}


def set_project_defaults(project_id: str, defaults: dict, *, db_path: Path | None = None) -> dict:
    """Merge ``defaults`` into the project's settings and return them.

    Accepts only the known keys (``default_analysis_profile_id``,
    ``default_context_deps``) — an unknown key is a spelling mistake waiting
    to become a silent no-op, so it raises instead.
    """
    unknown = sorted(set(defaults) - set(_DEFAULT_KEYS))
    if unknown:
        raise ValueError(f"unknown default(s) {unknown} — accepted: {', '.join(_DEFAULT_KEYS)}.")
    from yeaboi.projects.store import ProjectStore

    with ProjectStore(_db_path(db_path)) as store:
        if store.get(project_id) is None:
            raise ValueError(f"unknown project {project_id!r} — see list_projects.")
        settings = store.get_settings(project_id)
        settings.update(defaults)
        store.set_settings(project_id, settings)
    logger.info("set_project_defaults: %s keys=%s", project_id, sorted(defaults))
    return {"project_id": project_id, "settings": settings}
