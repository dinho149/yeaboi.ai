"""Routing and pages for the Agents family (agentwatch) in the TUI.

One dispatch entry (:func:`route_agent_mode`) instead of three more branches in
``select_mode``'s routing chain. Each mode wraps itself in ``mode_log`` (its own
log file under ~/.yeaboi/logs/agentwatch/) and its one-time beta notice.

All three pages share one threaded-engine loop: the pipeline runs on a
daemon thread feeding a progress spinner, then the capped result renders
statically (r re-runs, esc backs out).

Imports from :mod:`yeaboi.ui.mode_select` happen lazily inside function bodies —
the package imports this module's callers, so a top-level import is a cycle.
"""

from __future__ import annotations

import logging
import time

from rich.console import Console

from yeaboi.agentwatch.export import build_security_markdown, build_standup_markdown, build_usage_markdown
from yeaboi.logging_setup import mode_log
from yeaboi.ui.mode_select.screens._screens_agents import AGENT_RESULT_ACTIONS
from yeaboi.ui.shared._beta_notice import show_beta_notice
from yeaboi.ui.shared._components import (
    AGENT_SECURITY_THEME,
    AGENT_STANDUP_THEME,
    AGENT_USAGE_THEME,
    Theme,
)

logger = logging.getLogger(__name__)

_MODE_META: dict[str, tuple[str, Theme]] = {
    "agent-usage": ("Agent Usage", AGENT_USAGE_THEME),
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
    """Shared page loop: engine on a worker thread, spinner, then the result.

    The engines' LLM calls can take seconds, so the page keeps animating a
    spinner (fed by the engine's on_progress strings) while a daemon thread
    runs the pipeline. The engines never raise (parse → fallback → format);
    ``make_failure_artifact`` is belt-and-braces for a bug.

    On the result screen ←/→ move between Export / Copy / Re-run / Back and
    enter activates; `r` still re-runs and esc/q still backs out (a mid-run
    back-out lets the daemon finish + export). Export writes the Markdown
    artifact, Copy puts the same Markdown on the clipboard — both report back
    through the page's notice line rather than a popup, because neither can
    fail in a way the user must acknowledge.
    """
    import queue

    from yeaboi.ui.shared._music_bar import duck_working_thread

    logger.info("%s page opened", label)
    while True:  # re-entered by the r (re-run) key
        progress: queue.Queue[str] = queue.Queue()
        result: queue.Queue = queue.Queue(maxsize=1)

        def _work() -> None:
            try:
                result.put(run_engine(progress.put))
            except Exception as exc:  # noqa: BLE001 — belt and braces; the engine shouldn't raise
                logger.exception("%s engine failed", label)
                result.put(make_failure_artifact(exc))

        # duck_working_thread: the corner robo bobs for the engine's lifetime,
        # same liveness cue the Humans pages give their worker runs.
        worker = duck_working_thread(_work, name=label)
        worker.start()

        status = ""
        start = time.monotonic()
        artifact = None
        while artifact is None:
            try:
                status = progress.get_nowait()
            except queue.Empty:
                pass
            try:
                artifact = result.get_nowait()
            except queue.Empty:
                pass
            w, h = console.size
            live.update(build_screen(None, width=w, height=h, shimmer_tick=time.monotonic() - start, status=status))
            key = read_key(timeout=frame_time) if supports_timeout else read_key()
            if key in ("esc", "q"):
                logger.info("%s page: backed out while running", label)
                return

        logger.info("%s page: artifact ready", label)
        action_sel = 0
        notice = ""
        while True:
            w, h = console.size
            live.update(
                build_screen(
                    artifact,
                    width=w,
                    height=h,
                    shimmer_tick=time.monotonic() - start,
                    action_sel=action_sel,
                    notice=notice,
                )
            )
            key = read_key(timeout=frame_time) if supports_timeout else read_key()
            if key in ("esc", "q"):
                return
            if key == "r":
                logger.info("%s page: re-run requested", label)
                break  # back to the outer loop → fresh engine run
            if key == "left":
                action_sel = (action_sel - 1) % len(AGENT_RESULT_ACTIONS)
            elif key == "right":
                action_sel = (action_sel + 1) % len(AGENT_RESULT_ACTIONS)
            elif key == "enter":
                action = AGENT_RESULT_ACTIONS[action_sel]
                logger.info("%s page: action %s", label, action)
                if action == "Back":
                    return
                if action == "Re-run":
                    break  # same exit as `r`: falls out to the outer loop
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
