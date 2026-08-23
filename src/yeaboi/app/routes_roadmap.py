"""Native roadmap routes — the intake tile's source picker, analysis and results.

The roadmap has no MCP tool and no CLI flag; both are tracked gaps older than
this surface. These routes are the first non-TUI path to it, which is what the
desktop tile was waiting on.

An analysis is a chunked NDJSON stream: ``op`` first, then ``progress`` lines
from the engine's callback, then ``done``. The engine never raises — an ingest
or LLM failure comes back as an analysis carrying warnings — so ``error`` on
this stream means the store or the process broke, not the roadmap.
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

_PROGRESS_POLL_SECONDS = 0.2


def options(app, request: Request) -> Response:
    """``GET /api/roadmap/options`` — the three sources and their configured status."""
    from yeaboi.roadmap import setup

    return json_response({"sources": setup.source_options()})


def roadmaps(app, request: Request) -> Response:
    """``GET /api/roadmap/saved`` — the saved roadmaps, as the project list shows them."""
    from yeaboi.paths import get_db_path
    from yeaboi.roadmap.store import RoadmapStore

    db_path = get_db_path()
    if not db_path.exists():
        return json_response({"roadmaps": []})
    with RoadmapStore(db_path) as store:
        rows = store.list_roadmaps()
    return json_response({"roadmaps": to_jsonable(rows)})


def roadmap(app, request: Request) -> Response:
    """``GET /api/roadmap/saved/{roadmap_id}`` — one saved roadmap and its analysis."""
    from yeaboi.paths import get_db_path
    from yeaboi.roadmap.store import RoadmapStore

    roadmap_id = _int_param(request.params.get("roadmap_id", ""))
    db_path = get_db_path()
    row = None
    if db_path.exists():
        with RoadmapStore(db_path) as store:
            row = store.get_roadmap(roadmap_id)
    if row is None:
        raise HTTPError(404, f"no saved roadmap {roadmap_id}")
    return json_response({"roadmap": to_jsonable(row)})


def plan(app, request: Request) -> Response:
    """``POST /api/roadmap/plan`` — what Plan This hands to the planning chat.

    A round-trip rather than a rule the renderer keeps its own copy of: which
    projects are large enough for the full intake, and how a project with no
    description is described, must be answered the same way on every surface.
    """
    from yeaboi.roadmap import setup

    payload = request.json()
    resolved = _resolve(payload)
    picked = setup.project_choice(resolved.artifact, int(payload.get("index", 0) or 0))
    if picked is None:
        raise HTTPError(400, setup.NO_PROJECTS_MESSAGE)
    intake_mode, description = picked
    return json_response({"intake_mode": intake_mode, "description": description})


def analyze(app, request: Request) -> Response:
    """``POST /api/roadmap/analyze`` — one roadmap analysis, streamed as NDJSON."""
    from yeaboi import fs_policy
    from yeaboi.roadmap import setup

    payload = request.json()
    kind = str(payload.get("source_type", ""))
    source, problem = setup.resolve_source(kind, str(payload.get("locator", "")))
    if source is None:
        raise HTTPError(400, problem)
    if kind == "local" and not fs_policy.request_consent(source.locator, mode="read", context="roadmap intake"):
        # Stated before the run rather than discovered mid-analysis: the engine
        # would raise inside the worker and the stream would carry a sandbox
        # traceback instead of the one thing that fixes it. Asking rather than
        # only refusing — the consent modal is already open by the time this
        # answer arrives, and answering it makes the retry work.
        raise HTTPError(403, f"{source.locator} is outside the allowed paths — allow it and try again")
    roadmap_id = int(payload.get("roadmap_id", 0) or 0)
    op = app.ops.create()
    logger.info("Roadmap analyze start: type=%s roadmap_id=%s", kind, roadmap_id or "(new)")
    return Response(
        content_type="application/x-ndjson",
        stream=_lines(_analyze(app, op, source, roadmap_id)),
        headers=(("X-Accel-Buffering", "no"),),
    )


# ---------------------------------------------------------------------------


def _int_param(raw: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise HTTPError(400, f"roadmap id must be a number — got {raw!r}") from None


def _resolve(payload: dict):
    from yeaboi.sharing import resolve

    found = resolve.load("roadmap", run_id=int(payload.get("roadmap_id", 0) or 0))
    if found is None:
        raise HTTPError(404, "no analyzed roadmap")
    return found


def _analyze(app, op, source, roadmap_id: int) -> Iterator[dict]:
    from yeaboi.mcp.runtime import _ENGINE_LOCK
    from yeaboi.paths import get_db_path

    progress: queue.Queue = queue.Queue()
    result_box: list = [None, None]
    done = threading.Event()
    db_path = get_db_path()

    def worker() -> None:
        from yeaboi.roadmap.engine import run_roadmap_analysis

        try:
            # Engines are one-at-a-time process-wide. Never fork this lock.
            with _ENGINE_LOCK:
                result_box[0] = run_roadmap_analysis(source, db_path=db_path, on_progress=progress.put)
        except BaseException as exc:  # noqa: BLE001 — reported on the stream below
            result_box[1] = exc
        finally:
            done.set()

    thread = threading.Thread(target=worker, name="roadmap-analyze", daemon=True)
    thread.start()
    try:
        yield {"type": "op", "op_id": op.op_id}
        while True:
            finished = done.wait(_PROGRESS_POLL_SECONDS)
            while True:
                try:
                    yield {"type": "progress", "phase": str(progress.get_nowait())}
                except queue.Empty:
                    break
            if finished:
                break
        thread.join()
        if result_box[1] is not None:
            logger.error("Roadmap analyze failed: %s", result_box[1])
            yield {"type": "error", "message": "The analysis stopped unexpectedly — see logs."}
            return
        analysis = result_box[0]
        yield {"type": "done", "analysis": to_jsonable(analysis), "roadmap_id": _save(source, analysis, roadmap_id)}
    finally:
        app.ops.remove(op.op_id)


def _save(source, analysis, roadmap_id: int) -> int:
    """Insert or update the roadmap row. Best-effort — results show either way."""
    from yeaboi.paths import get_db_path
    from yeaboi.roadmap.store import RoadmapStore

    try:
        with RoadmapStore(get_db_path()) as store:
            return store.save_roadmap(source, analysis, roadmap_id=roadmap_id or None)
    except Exception:  # noqa: BLE001 — remembering the roadmap must not sink the run
        logger.error("Roadmap analyze: failed to save the roadmap", exc_info=True)
        return roadmap_id


def _lines(objects: Iterator[dict]) -> Iterator[bytes]:
    for obj in objects:
        yield (json.dumps(obj, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
