"""The Ship mode page: pick a story, supervise the run, approve at the gate.

One dispatch entry (:func:`run_ship_page`) rather than another branch spelled
inline in ``select_mode``. The engine runs on a daemon thread (the
``_run_report_generate`` worker-thread template); this loop renders the phase
checklist from its progress events, watches the store for the gate opening,
and resolves the gate through ``ShipStore.resolve_gate`` — the same single
seam a CLI approver uses, so the database CAS arbitrates whoever answers
first.

Imports from :mod:`yeaboi.ui.mode_select` happen lazily inside function bodies —
the package imports this module's caller, so a top-level import is a cycle.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

from rich.console import Console

from yeaboi.analysis.progress import is_component_progress
from yeaboi.ui.mode_select.screens._screens_ship import (
    SHIP_GATE_ACTIONS,
    SHIP_PICK_ACTIONS,
    SHIP_RESULT_ACTIONS,
    _build_ship_gate_screen,
    _build_ship_pick_screen,
    _build_ship_progress_screen,
    _build_ship_result_screen,
)
from yeaboi.ui.shared._scroll import SCROLL_KEYS, coalesce_scroll

logger = logging.getLogger(__name__)


def _load_stories() -> tuple[list, str, str]:
    """(stories, session_id, message) from the latest saved plan. Never raises."""
    from yeaboi.paths import get_db_path
    from yeaboi.sessions import SessionStore

    try:
        with SessionStore(get_db_path()) as store:
            session_id = store.get_latest_session_id()
            if not session_id:
                return [], "", ""
            state = store.load_state(session_id) or {}
    except Exception as exc:  # noqa: BLE001 — an unreadable DB must not crash the menu
        logger.warning("Ship page: could not load sessions: %s", exc)
        return [], "", "Could not read saved plans — see logs."
    return list(state.get("stories") or []), session_id, ""


def _resolve_target(repo: str) -> tuple[str, str]:
    """(the git toplevel this run will touch, a user-facing problem or "").

    The toplevel, not the typed path, is what every later write targets —
    ``git worktree add`` writes into ``<toplevel>/.git`` and the push runs from
    there. Consent is checked with ``is_relative_to`` containment, so granting a
    *subdirectory* would not grant the toplevel: resolving first is what keeps
    the consent prompt honest about what is about to be touched.
    """
    from yeaboi.ship import worktree

    try:
        top = worktree.resolve_repo(Path(repo).expanduser())
    except worktree.WorktreeError as exc:
        return "", str(exc)
    try:
        if worktree.is_dirty(top):
            return str(top), f"{top} has uncommitted changes — commit or stash first"
    except worktree.WorktreeError as exc:
        return str(top), str(exc)
    return str(top), ""


def run_ship_page(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> None:
    """Enter Ship from the menu; returns when the user backs out."""
    stories, session_id, message = _load_stories()
    logger.info("Ship page opened: %d stories from session %s", len(stories), session_id or "(none)")
    selected = 0
    action_sel = 0
    repo = str(Path.cwd())
    check_command = ""
    edit_field = ""
    edit = {"buf": "", "cur": 0}
    start = time.monotonic()

    while True:
        w, h = console.size
        live.update(
            _build_ship_pick_screen(
                stories,
                selected,
                repo=repo,
                check_command=check_command,
                width=w,
                height=h,
                shimmer_tick=time.monotonic() - start,
                action_sel=action_sel,
                message=message,
                edit_field=edit_field,
                edit_buf=edit["buf"],
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if edit_field:
            if key == "enter":
                if edit_field == "repo":
                    repo = edit["buf"].strip() or repo
                else:
                    check_command = edit["buf"].strip()
                edit_field = ""
            elif key == "esc":
                edit_field = ""
            elif isinstance(key, str) and key:
                from yeaboi.ui.mode_select import _settings_edit_keypress

                _settings_edit_keypress(key, edit)
            continue
        if key in ("esc", "q"):
            logger.info("Ship page closed from the picker")
            return
        if not stories:
            if key == "enter":
                return
            continue
        if key == "up":
            selected = (selected - 1) % len(stories)
        elif key == "down":
            selected = (selected + 1) % len(stories)
        elif key == "r":
            edit_field, edit["buf"], edit["cur"] = "repo", repo, len(repo)
        elif key == "c":
            edit_field, edit["buf"], edit["cur"] = "check", check_command, len(check_command)
        elif key == "left":
            action_sel = (action_sel - 1) % len(SHIP_PICK_ACTIONS)
        elif key == "right":
            action_sel = (action_sel + 1) % len(SHIP_PICK_ACTIONS)
        elif key == "enter":
            if SHIP_PICK_ACTIONS[action_sel] == "Back":
                return
            message = _launch(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                story=stories[selected],
                session_id=session_id,
                repo=repo,
                check_command=check_command,
            )
            # Returning here means the run finished (or never started) —
            # message carries anything the picker should show.


def _launch(
    console,
    live,
    read_key,
    frame_time,
    supports_timeout,
    *,
    story,
    session_id: str,
    repo: str,
    check_command: str,
) -> str:
    """Consent + worker thread + progress/gate/result loops. Returns a picker message."""
    from yeaboi.ui.shared._consent import _preflight_path_consent

    repo, problem = _resolve_target(repo)
    if problem:
        return problem
    if not _preflight_path_consent(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        repo,
        mode="write",
        context="Ship runs a coding agent against this repository",
    ):
        logger.info("Ship: consent denied for %s", repo)
        return "Launch cancelled — the repository was not granted."

    from yeaboi.ship import engine
    from yeaboi.ship.store import ShipStore
    from yeaboi.ui.shared._music_bar import duck_working_thread

    logger.info("Ship: launching %s against %s", story.id, repo)
    from yeaboi.config import get_ship_board_enabled

    progress_q: queue.Queue = queue.Queue()
    result_box: list = [None, None]
    cancel = threading.Event()

    # The engine hands its run id back as soon as it exists; everything else
    # in the shared store belongs to another session, and this loop must never
    # open a gate over a diff this user did not launch.
    mine: list[str] = [""]

    # The live board, opt-in. Created inside on_run_id (the run id is what it
    # reads the store by) and started there — the loopback server binds
    # instantly and the tunnel comes up on its own thread, so this callback,
    # which the engine awaits before setup, never blocks on the network.
    board_enabled = get_ship_board_enabled()
    board_box: list = [None]

    def _on_run_id(run_id: str) -> None:
        mine[0] = run_id
        if not board_enabled:
            return
        try:
            from yeaboi.ship.live import ShipBoardSession

            session_board = ShipBoardSession(run_id, story_title=story.title or story.id)
            session_board.start()
            board_box[0] = session_board
        except Exception:  # noqa: BLE001 — a board failure must never sink the run
            logger.warning("Ship: could not start the live board", exc_info=True)

    def _on_progress(item: object) -> None:
        progress_q.put(item)
        board = board_box[0]
        if board is not None and is_component_progress(item):
            board.note_component(item)

    def _on_agent_line(line: str) -> None:
        board = board_box[0]
        if board is not None:
            board.note_agent_line(line)

    def _work() -> None:
        try:
            result_box[0] = engine.run_ship(
                story.id,
                repo,
                session_id=session_id,
                check_command=check_command,
                on_progress=_on_progress,
                on_run_id=_on_run_id,
                # Enabling the board turns on the driver's stream-json path, so a
                # plain run (board off) keeps the unchanged one-shot json flow.
                on_agent_line=_on_agent_line if board_enabled else None,
                cancel_event=cancel,
            )
        except BaseException as exc:  # noqa: BLE001 — belt and braces; the engine shouldn't raise
            result_box[1] = exc

    with ShipStore() as watch:
        thread = duck_working_thread(_work, name="ship-run")
        thread.start()
        events_by_id: dict[str, dict] = {}
        start = time.monotonic()
        last_poll = 0.0

        try:
            _drive_progress(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                thread=thread,
                watch=watch,
                progress_q=progress_q,
                events_by_id=events_by_id,
                mine=mine,
                board_box=board_box,
                cancel=cancel,
                start=start,
                last_poll=last_poll,
            )
        finally:
            board = board_box[0]
            if board is not None:
                board.stop()

    run = result_box[0]
    if run is None:
        logger.error("Ship run crashed: %s", result_box[1])
        return f"Run crashed: {result_box[1]} — see logs."
    return _result_loop(console, live, read_key, frame_time, supports_timeout, run)


def _drive_progress(
    console,
    live,
    read_key,
    frame_time,
    supports_timeout,
    *,
    thread,
    watch,
    progress_q,
    events_by_id,
    mine,
    board_box,
    cancel,
    start,
    last_poll,
) -> None:
    """The progress/gate render loop, extracted so the board teardown is a
    single ``finally`` around it regardless of how the loop exits."""
    while thread.is_alive():
        while True:
            try:
                item = progress_q.get_nowait()
            except queue.Empty:
                break
            if is_component_progress(item):
                events_by_id[item["component_id"]] = item
        gated = None
        if mine[0] and time.monotonic() - last_poll > 0.5:
            last_poll = time.monotonic()
            # By id, not by scanning a newest-first page: with concurrency
            # raised, later runs would push ours off the end and its gate
            # would never open on the surface that launched it.
            row = watch.get_run(mine[0])
            if row is not None and row.status == "awaiting_approval" and not row.gate_resolution:
                gated = row
        if gated is not None:
            outcome = _gate_loop(console, live, read_key, frame_time, supports_timeout, watch, gated, cancel)
            if outcome == "cancelled":
                pass  # the engine notices the event and winds down
            continue
        w, h = console.size
        board = board_box[0]
        board_link = board.share_url if board is not None else ""
        live.update(
            _build_ship_progress_screen(
                list(events_by_id.values()),
                tick=time.monotonic() - start,
                width=w,
                height=h,
                board_link=board_link,
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if key in ("esc", "q") and not cancel.is_set():
            logger.info("Ship: cancel requested from the progress screen")
            cancel.set()
    thread.join()


def _gate_loop(console, live, read_key, frame_time, supports_timeout, watch, run, cancel) -> str:
    """The approval screen; returns "resolved" | "cancelled". Blocks until one."""
    from yeaboi.ui.mode_select import _settings_edit_keypress

    logger.info("Ship gate open for %s", run.run_id)
    action_sel = 0
    comment: str | None = None
    edit = {"buf": "", "cur": 0}
    message = ""
    diff_offset = 0
    # The builder publishes the pane's true geometry here; apply_scroll clamps
    # to exactly what is on screen, so the loop counter and the visible
    # position can never diverge (see ui/shared/_scroll.py).
    scroll_meta: dict = {}
    while True:
        w, h = console.size
        live.update(
            _build_ship_gate_screen(
                run,
                action_sel=action_sel,
                width=w,
                height=h,
                comment_edit=edit["buf"] if comment is not None else None,
                message=message,
                diff_offset=diff_offset,
                scroll_meta=scroll_meta,
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if comment is not None:
            if key == "enter":
                if watch.resolve_gate(run.run_id, "rejected", edit["buf"].strip()):
                    logger.info("Ship gate: rejected with comment (%d chars)", len(edit["buf"].strip()))
                    return "resolved"
                message = "The gate was already answered."
                comment = None
            elif key == "esc":
                comment = None
            elif isinstance(key, str) and key:
                _settings_edit_keypress(key, edit)
            continue
        if key in SCROLL_KEYS:
            # One membership test routes arrows, wheel, page and home/end; the
            # burst is drained in one shot so a fast wheel flick repaints once.
            diff_offset = coalesce_scroll(diff_offset, key, scroll_meta, read_key)
        elif key in ("left",):
            action_sel = (action_sel - 1) % len(SHIP_GATE_ACTIONS)
        elif key in ("right", "tab"):
            action_sel = (action_sel + 1) % len(SHIP_GATE_ACTIONS)
        elif key == "enter":
            action = SHIP_GATE_ACTIONS[action_sel]
            if action == "Approve":
                if watch.resolve_gate(run.run_id, "approved"):
                    logger.info("Ship gate: approved")
                    return "resolved"
                message = "The gate was already answered."
            elif action == "Reject":
                comment = ""
                edit["buf"], edit["cur"] = "", 0
            elif action == "Cancel Run":
                logger.info("Ship gate: run cancelled by the approver")
                cancel.set()
                return "cancelled"
        elif key in ("esc", "q"):
            # Esc at the gate means "not now", not "reject": keep the run
            # waiting and fall back to the progress screen. The gate reopens
            # on the next poll tick.
            return "resolved"


def _result_loop(console, live, read_key, frame_time, supports_timeout, run) -> str:
    """The terminal screen; returns the message for the picker ("" usually)."""
    logger.info("Ship run finished: %s (%s)", run.run_id or "(unstarted)", run.status)
    action_sel = 0
    notice = ""
    start = time.monotonic()
    while True:
        w, h = console.size
        live.update(
            _build_ship_result_screen(
                run,
                action_sel=action_sel,
                width=w,
                height=h,
                shimmer_tick=time.monotonic() - start,
                notice=notice,
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if key in ("esc", "q"):
            return ""
        if key == "left":
            action_sel = (action_sel - 1) % len(SHIP_RESULT_ACTIONS)
        elif key == "right":
            action_sel = (action_sel + 1) % len(SHIP_RESULT_ACTIONS)
        elif key == "enter":
            action = SHIP_RESULT_ACTIONS[action_sel]
            if action == "Back":
                return ""
            if action == "Copy":
                from yeaboi.clipboard import copy_text

                payload = run.pr_url or f"{run.story_id}: {run.status} on {run.branch}"
                notice = "Copied." if copy_text(payload) else "Couldn't reach the clipboard."
                logger.info("Ship result: copy %s", "ok" if notice == "Copied." else "failed")
