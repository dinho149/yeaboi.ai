"""Native team-analysis routes — the setup options, the run, and a saved result.

``team_analyze`` and ``team_roster`` are MCP tools, so a headless caller already
has them. What lives here is what MCP has no shape for: what this machine is
configured to offer a setup wizard, a run that reports progress and can be
stopped while it works, and a stored profile with the cards it earned.

A run is a chunked NDJSON stream: ``op`` first, then ``progress`` lines drained
off the engine's shared list, then ``done`` with the result. Cancelling the op
sets the engine's ``cancel_event``, which aborts queued jobs at pickup and
raises before anything is persisted.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator

from yeaboi.app.router import HTTPError, Request, Response, json_response
from yeaboi.mcp.runtime import to_jsonable

logger = logging.getLogger(__name__)

#: How often the run generator drains the engine's progress list. The engine
#: appends from worker threads and never calls back, so this is a poll.
_PROGRESS_POLL_SECONDS = 0.2


def options(app, request: Request) -> Response:
    """``GET /api/analysis/options`` — what a setup wizard may offer here."""
    from yeaboi.analysis import setup

    grid = setup.available_grid()
    return json_response(
        {
            "grid": grid,
            "features": [{"key": key, "label": label} for key, label in setup.FEATURES.items()],
            # A feature with no configured sub-source cannot be selected.
            "features_available": {
                "delivery": bool(grid["delivery"]),
                "ai_footprint": bool(grid["code"]),
                "code_health": bool(grid["code"]),
                "documentation": bool(grid["docs"]),
            },
            "steps": list(setup.STEPS),
            "depths": list(setup.DEPTHS),
            "default_depth": setup.DEFAULT_DEPTH,
            "window_presets": list(setup.WINDOW_PRESETS),
            "default_window_days": setup.DEFAULT_WINDOW_DAYS,
        }
    )


def steps(app, request: Request) -> Response:
    """``POST /api/analysis/steps`` — which steps a partial selection still needs.

    A round-trip rather than a rule the renderer keeps its own copy of: the
    terminal and the desktop must walk the same wizard, and a second copy of
    the applicability rules is a second thing to drift.
    """
    from yeaboi.analysis import setup

    answers = request.json()
    features = answers.get("features")
    components = answers.get("components") or {}
    depth = str(answers.get("depth", setup.DEFAULT_DEPTH))
    model_offered = bool(answers.get("model_offered"))
    solo = bool(answers.get("solo"))
    applicable = [
        step
        for step in setup.STEPS
        if setup.step_applies(
            step, features=features, components=components, depth=depth, model_offered=model_offered, solo=solo
        )
    ]
    roster_fallback = answers.get("roster_fallback") or setup.available_trackers()
    return json_response(
        {
            "steps": applicable,
            "grid": setup.filtered_grid(answers.get("grid") or setup.available_grid(), features),
            "run": setup.run_config(answers, roster_fallback=roster_fallback, model_offered=model_offered, solo=solo),
        }
    )


def profiles(app, request: Request) -> Response:
    """``GET /api/analysis/profiles`` — the saved profiles, newest first."""
    from yeaboi.paths import get_db_path
    from yeaboi.team_profile import TeamProfileStore

    db_path = get_db_path()
    if not db_path.exists():
        return json_response({"profiles": []})
    with TeamProfileStore(db_path) as store:
        saved = store.list_profiles()
    return json_response({"profiles": [to_jsonable(profile) for profile in saved]})


def result(app, request: Request) -> Response:
    """``GET /api/analysis/result/{team_id}`` — one profile plus its card list."""
    from yeaboi.analysis import dashboard
    from yeaboi.paths import get_db_path
    from yeaboi.team_profile import TeamProfileStore

    team_id = request.params.get("team_id", "")
    solo = request.query.get("solo") in ("1", "true")
    db_path = get_db_path()
    profile, examples = (None, None)
    if db_path.exists():
        with TeamProfileStore(db_path) as store:
            profile, examples = store.load_with_examples(team_id)
    if profile is None:
        raise HTTPError(404, f"no saved analysis {team_id!r}")
    return json_response(
        {
            "team_id": team_id,
            # A stored profile carries no top-level signals — the global scans
            # were persisted onto it, which is where the cards read them.
            "cards": dashboard.cards(profile, examples=examples, solo=solo),
            "profile": to_jsonable(profile),
            "examples": to_jsonable(examples or {}),
        }
    )


def run(app, request: Request) -> Response:
    """``POST /api/analysis/run`` — one analysis, streamed as NDJSON."""
    from yeaboi.analysis import setup

    payload = request.json()
    features = payload.get("features")
    if features is not None:
        unknown = [f for f in features if f not in setup.FEATURES]
        if unknown:
            raise HTTPError(400, f"unknown analysis feature(s) {unknown} — valid: {', '.join(setup.FEATURES)}")
    source = str(payload.get("source", ""))
    if source not in ("", "jira", "azdevops", "both"):
        raise HTTPError(400, "source must be 'jira', 'azdevops' or 'both' (blank auto-detects)")
    depth = str(payload.get("depth", setup.DEFAULT_DEPTH))
    if depth not in setup.DEPTHS:
        raise HTTPError(400, f"depth must be one of {', '.join(setup.DEPTHS)}")
    op = app.ops.create()
    logger.info("Analysis run start: source=%s depth=%s features=%s", source or "auto", depth, features)
    return Response(
        content_type="application/x-ndjson",
        stream=_lines(_run(app, op, payload, depth)),
        headers=(("X-Accel-Buffering", "no"),),
    )


def _run(app, op, payload: dict, depth: str) -> Iterator[dict]:
    from yeaboi.analysis.progress import format_analysis_progress
    from yeaboi.mcp.runtime import _ENGINE_LOCK

    progress: list = []
    result_box: list = [None, None]  # result, failure
    done = threading.Event()

    def worker() -> None:
        from yeaboi.analysis.engine import run_team_analysis

        try:
            # Engines are one-at-a-time process-wide. Never fork this lock.
            with _ENGINE_LOCK:
                result_box[0] = run_team_analysis(
                    source=str(payload.get("source", "")),
                    project_key=str(payload.get("project_key", "")),
                    team_name=str(payload.get("team_name", "")),
                    sprint_count=int(payload.get("sprint_count", 8)),
                    components=payload.get("components"),
                    members=payload.get("members_map"),
                    analysis_depth=depth,
                    analysis_window_days=int(payload.get("window_days", 120)),
                    analysis_scope=payload.get("analysis_scope"),
                    analysis_model=payload.get("model"),
                    analysis_features=payload.get("features"),
                    progress=progress,
                    cancel_event=op.cancel,
                )
        except BaseException as exc:  # noqa: BLE001 — reported on the stream below
            result_box[1] = exc
        finally:
            done.set()

    thread = threading.Thread(target=worker, name="analysis-run", daemon=True)
    thread.start()
    try:
        yield {"type": "op", "op_id": op.op_id}
        sent = 0
        while True:
            finished = done.wait(_PROGRESS_POLL_SECONDS)
            # One snapshot, then advance by what that snapshot held: reading the
            # length separately would skip anything appended in between. The
            # drain happens after the wait reports done, so the last phases of a
            # fast run still reach the client.
            seen = list(progress)
            for item in seen[sent:]:
                yield {"type": "progress", "phase": format_analysis_progress(item)}
            sent = len(seen)
            if finished:
                break
        thread.join()
        if result_box[1] is not None:
            yield _error_line(result_box[1])
        else:
            yield {"type": "done", "result": to_jsonable(result_box[0])}
    finally:
        app.ops.remove(op.op_id)


def _error_line(error: BaseException) -> dict:
    from yeaboi.analysis.cancellation import AnalysisCancelledError

    if isinstance(error, AnalysisCancelledError):
        return {"type": "cancelled"}
    # The one place SDK exceptions become human text — never str(exc), which for
    # a JIRAError is its entire HTTP response.
    from yeaboi.ui.session._utils import _classify_api_error

    message = _classify_api_error(error) if isinstance(error, Exception) else "The run stopped unexpectedly."
    logger.error("Analysis run failed: %s", message)
    return {"type": "error", "message": message}


def _lines(objects: Iterator[dict]) -> Iterator[bytes]:
    for obj in objects:
        yield (json.dumps(obj, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
