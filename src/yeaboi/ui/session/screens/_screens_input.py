"""Input screen builders for the TUI session — description and question screens.

# See docs: "Architecture" — pure functions that build Rich Panel screens.
# Each function takes state and returns a Panel renderable. No side effects.
"""

from __future__ import annotations

import re
import sys

import rich.box
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.session._utils import _pad_left, _wrap_text
from yeaboi.ui.shared._components import PAD, planning_title

_PAD = PAD

# Consistent width for all input boxes across screens (question, chat, edit).
_INPUT_BOX_W_MAX = 74


def _voice_hint() -> str:
    """Return a discoverability suffix for voice input on text-entry screens.

    Advertises the feature so users know it exists. When the voice extra is
    installed it prompts to speak; otherwise it shows how to enable it — hiding
    it entirely meant the feature was invisible to anyone who hadn't set it up.
    Returns "" when tips are switched off, so disabling tips hides this inline
    hint too (the double-tap Space shortcut still works regardless).
    """
    from yeaboi.config import is_tips_enabled
    from yeaboi.voice import is_voice_available, voice_install_command

    if not is_tips_enabled():
        return ""

    available, _reason = is_voice_available()
    if available:
        return " · \U0001f3a4 double-tap Space to speak"
    return f" · \U0001f3a4 dictate: {voice_install_command()}"


def _image_hint() -> str:
    """Return a discoverability suffix for Ctrl+V screenshot paste.

    Shown only on fields where pasted images actually reach the LLM. Spells out
    "Ctrl" — and warns off ⌘V on macOS, where Cmd+V is the terminal emulator's
    plain-text paste and physically cannot carry an image — because Mac users
    would otherwise reach for Cmd+V and conclude the feature doesn't exist.
    Returns "" when tips are switched off, mirroring _voice_hint().
    """
    from yeaboi.config import is_tips_enabled

    if not is_tips_enabled():
        return ""
    suffix = " (not ⌘V)" if sys.platform == "darwin" else ""
    return f" · \U0001f4f7 Ctrl+V paste screenshot{suffix}"


# ---------------------------------------------------------------------------
# Shared header — "Planning" ASCII title pinned at top of every screen
# ---------------------------------------------------------------------------


def _planning_title(shimmer_tick: float | None = None) -> Text:
    """Return the Planning ASCII title styled with the brand colour.

    Delegates to shared planning_title() — previously a local duplicate.
    Pass ``shimmer_tick`` (a monotonic elapsed clock) to animate the travelling
    highlight; ``None`` (default) renders the solid static title.
    """
    return planning_title(shimmer_tick)


# ---------------------------------------------------------------------------
# Screen builders — Description Input (Phase A)
# ---------------------------------------------------------------------------


def _build_description_screen(
    input_lines: list[str],
    cursor_row: int,
    cursor_col: int,
    *,
    width: int = 80,
    height: int = 24,
    border_override: str = "",
    status_line: str = "",
    shimmer_tick: float | None = None,
) -> Panel:
    """Build the multi-line project description input screen.

    Shows the Planning title, a "Tell me about your project" subtitle,
    and a multi-line text box. Enter on an empty line submits.
    border_override: if set, overrides the input box border colour (for green pulse).
    status_line: if set, replaces the submit hint with this text (e.g. the
        inline voice-recording indicator) so the user stays on this screen.
    shimmer_tick: if set, animates the title's travelling highlight.
    """
    title = _planning_title(shimmer_tick)
    sub = Text(_PAD + "Tell me about your project", style="dim", justify="left")

    # Hint + example — mirrors the REPL opener so users know what to type
    example_text = (
        "Example: \"We're building a mobile app for restaurant reservations. "
        "The team is 4 developers, we use React Native and Node.js, and we need "
        'to launch an MVP in 3 months."'
    )

    # Build the text box content
    box_w = min(90, width - 8)
    box_inner_w = box_w - 2 - 4  # panel border(2) + padding(4)
    wrap_w = box_inner_w  # full inner width available for text

    text_content = Text(justify="left")
    for i, line in enumerate(input_lines):
        display = line
        if i == cursor_row:
            # Insert cursor at cursor_col position
            if cursor_col <= len(display):
                display = display[:cursor_col] + "\u2588" + display[cursor_col:]
            else:
                display = display + "\u2588"
        if i > 0:
            text_content.append("\n")
        # Word-aware wrap: break at word boundaries so words don't split mid-word
        if wrap_w > 0 and len(display) > wrap_w:
            remaining = display
            first_chunk = True
            while remaining:
                if len(remaining) <= wrap_w:
                    chunk = remaining
                    remaining = ""
                else:
                    # Find last space within wrap_w to break at a word boundary
                    break_at = remaining.rfind(" ", 0, wrap_w)
                    if break_at <= 0:
                        # No space found — hard break as fallback
                        break_at = wrap_w
                    chunk = remaining[:break_at]
                    remaining = remaining[break_at:].lstrip(" ")
                if not first_chunk:
                    text_content.append("\n")
                text_content.append(chunk, style="bold white")
                first_chunk = False
        else:
            text_content.append(display, style="bold white")

    # Ensure at least 3 visible lines for the text box
    visible_lines = len(input_lines)
    for _ in range(max(0, 3 - visible_lines)):
        text_content.append("\n")

    input_box = Panel(
        text_content,
        title=" Project Description ",
        title_align="left",
        border_style=border_override or "white",
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=box_w,
    )

    if status_line:
        submit_hint = Text(_PAD + status_line, style="bold white", justify="left")
    else:
        submit_hint = Text(
            _PAD + "Enter submit \u00b7 \u2303N new line \u00b7 Esc go back" + _voice_hint() + _image_hint(),
            style="dim",
            justify="left",
        )

    # Wrap the example to fit within the box width
    example_lines = _wrap_text(example_text, box_w - 4)

    hint_msg = "A few sentences is enough to get started. I'll ask follow-up questions."
    body: list = [
        Text(_PAD + hint_msg, style="dim", justify="left"),
        Text(""),
    ]
    for ex_line in example_lines:
        body.append(Text(_PAD + ex_line, style="dim italic", justify="left"))
    body.append(Text(""))
    body.append(_pad_left(input_box))
    body.append(Text(""))
    body.append(submit_hint)

    body_h = 3 + len(example_lines) + 7  # hint(1) + blank + example lines + blank + box(~5) + blank + submit_hint

    inner_h = height - 4
    header_h = 10  # blank + title(6) + blank + subtitle + blank
    remaining_h = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining_h)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


# ---------------------------------------------------------------------------
# Screen builders — Intake Question (Phase B)
# ---------------------------------------------------------------------------


_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")


def _style_preamble_line(line: str) -> Text:
    """Render a preamble line with **bold** segments in bright white, rest dim.

    Markdown bold markers (**...**) are common in extraction summaries
    (e.g. "I **3** extracted from your description").
    """
    t = Text(justify="left")
    pos = 0
    for m in _BOLD_RE.finditer(line):
        # Text before the match — dim
        if m.start() > pos:
            t.append(line[pos : m.start()], style="dim")
        # Bold content — bright white
        t.append(m.group(1), style="bold bright_white")
        pos = m.end()
    # Remaining text after last match
    if pos < len(line):
        t.append(line[pos:], style="dim")
    return t


def _build_question_screen(
    question_text: str,
    input_value: str,
    *,
    choices: list[tuple[str, bool]] | None = None,
    suggestion: str | None = None,
    progress: str = "",
    phase_label: str = "",
    preamble_lines: list[str] | None = None,
    selected_choice: int = 0,
    width: int = 80,
    height: int = 24,
    border_override: str = "",
    status_line: str = "",
    shimmer_tick: float | None = None,
) -> Panel:
    """Build a single intake question screen.

    choices: list of (option_text, is_default) for single-choice questions.
             When provided, renders numbered options as cards.
    suggestion: pre-filled suggestion text (Enter to accept, type to replace).
    progress: e.g. "Q3 of 26" or "2 remaining" — shown in subtitle.
    phase_label: e.g. "Phase 1: Project Context" — shown above question.
    preamble_lines: dim context lines (extraction summary, remaining count).
    shimmer_tick: if set, animates the title's travelling highlight.
    """
    title = _planning_title(shimmer_tick)

    # Phase title in bright white, progress counter in dim
    sub = Text(_PAD, justify="left")
    sub_label = phase_label if phase_label else "Intake"
    sub.append(sub_label, style="bold bright_white")
    if progress:
        sub.append(f"  {progress}", style="dim")

    # All body content is constrained to the same width as the input box
    # so nothing extends past ~75% of the terminal width.
    box_w = min(_INPUT_BOX_W_MAX, int(width * 0.75))
    wrap_w = box_w - len(_PAD)  # text wrap width (accounting for left indent)
    box_inner_w = box_w - 2 - 4

    body: list = []
    body_h = 0

    # Preamble — dim context with **bold** numbers rendered bright white
    if preamble_lines:
        for line in preamble_lines:
            for wrapped in _wrap_text(line, wrap_w):
                body.append(_style_preamble_line(_PAD + wrapped))
                body_h += 1
        body.append(Text(""))
        body_h += 1

    # Question text — wrap to the same width as the input box
    q_lines = _wrap_text(question_text, wrap_w)
    for q_line in q_lines:
        body.append(Text(_PAD + q_line, style="bold white", justify="left"))
        body_h += 1
    body.append(Text(""))
    body_h += 1

    if choices:
        # Render choice options as numbered items
        for i, (option, is_default) in enumerate(choices):
            marker = " (default)" if is_default else ""
            is_sel = i == selected_choice
            if is_sel:
                style = "bold white"
                prefix = "\u25b6 "  # right-pointing triangle
            else:
                style = "dim"
                prefix = "  "
            body.append(
                Text(
                    f"{_PAD}  {prefix}[{i + 1}] {option}{marker}",
                    style=style,
                    justify="left",
                )
            )
            body_h += 1
        body.append(Text(""))
        body_h += 1
        hint = Text(
            _PAD + "Arrow keys to select \u00b7 Enter/Ctrl+S confirm \u00b7 Esc cancel", style="dim", justify="left"
        )
    elif suggestion:
        # Show suggestion with accept hint
        sugg_lines = _wrap_text(f"Suggestion: {suggestion}", wrap_w)
        for sl in sugg_lines:
            body.append(Text(_PAD + sl, style="rgb(100,140,200)", justify="left"))
            body_h += 1
        body.append(Text(""))
        body_h += 1
        hint = Text(
            _PAD + "Enter/Ctrl+S submit \u00b7 Esc cancel" + _voice_hint() + _image_hint(), style="dim", justify="left"
        )
    else:
        hint = Text(
            _PAD + "Enter/Ctrl+S submit \u00b7 Esc cancel" + _voice_hint() + _image_hint(), style="dim", justify="left"
        )

    # Inline voice-recording indicator replaces the hint so the user stays here.
    if status_line:
        hint = Text(_PAD + status_line, style="bold white", justify="left")

    if input_value:
        display = input_value + "\u2588"
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
        visible = display[-(avail - 1) :]
        input_content.append(" \u25c2", style="dim")
        input_content.append(visible, style=text_style)

    input_box = Panel(
        input_content,
        title=" Answer ",
        title_align="left",
        border_style=border_override or "white",
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=box_w,
    )

    if not choices:
        body.append(_pad_left(input_box))
        body_h += 5
    body.append(hint)
    body_h += 1

    inner_h = height - 4
    header_h = 10
    remaining_h = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining_h)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )
