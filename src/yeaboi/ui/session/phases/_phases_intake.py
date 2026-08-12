"""Description input and intake question phases for the TUI session."""

from __future__ import annotations

import logging
import time

from langchain_core.messages import AIMessage, HumanMessage
from rich.console import Console
from rich.live import Live

from yeaboi.agent.state import TOTAL_QUESTIONS, QuestionnaireState
from yeaboi.prompts.intake import PHASE_LABELS, QUESTION_METADATA, is_choice_question
from yeaboi.repl._io import _get_active_suggestion
from yeaboi.repl._questionnaire import (
    _SUGGEST_CONFIRM,
    _resolve_choice_input,
    _resolve_dynamic_choice,
    _split_intake_preamble,
)
from yeaboi.repl._ui import _predict_next_node
from yeaboi.ui.session._utils import _invoke_with_animation
from yeaboi.ui.session.screens._accordion import _build_accordion_question_screen
from yeaboi.ui.session.screens._screens_input import (
    _build_description_screen,
    _build_question_screen,
    _image_hint,
    _voice_hint,
)
from yeaboi.ui.shared._input import paste_payload

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase A: Description Input
# ---------------------------------------------------------------------------


def _phase_description_input(
    live: Live, console: Console, _key, *, dry_run: bool = False, scope_id: str = "", initial_text: str = ""
) -> tuple[str, list[str], int, int, list[str]] | None:
    """Multi-line text input for the project description.

    Returns (description, input_lines, cursor_row, cursor_col, image_paths) on
    submit, or None if the user pressed Esc. The extra editor state is used by
    the caller to animate the input box border during LLM processing;
    image_paths are the screenshots pasted with Ctrl+V whose [image #N] chips
    survive in the text (see ui/shared/_attachments.py) — the caller routes them
    to the analyzer via graph_state["pasted_images"].

    When initial_text is given (e.g. a project description extracted from the
    quarterly roadmap), the editor is pre-filled with it — the user can still
    edit before submitting. It wins over the dry-run example. Otherwise, when
    dry_run=True, pre-fills an example description so the developer can just
    hit Enter twice to move on quickly.
    """
    logger.debug("_phase_description_input: dry_run=%s preseeded=%s", dry_run, bool(initial_text))
    if initial_text:
        input_lines = initial_text.split("\n")
        cursor_row = len(input_lines) - 1
        cursor_col = len(input_lines[-1])
    elif dry_run:
        _example = (
            "We're building a mobile app for restaurant reservations. "
            "The team is 4 developers, we use React Native and Node.js, "
            "and we need to launch an MVP in 3 months."
        )
        input_lines = [_example]
        cursor_row = 0
        cursor_col = len(_example)
    else:
        input_lines = [""]
        cursor_row = 0
        cursor_col = 0

    w, h = console.size
    live.update(_build_description_screen(input_lines, cursor_row, cursor_col, width=w, height=h))

    # Voice input: double-tap Space to dictate (see DoubleTapSpace for why).
    from yeaboi.ui.shared._voice_input import DoubleTapSpace, record_voice_input

    _dts = DoubleTapSpace()

    # Ctrl+V image paste — screenshots saved to ~/.yeaboi/attachments/, tracked
    # here as file paths; a plain-text [image #N] chip marks each one in the buffer.
    from yeaboi.ui.shared._attachments import handle_ctrl_v, referenced_images

    attachments: list[str] = []
    paste_notice = ""  # transient status line, cleared on the next keypress

    def _set_paste_notice(msg: str) -> None:
        nonlocal paste_notice
        paste_notice = msg

    def _voice_render(_border, _line):
        # record_voice_input hands us a finished (border, status line) pair.
        _bw, _bh = console.size
        return _build_description_screen(
            input_lines,
            cursor_row,
            cursor_col,
            width=_bw,
            height=_bh,
            border_override=_border,
            status_line=_line,
        )

    def _run_voice() -> None:
        nonlocal cursor_row, cursor_col
        spoken = record_voice_input(live, console, _key, render_status=_voice_render)
        if not spoken:
            return
        voice_lines = spoken.split("\n")
        line = input_lines[cursor_row]
        tail = line[cursor_col:]
        input_lines[cursor_row] = line[:cursor_col] + voice_lines[0]
        cursor_col += len(voice_lines[0])
        for vl in voice_lines[1:]:
            cursor_row += 1
            input_lines.insert(cursor_row, vl)
            cursor_col = len(vl)
        input_lines[cursor_row] += tail

    _anim0 = time.monotonic()  # shimmer title clock
    while True:
        key = _key()
        if key and key != "":
            paste_notice = ""

        if key == "esc":
            return None
        elif key == "ctrl+v":
            # Reading the clipboard can take a moment (osascript hex round-trip)
            # — show progress before the blocking call so the UI never looks hung.
            w, h = console.size
            live.update(
                _build_description_screen(
                    input_lines, cursor_row, cursor_col, width=w, height=h, status_line="Pasting image…"
                )
            )
            chip = handle_ctrl_v(attachments, scope_id=scope_id or "planning", set_notice=_set_paste_notice)
            if chip:
                line = input_lines[cursor_row]
                input_lines[cursor_row] = line[:cursor_col] + chip + line[cursor_col:]
                cursor_col += len(chip)
                paste_notice = f"Screenshot attached as {chip}"
        elif key == "alt+enter":
            # Alt+Enter (Option+Enter on macOS) inserts a new line
            line = input_lines[cursor_row]
            input_lines[cursor_row] = line[:cursor_col]
            input_lines.insert(cursor_row + 1, line[cursor_col:])
            cursor_row += 1
            cursor_col = 0
        elif key == "enter":
            # Submit if there's any content
            text = "\n".join(input_lines).strip()
            if text:
                # Remove trailing empty lines
                while input_lines and not input_lines[-1].strip():
                    input_lines.pop()
                desc = "\n".join(input_lines)
                # Only attachments whose [image #N] chip survived editing are sent
                # — deleting a chip from the text detaches its screenshot.
                images = referenced_images(desc, attachments)
                logger.info("Description submitted: len=%d, images=%d", len(desc), len(images))
                return desc, input_lines, cursor_row, cursor_col, images
        elif key == "backspace":
            if cursor_col > 0:
                line = input_lines[cursor_row]
                input_lines[cursor_row] = line[: cursor_col - 1] + line[cursor_col:]
                cursor_col -= 1
            elif cursor_row > 0:
                # Merge with previous line
                prev_len = len(input_lines[cursor_row - 1])
                input_lines[cursor_row - 1] += input_lines[cursor_row]
                input_lines.pop(cursor_row)
                cursor_row -= 1
                cursor_col = prev_len
        elif key == "clear":
            input_lines = [""]
            cursor_row = 0
            cursor_col = 0
        elif key == "up":
            if cursor_row > 0:
                cursor_row -= 1
                cursor_col = min(cursor_col, len(input_lines[cursor_row]))
        elif key == "down":
            if cursor_row < len(input_lines) - 1:
                cursor_row += 1
                cursor_col = min(cursor_col, len(input_lines[cursor_row]))
        elif key == "left":
            if cursor_col > 0:
                cursor_col -= 1
            elif cursor_row > 0:
                cursor_row -= 1
                cursor_col = len(input_lines[cursor_row])
        elif key == "right":
            if cursor_col < len(input_lines[cursor_row]):
                cursor_col += 1
            elif cursor_row < len(input_lines) - 1:
                cursor_row += 1
                cursor_col = 0
        elif key == "shift+left":
            from yeaboi.ui.session.editor._editor_core import _word_boundary_left

            cursor_col = _word_boundary_left(input_lines[cursor_row], cursor_col)
        elif key == "shift+right":
            from yeaboi.ui.session.editor._editor_core import _word_boundary_right

            cursor_col = _word_boundary_right(input_lines[cursor_row], cursor_col)
        elif key == "word_backspace":
            from yeaboi.ui.session.editor._editor_core import _word_boundary_left

            word_start = _word_boundary_left(input_lines[cursor_row], cursor_col)
            line = input_lines[cursor_row]
            input_lines[cursor_row] = line[:word_start] + line[cursor_col:]
            cursor_col = word_start
        elif isinstance(key, str) and key.startswith("paste:"):
            pasted = paste_payload(key, multiline=True)
            paste_lines = pasted.split("\n")
            if paste_lines:
                # Insert first chunk at cursor
                line = input_lines[cursor_row]
                input_lines[cursor_row] = line[:cursor_col] + paste_lines[0]
                cursor_col += len(paste_lines[0])
                # Insert remaining lines
                for pl in paste_lines[1:]:
                    cursor_row += 1
                    input_lines.insert(cursor_row, pl)
                    cursor_col = len(pl)
                # Append remaining text from original line
                if len(paste_lines) > 1:
                    input_lines[cursor_row] += line[cursor_col - len(paste_lines[-1]) :]
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            line = input_lines[cursor_row]
            if key == " " and _dts.is_double(cursor_col > 0 and line[cursor_col - 1] == " ", time.monotonic()):
                # Double-tap Space → dictate; the first space stays as a separator
                # and the second one is swallowed by the gesture (not inserted).
                _run_voice()
            else:
                input_lines[cursor_row] = line[:cursor_col] + key + line[cursor_col:]
                cursor_col += 1
        elif key == "":
            pass  # timeout, no input
        else:
            continue

        w, h = console.size
        live.update(
            _build_description_screen(
                input_lines,
                cursor_row,
                cursor_col,
                width=w,
                height=h,
                shimmer_tick=time.monotonic() - _anim0,
                status_line=paste_notice,
            )
        )


# ---------------------------------------------------------------------------
# Phase B: Intake Questions
# ---------------------------------------------------------------------------


def _phase_intake_questions(
    live: Live,
    console: Console,
    graph,
    graph_state: dict,
    _key,
    export_only: bool,
    *,
    return_state_on_esc: bool = False,
) -> dict | None:
    """Loop through intake questions in TUI until questionnaire completes or enters review.

    Returns updated graph_state, or None if user cancelled. The chat driver's
    /form takeover passes return_state_on_esc=True: the node mutates the shared
    QuestionnaireState in place, so a None return would leave the caller holding
    recorded answers with stale `messages` — Esc must hand the loop's rebound
    graph_state back instead of discarding it.
    """
    logger.info("_phase_intake_questions started")
    while True:
        qs = graph_state.get("questionnaire")
        if isinstance(qs, QuestionnaireState):
            # Don't exit the intake loop while PTO sub-loop or editing is active —
            # _awaiting_leave_input means we're still collecting leave entries
            # within the confirmation gate, and editing_question means the user
            # is re-answering a question from the review screen.
            if (
                (qs.completed or qs.awaiting_confirmation)
                and not qs._awaiting_leave_input
                and qs.editing_question is None
            ):
                logger.info("Intake questions complete: completed=%s", qs.completed)
                return graph_state

        # Check what the next node will be — if not intake, hand off
        next_node = _predict_next_node(graph_state)
        if next_node != "project_intake":
            return graph_state

        # Determine current question context
        question_text = ""
        preamble_lines: list[str] = []
        choices: list[tuple[str, bool]] | None = None
        suggestion: str | None = None
        progress = ""
        phase_label = ""

        ai_msgs = graph_state.get("messages", [])
        if ai_msgs and isinstance(ai_msgs[-1], AIMessage):
            content = ai_msgs[-1].content
            preamble_parts, q_text = _split_intake_preamble(content)
            preamble_lines = preamble_parts
            question_text = q_text

        if isinstance(qs, QuestionnaireState) and not qs.completed:
            cur_q = qs.current_question
            phase = qs.current_phase
            phase_label = PHASE_LABELS.get(phase, "")
            if qs.intake_mode == "standard":
                progress = f"Q{cur_q} of {TOTAL_QUESTIONS}"
            suggestion = _get_active_suggestion(graph_state)

            # Choice options for single-choice and multi-choice questions.
            # When an extracted suggestion matches one of the options, pre-select
            # it instead of the static default — the user sees the arrow on the
            # extracted answer and can just press Enter to confirm.
            is_multi_select = False
            # Skip static choice rendering when PTO sub-loop is active — it
            # sets current_question=30 but shows its own Yes/No prompt text.
            in_pto_subloop = qs._awaiting_leave_input
            if is_choice_question(cur_q) and cur_q not in qs.probed_questions and not in_pto_subloop:
                meta = QUESTION_METADATA.get(cur_q)
                if meta and meta.question_type == "multi_choice":
                    # Multi-choice: pre-select options that match extracted/suggested values
                    is_multi_select = True
                    pre_selected: set[int] = set()
                    if suggestion:
                        # Suggestion may be comma-separated (e.g. "Backend, Frontend")
                        sugg_parts = {s.strip().lower() for s in suggestion.split(",")}
                        for i, opt in enumerate(meta.options):
                            if opt.lower() in sugg_parts:
                                pre_selected.add(i)
                        if pre_selected:
                            suggestion = None
                    choices = [(opt, i in pre_selected) for i, opt in enumerate(meta.options)]
                elif meta:
                    # Single-choice: highlight extracted suggestion > static default
                    pre_select_idx = meta.default_index
                    if suggestion:
                        sugg_lower = suggestion.lower().strip()
                        for i, opt in enumerate(meta.options):
                            if opt.lower().strip() == sugg_lower:
                                pre_select_idx = i
                                break
                    choices = [(opt, i == pre_select_idx) for i, opt in enumerate(meta.options)]
                    # Clear the text suggestion — the pre-selected option IS the
                    # suggestion now, so we don't need the two-step confirm flow.
                    if suggestion and pre_select_idx is not None:
                        suggestion = None

            # Dynamic choices — follow-up probes or node-generated options (e.g. Q27 sprint selection)
            # Skip during PTO sub-loop — it uses its own prompt text, not Q28's bank holiday choices.
            follow_up_choices = qs._follow_up_choices.get(cur_q)
            if follow_up_choices and not in_pto_subloop:
                choices = [(opt, False) for opt in follow_up_choices]
                # Tracker choice and sprint selection are single-select;
                # team member selection (Q6) is multi-select.
                _single_select_qs = {27}  # Q27 sprint selection = pick one
                if getattr(qs, "_awaiting_tracker_choice", False):
                    is_multi_select = False
                elif cur_q in _single_select_qs:
                    is_multi_select = False
                else:
                    is_multi_select = True

        # If no question text from AI, use a generic prompt
        if not question_text:
            question_text = "Tell me about your project \u2014 what are you building and why?"

        # Show question screen and get user input
        answer = _question_input_loop(
            live,
            console,
            _key,
            question_text=question_text,
            choices=choices,
            suggestion=suggestion,
            progress=progress,
            phase_label=phase_label,
            preamble_lines=preamble_lines,
            export_only=export_only,
            graph_state=graph_state,
            questionnaire=qs if isinstance(qs, QuestionnaireState) else None,
            multi_select=is_multi_select,
        )

        if answer is None:
            logger.info("Intake questions exit: esc (return_state=%s)", return_state_on_esc)
            return graph_state if return_state_on_esc else None

        # Resolve choice/suggestion input
        if isinstance(qs, QuestionnaireState) and not qs.completed:
            cur_q = qs.current_question
            # Handle suggestion confirmation — two-step flow:
            # 1st Enter: accept the suggestion (pre-fill it as the answer)
            # 2nd Enter: submit the accepted answer
            # This gives the user a chance to review/edit before submitting.
            if not answer or answer.lower() in _SUGGEST_CONFIRM:
                sugg = _get_active_suggestion(graph_state)
                if sugg:
                    # Re-show the question with the suggestion pre-filled as
                    # editable input text (no longer dimmed). The user presses
                    # Enter again to confirm, or edits before submitting.
                    answer = _question_input_loop(
                        live,
                        console,
                        _key,
                        question_text=question_text,
                        choices=choices,
                        suggestion=None,  # hide suggestion — it's now in input_value
                        progress=progress,
                        phase_label=phase_label,
                        preamble_lines=preamble_lines,
                        export_only=export_only,
                        graph_state=graph_state,
                        questionnaire=qs,
                        multi_select=is_multi_select,
                        prefill=sugg,
                    )
                    if answer is None:
                        logger.info("Intake questions exit: esc (return_state=%s)", return_state_on_esc)
                        return graph_state if return_state_on_esc else None

            # Resolve numeric choice
            if qs.editing_question is not None:
                answer = _resolve_choice_input(answer, qs.editing_question)
            elif not qs.awaiting_confirmation:
                dynamic_choices = qs._follow_up_choices.get(cur_q)
                if dynamic_choices:
                    answer = _resolve_dynamic_choice(answer, dynamic_choices)
                else:
                    answer = _resolve_choice_input(answer, cur_q)

        if not answer:
            # Empty enter on Q2 repo URL follow-up → treat as skip
            if isinstance(qs, QuestionnaireState) and qs.current_question in qs.probed_questions:
                answer = "skip"
            else:
                continue

        # Invoke graph with the answer
        user_msg = HumanMessage(content=answer)
        invoke_state = {**graph_state, "messages": [*graph_state.get("messages", []), user_msg]}

        # Animate the input box border with green/white cycling while the LLM processes.
        # When we have a QuestionnaireState, pass it so _invoke_with_animation uses
        # the accordion screen for the loading animation.
        screen_kwargs: dict = {
            "question_text": question_text,
            "input_value": answer,
            "choices": choices,
            "suggestion": suggestion,
            "progress": progress,
            "phase_label": phase_label,
            "selected_choice": next((i for i, (_, is_def) in enumerate(choices) if is_def), 0) if choices else 0,
        }
        if isinstance(qs, QuestionnaireState):
            screen_kwargs["questionnaire"] = qs
        else:
            screen_kwargs["preamble_lines"] = preamble_lines

        logger.info("Intake answer submitted: Q%s", cur_q if isinstance(qs, QuestionnaireState) else "?")
        result = _invoke_with_animation(
            live,
            console,
            graph,
            invoke_state,
            "Processing your answer",
            "",
            question_screen_kwargs=screen_kwargs,
        )
        if result is None:
            logger.info("Intake questions exit: invoke cancelled (return_state=%s)", return_state_on_esc)
            return graph_state if return_state_on_esc else None

        graph_state = result


def _question_input_loop(
    live: Live,
    console: Console,
    _key,
    *,
    question_text: str,
    choices: list[tuple[str, bool]] | None,
    suggestion: str | None,
    progress: str,
    phase_label: str,
    preamble_lines: list[str] | None,
    export_only: bool,
    graph_state: dict,
    questionnaire: QuestionnaireState | None = None,
    multi_select: bool = False,
    prefill: str = "",
) -> str | None:
    """Show a question screen and collect user input.

    Returns the answer string, or None if Esc pressed.
    For export_only mode, returns a synthetic answer.

    When questionnaire is provided, uses the accordion-style screen showing
    all 26 questions at once. Otherwise falls back to the single-question screen.

    prefill: Pre-populate the input box with this text (e.g. an accepted
        suggestion the user can review/edit before submitting).
    """
    if export_only:
        # Auto-answer: use suggestion or "continue"
        # Late import so tests can patch yeaboi.ui.session._get_active_suggestion
        from yeaboi.ui import session as _session_mod

        sugg = _session_mod._get_active_suggestion(graph_state)
        return sugg or "continue"

    # Pre-fill input: explicit prefill takes priority, then editing an existing answer.
    input_value = prefill
    if not input_value and questionnaire is not None and questionnaire.editing_question is not None:
        existing = questionnaire.answers.get(questionnaire.editing_question, "")
        if existing:
            input_value = existing
    # Cursor at start for prefilled suggestions (so user sees beginning of long text),
    # at end for editing existing answers (user typically appends).
    cursor_pos = 0 if prefill else len(input_value)
    # Start on the pre-selected option (extracted suggestion or static default) if any
    selected_choice = 0
    if choices:
        for i, (_opt, is_default) in enumerate(choices):
            if is_default:
                selected_choice = i
                break
    selected_choices: set[int] = set()  # for multi-select mode
    scroll_offset = 0
    use_accordion = questionnaire is not None
    _anim0 = time.monotonic()  # shimmer title clock

    # Ctrl+V image paste (free-text questions only) — surviving [image #N] chips
    # extend graph_state["pasted_images"] at submit so project_analyzer sees them.
    # Initialized BEFORE _render(): the closure reads paste_notice on every frame.
    from yeaboi.ui.shared._attachments import handle_ctrl_v, referenced_images

    attachments: list[str] = []
    paste_notice = ""

    def _set_paste_notice(msg: str) -> None:
        nonlocal paste_notice
        paste_notice = msg

    def _collect_pasted_images(answer: str) -> None:
        imgs = referenced_images(answer, attachments)
        if imgs:
            graph_state["pasted_images"] = list(graph_state.get("pasted_images") or []) + imgs
            logger.info("questionnaire answer submitted with %d pasted image(s)", len(imgs))

    def _render():
        w, h = console.size
        if use_accordion:
            return _build_accordion_question_screen(
                question_text,
                input_value,
                questionnaire,
                choices=choices,
                suggestion=suggestion,
                progress=progress,
                phase_label=phase_label,
                selected_choice=selected_choice,
                selected_choices=selected_choices if multi_select else None,
                scroll_offset=scroll_offset,
                width=w,
                height=h,
                cursor_pos=cursor_pos,
                shimmer_tick=time.monotonic() - _anim0,
                edit_hint=paste_notice
                or (
                    "Space toggle \u00b7 Enter submit"
                    if multi_select
                    else ("Enter/Ctrl+S submit \u00b7 Esc cancel" + ("" if choices else _voice_hint() + _image_hint()))
                ),
            )
        return _build_question_screen(
            question_text,
            input_value,
            choices=choices,
            suggestion=suggestion,
            progress=progress,
            phase_label=phase_label,
            preamble_lines=preamble_lines,
            selected_choice=selected_choice,
            width=w,
            height=h,
            shimmer_tick=time.monotonic() - _anim0,
            status_line=paste_notice,
        )

    live.update(_render())

    # Voice input: double-tap Space to dictate (free-text questions only).
    from yeaboi.ui.shared._voice_input import DoubleTapSpace, record_voice_input

    _dts = DoubleTapSpace()

    def _voice_render(_border, _line):
        _bw, _bh = console.size
        if use_accordion:
            return _build_accordion_question_screen(
                question_text,
                input_value,
                questionnaire,
                choices=choices,
                suggestion=suggestion,
                progress=progress,
                phase_label=phase_label,
                selected_choice=selected_choice,
                selected_choices=selected_choices if multi_select else None,
                scroll_offset=scroll_offset,
                width=_bw,
                height=_bh,
                cursor_pos=cursor_pos,
                border_override=_border,
                edit_hint=_line,
            )
        return _build_question_screen(
            question_text,
            input_value,
            choices=choices,
            suggestion=suggestion,
            progress=progress,
            phase_label=phase_label,
            preamble_lines=preamble_lines,
            selected_choice=selected_choice,
            width=_bw,
            height=_bh,
            border_override=_border,
            status_line=_line,
        )

    def _run_voice() -> None:
        nonlocal input_value, cursor_pos
        spoken = record_voice_input(live, console, _key, render_status=_voice_render)
        if spoken:
            input_value = input_value[:cursor_pos] + spoken + input_value[cursor_pos:]
            cursor_pos += len(spoken)

    while True:
        key = _key()
        if key and key != "":
            paste_notice = ""

        if key == "esc":
            return None
        elif key in ("enter", "ctrl+s"):
            # Enter or Ctrl+S submits the answer
            if choices:
                if multi_select:
                    # Submit all selected items, or current item if none toggled
                    if selected_choices:
                        selected_labels = [choices[i][0] for i in sorted(selected_choices)]
                        return ", ".join(selected_labels)
                    return choices[selected_choice][0]
                return choices[selected_choice][0]
            _collect_pasted_images(input_value)
            return input_value
        elif key == " " and choices and multi_select:
            # Space toggles selection in multi-select mode
            if selected_choice in selected_choices:
                selected_choices.discard(selected_choice)
            else:
                selected_choices.add(selected_choice)
        elif key in ("up", "scroll_up") and choices:
            selected_choice = (selected_choice - 1) % len(choices)
        elif key in ("down", "scroll_down") and choices:
            selected_choice = (selected_choice + 1) % len(choices)
        elif key == "":
            pass  # idle timeout — fall through to re-render so the title shimmer stays live
        elif choices:
            # Choice questions are arrow-key only — no typing
            continue
        elif key == "left":
            cursor_pos = max(0, cursor_pos - 1)
        elif key == "right":
            cursor_pos = min(len(input_value), cursor_pos + 1)
        elif key == "backspace":
            if cursor_pos > 0:
                input_value = input_value[: cursor_pos - 1] + input_value[cursor_pos:]
                cursor_pos -= 1
        elif key == "clear":
            input_value = ""
            cursor_pos = 0
        elif key == "shift+left":
            from yeaboi.ui.session.editor._editor_core import _word_boundary_left

            cursor_pos = _word_boundary_left(input_value, cursor_pos)
        elif key == "shift+right":
            from yeaboi.ui.session.editor._editor_core import _word_boundary_right

            cursor_pos = _word_boundary_right(input_value, cursor_pos)
        elif key == "word_backspace":
            from yeaboi.ui.session.editor._editor_core import _word_boundary_left

            word_start = _word_boundary_left(input_value, cursor_pos)
            input_value = input_value[:word_start] + input_value[cursor_pos:]
            cursor_pos = word_start
        elif isinstance(key, str) and key.startswith("paste:"):
            pasted = paste_payload(key)
            input_value = input_value[:cursor_pos] + pasted + input_value[cursor_pos:]
            cursor_pos += len(pasted)
        elif key == "ctrl+v":
            paste_notice = "Pasting image…"
            live.update(_render())
            chip = handle_ctrl_v(
                attachments,
                scope_id=graph_state.get("_attachment_scope") or "planning",
                set_notice=_set_paste_notice,
            )
            if chip:
                input_value = input_value[:cursor_pos] + chip + input_value[cursor_pos:]
                cursor_pos += len(chip)
                paste_notice = f"Screenshot attached as {chip}"
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            if key == " " and _dts.is_double(cursor_pos > 0 and input_value[cursor_pos - 1] == " ", time.monotonic()):
                # Double-tap Space → dictate; the first space stays as a separator.
                _run_voice()
            else:
                input_value = input_value[:cursor_pos] + key + input_value[cursor_pos:]
                cursor_pos += 1
        elif key == "":
            pass
        else:
            continue

        live.update(_render())
