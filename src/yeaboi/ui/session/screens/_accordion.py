"""Accordion-style intake question screen.

# See docs: "Architecture" — replaces the single-question-at-a-time screen
# with a vertically scrollable accordion showing all 26 questions at once.
#
# Visual states:
#   - Completed/extracted: "N. Title ✓" in green (1 line, collapsed)
#   - Active: "▶ N. Title" then description + input box (expanded)
#   - Future: "N. Title" dimmed (1 line, collapsed)
#
# The active question stays roughly centered via viewport scrolling, with
# peek indicators ("▲ N above" / "▼ N below") when questions overflow.
#
# Accordion recess effect: the 3 items nearest the BOTTOM viewport edge are
# progressively recessed to the LEFT (less padding), and their text is
# increasingly truncated, creating a depth/fade-away effect.
"""

from __future__ import annotations

import rich.box
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from yeaboi.agent.state import TOTAL_QUESTIONS, QuestionnaireState
from yeaboi.prompts.intake import QUESTION_SHORT_LABELS
from yeaboi.ui.session._utils import _pad_left, _wrap_text
from yeaboi.ui.session.screens._screens import _INPUT_BOX_W_MAX, _PAD, _planning_title

# ---------------------------------------------------------------------------
# Accordion item renderers
# ---------------------------------------------------------------------------

# Colours for the three visual states.
_GREEN_RGB = (80, 220, 120)
_DIM_RGB = (80, 80, 80)
_ACTIVE_STYLE = "bold white"

# Accordion depth settings.
# The 3 items nearest the bottom viewport edge are progressively recessed
# to the LEFT. The base indent aligns with the input box left edge (pad=6).
# Bottom-edge items step LEFT from this by explicit amounts.
_RECESS_STEPS = 3  # only the 3 items nearest the bottom edge get recessed
_INPUT_PAD = 6  # input box left padding — base indent aligns with this
_BASE_INDENT = " " * _INPUT_PAD  # normal items align with input box
# Spaces to remove from _BASE_INDENT: index 0 = bottom edge, 1 = next, 2 = 3rd
_RECESS_REMOVE = [4, 2, 1]

# Truncation settings for bottom-edge fade effect.
# Edge items have their label text progressively shortened.
_TRUNCATE_CHARS = [10, 4, 0]  # chars to remove: edge(0)=10, 1=4, 2=0

# Questions hidden from the accordion (redundant with other questions).
# Q15 ("Codebase status") is fully derived from Q2 ("Project type").
_HIDDEN_QUESTIONS: frozenset[int] = frozenset({15})


def _edge_dist(top_dist: int, bottom_dist: int) -> int:
    """Return the effective distance from the nearest viewport edge.

    Both the top and bottom edges use the same recess/fade effect.
    The minimum of the two distances determines the recess level.
    """
    return min(top_dist, bottom_dist)


def _depth_style(base_rgb: tuple[int, int, int], bottom_dist: int, top_dist: int = _RECESS_STEPS) -> str:
    """Compute a colour that fades toward black based on distance from viewport edge.

    Items far from both edges → full brightness.
    Items at either edge → dimmer.
    """
    dist = _edge_dist(top_dist, bottom_dist)
    if dist >= _RECESS_STEPS:
        return f"rgb({base_rgb[0]},{base_rgb[1]},{base_rgb[2]})"
    fade = max(0.3, 0.4 + dist * 0.2)
    r = int(base_rgb[0] * fade)
    g = int(base_rgb[1] * fade)
    b = int(base_rgb[2] * fade)
    return f"rgb({r},{g},{b})"


def _depth_indent(bottom_dist: int, top_dist: int = _RECESS_STEPS) -> str:
    """Compute the left indent for the accordion recess effect.

    Items at either viewport edge get LESS padding (recessed left);
    items 3+ steps from both edges get the full `_BASE_INDENT`.
    """
    dist = _edge_dist(top_dist, bottom_dist)
    if dist >= _RECESS_STEPS:
        return _BASE_INDENT
    remove = _RECESS_REMOVE[dist]
    pad_len = max(0, _INPUT_PAD - remove)
    return " " * pad_len


def _truncate_label(label: str, bottom_dist: int, top_dist: int = _RECESS_STEPS) -> str:
    """Progressively truncate label text for edge fade effect.

    Items far from both edges show full text. Items at either edge
    have their label increasingly shortened with an ellipsis.
    """
    dist = _edge_dist(top_dist, bottom_dist)
    if dist >= _RECESS_STEPS:
        return label
    trunc = _TRUNCATE_CHARS[dist]
    if trunc == 0 or len(label) <= trunc + 1:
        return label
    return label[: len(label) - trunc] + "\u2026"


def _render_completed_item(q_num: int, bottom_dist: int = _RECESS_STEPS, top_dist: int = _RECESS_STEPS) -> list[Text]:
    """Render a completed question: "N. Title ✓" in green, single line."""
    label = QUESTION_SHORT_LABELS.get(q_num, f"Question {q_num}")
    label = _truncate_label(label, bottom_dist, top_dist)
    indent = _depth_indent(bottom_dist, top_dist)
    style = _depth_style(_GREEN_RGB, bottom_dist, top_dist)
    line = Text(f"{indent}{q_num}. {label} \u2713", style=style, justify="left")
    return [line]


def _render_skipped_item(q_num: int, bottom_dist: int = _RECESS_STEPS, top_dist: int = _RECESS_STEPS) -> list[Text]:
    """Render a skipped question: "N. Title –" in dim, single line."""
    label = QUESTION_SHORT_LABELS.get(q_num, f"Question {q_num}")
    label = _truncate_label(label, bottom_dist, top_dist)
    indent = _depth_indent(bottom_dist, top_dist)
    style = _depth_style(_DIM_RGB, bottom_dist, top_dist)
    line = Text(f"{indent}{q_num}. {label} \u2013", style=style, justify="left")
    return [line]


def _render_future_item(q_num: int, bottom_dist: int = _RECESS_STEPS, top_dist: int = _RECESS_STEPS) -> list[Text]:
    """Render a future question: "N. Title" dimmed, single line."""
    label = QUESTION_SHORT_LABELS.get(q_num, f"Question {q_num}")
    label = _truncate_label(label, bottom_dist, top_dist)
    indent = _depth_indent(bottom_dist, top_dist)
    style = _depth_style(_DIM_RGB, bottom_dist, top_dist)
    line = Text(f"{indent}{q_num}. {label}", style=style, justify="left")
    return [line]


def _render_active_item(
    q_num: int,
    question_text: str,
    input_value: str,
    *,
    choices: list[tuple[str, bool]] | None = None,
    suggestion: str | None = None,
    selected_choice: int = 0,
    selected_choices: set[int] | None = None,
    border_override: str = "",
    box_w: int = 60,
    edit_hint: str = "",
    cursor_pos: int = -1,
) -> list[Text | Panel]:
    """Render the active question: title line, then description + input box.

    Always shows the short title on the first line (like collapsed items but
    with an arrow marker), then the full question text underneath as a description,
    followed by the input box or choice list.
    """
    lines: list[Text | Panel] = []
    wrap_w = box_w - _INPUT_PAD - 4

    # Title line: arrow sits in the margin before the number so the number
    # stays aligned with all other collapsed items.
    # e.g. "    ▶ 3. Problem to solve" where "    " is the margin and
    # "3." aligns with where other items start at _BASE_INDENT.
    label = QUESTION_SHORT_LABELS.get(q_num, f"Question {q_num}")
    arrow_indent = _BASE_INDENT[:-2] if len(_BASE_INDENT) >= 2 else ""
    lines.append(Text(f"{arrow_indent}\u25b6 {q_num}. {label}", style=_ACTIVE_STYLE, justify="left"))

    # Description: full question text, word-wrapped and indented under the title
    q_lines = _wrap_text(question_text, wrap_w)
    desc_indent = _BASE_INDENT + "   "  # align under the title text past "N. "
    for ql in q_lines:
        lines.append(Text(f"{desc_indent}{ql}", style="dim white", justify="left"))

    lines.append(Text(""))

    if choices:
        _multi = selected_choices is not None
        for i, (option, is_default) in enumerate(choices):
            marker = " (default)" if is_default else ""
            is_cursor = i == selected_choice
            if _multi:
                # Multi-select: checkbox indicator
                checked = i in selected_choices
                box = "\u25a0" if checked else "\u25a1"  # ■ filled / □ empty
                pfx = f"\u25b6 {box} " if is_cursor else f"  {box} "
                style = "bold white" if is_cursor else ("white" if checked else "dim")
            else:
                # Single-select: arrow indicator
                pfx = "\u25b6 " if is_cursor else "  "
                style = "bold white" if is_cursor else "dim"
            lines.append(Text(f"{_BASE_INDENT}    {pfx}[{i + 1}] {option}{marker}", style=style, justify="left"))
        # Show inline hint below choices for multi-select
        if _multi:
            lines.append(Text(""))
            lines.append(
                Text(
                    f"{_BASE_INDENT}    Space to toggle \u00b7 Enter to submit",
                    style="dim",
                    justify="left",
                )
            )
    else:
        # Free-text input box with cursor position support.
        # cursor_pos=-1 means cursor at end (default / no explicit position).
        box_inner_w = box_w - 2 - 4  # border + padding
        cpos = cursor_pos if cursor_pos >= 0 else len(input_value)
        if input_value:
            # Insert block cursor at the cursor position
            display = input_value[:cpos] + "\u2588" + input_value[cpos:]
            text_style = "bold white"
        elif suggestion:
            display = suggestion + "\u2588"
            text_style = "rgb(80,80,80)"
        else:
            display = "\u2588"
            text_style = "bold white"

        input_content = Text(justify="left", no_wrap=True, overflow="crop")
        avail = box_inner_w - 4
        if len(display) <= avail:
            input_content.append("  " + display, style=text_style)
        else:
            # Viewport follows cursor position. The cursor character (█) is
            # at index cpos in the display string. We window around it,
            # showing ◂/▸ indicators when text extends beyond either edge.
            cursor_in_display = cpos  # cursor char is at this index in display
            # Reserve 1 char each side for overflow indicators
            has_left = cursor_in_display > 0
            has_right = True  # text overflows if we're here
            indicator_budget = (1 if has_left else 0) + (1 if has_right else 0)
            view_w = avail - indicator_budget

            # Centre the viewport on the cursor
            start = max(0, cursor_in_display - view_w // 2)
            # Clamp so we don't go past the end
            if start + view_w > len(display):
                start = max(0, len(display) - view_w)
            end = start + view_w
            visible = display[start:end]

            left_overflow = start > 0
            right_overflow = end < len(display)

            prefix = " \u25c2" if left_overflow else "  "
            suffix = "\u25b8" if right_overflow else ""
            input_content.append(prefix, style="dim")
            input_content.append(visible, style=text_style)
            if suffix:
                input_content.append(suffix, style="dim")

        input_box = Panel(
            input_content,
            title=" Answer ",
            title_align="left",
            border_style=border_override or "white",
            box=rich.box.ROUNDED,
            padding=(1, 2),
            width=box_w,
        )
        lines.append(_pad_left(input_box, pad=6))

    # Extra blank line after input/choices to separate from next item
    lines.append(Text(""))

    return lines


# ---------------------------------------------------------------------------
# Viewport calculation
# ---------------------------------------------------------------------------


def _compute_item_heights(
    qs: QuestionnaireState,
    active_q: int,
    question_text: str,
    choices: list[tuple[str, bool]] | None,
    suggestion: str | None,
    box_w: int,
    edit_hint: str = "",
) -> dict[int, int]:
    """Compute the rendered height (in lines) for each of the 26 questions.

    Collapsed items (completed/skipped/future) = 1 line.
    The active item height depends on the question text length and input type.
    """
    heights: dict[int, int] = {}
    for q in range(1, TOTAL_QUESTIONS + 1):
        if q in _HIDDEN_QUESTIONS:
            heights[q] = 0
            continue
        if q == active_q:
            # Estimate active item height: title(1) + description lines + blank
            wrap_w = box_w - _INPUT_PAD - 4
            q_lines = _wrap_text(question_text, wrap_w)
            h = 1  # title line
            h += len(q_lines)  # description text lines
            h += 1  # blank line after description
            if choices:
                h += len(choices)  # one line per choice
                if "toggle" in edit_hint:
                    h += 2  # blank + hint line for multi-select
            else:
                h += 5  # input box (border-top + padding + content + padding + border-bottom)
            h += 1  # blank line after input/choices
            heights[q] = h
        else:
            heights[q] = 1  # collapsed
    return heights


def _compute_accordion_viewport(
    active_q: int,
    heights: dict[int, int],
    available_h: int,
    scroll_offset: int = 0,
) -> tuple[int, int]:
    """Compute which questions are visible, centering the active question.

    Returns (first_visible_q, last_visible_q) — 1-based, inclusive.
    The active question is placed roughly in the vertical center. Then we
    fill upward and downward with collapsed items until the viewport is full.

    scroll_offset shifts the viewport: positive = scroll down (show more below),
    negative = scroll up (show more above). The active question is always visible.
    """
    total_height = sum(heights.values())
    if total_height <= available_h:
        return 1, TOTAL_QUESTIONS

    active_h = heights[active_q]
    # Base centering: split available space evenly around active
    space_above = (available_h - active_h) // 2
    space_below = available_h - active_h - space_above

    # Walk backward from active to fill space_above
    first = active_q
    used_above = 0
    while first > 1 and used_above + heights[first - 1] <= space_above:
        first -= 1
        used_above += heights[first]

    # Walk forward from active to fill space_below
    last = active_q
    used_below = 0
    while last < TOTAL_QUESTIONS and used_below + heights[last + 1] <= space_below:
        last += 1
        used_below += heights[last]

    # Fill leftover space (near edges)
    remaining = available_h - used_above - active_h - used_below
    while remaining > 0 and last < TOTAL_QUESTIONS:
        last += 1
        remaining -= heights[last]
    while remaining > 0 and first > 1:
        first -= 1
        remaining -= heights[first]

    # Apply scroll_offset by shifting the viewport window.
    # Positive offset = show more below (increase first, decrease items above).
    # Negative offset = show more above (decrease first, increase items above).
    # The active question must always remain visible.
    if scroll_offset > 0:
        # Scroll down: remove items from top, add items to bottom
        for _ in range(scroll_offset):
            if first < active_q and last < TOTAL_QUESTIONS:
                first += 1
                last += 1
    elif scroll_offset < 0:
        # Scroll up: remove items from bottom, add items to top
        for _ in range(-scroll_offset):
            if last > active_q and first > 1:
                last -= 1
                first -= 1

    return first, last


# ---------------------------------------------------------------------------
# Main accordion screen builder
# ---------------------------------------------------------------------------


def _build_accordion_question_screen(
    question_text: str,
    input_value: str,
    questionnaire: QuestionnaireState,
    *,
    choices: list[tuple[str, bool]] | None = None,
    suggestion: str | None = None,
    progress: str = "",
    phase_label: str = "",
    selected_choice: int = 0,
    selected_choices: set[int] | None = None,
    scroll_offset: int = 0,
    width: int = 80,
    height: int = 24,
    border_override: str = "",
    edit_hint: str = "",
    cursor_pos: int = -1,
    shimmer_tick: float | None = None,
) -> Panel:
    """Build the accordion-style intake question screen.

    Shows all 26 questions as a vertically scrollable list. Completed questions
    are collapsed ("N. Title ✓"), the active question is expanded (title +
    description + input box), and future questions are dimmed.

    The viewport auto-scrolls to keep the active question roughly centered.
    Items at the bottom edge of the viewport recess left and truncate for
    a depth/fade-away effect.

    shimmer_tick: if set, animates the title's travelling highlight.
    """
    title = _planning_title(shimmer_tick)

    # Subtitle: phase label + progress + edit hint (inline)
    sub = Text(_PAD, justify="left")
    sub_label = phase_label if phase_label else "Intake"
    sub.append(sub_label, style="bold bright_white")
    if progress:
        sub.append(f"  {progress}", style="dim")
    if edit_hint:
        sub.append(f"  \u2502  {edit_hint}", style="dim")

    active_q = questionnaire.current_question
    box_w = min(_INPUT_BOX_W_MAX, int(width * 0.75))

    # Calculate heights and viewport bounds
    heights = _compute_item_heights(questionnaire, active_q, question_text, choices, suggestion, box_w, edit_hint)

    inner_h = height - 4  # outer panel border + padding
    header_h = 10  # blank + title(6) + blank + subtitle + blank
    available_h = max(5, inner_h - header_h - 1)  # -1 for bottom padding

    first_vis, last_vis = _compute_accordion_viewport(active_q, heights, available_h, scroll_offset)

    # Build the accordion body
    body: list = []

    # Count visible (non-hidden) items for top/bottom distance calculation
    visible_items = [q for q in range(first_vis, last_vis + 1) if q not in _HIDDEN_QUESTIONS]
    n_visible = len(visible_items)
    has_items_above = first_vis > 1  # top recess only when scrolled past first item

    # Render visible questions with edge-based recess.
    # Bottom edge always recesses. Top edge only recesses when there are
    # items above the viewport (i.e. user has scrolled down).
    for vis_idx, q in enumerate(visible_items):
        dist_from_bottom = n_visible - 1 - vis_idx
        dist_from_top = vis_idx if has_items_above else _RECESS_STEPS
        if q == active_q:
            # Expanded active item — always full padding, no recess
            active_lines = _render_active_item(
                q,
                question_text,
                input_value,
                choices=choices,
                suggestion=suggestion,
                selected_choice=selected_choice,
                selected_choices=selected_choices,
                border_override=border_override,
                box_w=box_w,
                edit_hint=edit_hint,
                cursor_pos=cursor_pos,
            )
            body.extend(active_lines)
        elif (
            q in questionnaire.answers
            or q in questionnaire.extracted_questions
            or q in questionnaire.defaulted_questions
            or (q < active_q and q not in questionnaire.skipped_questions)
        ):
            body.extend(_render_completed_item(q, bottom_dist=dist_from_bottom, top_dist=dist_from_top))
        elif q in questionnaire.skipped_questions:
            body.extend(_render_skipped_item(q, bottom_dist=dist_from_bottom, top_dist=dist_from_top))
        else:
            body.extend(_render_future_item(q, bottom_dist=dist_from_bottom, top_dist=dist_from_top))

    # Pad remaining height
    rendered_h = header_h + len(body)
    remaining_h = max(0, inner_h - rendered_h)
    pad_lines = [Text("") for _ in range(remaining_h)]

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *pad_lines,
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )
