"""The three actions every result screen carries: Export, Share, Anonymize.

They are one module because they are one gesture with three destinations — take
the artifact this screen is showing and send it somewhere — and because all
three start the same way: resolve a ``(kind, session_id, run_id)`` reference back
into the stored artifact (:mod:`yeaboi.sharing.resolve`).

What each one is:

* **Export** writes Markdown + HTML to disk, or publishes to Notion/Confluence.
  ``copy`` is answered as data rather than performed — a clipboard belongs to
  whatever is in front of the person, not to a background process.
* **Share** publishes the artifact behind an access code for as long as the
  host leaves it open, correctable when the kind allows it. The lifecycle lives
  in :class:`~yeaboi.app.supervisor.BoardSupervisor`, so a reloaded window
  rejoins the share it left.
* **Anonymize** runs the masking pass and returns the replacement map. The
  *surface* applies it, exactly as the terminal does: masking is a view over the
  same data, never a second copy of it.

Artifact corrections have MCP tools already (``artifact_fields``,
``artifact_edit_history``, ``artifact_edit_apply``); what is native here is the
one read an editor panel opens with — the fields for a kind and the corrections
already recorded against a specific artifact, in a single call.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator

from yeaboi.app.router import HTTPError, Request, Response, json_response

logger = logging.getLogger(__name__)

#: The stream is over. Never reaches the wire.
_END = object()


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def destinations(app, request: Request) -> Response:
    """``GET /api/export/destinations`` — the menu, for one mode."""
    from yeaboi.exporting import destination_options

    mode = str(request.query.get("mode", "planning"))
    extras = [e for e in str(request.query.get("extras", "")).split(",") if e]
    return json_response({"mode": mode, "destinations": destination_options(mode=mode, extras=extras)})


def export(app, request: Request) -> Response:
    """``POST /api/export`` — send one stored artifact to a destination.

    ``copy`` returns the Markdown instead of doing anything with it; see the
    module docstring.
    """
    from yeaboi.exporting import DEST_COPY, DEST_FILES, available_destinations, destination_blocker
    from yeaboi.sharing import resolve

    payload = request.json()
    destination = str(payload.get("destination", DEST_FILES))
    known = set(available_destinations())
    if destination not in known:
        raise HTTPError(400, f"unknown destination {destination!r} — one of {', '.join(sorted(known))}")
    blocker = destination_blocker(destination)
    if blocker:
        raise HTTPError(409, blocker)
    resolved = _resolve(payload)
    text = resolve.markdown(resolved)
    if destination == DEST_COPY:
        return json_response({"destination": destination, "title": resolved.title, "markdown": text})
    if destination == DEST_FILES:
        paths = resolve.export_files(resolved)
        logger.info("export: %s → %s", resolved.kind, paths["markdown"].parent)
        return json_response(
            {
                "destination": destination,
                "ok": True,
                "message": f"Exported to {paths['markdown'].parent}  (Markdown + HTML)",
                "paths": {name: str(path) for name, path in paths.items()},
            }
        )
    from yeaboi.export_targets import publish_markdown

    published = publish_markdown(destination, title=resolved.title, markdown=text)
    return json_response(
        {"destination": destination, "ok": published.ok, "message": published.message, "url": published.url}
    )


# ---------------------------------------------------------------------------
# Share
# ---------------------------------------------------------------------------


def shares(app, request: Request) -> Response:
    """``GET /api/shares`` — every share this process is publishing."""
    return json_response({"shares": [session.snapshot() for session in app.boards.shares()]})


def share(app, request: Request) -> Response:
    """``GET /api/shares/{share_id}`` — one share's link, code and edit count."""
    return json_response(_require_share(app, request).snapshot())


def start_share(app, request: Request) -> Response:
    """``POST /api/shares`` — publish one stored artifact behind an access code."""
    from yeaboi.config import tunnels_disabled
    from yeaboi.sharing import resolve

    payload = request.json()
    if tunnels_disabled():
        # The boards refuse differently — they are still useful on loopback for
        # the host. There is nothing useful about a loopback-only share of your
        # own output, so refuse before the server binds.
        raise HTTPError(409, "Sharing is off (YEABOI_NO_TUNNEL) — nothing was published.")
    resolved = _resolve(payload)
    editable = bool(payload.get("editable", True)) and resolved.kind in resolve.EDITABLE_KINDS
    try:
        session = app.boards.start_share(resolved, editable=editable)
    except OSError as exc:
        raise HTTPError(503, f"could not start the share server: {exc}") from None
    return json_response(session.snapshot())


def share_invite(app, request: Request) -> Response:
    """``GET /api/shares/{share_id}/invite`` — one link carrying the code."""
    from yeaboi.sharing.access import invite_url

    session = _require_share(app, request)
    return json_response(
        {
            "invite": invite_url(session.link.url, session.server.display_code),
            "display_code": session.server.display_code,
        }
    )


def discard_edits(app, request: Request) -> Response:
    """``POST /api/shares/{share_id}/discard`` — drop corrections from the document.

    The document goes back to what the run produced. The append-only log still
    holds every one of them, which is what the edit history and the next run's
    context keep reading — so this is precise about what it did.
    """
    session = _require_share(app, request)
    if session.editing is None:
        raise HTTPError(400, "this share is read-only")
    dropped = 0
    while session.editing.share.document.drop_last() is not None:
        dropped += 1
    noun = "correction" if dropped == 1 else "corrections"
    return json_response(
        {
            "dropped": dropped,
            "message": f"Removed {dropped} {noun} from this document — the edit history still shows them.",
            "share": session.snapshot(),
        }
    )


def stop_share(app, request: Request) -> Response:
    """``POST /api/shares/{share_id}/close`` — stop sharing, and decide on edits.

    ``commit`` appends the corrected artifact to its mode's history as a new
    row; the generated original survives, which is what makes a revert mean
    anything. It defaults to false because keeping somebody else's corrections
    is the host's decision, not a consequence of closing a window.
    """
    share_id = request.params.get("share_id", "")
    session = app.boards.share(share_id)
    if session is None:
        raise HTTPError(404, f"no live share {share_id!r}")
    recorded = session.edits
    commit = bool(request.json().get("commit", False))
    committed = app.boards.stop_share(share_id, commit=commit)
    noun = "correction" if recorded == 1 else "corrections"
    return json_response(
        {
            "closed": True,
            "share_id": share_id,
            "recorded": recorded,
            "committed_run_id": committed or 0,
            "message": f"Saved {recorded} {noun}." if committed else "",
        }
    )


# ---------------------------------------------------------------------------
# Anonymize
# ---------------------------------------------------------------------------


def anonymize(app, request: Request) -> Response:
    """``POST /api/anonymize`` — mask one artifact, streamed as NDJSON.

    ``op`` first, then a ``progress`` line per pass, then ``done`` carrying the
    replacement map and the note a surface shows while it is masked. The pass is
    an LLM call over a seed mask and it **never fails closed**: an auth or
    billing failure comes back as a warning with the deterministic seed-masked
    result, because the caller's whole reason for asking was that they were
    about to publish.
    """
    payload = request.json()
    resolved = _resolve(payload)
    instruction = str(payload.get("instruction", ""))
    op = app.ops.create()
    return Response(
        content_type="application/x-ndjson",
        stream=_lines(_anonymize(app, op, resolved, instruction)),
        headers=(("X-Accel-Buffering", "no"),),
    )


def _anonymize(app, op, resolved, instruction: str) -> Iterator[dict]:
    import queue

    from yeaboi.mcp.runtime import _ENGINE_LOCK, to_jsonable
    from yeaboi.sharing import resolve as resolver

    events: queue.Queue = queue.Queue()
    result: list = [None, None]

    def worker() -> None:
        from yeaboi.anonymize.engine import run_anonymize

        try:
            with _ENGINE_LOCK:
                result[0] = run_anonymize(
                    resolver.markdown(resolved),
                    instruction=instruction,
                    project_name=resolved.project_name or resolved.kind,
                    source_mode=resolved.kind,
                    on_progress=lambda phase: events.put({"type": "progress", "phase": phase}),
                )
        except BaseException as exc:  # noqa: BLE001 — reported on the stream below
            result[1] = exc
        finally:
            events.put(_END)

    thread = threading.Thread(target=worker, name="anonymize-run", daemon=True)
    thread.start()
    try:
        yield {"type": "op", "op_id": op.op_id}
        while (event := events.get()) is not _END:
            yield event
        thread.join()
        if result[1] is not None:
            logger.error("anonymize failed: %s", result[1])
            yield {"type": "error", "message": "Anonymize failed (see logs)."}
            return
        from yeaboi.anonymize.apply import masked_note

        masked = result[0]
        yield {
            "type": "done",
            "note": masked_note(masked),
            "replacements": [list(pair) for pair in masked.replacements],
            "warnings": list(masked.warnings or []),
            "result": to_jsonable(masked),
        }
    finally:
        app.ops.remove(op.op_id)


# ---------------------------------------------------------------------------
# Artifact corrections
# ---------------------------------------------------------------------------


def artifact_kinds(app, request: Request) -> Response:
    """``GET /api/artifacts/kinds`` — what each kind can do.

    A surface reads this rather than keeping its own table, so it never offers
    an action the backend would refuse: poker exports and nothing else, a team
    profile shares read-only, and only a standup or a retro is correctable.
    """
    from yeaboi.sharing import resolve

    return json_response({"kinds": resolve.capabilities()})


def artifact_edits(app, request: Request) -> Response:
    """``GET /api/artifacts/{kind}/edits`` — what an editor panel opens with.

    The kind's editable fields and the corrections already recorded against one
    artifact, in one read. Both halves exist as MCP tools; a panel needs them
    together, and asking for them separately is how the two come back describing
    different artifacts.
    """
    from yeaboi.artifacts.engine import artifact_edit_history, artifact_fields

    kind = request.params.get("kind", "")
    try:
        described = artifact_fields(kind)
    except ValueError as exc:
        raise HTTPError(404, str(exc)) from None
    try:
        run_id = int(request.query.get("run_id", "0") or 0)
    except ValueError:
        raise HTTPError(400, "run_id must be a number") from None
    history = artifact_edit_history(
        kind,
        session_id=str(request.query.get("session_id", "")),
        run_id=run_id,
        engineer=str(request.query.get("engineer", "")),
    )
    return json_response({"kind": kind, "ops": described["ops"], "artifact": described["artifacts"][0], **history})


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------


def _resolve(payload: dict):
    """Turn a ``{kind, session_id, run_id}`` reference into the stored artifact."""
    from yeaboi.sharing import resolve

    kind = str(payload.get("kind", ""))
    try:
        run_id = int(payload.get("run_id", 0) or 0)
    except (TypeError, ValueError):
        raise HTTPError(400, "run_id must be a number") from None
    try:
        resolved = resolve.load(kind, session_id=str(payload.get("session_id", "")), run_id=run_id)
    except ValueError as exc:
        raise HTTPError(400, str(exc)) from None
    if resolved is None:
        raise HTTPError(404, f"no stored {kind} to act on")
    return resolved


def _require_share(app, request: Request):
    share_id = request.params.get("share_id", "")
    session = app.boards.share(share_id)
    if session is None:
        raise HTTPError(404, f"no live share {share_id!r}")
    return session


def _lines(objects: Iterator[dict]) -> Iterator[bytes]:
    for obj in objects:
        yield (json.dumps(obj, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
