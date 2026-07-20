"""Import/export the intake questionnaire as a Markdown file.

# See docs: "Project Intake Questionnaire" — offline workflow
#
# This module handles serialization of the 30-question questionnaire to/from
# Markdown format. Users can export a blank template (or current answers),
# fill it in offline in any editor, then import the completed file to skip
# the interactive one-at-a-time flow.
#
# Three public functions:
#   export_questionnaire_md — write a .md file with all 30 questions
#   parse_questionnaire_md  — read a .md file back into a {q_num: answer} dict
#   build_questionnaire_from_answers — convert parsed answers into QuestionnaireState
#
# The Markdown format mirrors _build_intake_summary() from nodes.py:
#   ## Phase N: Label
#   **Q1.** Question text
#   > answer here
#
# This makes the exported file round-trippable: export → edit → import
# preserves answers exactly.
"""

import logging
import re
from pathlib import Path

from yeaboi.agent.state import (
    PHASE_QUESTION_RANGES,
    TOTAL_QUESTIONS,
    QuestionnaireState,
)
from yeaboi.prompts.intake import ESSENTIAL_QUESTIONS, INTAKE_QUESTIONS, PHASE_LABELS, QUESTION_DEFAULTS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

_TEMPLATE_HEADER = """\
# yeaboi.ai — Project Intake Questionnaire

Fill in your answers after the `> ` on each question.
Multi-line answers are supported — start each line with `> `.
To skip a question, leave the `> ` line blank or write `> skip`.

**Essential questions** (must be answered for best results): {essential}

---

"""


def export_questionnaire_md(questionnaire: QuestionnaireState | None, path: Path) -> Path:
    """Export the questionnaire as a Markdown file.

    Args:
        questionnaire: Current questionnaire state, or None for a blank template.
        path: File path to write to.

    Returns:
        The resolved path that was written.
    """
    path = Path(path).resolve()
    essential_list = ", ".join(f"Q{q}" for q in sorted(ESSENTIAL_QUESTIONS))
    lines = [_TEMPLATE_HEADER.format(essential=essential_list)]

    for phase, (start, end) in PHASE_QUESTION_RANGES.items():
        label = PHASE_LABELS[phase]
        lines.append(f"## {label}\n\n")
        for q_num in range(start, end + 1):
            question = INTAKE_QUESTIONS[q_num]
            lines.append(f"**Q{q_num}.** {question}\n")
            # Fill in the answer if questionnaire state is provided
            answer = questionnaire.answers.get(q_num) if questionnaire else None
            if answer:
                # Multi-line answers: each line gets a `> ` prefix
                for answer_line in answer.split("\n"):
                    lines.append(f"> {answer_line}\n")
            else:
                lines.append("> \n")
            lines.append("\n")
        lines.append("---\n\n")

    path.write_text("".join(lines))
    q_count = len(questionnaire.answers) if questionnaire else 0
    logger.info("Exported questionnaire to %s (%d answered)", path, q_count)
    return path


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

# Matches lines like "**Q12.** some question text"
_Q_HEADER_RE = re.compile(r"^\*\*Q(\d+)\.\*\*")


def parse_questionnaire_md(path: Path) -> dict[int, str]:
    """Parse a filled-in questionnaire Markdown file.

    Uses a line-by-line state machine: detects `**Q(\\d+).**` header lines,
    then accumulates subsequent `> ` lines as the answer until the next header
    or end of file.

    Args:
        path: Path to the Markdown file.

    Returns:
        Dict mapping question number (1-based) to answer text.

    Raises:
        FileNotFoundError: If path does not exist.
        ValueError: If zero valid question/answer pairs are found.
    """
    path = Path(path)
    text = path.read_text()
    lines = text.splitlines()

    answers: dict[int, str] = {}
    current_q: int | None = None
    current_lines: list[str] = []

    def _flush() -> None:
        """Save accumulated answer lines for the current question."""
        nonlocal current_q, current_lines
        if current_q is not None:
            # Join blockquote lines, strip the `> ` prefix
            answer = "\n".join(current_lines).strip()
            # Skip empty answers and the literal "skip" keyword
            if answer and answer.lower() != "skip":
                if 1 <= current_q <= TOTAL_QUESTIONS:
                    answers[current_q] = answer
        current_q = None
        current_lines = []

    for line in lines:
        match = _Q_HEADER_RE.match(line)
        if match:
            _flush()
            current_q = int(match.group(1))
            continue

        # Accumulate blockquote lines for the current question
        if current_q is not None and line.startswith("> "):
            current_lines.append(line[2:])
        elif current_q is not None and line == ">":
            # Bare `>` is an empty blockquote line (blank line in multi-line answer)
            current_lines.append("")

    # Flush the last question
    _flush()

    if not answers:
        raise ValueError("No valid question/answer pairs found in the file")

    logger.info("Parsed questionnaire from %s (%d answers)", path, len(answers))
    return answers


# ---------------------------------------------------------------------------
# Build QuestionnaireState from parsed answers
# ---------------------------------------------------------------------------


def build_questionnaire_from_answers(parsed_answers: dict[int, str]) -> QuestionnaireState:
    """Convert parsed answers into a QuestionnaireState ready for confirmation.

    For each Q1–Q26:
    - Answer found → stored in `answers`
    - No answer + has default → default applied, question added to `defaulted_questions`
    - No answer + no default (essential) → added to `skipped_questions`

    The resulting state has `awaiting_confirmation=True` and `current_question`
    set past the last question, so the intake node will show the summary.

    # See docs: "Project Intake Questionnaire" — confirmation gate
    """
    qs = QuestionnaireState()
    qs.current_question = TOTAL_QUESTIONS + 1
    qs.awaiting_confirmation = True
    qs.completed = False

    for q_num in range(1, TOTAL_QUESTIONS + 1):
        if q_num in parsed_answers:
            qs.answers[q_num] = parsed_answers[q_num]
        elif q_num in QUESTION_DEFAULTS:
            # Apply sensible default and track it
            qs.answers[q_num] = QUESTION_DEFAULTS[q_num]
            qs.defaulted_questions.add(q_num)
        else:
            # Essential question with no answer — flag as skipped
            qs.skipped_questions.add(q_num)

    return qs
