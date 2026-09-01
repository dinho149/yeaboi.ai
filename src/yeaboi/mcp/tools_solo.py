"""MCP tools: the Solo world's Weekly Review (a self-review of your own week)."""

from __future__ import annotations

import logging

# Context must be importable from module globals — FastMCP evaluates the
# stringified type hints (PEP 563) of tool functions against this namespace.
from mcp.server.fastmcp import Context

from yeaboi.mcp.runtime import run_engine, run_readonly, to_jsonable

logger = logging.getLogger(__name__)


def _weekly_review_run(
    session_id: str,
    project_id: str,
    context_deps: list | None,
    week_end: str,
    carried_statuses: dict | None,
):
    from yeaboi.solo.engine import run_weekly_review

    return run_weekly_review(
        session_id=session_id,
        project_id=project_id,
        context_deps=context_deps,
        week_end=week_end,
        carried_statuses=carried_statuses,
    )


def _weekly_review_history(session_id: str, project_id: str, limit: int) -> dict:
    """Past reviews, the newest in full, and last review's actions with their ids."""
    from yeaboi.paths import get_db_path
    from yeaboi.projects.scope import resolve_scope
    from yeaboi.solo.engine import carried_actions
    from yeaboi.solo.store import WeeklyReviewStore

    path = get_db_path()
    scope = resolve_scope(project_id, session_id, db_path=path)
    session_ids = scope.session_ids if scope is not None else None
    with WeeklyReviewStore(path) as store:
        history = store.get_all_history(limit=limit, session_ids=session_ids)
        latest = store.get_latest_report(session_ids=session_ids)
    # to_jsonable only unpacks a top-level dataclass; convert the nested review
    # here so latest is a structured dict rather than its str() repr.
    return {
        "project_id": scope.project_id if scope is not None else "",
        "history": history,
        "latest": to_jsonable(latest) if latest is not None else None,
        "carried": [to_jsonable(a) for a in carried_actions(scope, db_path=path)],
    }


def _weekly_review_export(run_id: int) -> dict:
    from yeaboi.paths import get_db_path
    from yeaboi.solo.export import export_weekly_review
    from yeaboi.solo.store import WeeklyReviewStore

    with WeeklyReviewStore(get_db_path()) as store:
        review = store.get_run_by_id(run_id) if run_id else store.get_latest_report()
    if review is None:
        which = f"run {run_id}" if run_id else "any run"
        raise ValueError(f"No weekly review recorded for {which} — run weekly_review_run first.")
    paths = export_weekly_review(review)
    logger.info("Weekly review exported via MCP: week=%s run_id=%s", review.week_label, run_id or "latest")
    return {"week_label": review.week_label, "markdown": str(paths["markdown"])}


def register(app) -> None:
    """Attach the Weekly Review tools to the FastMCP app."""

    @app.tool()
    async def weekly_review_run(
        ctx: Context,
        session_id: str = "",
        project_id: str = "",
        context_deps: list[str] | None = None,
        week_end: str = "",
        carried_statuses: dict[str, str] | None = None,
    ) -> dict:
        """BETA — Review your own week (the Solo world): what went well, what to change, and
        whether you are on track against your sprint plan, from your own standups, delivered
        tickets and plan. No roster, no board — a draft about one person's week, not a verdict.
        project_id scopes the reads to a project's own runs; blank inherits the session's link,
        and both blank reads the newest of everything. context_deps toggles the cross-mode
        sources ('standup' and 'plan' gate the reads here); null inherits, an empty list is
        incognito. week_end (YYYY-MM-DD) picks the week — its Monday through that date; blank
        is this week so far. carried_statuses marks last review's actions by id (from
        weekly_review_history's 'carried'): {id: 'done'|'dropped'|'pending'|'carried'}. The
        review is stored and exported to Markdown."""
        return await run_engine(
            ctx, _weekly_review_run, session_id, project_id, context_deps, week_end, carried_statuses
        )

    @app.tool()
    async def weekly_review_history(session_id: str = "", project_id: str = "", limit: int = 12) -> dict:
        """BETA — Past weekly reviews (newest first), the latest one in full, and 'carried': last
        review's still-open actions with the ids weekly_review_run's carried_statuses takes.
        project_id / session_id scope the list like weekly_review_run; blank = everything."""
        return await run_readonly(_weekly_review_history, session_id, project_id, limit)

    @app.tool()
    async def weekly_review_export(run_id: int = 0) -> dict:
        """BETA — Export one weekly review (run_id from weekly_review_history; 0 = the latest) as
        Markdown under ~/.yeaboi/exports/solo/ and return the path."""
        return await run_readonly(_weekly_review_export, run_id)
