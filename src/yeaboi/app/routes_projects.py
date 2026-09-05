"""Native routes for projects and the cross-mode sessions list.

The projects engine is five verbs over the ``projects`` table; these routes
are those verbs on the wire, plus the one read no engine owns — the union of
every mode's saved runs (``sessions_recent``), which is what a Projects page
and a Sessions page both list. Engines are called directly, the same shape as
``routes_solo.review``.

``{project_id}`` here is the engine's ``proj-<8hex>`` id — unrelated to the
``project_id`` segment of ``/api/chat/sessions/{project_id}``, which is the
planning chat's own handle.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from yeaboi.app.router import HTTPError, Request, Response, json_response

logger = logging.getLogger(__name__)

_TRUE = ("1", "true", "yes", "on")


def require_project(project_id: str) -> str:
    """A run body's optional ``project_id``, stripped: blank is "", an unknown id a 400."""
    project_id = project_id.strip()
    if not project_id:
        return ""
    from yeaboi.projects.engine import get_project

    try:
        get_project(project_id)
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None
    return project_id


def projects(app, request: Request) -> Response:
    """``GET /api/projects?include_archived=`` — every project with its session count."""
    from yeaboi.projects.engine import list_projects

    include_archived = str(request.query.get("include_archived", "")).strip().lower() in _TRUE
    rows = list_projects(include_archived)
    logger.info("projects listed: %d (archived=%s)", len(rows), include_archived)
    return json_response({"projects": rows})


def create(app, request: Request) -> Response:
    """``POST /api/projects`` ``{name, description?}`` — the new row; 400 on a blank name."""
    from yeaboi.projects.engine import create_project

    payload = request.json()
    name = str(payload.get("name", "")).strip()
    description = str(payload.get("description", "") or "")
    try:
        project = create_project(name, description)
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None
    logger.info("project created over the wire: %s", project["project_id"])
    return json_response(project)


def get(app, request: Request) -> Response:
    """``GET /api/projects/{project_id}`` — one row plus its ``session_ids``; 404 when unknown."""
    from yeaboi.projects.engine import get_project

    project_id = _project_id(request)
    try:
        project = get_project(project_id)
    except ValueError as exc:
        raise HTTPError(404, str(exc)) from None
    return json_response(project)


def status(app, request: Request) -> Response:
    """``POST /api/projects/{project_id}/status`` ``{status}`` — the row; 400 bad status, 404 unknown."""
    from yeaboi.projects.engine import set_project_status

    project_id = _project_id(request)
    value = str(request.json().get("status", "")).strip()
    try:
        project = set_project_status(project_id, value)
    except ValueError as exc:
        message = str(exc)
        raise HTTPError(404 if message.startswith("unknown project") else 400, message) from None
    logger.info("project status set over the wire: %s -> %s", project_id, value)
    return json_response(project)


def draft(app, request: Request) -> Response:
    """``POST /api/projects/draft`` ``{description}`` — a name and pitch for a new project; 400 when blank."""
    from yeaboi.projects.engine import draft_project_idea

    description = str(request.json().get("description", "") or "")
    try:
        result = draft_project_idea(description)
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None
    logger.info("project draft over the wire: source=%s", result["source"])
    return json_response(result)


def sessions(app, request: Request) -> Response:
    """``GET /api/projects/{project_id}/sessions?mode=&limit=`` — the project's runs, every mode."""
    from yeaboi.paths import get_db_path
    from yeaboi.projects.store import ProjectStore

    project_id = _project_id(request)
    with ProjectStore(get_db_path()) as store:
        if store.get(project_id) is None:
            raise HTTPError(404, f"unknown project {project_id!r}")
    rows = _recent(request, project_id=project_id)
    logger.info("project sessions listed: %s → %d", project_id, len(rows))
    return json_response({"sessions": rows})


def defaults(app, request: Request) -> Response:
    """``POST /api/projects/{project_id}/defaults`` ``{defaults: {…}}`` — merge and return the settings."""
    from yeaboi.projects.engine import set_project_defaults

    project_id = _project_id(request)
    values = request.json().get("defaults")
    if not isinstance(values, dict) or not values:
        raise HTTPError(400, "defaults must be a non-empty object of {key: value}")
    try:
        result = set_project_defaults(project_id, values)
    except ValueError as exc:
        message = str(exc)
        raise HTTPError(404 if message.startswith("unknown project") else 400, message) from None
    logger.info("project defaults set over the wire: %s keys=%s", project_id, sorted(values))
    return json_response(result)


def recent(app, request: Request) -> Response:
    """``GET /api/sessions/recent?limit=&mode=&project_id=`` — the newest runs across every mode."""
    project_id = str(request.query.get("project_id", "")).strip()
    rows = _recent(request, project_id=project_id)
    return json_response({"sessions": rows})


# ---------------------------------------------------------------------------


def _project_id(request: Request) -> str:
    project_id = str(request.params.get("project_id", "")).strip()
    if not project_id:
        raise HTTPError(400, "project_id is required")
    return project_id


def _recent(request: Request, *, project_id: str) -> list[dict]:
    from yeaboi.sessions_recent import recent_sessions

    mode = str(request.query.get("mode", "")).strip()
    raw_limit = str(request.query.get("limit", "")).strip()
    try:
        limit = int(raw_limit) if raw_limit else 20
    except ValueError:
        raise HTTPError(400, "limit must be a number") from None
    if limit < 0:
        raise HTTPError(400, "limit must be zero or more")
    try:
        rows = recent_sessions(limit=limit, mode=mode, project_id=project_id)
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None
    return [asdict(row) for row in rows]
