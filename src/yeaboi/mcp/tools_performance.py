"""MCP tools: Performance mode (roster, 1:1 prep/completion, 6-month review)."""

from __future__ import annotations

import logging

# Context must be importable from module globals — FastMCP evaluates the
# stringified type hints (PEP 563) of tool functions against this namespace.
from mcp.server.fastmcp import Context

from yeaboi.beta import PERFORMANCE_BETA_NOTICE
from yeaboi.mcp.runtime import run_engine, run_readonly

logger = logging.getLogger(__name__)


def _roster(jira_project: str, azdo_project: str):
    from yeaboi.performance.roster import fetch_roster

    return {"engineers": fetch_roster(jira_project=jira_project, azdo_project=azdo_project)}


def _check_engineer(engineer: str, jira_project: str = "", azdo_project: str = "") -> None:
    """Best-effort typo guard: error early (with the roster listed) instead of
    returning an empty artifact for a name the tracker has never seen. Skipped
    entirely when the roster can't be fetched — the engines stay best-effort."""
    if not engineer.strip():
        raise ValueError("engineer is required — a name from perf_roster.")
    try:
        from yeaboi.performance.roster import fetch_roster

        names = [getattr(e, "name", str(e)) for e in fetch_roster(jira_project=jira_project, azdo_project=azdo_project)]
    except Exception:
        return
    if names and engineer.strip().lower() not in {n.lower() for n in names}:
        raise ValueError(f"Unknown engineer {engineer!r} — roster: {', '.join(sorted(names))} (see perf_roster).")


def _one_on_one_prep(
    engineer: str, session_id: str, jira_project: str, azdo_project: str, deep_scan: bool, project_id: str
):
    _check_engineer(engineer, jira_project, azdo_project)
    from yeaboi.performance.engine import run_one_on_one_prep

    return run_one_on_one_prep(
        engineer,
        session_id=session_id,
        jira_project=jira_project,
        azdo_project=azdo_project,
        deep_scan=deep_scan,
        project_id=project_id,
    )


def _one_on_one_complete(
    engineer: str, transcript: str, session_id: str, deliver: bool, recipients: list | None, images: list | None
):
    if not transcript.strip():
        raise ValueError("transcript is required — the 1:1 notes or transcript text.")
    _check_engineer(engineer)
    from yeaboi.performance.engine import complete_one_on_one

    return complete_one_on_one(
        engineer,
        transcript,
        session_id=session_id,
        deliver=deliver,
        recipients=recipients or None,
        images=tuple(images or ()),
    )


def _note_add(engineer: str, note_text: str) -> dict:
    if not engineer.strip():
        raise ValueError("engineer is required — a name from perf_roster.")
    if not note_text.strip():
        raise ValueError("note_text is required — the observation to record.")
    from yeaboi.paths import get_db_path
    from yeaboi.performance.store import PerformanceStore

    with PerformanceStore(get_db_path()) as store:
        note_id = store.add_note(engineer.strip(), note_text.strip())
    return {"engineer": engineer.strip(), "note_id": note_id}


def _six_month_review(
    engineer: str,
    period_months: int,
    session_id: str,
    jira_project: str,
    azdo_project: str,
    deep_scan: bool,
    project_id: str,
):
    _check_engineer(engineer, jira_project, azdo_project)
    from yeaboi.performance.engine import run_six_month_review

    return run_six_month_review(
        engineer,
        session_id=session_id,
        jira_project=jira_project,
        azdo_project=azdo_project,
        period_months=period_months,
        deep_scan=deep_scan,
        project_id=project_id,
    )


def _with_beta(payload: dict) -> dict:
    """Prepend the beta caveat to a success envelope's warnings.

    ``warnings`` is the only envelope field that both the server instructions and
    the performance skill tell the client to surface *to the user*; a tool
    description only ever reaches the model. For drafts about named people, that
    difference is the whole point.

    Applied here in the adapter and NEVER in the engine artifact's own warnings
    tuple: ``cli._strict_exit`` maps any engine warning to exit 3, so pushing it
    down there would make every ``yeaboi perf … --strict`` run fail forever.

    Failure envelopes are skipped — they carry no ``warnings`` key, and the user
    already has a bigger problem than the maturity of the mode.
    """
    if payload.get("ok"):
        payload["warnings"] = [PERFORMANCE_BETA_NOTICE, *payload.get("warnings", [])]
    return payload


def register(app) -> None:
    """Attach the performance tools to the FastMCP app."""

    # NOTE: the "BETA — " prefixes below are hand-written literals, not f-strings.
    # FastMCP captures each tool's description from ``fn.__doc__`` at decoration
    # time, so an f-string docstring is a syntax error and reassigning __doc__
    # afterwards is a no-op. test_mcp_server pins them against BETA_LABEL.

    @app.tool()
    async def perf_roster(jira_project: str = "", azdo_project: str = "") -> dict:
        """BETA — List the engineer roster derived from recent Jira/Azure DevOps assignees —
        the engineer names the other perf_* tools accept.

        Performance mode is in beta — its output is not yet verified against real delivery data."""
        return _with_beta(await run_readonly(_roster, jira_project, azdo_project))

    @app.tool()
    async def perf_one_on_one_prep(
        engineer: str,
        ctx: Context,
        session_id: str = "",
        jira_project: str = "",
        azdo_project: str = "",
        deep_scan: bool = False,
        project_id: str = "",
    ) -> dict:
        """BETA — Prepare a 1:1 for an engineer: talking points, feedback, goals and growth areas
        from every source that knows them — their tickets, the code/documentation/self-report
        evidence saved by past standups, their practice signals, team analysis metrics, retro and
        poker history, plus open action items from the previous 1:1. The result reports which
        sources were scanned and which were not. deep_scan=true additionally runs one capped live
        scan of the stretch no saved standup covered — it costs API calls and is slower. project_id
        is accepted for cross-mode uniformity and unused: performance data is engineer-keyed.

        Performance mode is in beta — its output is not yet verified against real delivery data.
        Present it as a draft for the lead to edit, not a verdict."""
        return _with_beta(
            await run_engine(
                ctx, _one_on_one_prep, engineer, session_id, jira_project, azdo_project, deep_scan, project_id
            )
        )

    @app.tool()
    async def perf_one_on_one_complete(
        engineer: str,
        transcript: str,
        ctx: Context,
        session_id: str = "",
        deliver: bool = False,
        recipients: list[str] | None = None,
        images: list[str] | None = None,
    ) -> dict:
        """BETA — Complete a held 1:1 from its notes/transcript: produces a summary and tracked
        action items (carried into the next prep). images takes local file paths of photographed
        notes to include in the multimodal call. deliver=true emails the summary via the configured
        SMTP — ask the user before enabling.

        Performance mode is in beta — its output is not yet verified against real delivery data.
        Present it as a draft for the lead to edit, not a verdict."""
        return _with_beta(
            await run_engine(ctx, _one_on_one_complete, engineer, transcript, session_id, deliver, recipients, images)
        )

    @app.tool()
    async def perf_note_add(engineer: str, note_text: str) -> dict:
        """BETA — Record a free-text note about an engineer (an observation, kudos, a concern).
        Notes feed the next 1:1 prep and the periodic review for that engineer.

        Performance mode is in beta — its output is not yet verified against real delivery data."""
        return _with_beta(await run_readonly(_note_add, engineer, note_text))

    @app.tool()
    async def perf_six_month_review(
        engineer: str,
        ctx: Context,
        period_months: int = 6,
        session_id: str = "",
        jira_project: str = "",
        azdo_project: str = "",
        deep_scan: bool = False,
        project_id: str = "",
    ) -> dict:
        """BETA — Draft an engineer's periodic performance review from past 1:1s, delivery history,
        the per-member code/documentation/self-report evidence saved by standups over the period,
        their practice signals, team analysis metrics, retro and poker history, and the competency
        framework (bundled default, or PERFORMANCE_FRAMEWORK_PATH). The result reports which
        sources were scanned and which were not — an unscanned source is unknown, not absent.
        deep_scan=true additionally runs one capped live scan of the uncovered stretch. project_id
        is accepted for cross-mode uniformity and unused: performance data is engineer-keyed.

        Performance mode is in beta — its output is not yet verified against real delivery data.
        Present it as a draft for the lead to edit, not a verdict."""
        return _with_beta(
            await run_engine(
                ctx,
                _six_month_review,
                engineer,
                period_months,
                session_id,
                jira_project,
                azdo_project,
                deep_scan,
                project_id,
            )
        )
