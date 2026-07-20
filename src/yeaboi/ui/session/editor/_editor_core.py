"""Generic multi-line buffer editor core.

# See docs: "Architecture" — shared editor loop used by all artifact editors.
# Provides standard key bindings (cursor movement, text editing, word boundaries)
# with a pluggable `editable_start_fn` callback to protect field labels.
#
# Key bindings:
#   Arrow keys        — move cursor (skips non-editable rows)
#   Typing            — insert at cursor
#   Backspace/Delete  — delete character
#   Home/End          — jump to editable start / end of line
#   Shift+Left/Right  — word-boundary jumps
#   Ctrl+S            — save (returns buffer)
#   Esc               — cancel (returns None)
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

import rich.box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.shared._components import PAD, planning_title

# ---------------------------------------------------------------------------
# Shared field label pattern for all editors (story, task, sprint, etc.)
# ---------------------------------------------------------------------------

# Transient editor notice — rendered in the subtitle by render_editor_panel (and
# the story editor's _render_editor), cleared on the next keypress. Used for the
# Ctrl+V response: artifact editors deliberately do NOT accept image paste — a
# literal [image #N] chip would end up inside the artifact text (and its exports),
# and these editors make no LLM call that could consume the screenshot. Paste
# screenshots into the Edit/Regenerate feedback prompt instead.
_editor_notice = ""


def _set_editor_notice(msg: str) -> None:
    global _editor_notice
    _editor_notice = msg


_FIELD_LABELS = (
    r"Persona|Goal|Benefit|Points|Priority|Discipline|Title|Description|Name|Capacity"
    r"|Type|Target State|Sprint Length|Target Sprints"
)


# ---------------------------------------------------------------------------
# Word boundary helpers
# ---------------------------------------------------------------------------


def _word_boundary_left(line: str, col: int) -> int:
    """Find the start of the previous word from col."""
    if col <= 0:
        return 0
    pos = col - 1
    while pos > 0 and not line[pos].isalnum():
        pos -= 1
    while pos > 0 and line[pos - 1].isalnum():
        pos -= 1
    return pos


def _word_boundary_right(line: str, col: int) -> int:
    """Find the start of the next word from col."""
    length = len(line)
    if col >= length:
        return length
    pos = col
    while pos < length and line[pos].isalnum():
        pos += 1
    while pos < length and not line[pos].isalnum():
        pos += 1
    return pos


# ---------------------------------------------------------------------------
# Visual line wrapping for editor rendering
# ---------------------------------------------------------------------------


def _wrap_indent(line: str) -> int:
    """Return indent width for wrapped continuation lines.

    Field label lines (e.g. "Title: ...") get indented to align after the colon.
    All other lines get 4-space indent.
    """
    m = re.match(rf"^(?:\[\d+\]\s*)?({_FIELD_LABELS})\s*:\s*", line)
    return m.end() if m else 4


def _visual_lines(line: str, wrap_w: int) -> list[str]:
    """Word-wrap a buffer line into visual lines for display."""
    if not line or wrap_w < 10:
        return [line]
    if len(line) <= wrap_w:
        return [line]

    result: list[str] = []
    indent = _wrap_indent(line)
    indent_str = " " * indent

    first = line[:wrap_w]
    result.append(first)
    remaining = line[wrap_w:]

    cont_w = wrap_w - indent
    if cont_w < 5:
        result[0] = line
        return result

    while remaining:
        chunk = remaining[:cont_w]
        result.append(indent_str + chunk)
        remaining = remaining[cont_w:]

    return result


def _continuation_style(buf_line: str) -> str:
    """Return the style to use for wrapped continuation lines of a buffer row.

    AC fields (Given/When/Then) use green, other field labels use blue,
    and plain lines use default dim grey.
    """
    if re.match(r"^\[\d+\]\s*Given\s*:", buf_line) or re.match(r"^\s*(When|Then)\s*:", buf_line):
        return "rgb(100,130,100)"
    if re.match(rf"^({_FIELD_LABELS})\s*:", buf_line):
        return "rgb(100,100,140)"
    return "rgb(120,120,120)"


def _append_styled_line(text_content: Text, line_str: str, *, continuation_style: str = "") -> None:
    """Append a buffer line to the Text renderable with field-label highlighting.

    Field labels (Title:, Priority:, etc.) are styled distinctly from values.
    Continuation lines (from word-wrap) use a dimmer style.
    """
    if continuation_style:
        text_content.append(line_str, style=continuation_style)
        return

    m = re.match(rf"^(\[\d+\]\s*)?({_FIELD_LABELS}|Given|When|Then)\s*:\s*", line_str)
    if m:
        label_end = m.end()
        prefix = line_str[: m.start(2)] if m.group(1) else ""
        label = line_str[m.start(2) : label_end]
        value = line_str[label_end:]
        if prefix:
            text_content.append(prefix, style="cyan")
        is_ac = m.group(2) in ("Given", "When", "Then")
        label_style = "bold rgb(100,130,100)" if is_ac else "bold rgb(100,100,140)"
        text_content.append(label, style=label_style)
        text_content.append(value, style="white")
    elif line_str.strip().startswith("──") and line_str.strip().endswith("──"):
        text_content.append(line_str, style="dim cyan")
    elif line_str.strip().startswith("- ") or line_str.strip().startswith("– "):
        text_content.append(line_str, style="rgb(140,140,140)")
    else:
        text_content.append(line_str)


# ---------------------------------------------------------------------------
# Shared editor renderer (used by task, sprint, analysis, feature editors)
# ---------------------------------------------------------------------------


def render_editor_panel(
    buffer: list[str],
    cursor_row: int,
    cursor_col: int,
    scroll_offset: int,
    *,
    width: int = 80,
    height: int = 24,
    editor_label: str = "",
    title_override=None,
    shimmer_tick: float | None = None,
) -> tuple[Panel, int]:
    """Render a generic editor screen as a Rich Panel.

    Shows buffer lines with field-label highlighting, cursor, and scroll.
    Used by all non-story editors (task, sprint, analysis, feature).
    shimmer_tick: if set (and no title_override), animates the title highlight.
    """
    title = title_override if title_override is not None else planning_title(shimmer_tick)

    sub = Text(justify="left")
    sub.append(PAD + (f"Editing {editor_label}" if editor_label else "Editing"), style="dim")
    sub.append("  |  ", style="dim")
    sub.append("Ctrl+S Save", style="bold rgb(60,160,80)")
    sub.append("  |  ", style="dim")
    sub.append("Esc Cancel", style="dim")
    if _editor_notice:
        sub.append("  |  ", style="dim")
        sub.append(_editor_notice, style="bold white")

    inner_h = height - 4
    header_h = 10
    editor_h = max(3, inner_h - header_h)
    editor_w = width - 12
    wrap_w = editor_w - 4 - len(PAD)

    # Build visual lines with word wrapping
    visual_rows: list[tuple[str, int, int]] = []
    cursor_visual_row = 0

    for buf_row_idx in range(len(buffer)):
        line = buffer[buf_row_idx]
        vlines = _visual_lines(line, wrap_w)
        col_consumed = 0
        for vi, vline in enumerate(vlines):
            if vi == 0:
                buf_col_start = 0
                buf_col_end = len(vline)
            else:
                indent = _wrap_indent(line)
                content_len = len(vline) - indent
                buf_col_start = col_consumed
                buf_col_end = col_consumed + content_len

            visual_rows.append((vline, buf_row_idx, buf_col_start))
            col_consumed = buf_col_end

            if buf_row_idx == cursor_row and buf_col_start <= cursor_col < buf_col_end:
                cursor_visual_row = len(visual_rows) - 1
            elif buf_row_idx == cursor_row and cursor_col >= buf_col_end and vi == len(vlines) - 1:
                cursor_visual_row = len(visual_rows) - 1

    total_visual = len(visual_rows)

    # Clamp scroll
    if cursor_visual_row < scroll_offset:
        scroll_offset = cursor_visual_row
    elif cursor_visual_row >= scroll_offset + editor_h:
        scroll_offset = cursor_visual_row - editor_h + 1
    scroll_offset = max(0, min(scroll_offset, max(0, total_visual - editor_h)))

    text_content = Text(justify="left")
    visible_end = min(scroll_offset + editor_h, total_visual)
    for vi in range(scroll_offset, visible_end):
        if vi > scroll_offset:
            text_content.append("\n")
        text_content.append(PAD)

        vline, buf_row, buf_col_start = visual_rows[vi]
        cont_style = _continuation_style(buffer[buf_row]) if buf_col_start > 0 else ""

        if buf_row == cursor_row and vi == cursor_visual_row:
            if buf_col_start == 0:
                vis_col = cursor_col
            else:
                indent = _wrap_indent(buffer[buf_row])
                vis_col = indent + (cursor_col - buf_col_start)

            vis_col = max(0, min(vis_col, len(vline)))
            before = vline[:vis_col]
            after = vline[vis_col:]
            cursor_char = after[0] if after else " "
            remaining = after[1:] if after else ""

            _append_styled_line(text_content, before, continuation_style=cont_style)
            text_content.append(cursor_char, style="reverse bold white")
            if remaining:
                _append_styled_line(text_content, remaining, continuation_style=cont_style)
        else:
            _append_styled_line(text_content, vline, continuation_style=cont_style)

    # Pad to fill
    lines_shown = visible_end - scroll_offset
    for _ in range(max(0, editor_h - lines_shown)):
        text_content.append("\n")

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        text_content,
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    ), scroll_offset


# ---------------------------------------------------------------------------
# Generic editor key-handling loop
# ---------------------------------------------------------------------------


def edit_buffer_loop(
    live: Live,
    console: Console,
    buffer: list[str],
    cursor_row: int,
    cursor_col: int,
    _key,
    *,
    editable_start_fn: Callable[[str], int | None],
    render_fn: Callable[[list[str], int, int, int, int, int], tuple[Panel, int]],
) -> list[str] | None:
    """Generic editor loop with standard key bindings.

    Handles all standard keys (movement, editing, word boundaries).
    Returns the edited buffer on Ctrl+S, or None on Esc.

    editable_start_fn: given a line string, returns the column index where
        the editable region starts, or None if the line is non-editable.
    render_fn: called as render_fn(buffer, cursor_row, cursor_col,
        scroll_offset, width, height) → (Panel, new_scroll_offset).
    """
    scroll_offset = 0

    # Voice input: double-tap Space to dictate at the cursor (see DoubleTapSpace).
    from yeaboi.ui.shared._voice_input import DoubleTapSpace

    _dts = DoubleTapSpace()

    w, h = console.size
    panel, scroll_offset = render_fn(buffer, cursor_row, cursor_col, scroll_offset, w, h)
    live.update(panel)

    while True:
        try:
            key = _key(timeout=0.05)
        except TypeError:
            key = _key()
        if key and key != "" and key != "ctrl+v":
            _set_editor_notice("")

        if key == "esc":
            _set_editor_notice("")
            return None

        elif key == "ctrl+s":
            _set_editor_notice("")
            return buffer

        elif key == "ctrl+v":
            # See _editor_notice above — image paste is not supported in artifact editors.
            from yeaboi.ui.shared._attachments import unsupported_notice

            unsupported_notice(_set_editor_notice)

        elif key == "backspace":
            min_col = editable_start_fn(buffer[cursor_row])
            if min_col is not None and cursor_col > min_col:
                line = buffer[cursor_row]
                buffer[cursor_row] = line[: cursor_col - 1] + line[cursor_col:]
                cursor_col -= 1

        elif key == "delete":
            min_col = editable_start_fn(buffer[cursor_row])
            if min_col is not None and cursor_col >= min_col:
                line = buffer[cursor_row]
                if cursor_col < len(line):
                    buffer[cursor_row] = line[:cursor_col] + line[cursor_col + 1 :]

        elif key == "up":
            if cursor_row > 0:
                orig_row = cursor_row
                cursor_row -= 1
                min_col = editable_start_fn(buffer[cursor_row])
                if min_col is None:
                    while cursor_row > 0:
                        cursor_row -= 1
                        min_col = editable_start_fn(buffer[cursor_row])
                        if min_col is not None:
                            break
                if min_col is not None:
                    cursor_col = max(cursor_col, min_col)
                    cursor_col = min(cursor_col, len(buffer[cursor_row]))
                else:
                    cursor_row = orig_row

        elif key == "down":
            if cursor_row < len(buffer) - 1:
                orig_row = cursor_row
                cursor_row += 1
                min_col = editable_start_fn(buffer[cursor_row])
                if min_col is None:
                    while cursor_row < len(buffer) - 1:
                        cursor_row += 1
                        min_col = editable_start_fn(buffer[cursor_row])
                        if min_col is not None:
                            break
                if min_col is not None:
                    cursor_col = max(cursor_col, min_col)
                    cursor_col = min(cursor_col, len(buffer[cursor_row]))
                else:
                    cursor_row = orig_row

        elif key == "left":
            min_col = editable_start_fn(buffer[cursor_row])
            if min_col is not None and cursor_col > min_col:
                cursor_col -= 1

        elif key == "right":
            if cursor_col < len(buffer[cursor_row]):
                cursor_col += 1

        elif key == "home":
            min_col = editable_start_fn(buffer[cursor_row])
            cursor_col = min_col if min_col is not None else 0

        elif key == "end":
            cursor_col = len(buffer[cursor_row])

        elif key == "shift+left":
            min_col = editable_start_fn(buffer[cursor_row])
            if min_col is not None:
                new_col = _word_boundary_left(buffer[cursor_row], cursor_col)
                cursor_col = max(new_col, min_col)

        elif key == "shift+right":
            min_col = editable_start_fn(buffer[cursor_row])
            if min_col is not None:
                cursor_col = _word_boundary_right(buffer[cursor_row], cursor_col)

        elif key == "word_backspace":
            min_col = editable_start_fn(buffer[cursor_row])
            if min_col is not None and cursor_col > min_col:
                word_start = _word_boundary_left(buffer[cursor_row], cursor_col)
                word_start = max(word_start, min_col)
                line = buffer[cursor_row]
                buffer[cursor_row] = line[:word_start] + line[cursor_col:]
                cursor_col = word_start

        elif key == "word_delete":
            min_col = editable_start_fn(buffer[cursor_row])
            if min_col is not None and cursor_col >= min_col:
                word_end = _word_boundary_right(buffer[cursor_row], cursor_col)
                line = buffer[cursor_row]
                buffer[cursor_row] = line[:cursor_col] + line[word_end:]

        elif isinstance(key, str) and key.startswith("paste:"):
            min_col = editable_start_fn(buffer[cursor_row])
            if min_col is not None and cursor_col >= min_col:
                pasted = key[6:].replace("\n", " ")
                line = buffer[cursor_row]
                buffer[cursor_row] = line[:cursor_col] + pasted + line[cursor_col:]
                cursor_col += len(pasted)

        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            min_col = editable_start_fn(buffer[cursor_row])
            if min_col is not None and cursor_col >= min_col:
                line = buffer[cursor_row]
                prev_space = cursor_col > min_col and line[cursor_col - 1] == " "
                if key == " " and _dts.is_double(prev_space, time.monotonic()):
                    # Double-tap Space → dictate at the cursor (first space stays
                    # as a separator), collapsing any newlines like a paste.
                    from yeaboi.ui.shared._voice_input import record_voice_input

                    spoken = record_voice_input(live, console, _key)
                    if spoken:
                        spoken = spoken.replace("\n", " ")
                        cur = buffer[cursor_row]
                        buffer[cursor_row] = cur[:cursor_col] + spoken + cur[cursor_col:]
                        cursor_col += len(spoken)
                else:
                    buffer[cursor_row] = line[:cursor_col] + key + line[cursor_col:]
                    cursor_col += 1

        elif key == "":
            pass

        w, h = console.size
        panel, scroll_offset = render_fn(buffer, cursor_row, cursor_col, scroll_offset, w, h)
        live.update(panel)
