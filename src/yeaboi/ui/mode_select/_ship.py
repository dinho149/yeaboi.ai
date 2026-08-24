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
from yeaboi.ship.setup import load_plan, resolve_target
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


def _visible_rows(rows: list, expanded: set[str]) -> list:
    """The outline with the children of collapsed parents dropped.

    Recomputed every frame from ``(rows, expanded)`` rather than kept as widget
    state — the same shape standup's inline team expansion uses, and the reason
    a selection index can never point at a row that is no longer drawn.
    """
    visible: list = []
    hidden: set[str] = set()
    for row in rows:
        if row.parent_key and (row.parent_key in hidden or row.parent_key not in expanded):
            hidden.add(row.key)
            continue
        visible.append(row)
    return visible


def run_ship_page(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> None:
    """Enter Ship from the menu; returns when the user backs out."""
    from yeaboi.ship import scope
    from yeaboi.ui.mode_select.screens._screens_ship import SCOPE_ONE, SCOPE_SPLIT

    state, session_id, project_name, message = load_plan()
    rows = scope.outline(state)
    logger.info("Ship page opened: %d plan rows from session %s", len(rows), session_id or "(none)")
    parents = {r.parent_key for r in rows if r.parent_key}
    # Epics open, tasks tucked away: the story list is what the picker showed
    # before, and a plan with tasks decomposed would otherwise open on a wall.
    expanded = {r.key for r in rows if r.depth == 0}
    selected = 0
    action_sel = 0
    repo = str(Path.cwd())
    check_command = ""
    scope_mode = SCOPE_ONE
    edit_field = ""
    edit = {"buf": "", "cur": 0}
    start = time.monotonic()

    while True:
        visible = _visible_rows(rows, expanded)
        if visible:
            selected = max(0, min(selected, len(visible) - 1))
        current = visible[selected] if visible else None
        # Counted off the outline, not re-resolved: this runs every frame.
        split_count = (
            sum(1 for r in rows if r.level == "story" and r.parent_key == current.key) if current is not None else 0
        )
        w, h = console.size
        live.update(
            _build_ship_pick_screen(
                visible,
                selected,
                expanded=expanded,
                has_children=parents,
                scope_mode=scope_mode,
                split_count=split_count,
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
        if not visible or current is None:
            if key == "enter":
                return
            continue
        if key == "up":
            selected = (selected - 1) % len(visible)
        elif key == "down":
            selected = (selected + 1) % len(visible)
        elif key in ("space", " "):
            if current.key in parents:
                expanded.symmetric_difference_update({current.key})
            elif current.parent_key:
                # A leaf collapses its parent and lands on it, so a deep tree
                # can be climbed without reaching for the arrow keys.
                expanded.discard(current.parent_key)
                collapsed = _visible_rows(rows, expanded)
                selected = next((i for i, r in enumerate(collapsed) if r.key == current.parent_key), selected)
        elif key == "s":
            if current.level == "epic":
                scope_mode = SCOPE_SPLIT if scope_mode == SCOPE_ONE else SCOPE_ONE
                message = ""
            else:
                message = f"Only an epic can ship one PR per story — {current.id} is a {current.level}."
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
                item_id=current.id,
                level=current.level,
                item_title=current.title,
                split=scope_mode == SCOPE_SPLIT and current.level == "epic",
                session_id=session_id,
                project_name=project_name,
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
    item_id: str,
    level: str,
    item_title: str,
    split: bool,
    session_id: str,
    project_name: str,
    repo: str,
    check_command: str,
) -> str:
    """Consent + worker thread + progress/gate/result loops. Returns a picker message.

    ``split`` runs an epic as one stacked PR per story: the engine sequences the
    members, and the gate loop below follows them because ``on_run_id`` fires
    once per member.
    """
    from yeaboi.ui.shared._consent import _preflight_path_consent

    repo, problem = resolve_target(repo)
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

    logger.info("Ship: launching %s (%s, split=%s) against %s", item_id, level, split, repo)
    from yeaboi.config import get_ship_board_enabled

    progress_q: queue.Queue = queue.Queue()
    result_box: list = [None, None, ()]  # last run, crash, every batch member
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
        previous = board_box[0]
        if previous is not None:
            # A batch calls this once per member. ShipServer binds a fixed port,
            # so leaving the last one up both leaks a server and a tunnel and
            # stops the next member's board from binding at all.
            board_box[0] = None
            try:
                previous.stop()
            except Exception:  # noqa: BLE001 — see below; a board must never sink the run
                logger.warning("Ship: could not stop the previous live board", exc_info=True)
        try:
            from yeaboi.ship.live import ShipBoardSession

            session_board = ShipBoardSession(run_id, story_title=item_title or item_id, project_name=project_name)
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
            common = {
                "level": level,
                "session_id": session_id,
                "check_command": check_command,
                "on_progress": _on_progress,
                "on_run_id": _on_run_id,
                # Enabling the board turns on the driver's stream-json path, so a
                # plain run (board off) keeps the unchanged one-shot json flow.
                "on_agent_line": _on_agent_line if board_enabled else None,
                "cancel_event": cancel,
            }
            if split:
                members = engine.run_ship_batch(item_id, repo, **common)
                # The last member that actually ran. A stopped batch ends in
                # unstarted rows, which were never persisted and describe nothing.
                started = [m for m in members if m.run_id]
                result_box[0] = started[-1] if started else (members[-1] if members else None)
                result_box[2] = members
            else:
                result_box[0] = engine.run_ship(item_id, repo, **common)
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
    outcome = _result_loop(console, live, read_key, frame_time, supports_timeout, run)
    return _batch_message(result_box[2]) or outcome


def _batch_message(members) -> str:
    """What the picker says after a batch: what shipped, and why it stopped."""
    if not members:
        return ""
    shipped = sum(1 for m in members if m.status == "approved")
    if shipped == len(members):
        return f"Batch complete — {shipped} of {len(members)} stories shipped."
    stopped = next((m for m in members if m.status not in ("approved", "planned")), None)
    reason = (stopped.warnings[-1] if stopped is not None and stopped.warnings else "") or (
        stopped.status if stopped is not None else "not started"
    )
    return f"Batch stopped — {shipped} of {len(members)} stories shipped. {reason}"


def continue_batch_page(
    console,
    live,
    read_key,
    frame_time,
    supports_timeout,
    *,
    item_id: str,
    repo: str,
    session_id: str,
    check_command: str = "",
) -> str:
    """Pick up a batch that stopped partway. Returns a hub message.

    Deliberately the same call as launching one: ``run_ship_batch`` adopts the
    unfinished batch for this epic and skips the stories already shipped, so
    "continue" and "launch" are one code path and cannot drift.

    ``check_command`` has to be handed back in: it is not stored on the batch,
    only on each member's recorded verdict, and a continuation that quietly
    validated nothing would be the worst kind of surprise.
    """
    return _launch(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        item_id=item_id,
        level="epic",
        item_title=item_id,
        split=True,
        session_id=session_id,
        project_name="",
        repo=repo,
        check_command=check_command,
    )


def resume_run_page(console, live, read_key, frame_time, supports_timeout, run_id: str) -> str:
    """Re-attach to a run abandoned at the gate and finish it. Returns a hub message.

    The same three loops a fresh launch uses (:func:`_drive_progress`,
    :func:`_gate_loop`, :func:`_result_loop`) — only the worker differs, because
    resume starts from a stored artifact and a kept worktree rather than from a
    story. No consent prompt: the repository was granted when the run was launched
    and resume writes nowhere new.
    """
    from yeaboi.ship import engine
    from yeaboi.ship.store import ShipStore
    from yeaboi.ui.shared._music_bar import duck_working_thread

    logger.info("Ship: resuming %s", run_id)
    progress_q: queue.Queue = queue.Queue()
    result_box: list = [None, None]
    cancel = threading.Event()
    mine: list[str] = [run_id]  # already known — resume is addressed at one run
    board_box: list = [None]

    def _work() -> None:
        try:
            result_box[0] = engine.resume_ship(
                run_id,
                on_progress=progress_q.put,
                cancel_event=cancel,
            )
        except BaseException as exc:  # noqa: BLE001 — belt and braces; the engine shouldn't raise
            result_box[1] = exc

    with ShipStore() as watch:
        thread = duck_working_thread(_work, name="ship-resume")
        thread.start()
        _drive_progress(
            console,
            live,
            read_key,
            frame_time,
            supports_timeout,
            thread=thread,
            watch=watch,
            progress_q=progress_q,
            events_by_id={},
            mine=mine,
            board_box=board_box,
            cancel=cancel,
            start=time.monotonic(),
            last_poll=0.0,
        )

    run = result_box[0]
    if run is None:
        logger.error("Ship resume crashed: %s", result_box[1])
        return f"Resume crashed: {result_box[1]} — see logs."
    if run.status == "failed" and run.warnings:
        return run.warnings[-1]
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
    note_box = [""]  # which batch member is in flight, if any
    shown = ""  # the run the checklist is currently describing
    while thread.is_alive():
        if mine[0] and mine[0] != shown:
            # A batch moved on. The checklist is keyed by phase, so the previous
            # member's completed validate/gate/finalize rows would stand until
            # this one reached them — clear before draining the new member's.
            shown = mine[0]
            events_by_id.clear()
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
            if row is not None:
                note_box[0] = f"story {row.batch_index} of {row.batch_total}" if row.batch_total else ""
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
        board_code = board.display_code if board is not None and board_link else ""
        live.update(
            _build_ship_progress_screen(
                list(events_by_id.values()),
                tick=time.monotonic() - start,
                width=w,
                height=h,
                board_link=board_link,
                board_code=board_code,
                batch_note=note_box[0],
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

                payload = run.pr_url or f"{run.item_id}: {run.status} on {run.branch}"
                notice = "Copied." if copy_text(payload) else "Couldn't reach the clipboard."
                logger.info("Ship result: copy %s", "ok" if notice == "Copied." else "failed")
