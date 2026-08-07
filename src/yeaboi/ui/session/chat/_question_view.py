"""Derive what the intake node expects next — pure, testable, no rendering.

This is the choice/suggestion/preamble derivation that _phase_intake_questions
inlined (see _phases_intake.py history); the chat driver needs the same facts
to draw inline choice rows under the latest bubble. Behavior-identical by
design: these branches encode the private QuestionnaireState machinery (PTO
sub-loop, tracker choice, Q27 sprint selection, probed questions), and any
drift here changes which answer string reaches the node — i.e. planning
results. Keep changes mirrored with the recorded-state tests.

One sanctioned exception: CHAT_MODE_HIDDEN_CHOICES hides rows the pre-graph
size answer made redundant (Q10's "1–2 sprints" after the user chose Large),
so the chat can show fewer rows than the REPL/form for the same question.
Typed digits stay consistent because _resolve_choice passes the displayed
labels to _resolve_choice_input — the answer string itself is always one of
the canonical meta.options.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage

from yeaboi.agent.state import TOTAL_QUESTIONS, QuestionnaireState
from yeaboi.prompts.intake import (
    CHAT_MODE_HIDDEN_CHOICES,
    PHASE_LABELS,
    QUESTION_METADATA,
    AnswerSource,
    is_choice_question,
)
from yeaboi.repl._io import _get_active_suggestion
from yeaboi.repl._questionnaire import _split_intake_preamble

logger = logging.getLogger(__name__)

# Q27 (sprint selection) is single-select among the dynamic follow-up menus;
# Q6 member selection is multi-select. Same table as the phase loop used.
_SINGLE_SELECT_DYNAMIC_QS = {27}

# Answer sources that mean the user typed/picked it themselves — the questions
# that were genuinely ASKED this run, as opposed to filled by extraction,
# SCRUM.md, or defaults.
_USER_ANSWERED_SOURCES = (AnswerSource.DIRECT, AnswerSource.PROBED)

# Confirmation-gate choice labels. The driver maps a pick to the node literal
# ("accept"/"override") or handles it locally — the raw label must NEVER reach
# the graph: _parse_edit_intent reads a bare digit as "edit QN", and
# _is_confirm_intent doesn't know these strings.
CONFIRM_ACCEPT = "Accept — build the plan"
CONFIRM_EDIT = "Edit an answer…"
CONFIRM_OVERRIDE_VELOCITY = "Override the velocity…"
CONFIRM_FREETEXT = "Tell me what's off…"


def planned_question_sets(qs: QuestionnaireState) -> tuple[list[int], set[int]] | None:
    """(remaining gaps, user-answered) — the questions this run actually asks.

    Extraction, SCRUM.md and defaults silently answer most of the 30-question
    bank; only the essential gaps get asked. This reuses the same
    _find_essential_gaps the node drives the flow with, so it always agrees
    with its "(N remaining)" copy. None when the derivation fails (callers
    keep a fallback).
    """
    try:
        # Lazy: nodes is a heavy module and imports back into UI-adjacent
        # territory — same precedent as the driver's apply_size_switch import.
        from yeaboi.agent.nodes import _essentials_for_mode, _find_essential_gaps

        remaining = _find_essential_gaps(qs, _essentials_for_mode(qs.intake_mode))
    except Exception:  # pragma: no cover — a render must never die on this
        logger.warning("planned_question_sets: gap derivation failed", exc_info=True)
        return None
    asked = {q for q, src in qs.answer_sources.items() if src in _USER_ANSWERED_SOURCES}
    return remaining, asked


def planned_question_progress(qs: QuestionnaireState) -> tuple[int, int] | None:
    """(position, total) over the questions actually planned for this run.

    "Q7 of 30" lies — see planned_question_sets. Recomputed every turn: a
    CONDITIONAL_ESSENTIALS promotion growing the total mid-run is honesty,
    not drift. None when the count can't be derived (caller keeps a fallback).
    """
    sets = planned_question_sets(qs)
    if sets is None:
        return None
    remaining, asked = sets
    planned = set(remaining) | asked
    if not planned:
        return None
    done = len(planned) - len(remaining)
    return min(done + 1, len(planned)), len(planned)


@dataclass
class QuestionView:
    """Everything the chat needs to present the current intake prompt."""

    question_text: str = ""
    preamble_lines: list[str] = field(default_factory=list)
    choices: list[tuple[str, bool]] | None = None  # (label, pre_selected)
    multi_select: bool = False
    auto_submit: bool = False  # bare digit picks + submits (command menus only)
    suggestion: str | None = None  # free-text prefill (chip suggestions become prefill)
    progress: str = ""  # "Question 2 of 6" over the planned set (chat shows it for every mode)
    phase_label: str = ""
    current_question: int = 0


def derive_question_view(graph_state: dict) -> QuestionView:
    """Inspect graph_state and describe the pending intake prompt.

    Mirrors the derivation the phase loop performed inline; consumed by the
    chat driver to render choices and resolve replies via
    _resolve_choice_input/_resolve_dynamic_choice exactly as before.
    """
    view = QuestionView()

    messages = graph_state.get("messages", [])
    if messages and isinstance(messages[-1], AIMessage):
        preamble_parts, question_text = _split_intake_preamble(messages[-1].content)
        view.preamble_lines = preamble_parts
        view.question_text = question_text

    qs = graph_state.get("questionnaire")
    if not isinstance(qs, QuestionnaireState) or qs.completed:
        return view

    cur_q = qs.current_question
    view.current_question = cur_q
    view.phase_label = PHASE_LABELS.get(qs.current_phase, "")

    # Confirmation gate: the summary card's verdict is a pick, not prose.
    # Guard mirrors _append_reply's card condition exactly — the choices must
    # appear iff the card+prompt did, and never during the PTO sub-loop,
    # velocity number entry, or an edit re-ask.
    if (
        qs.awaiting_confirmation
        and not qs._awaiting_leave_input
        and not qs._awaiting_velocity_input
        and qs.editing_question is None
        and cur_q > TOTAL_QUESTIONS
    ):
        labels = [CONFIRM_ACCEPT, CONFIRM_EDIT]
        if qs.intake_mode != "small_project":
            # Small mode has no capacity/velocity machinery to override.
            labels.append(CONFIRM_OVERRIDE_VELOCITY)
        labels.append(CONFIRM_FREETEXT)
        view.choices = [(label, i == 0) for i, label in enumerate(labels)]
        view.multi_select = False
        view.auto_submit = True
        return view
    if 1 <= cur_q <= TOTAL_QUESTIONS:
        planned = planned_question_progress(qs)
        if planned:
            view.progress = f"Question {planned[0]} of {planned[1]}"
        else:
            view.progress = f"Q{cur_q} of {TOTAL_QUESTIONS}"
    view.suggestion = _get_active_suggestion(graph_state)

    # PTO sub-loop: current_question points at the leave section but the node
    # shows its own Yes/No prompt text — no static choice rendering.
    in_pto_subloop = qs._awaiting_leave_input

    if is_choice_question(cur_q) and cur_q not in qs.probed_questions and not in_pto_subloop:
        meta = QUESTION_METADATA.get(cur_q)
        if meta and meta.question_type == "multi_choice":
            view.multi_select = True
            pre_selected: set[int] = set()
            if view.suggestion:
                # Suggestion may be comma-separated (e.g. "Backend, Frontend")
                sugg_parts = {s.strip().lower() for s in view.suggestion.split(",")}
                for i, opt in enumerate(meta.options):
                    if opt.lower() in sugg_parts:
                        pre_selected.add(i)
                if pre_selected:
                    view.suggestion = None
            view.choices = [(opt, i in pre_selected) for i, opt in enumerate(meta.options)]
        elif meta:
            # Single-choice: pre-select extracted suggestion > static default.
            pre_select_idx = meta.default_index
            if view.suggestion:
                sugg_lower = view.suggestion.lower().strip()
                for i, opt in enumerate(meta.options):
                    if opt.lower().strip() == sugg_lower:
                        pre_select_idx = i
                        break
            view.choices = [(opt, i == pre_select_idx) for i, opt in enumerate(meta.options)]
            # The pre-selected option IS the suggestion — drop the text form.
            if view.suggestion and pre_select_idx is not None:
                view.suggestion = None

    # Chat-only: hide rows made redundant by the pre-graph size answer
    # (e.g. "1–2 sprints" after the user already chose Large). A pre-selected
    # row survives — hiding it would silently drop an extracted suggestion.
    hidden = CHAT_MODE_HIDDEN_CHOICES.get((cur_q, qs.intake_mode or ""))
    if hidden and view.choices:
        before = len(view.choices)
        view.choices = [(opt, sel) for opt, sel in view.choices if opt not in hidden or sel]
        if len(view.choices) != before:
            # Once per question turn, never per frame — the caller derives the
            # view when the intake advances, not while rendering.
            logger.debug(
                "chat: hid %d choice row(s) for Q%d in %s mode", before - len(view.choices), cur_q, qs.intake_mode
            )

    # Dynamic choices — follow-up probes or node-generated menus (tracker
    # choice, Q27 sprint pick, Q6 member select). They override static ones.
    follow_up_choices = qs._follow_up_choices.get(cur_q)
    if follow_up_choices and not in_pto_subloop:
        view.choices = [(opt, False) for opt in follow_up_choices]
        if getattr(qs, "_awaiting_tracker_choice", False):
            view.multi_select = False
        elif cur_q in _SINGLE_SELECT_DYNAMIC_QS:
            view.multi_select = False
        else:
            view.multi_select = True

    return view
