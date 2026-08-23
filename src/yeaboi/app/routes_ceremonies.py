"""Native ceremonies routes — the clock, and the Slack lane that answers it.

``ceremonies_list`` and ``ceremonies_history`` are MCP tools, so a headless
caller can already read the schedule. What lives here is everything MCP is
deliberately without: declaring one, pausing one, firing one now, and the
inbound Slack half — the identity links and the poll.

Why those are native rather than tools is the same distinction ship's launch
route rests on. The MCP surface refuses them because declaring a ceremony
installs a launchd or crontab job that outlives the session and spends money
unattended, and because linking a Slack id decides whose name goes on somebody
else's report. Both are decisions for a human at a machine they own — which is
what this app is, and what an arbitrary MCP client is not.

A run is chunked NDJSON: ``progress`` lines from the engine's callback, then
``done``. There is no ``op`` line, because ``run_ceremony`` takes no cancel
event — offering a Cancel button over a run nothing can stop would be a lie.
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


def ceremonies(app, request: Request) -> Response:
    """``GET /api/ceremonies`` — what is declared, what fires, and what drifted."""
    from yeaboi.ceremonies import render, setup

    session_id = str(request.query.get("session_id", "")) or setup.current_session()
    declared, last, spend, drift = setup.load_page(session_id)
    return json_response(
        {
            "session_id": session_id,
            "ceremonies": [
                {
                    **to_jsonable(ceremony),
                    "cadence": render.cadence_label(ceremony),
                    "next_fire": render.next_fire(ceremony),
                    "last_run": to_jsonable(last.get(ceremony.name)),
                    "month_spend_usd": spend.get(ceremony.name, 0.0),
                }
                for ceremony in declared
            ],
            "drift": drift,
            "modes": setup.mode_options(),
            "channels": setup.channel_options(),
            "add_hint": setup.add_hint(),
            "empty_message": setup.NO_SESSION_MESSAGE if not session_id else setup.NOTHING_SCHEDULED_MESSAGE,
        }
    )


def declare(app, request: Request) -> Response:
    """``POST /api/ceremonies`` — declare one and install its job.

    Everything the store refuses is a 400 naming the field: the name whitelist,
    the mode, the delivery channels, the time and the weekday spec are all
    validated in one place (``CeremonyStore.save``) so a surface cannot accept
    what another rejects.
    """
    from yeaboi.ceremonies import render, setup

    payload = request.json()
    session_id = str(payload.get("session_id", "")) or setup.current_session()
    args = tuple((str(k), str(v)) for k, v in (payload.get("args") or {}).items())
    stored, message = setup.declare(
        session_id,
        name=str(payload.get("name", "")),
        mode=str(payload.get("mode", "")),
        at=str(payload.get("at", "")),
        weekdays=str(payload.get("weekdays", "")),
        # A missing key means the default; an explicitly empty list is refused
        # by the store — a ceremony that tells nobody is not a ceremony. The
        # default here is `desktop`, not the CLI's `terminal`: this process
        # reserves stdout for the handshake and drops the terminal channel from
        # every fan-out, so a ceremony declared here with only that one would
        # run and deliver nowhere.
        channels=tuple(str(c) for c in payload.get("channels", ("desktop",))),
        args=args,
        stale_after_min=int(payload.get("stale_after_min", 120) or 0),
        monthly_cap_usd=float(payload.get("monthly_cap_usd", 0.0) or 0.0),
    )
    return json_response(
        {
            "ceremony": to_jsonable(stored),
            "cadence": render.cadence_label(stored),
            "scheduler": message,
            # The equivalent terminal command, so the surface that installed a
            # recurring job can also say exactly what it installed.
            "command": f"yeaboi ceremonies add {stored.name} --mode {stored.mode} --at {stored.at}",
        }
    )


def enabled(app, request: Request) -> Response:
    """``POST /api/ceremonies/{name}/enabled`` — pause or resume, job and all."""
    from yeaboi.ceremonies import setup

    payload = request.json()
    session_id = str(payload.get("session_id", "")) or setup.current_session()
    name = request.params.get("name", "")
    want = bool(payload.get("enabled", True))
    ceremony, message = setup.set_enabled(session_id, name, want)
    if ceremony is None:
        raise HTTPError(404, message)
    return json_response({"ceremony": to_jsonable(ceremony), "scheduler": message})


def remove(app, request: Request) -> Response:
    """``POST /api/ceremonies/{name}/remove`` — forget it and tear its job down."""
    from yeaboi.ceremonies import scheduler, setup
    from yeaboi.ceremonies.store import CeremonyStore

    payload = request.json()
    session_id = str(payload.get("session_id", "")) or setup.current_session()
    name = request.params.get("name", "")
    with CeremonyStore() as store:
        dropped = store.remove(session_id, name)
    # The job comes down either way: a declaration that is already gone with a
    # job still installed is exactly the drift the page reports.
    message = scheduler.remove_ceremony(session_id, name)
    if not dropped:
        raise HTTPError(404, f"no ceremony named {name!r}. {message}")
    logger.info("ceremony removed: %s — %s", name, message)
    return json_response({"removed": True, "name": name, "scheduler": message})


def run(app, request: Request) -> Response:
    """``POST /api/ceremonies/{name}/run`` — fire one now, streamed as NDJSON.

    Running one from here is **not** "scheduled": the staleness and monthly-cap
    guards answer questions an unattended fire raises, and a human pressing Run
    now at 14:00 means it.
    """
    from yeaboi.ceremonies import setup
    from yeaboi.ceremonies.store import CeremonyStore

    payload = request.json()
    session_id = str(payload.get("session_id", "")) or setup.current_session()
    name = request.params.get("name", "")
    with CeremonyStore() as store:
        if store.get(session_id, name) is None:
            raise HTTPError(404, f"no ceremony named {name!r}")
    logger.info("Ceremony run start: %s", name)
    return Response(
        content_type="application/x-ndjson",
        stream=_lines(_run(session_id, name, bool(payload.get("dry_run", False)))),
        headers=(("X-Accel-Buffering", "no"),),
    )


# ---------------------------------------------------------------------------
# The inbound Slack half
# ---------------------------------------------------------------------------


def slack(app, request: Request) -> Response:
    """``GET /api/slack`` — the lane's status, who is linked, and what it applied."""
    from yeaboi.ceremonies import setup
    from yeaboi.slack.engine import inbound_history

    session_id = str(request.query.get("session_id", "")) or setup.current_session()
    status = setup.slack_status(session_id)
    history: dict = {"events": [], "recent_polls": []}
    if status["two_way"]:
        try:
            history = inbound_history(limit=20)
        except Exception:  # noqa: BLE001 — an unreadable ledger must not sink the page
            logger.warning("slack: could not read the inbound history", exc_info=True)
    return json_response(
        {
            "session_id": session_id,
            **status,
            "link_hint": setup.LINK_HINT,
            "empty_message": setup.NO_TWO_WAY_MESSAGE,
            "events": to_jsonable(history.get("events", [])),
            "recent_polls": to_jsonable(history.get("recent_polls", [])),
        }
    )


def link(app, request: Request) -> Response:
    """``POST /api/slack/link`` — bind a Slack id to a roster name, or drop one."""
    from yeaboi.ceremonies import setup
    from yeaboi.slack.engine import link_slack_member

    payload = request.json()
    session_id = str(payload.get("session_id", "")) or setup.current_session()
    slack_user = str(payload.get("slack_user", "")).strip()
    if not slack_user:
        raise HTTPError(400, "a Slack user id is required")
    result = link_slack_member(
        session_id,
        slack_user,
        str(payload.get("member", "")).strip(),
        unlink=bool(payload.get("unlink", False)),
    )
    return json_response({**result, "identities": setup.slack_status(session_id)["identities"]})


def poll(app, request: Request) -> Response:
    """``POST /api/slack/poll`` — read the window once and apply what is new.

    Safe to offer as a button for the reason the engine gives for having no
    ``scheduled`` flag: a poll reads a fixed 48-hour window, everything it
    applies is free and idempotent, and a poll that declines (no token, an
    empty allowlist, another poll running) is not a failure.
    """
    from yeaboi.mcp.runtime import _ENGINE_LOCK
    from yeaboi.slack.engine import apply_inbound_events

    # Engines are one-at-a-time process-wide. Never fork this lock.
    with _ENGINE_LOCK:
        result = apply_inbound_events()
    return json_response(to_jsonable(result))


# ---------------------------------------------------------------------------


def _run(session_id: str, name: str, dry_run: bool) -> Iterator[dict]:
    from yeaboi.ceremonies import setup
    from yeaboi.mcp.runtime import _ENGINE_LOCK

    progress: queue.Queue = queue.Queue()
    result_box: list = [None, None]  # run, failure
    done = threading.Event()

    def worker() -> None:
        from yeaboi.ceremonies.engine import run_ceremony

        try:
            with _ENGINE_LOCK:
                result_box[0] = run_ceremony(
                    name,
                    session_id=session_id,
                    dry_run=dry_run,
                    # The delivery fan-out's terminal channel prints to stdout,
                    # which this process reserves for the handshake line.
                    suppress_terminal=True,
                    on_progress=progress.put,
                )
        except BaseException as exc:  # noqa: BLE001 — reported on the stream below
            result_box[1] = exc
        finally:
            done.set()

    threading.Thread(target=worker, name="ceremony-run", daemon=True).start()
    yield from _drain(progress, done)
    if result_box[1] is not None:
        logger.error("Ceremony run failed: %s", result_box[1])
        yield {"type": "error", "message": f"{type(result_box[1]).__name__}: {result_box[1]}"}
        return
    finished = result_box[0]
    yield {"type": "done", "run": to_jsonable(finished), "summary": setup.run_summary(finished)}


def _drain(progress: queue.Queue, done: threading.Event) -> Iterator[dict]:
    while True:
        finished = done.wait(_PROGRESS_POLL_SECONDS)
        while True:
            try:
                yield {"type": "progress", "phase": str(progress.get_nowait())}
            except queue.Empty:
                break
        if finished:
            return


def _lines(objects: Iterator[dict]) -> Iterator[bytes]:
    for obj in objects:
        yield (json.dumps(obj, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
