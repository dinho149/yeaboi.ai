"""Routing and pages for the Agents family (agentwatch) in the TUI.

One dispatch entry (:func:`route_agent_mode`) instead of four more branches in
``select_mode``'s routing chain. Each mode wraps itself in ``mode_log`` (its own
log file under ~/.yeaboi/logs/agentwatch/) and its one-time beta notice.

All three pages share one threaded-engine loop: the page opens instantly on
the last saved report (age-stamped) and re-runs the pipeline on a daemon
thread only when that report is stale (``config.get_agentwatch_fresh_minutes``)
— a phase checklist / refresh banner shows while it runs, then the capped
fresh result swaps in (r re-runs behind the visible report, esc backs out).

Imports from :mod:`yeaboi.ui.mode_select` happen lazily inside function bodies —
the package imports this module's callers, so a top-level import is a cycle.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from rich.console import Console

from yeaboi.agentwatch import setup as agents_setup
from yeaboi.logging_setup import mode_log
from yeaboi.ui.mode_select.screens._screens_agents import (
    _ALWAYS_EXPANDED,
    AGENT_RESULT_ACTIONS,
    ISSUE_ACTIONS,
    visible_issues,
)
from yeaboi.ui.shared._beta_notice import show_beta_notice

logger = logging.getLogger(__name__)


def route_agent_mode(
    key: str,
    *,
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    project_path: str = "",
) -> None:
    """Enter one Agents mode from the menu; returns when the user backs out.

    ``project_path`` is the active project's repository: the scoped modes
    read only the sessions under it, security stays machine-wide.
    """
    mode = agents_setup.lookup(key)
    if mode is None:
        logger.warning("agents: no such mode %r", key)
        return
    scoped_to = project_path if mode.scoped else ""
    with mode_log("agentwatch"):
        logger.info("%s opened (repo=%s)", mode.label, scoped_to or "-")
        if not show_beta_notice(live, console, read_key, frame_time, supports_timeout, mode_key=key):
            logger.info("%s beta notice declined — back to menu", mode.label)
            return
        _run_agent_page(mode, console, live, read_key, frame_time, supports_timeout, project_path=scoped_to)
        logger.info("%s closed", mode.label)


def _run_result_action(action: str, artifact, *, mode: agents_setup.AgentMode) -> str:
    """Run Export or Copy for a finished artifact; return the notice to show.

    Never raises: a failed write or an absent clipboard tool becomes a notice,
    because losing the whole result screen over a failed copy would be a far
    worse outcome than the copy not happening.
    """
    from yeaboi.agentwatch.export import export_artifact
    from yeaboi.clipboard import copy_text

    if action == "Export":
        try:
            written = export_artifact(artifact, kind=mode.kind)
        except Exception as exc:  # noqa: BLE001 — a failed export must not close the page
            logger.warning("%s page: export failed: %s", mode.key, exc)
            return "Couldn't write the export — see logs."
        path = next(iter(written.values()), None)
        logger.info("%s page: exported to %s", mode.key, path)
        return f"Exported to {path}" if path else "Nothing to export."

    if action == "Copy":
        try:
            markdown = agents_setup.markdown(mode, artifact)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s page: could not build markdown to copy: %s", mode.key, exc)
            return "Couldn't copy — see logs."
        ok = copy_text(markdown)
        logger.info("%s page: copy %s", mode.key, "succeeded" if ok else "failed")
        return "Copied the report to the clipboard." if ok else "Couldn't reach the clipboard."

    return ""


#: The screen builder for each mode — the only per-mode thing the shared page
#: loop needs that ``agentwatch.setup``'s table does not carry.
_SCREENS: dict[str, tuple[str, str]] = {
    "agent-usage": ("_screens_agents", "_build_agent_usage_screen"),
    "agent-advisor": ("_screens_agents", "_build_agent_advisor_screen"),
    "agent-security": ("_screens_agents", "_build_agent_security_screen"),
}


def _screen_builder(mode: agents_setup.AgentMode):
    from importlib import import_module

    module, attribute = _SCREENS[mode.key]
    return getattr(import_module(f"yeaboi.ui.mode_select.screens.{module}"), attribute)


def _run_agent_page(
    mode: agents_setup.AgentMode,
    console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    project_path: str = "",
) -> None:
    """Shared page loop: engine on a worker thread, instant last report, result.

    A scoped page (``project_path`` set) skips the instant open: saved reports
    are machine-wide, so the last one would be the wrong report under a
    project's name. The repository shows in the subtitle instead.

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
    `w` cycles the window (7 / 30 / 90 days) on the windowed modes and re-runs;
    on Security `i` toggles the informational findings (a rebuild, not a
    scan), ↑↓ move over the issues, `enter` opens one (why, fixes, replay),
    `f` applies its first fix, `x` marks it test data and `d` dismisses it
    after asking for a reason. Export writes the Markdown
    artifact, Copy puts the same Markdown on the clipboard — both report back
    through the page's notice line rather than a popup, because neither can
    fail in a way the user must acknowledge.
    """
    import queue

    from yeaboi.analysis.progress import is_component_progress
    from yeaboi.ui.mode_select import _settings_edit_keypress
    from yeaboi.ui.shared._music_bar import duck_working_thread

    label = mode.key
    build_screen = _screen_builder(mode)
    logger.info("%s page opened", label)
    artifact = None
    as_of = ""
    options: dict = {}
    if mode.kind in ("usage", "advisor"):
        options["window_days"] = 30
    if mode.kind == "security":
        options["include_info"] = False
    finding_sel = 0
    dismiss_edit: dict | None = None
    expanded: tuple[str, ...] = _ALWAYS_EXPANDED
    issue_view: dict | None = None  # the open issue screen's state; None on the list
    loaded = None if project_path else agents_setup.latest_artifact(mode.kind)
    if loaded is not None:
        artifact, as_of = loaded
        logger.info("%s page: showing last saved report (from %s)", label, as_of or "unknown time")

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
                rq.put(("ok", agents_setup.run(mode, pq.put, project_path=project_path, options=dict(options))))
            except Exception as exc:  # noqa: BLE001 — belt and braces; the engine shouldn't raise
                logger.exception("%s engine failed", label)
                rq.put(("err", exc))

        progress_q, result_q = pq, rq
        events_by_id = {}
        status = ""
        refreshing = artifact is not None
        # duck_working_thread: the corner robo bobs for the engine's lifetime,
        # same liveness cue the Team pages give their worker runs.
        duck_working_thread(_work, name=label).start()

    if artifact is None or not agents_setup.is_fresh(as_of):
        _start_worker()
    else:
        logger.info("%s page: saved report is fresh — not re-running", label)
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

    def _cycle_window() -> None:
        nonlocal notice
        if "window_days" not in options:
            return
        if refreshing:
            notice = "Already refreshing…"
            return
        steps = (7, 30, 90)
        current = options["window_days"]
        options["window_days"] = steps[(steps.index(current) + 1) % len(steps)] if current in steps else 30
        logger.info("%s page: window → %d days", label, options["window_days"])
        notice = f"Window: {options['window_days']} days"
        _handle_rerun()

    def _rebuild() -> None:
        """Re-derive the security report from what is stored — no scan, no LLM."""
        nonlocal artifact, as_of
        from yeaboi.agentwatch.engine import rebuild_security_report

        try:
            artifact = rebuild_security_report(include_info=bool(options.get("include_info")))
            as_of = ""
        except Exception as exc:  # noqa: BLE001 — keep the report on screen
            logger.warning("%s page: rebuild failed: %s", label, exc)

    def _toggle_info() -> None:
        nonlocal notice
        if "include_info" not in options:
            return
        options["include_info"] = not options["include_info"]
        notice = "Showing informational findings" if options["include_info"] else "Hiding informational findings"
        _rebuild()

    def _focused_issue():
        rows = visible_issues(artifact, expanded) if getattr(artifact, "issues", None) is not None else []
        if not rows:
            return None
        return rows[min(finding_sel, len(rows) - 1)]

    def _current_issue():
        if issue_view is not None:
            for issue in getattr(artifact, "issues", ()):
                if issue.id == issue_view["id"]:
                    return issue
            return None
        return _focused_issue()

    def _dismiss(reason: str) -> str:
        from yeaboi.agentwatch import dismissals

        issue = _current_issue()
        if issue is None:
            return "Nothing to dismiss."
        try:
            for key in issue.finding_keys:
                dismissals.dismiss(key, reason=reason)
        except ValueError as exc:
            return str(exc)
        logger.info("%s page: dismissed %s (%d finding(s))", label, issue.id, len(issue.finding_keys))
        _rebuild()
        return f"Dismissed {issue.title} — {reason}"

    def _mark_test_data() -> str:
        from yeaboi.agentwatch import dismissals

        issue = _current_issue()
        if issue is None:
            return "Nothing to mark."
        for key in issue.finding_keys:
            dismissals.dismiss(key, reason="test data: fixture or example text")
        logger.info("%s page: %s marked as test data", label, issue.id)
        _rebuild()
        return f"Marked as test data. {issue.title} now reads as handled."

    def _apply_fix(fix) -> str:
        from yeaboi.agentwatch import security_fixes
        from yeaboi.ui.shared._consent import _preflight_path_consent

        issue = _current_issue()
        if issue is None or not fix:
            return "Nothing to apply."
        if fix.id == "dismiss":
            return "Press d to dismiss with a reason."
        if fix.kind in ("write", "pr") and fix.target:
            # ~/.claude and a repository are outside the sandbox until granted;
            # ask here, the way Ship does, so the fix can succeed on the retry.
            target = Path(fix.target).expanduser()
            if not _preflight_path_consent(
                console,
                live,
                read_key,
                frame_time,
                supports_timeout,
                target if fix.kind == "pr" else target.parent,
                mode="write",
                context=fix.detail or fix.label,
            ):
                return "Not applied — the path was not granted."
        try:
            outcome = security_fixes.apply_fix(issue.finding_keys[0], fix.id, keys=issue.finding_keys)
        except Exception as exc:  # noqa: BLE001 — a failed fix is a notice, never a dead page
            logger.exception("%s page: fix %s failed", label, fix.id)
            return f"Couldn't apply: {exc}"
        logger.info("%s page: fix %s on %s → %s", label, fix.id, issue.id, "ok" if outcome.ok else outcome.detail)
        if outcome.ok and fix.id not in ("rotate", "manual"):
            _rebuild()
        if fix.id == "rotate":
            return f"Open {fix.target} to rotate the key, then mark it rotated."
        return outcome.detail if outcome.ok else f"Couldn't apply: {outcome.detail}"

    def _load_replay(issue, index: int) -> None:
        from yeaboi.agentwatch import replay as replay_mod

        assert issue_view is not None
        issue_view.update({"replay": None, "replay_status": "Loading the replay…", "signal": index, "scroll": 0})
        finding = next((f for f in artifact.findings if f.key == issue.finding_keys[index]), None)
        if finding is None or finding.category not in ("secret", "risky_tool"):
            issue_view["replay_status"] = "No transcript behind this finding — it is a fact about a config file."
            return
        try:
            issue_view["replay"] = replay_mod.replay(finding.location, finding.line_no, pattern=finding.pattern)
            issue_view["replay_status"] = ""
        except replay_mod.ReplayError as exc:
            issue_view["replay_status"] = f"No replay: {exc}"

    def _open_issue() -> None:
        nonlocal issue_view, notice
        issue = _focused_issue()
        if issue is None:
            notice = "Nothing to open."
            return
        issue_view = {"id": issue.id, "fix_sel": 0, "action_sel": 0, "scroll": 0, "confirm": "", "meta": {}}
        logger.info("%s page: opened %s", label, issue.id)
        _load_replay(issue, 0)

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
                artifact = agents_setup.failure_artifact(mode, payload)
                refreshing = False

        w, h = console.size
        tick = time.monotonic() - start
        if issue_view is not None and artifact is not None:
            issue = _current_issue()
            if issue is None:
                issue_view = None
                continue
            from yeaboi.ui.mode_select.screens._screens_agents import _build_agent_security_issue_screen

            live.update(
                _build_agent_security_issue_screen(
                    artifact,
                    issue,
                    width=w,
                    height=h,
                    shimmer_tick=tick,
                    fix_sel=issue_view["fix_sel"],
                    action_sel=issue_view["action_sel"],
                    notice=notice,
                    replay=issue_view.get("replay"),
                    replay_status=issue_view.get("replay_status", ""),
                    signal_index=issue_view.get("signal", 0),
                    scroll=issue_view["scroll"],
                    confirm=issue_view["confirm"],
                    dismiss_edit=dismiss_edit["buf"] if dismiss_edit is not None else None,
                    scroll_meta=issue_view["meta"],
                )
            )
            key = read_key(timeout=frame_time) if supports_timeout else read_key()
            if dismiss_edit is not None:
                if key == "enter":
                    notice = _dismiss(dismiss_edit["buf"].strip())
                    dismiss_edit = None
                elif key == "esc":
                    dismiss_edit = None
                elif isinstance(key, str) and key:
                    _settings_edit_keypress(key, dismiss_edit)
                continue
            fixes = list(issue.fixes)
            fix = fixes[min(issue_view["fix_sel"], len(fixes) - 1)] if fixes else None
            if issue_view["confirm"]:
                if key == "enter":
                    issue_view["confirm"] = ""
                    notice = _apply_fix(fix)
                elif key == "esc":
                    issue_view["confirm"] = ""
                continue
            if key in ("esc", "q"):
                issue_view = None
                notice = ""
            elif key == "up":
                issue_view["fix_sel"] = max(0, issue_view["fix_sel"] - 1)
            elif key == "down":
                issue_view["fix_sel"] = min(max(0, len(fixes) - 1), issue_view["fix_sel"] + 1)
            elif key in ("j", "pgdn"):
                issue_view["scroll"] = min(issue_view["meta"].get("max", 0), issue_view["scroll"] + 3)
            elif key in ("k", "pgup"):
                issue_view["scroll"] = max(0, issue_view["scroll"] - 3)
            elif key == "n" and len(issue.finding_keys) > 1:
                _load_replay(issue, (issue_view.get("signal", 0) + 1) % len(issue.finding_keys))
            elif key == "p" and len(issue.finding_keys) > 1:
                _load_replay(issue, (issue_view.get("signal", 0) - 1) % len(issue.finding_keys))
            elif key == "x":
                notice = _mark_test_data()
            elif key == "d":
                dismiss_edit = {"buf": "", "cur": 0}
            elif key == "left":
                issue_view["action_sel"] = (issue_view["action_sel"] - 1) % len(ISSUE_ACTIONS)
            elif key == "right":
                issue_view["action_sel"] = (issue_view["action_sel"] + 1) % len(ISSUE_ACTIONS)
            elif key in ("enter", "f"):
                if key == "enter" and ISSUE_ACTIONS[issue_view["action_sel"]] == "Back":
                    issue_view = None
                    notice = ""
                elif fix is None:
                    notice = "Nothing to apply."
                elif fix.kind in ("write", "pr"):
                    issue_view["confirm"] = fix.label
                else:
                    notice = _apply_fix(fix)
            continue
        if artifact is None:
            live.update(
                build_screen(
                    None,
                    width=w,
                    height=h,
                    shimmer_tick=tick,
                    status=status,
                    progress=list(events_by_id.values()),
                    scope=project_path,
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
                    scope=project_path,
                    options=options,
                    finding_sel=finding_sel,
                    dismiss_edit=dismiss_edit["buf"] if dismiss_edit is not None else None,
                    **({"expanded": expanded} if mode.kind == "security" else {}),
                )
            )
        key = read_key(timeout=frame_time) if supports_timeout else read_key()
        if dismiss_edit is not None:
            if key == "enter":
                notice = _dismiss(dismiss_edit["buf"].strip())
                dismiss_edit = None
            elif key == "esc":
                dismiss_edit = None
            elif isinstance(key, str) and key:
                _settings_edit_keypress(key, dismiss_edit)
            continue
        if key in ("esc", "q"):
            if artifact is None or refreshing:
                logger.info("%s page: backed out while running", label)
            return
        if artifact is None:
            continue  # still loading: only esc/q act
        if key == "r":
            _handle_rerun()
        elif key == "w":
            _cycle_window()
        elif key == "i":
            _toggle_info()
        elif key == "d" and mode.kind == "security":
            if _focused_issue() is not None:
                dismiss_edit = {"buf": "", "cur": 0}
            else:
                notice = "Nothing to dismiss."
        elif key == "x" and mode.kind == "security":
            notice = _mark_test_data()
        elif key == "f" and mode.kind == "security":
            issue = _focused_issue()
            fix = issue.fixes[0] if issue is not None and issue.fixes else None
            if fix is not None and fix.kind in ("write", "pr"):
                _open_issue()
                if issue_view is not None:
                    issue_view["confirm"] = fix.label
            else:
                notice = _apply_fix(fix)
        elif key == "t" and mode.kind == "security":
            expanded = (
                _ALWAYS_EXPANDED
                if len(expanded) > len(_ALWAYS_EXPANDED)
                else (*_ALWAYS_EXPANDED, "test-data", "handled", "info")
            )
            finding_sel = 0
        elif key == "up" and mode.kind == "security":
            finding_sel = max(0, finding_sel - 1)
        elif key == "down" and mode.kind == "security":
            finding_sel = min(max(0, len(visible_issues(artifact, expanded)) - 1), finding_sel + 1)
        elif key == "left":
            action_sel = (action_sel - 1) % len(AGENT_RESULT_ACTIONS)
        elif key == "right":
            action_sel = (action_sel + 1) % len(AGENT_RESULT_ACTIONS)
        elif key == "enter" and mode.kind == "security" and action_sel == 0 and _focused_issue() is not None:
            # Enter on the list opens the focused issue; the buttons still answer
            # once the selection has moved off the first one.
            _open_issue()
        elif key == "enter":
            action = AGENT_RESULT_ACTIONS[action_sel]
            logger.info("%s page: action %s", label, action)
            if action == "Back":
                return
            if action == "Re-run":
                _handle_rerun()
            else:
                notice = _run_result_action(action, artifact, mode=mode)
