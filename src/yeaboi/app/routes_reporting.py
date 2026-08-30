"""Native reporting routes — the generate flow, the sprint list, the deck style.

``report_delivery``, ``reporting_history`` and ``reporting_export`` are MCP
tools, so a headless caller already has the report itself. What lives here is
what MCP has no shape for: what this machine may be asked (the periods, the
configured sources, the palettes and the deck-style vocabulary), the quarter's
sprint list, a run that reports progress and can be stopped while it works, and
the persisted deck style a surface edits in place.

A run is a chunked NDJSON stream: ``op`` first, then ``progress`` lines from the
engine's callback, then ``done`` with the report. Cancelling the op sets the
engine's ``cancel_event``, which raises ``ReportCancelledError`` at the next
stage boundary — before anything is persisted.
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

#: How long the run generator waits on the progress queue between drains.
_PROGRESS_POLL_SECONDS = 0.2


def options(app, request: Request) -> Response:
    """``GET /api/reporting/options`` — everything a reporting page may offer."""
    from yeaboi.reporting import setup
    from yeaboi.reporting.style import (
        COLOR_ROLES,
        CONTENT_FIT_LABELS,
        CONTENT_FITS,
        FONT_PRESETS,
        FONT_SCALES,
        LAYOUTS,
        MAX_BULLET_CHOICES,
        STYLE_FIELDS,
        load_deck_style,
        style_summary,
        style_to_dict,
    )
    from yeaboi.reporting.themes import all_palettes

    grid = setup.source_grid()
    style = load_deck_style()
    default_start, default_end = setup.default_window()
    palettes = all_palettes()
    return json_response(
        {
            "periods": setup.period_options(),
            "sources": {
                "grid": setup.offerable_grid(grid),
                "step_applies": setup.sources_step_applies(grid),
                "descriptions": setup.COMPONENT_DESCRIPTIONS,
                "titles": setup.SOURCE_TITLES,
                "summary": setup.sources_summary(None, grid),
            },
            "themes": list(palettes),
            "palettes": palettes,
            "style": style_to_dict(style),
            "style_summary": style_summary(style),
            "style_fields": [{"key": key, "label": label, "kind": kind} for key, label, kind in STYLE_FIELDS],
            "style_choices": {
                "color_roles": list(COLOR_ROLES),
                "fonts": list(FONT_PRESETS),
                "font_scales": list(FONT_SCALES),
                "layouts": list(LAYOUTS),
                "content_fits": list(CONTENT_FITS),
                "content_fit_labels": CONTENT_FIT_LABELS,
                "max_bullets": list(MAX_BULLET_CHOICES),
            },
            "default_window": {"start": default_start, "end": default_end},
        }
    )


def sprints(app, request: Request) -> Response:
    """``GET /api/reporting/sprints`` — the quarter's sprints, pre-checked.

    An empty list is the honest answer, not an error: with no tracker and no
    plan sprints the caller reports over the calendar quarter instead, and the
    fallback window is returned alongside so it does not have to compute one.
    """
    from yeaboi.reporting import setup

    session_id = request.query.get("session_id", "")
    found = setup.sprint_options(session_id)
    return json_response(
        {
            "sprints": [to_jsonable(sprint) for sprint in found],
            "checked": setup.default_checked(found),
            "fallback": _window_out(setup.calendar_quarter_window()),
        }
    )


def window(app, request: Request) -> Response:
    """``POST /api/reporting/window`` — the window a set of checked sprints makes.

    A round-trip rather than a rule the renderer keeps its own copy of: which
    sprints make a quarter "custom", and the fact that the window never runs
    past today, must be answered the same way on every surface.
    """
    from yeaboi.reporting import setup

    payload = request.json()
    sprint_rows = payload.get("sprints") or []
    checked = [int(i) for i in (payload.get("checked") or [])]
    found = setup.sprint_options(str(payload.get("session_id", ""))) if not sprint_rows else _sprint_refs(sprint_rows)
    computed = setup.window_from_sprints(found, checked)
    if not computed:
        raise HTTPError(400, setup.NO_SPRINTS_CHECKED_MESSAGE)
    return json_response(_window_out(computed))


def style(app, request: Request) -> Response:
    """``POST /api/reporting/style`` — persist the deck style (or reset it)."""
    from yeaboi.reporting.style import DEFAULT_STYLE, save_deck_style, style_from_dict, style_summary, style_to_dict

    payload = request.json()
    updated = DEFAULT_STYLE if payload.get("reset") else style_from_dict(payload.get("style") or {})
    save_deck_style(updated)
    logger.info("Reporting deck style saved: %s", style_summary(updated))
    return json_response({"style": style_to_dict(updated), "style_summary": style_summary(updated)})


def fit(app, request: Request) -> Response:
    """``POST /api/reporting/fit`` — how many extra slides fitting everything costs.

    ``extra_slides`` is 0 when there is nothing to ask: the style already has an
    answer, or expanding costs nothing. The style that comes back is the one to
    export with in that case; otherwise the caller asks and posts the answer.
    """
    from yeaboi.reporting import setup
    from yeaboi.reporting.style import load_deck_style, style_from_dict, style_to_dict

    payload = request.json()
    resolved = _resolve_report(payload)
    chosen = style_from_dict(payload["style"]) if payload.get("style") else load_deck_style()
    decided, extra = setup.resolve_fit(resolved.artifact, chosen)
    return json_response({"extra_slides": extra, "style": style_to_dict(decided)})


def export_deck(app, request: Request) -> Response:
    """``POST /api/reporting/export`` — the deck outputs a plain export cannot write.

    ``/api/export`` writes Markdown + HTML for every kind. A report also has a
    slide deck and (with python-pptx) a .pptx, and both are styled — so they go
    through here, where the deck style and the answered fit question arrive.
    """
    from yeaboi.reporting import setup
    from yeaboi.reporting.export import export_pptx_only, export_report
    from yeaboi.reporting.style import load_deck_style, style_from_dict

    payload = request.json()
    resolved = _resolve_report(payload)
    chosen = style_from_dict(payload["style"]) if payload.get("style") else load_deck_style()
    if "expand" in payload:
        chosen = setup.apply_fit(chosen, bool(payload["expand"]))
    theme = str(payload.get("theme") or "midnight")
    if payload.get("pptx_only"):
        path = export_pptx_only(resolved.artifact, theme=theme, style=chosen)
        if path is None:
            raise HTTPError(503, "PowerPoint export needs python-pptx — install with: uv sync --extra docs")
        return json_response({"paths": {"pptx": str(path)}})
    paths = export_report(
        resolved.artifact,
        project_name=resolved.project_name,
        theme=theme,
        history=list(resolved.history),
        style=chosen,
    )
    logger.info("Reporting deck exported: %s", ", ".join(sorted(paths)))
    return json_response({"paths": {name: str(path) for name, path in paths.items()}})


def run(app, request: Request) -> Response:
    """``POST /api/reporting/run`` — one delivery report, streamed as NDJSON."""
    from yeaboi.reporting import setup
    from yeaboi.reporting.activity import PERIOD_LABELS

    payload = request.json()
    period = str(payload.get("period", "last_month"))
    if period not in PERIOD_LABELS:
        raise HTTPError(400, f"period must be one of {', '.join(PERIOD_LABELS)}")
    try:
        window_start, window_end = setup.validate_window(
            str(payload.get("window_start", "")), str(payload.get("window_end", ""))
        )
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None
    if setup.needs_window(period) and not (window_start and window_end):
        raise HTTPError(400, "a custom range needs both window_start and window_end")
    op = app.ops.create()
    logger.info("Reporting run start: period=%s window=%s→%s", period, window_start or "—", window_end or "—")
    return Response(
        content_type="application/x-ndjson",
        stream=_lines(_run(app, op, payload, period, window_start, window_end)),
        headers=(("X-Accel-Buffering", "no"),),
    )


# ---------------------------------------------------------------------------


def _sprint_refs(rows: list) -> list:
    from yeaboi.reporting.sprints import SprintRef

    return [
        SprintRef(
            name=str(row.get("name", "")),
            start_date=str(row.get("start_date", "")),
            end_date=str(row.get("end_date", "")),
            source=str(row.get("source", "")),
            in_quarter=bool(row.get("in_quarter")),
        )
        for row in rows
    ]


def _window_out(computed: dict) -> dict:
    return {
        "window_start": computed["window_start"],
        "window_end": computed["window_end"],
        "sprint_names": list(computed["sprint_names"]),
        "period_label_override": computed["period_label_override"],
    }


def _resolve_report(payload: dict):
    from yeaboi.sharing import resolve

    found = resolve.load(
        "reporting",
        session_id=str(payload.get("session_id", "")),
        run_id=int(payload.get("run_id", 0) or 0),
    )
    if found is None:
        raise HTTPError(404, "no saved delivery report to export")
    return found


def _run(app, op, payload: dict, period: str, window_start: str, window_end: str) -> Iterator[dict]:
    from yeaboi.mcp.runtime import _ENGINE_LOCK

    progress: queue.Queue = queue.Queue()
    result_box: list = [None, None]  # report, failure
    done = threading.Event()

    def worker() -> None:
        from yeaboi.reporting.engine import run_delivery_report

        try:
            # Engines are one-at-a-time process-wide. Never fork this lock.
            with _ENGINE_LOCK:
                result_box[0] = run_delivery_report(
                    period,
                    session_id=str(payload.get("session_id", "")),
                    project_id=str(payload.get("project_id", "")),
                    window_start=window_start,
                    window_end=window_end,
                    sprint_names=tuple(payload.get("sprint_names") or ()),
                    period_label_override=str(payload.get("period_label_override", "")),
                    theme=str(payload.get("theme") or "midnight"),
                    sources=payload.get("sources"),
                    on_progress=progress.put,
                    cancel_event=op.cancel,
                )
        except BaseException as exc:  # noqa: BLE001 — reported on the stream below
            result_box[1] = exc
        finally:
            done.set()

    thread = threading.Thread(target=worker, name="reporting-run", daemon=True)
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
            yield _error_line(result_box[1])
        else:
            report = result_box[0]
            yield {
                "type": "done",
                "report": to_jsonable(report),
                "delivered": len(getattr(report, "delivered_items", ()) or ()),
            }
    finally:
        app.ops.remove(op.op_id)


def _error_line(error: BaseException) -> dict:
    from yeaboi.reporting.engine import ReportCancelledError

    if isinstance(error, ReportCancelledError):
        return {"type": "cancelled"}
    # The one place SDK exceptions become human text — never str(exc), which
    # for a JIRAError is its entire HTTP response.
    from yeaboi.ui.session._utils import _classify_api_error

    message = _classify_api_error(error) if isinstance(error, Exception) else "The run stopped unexpectedly."
    logger.error("Reporting run failed: %s", message)
    return {"type": "error", "message": message}


def _lines(objects: Iterator[dict]) -> Iterator[bytes]:
    for obj in objects:
        yield (json.dumps(obj, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
