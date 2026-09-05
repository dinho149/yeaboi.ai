"""Projects engine — the headless entry points every surface adapts.

Seven verbs over the ``projects`` table in the shared sessions.db: create,
list, get, link a session, set defaults, set the status, and draft a new
project's name and pitch. Anything heavier (scoped context reads) lives in
``projects/scope.py``; the store itself in ``projects/store.py``. Keep this
module's public surface exactly these seven functions — surface parity
registers each one.

Naming hazard: these ``proj-<8hex>`` ids are unrelated to the legacy
planning-TUI uuid4 "project_id" in ``projects.json`` (persistence.py).

# See docs: "Architecture" — engine-first, thin adapters
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# A drafted name when no model can name the project: its first few words.
_FALLBACK_NAME_WORDS = 4

# The settings keys set_project_defaults accepts. default_context_deps is the
# context-source toggle default a scoped run inherits (scope.resolve_scope);
# repo_path is the absolute path the Agents world scopes its reports to.
_DEFAULT_KEYS = ("default_analysis_profile_id", "default_context_deps", "repo_path")


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


def _validated_repo_path(value: object) -> str:
    """``repo_path`` as stored: a non-empty absolute path, normalised, never the filesystem root."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("repo_path must be a non-empty absolute path.")
    path = os.path.normpath(value)
    if not os.path.isabs(path):
        raise ValueError(f"repo_path must be an absolute path, got {value!r}.")
    if os.path.dirname(path) == path:
        raise ValueError("repo_path must not be the filesystem root.")
    return path


def set_project_defaults(project_id: str, defaults: dict, *, db_path: Path | None = None) -> dict:
    """Merge ``defaults`` into the project's settings and return them.

    Accepts only the known keys (``default_analysis_profile_id``,
    ``default_context_deps``, ``repo_path``) — an unknown key is a spelling
    mistake waiting to become a silent no-op, so it raises instead. A
    ``repo_path`` must be absolute and is stored normalised.
    """
    unknown = sorted(set(defaults) - set(_DEFAULT_KEYS))
    if unknown:
        raise ValueError(f"unknown default(s) {unknown} — accepted: {', '.join(_DEFAULT_KEYS)}.")
    if "repo_path" in defaults:
        defaults = {**defaults, "repo_path": _validated_repo_path(defaults["repo_path"])}
    from yeaboi.projects.store import ProjectStore

    with ProjectStore(_db_path(db_path)) as store:
        if store.get(project_id) is None:
            raise ValueError(f"unknown project {project_id!r} — see list_projects.")
        settings = store.get_settings(project_id)
        settings.update(defaults)
        store.set_settings(project_id, settings)
    logger.info("set_project_defaults: %s keys=%s", project_id, sorted(defaults))
    return {"project_id": project_id, "settings": settings}


def set_project_status(project_id: str, status: str, *, db_path: Path | None = None) -> dict:
    """Set the owner's verdict — ``done`` is complete, ``active`` is in progress — and return the row."""
    from yeaboi.projects.store import STATUSES, ProjectStore

    status = status.strip().lower()
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}, got {status!r}.")
    with ProjectStore(_db_path(db_path)) as store:
        if not store.set_status(project_id, status):
            raise ValueError(f"unknown project {project_id!r} — see list_projects.")
        project = store.get(project_id)
    logger.info("set_project_status: %s -> %s", project_id, status)
    return project


def _fallback_name(description: str) -> str:
    """A name from the draft's first words, for when no model names it."""
    words = re.findall(r"[\w'-]+", description)
    return " ".join(words[:_FALLBACK_NAME_WORDS]).lower() or "untitled project"


def _parse_draft(raw: str) -> tuple[str, str] | None:
    """``(name, pitch)`` from the model's JSON, tolerating markdown fences."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]
    try:
        parsed = json.loads(raw.strip())
    except (json.JSONDecodeError, TypeError):
        logger.warning("draft_project_idea: could not parse the model's JSON")
        return None
    if not isinstance(parsed, dict):
        return None
    name = " ".join(str(parsed.get("name", "")).split()).strip().strip("\"'").lower()
    pitch = " ".join(str(parsed.get("pitch", "")).split()).strip()
    if not name or not pitch:
        return None
    return name, pitch


def draft_project_idea(description: str) -> dict:
    """A name and pitch for a project that does not exist yet. Never raises past a blank draft.

    Returns ``{name, description, source, note}``: ``source`` is ``"ai"`` when
    the model rewrote the draft and ``"original"`` when it could not, in which
    case ``description`` is the draft as given and ``name`` its first words.
    Nothing is created — the caller passes the result to :func:`create_project`.
    """
    description = " ".join(description.split()).strip()
    if not description:
        raise ValueError("description is required — say what you are building.")
    original = {
        "name": _fallback_name(description),
        "description": description,
        "source": "original",
    }
    from yeaboi.config import is_llm_configured

    configured, why = is_llm_configured()
    if not configured:
        logger.warning("draft_project_idea: LLM not configured (%s) — keeping the draft", why)
        return {**original, "note": f"AI unavailable ({why}) — your words, named from the first few."}
    logger.info("draft_project_idea: rewrite requested (%d chars)", len(description))

    from yeaboi.agent.llm import get_llm, invoke_with_images, track_usage
    from yeaboi.agent.nodes import _is_llm_auth_or_billing_error
    from yeaboi.prompts.project_idea import get_project_idea_prompt

    try:
        # See docs: "Agentic Blueprint Reference" — invoking the LLM directly
        response = invoke_with_images(get_llm(temperature=0.2), get_project_idea_prompt(description), None)
        track_usage(response)
        parsed = _parse_draft(response.content)
    except Exception as exc:
        if _is_llm_auth_or_billing_error(exc):
            logger.warning("draft_project_idea: LLM auth/billing error — keeping the draft: %s", exc)
            return {**original, "note": "AI unavailable (API key/billing) — your words, named from the first few."}
        logger.warning("draft_project_idea: rewrite failed — keeping the draft: %s", exc)
        return {**original, "note": "AI request failed — your words, named from the first few (see logs)."}
    if parsed is None:
        return {**original, "note": "AI returned nothing usable — your words, named from the first few."}
    name, pitch = parsed
    logger.info("draft_project_idea: rewrote to %r (%d chars)", name, len(pitch))
    return {"name": name, "description": pitch, "source": "ai", "note": "AI rewrote your draft and named it."}
