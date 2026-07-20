"""Full-screen TUI session — replaces the prompt_toolkit REPL for Smart/Full intake.

# See docs: "Architecture" — this is a UI component in the CLI layer.
# It reuses the same Rich Live context from mode_select.py so there's no
# jarring screen-clear/re-render gap between mode selection and the first question.
#
# The session drives the entire post-selection experience:
#   description input -> intake questions -> review -> pipeline -> chat
#
# Threading pattern: LLM calls use the same threading pattern as provider_select.py.
# A background thread runs graph.invoke() while the main thread animates a pulsing
# border. When the thread completes, the result is read from a shared list.
#
# Scrollable viewport: Long content (artifact tables, chat responses) is rendered
# to a string buffer, split into lines, and sliced by scroll_offset. Scroll
# indicators (up-arrow/down-arrow) show when content extends beyond the viewport.
"""

from __future__ import annotations

import inspect
import logging
import threading
import time

import anthropic
from langchain_core.messages import HumanMessage
from rich.console import Console
from rich.live import Live

from yeaboi.agent.graph import create_graph
from yeaboi.agent.state import QuestionnaireState
from yeaboi.persistence import create_project_id, save_project_snapshot
from yeaboi.repl._io import _export_plan_markdown, _get_active_suggestion  # noqa: F401

# Re-export utils for backward compatibility (tests import from here)
from yeaboi.ui.session._utils import (  # noqa: F401
    _handle_rate_limit_tui,
    _invoke_graph_thread,
    _render_to_lines,
    _wrap_text,
)

# Re-export phase functions for backward compatibility (tests import from here)
# Internal imports used by run_session
from yeaboi.ui.session.phases._phases import (  # noqa: F401
    _phase_description_input,
    _phase_intake_questions,
    _phase_intake_review,
    _phase_pipeline,
    _question_input_loop,
)

# Re-export accordion screen builder for test access
from yeaboi.ui.session.screens._accordion import _build_accordion_question_screen  # noqa: F401

# Re-export screen builders for backward compatibility (tests import from here)
from yeaboi.ui.session.screens._screens import _build_summary_screen  # noqa: F401
from yeaboi.ui.session.screens._screens_input import (  # noqa: F401
    _build_description_screen,
    _build_question_screen,
)
from yeaboi.ui.session.screens._screens_pipeline import (  # noqa: F401
    _build_chat_screen,
    _build_edit_prompt_screen,
    _build_pipeline_screen,
)
from yeaboi.ui.shared._animations import FRAME_TIME_30FPS, loading_border_color
from yeaboi.ui.shared._components import PAD
from yeaboi.ui.shared._input import read_key

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

_PAD = PAD  # alias for backward compatibility within this module


# ---------------------------------------------------------------------------
# Public API — run_session()
# ---------------------------------------------------------------------------


def run_session(
    live: Live,
    console: Console,
    intake_mode: str,
    *,
    questionnaire: QuestionnaireState | None = None,
    resume_project_id: str = "",
    resume_graph_state: dict | None = None,
    export_only: bool = False,
    bell: bool = True,
    theme: str = "dark",
    dry_run: bool = False,
    _read_key_fn=None,
    analysis_profile_id: str = "",
    initial_description: str = "",
) -> None:
    """Drive the full TUI session inside an existing Live context.

    # See docs: "Architecture" — this replaces run_repl() for Smart/Full intake.
    # Called from mode_select.py after the user picks Smart or Full, inside the
    # same `with Live(...)` block so there's no screen-clearing gap.

    Args:
        live: The Rich Live instance from mode_select.py (already active).
        console: The Rich Console for size queries.
        intake_mode: "smart" / "small_project" / "quick" — which intake flow to use.
        questionnaire: Pre-populated questionnaire (from import flow). Usually None.
        resume_project_id: If resuming, the existing project ID to reuse.
        resume_graph_state: If resuming, the pre-loaded graph state dict.
        export_only: Auto-accept all review checkpoints (for --export-only).
        bell: Ring terminal bell after pipeline steps.
        theme: Colour theme ("dark" or "light").
        _read_key_fn: Override for testing (default: read_key from _input.py).
        initial_description: Pre-fill for the Phase A description editor (e.g.
            a project description extracted from the quarterly roadmap). The
            user can still edit before submitting.
    """
    logger.info(
        "run_session started: mode=%s resume=%s export_only=%s dry_run=%s preseeded=%s",
        intake_mode,
        bool(resume_graph_state),
        export_only,
        dry_run,
        bool(initial_description),
    )
    rk = _read_key_fn or read_key
    _supports_timeout = "timeout" in inspect.signature(rk).parameters

    def _key(timeout: float = FRAME_TIME_30FPS) -> str:
        return rk(timeout=timeout) if _supports_timeout else rk()

    # Use existing project ID when resuming, otherwise generate a new one.
    # See docs: "Memory & State" — each session gets a UUID so snapshots
    # can be upserted by ID across save points.
    project_id = resume_project_id or create_project_id()
    logger.info("Session %s: project_id=%s", "resumed" if resume_project_id else "started", project_id)

    # Attach a per-session log file so each session's diagnostics are isolated.
    # The log lives at ~/.scrum-agent/logs/{project_id}.log and is cleaned up
    # when the project is deleted from the TUI.
    from yeaboi.persistence import attach_session_logger, remove_session_logger

    attach_session_logger(project_id)
    try:
        _run_session_body(
            live,
            console,
            intake_mode,
            project_id,
            rk,
            _key,
            questionnaire=questionnaire,
            resume_graph_state=resume_graph_state,
            export_only=export_only,
            bell=bell,
            dry_run=dry_run,
            analysis_profile_id=analysis_profile_id,
            initial_description=initial_description,
        )
    finally:
        logger.info("Session ended: project_id=%s", project_id)
        remove_session_logger()


def _run_session_body(
    live,
    console,
    intake_mode,
    project_id,
    rk,
    _key,
    *,
    questionnaire,
    resume_graph_state,
    export_only,
    bell,
    dry_run,
    analysis_profile_id="",
    initial_description="",
):
    """Session body — extracted so run_session can use try/finally for log cleanup."""
    # Compile graph once for the session (skipped in dry-run — no LLM calls)
    # See docs: "Agentic Blueprint Reference" — Core Graph Setup
    graph = None if dry_run else create_graph()

    # When resuming an in-progress project, use the saved graph state.
    # This skips the description input and jumps straight to where the user left off.
    if resume_graph_state is not None:
        graph_state = resume_graph_state
    else:
        graph_state: dict = {"messages": []}
        graph_state["_intake_mode"] = intake_mode
        if analysis_profile_id:
            graph_state["analysis_profile_id"] = analysis_profile_id
            # Extract custom DoD items from the analysis profile
            try:
                from yeaboi.agent.nodes import _load_profile_by_id

                _p, _ex = _load_profile_by_id(analysis_profile_id)
                if _ex:
                    proposed = _ex.get("proposed_dod", {})
                    if isinstance(proposed, dict):
                        _dod = [
                            it["practice"]
                            for it in proposed.get("items", [])
                            if isinstance(it, dict) and it.get("status") in ("established", "emerging")
                        ]
                        if _dod:
                            graph_state["custom_dod_items"] = tuple(_dod)
                            logger.info("Custom DoD from analysis: %s", _dod)
            except Exception:
                pass

    # Where Ctrl+V screenshots for this session are saved (~/.yeaboi/attachments/
    # <scope>/). Stashed in state so nested input loops don't need project_id
    # threaded through every signature; persists harmlessly across --resume.
    graph_state["_attachment_scope"] = project_id

    if questionnaire is not None:
        questionnaire.intake_mode = intake_mode
        graph_state["questionnaire"] = questionnaire

    logger.info("Phase transition: description_input")
    # ── Phase A: Description Input ─────────────────────────────────────
    # Multi-line text input for the initial project description.
    # This is the first thing the user types — their project overview.
    # Skipped when resuming a project that already has messages.
    # In dry-run mode, the input is pre-filled with an example description.
    if questionnaire is None and resume_graph_state is None:
        desc_result = _phase_description_input(
            live, console, _key, dry_run=dry_run, scope_id=project_id, initial_text=initial_description
        )
        if desc_result is None:
            return  # Esc pressed — go back to mode select

        description, desc_lines, desc_row, desc_col, desc_images = desc_result
        if desc_images:
            # Screenshot paths ride in graph state (not in messages — nodes
            # string-op on message content); project_analyzer attaches them as
            # multimodal image blocks at invoke time. See agent/state.py.
            graph_state["pasted_images"] = desc_images

        # ── Dry-run: skip LLM calls, load pre-saved state ──
        # Instead of jumping straight to the pipeline, set the questionnaire
        # to awaiting_confirmation so the normal flow shows the intake summary
        # (with Edit/accordion support) before proceeding to the pipeline.
        if dry_run:
            from yeaboi.ui.session._dry_run import load_dry_run_state

            graph_state = load_dry_run_state()
            if graph_state is None:
                return
            # Strip pipeline artifacts so the pipeline replays all 5 stages
            for key in ("project_analysis", "epics", "stories", "tasks", "sprints"):
                graph_state.pop(key, None)
            # Set questionnaire to awaiting_confirmation so Phase C shows
            # the intake summary with Accept/Edit/Export before pipeline.
            qs = graph_state.get("questionnaire")
            if isinstance(qs, QuestionnaireState):
                qs.completed = False
                qs.awaiting_confirmation = True
            graph_state["pending_review"] = "project_intake"
        else:
            # Inject as the first human message and invoke the graph
            user_msg = HumanMessage(content=description)
            invoke_state = {**graph_state, "messages": [user_msg]}

            # Animate the description input box border with green/white cycling
            # while the LLM processes the description.
            result_box: list = [None, None]
            thread = threading.Thread(
                target=_invoke_graph_thread,
                args=(graph, invoke_state, result_box),
                daemon=True,
            )
            thread.start()

            anim_start = time.monotonic()
            while thread.is_alive():
                tick = time.monotonic() - anim_start
                w, h = console.size
                live.update(
                    _build_description_screen(
                        desc_lines,
                        desc_row,
                        desc_col,
                        width=w,
                        height=h,
                        border_override=loading_border_color(tick),
                    )
                )
                time.sleep(FRAME_TIME_30FPS)
            thread.join()

            if result_box[1] is not None:
                err = result_box[1]
                if isinstance(err, anthropic.RateLimitError):
                    if _handle_rate_limit_tui(graph, invoke_state, result_box):
                        result = result_box[0]
                    else:
                        return
                else:
                    # Show a user-friendly error message instead of silently
                    # returning to the project select screen.
                    from yeaboi.ui.session._utils import _classify_api_error

                    error_msg = _classify_api_error(err)
                    logger.warning("Description processing failed: %s", error_msg)
                    from rich.panel import Panel
                    from rich.text import Text as RichText

                    w, _ = console.size
                    error_panel = Panel(
                        RichText(error_msg, style="bold red"),
                        title="[bold red]Error[/bold red]",
                        border_style="red",
                        width=min(w - 4, 80),
                        padding=(1, 2),
                    )
                    live.update(error_panel)
                    time.sleep(4)
                    return
            else:
                result = result_box[0]

            if result is None:
                return
            graph_state = result

            # Save Point A — persist after description processing
            save_project_snapshot(project_id, graph_state)

    # Phases B→D run in a loop so a Small project → Large switch (chosen at
    # the analysis review) can re-run intake for the extra Large-mode questions with
    # the user's answers preserved. Normal runs execute the body exactly once.
    # See docs: "Guardrails" — human-in-the-loop (advisory).
    while True:
        # Determine resume point — skip phases that are already complete.
        # See docs: "Memory & State" — when resuming a saved session, we jump
        # to the earliest incomplete phase rather than replaying from the start.
        qs = graph_state.get("questionnaire")
        _intake_done = qs is not None and hasattr(qs, "completed") and qs.completed
        _pipeline_done = bool(graph_state.get("sprints")) and not graph_state.get("pending_review")

        # ── Phase B: Intake Questions ──────────────────────────────────────
        # Loop through intake questions until the questionnaire is complete.
        if not _intake_done:
            logger.info("Phase transition: intake_questions")
            graph_state = _phase_intake_questions(live, console, graph, graph_state, _key, export_only)
            if graph_state is None:
                return

            # Save Point A2 — persist after intake questions so answers survive restarts.
            # Without this, exiting at the review screen would lose all questionnaire answers.
            save_project_snapshot(project_id, graph_state)

        # ── Phase C: Intake Summary + Review ───────────────────────────────
        if not _intake_done:
            logger.info("Phase transition: intake_review")
            graph_state = _phase_intake_review(live, console, graph, graph_state, _key, export_only)
            if graph_state is None:
                return

            # Save Point B — persist after intake review acceptance
            save_project_snapshot(project_id, graph_state)

        # ── Phase D: Pipeline Stages ───────────────────────────────────────
        if not _pipeline_done:
            logger.info("Phase transition: pipeline")
            graph_state = _phase_pipeline(
                live, console, graph, graph_state, _key, export_only, bell, project_id, dry_run=dry_run
            )
            if graph_state is None:
                return

        # Small → Large switch requested at the analysis review: re-run the
        # loop. apply_epic_switch() already reset the questionnaire (mode=smart,
        # completed=False, _reopen_for_epic=True) and cleared artifacts. Invoke
        # the graph once (no LLM) so project_intake produces the first Large-mode
        # gap question before Phase B re-runs.
        if graph_state is not None and graph_state.pop("_switch_to_epic_pending", False):
            logger.info("Switching Small project → Large; re-running intake")
            if graph is not None:
                try:
                    graph_state = graph.invoke(graph_state)
                except Exception:
                    logger.warning("Epic-switch re-invoke failed", exc_info=True)
            save_project_snapshot(project_id, graph_state)
            continue
        break

    # Auto-export when running with --export-only flag
    if export_only and graph_state is not None:
        from yeaboi.repl._io import _export_plan_markdown

        logger.info("Auto-export triggered (export_only)")
        _md_path = _export_plan_markdown(graph_state)
        logger.info("Auto-export complete: %s", _md_path)

    # Auto-generate SCRUM.md from the completed project so the user can
    # review, tweak, and re-use it as input for future runs.
    if project_id and graph_state is not None and graph_state.get("sprints"):
        from yeaboi.persistence import generate_scrum_md

        generate_scrum_md(project_id)
        logger.info("SCRUM.md generated: project_id=%s", project_id)

    logger.info("Session complete")
    # Pipeline complete — return to project dashboard
