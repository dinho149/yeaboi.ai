"""MCP tools: team-learning (calibration profiles + full board analysis)."""

from __future__ import annotations

import logging

# Context must be importable from module globals — FastMCP evaluates the
# stringified type hints (PEP 563) of tool functions against this namespace.
from mcp.server.fastmcp import Context

from yeaboi.mcp.runtime import run_engine, run_readonly

logger = logging.getLogger(__name__)


class _BridgedProgress(list):
    """The analysis engine's `progress` seam is a shared list its workers append
    status lines to (the TUI reads it every frame). Here each append also fires
    an MCP progress notification. The appends come from the engine's own worker
    threads (not anyio's), so the bridge uses run_coroutine_threadsafe against
    the server loop captured at tool-call time — fire-and-forget."""

    def __init__(self, loop, ctx):
        super().__init__()
        self._loop = loop
        self._ctx = ctx

    def append(self, item) -> None:
        super().append(item)
        try:
            import asyncio

            from yeaboi.analysis.progress import format_analysis_progress

            message = format_analysis_progress(item)
            asyncio.run_coroutine_threadsafe(self._ctx.report_progress(float(len(self)), None, message), self._loop)
        except Exception:
            logger.debug("analysis progress bridge failed (continuing)", exc_info=True)


def _team_analyze(
    source: str,
    project_key: str,
    sprint_count: int,
    generate_samples: bool,
    include_insights: bool,
    include_ai_usage: bool,
    include_doc_quality: bool,
    analysis_depth: str,
    analysis_window_days: int,
    components=None,
    members=None,
    analysis_scope=None,
    analysis_model=None,
    analysis_features=None,
    progress=None,
):
    if source not in ("", "jira", "azdevops", "both"):
        raise ValueError(f"source must be 'jira', 'azdevops', or 'both' (blank auto-detects) — got {source!r}")
    if analysis_depth not in ("quick", "deep"):
        raise ValueError(f"analysis_depth must be 'quick' or 'deep' — got {analysis_depth!r}")
    allowed = {"delivery": ("jira", "azdevops"), "code": ("github", "azdo"), "docs": ("confluence", "notion")}
    if components:
        for comp, subs in components.items():
            if comp not in allowed:
                raise ValueError(f"components keys must be 'delivery'/'code'/'docs' — got {comp!r}")
            bad = [s for s in (subs or []) if s not in allowed[comp]]
            if bad:
                raise ValueError(f"{comp} sub-sources must be a subset of {allowed[comp]} — got {bad!r}")
    allowed_features = {"delivery", "ai_footprint", "code_health", "documentation"}
    bad_features = [feature for feature in (analysis_features or []) if feature not in allowed_features]
    if bad_features:
        raise ValueError(f"analysis_features contains unsupported values: {bad_features!r}")
    from yeaboi.analysis import run_team_analysis

    return run_team_analysis(
        source=source,
        project_key=project_key,
        sprint_count=sprint_count,
        generate_samples=generate_samples,
        include_insights=include_insights,
        include_ai_usage=include_ai_usage,
        include_doc_quality=include_doc_quality,
        analysis_depth=analysis_depth,
        analysis_window_days=analysis_window_days,
        analysis_scope=analysis_scope,
        analysis_model=analysis_model,
        analysis_features=analysis_features,
        components=components,
        members=members,
        progress=progress,
    )


def _team_roster(source: str, project_key: str) -> dict:
    from yeaboi.analysis import get_team_roster

    return {"source": source, "project_key": project_key, "members": get_team_roster(source, project_key)}


def _team_profile_get() -> dict:
    from yeaboi.paths import get_db_path
    from yeaboi.team_profile import TeamProfileStore

    db_path = get_db_path()
    if not db_path.exists():
        return {"profiles": []}
    with TeamProfileStore(db_path) as store:
        profiles = store.list_profiles()
    return {"profiles": profiles}


def _compare_plan_to_actuals(session_id: str, source: str, project_key: str) -> dict:
    import json

    from yeaboi.tools.team_learning import compare_plan_to_actuals

    # A LangChain @tool — .invoke() runs the underlying function; it returns
    # a JSON string (all yeaboi tools do, for the agent loop).
    raw = compare_plan_to_actuals.invoke({"session_id": session_id, "source": source, "project_key": project_key})
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {"raw": raw}


def register(app) -> None:
    """Attach the team-learning tools to the FastMCP app."""

    @app.tool()
    async def team_analyze(
        ctx: Context,
        source: str = "",
        project_key: str = "",
        sprint_count: int = 8,
        generate_samples: bool = False,
        include_insights: bool = True,
        include_ai_usage: bool = True,
        include_doc_quality: bool = True,
        analysis_depth: str = "deep",
        analysis_window_days: int = 120,
        components: dict[str, list[str]] | None = None,
        members: dict[str, list[str]] | None = None,
        analysis_scope: dict[str, list[str]] | None = None,
        analysis_model: str | None = None,
        analysis_features: list[str] | None = None,
    ) -> dict:
        """Analyse the team's tracker history (closed sprints) into a calibration profile:
        velocity, story-point calibration, writing style, DoD signals, plus coaching insights
        and headline stats. The profile is saved and feeds future planning. Quick depth
        performs tracker paging but no LLM calls; Deep may take minutes on a cold cache.
        source: 'jira', 'azdevops', or 'both' (blank auto-detects a single tracker). With 'both'
        the analysis runs once per configured tracker and returns a combined result
        {source:'both', results:{jira:..., azdevops:...}, comparison:[[label,jira,azdevops],...]} —
        the two profiles are kept clearly separate, never blended (project_key is ignored and
        auto-resolved per source). generate_samples additionally drafts
        sample tickets in the team's style (more LLM calls). include_ai_usage also scans the
        team's commits/PRs for AI-tool markers (Co-Authored-By: Claude, Copilot, Cursor, …) and
        reports a detectable AI-adoption footprint — a LOWER BOUND (inline IDE assist leaves no
        trace); set False to skip those GitHub/AzDO network calls. include_doc_quality also reads
        every recently changed page in the configured Notion/Confluence estate and reports
        documentation clarity, ownership, structure, and actionability; set False to skip those calls.
        analysis_depth is "deep" (default, exhaustive AI enrichment) or "quick"
        (deterministic metrics only). analysis_window_days defaults to 120.
        components selects which parts run, each over its OWN sub-sources:
        {"delivery": ["jira","azdevops"], "code": ["github","azdo"], "docs": ["confluence","notion"]}.
        analysis_features independently selects result areas from delivery, ai_footprint,
        code_health, and documentation; None runs every area supported by components.
        Delivery runs one velocity profile PER selected tracker; code and docs are each ONE global
        scan over their selected hosts. An absent/empty component is skipped; None falls back to the
        include_* booleans. Result: {delivery:{tracker:{profile,...}}, code:{signal,examples}|null,
        docs:{signal,examples}|null, comparison, warnings}.
        members re-scopes each delivery tracker's velocity/contributors and is mandatory scope
        for code analysis, e.g. {"jira": ["Alice","Bob"]}. Code results contain only matched
        selected-user commits, authored PRs, activity, and changed files; a blank or unmatched
        scope never falls back to whole-team code. Discover names with team_roster."""
        import asyncio

        try:
            progress = _BridgedProgress(asyncio.get_running_loop(), ctx)
        except RuntimeError:  # non-asyncio backend — run without progress
            progress = None
        return await run_engine(
            ctx,
            _team_analyze,
            source,
            project_key,
            sprint_count,
            generate_samples,
            include_insights,
            include_ai_usage,
            include_doc_quality,
            analysis_depth,
            analysis_window_days,
            components,
            members,
            analysis_scope,
            analysis_model,
            analysis_features,
            progress,
        )

    @app.tool()
    async def team_roster(source: str = "", project_key: str = "") -> dict:
        """List candidate team member names for a tracker (assignees on recent closed
        sprints) — a cheap, no-LLM lookup for building team_analyze's members= filter.
        source: 'jira' or 'azdevops' (blank auto-detects a single tracker)."""
        return await run_readonly(_team_roster, source, project_key)

    @app.tool()
    async def team_profile_get() -> dict:
        """Get stored team calibration profiles (velocity, estimation accuracy, completion rate)
        learned from tracker history via yeaboi --learn."""
        return await run_readonly(_team_profile_get)

    @app.tool()
    async def team_compare_plan_to_actuals(session_id: str = "", source: str = "", project_key: str = "") -> dict:
        """Compare a generated plan to actual sprint outcomes from the tracker: estimated vs
        actual points, planned vs actual sprints, added/removed stories, cycle times. Blank
        session_id = most recent session; source: 'jira' or 'azdo' (auto-detected when blank)."""
        return await run_readonly(_compare_plan_to_actuals, session_id, source, project_key)
