"""Routing and pages for the Agents family (agentwatch) in the TUI.

One dispatch entry (:func:`route_agent_mode`) instead of four more branches in
``select_mode``'s routing chain. Each mode wraps itself in ``mode_log`` (its own
log file under ~/.yeaboi/logs/agentwatch/) and its one-time beta notice.

All four pages share one threaded-engine loop: the page opens instantly on
the last saved report (age-stamped) while the pipeline runs on a daemon
thread feeding a phase checklist / refresh banner, then the capped fresh
result swaps in (r re-runs behind the visible report, esc backs out).

Imports from :mod:`yeaboi.ui.mode_select` happen lazily inside function bodies —
the package imports this module's callers, so a top-level import is a cycle.
"""

from __future__ import annotations

import logging
import time

from rich.console import Console

from yeaboi.agentwatch.export import (
    build_advisor_markdown,
    build_security_markdown,
    build_standup_markdown,
    build_usage_markdown,
)
from yeaboi.logging_setup import mode_log
from yeaboi.ui.mode_select.screens._screens_agents import AGENT_RESULT_ACTIONS
from yeaboi.ui.shared._beta_notice import show_beta_notice
from yeaboi.ui.shared._components import (
    AGENT_ADVISOR_THEME,
    AGENT_SECURITY_THEME,
    AGENT_STANDUP_THEME,
    AGENT_USAGE_THEME,
    Theme,
)

logger = logging.getLogger(__name__)

_MODE_META: dict[str, tuple[str, Theme]] = {
    "agent-usage": ("Agent Usage", AGENT_USAGE_THEME),
    "agent-advisor": ("Agent Advisor", AGENT_ADVISOR_THEME),
    "agent-standup": ("Agent Standup", AGENT_STANDUP_THEME),
    "agent-security": ("Agent Security", AGENT_SECURITY_THEME),
}


def route_agent_mode(
    key: str,
    *,
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
) -> None:
    """Enter one Agents mode from the menu; returns when the user backs out."""
    label, _theme = _MODE_META.get(key, (key, AGENT_USAGE_THEME))
    with mode_log("agentwatch"):
        logger.info("%s opened", label)
        if not show_beta_notice(live, console, read_key, frame_time, supports_timeout, mode_key=key):
            logger.info("%s beta notice declined — back to menu", label)
            return
        if key == "agent-usage":
            _run_agent_usage_page(console, live, read_key, frame_time, supports_timeout)
        elif key == "agent-advisor":
            _run_agent_advisor_page(console, live, read_key, frame_time, supports_timeout)
        elif key == "agent-standup":
            _run_agent_standup_page(console, live, read_key, frame_time, supports_timeout)
        elif key == "agent-security":
            _run_agent_security_page(console, live, read_key, frame_time, supports_timeout)
        logger.info("%s closed", label)


def _run_result_action(action: str, artifact, *, label: str, export_kind: str, build_markdown) -> str:
    """Run Export or Copy for a finished artifact; return the notice to show.

    Never raises: a failed write or an absent clipboard tool becomes a notice,
    because losing the whole result screen over a failed copy would be a far
    worse outcome than the copy not happening.
    """
    from yeaboi.agentwatch.export import export_artifact
    from yeaboi.clipboard import copy_text

    if action == "Export":
        try:
            written = export_artifact(artifact, kind=export_kind)
        except Exception as exc:  # noqa: BLE001 — a failed export must not close the page
            logger.warning("%s page: export failed: %s", label, exc)
            return "Couldn't write the export — see logs."
        path = next(iter(written.values()), None)
        logger.info("%s page: exported to %s", label, path)
        return f"Exported to {path}" if path else "Nothing to export."

    if action == "Copy":
        try:
            markdown = build_markdown(artifact)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s page: could not build markdown to copy: %s", label, exc)
            return "Couldn't copy — see logs."
        ok = copy_text(markdown)
        logger.info("%s page: copy %s", label, "succeeded" if ok else "failed")
        return "Copied the report to the clipboard." if ok else "Couldn't reach the clipboard."

    return ""


def _load_latest_artifact(kind: str):
    """The newest saved report of one kind, rehydrated, plus its created_at.

    None when history is empty or the stored payload cannot be rebuilt — the
    page then falls back to the first-run loading screen. Never raises: a
    broken history row must not take down the page it was meant to speed up.
    """
    from yeaboi.agentwatch.store import AgentWatchStore, report_from_payload
    from yeaboi.paths import get_db_path

    try:
        with AgentWatchStore(get_db_path()) as store:
            row = store.latest_report(kind)
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent %s page: could not read report history: %s", kind, exc)
        return None
    if not row:
        return None
    artifact = report_from_payload(kind, row.get("report"))
    if artifact is None:
        return None
    return artifact, str(row.get("created_at") or "")


def _run_threaded_engine_page(
    console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    *,
    label: str,
    run_engine,
    build_screen,
    make_failure_artifact,
    export_kind: str,
    build_markdown,
) -> None:
    """Shared page loop: engine on a worker thread, instant last report, result.

    The page opens on the *last saved* report (stamped with its age) whenever
    one exists, while a daemon thread runs a fresh engine pass behind a
    "Refreshing…" banner — the loading screen only ever appears on a first run.
    While loading, the engine's structured progress events (analysis_component
    dicts) render as a phase checklist with a files meter; the queue is drained
    every frame (latest event per phase wins) so the display never lags the
    scan. The engines never raise (parse → fallback → format);
    ``make_failure_artifact`` is belt-and-braces for a bug — and a failed
    background refresh keeps the stale report on screen rather than replacing
    it with a failure artifact.

    On the result screen ←/→ move between Export / Copy / Re-run / Back and
    enter activates; `r` still re-runs (keeping the current report visible) and
    esc/q still backs out (a mid-run back-out lets the daemon finish + export).
    Export writes the Markdown artifact, Copy puts the same Markdown on the
    clipboard — both report back through the page's notice line rather than a
    popup, because neither can fail in a way the user must acknowledge.
    """
    import queue

    from yeaboi.analysis.progress import is_component_progress
    from yeaboi.ui.shared._music_bar import duck_working_thread

    logger.info("%s page opened", label)
    artifact = None
    as_of = ""
    loaded = _load_latest_artifact(export_kind)
    if loaded is not None:
        artifact, as_of = loaded
        logger.info("%s page: showing last saved report (from %s) while refreshing", label, as_of or "unknown time")

    progress_q: queue.Queue = queue.Queue()
    result_q: queue.Queue = queue.Queue(maxsize=1)
    events_by_id: dict[str, dict] = {}
    status = ""
    refreshing = False

    def _start_worker() -> None:
        nonlocal progress_q, result_q, events_by_id, status, refreshing
        pq: queue.Queue = queue.Queue()
        rq: queue.Queue = queue.Queue(maxsize=1)

        def _work() -> None:
            try:
                rq.put(("ok", run_engine(pq.put)))
            except Exception as exc:  # noqa: BLE001 — belt and braces; the engine shouldn't raise
                logger.exception("%s engine failed", label)
                rq.put(("err", exc))

        progress_q, result_q = pq, rq
        events_by_id = {}
        status = ""
        refreshing = artifact is not None
        # duck_working_thread: the corner robo bobs for the engine's lifetime,
        # same liveness cue the Humans pages give their worker runs.
        duck_working_thread(_work, name=label).start()

    _start_worker()
    start = time.monotonic()
    action_sel = 0
    notice = ""

    def _handle_rerun() -> None:
        nonlocal notice, as_of
        if refreshing:
            notice = "Already refreshing…"
            return
        logger.info("%s page: re-run requested", label)
        notice = ""
        as_of = artifact.generated_at
        _start_worker()

    while True:
        # Drain everything queued since the last frame — the collector can
        # emit faster than 60fps, and showing anything but the latest event
        # per phase would replay stale progress.
        while True:
            try:
                item = progress_q.get_nowait()
            except queue.Empty:
                break
            if is_component_progress(item):
                events_by_id[item["component_id"]] = item
            else:
                status = str(item)
        try:
            outcome, payload = result_q.get_nowait()
        except queue.Empty:
            pass
        else:
            if outcome == "ok":
                artifact = payload
                refreshing = False
                as_of = ""
                notice = ""
                logger.info("%s page: artifact ready", label)
            elif artifact is not None:
                # Keep the stale report — losing a good dashboard over a
                # failed background refresh is the worse outcome.
                refreshing = False
                notice = "Refresh failed — showing the last saved report (see logs)."
                logger.warning("%s page: background refresh failed; keeping the last saved report", label)
            else:
                artifact = make_failure_artifact(payload)
                refreshing = False

        w, h = console.size
        tick = time.monotonic() - start
        if artifact is None:
            live.update(
                build_screen(
                    None,
                    width=w,
                    height=h,
                    shimmer_tick=tick,
                    status=status,
                    progress=list(events_by_id.values()),
                )
            )
        else:
            live.update(
                build_screen(
                    artifact,
                    width=w,
                    height=h,
                    shimmer_tick=tick,
                    action_sel=action_sel,
                    notice=notice,
                    refreshing=refreshing,
                    as_of=as_of,
                    # The refresh banner names the running phase + meter, so
                    # progress is visible on the common path too — not only on
                    # the first-ever run's full checklist.
                    progress=list(events_by_id.values()) if refreshing else None,
                )
            )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if key in ("esc", "q"):
            if artifact is None or refreshing:
                logger.info("%s page: backed out while running", label)
            return
        if artifact is None:
            continue  # still loading: only esc/q act
        if key == "r":
            _handle_rerun()
        elif key == "left":
            action_sel = (action_sel - 1) % len(AGENT_RESULT_ACTIONS)
        elif key == "right":
            action_sel = (action_sel + 1) % len(AGENT_RESULT_ACTIONS)
        elif key == "enter":
            action = AGENT_RESULT_ACTIONS[action_sel]
            logger.info("%s page: action %s", label, action)
            if action == "Back":
                return
            if action == "Re-run":
                _handle_rerun()
            else:
                notice = _run_result_action(
                    action, artifact, label=label, export_kind=export_kind, build_markdown=build_markdown
                )


def _run_agent_usage_page(console, live, read_key, frame_time, supports_timeout) -> None:
    """Agent Usage — threaded engine run + capped dashboard."""
    from yeaboi.ui.mode_select.screens._screens_agents import _build_agent_usage_screen

    def _run(on_progress):
        from yeaboi.agentwatch.engine import run_agent_usage

        return run_agent_usage(on_progress=on_progress)

    def _failure(exc):
        from yeaboi.agent.state import AgentUsageReport

        return AgentUsageReport(warnings=(f"Agent usage failed: {exc}",))

    _run_threaded_engine_page(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        label="agent-usage",
        run_engine=_run,
        build_screen=_build_agent_usage_screen,
        make_failure_artifact=_failure,
        export_kind="usage",
        build_markdown=build_usage_markdown,
    )


def _run_agent_advisor_page(console, live, read_key, frame_time, supports_timeout) -> None:
    """Agent Advisor — threaded engine run + capped recoverable-spend report."""
    from yeaboi.ui.mode_select.screens._screens_agents import _build_agent_advisor_screen

    def _run(on_progress):
        from yeaboi.agentwatch.advisor import run_agent_advisor

        return run_agent_advisor(on_progress=on_progress)

    def _failure(exc):
        from yeaboi.agent.state import AgentAdvisorReport

        return AgentAdvisorReport(warnings=(f"Agent advisor failed: {exc}",))

    _run_threaded_engine_page(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        label="agent-advisor",
        run_engine=_run,
        build_screen=_build_agent_advisor_screen,
        make_failure_artifact=_failure,
        export_kind="advisor",
        build_markdown=build_advisor_markdown,
    )


def _run_agent_standup_page(console, live, read_key, frame_time, supports_timeout) -> None:
    """Agent Standup — threaded engine run + capped digest."""
    from yeaboi.ui.mode_select.screens._screens_agents import _build_agent_standup_screen

    def _run(on_progress):
        from yeaboi.agentwatch.engine import run_agent_standup

        return run_agent_standup(on_progress=on_progress)

    def _failure(exc):
        from yeaboi.agent.state import AgentStandupDigest

        return AgentStandupDigest(warnings=(f"Agent standup failed: {exc}",))

    _run_threaded_engine_page(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        label="agent-standup",
        run_engine=_run,
        build_screen=_build_agent_standup_screen,
        make_failure_artifact=_failure,
        export_kind="standup",
        build_markdown=build_standup_markdown,
    )


def _run_agent_security_page(console, live, read_key, frame_time, supports_timeout) -> None:
    """Agent Security — threaded engine run + capped findings report."""
    from yeaboi.ui.mode_select.screens._screens_agents import _build_agent_security_screen

    def _run(on_progress):
        from yeaboi.agentwatch.engine import run_agent_security

        return run_agent_security(on_progress=on_progress)

    def _failure(exc):
        from yeaboi.agent.state import AgentSecurityReport

        return AgentSecurityReport(warnings=(f"Agent security scan failed: {exc}",))

    _run_threaded_engine_page(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        label="agent-security",
        run_engine=_run,
        build_screen=_build_agent_security_screen,
        make_failure_artifact=_failure,
        export_kind="security",
        build_markdown=build_security_markdown,
    )
