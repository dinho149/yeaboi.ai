"""Native routes for the Solo world.

The desktop's Solo home renders the same "where am I" snapshot the terminal's
welcome strip does, from the one builder in :mod:`yeaboi.solo.today` — so the
two surfaces cannot say different things about today.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Iterator

from yeaboi.app.router import HTTPError, Request, Response, json_response
from yeaboi.mcp.runtime import to_jsonable

logger = logging.getLogger(__name__)


def today(app, request: Request) -> Response:
    """``GET /api/solo/today?project_id=`` — the TodaySnapshot, text and numbers only."""
    from yeaboi.solo.today import build_today_snapshot

    project_id = (request.query.get("project_id") or "").strip()
    logger.info("solo today requested (project=%s)", project_id or "-")
    return json_response(to_jsonable(build_today_snapshot(project_id=project_id)))


# ---------------------------------------------------------------------------
# Weekly Review — the Solo world's own capability
# ---------------------------------------------------------------------------

#: The stream is over. Never reaches the wire.
_END = object()


def _beta_notice() -> str:
    from yeaboi.beta import WEEKLY_REVIEW_BETA_NOTICE

    return WEEKLY_REVIEW_BETA_NOTICE


def review(app, request: Request) -> Response:
    """``GET /api/solo/review`` — the latest review, the history, and last week's actions."""
    from yeaboi.paths import get_db_path
    from yeaboi.projects.scope import resolve_scope
    from yeaboi.solo.engine import carried_actions
    from yeaboi.solo.store import WeeklyReviewStore

    session_id = str(request.query.get("session_id", "")).strip()
    project_id = str(request.query.get("project_id", "")).strip()
    path = get_db_path()
    scope = resolve_scope(project_id, session_id, db_path=path)
    session_ids = scope.session_ids if scope is not None else None
    with WeeklyReviewStore(path) as store:
        history = store.get_all_history(limit=30, session_ids=session_ids)
        latest = store.get_latest_report(session_ids=session_ids)
    latest_row = history[0]["id"] if history and latest is not None else 0
    logger.info("weekly review page: project=%s history=%d", project_id or "-", len(history))
    return json_response(
        {
            "latest": {"run_id": latest_row, "review": to_jsonable(latest)} if latest is not None else None,
            "history": history,
            "carried": [to_jsonable(a) for a in carried_actions(scope, db_path=path)],
            "beta_notice": _beta_notice(),
        }
    )


def review_run_get(app, request: Request) -> Response:
    """``GET /api/solo/review/runs/{run_id}`` — one saved review; 404 when unknown."""
    from yeaboi.paths import get_db_path
    from yeaboi.solo.store import WeeklyReviewStore

    run_id = _run_id(request)
    with WeeklyReviewStore(get_db_path()) as store:
        found = store.get_run_by_id(run_id)
    if found is None:
        raise HTTPError(404, f"no weekly review {run_id}")
    return json_response({"run_id": run_id, "review": to_jsonable(found)})


def review_delete(app, request: Request) -> Response:
    """``POST /api/solo/review/runs/{run_id}/delete`` — drop one review from the hub."""
    from yeaboi.paths import get_db_path
    from yeaboi.solo.store import WeeklyReviewStore

    run_id = _run_id(request)
    with WeeklyReviewStore(get_db_path()) as store:
        deleted = store.delete_run(run_id)
    if not deleted:
        raise HTTPError(404, f"no weekly review {run_id}")
    logger.info("Weekly review deleted: id=%s", run_id)
    return json_response({"deleted": True, "run_id": run_id})


def review_run(app, request: Request) -> Response:
    """``POST /api/solo/review/run`` — one weekly review, streamed as NDJSON.

    Same line shapes as the standup run: ``op`` first, then a ``progress`` line
    per engine phase, then ``done`` with the stored run's id and the review. Not
    cancellable — the engine has no cancel seam — so the op is never a promise.
    """
    payload = request.json()
    session_id = str(payload.get("session_id", "")).strip()
    project_id = str(payload.get("project_id", "")).strip()
    context_deps = payload.get("context_deps")
    if context_deps is not None and not isinstance(context_deps, list):
        raise HTTPError(400, "context_deps must be a list of tokens or null")
    week_end = str(payload.get("week_end", "")).strip()
    if week_end:
        from yeaboi.timeparse import parse_date

        try:
            parse_date(week_end)
        except ValueError:
            raise HTTPError(400, "week_end must be an ISO date (YYYY-MM-DD)") from None
    carried_statuses = payload.get("carried_statuses")
    if carried_statuses is not None and not isinstance(carried_statuses, dict):
        raise HTTPError(400, "carried_statuses must be an object of {action_id: status}")
    op = app.ops.create()
    logger.info(
        "Weekly review run start: session=%s project=%s week_end=%s marks=%d",
        session_id or "-",
        project_id or "-",
        week_end or "today",
        len(carried_statuses or {}),
    )
    return Response(
        content_type="application/x-ndjson",
        stream=_lines(_run(app, op, session_id, project_id, context_deps, week_end, carried_statuses)),
        headers=(("X-Accel-Buffering", "no"),),
    )


def _run(app, op, session_id, project_id, context_deps, week_end, carried_statuses) -> Iterator[dict]:
    from yeaboi.mcp.runtime import _ENGINE_LOCK

    events: queue.Queue = queue.Queue()
    result: list = [None, None]  # review, failure

    last_phase = [""]

    def on_progress(event) -> None:
        # One progress line per phase transition; the engine's detail refreshes
        # and done events stay off the wire (the contract names the seven ids).
        from yeaboi.analysis.progress import is_component_progress

        if not is_component_progress(event) or event["status"] != "running":
            return
        phase = event["component_id"]
        if phase != last_phase[0]:
            last_phase[0] = phase
            events.put({"type": "progress", "phase": phase})

    def worker() -> None:
        from yeaboi.solo.engine import run_weekly_review

        try:
            # Engines are one-at-a-time process-wide. Never fork this lock.
            with _ENGINE_LOCK:
                result[0] = run_weekly_review(
                    session_id=session_id,
                    project_id=project_id,
                    context_deps=context_deps,
                    week_end=week_end,
                    carried_statuses=carried_statuses,
                    on_progress=on_progress,
                )
        except BaseException as exc:  # noqa: BLE001 — reported on the stream below
            result[1] = exc
        finally:
            events.put(_END)

    thread = threading.Thread(target=worker, name="weekly-review-run", daemon=True)
    thread.start()
    try:
        yield {"type": "op", "op_id": op.op_id}
        while (event := events.get()) is not _END:
            yield event
        thread.join()
        if result[1] is not None:
            yield _error_line(result[1])
        else:
            yield {"type": "done", "run_id": _latest_run_id(result[0]), "review": to_jsonable(result[0])}
    finally:
        app.ops.remove(op.op_id)


def _latest_run_id(review) -> int:
    """The history row the engine just wrote — the newest for its session."""
    from yeaboi.paths import get_db_path
    from yeaboi.solo.store import WeeklyReviewStore

    try:
        with WeeklyReviewStore(get_db_path()) as store:
            rows = store.get_history(getattr(review, "session_id", ""), limit=1)
    except Exception:  # noqa: BLE001 — the review itself is already on the stream
        logger.warning("weekly review: could not read back the run id", exc_info=True)
        return 0
    return int(rows[0]["id"]) if rows else 0


def _error_line(error: BaseException) -> dict:
    # The one place SDK exceptions become human text — never str(exc), which for
    # a JIRAError is its entire HTTP response.
    from yeaboi.ui.session._utils import _classify_api_error

    message = _classify_api_error(error) if isinstance(error, Exception) else "The run stopped unexpectedly."
    logger.error("Weekly review run failed: %s", message)
    return {"type": "error", "message": message}


def _lines(objects: Iterator[dict]) -> Iterator[bytes]:
    for obj in objects:
        yield (json.dumps(obj, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def _run_id(request: Request) -> int:
    try:
        return int(request.params.get("run_id", ""))
    except ValueError:
        raise HTTPError(400, "run_id must be a number") from None
