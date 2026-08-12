"""MCP tools: Daily Standup (run a standup, read history, get/set the config)."""

from __future__ import annotations

import logging
import re

# Context must be importable from module globals — FastMCP evaluates the
# stringified type hints (PEP 563) of tool functions against this namespace.
from mcp.server.fastmcp import Context

from yeaboi.mcp.runtime import run_engine, run_readonly, to_jsonable

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
    "github_owners": [],
    "github_repositories": [],
    "github_excluded_repositories": [],
    "azdo_projects": [],
    "azdo_repositories": [],
    "code_scope_configured": False,
    "documentation_sources": [],
    "documentation_scope_configured": False,
    "automation_markers": "",
    "automation_handling": "exclude",
    "transcript_dir": "",
    "transcript_review_enabled": True,
    "habit_detection": "on",
    "habit_rules": "",
    "habit_ai_match": "on",
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
    github_owners: list | None,
    github_repositories: list | None,
    github_excluded_repositories: list | None,
    azdo_projects: list | None,
    azdo_repositories: list | None,
    documentation_sources: list | None,
    review_transcripts: bool,
):
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.standup.engine import run_standup

    resolved = resolve_session_id(session_id)
    return run_standup(
        resolved,
        review_transcripts=review_transcripts,
        deliver=deliver,
        days=days or None,
        channels=_validated_channels(channels),
        tracker_sources=tracker_sources,
        team_members=team_members,
        code_sources=code_sources,
        github_owners=github_owners,
        github_repositories=github_repositories,
        github_excluded_repositories=github_excluded_repositories,
        azdo_projects=azdo_projects,
        azdo_repositories=azdo_repositories,
        documentation_sources=documentation_sources,
    )


def _standup_review(
    session_id: str,
    transcript_paths: list | None,
    transcript_text: str,
    transcript_dir: str,
    standup_date: str,
    max_transcripts: int,
    include_reviewed: bool,
    file_issues: bool,
):
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.standup.engine import file_transcript_issues, run_transcript_review

    if transcript_dir:
        # Sandbox check at write time, same as repo_path: the sweep reads files
        # out of this folder, so it must clear the policy before we try.
        from yeaboi.fs_policy import resolve_and_check

        resolve_and_check(transcript_dir, mode="read", context="standup transcript_dir")

    resolved = resolve_session_id(session_id)
    review = run_transcript_review(
        resolved,
        transcript_paths=transcript_paths,
        transcript_text=transcript_text,
        transcript_dir=transcript_dir,
        standup_date=standup_date,
        max_transcripts=max_transcripts,
        include_reviewed=include_reviewed,
    )
    if not file_issues:
        return review
    # Filing is a separate, explicit act — the review itself never publishes.
    filing = file_transcript_issues(review.review_id, session_id=resolved)
    return {"review": review, "filing": filing}


def _standup_gaps(session_id: str, limit: int) -> dict:
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.paths import get_db_path
    from yeaboi.standup.engine import transcript_nudge
    from yeaboi.standup.store import StandupStore

    resolved = resolve_session_id(session_id)
    with StandupStore(get_db_path()) as store:
        reviews = store.get_reviews(resolved, limit=limit)
        latest = store.get_latest_review(resolved)
        ledger = store.get_gap_issues(limit=limit)
    # to_jsonable only converts a TOP-LEVEL dataclass; nested inside this dict
    # both of these would fall to default=str and reach the agent as their str()
    # repr instead of a structured object (same as tools_poker/tools_reporting).
    return {
        "session_id": resolved,
        "reviews": reviews,
        "latest_review": to_jsonable(latest) if latest is not None else None,
        "gap_issues": ledger,
        # Which standups ran without ever being checked against their meeting.
        "nudge": to_jsonable(transcript_nudge(resolved)),
    }


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


def _standup_practice_feedback(session_id: str, member: str, rule: str, verdict: str, note: str, run_id: int) -> dict:
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.paths import get_db_path
    from yeaboi.standup import practice_feedback
    from yeaboi.standup.habits import ALL_RULES
    from yeaboi.standup.store import StandupStore

    if not str(member or "").strip():
        raise ValueError("member is required — a verdict is always about one person's signal")
    if rule not in ALL_RULES:
        raise ValueError(f"unknown practice rule {rule!r} — valid: {', '.join(ALL_RULES)}")
    if verdict not in practice_feedback.VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(practice_feedback.VERDICTS)}, got {verdict!r}")

    resolved = resolve_session_id(session_id)
    member = member.strip()
    with StandupStore(get_db_path()) as store:
        # 0 is the MCP-schema stand-in for "not given" (the tool takes an int,
        # not an optional), and means the session's latest run.
        target = run_id or store.get_latest_run_id(resolved)
        # Read the signal before the write so an unapplied verdict can say which
        # of the two ordinary causes it hit. Neither is an error: one is a stale
        # view, the other a report older than this feature.
        signal = practice_feedback.find_signal(store.get_run_by_id(target) if target else None, member, rule)
        applied = practice_feedback.apply_verdict(
            store,
            session_id=resolved,
            member=member,
            rule=rule,
            verdict=verdict,
            note=note or "",
            run_id=target,
        )
        ledger = practice_feedback.load(store, resolved)
    if applied:
        reason = ""
    elif signal is None:
        reason = f"no {rule} signal for {member} in that run — it may already have been answered"
    else:
        reason = f"that {rule} signal predates practice feedback, so there is nothing to remember"
    return {
        "session_id": resolved,
        "applied": applied,
        "reason": reason,
        "excused_changes": len(ledger.excused),
        "confirmed_changes": len(ledger.confirmed),
    }


def _standup_repositories(code_sources: list | None) -> dict:
    from yeaboi.standup.code_scope import (
        CODE_SOURCES,
        SOURCE_GITHUB,
        discover_code_repositories,
        discover_github_repositories,
        validate_code_sources,
    )

    selected = validate_code_sources(code_sources or list(CODE_SOURCES))
    discovered = discover_code_repositories(selected)
    return {
        "code_sources": selected,
        # Owners/organisations, the GitHub analog of an Azure project: one entry
        # stands for every repository inside it. github_repositories stays for the
        # narrower "these exact repos" scope and is listed separately — a second
        # page-through, not free, but it answers a different question (every repo
        # the token can see, unfiltered by activity) and dropping the key would
        # silently break any caller that pins repositories.
        "github_owners": discovered.get("github", []),
        "github_repositories": discover_github_repositories() if SOURCE_GITHUB in selected else [],
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
    github_owners: list | None,
    github_repositories: list | None,
    github_excluded_repositories: list | None,
    azdo_projects: list | None,
    azdo_repositories: list | None,
    documentation_sources: list | None,
    automation_markers: str | None,
    automation_handling: str | None,
    transcript_dir: str | None,
    transcript_review_enabled: bool | None,
    habit_detection: str | None,
    habit_rules: str | None,
    habit_ai_match: str | None,
) -> dict:
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.paths import get_db_path
    from yeaboi.standup.automation import VALID_AUTOMATION_HANDLING
    from yeaboi.standup.code_scope import validate_code_sources
    from yeaboi.standup.documentation_scope import validate_documentation_sources
    from yeaboi.standup.habits import VALID_HABIT_HANDLING, validate_habit_rules
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
    if habit_detection is not None and habit_detection not in VALID_HABIT_HANDLING:
        raise ValueError(f"habit_detection must be one of {', '.join(VALID_HABIT_HANDLING)}, got {habit_detection!r}")
    if habit_ai_match is not None and habit_ai_match not in VALID_HABIT_HANDLING:
        raise ValueError(f"habit_ai_match must be one of {', '.join(VALID_HABIT_HANDLING)}, got {habit_ai_match!r}")
    # Raises on an unknown rule id rather than silently dropping it — a typo'd
    # rule would otherwise read as "that rule is switched off".
    normalized_rules = None if habit_rules is None else validate_habit_rules(habit_rules)
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
            "github_owners": (
                current.get("github_owners", []) if github_owners is None else list(dict.fromkeys(github_owners))
            ),
            "github_repositories": (
                current.get("github_repositories", [])
                if github_repositories is None
                else list(dict.fromkeys(github_repositories))
            ),
            "github_excluded_repositories": (
                current.get("github_excluded_repositories", [])
                if github_excluded_repositories is None
                else list(dict.fromkeys(github_excluded_repositories))
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
                or github_owners is not None
                or github_repositories is not None
                or github_excluded_repositories is not None
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
            "habit_detection": (current.get("habit_detection", "on") if habit_detection is None else habit_detection),
            "habit_rules": (current.get("habit_rules", "") if normalized_rules is None else normalized_rules),
            "habit_ai_match": (current.get("habit_ai_match", "on") if habit_ai_match is None else habit_ai_match),
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
        github_owners: list[str] | None = None,
        github_repositories: list[str] | None = None,
        github_excluded_repositories: list[str] | None = None,
        azdo_projects: list[str] | None = None,
        azdo_repositories: list[str] | None = None,
        documentation_sources: list[str] | None = None,
        review_transcripts: bool = True,
    ) -> dict:
        """Run a Daily Standup: collect team activity (Jira/AzDO/GitHub/git/docs), score sprint
        confidence, and summarize per member. Returns the report for you to present; deliver=true
        additionally sends it to the session's configured channels (Slack/email/desktop) — ask the
        user before enabling. channels overrides the saved channels for this run (terminal,
        desktop, slack, email). tracker_sources/team_members override the saved Team scope;
        code_sources, github_owners (a GitHub org/user — covers every active repo inside it, like
        an Azure project), github_repositories (exact owner/repo slugs), and azdo_projects override
        the saved code scope without changing it. github_excluded_repositories drops specific
        owner/repo slugs from an included owner's expansion — never widens scope, only trims it.
        azdo_repositories is a legacy compatibility override.
        documentation_sources selects
        Confluence/Notion providers without changing saved config. days overrides the activity look-back
        window. review_transcripts (default true) first reviews any unreviewed standup meeting
        transcripts covering earlier dates, so yesterday's corrections inform today's report; it
        drafts issues locally and never writes to GitHub. Blank session_id = most recent session."""
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
            github_owners,
            github_repositories,
            github_excluded_repositories,
            azdo_projects,
            azdo_repositories,
            documentation_sources,
            review_transcripts,
        )

    @app.tool()
    async def standup_review(
        ctx: Context,
        session_id: str = "",
        transcript_paths: list[str] | None = None,
        transcript_text: str = "",
        transcript_dir: str = "",
        standup_date: str = "",
        max_transcripts: int = 5,
        include_reviewed: bool = False,
        file_issues: bool = False,
    ) -> dict:
        """Review standup meeting transcripts against the reports they discussed, to find what
        standup missed and why. Reads .txt/.md/.vtt/.srt/.json transcripts from ~/.yeaboi/transcripts
        (and the configured transcript_dir), checks what each person said they did against the
        evidence the report actually had, and diagnoses each gap: a missing integration, an
        unconfigured source, a capability the collectors lack, or a summary that dropped what it
        collected. Product-level gaps are drafted as GitHub issues against the yeaboi repo; config
        gaps come back as suggestions with an exact remedy and are never filed.
        transcript_paths reviews specific files instead of sweeping. transcript_text reviews raw
        transcript text you already have (a paste, a meeting-notes doc) — it is saved into
        ~/.yeaboi/transcripts first and then reviewed like any other file, so there is no need to
        ask the user to save it themselves. standup_date attributes transcripts whose own date
        cannot be inferred, and for transcript_text it wins outright. max_transcripts caps distinct standup DATES
        (one AI call each). include_reviewed re-reviews transcripts already processed.
        file_issues=true WRITES PUBLIC GITHUB ISSUES — always ask the user before enabling it;
        the default drafts them locally so they can be reviewed first.
        Blank session_id = most recent session."""
        return await run_engine(
            ctx,
            _standup_review,
            session_id,
            transcript_paths,
            transcript_text,
            transcript_dir,
            standup_date,
            max_transcripts,
            include_reviewed,
            file_issues,
        )

    @app.tool()
    async def standup_gaps(session_id: str = "", limit: int = 30) -> dict:
        """List past standup transcript reviews and the gap→GitHub-issue ledger for a session.
        Shows which diagnosed gaps have been filed, which recurred, and their issue numbers —
        read-only. Blank session_id = most recent session."""
        return await run_readonly(_standup_gaps, session_id, limit)

    @app.tool()
    async def standup_members(session_id: str = "", tracker_sources: list[str] | None = None) -> dict:
        """Preview standup team candidates from Jira, Azure DevOps, or both. Valid source
        names are jira and azure_devops; omitted sources use the saved/default selection."""
        return await run_readonly(_standup_members, session_id, tracker_sources)

    @app.tool()
    async def standup_repositories(code_sources: list[str] | None = None) -> dict:
        """Discover the Standup code scope you can pick from: GitHub owners/organisations
        (github_owners — each covers every active repo inside it) and Azure DevOps projects,
        plus github_repositories for pinning exact repos."""
        return await run_readonly(_standup_repositories, code_sources)

    @app.tool()
    async def standup_history(session_id: str = "", limit: int = 30) -> dict:
        """Get recent Daily Standup runs for a session, including the latest full report.
        Blank session_id = most recent session."""
        return await run_readonly(_standup_history, session_id, limit)

    @app.tool()
    async def standup_practice_feedback(
        member: str,
        rule: str,
        verdict: str,
        session_id: str = "",
        note: str = "",
        run_id: int = 0,
    ) -> dict:
        """Tell the standup whether an engineering-practice signal was right about someone.

        member is the name exactly as it appears in the report; rule is one of untracked-work,
        untracked-docs, board-not-updated, wip-sprawl, large-change, no-pull-request,
        commit-messages; verdict is 'down' (the signal was wrong) or 'up' (it was right).

        'down' removes that signal from the stored report and remembers every change behind it,
        so none of them is ever reported for that rule again. 'up' leaves the report alone and
        records the change as a confirmed true positive. Both feed the optional LLM matching pass
        as calibration, so note is worth writing on a 'down' — one sentence on why it was wrong
        (e.g. 'that PR is the spike ticket, it just does not name it').

        Returns applied=false, with a reason, when that member has no such signal in the run —
        an already-voted or already-regenerated report, not an error. run_id 0 = the latest run.
        Blank session_id = most recent session."""
        return await run_readonly(_standup_practice_feedback, session_id, member, rule, verdict, note, run_id)

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
        github_owners: list[str] | None = None,
        github_repositories: list[str] | None = None,
        github_excluded_repositories: list[str] | None = None,
        azdo_projects: list[str] | None = None,
        azdo_repositories: list[str] | None = None,
        documentation_sources: list[str] | None = None,
        automation_markers: str | None = None,
        automation_handling: str | None = None,
        transcript_dir: str | None = None,
        transcript_review_enabled: bool | None = None,
        habit_detection: str | None = None,
        habit_rules: str | None = None,
        habit_ai_match: str | None = None,
    ) -> dict:
        """Update a session's standup configuration; omitted fields keep their current value.
        time is HH:MM (the meeting time), weekdays like '1-5' or '1,3,5', delivery_channels from
        terminal/desktop/slack/email, my_aliases a comma-separated identity list across tools,
        tracker_sources a subset of jira/azure_devops, team_members the authoritative roster,
        code_sources a subset of github/azure_devops; github_owners (GitHub orgs/users, each
        covering every active repo inside it), github_repositories (exact owner/repo slugs),
        github_excluded_repositories (owner/repo slugs dropped from an included owner's
        expansion — never widens scope, only trims it) and
        azdo_projects define the code scope,
        and documentation_sources a subset of confluence/notion.
        automation_markers is a comma-separated list of content signatures (e.g. 'wiz') marking
        service-hook/bot comments posted under a member's identity; automation_handling is
        'exclude' (drop detected automation from member credit, with a notice) or 'off'.
        transcript_dir is an optional EXTERNAL folder of standup meeting transcripts (the
        managed ~/.yeaboi/transcripts folder is always swept); transcript_review_enabled
        turns off the automatic transcript review that runs before each standup.
        habit_detection turns the deterministic engineering-practice signals on/off ('on' default);
        habit_rules is a comma-separated subset of untracked-work, untracked-docs, board-not-updated,
        wip-sprawl, large-change, no-pull-request, commit-messages (empty = all of them).
        habit_ai_match is 'on' (default) or 'off': when on, an LLM pass may excuse a change that
        belongs to a ticket it never names. It can only ever suppress a signal, never raise one.
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
            github_owners,
            github_repositories,
            github_excluded_repositories,
            azdo_projects,
            azdo_repositories,
            documentation_sources,
            automation_markers,
            automation_handling,
            transcript_dir,
            transcript_review_enabled,
            habit_detection,
            habit_rules,
            habit_ai_match,
        )
