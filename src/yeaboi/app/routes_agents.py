"""Native routes for the Agents family (agentwatch).

Each mode's run and its history are MCP tools already (``agents_usage``,
``agents_advisor_run``, ``agents_*_history``…), and a headless caller should
use them: they take the window and the filters, and they answer in one call.

What is native here is the shape of the *page*. An agentwatch pass scans every
session log on the machine and takes tens of seconds on a cold cache, so the
TUI opens on the last saved report and refreshes behind it — a rule that needs
two things a request/response tool cannot give: the last artifact on its own,
and the fresh one as a progress stream. Export is native for a smaller reason:
these four artifacts write through ``agentwatch/export.py`` rather than the
shared exporter, so ``/api/export`` cannot reach them.

A run carries no ``op`` line — the agentwatch engines take no cancel event, and
a Cancel button over a run nothing can stop would be a lie. Backing out is
free: the pass finishes and stores its report either way.
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


def modes(app, request: Request) -> Response:
    """``GET /api/agents/modes`` — the four modes and how fresh each report is."""
    from yeaboi.agentwatch import setup
    from yeaboi.beta import AGENTWATCH_BETA_NOTICE

    return json_response(
        {
            "modes": setup.mode_options(),
            "actions": list(setup.RESULT_ACTIONS),
            "beta_notice": AGENTWATCH_BETA_NOTICE,
        }
    )


def latest(app, request: Request) -> Response:
    """``GET /api/agents/{kind}/latest`` — the last saved report, for an instant open.

    ``report`` is ``null`` when nothing has been stored yet, which is the
    first-run loading state rather than an error.
    """
    from yeaboi.agentwatch import setup

    mode = _mode(request)
    loaded = setup.latest_artifact(mode.kind)
    return json_response(
        {
            "kind": mode.kind,
            "label": mode.label,
            "report": to_jsonable(loaded[0]) if loaded else None,
            "as_of": loaded[1] if loaded else "",
        }
    )


def run(app, request: Request) -> Response:
    """``POST /api/agents/{kind}/run`` — one fresh pass, streamed as NDJSON."""
    mode = _mode(request)
    logger.info("Agents run start: %s", mode.key)
    return Response(
        content_type="application/x-ndjson",
        stream=_lines(_run(mode)),
        headers=(("X-Accel-Buffering", "no"),),
    )


def export(app, request: Request) -> Response:
    """``POST /api/agents/{kind}/export`` — write the report, or hand back its Markdown.

    ``copy`` is answered as data rather than performed, the same rule
    ``/api/export`` follows: a clipboard belongs to whatever is in front of the
    person, not to a background process.
    """
    from yeaboi.agentwatch import setup
    from yeaboi.agentwatch.export import export_artifact
    from yeaboi.exporting import DEST_COPY, DEST_FILES

    mode = _mode(request)
    destination = str(request.json().get("destination", DEST_FILES))
    if destination not in (DEST_FILES, DEST_COPY):
        raise HTTPError(400, f"unknown destination {destination!r} — one of {DEST_FILES}, {DEST_COPY}")
    loaded = setup.latest_artifact(mode.kind)
    if loaded is None:
        raise HTTPError(404, f"no saved {mode.label} report yet — run one first")
    artifact, _as_of = loaded
    if destination == DEST_COPY:
        return json_response(
            {"destination": destination, "title": mode.label, "markdown": setup.markdown(mode, artifact)}
        )
    written = export_artifact(artifact, kind=mode.kind)
    paths = {name: str(path) for name, path in written.items()}
    logger.info("Agents export: %s → %s", mode.key, paths.get("markdown", ""))
    return json_response(
        {
            "destination": destination,
            "ok": True,
            "message": f"Exported to {written['markdown'].parent}",
            "paths": paths,
        }
    )


# ---------------------------------------------------------------------------


def _mode(request: Request):
    from yeaboi.agentwatch import setup

    kind = request.params.get("kind", "")
    mode = setup.lookup(kind)
    if mode is None:
        raise HTTPError(404, f"unknown agents mode {kind!r} — one of {', '.join(m.kind for m in setup.MODES)}")
    return mode


def _run(mode) -> Iterator[dict]:
    from yeaboi.agentwatch import setup
    from yeaboi.mcp.runtime import _ENGINE_LOCK

    progress: queue.Queue = queue.Queue()
    result_box: list = [None, None]  # artifact, failure
    done = threading.Event()

    def worker() -> None:
        try:
            # Engines are one-at-a-time process-wide. Never fork this lock.
            with _ENGINE_LOCK:
                result_box[0] = setup.run(mode, progress.put)
        except BaseException as exc:  # noqa: BLE001 — reported on the stream below
            result_box[1] = exc
        finally:
            done.set()

    threading.Thread(target=worker, name=f"{mode.key}-run", daemon=True).start()
    while True:
        finished = done.wait(_PROGRESS_POLL_SECONDS)
        while True:
            try:
                yield _progress_line(progress.get_nowait())
            except queue.Empty:
                break
        if finished:
            break
    if result_box[1] is not None:
        # The engines never raise — parse → fallback → format — so this is a bug
        # or the store, not the scan.
        logger.error("Agents run failed: %s", result_box[1])
        yield {"type": "error", "message": f"The {mode.label} pass stopped unexpectedly — see logs."}
        return
    yield {"type": "done", "kind": mode.kind, "report": to_jsonable(result_box[0])}


def _progress_line(event: object) -> dict:
    """One engine progress event, as a wire line.

    Every agentwatch phase today is a structured ``analysis_component`` dict —
    the checklist with its files meter. Anything else is passed through as a
    plain phase rather than dropped, so a mode that grows a bare-string step
    still reaches the surface.
    """
    from yeaboi.analysis.progress import is_component_progress

    if is_component_progress(event):
        return {"type": "component", "component": to_jsonable(event)}
    return {"type": "progress", "phase": str(event)}


def _lines(objects: Iterator[dict]) -> Iterator[bytes]:
    for obj in objects:
        yield (json.dumps(obj, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
