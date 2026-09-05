"""Native routes for the Agents family (agentwatch).

Each mode's run and its history are MCP tools already (``agents_usage``,
``agents_advisor_run``, ``agents_*_history``…), and a headless caller should
use them: they take the window and the filters, and they answer in one call.

What is native here is the shape of the *page*. An agentwatch pass scans every
session log on the machine and takes tens of seconds on a cold cache, so the
TUI opens on the last saved report and refreshes behind it — a rule that needs
two things a request/response tool cannot give: the last artifact on its own,
and the fresh one as a progress stream. Export is native for a smaller reason:
these artifacts write through ``agentwatch/export.py`` rather than the
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
    """``GET /api/agents/modes`` — the modes and how fresh each report is."""
    from yeaboi.agentwatch import setup
    from yeaboi.beta import AGENTWATCH_BETA_NOTICE
    from yeaboi.config import get_agentwatch_fresh_minutes

    return json_response(
        {
            "modes": setup.mode_options(),
            "actions": list(setup.RESULT_ACTIONS),
            "beta_notice": AGENTWATCH_BETA_NOTICE,
            "fresh_minutes": get_agentwatch_fresh_minutes(),
        }
    )


def latest(app, request: Request) -> Response:
    """``GET /api/agents/{kind}/latest?project_id=`` — the last saved report, for an instant open.

    ``report`` is ``null`` when nothing has been stored yet, which is the
    first-run loading state rather than an error. Saved reports carry no
    project, so a scoped read (``project_id`` resolving to a ``repo_path``)
    answers ``null`` with ``scoped_to`` set and the surface runs fresh.
    """
    from yeaboi.agentwatch import setup

    mode = _mode(request)
    scoped_to = _repo_path(str(request.query.get("project_id", "")).strip()) if mode.scoped else ""
    loaded = None if scoped_to else setup.latest_artifact(mode.kind)
    include_info = str(request.query.get("include_info", "")).lower() in ("1", "true", "yes")
    if loaded and mode.kind == "security" and include_info and getattr(loaded[0], "hidden_info_count", 0):
        # The saved report folded its informational rows; re-derive with them
        # listed rather than paying for a scan the toggle does not need.
        from yeaboi.agentwatch.engine import rebuild_security_report

        loaded = (rebuild_security_report(include_info=True, record=False), loaded[1])
    return json_response(
        {
            "kind": mode.kind,
            "label": mode.label,
            "report": to_jsonable(loaded[0]) if loaded else None,
            "as_of": loaded[1] if loaded else "",
            # The surface re-runs only when this is false — the same rule the
            # terminal follows, decided once here so the two cannot drift.
            "fresh": bool(loaded and setup.is_fresh(loaded[1])),
            "scoped_to": scoped_to,
        }
    )


def run(app, request: Request) -> Response:
    """``POST /api/agents/{kind}/run`` ``{project_id?, window_days?, include_info?}`` — one fresh pass, as NDJSON."""
    mode = _mode(request)
    body = request.json()
    project_id = str(body.get("project_id", "")).strip()
    scoped_to = _repo_path(project_id) if mode.scoped else ""
    options: dict = {}
    if "window_days" in body:
        try:
            options["window_days"] = max(1, min(365, int(body["window_days"])))
        except (TypeError, ValueError) as exc:
            raise HTTPError(400, "window_days must be an integer between 1 and 365") from exc
    if "include_info" in body:
        options["include_info"] = bool(body["include_info"])
    logger.info("Agents run start: %s (repo=%s options=%s)", mode.key, scoped_to or "-", options or "-")
    return Response(
        content_type="application/x-ndjson",
        stream=_lines(_run(mode, scoped_to, options)),
        headers=(("X-Accel-Buffering", "no"),),
    )


def dismiss(app, request: Request) -> Response:
    """``POST /api/agents/security/dismiss`` ``{key, reason, expires?, undo?}`` — set a finding aside, with the why.

    A dismissal needs a reason; an empty one is a 400, not a silent no-op. The
    next run drops the finding from the report and counts it as dismissed.
    """
    from yeaboi.agentwatch import dismissals

    body = request.json()
    key = str(body.get("key", "")).strip()
    if not key:
        raise HTTPError(400, "key is required — the finding's category:pattern:location")
    include_info = bool(body.get("include_info"))
    if body.get("undo"):
        restored = dismissals.undismiss(key)
        if not restored:
            raise HTTPError(404, f"no dismissal on file for {key!r}")
        logger.info("Agents security: restored %s", key)
        return json_response(
            {
                "ok": True,
                "restored": key,
                "dismissed": [d.__dict__ for d in dismissals.load()],
                "report": _rebuilt(include_info),
            }
        )
    try:
        entry = dismissals.dismiss(key, reason=str(body.get("reason", "")), expires=str(body.get("expires", "")))
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from exc
    logger.info("Agents security: dismissed %s", key)
    return json_response(
        {
            "ok": True,
            "entry": entry.__dict__,
            "dismissed": [d.__dict__ for d in dismissals.load()],
            "report": _rebuilt(include_info),
        }
    )


def verdict(app, request: Request) -> Response:
    """``POST /api/agents/security/verdict`` ``{keys, verdict, reason?, include_info?}`` — many findings at once.

    ``verdict`` is ``test-data`` (the reason is filled in), ``dismiss`` (needs
    ``reason``) or ``undo``. The answer carries the re-derived report, so the
    page updates without a scan.
    """
    from yeaboi.agentwatch import dismissals

    body = request.json()
    keys = [str(k).strip() for k in (body.get("keys") or []) if str(k).strip()]
    if not keys:
        raise HTTPError(400, "keys is required — one or more finding keys")
    word = str(body.get("verdict", "")).strip()
    reason = str(body.get("reason", "")).strip()
    if word == "undo":
        restored = [k for k in keys if dismissals.undismiss(k)]
        logger.info("Agents security: restored %d finding(s)", len(restored))
        return json_response({"ok": True, "restored": restored, "report": _rebuilt(bool(body.get("include_info")))})
    if word == "test-data":
        reason = reason or "test data: fixture or example text"
    elif word != "dismiss":
        raise HTTPError(400, "verdict must be one of test-data, dismiss, undo")
    if not reason:
        raise HTTPError(400, "a dismissal needs a reason — say why these findings are expected")
    done: list[str] = []
    for key in keys:
        try:
            dismissals.dismiss(key, reason=reason)
            done.append(key)
        except ValueError as exc:
            raise HTTPError(400, str(exc)) from exc
    logger.info("Agents security: %s on %d finding(s)", word, len(done))
    return json_response({"ok": True, "handled": done, "report": _rebuilt(bool(body.get("include_info")))})


def fix(app, request: Request) -> Response:
    """``POST /api/agents/security/fix`` ``{key, fix_id, keys?, reason?, repo?, include_info?}`` — apply one fix.

    A refused sandbox path is a 403 carrying the path, the same answer the
    ship launch gives: the consent modal opens beside it and the retry works.
    """
    from yeaboi.agentwatch import security_fixes

    body = request.json()
    key = str(body.get("key", "")).strip()
    fix_id = str(body.get("fix_id", "")).strip()
    if not key or not fix_id:
        raise HTTPError(400, "key and fix_id are required")
    keys = tuple(str(k).strip() for k in (body.get("keys") or []) if str(k).strip())
    outcome = security_fixes.apply_fix(
        key, fix_id, keys=keys, reason=str(body.get("reason", "")), repo=str(body.get("repo", ""))
    )
    if outcome.consent_needed:
        raise HTTPError(403, outcome.detail)
    logger.info("Agents security: fix %s on %s → %s", fix_id, key, "ok" if outcome.ok else outcome.detail)
    payload = {
        "ok": outcome.ok,
        "fix_id": outcome.fix_id,
        "detail": outcome.detail,
        "pr_url": outcome.pr_url,
        "paths": list(outcome.paths),
        "handled": list(outcome.handled_keys),
    }
    if outcome.ok and fix_id not in ("rotate", "manual"):
        payload["report"] = _rebuilt(bool(body.get("include_info")))
    return json_response(payload)


def replay(app, request: Request) -> Response:
    """``GET /api/agents/security/replay?key=&line=`` — the transcript turns around one signal."""
    from yeaboi.agentwatch import replay as replay_mod

    key = str(request.query.get("key", "")).strip()
    finding = _finding(key)
    try:
        line = int(request.query.get("line", "") or finding.line_no)
    except ValueError as exc:
        raise HTTPError(400, "line must be an integer") from exc
    try:
        result = replay_mod.replay(finding.location, line, pattern=finding.pattern)
    except replay_mod.ReplayError as exc:
        raise HTTPError(400, str(exc)) from exc
    return json_response(to_jsonable(result))


def signals(app, request: Request) -> Response:
    """``GET /api/agents/security/signals?key=`` — every stored line behind one finding."""
    from yeaboi.agentwatch.store import AgentWatchStore
    from yeaboi.paths import get_db_path

    key = str(request.query.get("key", "")).strip()
    finding = _finding(key)
    with AgentWatchStore(get_db_path()) as store:
        rows = store.list_findings_for_key(
            category=finding.category, pattern=finding.pattern, source_path=finding.location, context=finding.context
        )
    return json_response(
        {
            "key": key,
            "signals": [
                {
                    "line_no": int(r.get("line_no") or 0),
                    "at": str(r.get("at") or ""),
                    "session_id": str(r.get("session_id") or ""),
                    "context": str(r.get("context") or ""),
                    "snippet": str(r.get("snippet") or ""),
                }
                for r in rows
            ],
        }
    )


def _rebuilt(include_info: bool) -> dict | None:
    from yeaboi.agentwatch.engine import rebuild_security_report

    try:
        return to_jsonable(rebuild_security_report(include_info=include_info))
    except Exception as exc:  # noqa: BLE001 — the action succeeded; the page can re-fetch
        logger.warning("Agents security: could not rebuild the report: %s", exc)
        return None


def _finding(key: str):
    from yeaboi.agentwatch.engine import rebuild_security_report

    if not key:
        raise HTTPError(400, "key is required — the finding's key")
    report = rebuild_security_report(include_info=True, record=False)
    finding = next((f for f in report.findings if f.key == key), None)
    if finding is None:
        raise HTTPError(404, f"no finding {key!r} in the latest scan")
    if finding.category not in ("secret", "risky_tool"):
        raise HTTPError(400, "only transcript findings have a replay")
    return finding


def dismissed(app, request: Request) -> Response:
    """``GET /api/agents/security/dismissed`` — the dismissals on file, reasons included."""
    from yeaboi.agentwatch import dismissals

    return json_response({"dismissed": [d.__dict__ for d in dismissals.load()]})


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


def _repo_path(project_id: str) -> str:
    """The ``repo_path`` a project scopes to; "" for no project.

    An unknown project is a 404; a project with no ``repo_path`` yet is a 400
    naming the command that sets one — a silently machine-wide report under a
    project's name would be the worse answer.
    """
    if not project_id:
        return ""
    from yeaboi.paths import get_db_path
    from yeaboi.projects.store import ProjectStore

    with ProjectStore(get_db_path()) as store:
        project = store.get(project_id)
    if project is None:
        raise HTTPError(404, f"unknown project {project_id!r}")
    repo_path = str(project["settings"].get("repo_path") or "").strip()
    if not repo_path:
        raise HTTPError(
            400,
            f"project {project_id!r} has no repo_path yet — yeaboi project set-defaults {project_id} --repo <path>",
        )
    return repo_path


def _run(mode, project_path: str = "", options: dict | None = None) -> Iterator[dict]:
    from yeaboi.agentwatch import setup
    from yeaboi.mcp.runtime import _ENGINE_LOCK

    progress: queue.Queue = queue.Queue()
    result_box: list = [None, None]  # artifact, failure
    done = threading.Event()

    def worker() -> None:
        try:
            # Engines are one-at-a-time process-wide. Never fork this lock.
            with _ENGINE_LOCK:
                result_box[0] = setup.run(mode, progress.put, project_path=project_path, options=options)
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
    yield {"type": "done", "kind": mode.kind, "report": to_jsonable(result_box[0]), "scoped_to": project_path}


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
