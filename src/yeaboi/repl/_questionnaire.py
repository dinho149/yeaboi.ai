"""Questionnaire UI — constants, choice rendering, and dynamic follow-up resolution."""

import logging
import re

from rich.console import Console

from yeaboi.agent.state import QuestionnaireState
from yeaboi.prompts.intake import INTAKE_QUESTIONS, QUESTION_METADATA, is_choice_question

logger = logging.getLogger(__name__)

# See docs: "Scrum Standards" — questionnaire phases
# UI-only hint shown below each intake question so users know they can skip.
SKIP_HINT = "[hint](type 'skip' or 'I don't know' to skip this question)[/hint]"

# ── Chat attribution labels ──────────────────────────────────────
# Visual distinction between user messages and AI responses in the
# scrollback. REPL-only decoration — not stored in message history.
# Chat attribution labels — use semantic style names so theme colors apply.
USER_LABEL = "[user.label]You:[/user.label]"
AI_LABEL = "[ai.label]Scrum AI:[/ai.label]"
AI_QUESTION_LABEL = "[ai.question]Scrum AI[/ai.question] [hint](question)[/hint][ai.question]:[/ai.question]"

# Shown when a question has a suggested answer from the initial description.
# The user can press Enter or type Y to confirm, or type a different answer.
SUGGEST_HINT = "[command](press Enter or Y to accept, or type a different answer)[/command]"

# Keywords that confirm a suggested answer.
_SUGGEST_CONFIRM: frozenset[str] = frozenset({"y", "yes"})

# Randomised warm confirmations — keeps the REPL from feeling robotic.
# Each tuple is (prefix, suffix) so the answer text goes in between.
_CONFIRMATIONS: tuple[tuple[str, str], ...] = (
    ("Got it!", "— I'll factor that in."),
    ("Nice!", "— noted!"),
    ("Perfect,", "— I'll work with that."),
    ("Okay!", "— that's clear."),
    ("Great,", "— thanks for the detail!"),
    ("Understood!", "— I'll include that."),
    ("Right,", "— makes sense!"),
    ("Good to know!", "— I'll keep that in mind."),
)


def _warm_confirm(text: str) -> str:
    """Return a Rich-formatted confirmation string with a random warm phrase.

    Args:
        text: The resolved answer text to echo back.
    """
    import random

    prefix, suffix = random.choice(_CONFIRMATIONS)  # noqa: S311 — not security-sensitive
    return f"[success]{prefix}[/success] {text} {suffix}"


# Shown when the user is re-answering a question via the edit flow.
# See docs: "Project Intake Questionnaire" — edit flow
EDIT_HINT = "[hint](enter your new answer, or type 'skip' to keep the current one)[/hint]"

# Shown below dynamic follow-up choices so the user knows they can type freely.
# See docs: "Project Intake Questionnaire" — follow-up probing
FOLLOW_UP_CHOICE_HINT = "[hint](pick a number, or type your own answer)[/hint]"

# Hint shown below sprint selection options.
SPRINT_CHOICE_HINT = "[hint](pick 1–4, or type a sprint number)[/hint]"

# ── Intake message styling ─────────────────────────────────────────────────
# In smart/quick mode the AI concatenates context (extraction summary, remaining
# count) with the actual question into a single AIMessage. These patterns match
# the context lines so we can render them dimmed, letting the question stand out.

_PREAMBLE_PATTERNS = (
    # Smart/quick first invocation: "I **N** extracted … and **N** filled …"
    re.compile(r"^I (?:\*\*\d+\*\* (?:extracted|filled|picked)[^\n]*\.)\s*$", re.MULTILINE),
    # Remaining count: "A few more questions (N remaining):" or "One more question:"
    re.compile(r"^(?:A few more questions \(\d+ remaining\)|One more question):\s*$", re.MULTILINE),
    # Standard mode phase header: "**Phase Label**" (bold markdown line on its own)
    re.compile(r"^\*\*[^*]+\*\*\s*$", re.MULTILINE),
    # Follow-up probe label: "**Follow-up on QN:**" on its own line
    re.compile(r"^\*\*Follow-up on Q\d+:\*\*\s*$", re.MULTILINE),
)


def _split_intake_preamble(content: str) -> tuple[list[str], str]:
    """Split an intake AI message into styled preamble lines and question text.

    Scans from the top of the message, matching known preamble patterns
    (extraction summaries, remaining-question counts, phase headers, follow-up
    labels).  Everything before the first unmatched paragraph is preamble;
    everything after is the question to stream.

    Returns:
        (preamble_lines, question_text) — preamble may be empty.
    """
    paragraphs = content.split("\n\n")
    preamble: list[str] = []
    for i, para in enumerate(paragraphs):
        stripped = para.strip()
        if not stripped:
            continue
        if any(pat.search(stripped) for pat in _PREAMBLE_PATTERNS):
            preamble.append(stripped)
        else:
            # First non-matching paragraph — everything from here is the question.
            return preamble, "\n\n".join(paragraphs[i:])
    # Everything matched — unlikely, but return content as question to be safe.
    return preamble, content


def _render_choice_options(console: Console, q_num: int, *, option_labels: tuple[str, ...] | None = None) -> None:
    """Render a numbered option menu for a single-choice question.

    # See docs: "Project Intake Questionnaire" — selection menus
    #
    # Shows options like:  [1] Greenfield  [2] Existing codebase  [3] Hybrid
    # The default option (if any) gets a *(default)* suffix.

    Args:
        console: Rich Console instance for output.
        q_num: The 1-based question number to render options for.
        option_labels: Optional override labels for display. When provided,
            these are shown instead of meta.options (same length required).
            The underlying meta.options are still used for resolution.
    """
    meta = QUESTION_METADATA.get(q_num)
    if meta is None or meta.question_type != "single_choice":
        return

    labels = option_labels if option_labels and len(option_labels) == len(meta.options) else meta.options
    parts: list[str] = []
    for i, option in enumerate(labels):
        marker = " [hint]*(default)*[/hint]" if i == meta.default_index else ""
        parts.append(f"  [command]\\[{i + 1}][/command] {option}{marker}")
    console.print("\n".join(parts))


def _resolve_choice_input(user_input: str, q_num: int) -> str:
    """Resolve numeric input to the corresponding option text for choice questions.

    If the input is a valid number for a choice question, returns the option text.
    Otherwise returns the original input unchanged.

    Args:
        user_input: The raw user input (stripped).
        q_num: The current question number.

    Returns:
        The resolved option text, or the original input if not a choice number.
    """
    meta = QUESTION_METADATA.get(q_num)
    if meta is None or meta.question_type != "single_choice":
        return user_input

    try:
        idx = int(user_input)
    except ValueError:
        logger.debug("_resolve_choice_input: free-text for Q%d", q_num)
        return user_input

    if 1 <= idx <= len(meta.options):
        resolved = meta.options[idx - 1]
        logger.debug("_resolve_choice_input: Q%d idx=%d -> %s", q_num, idx, resolved)
        return resolved
    return user_input


def _render_dynamic_choices(console: Console, choices: tuple[str, ...]) -> None:
    """Render LLM-generated follow-up choices as a numbered menu.

    # See docs: "Project Intake Questionnaire" — follow-up probing
    #
    # When a vague answer triggers a follow-up probe, the LLM provides 2-4
    # contextual options. This renders them identically to static choice menus
    # (e.g. Q2 project type) so the UX is consistent. The user can pick a
    # number or type their own answer.

    Args:
        console: Rich Console instance for output.
        choices: Tuple of 2-4 option strings from _check_vague_answer().
    """
    parts: list[str] = []
    for i, option in enumerate(choices):
        parts.append(f"  [command]\\[{i + 1}][/command] {option}")
    console.print("\n".join(parts))
    console.print(FOLLOW_UP_CHOICE_HINT)


def _render_sprint_options(console: Console, current_num: int) -> None:
    """Render sprint selection options using the same style as choice menus.

    Matches the visual style of _render_choice_options and _render_dynamic_choices
    so all numbered menus in the REPL look consistent.

    Args:
        console: Rich Console instance for output.
        current_num: The current active sprint number (e.g. 104).
    """
    options = [
        f"Sprint {current_num + 1} (next sprint)",
        f"Sprint {current_num + 2}",
        f"Sprint {current_num + 3}",
        "Other (type a sprint number)",
    ]
    parts: list[str] = []
    for i, option in enumerate(options):
        parts.append(f"  [command]\\[{i + 1}][/command] {option}")
    console.print("\n".join(parts))
    console.print(SPRINT_CHOICE_HINT)


def _resolve_dynamic_choice(user_input: str, choices: tuple[str, ...]) -> str:
    """Resolve numeric or multi-select input to dynamic follow-up choice(s).

    Handles three patterns:
    - Single number: "2" → that option's text
    - "All" variants: "all", "all of the above" → all options joined
    - Multiple numbers: "1 and 3", "1, 2, 4" → those options joined

    Falls back to the original input for free-text answers.

    Args:
        user_input: The raw user input (stripped).
        choices: Tuple of option strings from _follow_up_choices.

    Returns:
        The resolved choice text, or the original input if not a match.
    """
    import re as _re

    logger.debug("_resolve_dynamic_choice: input=%.50s choices=%d", user_input, len(choices))

    # "all", "all of the above", "all of them", "all the options", etc.
    if _re.match(r"^all(\b|$)", user_input, _re.IGNORECASE):
        logger.debug("_resolve_dynamic_choice: resolved to all choices")
        return "; ".join(choices)

    # Single number
    try:
        idx = int(user_input)
        if 1 <= idx <= len(choices):
            return choices[idx - 1]
        return user_input
    except ValueError:
        pass

    # If input already contains full labels (has parentheses with info),
    # it's from the multi-select TUI — return as-is, don't re-resolve
    if "(" in user_input and "pts/sprint" in user_input:
        logger.debug("_resolve_dynamic_choice: multi-select labels detected, returning as-is")
        return user_input

    # Multiple numbers: "1 and 3", "1, 2, 4", "1,3", "1 3"
    nums = _re.findall(r"\d+", user_input)
    if len(nums) >= 2:
        resolved = []
        for n in nums:
            idx = int(n)
            if 1 <= idx <= len(choices):
                resolved.append(choices[idx - 1])
        if resolved:
            return "; ".join(resolved)

    return user_input


def _compute_q10_labels(sprint_weeks: int) -> tuple[str, ...]:
    """Compute Q10 option labels with duration hints based on sprint length.

    Each range option gets a human-readable duration like "~1 month" or "~1 quarter".
    The durations are calculated from the sprint length so they're always accurate.

    Args:
        sprint_weeks: Sprint length in weeks (1, 2, 3, or 4).

    Returns:
        Tuple of option labels matching Q10's QUESTION_METADATA options.
    """

    def _fmt_weeks(lo: int, hi: int) -> str:
        """Format a week range as a human-readable duration."""
        wks_lo = lo * sprint_weeks
        wks_hi = hi * sprint_weeks
        # Use the midpoint for labelling
        mid = (wks_lo + wks_hi) // 2
        if mid <= 2:
            return f"~{mid} week{'s' if mid != 1 else ''}"
        if mid <= 6:
            return "~1 month" if mid <= 5 else "~1.5 months"
        months = round(mid / 4.3)
        if months <= 3:
            return f"~{months} months"
        quarters = months / 3
        if quarters <= 1.5:
            return "~1 quarter"
        return f"~{round(quarters)} quarters"

    return (
        f"1–2 sprints ({_fmt_weeks(1, 2)})",
        f"3–5 sprints ({_fmt_weeks(3, 5)})",
        f"6–10 sprints ({_fmt_weeks(6, 10)})",
        f"10+ sprints ({_fmt_weeks(10, 15)})",
        "No preference — let the agent decide",
    )


def _render_questionnaire_ui(console: Console, questionnaire: QuestionnaireState) -> None:
    """Show progress bar, choice options (if applicable), and skip hint.

    Uses a text-based bar (20 chars with ━/─) — lightweight, no extra imports,
    works in all terminals. Placed after the streamed AI response, not stored
    in message history — pure UI decoration.

    For single-choice questions, a numbered option menu is rendered so the
    user can type a number instead of the full option text.

    In smart/quick mode, the progress bar is hidden — the gap-filling messages
    already communicate remaining questions (e.g. "A few more questions (3
    remaining):") and a /26 progress bar is misleading when only 4 Qs are asked.
    """
    # Only show the /26 progress bar in standard mode.
    if questionnaire.intake_mode == "standard":
        pct = int(questionnaire.progress * 100)
        filled = int(questionnaire.progress * 20)
        bar = "━" * filled + "─" * (20 - filled)
        console.print(f"\n[hint]{bar} {pct}% complete[/hint]")

    current_q = questionnaire.current_question

    # Show dynamic choices — follow-up probes or node-generated options (e.g. Q27 sprint selection).
    # The user picks a number or types freely.
    # See docs: "Project Intake Questionnaire" — follow-up probing
    follow_up_choices = questionnaire._follow_up_choices.get(current_q)
    if follow_up_choices:
        _render_dynamic_choices(console, follow_up_choices)
        return  # Don't show static choices or suggestion hints during a probe

    # Show numbered options for choice questions (only when it's a fresh
    # question, not during a follow-up probe).
    # Q10 (target sprints) gets dynamic labels with duration hints based
    # on the Q8 (sprint length) answer — e.g. "3–5 sprints (~1 quarter)".
    # Duration hints are only shown for the 4 standard sprint lengths
    # (1–4 weeks); non-standard values (e.g. "5 weeks") skip hints.
    if current_q not in questionnaire.probed_questions and is_choice_question(current_q):
        q10_labels = None
        if current_q == 10:
            q8_answer = questionnaire.answers.get(8, "2 weeks")
            import re as _re

            _wk_match = _re.search(r"\d+", q8_answer)
            sprint_weeks = int(_wk_match.group()) if _wk_match else 2
            if 1 <= sprint_weeks <= 4:
                q10_labels = _compute_q10_labels(sprint_weeks)
        _render_choice_options(console, current_q, option_labels=q10_labels)

    # Only show the suggestion hint when there's a pre-filled suggestion
    # AND the user hasn't answered yet (not during a follow-up probe).
    # The skip hint is discoverable via `help` to keep the UI clean.
    if current_q not in questionnaire.probed_questions and questionnaire.suggested_answers.get(current_q):
        console.print(SUGGEST_HINT)


def _render_resume_context(console: Console, questionnaire: QuestionnaireState) -> None:
    """Show context when resuming a mid-questionnaire session.

    Displays the last few answered questions (up to 3) and the current
    question with its suggestion so the user knows where they left off.
    """
    answers = questionnaire.answers
    cur_q = questionnaire.current_question

    # Show last few answered questions for context
    answered_nums = sorted(q for q in answers if q < cur_q)
    recent = answered_nums[-3:]  # last 3 answered
    if recent:
        console.print("\n[dim]── Recent answers ──[/dim]")
        for qn in recent:
            q_text = INTAKE_QUESTIONS.get(qn, f"Q{qn}")
            # Truncate long answers for display
            ans = str(answers[qn])
            if len(ans) > 80:
                ans = ans[:77] + "..."
            console.print(f"[dim]  Q{qn}: {q_text}[/dim]")
            console.print(f"[dim]  → {ans}[/dim]")
        console.print()

    # Show the current question
    q_text = INTAKE_QUESTIONS.get(cur_q, f"Question {cur_q}")
    console.print(f"[bold]{AI_QUESTION_LABEL}[/bold]")
    console.print(f"{q_text}")

    # Show the suggested answer if present
    suggestion = questionnaire.suggested_answers.get(cur_q)
    if suggestion:
        console.print(f"\n[hint]Suggested: {suggestion}[/hint]")
