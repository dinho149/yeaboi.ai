"""MCP tools: Daily Standup (run a standup, read history, get/set the config)."""

from __future__ import annotations

import logging
import re

# Context must be importable from module globals — FastMCP evaluates the
# stringified type hints (PEP 563) of tool functions against this namespace.
from mcp.server.fastmcp import Context

from yeaboi.mcp.runtime import run_engine, run_readonly

logger = logging.getLogger(__name__)

# Defaults used when standup_config_set runs before any config exists — mirror
# the standup_config table defaults in standup/store.py.
_CONFIG_DEFAULTS = {
    "enabled": False,
    "time": "10:00",
    "weekdays": "1-5",
    "delivery_channels": ["terminal"],
    "lead_minutes": 10,
    "timezone": "",
    "repo_path": "",
    "my_aliases": "",
    "tracker_sources": ["jira"],
    "team_members": [],
    "roster_configured": False,
    "code_sources": [],
    "github_repositories": [],
    "azdo_projects": [],
    "azdo_repositories": [],
    "code_scope_configured": False,
    "documentation_sources": [],
    "documentation_scope_configured": False,
    "automation_markers": "",
    "automation_handling": "exclude",
    "transcript_dir": "",
    "transcript_review_enabled": True,
}


def _validated_channels(channels: list | None) -> list[str] | None:
    if not channels:
        return None
    from yeaboi.standup.delivery import ALL_CHANNELS

    bad = [c for c in channels if c not in ALL_CHANNELS]
    if bad:
        raise ValueError(f"unknown delivery channel(s) {bad} — valid: {', '.join(ALL_CHANNELS)}")
    return list(channels)


def _standup_run(
    session_id: str,
    deliver: bool,
    days: int,
    channels: list | None,
    tracker_sources: list | None,
    team_members: list | None,
    code_sources: list | None,
    github_repositories: list | None,
    azdo_projects: list | None,
    azdo_repositories: list | None,
    documentation_sources: list | None,
):
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.standup.engine import run_standup

    resolved = resolve_session_id(session_id)
    return run_standup(
        resolved,
        deliver=deliver,
        days=days or None,
        channels=_validated_channels(channels),
        tracker_sources=tracker_sources,
        team_members=team_members,
        code_sources=code_sources,
        github_repositories=github_repositories,
        azdo_projects=azdo_projects,
        azdo_repositories=azdo_repositories,
        documentation_sources=documentation_sources,
    )


def _standup_members(session_id: str, tracker_sources: list | None) -> dict:
    from yeaboi.config import get_azure_devops_project, get_jira_project_key
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.paths import get_db_path
    from yeaboi.standup.roster import default_tracker_sources, discover_team_members, validate_tracker_sources
    from yeaboi.standup.store import StandupStore

    resolved = resolve_session_id(session_id)
    jira_project = get_jira_project_key() or ""
    azdo_project = get_azure_devops_project() or ""
    with StandupStore(get_db_path()) as store:
        config = store.load_config(resolved) or {}
    selected = tracker_sources or (config.get("tracker_sources") if config.get("roster_configured") else None)
    if not selected:
        selected = default_tracker_sources(jira_project=jira_project, azdo_project=azdo_project)
    selected = validate_tracker_sources(selected)
    members = discover_team_members(
        selected,
        jira_project=jira_project,
        azdo_project=azdo_project,
    )
    return {"session_id": resolved, "tracker_sources": selected, "members": members}


def _standup_history(session_id: str, limit: int) -> dict:
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.paths import get_db_path
    from yeaboi.standup.store import StandupStore

    resolved = resolve_session_id(session_id)
    with StandupStore(get_db_path()) as store:
        history = store.get_history(resolved, limit=limit)
        latest = store.get_latest_report(resolved)
    return {"session_id": resolved, "history": history, "latest_report": latest}


def _standup_repositories(code_sources: list | None) -> dict:
    from yeaboi.standup.code_scope import CODE_SOURCES, discover_code_repositories, validate_code_sources

    selected = validate_code_sources(code_sources or list(CODE_SOURCES))
    discovered = discover_code_repositories(selected)
    return {
        "code_sources": selected,
        "github_repositories": discovered.get("github", []),
        "azdo_projects": discovered.get("azure_devops", []),
    }


def _standup_config_get(session_id: str) -> dict:
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.paths import get_db_path
    from yeaboi.standup.delivery import ALL_CHANNELS
    from yeaboi.standup.store import StandupStore

    resolved = resolve_session_id(session_id)
    with StandupStore(get_db_path()) as store:
        config = store.load_config(resolved)
    return {"session_id": resolved, "config": config, "valid_channels": list(ALL_CHANNELS)}


def _standup_config_set(
    session_id: str,
    enabled: bool | None,
    time: str,
    weekdays: str,
    delivery_channels: list | None,
    lead_minutes: int,
    repo_path: str | None,
    my_aliases: str | None,
    tracker_sources: list | None,
    team_members: list | None,
    code_sources: list | None,
    github_repositories: list | None,
    azdo_projects: list | None,
    azdo_repositories: list | None,
    documentation_sources: list | None,
    automation_markers: str | None,
    automation_handling: str | None,
    transcript_dir: str | None,
    transcript_review_enabled: bool | None,
) -> dict:
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.paths import get_db_path
    from yeaboi.standup.automation import VALID_AUTOMATION_HANDLING
    from yeaboi.standup.code_scope import validate_code_sources
    from yeaboi.standup.documentation_scope import validate_documentation_sources
    from yeaboi.standup.roster import validate_tracker_sources
    from yeaboi.standup.store import StandupStore

    if time and not re.fullmatch(r"\d{1,2}:\d{2}", time):
        raise ValueError(f"time must be HH:MM (24h), got {time!r}")
    if repo_path:
        # Sandbox check at write time: this path is later fed to `git -C` by the
        # standup engine, so it must be whitelisted before it can be persisted.
        # A violation propagates through the MCP error envelope with the
        # message naming YEABOI_ALLOWED_PATHS.
        from yeaboi.fs_policy import resolve_and_check

        resolve_and_check(repo_path, mode="read", context="standup repo_path")
    if transcript_dir:
        # Same reasoning as repo_path: the transcript sweep later reads files out
        # of this directory, so it must clear the sandbox before it is persisted
        # — otherwise every scheduled run would fail the check silently instead.
        from yeaboi.fs_policy import resolve_and_check

        resolve_and_check(transcript_dir, mode="read", context="standup transcript_dir")
    if automation_handling is not None and automation_handling not in VALID_AUTOMATION_HANDLING:
        raise ValueError(
            f"automation_handling must be one of {', '.join(VALID_AUTOMATION_HANDLING)}, got {automation_handling!r}"
        )
    resolved = resolve_session_id(session_id)
    with StandupStore(get_db_path()) as store:
        current = store.load_config(resolved) or dict(_CONFIG_DEFAULTS)
        merged = {
            "enabled": current["enabled"] if enabled is None else enabled,
            "time": time or current["time"],
            "weekdays": weekdays or current["weekdays"],
            "delivery_channels": _validated_channels(delivery_channels) or current["delivery_channels"],
            "lead_minutes": current.get("lead_minutes", 10) if lead_minutes < 0 else lead_minutes,
            "timezone": current.get("timezone", ""),
            "repo_path": current.get("repo_path", "") if repo_path is None else repo_path,
            "my_aliases": current.get("my_aliases", "") if my_aliases is None else my_aliases,
            "tracker_sources": (
                current.get("tracker_sources", ["jira"])
                if tracker_sources is None
                else validate_tracker_sources(tracker_sources)
            ),
            "team_members": (
                current.get("team_members", []) if team_members is None else list(dict.fromkeys(team_members))
            ),
            "roster_configured": (
                current.get("roster_configured", False) or tracker_sources is not None or team_members is not None
            ),
            "code_sources": (
                current.get("code_sources", []) if code_sources is None else validate_code_sources(code_sources)
            ),
            "github_repositories": (
                current.get("github_repositories", [])
                if github_repositories is None
                else list(dict.fromkeys(github_repositories))
            ),
            "azdo_projects": (
                current.get("azdo_projects", []) if azdo_projects is None else list(dict.fromkeys(azdo_projects))
            ),
            "azdo_repositories": (
                current.get("azdo_repositories", [])
                if azdo_repositories is None
                else list(dict.fromkeys(azdo_repositories))
            ),
            "code_scope_configured": (
                current.get("code_scope_configured", False)
                or code_sources is not None
                or github_repositories is not None
                or azdo_projects is not None
                or azdo_repositories is not None
            ),
            "documentation_sources": (
                current.get("documentation_sources", [])
                if documentation_sources is None
                else validate_documentation_sources(documentation_sources)
            ),
            "documentation_scope_configured": (
                current.get("documentation_scope_configured", False) or documentation_sources is not None
            ),
            "automation_markers": (
                current.get("automation_markers", "") if automation_markers is None else automation_markers
            ),
            "automation_handling": (
                current.get("automation_handling", "exclude") if automation_handling is None else automation_handling
            ),
            "transcript_dir": (current.get("transcript_dir", "") if transcript_dir is None else transcript_dir),
            "transcript_review_enabled": (
                current.get("transcript_review_enabled", True)
                if transcript_review_enabled is None
                else transcript_review_enabled
            ),
        }
        store.save_config(resolved, **merged)
    logger.info("Standup config updated via MCP: session=%s enabled=%s", resolved, merged["enabled"])
    return {"session_id": resolved, "config": merged}


def register(app) -> None:
    """Attach the standup tools to the FastMCP app."""

    @app.tool()
    async def standup_run(
        ctx: Context,
        session_id: str = "",
        deliver: bool = False,
        days: int = 0,
        channels: list[str] | None = None,
        tracker_sources: list[str] | None = None,
        team_members: list[str] | None = None,
        code_sources: list[str] | None = None,
        github_repositories: list[str] | None = None,
        azdo_projects: list[str] | None = None,
        azdo_repositories: list[str] | None = None,
        documentation_sources: list[str] | None = None,
    ) -> dict:
        """Run a Daily Standup: collect team activity (Jira/AzDO/GitHub/git/docs), score sprint
        confidence, and summarize per member. Returns the report for you to present; deliver=true
        additionally sends it to the session's configured channels (Slack/email/desktop) — ask the
        user before enabling. channels overrides the saved channels for this run (terminal,
        desktop, slack, email). tracker_sources/team_members override the saved Team scope;
        code_sources, github_repositories (owner/repo), and azdo_projects override the saved
        code scope without changing it. azdo_repositories is a legacy compatibility override.
        documentation_sources selects
        Confluence/Notion providers without changing saved config. days overrides the activity look-back
        window. Blank session_id = most recent session."""
        return await run_engine(
            ctx,
            _standup_run,
            session_id,
            deliver,
            days,
            channels,
            tracker_sources,
            team_members,
            code_sources,
            github_repositories,
            azdo_projects,
            azdo_repositories,
            documentation_sources,
        )

    @app.tool()
    async def standup_members(session_id: str = "", tracker_sources: list[str] | None = None) -> dict:
        """Preview standup team candidates from Jira, Azure DevOps, or both. Valid source
        names are jira and azure_devops; omitted sources use the saved/default selection."""
        return await run_readonly(_standup_members, session_id, tracker_sources)

    @app.tool()
    async def standup_repositories(code_sources: list[str] | None = None) -> dict:
        """Discover accessible GitHub repositories and Azure DevOps projects for Standup code scope."""
        return await run_readonly(_standup_repositories, code_sources)

    @app.tool()
    async def standup_history(session_id: str = "", limit: int = 30) -> dict:
        """Get recent Daily Standup runs for a session, including the latest full report.
        Blank session_id = most recent session."""
        return await run_readonly(_standup_history, session_id, limit)

    @app.tool()
    async def standup_config_get(session_id: str = "") -> dict:
        """Get a session's standup configuration (time, weekdays, delivery channels, aliases).
        config is null when nothing is configured yet. Blank session_id = most recent session."""
        return await run_readonly(_standup_config_get, session_id)

    @app.tool()
    async def standup_config_set(
        session_id: str = "",
        enabled: bool | None = None,
        time: str = "",
        weekdays: str = "",
        delivery_channels: list[str] | None = None,
        lead_minutes: int = -1,
        repo_path: str | None = None,
        my_aliases: str | None = None,
        tracker_sources: list[str] | None = None,
        team_members: list[str] | None = None,
        code_sources: list[str] | None = None,
        github_repositories: list[str] | None = None,
        azdo_projects: list[str] | None = None,
        azdo_repositories: list[str] | None = None,
        documentation_sources: list[str] | None = None,
        automation_markers: str | None = None,
        automation_handling: str | None = None,
        transcript_dir: str | None = None,
        transcript_review_enabled: bool | None = None,
    ) -> dict:
        """Update a session's standup configuration; omitted fields keep their current value.
        time is HH:MM (the meeting time), weekdays like '1-5' or '1,3,5', delivery_channels from
        terminal/desktop/slack/email, my_aliases a comma-separated identity list across tools,
        tracker_sources a subset of jira/azure_devops, team_members the authoritative roster,
        code_sources a subset of github/azure_devops, github_repositories and azdo_projects
        define the explicit code scope,
        and documentation_sources a subset of confluence/notion.
        automation_markers is a comma-separated list of content signatures (e.g. 'wiz') marking
        service-hook/bot comments posted under a member's identity; automation_handling is
        'exclude' (drop detected automation from member credit, with a notice) or 'off'.
        transcript_dir is an optional EXTERNAL folder of standup meeting transcripts (the
        managed ~/.yeaboi/transcripts folder is always swept); transcript_review_enabled
        turns off the automatic transcript review that runs before each standup.
        NOTE: this saves the config only — installing the OS schedule (launchd/cron) is
        machine-local and done from the yeaboi TUI. Blank session_id = most recent session."""
        return await run_readonly(
            _standup_config_set,
            session_id,
            enabled,
            time,
            weekdays,
            delivery_channels,
            lead_minutes,
            repo_path,
            my_aliases,
            tracker_sources,
            team_members,
            code_sources,
            github_repositories,
            azdo_projects,
            azdo_repositories,
            documentation_sources,
            automation_markers,
            automation_handling,
            transcript_dir,
            transcript_review_enabled,
        )
