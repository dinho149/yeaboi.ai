"""Derive what the intake node expects next — pure, testable, no rendering.

This is the choice/suggestion/preamble derivation that _phase_intake_questions
inlined (see _phases_intake.py history); the chat driver needs the same facts
to draw inline choice rows under the latest bubble. Behavior-identical by
design: these branches encode the private QuestionnaireState machinery (PTO
sub-loop, tracker choice, Q27 sprint selection, probed questions), and any
drift here changes which answer string reaches the node — i.e. planning
results. Keep changes mirrored with the recorded-state tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.messages import AIMessage

from yeaboi.agent.state import TOTAL_QUESTIONS, QuestionnaireState
from yeaboi.prompts.intake import PHASE_LABELS, QUESTION_METADATA, is_choice_question
from yeaboi.repl._io import _get_active_suggestion
from yeaboi.repl._questionnaire import _split_intake_preamble

# Q27 (sprint selection) is single-select among the dynamic follow-up menus;
# Q6 member selection is multi-select. Same table as the phase loop used.
_SINGLE_SELECT_DYNAMIC_QS = {27}


@dataclass
class QuestionView:
    """Everything the chat needs to present the current intake prompt."""

    question_text: str = ""
    preamble_lines: list[str] = field(default_factory=list)
    choices: list[tuple[str, bool]] | None = None  # (label, pre_selected)
    multi_select: bool = False
    suggestion: str | None = None  # free-text prefill (chip suggestions become prefill)
    progress: str = ""  # "Q7 of 30" (chat shows it for every mode)
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
    if 1 <= cur_q <= TOTAL_QUESTIONS:
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
