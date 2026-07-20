"""Pipeline and chat phase orchestration for the TUI session.

# See docs: "Architecture" — phase functions drive the interactive flow.
# Contains the pipeline stage loop (Phase D) and post-pipeline chat (Phase E),
# plus helper functions for story selection and pipeline choice screens.
#
# Intake phases are in _phases_intake.py, review phases in _phases_review.py.
"""

from __future__ import annotations

import logging
import threading
import time

from langchain_core.messages import AIMessage, HumanMessage
from rich.console import Console
from rich.live import Live

from yeaboi.agent.state import ReviewDecision
from yeaboi.repl._review import (
    _clear_downstream_artifacts,
    _serialize_artifacts_for_review,
)
from yeaboi.repl._ui import _PIPELINE_STEPS, _SPINNER_MESSAGES, _predict_next_node
from yeaboi.ui.session._renderers import _render_pipeline_artifacts
from yeaboi.ui.session._utils import _invoke_graph_thread, _invoke_with_animation

# Re-export intake and review phase functions for backward compatibility.
# The __init__.py and other callers import these from _phases.
from yeaboi.ui.session.phases._phases_intake import (  # noqa: F401
    _phase_description_input,
    _phase_intake_questions,
    _question_input_loop,
)
from yeaboi.ui.session.phases._phases_review import (  # noqa: F401
    _edit_accordion_browse,
    _get_edit_input,
    _phase_intake_review,
)
from yeaboi.ui.session.screens._screens_pipeline import _build_chat_screen, _build_pipeline_screen
from yeaboi.ui.shared._animations import FRAME_TIME_30FPS
from yeaboi.ui.shared._scroll import SCROLL_KEYS, coalesce_scroll

logger = logging.getLogger(__name__)

# Sentinel scroll offset meaning "pin to the last line". Any screen builder
# clamps it down to its real maximum for display and publishes that maximum via
# scroll_meta, which the loop then adopts. Larger than any realistic line count.
_SCROLL_BOTTOM = 1_000_000_000


def _plan_slug(graph_state: dict) -> str:
    """Filesystem-safe slug for the plan's project name (same rules as persistence)."""
    name = getattr(graph_state.get("project_analysis"), "project_name", "") or "project"
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in name.lower()).strip("-") or "project"


def _plan_export_flow(live, console, key_fn, graph_state: dict, stage: str) -> None:
    """Export the plan via the shared destination picker (files / Notion / Confluence).

    Files land in the planning export dir (~/.yeaboi/exports/planning/<project>/,
    honouring the YEABOI_HOME data-dir override) — unified with the other modes
    instead of the old scrum-plan.* in the current working directory. Blocks on
    the success screen (min 1 s + a key press); returns straight away on Back/Esc.
    """
    from yeaboi.ui.shared._export_picker import pick_export_destination

    def _open_setup():
        # Same suspend-wizard-resume dance as Settings → Configure.
        from yeaboi.ui.mode_select import _launch_setup_wizard

        _launch_setup_wizard(console, live)

    dest = pick_export_destination(live, console, key_fn, 0.05, True, mode="planning", open_setup=_open_setup)
    if dest is None:
        return
    from yeaboi.ui.mode_select.screens._screens_secondary import _build_project_export_success_screen

    if dest == "files":
        from yeaboi.html_exporter import export_plan_html
        from yeaboi.paths import get_planning_export_dir
        from yeaboi.repl._io import _export_plan_markdown

        out_dir = get_planning_export_dir(_plan_slug(graph_state))
        html_path = export_plan_html(graph_state, stage=stage, path=out_dir / "scrum-plan.html")
        md_path = _export_plan_markdown(graph_state, path=out_dir / "scrum-plan.md")
        logger.info("Exported: HTML=%s, MD=%s", html_path, md_path)
        body = f"HTML  {html_path}\nMD    {md_path}"
        subtitle = "Exported (HTML + MD)"
    elif dest == "copy":
        from yeaboi.clipboard import copy_markdown_status
        from yeaboi.repl._io import build_plan_markdown

        subtitle = copy_markdown_status(build_plan_markdown(graph_state))
        body = "Sprint plan Markdown copied — paste it anywhere."
    else:
        from yeaboi.export_targets import publish_markdown
        from yeaboi.repl._io import build_plan_markdown

        name = getattr(graph_state.get("project_analysis"), "project_name", "")
        title = f"Sprint Plan — {name}" if name else "Sprint Plan"
        result = publish_markdown(dest, title=title, markdown=build_plan_markdown(graph_state))
        body = result.url or result.message
        subtitle = result.message if result.ok else f"Export failed — {result.message}"

    w, h = console.size
    live.update(_build_project_export_success_screen(body, width=w, height=h, subtitle=subtitle, mode="planning"))
    t0 = time.monotonic()
    while True:
        try:
            ek = key_fn(timeout=0.05)
        except TypeError:
            ek = key_fn()
        if time.monotonic() - t0 > 1.0 and ek:
            break


# ---------------------------------------------------------------------------
# Story-level auto-scroll helper
# ---------------------------------------------------------------------------


def _find_story_panel_ranges(content_lines: list[str]) -> list[tuple[int, int]]:
    """Find (start_line, end_line) ranges for each story panel in content_lines.

    Scans for Rich Panel border characters (╭ = top, ╰ = bottom) to identify
    panel boundaries. Returns a list of (start, end) pairs — one per story panel.
    """
    import re

    # Find panel top-border lines (╭) and bottom-border lines (╰)
    panel_starts: list[int] = []
    panel_ends: list[int] = []
    for i, line in enumerate(content_lines):
        plain = re.sub(r"\x1b\[[0-9;]*m", "", line)
        if "\u256d" in plain:
            panel_starts.append(i)
        elif "\u2570" in plain:
            panel_ends.append(i)

    # Pair starts with ends
    ranges: list[tuple[int, int]] = []
    for j, start in enumerate(panel_starts):
        end = panel_ends[j] if j < len(panel_ends) else len(content_lines) - 1
        ranges.append((start, end))
    return ranges


def _story_index_at_scroll(
    panel_ranges: list[tuple[int, int]],
    scroll_offset: int,
    viewport_h: int,
) -> int:
    """Determine which story is most visible in the viewport.

    Returns the index of the story panel that overlaps most with the
    viewport center. Falls back to 0 if no panels exist.
    """
    if not panel_ranges:
        return 0

    viewport_center = scroll_offset + viewport_h // 2
    best_idx = 0
    best_dist = float("inf")

    for idx, (start, end) in enumerate(panel_ranges):
        panel_center = (start + end) // 2
        dist = abs(panel_center - viewport_center)
        if dist < best_dist:
            best_dist = dist
            best_idx = idx

    return best_idx


# ---------------------------------------------------------------------------
# Pipeline choice screen (sprint selector, capacity warning)
# ---------------------------------------------------------------------------


def _pipeline_choice_screen(
    live: Live,
    console: Console,
    _key,
    *,
    title: str,
    subtitle: str,
    options: list[str],
    step: int,
    total: int,
    stage_label: str,
    progress: str,
) -> int | None:
    """Show a simple choice picker during the pipeline flow.

    # See docs: "Architecture" — intermediate pipeline questions.
    # Used for sprint selector and capacity warning intercepts.

    Returns the 0-based index of the selected option, or None on Esc.
    """
    from yeaboi.ui.shared._components import PAD

    selected = 0
    num_opts = len(options)

    # ANSI escape helpers for styled content lines.
    # _build_pipeline_screen renders content_lines via Text.from_ansi(),
    # so we embed ANSI codes directly for colour/weight.
    _bold = "\033[1m"
    _dim = "\033[2m"
    _rst = "\033[0m"
    _amber = "\033[38;2;200;160;60m"
    _white = "\033[97m"
    _accent = "\033[38;2;70;100;180m"
    _ch_anim0 = time.monotonic()  # shimmer title clock

    def _render_choices():
        w, _ = console.size
        wrap_w = max(40, w - 16)
        lines: list[str] = []
        lines.append("")
        # Title — bold white with warning icon
        lines.append(f"{PAD}{_bold}{_white}\u26a0  {title}{_rst}")
        lines.append("")
        # Subtitle (warning text) — amber, word-wrapped
        if subtitle:
            import textwrap

            for paragraph in subtitle.split("\n"):
                stripped = paragraph.strip()
                if stripped:
                    for wrapped in textwrap.wrap(stripped, wrap_w):
                        lines.append(f"{PAD}{_amber}{wrapped}{_rst}")
            lines.append("")
        # Divider
        lines.append(f"{PAD}{_dim}{'─' * min(50, wrap_w)}{_rst}")
        lines.append("")
        # Options — selected gets bold accent + arrow, unselected gets dim
        for i, opt in enumerate(options):
            if i == selected:
                lines.append(f"{PAD}  {_bold}{_accent}\u203a {opt}{_rst}")
            else:
                lines.append(f"{PAD}    {_dim}{opt}{_rst}")
        lines.append("")
        w, h = console.size
        return _build_pipeline_screen(
            stage_label,
            progress,
            lines,
            0,
            selected,
            status="complete",
            width=w,
            height=h,
            step=step,
            total=total,
            actions=["Select"],
            shimmer_tick=time.monotonic() - _ch_anim0,
        )

    live.update(_render_choices())
    logger.info("Choice screen shown: title=%s, options=%s", title, options)

    while True:
        key = _key()
        if key == "esc":
            logger.info("Choice screen: Esc pressed — cancelling")
            return None
        elif key in ("up", "scroll_up"):
            selected = (selected - 1) % num_opts
        elif key in ("down", "scroll_down"):
            selected = (selected + 1) % num_opts
        elif key in ("enter", " "):
            logger.info("Choice screen: confirmed option %d (%s)", selected, options[selected])
            return selected
        elif key == "":
            pass
        else:
            continue
        live.update(_render_choices())


# ---------------------------------------------------------------------------
# Tracker detection helper
# ---------------------------------------------------------------------------


def _get_active_trackers() -> list[str]:
    """Detect which issue trackers are configured.

    Returns a list of configured trackers, e.g. ["jira"], ["azdevops"], ["jira", "azdevops"], or [].
    """
    from yeaboi.jira_sync import is_jira_configured

    trackers: list[str] = []
    if is_jira_configured():
        trackers.append("jira")

    from yeaboi.azdevops_sync import is_azdevops_board_configured

    if is_azdevops_board_configured():
        trackers.append("azdevops")

    return trackers


# ---------------------------------------------------------------------------
# Tracker sync helper (dispatches to Jira or Azure DevOps)
# ---------------------------------------------------------------------------


def _handle_tracker_sync(
    live: Live,
    console: Console,
    _key,
    graph_state: dict,
    stage: str,
    stage_label: str,
    progress: str,
    step: int,
    total: int,
    tracker: str = "jira",
) -> dict | None:
    """Run tracker sync for the current pipeline stage with progress animation.

    Shows a confirmation summary, then a progress screen during sync,
    then updates graph_state with the results.
    Returns updated graph_state on success, or None on cancel/failure.

    tracker: "jira" or "azdevops" — determines which sync module to use.
    # See docs: "Tools" — tool types, write tools, human-in-the-loop pattern
    """
    tracker_label = "Jira" if tracker == "jira" else "Azure DevOps"

    if tracker == "jira":
        from yeaboi.jira_sync import (
            sync_sprints_to_jira,
            sync_stories_to_jira,
            sync_tasks_to_jira,
        )

        story_key_field = "jira_story_keys"
        task_key_field = "jira_task_keys"
        sprint_key_field = "jira_sprint_keys"
        epic_key_field = "jira_epic_key"
    else:
        from yeaboi.azdevops_sync import (
            sync_iterations_to_azdevops,
            sync_stories_to_azdevops,
            sync_tasks_to_azdevops,
        )

        story_key_field = "azdevops_story_keys"
        task_key_field = "azdevops_task_keys"
        sprint_key_field = "azdevops_iteration_keys"
        epic_key_field = "azdevops_epic_id"

    # Pick the right sync function based on stage and tracker
    sync_fn = None
    if stage == "story_writer":
        sync_fn = sync_stories_to_jira if tracker == "jira" else sync_stories_to_azdevops
    elif stage == "task_decomposer":
        sync_fn = sync_tasks_to_jira if tracker == "jira" else sync_tasks_to_azdevops
    elif stage == "sprint_planner":
        sync_fn = sync_sprints_to_jira if tracker == "jira" else sync_iterations_to_azdevops
    elif stage != "epic_review":
        return None

    # Compute what will be created vs skipped for confirmation
    stories = graph_state.get("stories", [])
    tasks = graph_state.get("tasks", [])
    sprints = graph_state.get("sprints", [])
    existing_stories = len(graph_state.get(story_key_field, {}))
    existing_tasks = len(graph_state.get(task_key_field, {}))
    existing_sprints = len(graph_state.get(sprint_key_field, {}))
    has_epic = bool(graph_state.get(epic_key_field))

    # Build confirmation description
    parts: list[str] = []
    if stage == "epic_review":
        if has_epic:
            parts.append("Epic already exists — nothing to create")
        else:
            parts.append("1 Epic")
    elif stage == "story_writer":
        new_stories = len(stories) - existing_stories
        if not has_epic:
            parts.append("1 Epic")
        if new_stories > 0:
            parts.append(f"{new_stories} Stories")
        if existing_stories > 0:
            parts.append(f"({existing_stories} already exist)")
    elif stage == "task_decomposer":
        new_tasks = len(tasks) - existing_tasks
        if not existing_stories and stories:
            parts.append(f"{len(stories)} Stories (cascade)")
        if new_tasks > 0:
            task_label = "Sub-tasks" if tracker == "jira" else "Tasks"
            parts.append(f"{new_tasks} {task_label}")
        if existing_tasks > 0:
            parts.append(f"({existing_tasks} already exist)")
    elif stage == "sprint_planner":
        new_sprints = len(sprints) - existing_sprints
        sprint_label = "Sprints" if tracker == "jira" else "Iterations"
        if new_sprints > 0:
            parts.append(f"{new_sprints} {sprint_label}")
        if existing_sprints > 0:
            parts.append(f"({existing_sprints} already exist)")

    if not parts:
        return None  # Nothing to create

    # Show confirmation via choice screen
    desc = f"Create in {tracker_label}: " + ", ".join(parts)
    choice = _pipeline_choice_screen(
        live,
        console,
        _key,
        title=f"Create in {tracker_label}",
        subtitle=desc,
        options=["Confirm", "Cancel"],
        step=step,
        total=total,
        stage_label=stage_label,
        progress=progress,
    )
    if choice != 0:
        logger.info("Tracker sync cancelled: %s stage=%s", tracker_label, stage)
        return None  # User cancelled
    logger.info("Tracker sync started: %s stage=%s", tracker_label, stage)

    # ── Epic-only sync (single item, no sync_fn) ──────────────────
    if stage == "epic_review":
        _ep_analysis = graph_state.get("project_analysis")
        _ep_title = getattr(_ep_analysis, "project_name", "Project") if _ep_analysis else "Project"
        _ep_desc = getattr(_ep_analysis, "project_description", "") if _ep_analysis else ""
        _new_state = dict(graph_state)
        _ep_error: str | None = None
        _ep_status = ""

        # Show processing animation during the API call
        _ep_done = threading.Event()
        _ep_result_box: list = [None, None]  # [key, error]

        def _create_epic():
            if tracker == "jira":
                try:
                    from jira import JIRA

                    from yeaboi.config import (
                        get_jira_base_url,
                        get_jira_email,
                        get_jira_project_key,
                        get_jira_token,
                    )
                    from yeaboi.jira_sync import _discover_issue_types

                    _j = JIRA(get_jira_base_url(), basic_auth=(get_jira_email(), get_jira_token()))
                    _it = _discover_issue_types(_j, get_jira_project_key())
                    _fields = {
                        "project": {"key": get_jira_project_key()},
                        "summary": _ep_title,
                        "description": _ep_desc,
                        "issuetype": {"name": _it.get("epic", "Epic")},
                    }
                    _issue = _j.create_issue(fields=_fields)
                    _ep_result_box[0] = _issue.key
                except Exception as exc:
                    from yeaboi.ui.session._utils import _classify_api_error

                    _ep_result_box[1] = _classify_api_error(exc)
            else:
                try:
                    from azure.devops.v7_0.work_item_tracking.models import JsonPatchOperation

                    from yeaboi.azdevops_sync import _get_wit_client
                    from yeaboi.config import get_azure_devops_org_url, get_azure_devops_project

                    _wit = _get_wit_client(get_azure_devops_org_url())
                    _ops = [
                        JsonPatchOperation(op="add", path="/fields/System.Title", value=_ep_title),
                        JsonPatchOperation(op="add", path="/fields/System.Description", value=_ep_desc),
                    ]
                    _wi = _wit.create_work_item(_ops, get_azure_devops_project(), "Epic")
                    _ep_result_box[0] = str(_wi.id)
                except Exception as exc:
                    from yeaboi.ui.session._utils import _classify_api_error

                    _ep_result_box[1] = _classify_api_error(exc)
            _ep_done.set()

        _ep_thread = threading.Thread(target=_create_epic, daemon=True)
        _ep_thread.start()

        _ep_start = time.monotonic()
        while not _ep_done.is_set():
            tick = time.monotonic() - _ep_start
            w, h = console.size
            live.update(
                _build_pipeline_screen(
                    stage_label,
                    progress,
                    [f"  \033[33m▸\033[0m Creating {tracker_label} Epic..."],
                    0,
                    0,
                    status="processing",
                    width=w,
                    height=h,
                    tick=tick,
                    step=step,
                    total=total,
                    shimmer_tick=tick,
                )
            )
            time.sleep(FRAME_TIME_30FPS)
        _ep_thread.join()

        if _ep_result_box[1]:
            logger.error("Epic sync failed: %s", _ep_result_box[1])
            _ep_status = f"\u2717 {tracker_label} Epic failed: {_ep_result_box[1]}"
        elif _ep_result_box[0]:
            _ep_key = _ep_result_box[0]
            if tracker == "jira":
                _new_state["jira_epic_key"] = _ep_key
            else:
                _new_state["azdevops_epic_id"] = _ep_key
            logger.info("Created %s Epic: %s", tracker_label, _ep_key)
            _ep_status = f"\u2713 {tracker_label} Epic created: {_ep_key}"

        # Show result with status message — wait for user to dismiss
        w, h = console.size
        live.update(
            _build_pipeline_screen(
                stage_label,
                progress,
                [f"  {_ep_status}"],
                0,
                0,
                status="complete",
                width=w,
                height=h,
                step=step,
                total=total,
                actions=["OK"],
                status_msg=_ep_status,
            )
        )
        while True:
            try:
                _ek = _key(timeout=0.05)
            except TypeError:
                _ek = _key()
            if _ek in ("enter", " ", "esc"):
                break

        if _ep_result_box[1]:
            return None
        return _new_state

    # ── Multi-item sync (stories/tasks/sprints) ─────────────────
    # Run sync in a background thread with progress updates
    _sync_log: list[str] = []
    _sync_current: list[str] = ["Starting..."]
    _sync_counter: list[int] = [0, 0]  # [current, total]
    result_box: list = [None]
    state_box: list[dict | None] = [None]
    error_box: list[Exception | None] = [None]
    done = threading.Event()

    def _on_progress(current: int, total_items: int, desc: str) -> None:
        _sync_counter[0] = current
        _sync_counter[1] = total_items
        # Move previous current item to log
        if _sync_current[0] and _sync_current[0] != "Starting...":
            _sync_log.append(f"  \033[32m✓\033[0m {_sync_current[0]}")
        _sync_current[0] = desc

    def _run_sync() -> None:
        try:
            sync_result, new_state = sync_fn(graph_state, on_progress=_on_progress)
            result_box[0] = sync_result
            state_box[0] = new_state
        except Exception as e:
            error_box[0] = e
        finally:
            done.set()

    thread = threading.Thread(target=_run_sync, daemon=True)
    thread.start()

    # Animate progress while sync runs — show scrolling log of completed items
    start = time.monotonic()
    while not done.is_set():
        tick = time.monotonic() - start
        w, h = console.size
        viewport_h = max(3, h - 16)
        visible_log = _sync_log[-viewport_h:] if _sync_log else []
        cur = _sync_counter[0]
        tot = _sync_counter[1]
        counter = f"[{cur}/{tot}]" if tot else ""
        active_line = f"  \033[33m▸\033[0m {counter} {_sync_current[0]}"
        status_lines = [*visible_log, active_line]
        live.update(
            _build_pipeline_screen(
                stage_label,
                progress,
                status_lines,
                0,
                0,
                status="processing",
                width=w,
                height=h,
                tick=tick,
                step=step,
                total=total,
            )
        )
        time.sleep(FRAME_TIME_30FPS)

    thread.join()

    if error_box[0] is not None:
        logger.error("%s sync failed: %s", tracker_label, error_box[0])
        return None

    if state_box[0] is not None:
        sync_result = result_box[0]
        if sync_result and sync_result.errors:
            logger.warning("%s sync completed with %d errors", tracker_label, len(sync_result.errors))
        else:
            logger.info("Tracker sync completed: %s stage=%s", tracker_label, stage)
        return state_box[0]

    return None


# ---------------------------------------------------------------------------
# Phase D: Pipeline Stages
# ---------------------------------------------------------------------------


def _phase_pipeline(
    live: Live,
    console: Console,
    graph,
    graph_state: dict,
    _key,
    export_only: bool,
    bell: bool,
    project_id: str = "",
    dry_run: bool = False,
) -> dict | None:
    """Run 5 pipeline stages: analyzer -> features -> stories -> tasks -> sprints.

    Each stage: invoke graph -> show result -> Accept/Edit/Export review.
    Returns updated graph_state after all stages, or None on cancel.

    When dry_run=True, skips graph invocations and progressively loads
    pre-saved artifacts with fake animation delays (1.5-3s per stage).
    """
    logger.info("_phase_pipeline started: export_only=%s dry_run=%s", export_only, dry_run)
    from yeaboi.persistence import save_project_snapshot

    # Pre-load the complete state for dry-run playback.
    dry_run_full_state = None
    if dry_run:
        from yeaboi.ui.session._dry_run import build_stage_snapshot, load_dry_run_state

        dry_run_full_state = load_dry_run_state()
        if dry_run_full_state is None:
            return graph_state

    while True:
        # If a previous session left pending_review set (user exited mid-review),
        # skip the LLM call and jump straight to the review screen.
        pending = graph_state.get("pending_review")
        if pending and pending in _PIPELINE_STEPS:
            next_node = pending
        elif dry_run:
            # In dry-run, walk through pipeline stages sequentially.
            # Determine next stage based on which artifacts are missing.
            if "project_analysis" not in graph_state:
                next_node = "project_analyzer"
            elif "features" not in graph_state:
                next_node = "feature_generator"
            elif "stories" not in graph_state:
                next_node = "story_writer"
            elif "tasks" not in graph_state:
                next_node = "task_decomposer"
            elif "sprints" not in graph_state:
                next_node = "sprint_planner"
            else:
                return graph_state  # all stages done
        else:
            next_node = _predict_next_node(graph_state)

        if next_node == "agent":
            # Pipeline complete
            return graph_state
        if next_node == "project_intake":
            # Still in intake — shouldn't happen but handle gracefully
            return graph_state

        logger.info("Pipeline stage entry: %s", next_node)
        # feature_skip occupies the same pipeline slot as feature_generator (step 2/5).
        step_node = "feature_generator" if next_node == "feature_skip" else next_node
        step = _PIPELINE_STEPS.index(step_node) + 1 if step_node in _PIPELINE_STEPS else 0
        total = len(_PIPELINE_STEPS)
        progress = f"[{step}/{total}]"
        stage_label = _SPINNER_MESSAGES.get(next_node, "Working")

        # ── Epic review intercept (before feature_generator invocation) ──
        if (
            next_node in ("feature_generator", "feature_skip")
            and not graph_state.get("_epic_reviewed", False)
            and not export_only
        ):
            _ep_analysis = graph_state.get("project_analysis")
            if _ep_analysis:
                graph_state["_epic_reviewed"] = True
                from yeaboi.ui.session._renderers import _render_tui_epic
                from yeaboi.ui.session._utils import _render_to_lines

                _rw = max(40, console.size[0] - 20)
                _ep_profile_id = graph_state.get("analysis_profile_id", "")
                _ep_examples = None
                _ep_profile = None

                # Try to load profile — from explicit selection or auto-detect
                try:
                    from yeaboi.agent.nodes import _load_profile_by_id, _load_team_examples, _load_team_profile

                    if _ep_profile_id:
                        _ep_profile, _ep_examples = _load_profile_by_id(_ep_profile_id)
                    else:
                        # Auto-detect from configured trackers (for resumed sessions)
                        _ep_profile = _load_team_profile()
                        _ep_examples = _load_team_examples()
                        if _ep_profile:
                            _ep_profile_id = getattr(_ep_profile, "team_id", "")
                            logger.info("Epic review: auto-detected profile %s", _ep_profile_id)
                except Exception:
                    pass

                # If analysis profile is active, reformat the epic using
                # team conventions (naming, template sections, sizing)
                logger.info(
                    "Epic review: profile=%s, has_examples=%s, dry_run=%s",
                    _ep_profile_id or "(none)",
                    bool(_ep_examples),
                    dry_run,
                )
                if _ep_profile and _ep_examples and not dry_run:
                    try:
                        from yeaboi.agent.nodes import _format_team_calibration

                        _cal_text = _format_team_calibration(_ep_profile, examples=_ep_examples)
                        if _cal_text:
                            # Show loading screen while LLM reformats
                            import threading

                            _epic_result = [None]

                            # Check if team uses quarter-scoped naming
                            _naming_info = _ep_examples.get("naming_conventions", {})
                            _epic_style = (
                                _naming_info.get("epic_naming_style", "") if isinstance(_naming_info, dict) else ""
                            )
                            _quarter_label = ""

                            if "quarter" in _epic_style.lower():
                                # Compute quarter/year from sprint dates
                                from datetime import datetime as _dt
                                from datetime import timedelta

                                _sprint_start = graph_state.get("sprint_start_date", "")
                                _target_sprints = getattr(_ep_analysis, "target_sprints", 0)
                                _sprint_weeks = getattr(_ep_analysis, "sprint_length_weeks", 2)
                                try:
                                    _start_dt = _dt.fromisoformat(_sprint_start) if _sprint_start else _dt.now()
                                except Exception:
                                    _start_dt = _dt.now()
                                _start_q = ((_start_dt.month - 1) // 3) + 1
                                _start_year = _start_dt.year
                                _end_dt = (
                                    _start_dt + timedelta(weeks=_target_sprints * _sprint_weeks)
                                    if _target_sprints
                                    else _start_dt
                                )
                                _end_q = ((_end_dt.month - 1) // 3) + 1
                                _end_year = _end_dt.year
                                if _start_q == _end_q and _start_year == _end_year:
                                    _quarter_label = f"Q{_start_q}|{_start_year}"
                                else:
                                    _quarter_label = f"Q{_start_q}|{_start_year}-Q{_end_q}|{_end_year}"

                            def _reformat_epic():
                                try:
                                    from yeaboi.tools.team_learning import _llm_invoke

                                    _proj_name = getattr(_ep_analysis, "project_name", "")
                                    _proj_desc = getattr(_ep_analysis, "project_description", "")
                                    _naming = _ep_examples.get("naming_conventions", {})
                                    _sections = (
                                        _naming.get("template_sections", []) if isinstance(_naming, dict) else []
                                    )
                                    _sec_names = [
                                        s[0] if isinstance(s, (list, tuple)) else str(s) for s in _sections[:5]
                                    ]

                                    _prompt = (
                                        f"Reformat this project epic to match the team's style.\n\n"
                                        f"Project: {_proj_name}\n"
                                        f"Description: {_proj_desc}\n\n"
                                    )
                                    if _quarter_label:
                                        _prompt += (
                                            f"IMPORTANT: The team uses quarter-scoped naming. "
                                            f"The correct quarter is: {_quarter_label}\n"
                                            f"Use this EXACT quarter/year in the title.\n\n"
                                        )
                                    _prompt += f"{_cal_text}\n\nRequirements:\n"
                                    if _quarter_label:
                                        _prompt += f"1. Use the team's naming convention with {_quarter_label}\n"
                                    else:
                                        _prompt += "1. Use the team's naming convention for the title\n"
                                    if _sec_names:
                                        _prompt += (
                                            f"2. Structure the description with these sections: "
                                            f"{', '.join(_sec_names)}\n"
                                        )
                                    _prompt += (
                                        "3. Keep the project scope — don't change what the epic is about\n"
                                        "4. Match the team's writing style and level of detail\n\n"
                                        "Return ONLY a JSON object:\n"
                                        '{"title": "...", "description": "...", "stories_estimate": N, '
                                        '"points_estimate": N, "rationale": "..."}'
                                    )
                                    resp = _llm_invoke(_prompt, temperature=0.2)
                                    import json
                                    import re

                                    text = resp.content if hasattr(resp, "content") else str(resp)
                                    text = text.strip()
                                    if text.startswith("```"):
                                        text = re.sub(r"^```\w*\n?", "", text)
                                        text = re.sub(r"\n?```$", "", text)
                                    _epic_result[0] = json.loads(text)
                                except Exception as exc:
                                    logger.warning("Epic reformat failed: %s", exc)

                            _t = threading.Thread(target=_reformat_epic, daemon=True)
                            _t.start()
                            _anim_start = time.monotonic()
                            while _t.is_alive():
                                _tick = time.monotonic() - _anim_start
                                w, h = console.size
                                live.update(
                                    _build_pipeline_screen(
                                        "Formatting epic",
                                        "[2/6]",
                                        [],
                                        0,
                                        0,
                                        status="processing",
                                        width=w,
                                        height=h,
                                        tick=_tick,
                                        step=1,
                                        total=6,
                                        shimmer_tick=_tick,
                                    )
                                )
                                time.sleep(1 / 30)
                            _t.join()

                            if _epic_result[0] and isinstance(_epic_result[0], dict):
                                _new_epic = _epic_result[0]
                                from dataclasses import fields as _dc_f

                                _pa_kw = {f.name: getattr(_ep_analysis, f.name) for f in _dc_f(_ep_analysis)}
                                _new_title = _new_epic.get("title", _pa_kw["project_name"])
                                _new_desc = _new_epic.get("description", _pa_kw["project_description"])
                                _pa_kw["project_name"] = _new_title
                                _pa_kw["project_description"] = _new_desc
                                graph_state["project_analysis"] = type(_ep_analysis)(**_pa_kw)
                                _ep_analysis = graph_state["project_analysis"]
                                logger.info("Epic reformatted to team style: %s", _new_title)
                    except Exception:
                        logger.debug("Epic reformat skipped", exc_info=True)

                _ep_renderable = _render_tui_epic(_ep_analysis, render_w=_rw, examples=_ep_examples)
                if _ep_profile_id:
                    from rich.console import Group as _EpGroup
                    from rich.text import Text as _EpText

                    from yeaboi.ui.session._renderers import _render_calibration_banner

                    _ep_banner = _render_calibration_banner(_ep_profile_id, _rw, stage="feature_generator")
                    if _ep_banner:
                        _ep_renderable = _EpGroup(_ep_banner, _EpText(""), _ep_renderable)

                _ep_lines = _render_to_lines(console, _ep_renderable, _rw)
                _ep_scroll, _ep_sel = 0, 0
                _ep_scroll_meta: dict = {}
                _ep_actions = ["Accept", "Edit", "Export"]

                # Add tracker sync buttons (dynamic based on configured boards)
                _ep_preferred = ""
                _qs = graph_state.get("questionnaire")
                if _qs:
                    _ep_preferred = getattr(_qs, "_preferred_tracker", "")
                try:
                    from yeaboi.jira_sync import is_jira_configured

                    _jira_ok = is_jira_configured()
                except Exception:
                    _jira_ok = False
                try:
                    from yeaboi.azdevops_sync import is_azdevops_board_configured

                    _azdo_ok = is_azdevops_board_configured()
                except Exception:
                    _azdo_ok = False
                if _ep_preferred == "jira" and _jira_ok:
                    _ep_actions.append("Jira")
                elif _ep_preferred == "azdevops" and _azdo_ok:
                    _ep_actions.append("Azure DevOps")
                elif _jira_ok:
                    _ep_actions.append("Jira")
                elif _azdo_ok:
                    _ep_actions.append("Azure DevOps")

                logger.info("Epic review: showing project-level epic")

                _epv_anim0 = time.monotonic()  # shimmer title clock
                while True:
                    w, h = console.size
                    live.update(
                        _build_pipeline_screen(
                            "Reviewing epic",
                            "[2/6]",
                            _ep_lines,
                            _ep_scroll,
                            _ep_sel,
                            status="complete",
                            width=w,
                            height=h,
                            actions=_ep_actions,
                            step=1,
                            total=6,
                            shimmer_tick=time.monotonic() - _epv_anim0,
                            scroll_meta=_ep_scroll_meta,
                        )
                    )
                    key = _key()
                    if key in ("esc", "q"):
                        logger.info("Epic review: user pressed Esc — exiting planning")
                        return graph_state
                    elif key in SCROLL_KEYS:
                        _ep_scroll = coalesce_scroll(_ep_scroll, key, _ep_scroll_meta, _key)
                    elif key == "left":
                        _ep_sel = max(0, _ep_sel - 1)
                    elif key == "right":
                        _ep_sel = min(len(_ep_actions) - 1, _ep_sel + 1)
                    elif key in ("enter", " "):
                        _ep_act = _ep_actions[_ep_sel]
                        if _ep_act == "Accept":
                            logger.info("Epic review: accepted")
                            break
                        elif _ep_act == "Edit":
                            logger.info("Epic review: editing")
                            from dataclasses import fields as _dc_fields

                            from yeaboi.agent.state import Feature, Priority
                            from yeaboi.ui.session.editor._editor_artifacts import (
                                _feature_editable_start,
                                _features_to_text,
                                _find_first_editable,
                                _parse_edited_features,
                            )
                            from yeaboi.ui.session.editor._editor_core import (
                                edit_buffer_loop,
                                render_editor_panel,
                            )

                            _ep_feat = Feature(
                                id="EPIC",
                                title=_ep_analysis.project_name,
                                description=_ep_analysis.project_description,
                                priority=Priority.HIGH,
                            )
                            _ep_buf = _features_to_text([_ep_feat]).split("\n")
                            _ep_cr, _ep_cc = _find_first_editable(_ep_buf, _feature_editable_start)
                            _ep_anim0 = time.monotonic()  # shimmer title clock

                            def _ep_render(buf, cr, cc, so, rw, rh):
                                return render_editor_panel(
                                    buf,
                                    cr,
                                    cc,
                                    so,
                                    width=rw,
                                    height=rh,
                                    editor_label="epic",
                                    shimmer_tick=time.monotonic() - _ep_anim0,
                                )

                            _ep_edited = edit_buffer_loop(
                                live,
                                console,
                                _ep_buf,
                                _ep_cr,
                                _ep_cc,
                                _key,
                                editable_start_fn=_feature_editable_start,
                                render_fn=_ep_render,
                            )
                            if _ep_edited is not None:
                                _ep_parsed = _parse_edited_features("\n".join(_ep_edited), [_ep_feat])
                                if _ep_parsed:
                                    _new = _ep_parsed[0]
                                    _pa_kw = {f.name: getattr(_ep_analysis, f.name) for f in _dc_fields(_ep_analysis)}
                                    _pa_kw["project_name"] = _new.title
                                    _pa_kw["project_description"] = _new.description
                                    graph_state["project_analysis"] = type(_ep_analysis)(**_pa_kw)
                                    _ep_analysis = graph_state["project_analysis"]
                                    _ep_renderable = _render_tui_epic(
                                        _ep_analysis,
                                        render_w=_rw,
                                        examples=_ep_examples,
                                    )
                                    if _ep_profile_id:
                                        _b = _render_calibration_banner(_ep_profile_id, _rw, stage="feature_generator")
                                        if _b:
                                            _ep_renderable = _EpGroup(_b, _EpText(""), _ep_renderable)
                                    _ep_lines = _render_to_lines(console, _ep_renderable, _rw)
                        elif _ep_act == "Export":
                            logger.info("Epic review: exporting")
                            _plan_export_flow(live, console, _key, graph_state, "project_analyzer")
                        elif _ep_act in ("Jira", "Azure DevOps"):
                            logger.info("Epic review: syncing to %s", _ep_act)
                            _btn_tracker = "jira" if _ep_act == "Jira" else "azdevops"
                            _sync_result = _handle_tracker_sync(
                                live,
                                console,
                                _key,
                                graph_state,
                                "epic_review",
                                "Reviewing epic",
                                "[2/6]",
                                1,
                                6,
                                tracker=_btn_tracker,
                            )
                            logger.info(
                                "Epic sync result: %s",
                                "success" if _sync_result is not None else "failed/cancelled",
                            )
                            if _sync_result is not None:
                                graph_state = _sync_result
                                if project_id:
                                    save_project_snapshot(project_id, graph_state)

        # Skip the LLM call if we already have artifacts awaiting review
        # (resumed session or re-entering the loop after an edit request).
        if not pending:
            if dry_run:
                # Fake animation delay (1.5-3s) with the pipeline processing screen
                import random

                delay = random.uniform(1.5, 3.0)  # noqa: S311 - UI animation jitter, not security
                anim_start = time.monotonic()
                while time.monotonic() - anim_start < delay:
                    tick = time.monotonic() - anim_start
                    w, h = console.size
                    live.update(
                        _build_pipeline_screen(
                            stage_label,
                            progress,
                            [],
                            0,
                            0,
                            status="processing",
                            width=w,
                            height=h,
                            tick=tick,
                            step=step,
                            total=total,
                            shimmer_tick=tick,
                        )
                    )
                    time.sleep(FRAME_TIME_30FPS)
                # Load the artifacts for this stage from the pre-saved state
                graph_state = build_stage_snapshot(dry_run_full_state, next_node)
            else:
                # Invoke the pipeline node
                user_msg = HumanMessage(content="continue")
                invoke_state = {**graph_state, "messages": [*graph_state.get("messages", []), user_msg]}

                result = _invoke_with_animation(
                    live, console, graph, invoke_state, stage_label, progress, step=step, total=total
                )
                if result is None:
                    return None

                graph_state = result

            if bell:
                console.bell()

            # Save immediately after LLM call so work isn't lost if user exits mid-review.
            if project_id:
                save_project_snapshot(project_id, graph_state)

        # ── Capacity warning intercept ────────────────────────────────
        # When total story points exceed sprint capacity, sprint_planner
        # returns a warning with capacity_override_target < -1.
        # Show a choice popup BEFORE generating sprints so the user can
        # decide whether to extend or keep the original target.
        # See docs: "Guardrails" — human-in-the-loop pattern
        _cap_sel = graph_state.get("capacity_override_target", 0)
        if _cap_sel < -1 and not dry_run:
            recommended = abs(_cap_sel)
            original_target = graph_state.get("_original_target_sprints", recommended)
            recommended_team = graph_state.get("_recommended_team_size", 0)
            current_team = graph_state.get("team_size", 1)
            # Extract warning text from the last AI message
            ai_msgs = graph_state.get("messages", [])
            cap_warning_text = ""
            if ai_msgs and isinstance(ai_msgs[-1], AIMessage):
                cap_warning_text = ai_msgs[-1].content.replace("**", "")
            # Build 3 options: extend sprints, increase team, keep as-is (overload).
            # Options 1 and 2 are both viable — only option 3 is flagged as not recommended.
            # When team is already at the Jira org cap, the "increase team" option is
            # replaced with a disabled note explaining why it's unavailable.
            # See docs: "Guardrails" — human-in-the-loop pattern
            options = [
                f"Extend to {recommended} sprints",
            ]
            team_can_grow = recommended_team > current_team
            if team_can_grow:
                options.append(f"Keep {original_target} sprints — increase team to {recommended_team} engineers")
            elif recommended_team > 0:
                # Jira cap reached — show disabled note in subtitle
                cap_warning_text += (
                    f"\n\nIncrease team is unavailable — your Jira board has "
                    f"{current_team} team member(s), which is already the maximum."
                )
            options.append(f"Keep {original_target} sprints, {current_team} engineer(s) — overload (not recommended)")
            # Show interactive choice popup before re-invoking sprint_planner
            choice = _pipeline_choice_screen(
                live,
                console,
                _key,
                title="Capacity Overflow",
                subtitle=cap_warning_text,
                options=options,
                step=step,
                total=total,
                stage_label=stage_label,
                progress=progress,
            )
            overload_index = len(options) - 1
            team_index = 1 if team_can_grow else -1  # -1 = no team option
            if choice == team_index:
                # Increase team size — sprint_planner will recalculate velocity
                graph_state["capacity_override_target"] = -1
                graph_state["_capacity_team_override"] = recommended_team
            elif choice == overload_index:
                # Keep as-is with overloaded sprints — sprint_planner will use enforce_target
                graph_state["capacity_override_target"] = -1
            else:
                # Extend to recommended (default if Esc or option 0)
                graph_state["capacity_override_target"] = recommended
            # Save warning text as a banner for the sprint review screen
            graph_state["_capacity_warning"] = {"text": cap_warning_text, "recommended": recommended}
            continue  # Re-invoke graph — sprint_planner generates sprints

        # Check for pending_review
        pending = graph_state.get("pending_review")
        if not pending:
            continue

        # Story highlighting: for the story_writer stage, normal line-by-line
        # scrolling is preserved but the story closest to the viewport center
        # is highlighted with a white border. The selected_story index is
        # recomputed from scroll_offset on every frame.
        # See docs: "Architecture" — story highlighting in pipeline review
        is_story_stage = pending == "story_writer"
        story_count = len(graph_state.get("stories", [])) if is_story_stage else 0
        selected_story = 0 if is_story_stage and story_count > 0 else None

        # Render artifacts for review (with story selection highlight if applicable)
        content_lines, sticky_headers = _render_pipeline_artifacts(
            console, graph_state, selected_story=selected_story if is_story_stage else None
        )
        # Pre-compute panel ranges for story highlighting
        story_panel_ranges = _find_story_panel_ranges(content_lines) if is_story_stage else []

        if export_only:
            # Auto-accept
            graph_state.pop("pending_review", None)
            graph_state.pop("last_review_decision", None)
            graph_state.pop("last_review_feedback", None)
            graph_state.pop("review_feedback_images", None)
            continue

        # Review loop
        scroll_offset = 0
        menu_selected = 0
        status_msg = ""
        _scroll_meta: dict = {}  # geometry published by _build_pipeline_screen

        # Action buttons differ by stage:
        # story_writer/task_decomposer/sprint_planner: Accept | Edit | Regenerate | Export [| Jira]
        # others: Accept | Edit | Export
        is_analysis_stage = pending == "project_analyzer"
        is_feature_stage = pending == "feature_generator"
        is_task_stage = pending == "task_decomposer"
        is_sprint_stage = pending == "sprint_planner"
        # Add tracker sync buttons to stages that produce syncable artifacts.
        # When the user chose a preferred tracker at intake (both were configured),
        # only show that tracker's button. Otherwise show all configured trackers.
        # Feature stage does NOT get sync buttons — features map to labels/tags, not issues.
        # See docs: "Tools" — tool types, write tools, human-in-the-loop pattern
        _active_trackers = _get_active_trackers()
        _qs = graph_state.get("questionnaire")
        _pref = getattr(_qs, "_preferred_tracker", "") if _qs else ""
        if _pref:
            # User chose a preferred tracker — only show that one
            _active_trackers = [_pref] if _pref in _active_trackers else _active_trackers

        # Anonymize state for the sprint stage: None = real plan; set = mask in place.
        # See docs: "Guardrails" — output masking for public sharing
        anon = None
        anon_instruction = ""

        def _compute_actions() -> list[str]:
            if is_story_stage or is_feature_stage or is_task_stage or is_sprint_stage:
                acts = ["Accept", "Edit", "Regenerate", "Export"]
            elif is_analysis_stage and graph_state.get("_small_project_oversized"):
                # Small-project scope advisory — offer a switch to Large (answers
                # are preserved). See README: "Guardrails" — human-in-the-loop (advisory).
                acts = ["Accept", "Edit", "Switch to Large", "Export"]
            else:
                acts = ["Accept", "Edit", "Export"]
            # The full plan (analysis → sprints) exists once the sprint stage renders,
            # so offer Anonymize there — a masked copy for public sharing. While masked,
            # swap it for Adjust (refine) + Revert (restore real names).
            if is_sprint_stage:
                acts.append("Share Online")
                acts.extend(["Adjust", "Revert"] if anon is not None else ["Anonymize"])
            if _active_trackers and (is_story_stage or is_task_stage or is_sprint_stage):
                for _trk in _active_trackers:
                    acts.append("Jira" if _trk == "jira" else "Azure DevOps")
            return acts

        actions = _compute_actions()
        num_actions = len(actions)

        def _rl():
            """Return (content_lines, sticky_headers), masked in place when anonymized."""
            if anon is None:
                return content_lines, sticky_headers
            from yeaboi.anonymize.apply import apply_replacements, mask_lines

            reps = anon.replacements
            return (
                mask_lines(content_lines, reps),
                [(i, apply_replacements(t, reps)) for i, t in sticky_headers],
            )

        def _anon_banner() -> str:
            """The sprint-stage banner: the anonymized indicator wins over the capacity note."""
            if anon is not None:
                return f"Anonymized · {len(anon.replacements)} masked — review before sharing"
            return cap_warning_text if is_sprint_stage else ""

        def _plan_document() -> tuple[str, str]:
            from yeaboi.repl._io import build_plan_markdown

            _pn = getattr(graph_state.get("project_analysis"), "project_name", "") or ""
            return (f"Sprint Plan — {_pn}" if _pn else "Sprint Plan"), build_plan_markdown(graph_state)

        # Capacity warning state — shown as a banner on the sprint review screen.
        # The user already made their choice before sprint generation, so this is
        # display-only (no popup on Accept).
        cap_warning = graph_state.get("_capacity_warning")
        cap_warning_text = cap_warning["text"] if cap_warning else ""

        # Button fade animation state — same pattern as intake review
        btn_fades = [1.0] + [0.0] * (num_actions - 1)
        btn_targets = list(btn_fades)

        w, h = console.size
        _ml, _sh = _rl()
        live.update(
            _build_pipeline_screen(
                stage_label,
                progress,
                _ml,
                scroll_offset,
                menu_selected,
                status="complete",
                width=w,
                height=h,
                btn_fades=btn_fades,
                step=step,
                total=total,
                sticky_headers=_sh,
                actions=actions,
                warning_text=_anon_banner(),
                scroll_meta=_scroll_meta,
            )
        )

        _pl_anim0 = time.monotonic()  # shimmer title clock
        while True:
            key = _key()

            if key == "esc":
                return None
            elif key in SCROLL_KEYS:
                _ns = coalesce_scroll(scroll_offset, key, _scroll_meta, _key)
                if _ns == scroll_offset:
                    continue  # boundary — skip the repaint so the title shimmer stays put
                scroll_offset = _ns
                # Track which story is closest to viewport center (for Edit action)
                if is_story_stage and story_panel_ranges:
                    _, viewport_h = console.size
                    vp_h = max(3, viewport_h - 14)
                    new_sel = _story_index_at_scroll(story_panel_ranges, scroll_offset, vp_h)
                    if new_sel != selected_story:
                        selected_story = new_sel
            elif key == "left":
                menu_selected = (menu_selected - 1) % num_actions
                btn_targets = [1.0 if i == menu_selected else 0.0 for i in range(num_actions)]
            elif key == "right":
                menu_selected = (menu_selected + 1) % num_actions
                btn_targets = [1.0 if i == menu_selected else 0.0 for i in range(num_actions)]
            elif key == "enter":
                action = actions[menu_selected]
                if action == "Accept":
                    logger.info("Review decision: Accept for %s", pending)
                    graph_state.pop("pending_review", None)
                    graph_state.pop("last_review_decision", None)
                    graph_state.pop("last_review_feedback", None)
                    graph_state.pop("review_feedback_images", None)
                    graph_state.pop("_small_project_oversized", None)
                    # Save Point C — persist after each pipeline stage acceptance
                    if project_id:
                        save_project_snapshot(project_id, graph_state)
                    break
                elif action == "Switch to Large":
                    # Small → Large switch. Preserve answers, clear artifacts,
                    # and signal _run_session_body to re-run intake for the extra
                    # Large-mode questions. See README: "Guardrails" — human-in-the-loop.
                    logger.info("Review decision: Switch to Large")
                    from yeaboi.agent.nodes import apply_epic_switch

                    apply_epic_switch(graph_state)
                    graph_state["_switch_to_epic_pending"] = True
                    if project_id:
                        save_project_snapshot(project_id, graph_state)
                    return graph_state
                elif action == "Edit":
                    logger.info("Review decision: Edit for %s", pending)
                    if is_story_stage and selected_story is not None:
                        # Direct text editor for the selected story
                        from yeaboi.ui.session.editor._editor import edit_story

                        stories = graph_state.get("stories", [])
                        if 0 <= selected_story < len(stories):
                            edited = edit_story(live, console, stories[selected_story], _key, width=w, height=h)
                            if edited is not None:
                                # Replace the story in the list (stories is a plain list at this point)
                                stories[selected_story] = edited
                                graph_state["stories"] = stories
                                # Re-render with the updated story
                                content_lines, sticky_headers = _render_pipeline_artifacts(
                                    console, graph_state, selected_story=selected_story
                                )
                        # Stay in review loop — no LLM call
                    elif is_feature_stage:
                        # Direct text editor for all epics
                        from yeaboi.ui.session.editor._editor_artifacts import edit_feature

                        epic_list = graph_state.get("features", [])
                        if epic_list:
                            edited_epics = edit_feature(
                                live,
                                console,
                                epic_list,
                                _key,
                                width=w,
                                height=h,
                            )
                            if edited_epics is not None:
                                graph_state["features"] = edited_epics
                                content_lines, sticky_headers = _render_pipeline_artifacts(console, graph_state)
                        # Stay in review loop — no LLM call
                    elif is_task_stage:
                        # Direct text editor for tasks — find the story group
                        # closest to viewport center using panel ranges.
                        from yeaboi.ui.session.editor._editor_artifacts import edit_task

                        all_tasks = graph_state.get("tasks", [])
                        stories = graph_state.get("stories", [])
                        # Build story groups in the same order as rendering
                        tasks_by_story: dict[str, list] = {}
                        for t in all_tasks:
                            tasks_by_story.setdefault(t.story_id, []).append(t)
                        story_group_ids = [s.id for s in stories if s.id in tasks_by_story]
                        # Determine selected story group from scroll position
                        task_panel_ranges = _find_story_panel_ranges(content_lines)
                        _, vp_h = console.size
                        vp_h_eff = max(3, vp_h - 14)
                        sel_group_idx = _story_index_at_scroll(task_panel_ranges, scroll_offset, vp_h_eff)
                        if 0 <= sel_group_idx < len(story_group_ids):
                            sid = story_group_ids[sel_group_idx]
                            group_tasks = tasks_by_story[sid]
                            edited_tasks = edit_task(
                                live,
                                console,
                                group_tasks,
                                _key,
                                width=w,
                                height=h,
                                story_id=sid,
                            )
                            if edited_tasks is not None:
                                # Replace the edited tasks in the full list
                                task_ids = {t.id for t in group_tasks}
                                new_all = [t for t in all_tasks if t.id not in task_ids]
                                new_all.extend(edited_tasks)
                                # Re-sort to preserve original order
                                orig_order = {t.id: i for i, t in enumerate(all_tasks)}
                                new_all.sort(key=lambda t: orig_order.get(t.id, 999))
                                graph_state["tasks"] = new_all
                                content_lines, sticky_headers = _render_pipeline_artifacts(console, graph_state)
                        # Stay in review loop — no LLM call
                    elif is_sprint_stage:
                        # Direct text editor for sprints — find the sprint
                        # closest to viewport center using panel ranges.
                        from yeaboi.ui.session.editor._editor_artifacts import edit_sprint

                        sprint_list = graph_state.get("sprints", [])
                        sprint_panel_ranges = _find_story_panel_ranges(content_lines)
                        _, vp_h = console.size
                        vp_h_eff = max(3, vp_h - 14)
                        sel_sprint_idx = _story_index_at_scroll(sprint_panel_ranges, scroll_offset, vp_h_eff)
                        if 0 <= sel_sprint_idx < len(sprint_list):
                            edited_sprint = edit_sprint(
                                live,
                                console,
                                sprint_list[sel_sprint_idx],
                                _key,
                                width=w,
                                height=h,
                            )
                            if edited_sprint is not None:
                                sprint_list[sel_sprint_idx] = edited_sprint
                                graph_state["sprints"] = sprint_list
                                content_lines, sticky_headers = _render_pipeline_artifacts(console, graph_state)
                        # Stay in review loop — no LLM call
                    elif is_analysis_stage:
                        # Go back to the accordion to edit questionnaire answers,
                        # then regenerate the analysis from the updated answers.
                        edit_result = _edit_accordion_browse(
                            live,
                            console,
                            graph,
                            graph_state,
                            _key,
                            export_only,
                        )
                        if edit_result is None:
                            return None
                        graph_state = edit_result
                        # Clear analysis so it gets regenerated from updated answers
                        graph_state.pop("project_analysis", None)
                        graph_state.pop("pending_review", None)
                        break  # Re-invoke graph to regenerate analysis
                    else:
                        # Non-story stages: prompt for feedback → LLM regeneration
                        edit_attachments: list[str] = []
                        feedback = _get_edit_input(
                            live,
                            console,
                            _key,
                            "Describe what you'd like to change:",
                            attachments=edit_attachments,
                            scope_id=graph_state.get("_attachment_scope", ""),
                        )
                        if feedback:
                            from yeaboi.ui.shared._attachments import referenced_images

                            pending_node = graph_state["pending_review"]
                            serialized = _serialize_artifacts_for_review(graph_state, pending_node)
                            _clear_downstream_artifacts(graph_state, pending_node)
                            graph_state["last_review_decision"] = ReviewDecision.EDIT
                            # Ctrl+V screenshots attached to this feedback — consumed
                            # by the regenerating node, cleared alongside the feedback.
                            feedback_images = referenced_images(feedback, edit_attachments)
                            if feedback_images:
                                graph_state["review_feedback_images"] = feedback_images
                            if serialized:
                                graph_state["last_review_feedback"] = (
                                    f"{feedback}\n\n---PREVIOUS OUTPUT---\n{serialized}"
                                )
                            else:
                                graph_state["last_review_feedback"] = feedback
                            graph_state.pop("pending_review", None)
                            break
                        # Cancel edit — stay on review
                elif action == "Regenerate":
                    logger.info("Review decision: Regenerate for %s", pending)
                    # LLM-assisted regeneration (story stage only)
                    regen_attachments: list[str] = []
                    feedback = _get_edit_input(
                        live,
                        console,
                        _key,
                        "Describe what you'd like the AI to change:",
                        attachments=regen_attachments,
                        scope_id=graph_state.get("_attachment_scope", ""),
                    )
                    if feedback:
                        from yeaboi.ui.shared._attachments import referenced_images

                        pending_node = graph_state["pending_review"]
                        serialized = _serialize_artifacts_for_review(graph_state, pending_node)
                        _clear_downstream_artifacts(graph_state, pending_node)
                        graph_state["last_review_decision"] = ReviewDecision.EDIT
                        feedback_images = referenced_images(feedback, regen_attachments)
                        if feedback_images:
                            graph_state["review_feedback_images"] = feedback_images
                        if serialized:
                            graph_state["last_review_feedback"] = f"{feedback}\n\n---PREVIOUS OUTPUT---\n{serialized}"
                        else:
                            graph_state["last_review_feedback"] = feedback
                        graph_state.pop("pending_review", None)
                        break
                    # Cancel — stay on review
                elif action == "Export":
                    logger.info("Review decision: Export for %s", pending)
                    if anon is not None:  # export the masked copy, matching the screen
                        from yeaboi.ui.mode_select import _anon_export

                        _dt, _md = _plan_document()
                        _plan_name = getattr(graph_state.get("project_analysis"), "project_name", "") or ""
                        _anon_export(
                            console,
                            live,
                            _key,
                            0.05,
                            False,
                            anon=anon,
                            doc_title=_dt,
                            markdown=_md,
                            project_name=_plan_name,
                            source_mode="planning",
                        )
                    else:
                        _plan_export_flow(live, console, _key, graph_state, pending)
                    status_msg = ""
                    # Force immediate redraw
                    w, h = console.size
                    _ml, _sh = _rl()
                    live.update(
                        _build_pipeline_screen(
                            stage_label,
                            progress,
                            _ml,
                            scroll_offset,
                            menu_selected,
                            status="complete",
                            width=w,
                            height=h,
                            status_msg=status_msg,
                            btn_fades=btn_fades,
                            step=step,
                            total=total,
                            sticky_headers=_sh,
                            actions=actions,
                            warning_text=_anon_banner(),
                            scroll_meta=_scroll_meta,
                        )
                    )
                elif action == "Share Online":
                    logger.info("Review decision: Share Online for %s", pending)
                    from yeaboi.sharing.documents import planning_document
                    from yeaboi.ui.shared._components import PLANNING_THEME, planning_title
                    from yeaboi.ui.shared._output_share import run_output_share

                    run_output_share(
                        console,
                        live,
                        _key,
                        0.05,
                        True,
                        document=planning_document(graph_state, stage=pending, anon=anon),
                        theme=PLANNING_THEME,
                        title_fn=planning_title,
                    )
                elif action in ("Anonymize", "Adjust"):
                    # In-place mask (or refine): re-render the SAME pipeline with only the
                    # sensitive words swapped. The blocking _key() drives the loading screen.
                    logger.info("Review decision: %s for %s", action, pending)
                    from yeaboi.ui.mode_select import _run_anonymize_pass
                    from yeaboi.ui.shared._components import PLANNING_THEME, planning_title

                    _plan_name = getattr(graph_state.get("project_analysis"), "project_name", "") or ""
                    if action == "Adjust":
                        adj = _get_edit_input(
                            live,
                            console,
                            _key,
                            "Also mask …  ·  don't mask … (it's public/safe):",
                            attachments=[],
                            scope_id=graph_state.get("_attachment_scope", ""),
                        )
                        if adj and adj.strip():
                            anon_instruction = f"{anon_instruction}\n{adj.strip()}".strip()
                        else:
                            adj = None  # cancelled — leave the current mask untouched
                    else:
                        anon_instruction = ""
                        adj = "run"  # sentinel: always run on first Anonymize
                    if adj:
                        res = _run_anonymize_pass(
                            console,
                            live,
                            _key,
                            0.05,
                            False,
                            markdown=_plan_document()[1],
                            instruction=anon_instruction,
                            project_name=_plan_name,
                            source_mode="planning",
                            theme=PLANNING_THEME,
                            title=planning_title(),
                        )
                        if res is not None:
                            anon = res
                            actions = _compute_actions()
                            num_actions = len(actions)
                            menu_selected = min(menu_selected, num_actions - 1)
                            btn_targets = [1.0 if i == menu_selected else 0.0 for i in range(num_actions)]
                            btn_fades = list(btn_targets)
                elif action == "Revert":  # restore the real names (no LLM call)
                    anon, anon_instruction = None, ""
                    actions = _compute_actions()
                    num_actions = len(actions)
                    menu_selected = min(menu_selected, num_actions - 1)
                    btn_targets = [1.0 if i == menu_selected else 0.0 for i in range(num_actions)]
                    btn_fades = list(btn_targets)
                elif action in ("Jira", "Azure DevOps"):
                    _btn_tracker = "jira" if action == "Jira" else "azdevops"
                    logger.info("%s sync requested for %s", action, pending)
                    tracker_result = _handle_tracker_sync(
                        live,
                        console,
                        _key,
                        graph_state,
                        pending,
                        stage_label,
                        progress,
                        step,
                        total,
                        tracker=_btn_tracker,
                    )
                    if tracker_result is not None:
                        graph_state = tracker_result
                        # Save after tracker sync
                        if project_id:
                            save_project_snapshot(project_id, graph_state)
                        # Re-render artifacts (content_lines unchanged, but status_msg updates)
                        _sk = "jira_story_keys" if _btn_tracker == "jira" else "azdevops_story_keys"
                        created = len(graph_state.get(_sk, {}))
                        status_msg = f"Synced to {action} ({created} stories)"

            # Animate button fades toward targets
            _fade_step = 0.15
            for i in range(num_actions):
                if btn_fades[i] < btn_targets[i]:
                    btn_fades[i] = min(btn_fades[i] + _fade_step, btn_targets[i])
                elif btn_fades[i] > btn_targets[i]:
                    btn_fades[i] = max(btn_fades[i] - _fade_step, btn_targets[i])

            w, h = console.size
            _ml, _sh = _rl()
            live.update(
                _build_pipeline_screen(
                    stage_label,
                    progress,
                    _ml,
                    scroll_offset,
                    menu_selected,
                    status="complete",
                    width=w,
                    height=h,
                    status_msg=status_msg,
                    btn_fades=btn_fades,
                    step=step,
                    total=total,
                    sticky_headers=_sh,
                    actions=actions,
                    warning_text=_anon_banner(),
                    shimmer_tick=time.monotonic() - _pl_anim0,
                    scroll_meta=_scroll_meta,
                )
            )


# ---------------------------------------------------------------------------
# Phase E: Completion + Chat
# ---------------------------------------------------------------------------


def _phase_chat(
    live: Live,
    console: Console,
    graph,
    graph_state: dict,
    _key,
    export_only: bool,
    project_id: str = "",
) -> None:
    """Post-pipeline chat — the user can ask questions or export.

    For export_only mode, just exports and returns.
    """
    logger.info("_phase_chat started")
    from yeaboi.persistence import save_project_snapshot
    from yeaboi.repl._io import _export_plan_markdown

    if export_only:
        _export_plan_markdown(graph_state)
        return

    messages: list[tuple[str, str]] = []
    input_value = ""
    scroll_offset = 0
    _chat_scroll_meta: dict = {}

    # Ctrl+V image paste — per-message attachments; surviving [image #N] chips
    # ride to the agent node via invoke_state["chat_images"] (see agent/state.py).
    from yeaboi.ui.shared._attachments import handle_ctrl_v, referenced_images

    chat_attachments: list[str] = []
    paste_notice = ""

    def _set_paste_notice(msg: str) -> None:
        nonlocal paste_notice
        paste_notice = msg

    # Follow the newest message until the user scrolls up; new messages re-pin to
    # the bottom only while following. Manual scroll keys break the follow.
    _chat_follow = True

    def _chat_bottom() -> int:
        return _chat_scroll_meta.get("max_offset", 0)

    w, h = console.size
    live.update(
        _build_chat_screen(messages, input_value, scroll_offset, width=w, height=h, scroll_meta=_chat_scroll_meta)
    )

    _chat_anim0 = time.monotonic()  # shimmer title clock
    while True:
        key = _key()
        if key and key != "":
            paste_notice = ""

        if key == "esc":
            return
        elif key == "enter":
            if not input_value.strip():
                continue

            text = input_value.strip()

            # Handle export command
            if text.lower() == "export":
                logger.info("Chat: export requested")
                _plan_export_flow(live, console, _key, graph_state, "complete")
                messages.append(("ai", "Plan exported successfully."))
                input_value = ""
                # Pin to the newest line: request past-the-end, the builder clamps
                # for display AND publishes the true bottom, which we then adopt.
                scroll_offset = _SCROLL_BOTTOM
                w, h = console.size
                live.update(
                    _build_chat_screen(
                        messages, input_value, scroll_offset, width=w, height=h, scroll_meta=_chat_scroll_meta
                    )
                )
                scroll_offset = _chat_bottom()
                _chat_follow = True
                continue

            if text.lower() in {"exit", "quit"}:
                return

            messages.append(("user", text))
            input_value = ""
            logger.debug("Chat message sent: len=%d", len(text))

            # Invoke graph in background. The message itself stays text-only
            # (nodes string-op on .content); surviving screenshots travel via
            # the chat_images state field and are attached inside call_model.
            chat_images = referenced_images(text, chat_attachments)
            chat_attachments = []
            user_msg = HumanMessage(content=text)
            invoke_state = {**graph_state, "messages": [*graph_state.get("messages", []), user_msg]}
            if chat_images:
                invoke_state["chat_images"] = chat_images
                logger.info("Chat message includes %d pasted image(s)", len(chat_images))

            # Show processing state
            result_box: list = [None, None]
            thread = threading.Thread(
                target=_invoke_graph_thread,
                args=(graph, invoke_state, result_box),
                daemon=True,
            )
            thread.start()

            start = time.monotonic()
            while thread.is_alive():
                tick = time.monotonic() - start
                w, h = console.size
                live.update(
                    _build_chat_screen(
                        messages,
                        "",
                        scroll_offset,
                        width=w,
                        height=h,
                        processing=True,
                        tick=tick,
                        shimmer_tick=tick,
                        scroll_meta=_chat_scroll_meta,
                    )
                )
                time.sleep(FRAME_TIME_30FPS)
            thread.join()

            if result_box[0] is not None:
                graph_state = result_box[0]
                ai_msgs = graph_state.get("messages", [])
                if ai_msgs and isinstance(ai_msgs[-1], AIMessage):
                    messages.append(("ai", ai_msgs[-1].content))
                # Save Point D — persist after chat messages
                if project_id:
                    save_project_snapshot(project_id, graph_state)
            elif result_box[1] is not None:
                logger.error("Chat graph invoke failed: %s", result_box[1])
                messages.append(("ai", f"Error: {result_box[1]}"))

            # New reply — pin to the bottom (adopted after the render below).
            scroll_offset = _SCROLL_BOTTOM
            _chat_follow = True

        elif key in ("up", "scroll_up", "pageup", "home"):
            _ns = coalesce_scroll(scroll_offset, key, _chat_scroll_meta, _key)
            if _ns == scroll_offset:
                continue  # already at the top — skip the repaint (no shimmer flicker)
            # Any upward scroll stops following the newest message.
            _chat_follow = False
            scroll_offset = _ns
        elif key in ("down", "scroll_down", "pagedown", "end"):
            _ns = coalesce_scroll(scroll_offset, key, _chat_scroll_meta, _key)
            if _ns == scroll_offset:
                continue  # already at the bottom — skip the repaint
            scroll_offset = _ns
            # Re-pin to follow mode once the user scrolls back to the bottom.
            if scroll_offset >= _chat_scroll_meta.get("max_offset", 0):
                _chat_follow = True
        elif key == "backspace":
            input_value = input_value[:-1]
        elif key == "clear":
            input_value = ""
        elif isinstance(key, str) and key.startswith("paste:"):
            input_value += key[6:]
        elif key == "ctrl+v":
            w, h = console.size
            live.update(
                _build_chat_screen(
                    messages,
                    input_value,
                    scroll_offset,
                    width=w,
                    height=h,
                    scroll_meta=_chat_scroll_meta,
                    notice="Pasting image…",
                )
            )
            chip = handle_ctrl_v(
                chat_attachments,
                scope_id=graph_state.get("_attachment_scope", "") or "planning",
                set_notice=_set_paste_notice,
            )
            if chip:
                input_value += chip
                paste_notice = f"Screenshot attached as {chip}"
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            input_value += key
        elif key == "":
            pass
        else:
            continue

        w, h = console.size
        live.update(
            _build_chat_screen(
                messages,
                input_value,
                scroll_offset,
                width=w,
                height=h,
                shimmer_tick=time.monotonic() - _chat_anim0,
                scroll_meta=_chat_scroll_meta,
                notice=paste_notice,
            )
        )
        # Adopt the builder's clamped bottom when following or when we requested
        # past-the-end, so the loop counter matches what's displayed.
        if _chat_follow or scroll_offset == _SCROLL_BOTTOM:
            scroll_offset = _chat_bottom()
