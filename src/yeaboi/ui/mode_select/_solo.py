"""The Weekly Review page: mark last week's actions, run the review, read it.

One dispatch entry (:func:`run_solo_review_page`) rather than another branch
spelled inline in ``select_mode``. The engine runs on a daemon thread behind
the shared progress screen (the ``_run_report_generate`` template, minus
cancel — the engine has no cancel seam); this loop owns the keys, the carried
statuses and the detail view's scroll.

Imports from :mod:`yeaboi.ui.mode_select` happen lazily inside function bodies —
the package imports this module's caller, so a top-level import is a cycle.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace

from rich.console import Console

from yeaboi.solo.engine import ACTION_STATUSES
from yeaboi.ui.mode_select.screens._screens_solo import (
    SOLO_REVIEW_CARRIED_ACTIONS,
    SOLO_REVIEW_DETAIL_ACTIONS,
    SOLO_REVIEW_PHASES,
    _build_solo_review_screen,
)
from yeaboi.ui.shared._scroll import SCROLL_KEYS, coalesce_scroll

logger = logging.getLogger(__name__)

# Space walks a carried action through these, in order, and wraps.
_MARK_CYCLE = ("pending", "done", "dropped")


def _cycle_status(status: str) -> str:
    """The next status Space lands on; anything unknown restarts the cycle."""
    if status not in _MARK_CYCLE:
        return _MARK_CYCLE[0]
    return _MARK_CYCLE[(_MARK_CYCLE.index(status) + 1) % len(_MARK_CYCLE)]


def _load_carried(db_path):
    """Last review's open actions, or an empty list when nothing is carried."""
    from yeaboi.projects.active import get_active_project, get_context_deps
    from yeaboi.projects.scope import resolve_scope
    from yeaboi.solo.engine import carried_actions

    scope = resolve_scope(get_active_project(), context_deps=get_context_deps(), db_path=db_path)
    return list(carried_actions(scope, db_path=db_path))


def run_solo_review_page(console: Console, live, read_key, frame_time: float, supports_timeout: bool) -> None:
    """Enter Weekly Review from the hub's "+ New review"; returns when the user backs out."""
    from yeaboi.ui.mode_select import _ana_dbp

    logger.info("weekly review page: opened")
    try:
        carried = _load_carried(_ana_dbp)
    except Exception as e:  # noqa: BLE001 — a broken store must not hide the page
        logger.warning("weekly review page: could not read carried actions: %s", e)
        carried = []
    statuses: dict[str, str] = {a.id: a.status for a in carried}
    cursor = 0
    action_sel = 0
    message = ""
    anim_start = time.monotonic()

    while True:
        w, h = console.size
        elapsed = time.monotonic() - anim_start
        live.update(
            _build_solo_review_screen(
                {
                    "view": "carried",
                    "carried": [replace(a, status=statuses.get(a.id, a.status)) for a in carried],
                    "cursor": cursor,
                    "actions": SOLO_REVIEW_CARRIED_ACTIONS,
                    "message": message,
                },
                width=w,
                height=max(10, h - 1),
                action_sel=action_sel,
                shimmer_tick=elapsed,
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if key in ("esc", "q"):
            logger.info("weekly review page: closed from the carried view")
            return
        if key == "up" and carried:
            cursor = (cursor - 1) % len(carried)
        elif key == "down" and carried:
            cursor = (cursor + 1) % len(carried)
        elif key == " " and carried:
            action = carried[cursor]
            statuses[action.id] = _cycle_status(statuses.get(action.id, action.status))
            logger.info("weekly review page: marked %s as %s", action.id, statuses[action.id])
        elif key == "left":
            action_sel = (action_sel - 1) % len(SOLO_REVIEW_CARRIED_ACTIONS)
        elif key in ("right", "tab"):
            action_sel = (action_sel + 1) % len(SOLO_REVIEW_CARRIED_ACTIONS)
        elif key == "enter":
            if SOLO_REVIEW_CARRIED_ACTIONS[action_sel] == "Back":
                logger.info("weekly review page: closed via Back")
                return
            marked = {aid: st for aid, st in statuses.items() if st in ACTION_STATUSES}
            review, err = _generate(console, live, read_key, frame_time, supports_timeout, marked)
            if review is None:
                message = f"Review failed: {err}"
                continue
            _detail_loop(console, live, read_key, frame_time, supports_timeout, review)
            return


def _generate(console, live, read_key, frame_time, supports_timeout, carried_statuses: dict[str, str]):
    """Run the engine on a worker thread behind the progress screen.

    Returns ``(review, None)`` or ``(None, error)``; the frame loop repaints
    live progress while the tracker and LLM calls run.
    """
    from yeaboi.projects.active import get_active_project, get_context_deps
    from yeaboi.solo.engine import run_weekly_review
    from yeaboi.ui.mode_select import _ana_dbp, _duck_react
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_progress_screen
    from yeaboi.ui.shared._components import SOLO_THEME, solo_review_title
    from yeaboi.ui.shared._music_bar import duck_working_thread

    project_id = get_active_project()
    logger.info("weekly review: generating (project=%s, %d marked)", project_id or "(none)", len(carried_statuses))
    progress: list[str] = ["Starting"]
    result_box: list = [None, None]  # [review, exception]

    def _worker() -> None:
        try:
            result_box[0] = run_weekly_review(
                project_id=project_id,
                context_deps=get_context_deps(),
                carried_statuses=carried_statuses or None,
                db_path=_ana_dbp,
                on_progress=progress.append,
            )
        except BaseException as e:  # noqa: BLE001 — re-surfaced on the UI thread below
            result_box[1] = e

    thread = duck_working_thread(_worker, name="weekly-review-generate")
    thread.start()
    start = time.monotonic()
    while thread.is_alive():
        elapsed = time.monotonic() - start
        w, h = console.size
        live.update(
            _build_standup_progress_screen(
                list(progress),
                width=w,
                height=max(10, h - 1),
                elapsed=elapsed,
                anim_tick=elapsed,
                theme=SOLO_THEME,
                title=solo_review_title(elapsed, width=w),
                label="Reviewing your week",
                phases=SOLO_REVIEW_PHASES,
            )
        )
        # The key read doubles as the frame pacer; no cancel seam, so keys are dropped.
        if supports_timeout:
            read_key(timeout=frame_time)
        else:
            time.sleep(1 / 30)
    thread.join()
    err = result_box[1]
    if err is None and result_box[0] is not None:
        review = result_box[0]
        logger.info("weekly review: generated %s — %d action(s)", review.week_label, len(review.actions))
        _duck_react("report_done")
        return review, None
    logger.error("weekly review generate failed: %s", err, exc_info=err)
    return None, err


def _detail_loop(console, live, read_key, frame_time, supports_timeout, review) -> None:
    """Show one review with Export / Anonymize / Back."""
    from yeaboi.solo.export import build_weekly_review_markdown, export_weekly_review
    from yeaboi.ui.mode_select import _anon_note, _export_via_picker, _run_anonymize_pass
    from yeaboi.ui.shared._components import SOLO_THEME, solo_review_title

    action_sel = 0
    scroll = 0
    scroll_meta: dict = {}
    message = ""
    anon = None
    anim_start = time.monotonic()
    while True:
        w, h = console.size
        elapsed = time.monotonic() - anim_start
        shown = review
        if anon is not None:
            from yeaboi.anonymize.apply import mask_artifact

            shown = mask_artifact(review, anon.replacements)
        live.update(
            _build_solo_review_screen(
                {"view": "detail", "review": shown, "actions": SOLO_REVIEW_DETAIL_ACTIONS, "message": message},
                scroll_offset=scroll,
                scroll_meta=scroll_meta,
                width=w,
                height=max(10, h - 1),
                action_sel=action_sel,
                shimmer_tick=elapsed,
                anon_note=_anon_note(anon) if anon is not None else "",
            )
        )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if key in ("esc", "q"):
            return
        if key in SCROLL_KEYS:
            scroll = coalesce_scroll(scroll, key, scroll_meta, read_key)
        elif key == "left":
            action_sel = (action_sel - 1) % len(SOLO_REVIEW_DETAIL_ACTIONS)
        elif key in ("right", "tab"):
            action_sel = (action_sel + 1) % len(SOLO_REVIEW_DETAIL_ACTIONS)
        elif key == "enter":
            action = SOLO_REVIEW_DETAIL_ACTIONS[action_sel]
            if action == "Back":
                return
            if action == "Export":
                target = shown
                result = _export_via_picker(
                    console,
                    live,
                    read_key,
                    frame_time,
                    supports_timeout,
                    mode="solo",
                    files_export=lambda t=target: f"Exported to {export_weekly_review(t)['markdown'].parent}",
                    get_document=lambda t=target: (
                        f"Weekly Review — {t.week_label}",
                        build_weekly_review_markdown(t),
                    ),
                )
                if result is not None:
                    message = result
                    logger.info("weekly review: export — %s", result)
            elif action == "Anonymize":
                if anon is not None:
                    anon = None
                    message = "Showing the original."
                    continue
                anon = _run_anonymize_pass(
                    console,
                    live,
                    read_key,
                    frame_time,
                    supports_timeout,
                    markdown=build_weekly_review_markdown(review),
                    instruction="",
                    project_name=review.project_name,
                    source_mode="solo",
                    theme=SOLO_THEME,
                    title=solo_review_title,
                )
                message = "Anonymized — names and identifiers masked." if anon is not None else "Anonymize failed."
