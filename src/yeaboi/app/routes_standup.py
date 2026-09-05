"""Native standup routes — the dashboard, the run, and the schedule.

The read-only pieces (history, config, roster, repositories, the transcript
review) are MCP tools already, so the desktop calls those through
``/api/tool/<name>``. What lives here is what MCP has no shape for: a whole
dashboard in one read, a run that reports progress while it works, and the OS
jobs a schedule implies.

A run is a chunked NDJSON stream: ``op`` first, then ``progress`` lines as each
pipeline phase starts, then ``done`` with the report. It is deliberately NOT
cancellable — ``run_standup`` has no cancel seam, and an op the client can
address but the engine ignores would be a lie. The op line is still sent, so
the shell can join progress events to a run.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections.abc import Iterator

from yeaboi.app.router import HTTPError, Request, Response, json_response
from yeaboi.app.routes_projects import require_project
from yeaboi.mcp.runtime import to_jsonable

logger = logging.getLogger(__name__)

#: The stream is over. Never reaches the wire.
_END = object()


def dashboard(app, request: Request) -> Response:
    """``GET /api/standup/dashboard`` — every card of one session's standup."""
    from yeaboi.standup import dashboard as view

    try:
        run_id = int(request.query.get("run_id", "0") or 0)
    except ValueError:
        raise HTTPError(400, "run_id must be a number") from None
    data = view.collect(_session_id(request), run_id=run_id)
    return json_response(
        {
            "session_id": data["session_id"],
            "session_name": data["session_name"],
            "my_name": data["my_name"],
            "run_id": data["run_id"],
            # The saved-runs hub: every run this session has done, newest first.
            "history": data["history"],
            "cards": view.cards(data),
            "report": to_jsonable(data["report"]) if data["report"] is not None else None,
            "config": data["config"],
            "schedule": data["schedule"],
            "review": to_jsonable(data["review"]) if data["review"] is not None else None,
            "nudge": to_jsonable(data["nudge"]) if data["nudge"] is not None else None,
            "gap_issues": data["gap_issues"],
            # Who has activity attributed today, by the one rule both surfaces
            # use — old reports carry no count and must not read as all-quiet.
            "active": [
                m.name for m in (data["report"].member_updates if data["report"] else ()) if view.member_active(m)
            ],
        }
    )


def delete_run(app, request: Request) -> Response:
    """``POST /api/standup/runs/{run_id}/delete`` — drop one run from the hub."""
    from yeaboi.paths import get_db_path
    from yeaboi.standup.store import StandupStore

    try:
        run_id = int(request.params.get("run_id", ""))
    except ValueError:
        raise HTTPError(400, "run_id must be a number") from None
    with StandupStore(get_db_path()) as store:
        deleted = store.delete_run(run_id)
    if not deleted:
        raise HTTPError(404, f"no standup run {run_id}")
    logger.info("Standup run deleted: id=%s", run_id)
    return json_response({"deleted": True, "run_id": run_id})


def schedule(app, request: Request) -> Response:
    """``GET /api/standup/schedule`` — the saved fields plus the installed reminder."""
    from yeaboi.standup.schedule import current_schedule

    session_id = _session_id(request)
    if not session_id:
        raise HTTPError(400, "session_id is required")
    return json_response(current_schedule(session_id))


def set_schedule(app, request: Request) -> Response:
    """``POST /api/standup/schedule`` — save the schedule and install its OS jobs."""
    from yeaboi.standup.delivery import ALL_CHANNELS
    from yeaboi.standup.schedule import REMINDER_PRESETS, apply_schedule, current_schedule

    payload = request.json()
    session_id = str(payload.get("session_id", "")).strip()
    if not session_id:
        raise HTTPError(400, "session_id is required")
    channels = [str(c) for c in payload.get("delivery_channels") or ["terminal"]]
    unknown = [c for c in channels if c not in ALL_CHANNELS]
    if unknown:
        raise HTTPError(400, f"unknown delivery channel(s) {unknown} — valid: {', '.join(ALL_CHANNELS)}")
    remind_after = int(payload.get("remind_after", 0))
    if remind_after not in REMINDER_PRESETS:
        raise HTTPError(400, f"remind_after must be one of {', '.join(str(p) for p in REMINDER_PRESETS)}")
    solo = bool(payload.get("solo", False))
    message = apply_schedule(
        session_id,
        enabled=bool(payload.get("enabled")),
        time=str(payload.get("time", "10:00")),
        weekdays=str(payload.get("weekdays", "1-5")),
        lead_minutes=int(payload.get("lead_minutes", 10)),
        delivery_channels=channels,
        remind_after=remind_after,
        solo=solo,
    )
    return json_response({"message": message, "schedule": current_schedule(session_id)})


def run(app, request: Request) -> Response:
    """``POST /api/standup/run`` — one standup, streamed as NDJSON."""
    payload = request.json()
    session_id = str(payload.get("session_id", "")).strip()
    if not session_id:
        raise HTTPError(400, "session_id is required")
    deliver = bool(payload.get("deliver", False))
    solo = bool(payload.get("solo", False))
    project_id = require_project(str(payload.get("project_id") or ""))
    op = app.ops.create()
    logger.info(
        "Standup run start: session=%s deliver=%s solo=%s project=%s", session_id, deliver, solo, project_id or "-"
    )
    return Response(
        content_type="application/x-ndjson",
        stream=_lines(_run(app, op, session_id, deliver, solo, project_id)),
        headers=(("X-Accel-Buffering", "no"),),
    )


def _run(app, op, session_id: str, deliver: bool, solo: bool = False, project_id: str = "") -> Iterator[dict]:
    from yeaboi.mcp.runtime import _ENGINE_LOCK

    events: queue.Queue = queue.Queue()
    result: list = [None, None]  # report, failure

    def worker() -> None:
        from yeaboi.standup.engine import run_standup

        try:
            # Engines are one-at-a-time process-wide. Never fork this lock.
            with _ENGINE_LOCK:
                result[0] = run_standup(
                    session_id,
                    deliver=deliver,
                    solo=solo,
                    project_id=project_id,
                    # A preview must not post to Slack on its way to the screen.
                    dry_run=not deliver,
                    on_progress=lambda phase: events.put({"type": "progress", "phase": phase}),
                    on_run_id=lambda run_id: events.put({"type": "run_id", "run_id": run_id}),
                )
        except BaseException as exc:  # noqa: BLE001 — reported on the stream below
            result[1] = exc
        finally:
            events.put(_END)

    thread = threading.Thread(target=worker, name="standup-run", daemon=True)
    thread.start()
    try:
        yield {"type": "op", "op_id": op.op_id}
        while (event := events.get()) is not _END:
            yield event
        thread.join()
        if result[1] is not None:
            yield _error_line(result[1])
        else:
            yield {"type": "done", "report": to_jsonable(result[0])}
    finally:
        app.ops.remove(op.op_id)


def _error_line(error: BaseException) -> dict:
    # The one place SDK exceptions become human text — never str(exc), which for
    # a JIRAError is its entire HTTP response.
    from yeaboi.ui.session._utils import _classify_api_error

    message = _classify_api_error(error) if isinstance(error, Exception) else "The run stopped unexpectedly."
    logger.error("Standup run failed: %s", message)
    return {"type": "error", "message": message}


def _lines(objects: Iterator[dict]) -> Iterator[bytes]:
    for obj in objects:
        yield (json.dumps(obj, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def _session_id(request: Request) -> str:
    return str(request.query.get("session_id", "")).strip()
