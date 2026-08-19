"""The Ceremonies page loop: read the schedule, run one, pause one, add one.

Reads are cheap and the page is short, so it re-reads the store on every action
rather than caching — a ceremony's last run changes underneath this screen every
time one fires, and a stale row here is exactly the kind of lie the page exists
to prevent.

Two things about what this page does and does not do:

- **Running one from here is not "scheduled".** The guards (staleness, the
  monthly cap) answer questions an unattended fire raises; a human pressing
  "Run now" at 14:00 means it.
- **Declaring one is a terminal command, and the page says so rather than
  offering a form.** `yeaboi ceremonies add` installs a launchd or crontab job,
  and the surface that does that should be the one you can see the resulting
  command in. The page owns the reading, the running and the pausing.
"""

from __future__ import annotations

import logging
import threading
import time

from rich.console import Console

from yeaboi.agent.state import Ceremony
from yeaboi.ceremonies import catalog, scheduler
from yeaboi.ceremonies.store import CeremonyStore
from yeaboi.ui.mode_select.screens._screens_ceremonies import _build_ceremonies_screen
from yeaboi.ui.shared._scroll import SCROLL_KEYS, coalesce_scroll

logger = logging.getLogger(__name__)

ACTIONS = ("Run now", "Pause", "Back")


def _session() -> str:
    """The session the page is scoped to, or '' when there is none yet."""
    try:
        from yeaboi.mcp.tools_sessions import resolve_session_id

        return resolve_session_id("")
    except Exception:  # noqa: BLE001 — no saved sessions is a normal empty state
        logger.info("ceremonies page: no session to scope to")
        return ""


def _load(session_id: str) -> tuple[list[Ceremony], dict, dict, list[str]]:
    """(ceremonies, last runs, month spend, drift lines) — one read, one screen."""
    if not session_id:
        return [], {}, {}, []
    with CeremonyStore() as store:
        declared = store.list(session_id)
        last = {c.name: store.last_run(session_id, c.name) for c in declared}
        spend = {c.name: store.month_spend(session_id, c.name) for c in declared}

    # The store says what is declared, the OS says what will fire, and nothing
    # else in the app would ever mention the gap.
    installed = set(scheduler.installed_ceremonies(session_id))
    known = {c.name for c in declared}
    enabled = {c.name for c in declared if c.enabled}
    drift = [f"a job is installed for {name!r}, which is not declared here" for name in sorted(installed - known)]
    drift += [f"{name!r} is paused but its job is still installed" for name in sorted((installed & known) - enabled)]
    drift += [f"{name!r} is declared but has no scheduled job — re-add it" for name in sorted(enabled - installed)]
    return declared, last, spend, drift


def _run_now(console: Console, live, session_id: str, ceremony: Ceremony, dry_run: bool = False) -> str:
    """Fire one ceremony on a worker thread, repainting while it runs.

    On a thread because a real run makes LLM and network calls for tens of
    seconds, and a frozen terminal is indistinguishable from a crashed one.

    ``suppress_terminal`` because of that same thread: the main thread is
    repainting a ``Live`` while this runs, and the terminal channel's whole job
    is printing to the screen it is repainting.
    """
    from yeaboi.ceremonies.engine import run_ceremony

    outcome: dict = {}

    def _work() -> None:
        try:
            outcome["run"] = run_ceremony(ceremony.name, session_id=session_id, dry_run=dry_run, suppress_terminal=True)
        except Exception as exc:  # noqa: BLE001 — surfaced as a message, never a traceback
            logger.error("ceremonies page: %s raised", ceremony.name, exc_info=True)
            outcome["error"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=_work, name="ceremony-run", daemon=True)
    worker.start()
    while worker.is_alive():
        w, h = console.size
        live.update(
            _build_ceremonies_screen(
                [ceremony],
                width=w,
                height=h,
                actions=list(ACTIONS),
                message=f"running {ceremony.name}…",
            )
        )
        time.sleep(0.1)
    if "error" in outcome:
        return outcome["error"]
    run = outcome.get("run")
    if run is None:
        return "the run produced nothing"
    if run.outcome == "ok":
        delivered = ", ".join(ch for ch, ok in run.delivery if ok) or "nowhere"
        return f"{ceremony.name} ran (${run.cost_usd:.2f}) → {delivered}"
    return f"{ceremony.name}: {run.outcome} — {run.error or run.detail}"


def run_ceremonies_page(
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    dry_run: bool = False,
) -> None:
    """Enter Ceremonies from the menu; returns when the user backs out.

    ``dry_run`` is threaded through to the engine like every other page's is.
    Without it ``make run-dry`` — documented as "no LLM calls" — would make real
    ones the moment somebody pressed Run now, and post the result to the real
    Slack webhook.
    """
    session_id = _session()
    ceremonies, last, spend, drift = _load(session_id)
    logger.info("Ceremonies page opened: %d declared in session %s", len(ceremonies), session_id or "(none)")
    selected = action_sel = scroll = 0
    scroll_meta: dict = {}
    message = "" if session_id else "No saved session yet — plan a project first."
    start = time.monotonic()

    while True:
        # The Pause button reads as its inverse on a paused row, because a
        # button that says "Pause" on something already paused is a bug report.
        actions = list(ACTIONS)
        if ceremonies and not ceremonies[selected].enabled:
            actions[1] = "Resume"
        w, h = console.size
        live.update(
            _build_ceremonies_screen(
                ceremonies,
                last_runs=last,
                spend=spend,
                drift=drift,
                selected=selected,
                scroll_offset=scroll,
                scroll_meta=scroll_meta,
                width=w,
                height=h,
                action_sel=action_sel,
                actions=actions,
                shimmer_tick=time.monotonic() - start,
                sub_reveal=(time.monotonic() - start) * 6.0,
                message=message,
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()

        if key in SCROLL_KEYS:
            scroll = coalesce_scroll(scroll, key, scroll_meta, read_key)
            continue
        if key in ("esc", "q"):
            logger.info("Ceremonies page closed")
            return
        if key == "left":
            action_sel = (action_sel - 1) % len(actions)
        elif key == "right":
            action_sel = (action_sel + 1) % len(actions)
        elif key in ("up", "down") and ceremonies:
            step = -1 if key == "up" else 1
            selected = (selected + step) % len(ceremonies)
            message = ""
        elif key == "enter":
            choice = actions[action_sel]
            if choice == "Back":
                logger.info("Ceremonies page closed from the buttons")
                return
            if not ceremonies:
                message = "Nothing scheduled — add one with `yeaboi ceremonies add`."
                continue
            ceremony = ceremonies[selected]
            if choice == "Run now":
                logger.info("Ceremonies: running %s on request", ceremony.name)
                message = _run_now(console, live, session_id, ceremony, dry_run)
            else:
                enable = choice == "Resume"
                logger.info("Ceremonies: %s %s", "resuming" if enable else "pausing", ceremony.name)
                with CeremonyStore() as store:
                    store.set_enabled(session_id, ceremony.name, enable)
                if enable:
                    detail = scheduler.install_ceremony(session_id, ceremony.name, ceremony.at, ceremony.weekdays)
                else:
                    # Pause removes the JOB and keeps the declaration; a paused
                    # ceremony that still fires is the thing users report as a bug.
                    detail = scheduler.remove_ceremony(session_id, ceremony.name)
                message = f"{ceremony.name} {'resumed' if enable else 'paused'}. {detail}"
            ceremonies, last, spend, drift = _load(session_id)
            selected = min(selected, max(0, len(ceremonies) - 1))
        elif key == "n":
            message = (
                f"Add one from the terminal: yeaboi ceremonies add <name> --mode {catalog.CATALOG[0].key} --at 09:00"
            )
