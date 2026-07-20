"""Intake review and accordion browse phases for the TUI session."""

from __future__ import annotations

import logging
import time

from langchain_core.messages import HumanMessage
from rich.console import Console
from rich.live import Live

from yeaboi.agent.state import TOTAL_QUESTIONS, QuestionnaireState
from yeaboi.prompts.intake import INTAKE_QUESTIONS, QUESTION_METADATA, is_choice_question
from yeaboi.repl._io import _export_checkpoint
from yeaboi.ui.session._utils import _invoke_with_animation, _render_to_lines, _render_tui_intake_summary
from yeaboi.ui.session.screens._accordion import _build_accordion_question_screen
from yeaboi.ui.session.screens._screens import _build_summary_screen
from yeaboi.ui.session.screens._screens_pipeline import _build_edit_prompt_screen
from yeaboi.ui.shared._scroll import SCROLL_KEYS, coalesce_scroll

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase C: Intake Summary + Review
# ---------------------------------------------------------------------------


def _phase_intake_review(
    live: Live,
    console: Console,
    graph,
    graph_state: dict,
    _key,
    export_only: bool,
) -> dict | None:
    """Show intake summary and handle Accept/Edit/Export flow.

    Returns updated graph_state after acceptance, or None on cancel.
    """
    logger.info("_phase_intake_review started")
    while True:
        pending = graph_state.get("pending_review")
        if pending != "project_intake":
            # Not in intake review — check if questionnaire just completed
            qs = graph_state.get("questionnaire")
            if isinstance(qs, QuestionnaireState) and qs.completed:
                return graph_state
            if isinstance(qs, QuestionnaireState) and qs.awaiting_confirmation:
                # Need to trigger the graph to show summary
                pass
            else:
                return graph_state

        # Render the intake summary
        qs = graph_state.get("questionnaire")
        if not isinstance(qs, QuestionnaireState):
            return graph_state

        w, h = console.size
        table_w = max(40, w - 20)
        summary_renderable = _render_tui_intake_summary(qs, table_w)
        summary_lines = _render_to_lines(console, summary_renderable, w - 16)

        if export_only:
            # Auto-accept
            graph_state.pop("pending_review", None)
            user_msg = HumanMessage(content="accept")
            invoke_state = {**graph_state, "messages": [*graph_state.get("messages", []), user_msg]}
            result = _invoke_with_animation(live, console, graph, invoke_state, "Confirming", "")
            if result is None:
                return None
            graph_state = result
            continue

        scroll_offset = 0
        menu_selected = 0  # 0=Accept, 1=Edit, 2=Export
        status_msg = ""
        # The screen builder publishes its true scroll geometry here each render;
        # coalesce_scroll() below clamps to it so the offset never runs past the end.
        _scroll_meta: dict = {}

        # Button fade animation state — mirrors project dashboard pattern.
        # Each button has a current fade (0.0–1.0) and a target.
        # The selected button fades in (target=1.0), others fade out (target=0.0).
        btn_fades = [1.0, 0.0, 0.0]  # Accept starts focused
        btn_targets = [1.0, 0.0, 0.0]

        live.update(
            _build_summary_screen(
                summary_lines,
                scroll_offset,
                menu_selected,
                width=w,
                height=h,
                btn_fades=btn_fades,
                scroll_meta=_scroll_meta,
            )
        )

        _anim0 = time.monotonic()  # shimmer title clock
        while True:
            key = _key()

            if key == "esc":
                return None
            elif key in SCROLL_KEYS:
                _ns = coalesce_scroll(scroll_offset, key, _scroll_meta, _key)
                if _ns == scroll_offset:
                    continue  # boundary — skip the repaint so the title shimmer stays put
                scroll_offset = _ns
            elif key == "left":
                menu_selected = (menu_selected - 1) % 3
                btn_targets = [1.0 if i == menu_selected else 0.0 for i in range(3)]
            elif key == "right":
                menu_selected = (menu_selected + 1) % 3
                btn_targets = [1.0 if i == menu_selected else 0.0 for i in range(3)]
            elif key == "enter":
                if menu_selected == 0:
                    # Accept
                    logger.info("Intake review: Accept")
                    graph_state.pop("pending_review", None)
                    if graph is None:
                        # Dry-run: skip graph invocation, mark questionnaire complete
                        qs_obj = graph_state.get("questionnaire")
                        if isinstance(qs_obj, QuestionnaireState):
                            qs_obj.completed = True
                            qs_obj.awaiting_confirmation = False
                    else:
                        user_msg = HumanMessage(content="accept")
                        invoke_state = {**graph_state, "messages": [*graph_state.get("messages", []), user_msg]}
                        result = _invoke_with_animation(live, console, graph, invoke_state, "Confirming", "")
                        if result is None:
                            return None
                        graph_state = result
                    break  # Exit inner loop, re-check outer
                elif menu_selected == 1:
                    logger.info("Intake review: Edit")
                    # Edit — show accordion browser to pick a question
                    edit_result = _edit_accordion_browse(live, console, graph, graph_state, _key, export_only)
                    if edit_result is None:
                        return None  # User cancelled entirely
                    graph_state = edit_result
                    break  # Re-render summary with updated answers
                elif menu_selected == 2:
                    logger.info("Intake review: Export")
                    # Export
                    _export_checkpoint(console, graph_state, stage="questionnaire")
                    status_msg = "Exported successfully"

            # Animate button fades toward targets (same step-per-frame as project dashboard)
            _fade_step = 0.15
            for i in range(3):
                if btn_fades[i] < btn_targets[i]:
                    btn_fades[i] = min(btn_fades[i] + _fade_step, btn_targets[i])
                elif btn_fades[i] > btn_targets[i]:
                    btn_fades[i] = max(btn_fades[i] - _fade_step, btn_targets[i])

            w, h = console.size
            live.update(
                _build_summary_screen(
                    summary_lines,
                    scroll_offset,
                    menu_selected,
                    width=w,
                    height=h,
                    status_msg=status_msg,
                    btn_fades=btn_fades,
                    shimmer_tick=time.monotonic() - _anim0,
                    scroll_meta=_scroll_meta,
                )
            )


def _edit_accordion_browse(
    live: Live,
    console: Console,
    graph,
    graph_state: dict,
    _key,
    export_only: bool,
) -> dict | None:
    """Show the accordion with free arrow-key navigation for editing answers.

    # See docs: "Architecture" — edit mode lets the user browse all questions
    # with up/down arrows. Enter re-asks the highlighted question; Esc returns
    # to the review screen without changes.

    Returns updated graph_state, or None if user cancelled (Esc from session).
    """
    logger.debug("_edit_accordion_browse started")
    from yeaboi.ui.session.screens._accordion import _HIDDEN_QUESTIONS

    qs = graph_state.get("questionnaire")
    if not isinstance(qs, QuestionnaireState):
        return graph_state

    # Build list of navigable question numbers (skip hidden)
    nav_questions = [q for q in range(1, TOTAL_QUESTIONS + 1) if q not in _HIDDEN_QUESTIONS]
    browse_idx = 0  # index into nav_questions
    # Start at the first question
    if qs.current_question in nav_questions:
        browse_idx = nav_questions.index(qs.current_question)

    # Temporarily set current_question for the accordion renderer
    original_q = qs.current_question

    while True:
        qs.current_question = nav_questions[browse_idx]
        existing_answer = qs.answers.get(qs.current_question, "")
        # Browse mode: always show the input box (not choice picker).
        # Add "Enter to edit" hint so user knows to press Enter first.
        w, h = console.size
        live.update(
            _build_accordion_question_screen(
                INTAKE_QUESTIONS[qs.current_question],
                existing_answer,
                qs,
                progress=f"Editing \u2014 Q{qs.current_question} of {TOTAL_QUESTIONS}",
                phase_label="",
                width=w,
                height=h,
                edit_hint="Enter to edit \u00b7 Ctrl+S save \u00b7 Esc exit",
            )
        )

        key = _key()

        if key in ("esc", "ctrl+s"):
            # Return to review — Ctrl+S or Esc both save edits and go back
            qs.current_question = original_q
            return graph_state
        elif key in ("up", "scroll_up"):
            browse_idx = max(0, browse_idx - 1)
        elif key in ("down", "scroll_down"):
            browse_idx = min(len(nav_questions) - 1, browse_idx + 1)
        elif key == "enter":
            q_num = nav_questions[browse_idx]
            logger.info("Accordion edit: Q%d selected for re-answer", q_num)
            if graph is None:
                # Dry-run: edit answer inline (no graph invocation).
                # Uses the accordion question input which supports both
                # choice pickers and free text.
                from yeaboi.ui.session.phases._phases_intake import _question_input_loop

                q_text = INTAKE_QUESTIONS.get(q_num, f"Question {q_num}")
                choices = None
                if is_choice_question(q_num):
                    meta = QUESTION_METADATA.get(q_num)
                    if meta:
                        choices = [(opt, i == meta.default_index) for i, opt in enumerate(meta.options)]
                qs.editing_question = q_num
                new_answer = _question_input_loop(
                    live,
                    console,
                    _key,
                    question_text=q_text,
                    choices=choices,
                    suggestion=None,
                    progress=f"Editing \u2014 Q{q_num} of {TOTAL_QUESTIONS}",
                    phase_label="",
                    preamble_lines=None,
                    export_only=False,
                    graph_state=graph_state,
                    questionnaire=qs,
                )
                qs.editing_question = None
                if new_answer is not None and new_answer != "":
                    qs.answers[q_num] = new_answer
                    logger.info("Accordion edit: Q%d answer updated", q_num)
                # Stay in browse mode — re-render accordion with updated answer
                graph_state["pending_review"] = "project_intake"
            else:
                # Re-ask this question through the graph
                from yeaboi.ui.session.phases._phases_intake import _phase_intake_questions

                user_msg = HumanMessage(content=f"Q{q_num}")
                graph_state.pop("pending_review", None)
                invoke_state = {
                    **graph_state,
                    "messages": [*graph_state.get("messages", []), user_msg],
                }
                result = _invoke_with_animation(live, console, graph, invoke_state, "Re-asking question", "")
                if result is None:
                    return None
                graph_state = result
                # Enter single-question input for this question
                graph_state = _phase_intake_questions(live, console, graph, graph_state, _key, export_only)
                if graph_state is None:
                    return None
                # After answering, stay in browse mode with updated state
                qs = graph_state.get("questionnaire")
                if not isinstance(qs, QuestionnaireState):
                    return graph_state
            original_q = qs.current_question


def _get_edit_input(
    live: Live,
    console: Console,
    _key,
    prompt: str,
    *,
    attachments: list[str] | None = None,
    scope_id: str = "",
) -> str | None:
    """Show a simple text input for edit prompts. Returns text or None on Esc.

    attachments: caller-owned list that Ctrl+V screenshot paths are appended to
        (each marked by an [image #N] chip in the text). The caller resolves
        surviving chips with referenced_images() after submit. None disables
        image paste (shows the standard "not supported" notice instead).
    """
    from yeaboi.ui.shared._attachments import handle_ctrl_v, unsupported_notice

    input_value = ""
    notice = ""
    w, h = console.size
    live.update(_build_edit_prompt_screen(prompt, input_value, width=w, height=h))

    def _set_notice(msg: str) -> None:
        nonlocal notice
        notice = msg

    _anim0 = time.monotonic()  # shimmer title clock
    while True:
        key = _key()
        if key and key != "":
            notice = ""

        if key == "esc":
            return None
        elif key in ("enter", "ctrl+s"):
            # Enter or Ctrl+S submits
            return input_value
        elif key == "backspace":
            input_value = input_value[:-1]
        elif key == "clear":
            input_value = ""
        elif key == "word_backspace":
            from yeaboi.ui.session.editor._editor_core import _word_boundary_left

            word_start = _word_boundary_left(input_value, len(input_value))
            input_value = input_value[:word_start]
        elif isinstance(key, str) and key.startswith("paste:"):
            input_value += key[6:]
        elif key == "ctrl+v":
            if attachments is None:
                unsupported_notice(_set_notice)
            else:
                w, h = console.size
                live.update(_build_edit_prompt_screen(prompt, input_value, width=w, height=h, notice="Pasting image…"))
                chip = handle_ctrl_v(attachments, scope_id=scope_id or "planning", set_notice=_set_notice)
                if chip:
                    input_value += chip
                    notice = f"Screenshot attached as {chip}"
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            input_value += key
        elif key == "":
            pass
        else:
            continue

        w, h = console.size
        live.update(
            _build_edit_prompt_screen(
                prompt, input_value, width=w, height=h, shimmer_tick=time.monotonic() - _anim0, notice=notice
            )
        )
