"""MCP tools: projects (project_create/list/get/link_session/set_defaults/set_status/draft)."""

from __future__ import annotations

import logging

from mcp.server.fastmcp import Context

from yeaboi.mcp.runtime import run_engine, run_readonly

logger = logging.getLogger(__name__)


def _create(name: str, description: str) -> dict:
    from yeaboi.projects.engine import create_project

    return create_project(name, description)


def _list(include_archived: bool) -> list[dict]:
    from yeaboi.projects.engine import list_projects

    return list_projects(include_archived)


def _get(project_id: str) -> dict:
    if not project_id.strip():
        raise ValueError("project_id is required — see project_list.")
    from yeaboi.projects.engine import get_project

    return get_project(project_id.strip())


def _link_session(project_id: str, session_id: str) -> dict:
    if not project_id.strip():
        raise ValueError("project_id is required — see project_list.")
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.projects.engine import link_session

    return link_session(project_id.strip(), resolve_session_id(session_id))


def _set_defaults(project_id: str, defaults: dict | None) -> dict:
    if not project_id.strip():
        raise ValueError("project_id is required — see project_list.")
    from yeaboi.projects.engine import set_project_defaults

    return set_project_defaults(project_id.strip(), defaults or {})


def _set_status(project_id: str, status: str) -> dict:
    if not project_id.strip():
        raise ValueError("project_id is required — see project_list.")
    from yeaboi.projects.engine import set_project_status

    return set_project_status(project_id.strip(), status.strip())


def _draft(description: str) -> dict:
    from yeaboi.projects.engine import draft_project_idea

    return draft_project_idea(description)


def register(app) -> None:
    """Attach the project tools to the FastMCP app."""

    @app.tool()
    async def project_create(name: str, description: str = "") -> dict:
        """Create a project — the identity that links yeaboi sessions across modes (a plan's
        analysis feeds its standups, standups feed its retros). Returns the row with its
        project_id (proj-<8hex>); pass that id to plan_generate/standup_run/report_delivery
        to scope a run to the project."""
        return await run_readonly(_create, name, description)

    @app.tool()
    async def project_list(include_archived: bool = False) -> dict:
        """List projects (id, name, last activity, linked-session count), most recently
        active first."""
        return await run_readonly(_list, include_archived)

    @app.tool()
    async def project_get(project_id: str) -> dict:
        """Get one project: its row, settings/defaults, and the session ids linked to it."""
        return await run_readonly(_get, project_id)

    @app.tool()
    async def project_link_session(project_id: str, session_id: str = "") -> dict:
        """Link an existing session to a project so its runs and history count toward the
        project's scoped context. Blank session_id = most recent session."""
        return await run_readonly(_link_session, project_id, session_id)

    @app.tool()
    async def project_set_defaults(project_id: str, defaults: dict | None = None) -> dict:
        """Merge default settings into a project. Accepted keys:
        default_analysis_profile_id (the team profile a scoped plan_generate seeds when the
        caller passes none — set it after a team analysis) and default_context_deps (the
        context-source toggles a scoped run inherits when the caller passes none — a list of
        retro/standup/plan/performance/analysis; [] makes the project's runs incognito by
        default) and repo_path (the absolute path of the project's repository — the Agents
        reports scope to sessions under it, worktrees included). Unknown keys are rejected."""
        return await run_readonly(_set_defaults, project_id, defaults)

    @app.tool()
    async def project_set_status(project_id: str, status: str) -> dict:
        """Set the owner's verdict on a project: "done" marks it complete (it lists under
        Completed and can still be opened), "active" reopens it. Archive is separate."""
        return await run_readonly(_set_status, project_id, status)

    @app.tool()
    async def project_draft(ctx: Context, description: str) -> dict:
        """Turn a rough description of what is being built into a project name and a one- or
        two-sentence pitch — the AI rewrite behind the New project dialog. Returns
        {name, description, source, note}; when the LLM is unavailable the description comes
        back as sent, named from its first words (source "original"). Nothing is created:
        pass the result to project_create."""
        return await run_engine(ctx, _draft, description)
