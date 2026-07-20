"""Story editor — interactive text editor for UserStory fields.

# See docs: "Architecture" — UI component in the session layer.
# The most complex editor: includes DoD toggle buttons, AC insertion,
# and custom grid navigation. Other editors (task, sprint, analysis,
# feature) live in _editor_artifacts.py and use the generic loop from
# _editor_core.py.
#
# Key bindings:
#   Arrow keys  — move cursor (DoD: grid navigation)
#   Typing      — insert at cursor
#   Enter       — toggle DoD / add AC / no-op on other lines
#   Ctrl+S      — save
#   Esc         — cancel
"""

from __future__ import annotations

import logging
import re
import time

from rich.console import Console
from rich.live import Live
from rich.text import Text

from yeaboi.agent.state import (
    DOD_ITEMS,
    AcceptanceCriterion,
    Discipline,
    Priority,
    StoryPointValue,
    UserStory,
)
from yeaboi.ui.session.editor import _editor_core as _core
from yeaboi.ui.session.editor._editor_core import (
    _append_styled_line,
    _continuation_style,
    _visual_lines,
    _word_boundary_left,
    _word_boundary_right,
    _wrap_indent,
)
from yeaboi.ui.shared._animations import lerp_color
from yeaboi.ui.shared._components import PAD, planning_title

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

_ADD_AC_MARKER = "Add Criteria"

# Button styling
_BTN_GREY_BORDER = (50, 50, 60)
_BTN_GREY_LABEL = (60, 60, 70)
_BTN_WHITE_BORDER = (200, 200, 210)
_BTN_WHITE_LABEL = (240, 240, 245)
_BTN_GREEN_BORDER = (50, 140, 60)
_BTN_GREEN_LABEL = (60, 180, 80)
_FADE_STEP = 0.15

# DoD button grid
_DOD_SHORT = ("AC Met", "Docs", "Testing", "Code Merged", "SDLC", "Sign-off", "Know. Sharing")
_DOD_BTN_INNER = max(len(lbl) for lbl in _DOD_SHORT) + 4
_DOD_BTN_W = _DOD_BTN_INNER + 2
_DOD_BTN_GAP = 2

# Valid enum values for lenient parsing
_VALID_PRIORITIES = {p.value for p in Priority}
_VALID_DISCIPLINES = {d.value for d in Discipline}
_VALID_POINTS = {v.value for v in StoryPointValue}


# ---------------------------------------------------------------------------
# Story → editable text → Story
# ---------------------------------------------------------------------------


def _story_to_text(story: UserStory) -> str:
    """Convert a UserStory to structured editable text."""
    w = 12  # len("Discipline: ")
    lines: list[str] = []
    lines.append(f"{'Persona:':<{w}}{story.persona}")
    lines.append(f"{'Goal:':<{w}}{story.goal}")
    lines.append(f"{'Benefit:':<{w}}{story.benefit}")
    lines.append("")
    lines.append(f"{'Points:':<{w}}{story.story_points}")
    if story.points_rationale:
        lines.append(f"{'Rationale:':<{w}}{story.points_rationale}")
    lines.append(f"{'Priority:':<{w}}{story.priority.value}")
    lines.append(f"{'Discipline:':<{w}}{story.discipline.value}")
    lines.append("")
    lines.append("\u2500\u2500 Acceptance Criteria \u2500\u2500")
    lines.append("")
    for i, ac in enumerate(story.acceptance_criteria, 1):
        if i > 1:
            lines.append("")
        lines.append(f"[{i}] Given: {ac.given}")
        lines.append(f"    {'When:':<7}{ac.when}")
        lines.append(f"    {'Then:':<7}{ac.then}")
    lines.append("")
    lines.append(_ADD_AC_MARKER)
    if len(story.dod_applicable) == len(DOD_ITEMS):
        lines.append("")
        lines.append("\u2500\u2500 Definition of Done \u2500\u2500")
        lines.append("")
        for label, applicable in zip(_DOD_SHORT, story.dod_applicable):
            prefix = "+" if applicable else "-"
            lines.append(f"{prefix}{label}")
    return "\n".join(lines)


def _parse_edited_story(text: str, original: UserStory) -> UserStory:
    """Parse structured editor text back into a UserStory."""
    lines = [ln for ln in text.split("\n") if ln.strip() != _ADD_AC_MARKER]
    fields: dict[str, str] = {}
    for line in lines:
        m = re.match(r"^(Persona|Goal|Benefit|Points|Rationale|Priority|Discipline)\s*:\s*(.+)$", line.strip())
        if m:
            fields[m.group(1).lower()] = m.group(2).strip()

    # Parse acceptance criteria
    criteria: list[AcceptanceCriterion] = []
    ac_pat = re.compile(r"^\[(\d+)\]\s*Given\s*:\s*(.*)$", re.IGNORECASE)
    when_pat = re.compile(r"^\s*When\s*:\s*(.*)$", re.IGNORECASE)
    then_pat = re.compile(r"^\s*Then\s*:\s*(.*)$", re.IGNORECASE)
    i = 0
    while i < len(lines):
        ac_m = ac_pat.match(lines[i].strip())
        if ac_m:
            given = ac_m.group(2).strip()
            when_val = then_val = ""
            if i + 1 < len(lines):
                wm = when_pat.match(lines[i + 1].strip())
                if wm:
                    when_val = wm.group(1).strip()
                    i += 1
            if i + 1 < len(lines):
                tm = then_pat.match(lines[i + 1].strip())
                if tm:
                    then_val = tm.group(1).strip()
                    i += 1
            criteria.append(AcceptanceCriterion(given=given, when=when_val, then=then_val))
        i += 1

    # Resolve enum values
    try:
        pv = int(fields.get("points", str(original.story_points)))
        points = StoryPointValue(pv) if pv in _VALID_POINTS else original.story_points
    except (ValueError, KeyError):
        points = original.story_points
    pri_str = fields.get("priority", original.priority.value).lower()
    priority = Priority(pri_str) if pri_str in _VALID_PRIORITIES else original.priority
    disc_str = fields.get("discipline", original.discipline.value).lower()
    discipline = Discipline(disc_str) if disc_str in _VALID_DISCIPLINES else original.discipline

    # Parse DoD toggle flags
    dod_flags = [
        s.strip()[0] == "+" for s in lines if s.strip() and s.strip()[0] in "+-" and s.strip()[1:] in _DOD_SHORT
    ]

    return UserStory(
        id=original.id,
        feature_id=original.feature_id,
        # title is not part of the editable buffer — carry it over so an edit
        # doesn't silently reset it to the dataclass default.
        title=original.title,
        persona=fields.get("persona", original.persona),
        goal=fields.get("goal", original.goal),
        benefit=fields.get("benefit", original.benefit),
        acceptance_criteria=tuple(criteria) if criteria else original.acceptance_criteria,
        story_points=points,
        priority=priority,
        discipline=discipline,
        dod_applicable=tuple(dod_flags) if len(dod_flags) == len(DOD_ITEMS) else original.dod_applicable,
        points_rationale=fields.get("rationale", original.points_rationale),
    )


# ---------------------------------------------------------------------------
# Line classification helpers
# ---------------------------------------------------------------------------


def _editable_start(line: str) -> int | None:
    """Return column where editable value starts, or None if non-editable."""
    stripped = line.strip()
    if stripped.startswith("\u2500\u2500"):
        return None
    if stripped == _ADD_AC_MARKER:
        return None
    if stripped and stripped[0] in "+-" and stripped[1:] in _DOD_SHORT:
        return None
    if not stripped:
        return None
    m = re.match(r"^(Persona|Goal|Benefit|Points|Priority|Discipline)\s*:\s*", line)
    if m:
        return m.end()
    ac_m = re.match(r"^\[\d+\]\s*Given\s*:\s*", line)
    if ac_m:
        return ac_m.end()
    wt_m = re.match(r"^\s*(When|Then)\s*:\s*", line)
    if wt_m:
        return wt_m.end()
    return 0


def _is_add_marker(buffer: list[str], row: int) -> bool:
    return 0 <= row < len(buffer) and buffer[row].strip() == _ADD_AC_MARKER


def _is_dod_checkbox(buffer: list[str], row: int) -> bool:
    if not (0 <= row < len(buffer)):
        return False
    s = buffer[row].strip()
    return bool(s and s[0] in "+-" and s[1:] in _DOD_SHORT)


def _is_dod_selected(buffer: list[str], row: int) -> bool:
    return _is_dod_checkbox(buffer, row) and buffer[row].strip().startswith("+")


def _clamp_cursor_to_editable(buffer: list[str], row: int, col: int, *, prefer_forward: bool = True) -> tuple[int, int]:
    """Ensure cursor is within an editable region."""
    if _is_add_marker(buffer, row) or _is_dod_checkbox(buffer, row):
        return row, 0
    min_col = _editable_start(buffer[row])
    if min_col is not None:
        return row, max(col, min_col)
    primary = 1 if prefer_forward else -1
    for sign in (primary, -primary):
        for offset in range(1, len(buffer)):
            candidate = row + offset * sign
            if 0 <= candidate < len(buffer):
                if _is_add_marker(buffer, candidate) or _is_dod_checkbox(buffer, candidate):
                    return candidate, 0
                mc = _editable_start(buffer[candidate])
                if mc is not None:
                    return candidate, mc
    return row, col


# ---------------------------------------------------------------------------
# Story renderer (DoD button grid + AC markers)
# ---------------------------------------------------------------------------


def _append_button_line(text: Text, vline: str, border_style: str, label_style: str, label: str) -> None:
    """Append a button visual line with separate border/label styles."""
    idx = vline.find(label)
    if idx >= 0:
        text.append(vline[:idx], style=border_style)
        text.append(label, style=label_style)
        text.append(vline[idx + len(label) :], style=border_style)
    else:
        text.append(vline, style=border_style)


def _render_dod_button_group(
    text: Text,
    buffer: list[str],
    cursor_row: int,
    fades: dict[int, float],
    grp_buf_rows: list[int],
    line_type: int,
    wrap_w: int = 0,
) -> None:
    """Render one visual line of a DoD button group (top/mid/bot border)."""
    gap_str = " " * _DOD_BTN_GAP
    n = len(grp_buf_rows)
    row_w = n * _DOD_BTN_W + (n - 1) * _DOD_BTN_GAP
    pad_l = max(0, (wrap_w - row_w) // 2) if wrap_w > 0 else 0
    if pad_l:
        text.append(" " * pad_l)
    for si, buf_row in enumerate(grp_buf_rows):
        if si > 0:
            text.append(gap_str)
        t = fades.get(buf_row, 1.0 if buf_row == cursor_row else 0.0)
        selected = _is_dod_selected(buffer, buf_row)
        if selected:
            border_s = lerp_color(t, _BTN_GREEN_BORDER, _BTN_WHITE_BORDER)
            label_s = lerp_color(t, _BTN_GREEN_LABEL, _BTN_WHITE_LABEL)
        else:
            border_s = lerp_color(t, _BTN_GREY_BORDER, _BTN_WHITE_BORDER)
            label_s = lerp_color(t, _BTN_GREY_LABEL, _BTN_WHITE_LABEL)
        if t > 0.3:
            label_s = f"bold {label_s}"
        display_label = buffer[buf_row].strip()[1:]
        if line_type == 0:
            text.append("\u256d" + "\u2500" * _DOD_BTN_INNER + "\u256e", style=border_s)
        elif line_type == 2:
            text.append("\u2570" + "\u2500" * _DOD_BTN_INNER + "\u256f", style=border_s)
        else:
            inner_pad_l = (_DOD_BTN_INNER - len(display_label)) // 2
            inner_pad_r = _DOD_BTN_INNER - len(display_label) - inner_pad_l
            text.append("\u2502" + " " * inner_pad_l, style=border_s)
            text.append(display_label, style=label_s)
            text.append(" " * inner_pad_r + "\u2502", style=border_s)


def _render_editor(
    buffer: list[str],
    cursor_row: int,
    cursor_col: int,
    scroll_offset: int,
    *,
    width: int = 80,
    height: int = 24,
    story_id: str = "",
    fades: dict[int, float] | None = None,
    shimmer_tick: float | None = None,
) -> tuple:
    """Render the story editor screen as a Rich Panel.

    Complex renderer with DoD button grid and AC markers — story-specific.
    shimmer_tick: if set, animates the title's travelling highlight.
    """
    import rich.box
    from rich.console import Group
    from rich.panel import Panel

    title = planning_title(shimmer_tick)
    sub = Text(justify="left")
    sub.append(PAD + (f"Editing {story_id}" if story_id else "Editing story"), style="dim")
    sub.append("  |  ", style="dim")
    sub.append("Ctrl+S Save", style="bold rgb(60,160,80)")
    sub.append("  |  ", style="dim")
    sub.append("Esc Cancel", style="dim")
    if _core._editor_notice:
        sub.append("  |  ", style="dim")
        sub.append(_core._editor_notice, style="bold white")
    if _is_add_marker(buffer, cursor_row):
        sub.append("  |  ", style="dim")
        sub.append("Enter Add Criteria", style="bold rgb(70,100,180)")
    elif _is_dod_checkbox(buffer, cursor_row):
        sub.append("  |  ", style="dim")
        sub.append("Enter Toggle", style="bold rgb(70,100,180)")

    inner_h = height - 4
    header_h = 10
    editor_h = max(3, inner_h - header_h)
    editor_w = width - 12
    wrap_w = editor_w - 4 - len(PAD)
    if fades is None:
        fades = {}
    btns_per_row = max(1, (wrap_w + _DOD_BTN_GAP) // (_DOD_BTN_W + _DOD_BTN_GAP))

    # Build visual lines
    visual_rows: list[tuple[str, int, int]] = []
    cursor_visual_row = 0
    dod_grp_map: dict[int, tuple[list[int], int]] = {}

    buf_row = 0
    while buf_row < len(buffer):
        line = buffer[buf_row]
        if line.strip() == _ADD_AC_MARKER:
            btn_inner = len(_ADD_AC_MARKER) + 4
            btn_top = "\u256d" + "\u2500" * btn_inner + "\u256e"
            btn_mid = "\u2502  " + _ADD_AC_MARKER + "  \u2502"
            btn_bot = "\u2570" + "\u2500" * btn_inner + "\u256f"
            pad = max(0, (wrap_w - len(btn_top)) // 2)
            for bline in (btn_top, btn_mid, btn_bot):
                visual_rows.append((" " * pad + bline, buf_row, -1))
            if buf_row == cursor_row:
                cursor_visual_row = len(visual_rows) - 1
            buf_row += 1
            continue
        if _is_dod_checkbox(buffer, buf_row):
            dod_rows: list[int] = []
            while buf_row < len(buffer) and _is_dod_checkbox(buffer, buf_row):
                dod_rows.append(buf_row)
                buf_row += 1
            for grp_start in range(0, len(dod_rows), btns_per_row):
                grp = dod_rows[grp_start : grp_start + btns_per_row]
                for line_type in range(3):
                    vi_idx = len(visual_rows)
                    visual_rows.append(("", grp[0], -1))
                    dod_grp_map[vi_idx] = (grp, line_type)
                for br in grp:
                    if br == cursor_row:
                        cursor_visual_row = len(visual_rows) - 1
            continue
        stripped = line.strip()
        if stripped.startswith("\u2500\u2500") and stripped.endswith("\u2500\u2500"):
            title_text = stripped.strip("\u2500").strip()
            side_w = max(2, (wrap_w - len(title_text) - 2) // 2)
            header_line = "\u2500" * side_w + " " + title_text + " " + "\u2500" * side_w
            if len(header_line) < wrap_w:
                header_line += "\u2500" * (wrap_w - len(header_line))
            visual_rows.append((header_line[:wrap_w], buf_row, 0))
            if buf_row == cursor_row:
                cursor_visual_row = len(visual_rows) - 1
            buf_row += 1
            continue
        vlines = _visual_lines(line, wrap_w)
        col_consumed = 0
        for vi, vline in enumerate(vlines):
            if vi == 0:
                buf_col_start, buf_col_end = 0, len(vline)
            else:
                indent = _wrap_indent(line)
                buf_col_start = col_consumed
                buf_col_end = col_consumed + len(vline) - indent
            visual_rows.append((vline, buf_row, buf_col_start))
            col_consumed = buf_col_end
            if buf_row == cursor_row and buf_col_start <= cursor_col < buf_col_end:
                cursor_visual_row = len(visual_rows) - 1
            elif buf_row == cursor_row and cursor_col >= buf_col_end and vi == len(vlines) - 1:
                cursor_visual_row = len(visual_rows) - 1
        buf_row += 1

    total_visual = len(visual_rows)
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
        vline, br, buf_col_start = visual_rows[vi]
        if buf_col_start == -1:
            if _is_add_marker(buffer, br):
                t = fades.get(br, 1.0 if br == cursor_row else 0.0)
                border_s = lerp_color(t, _BTN_GREY_BORDER, _BTN_WHITE_BORDER)
                label_s = lerp_color(t, _BTN_GREY_LABEL, _BTN_WHITE_LABEL)
                if t > 0.5:
                    label_s = f"bold {label_s}"
                _append_button_line(text_content, vline, border_s, label_s, _ADD_AC_MARKER)
                continue
            grp_info = dod_grp_map.get(vi)
            if grp_info:
                grp_rows, lt = grp_info
                _render_dod_button_group(text_content, buffer, cursor_row, fades, grp_rows, lt, wrap_w)
            continue
        cont_style = _continuation_style(buffer[br]) if buf_col_start > 0 else ""
        if br == cursor_row and vi == cursor_visual_row:
            vis_col = cursor_col if buf_col_start == 0 else _wrap_indent(buffer[br]) + (cursor_col - buf_col_start)
            vis_col = max(0, min(vis_col, len(vline)))
            before, after = vline[:vis_col], vline[vis_col:]
            cursor_char = after[0] if after else " "
            remaining = after[1:] if after else ""
            _append_styled_line(text_content, before, continuation_style=cont_style)
            text_content.append(cursor_char, style="reverse bold white")
            if remaining:
                _append_styled_line(text_content, remaining, continuation_style=cont_style)
        else:
            _append_styled_line(text_content, vline, continuation_style=cont_style)

    for _ in range(max(0, editor_h - (visible_end - scroll_offset))):
        text_content.append("\n")

    content = Group(Text(""), title, Text(""), sub, Text(""), text_content)
    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    ), scroll_offset


# ---------------------------------------------------------------------------
# Main editor loop
# ---------------------------------------------------------------------------


def edit_story(
    live: Live,
    console: Console,
    story: UserStory,
    _key,
    *,
    width: int = 80,
    height: int = 24,
) -> UserStory | None:
    """Open the text editor for a UserStory.

    The story editor has custom key handling for DoD grid navigation and
    AC insertion that can't use the generic edit_buffer_loop.

    Returns a new UserStory with edited fields, or None if cancelled (Esc).
    """
    logger.info("editor: story editor opened: %s", story.id)
    text = _story_to_text(story)
    buffer = text.split("\n")
    cursor_row, cursor_col = _clamp_cursor_to_editable(buffer, 0, 0)
    scroll_offset = 0

    btn_fades: dict[int, float] = {}

    def _update_fade_targets():
        for r in range(len(buffer)):
            if _is_add_marker(buffer, r) or _is_dod_checkbox(buffer, r):
                btn_fades.setdefault(r, 0.0)

    def _step_fades():
        for r in list(btn_fades):
            target = 1.0 if r == cursor_row else 0.0
            if abs(btn_fades[r] - target) > 0.01:
                if btn_fades[r] < target:
                    btn_fades[r] = min(btn_fades[r] + _FADE_STEP, target)
                else:
                    btn_fades[r] = max(btn_fades[r] - _FADE_STEP, target)

    _update_fade_targets()
    w, h = console.size
    panel, scroll_offset = _render_editor(
        buffer, cursor_row, cursor_col, scroll_offset, width=w, height=h, story_id=story.id, fades=btn_fades
    )
    live.update(panel)

    def _cleanup_empty_ac(old_row: int):
        given_row = old_row
        while given_row >= 0 and not re.match(r"^\[\d+\]\s*Given\s*:", buffer[given_row]):
            given_row -= 1
        if given_row < 0 or given_row + 2 >= len(buffer):
            return
        if (
            re.sub(r"^\[\d+\]\s*Given\s*:\s*", "", buffer[given_row]).strip()
            or re.sub(r"^\s*When\s*:\s*", "", buffer[given_row + 1]).strip()
            or re.sub(r"^\s*Then\s*:\s*", "", buffer[given_row + 2]).strip()
        ):
            return
        start = given_row - 1 if given_row > 0 and not buffer[given_row - 1].strip() else given_row
        for _ in range(given_row + 3 - start):
            if start < len(buffer):
                buffer.pop(start)
        ac_num = 0
        for i, ln in enumerate(buffer):
            if re.match(r"^\[\d+\]\s*Given", ln):
                ac_num += 1
                buffer[i] = re.sub(r"^\[\d+\]", f"[{ac_num}]", ln)

    def _adjacent_dod(direction: int) -> int | None:
        r = cursor_row + direction
        while 0 <= r < len(buffer):
            if _is_dod_checkbox(buffer, r):
                return r
            if buffer[r].strip():
                break
            r += direction
        return None

    def _dod_grid_navigate(direction: int) -> int | None:
        all_dod = [r for r in range(len(buffer)) if _is_dod_checkbox(buffer, r)]
        if cursor_row not in all_dod:
            return None
        w2, _ = console.size
        ww = w2 - 12 - 4 - len(PAD)
        bpr = max(1, (ww + _DOD_BTN_GAP) // (_DOD_BTN_W + _DOD_BTN_GAP))
        idx = all_dod.index(cursor_row)
        target_idx = idx + (direction * bpr)
        return all_dod[target_idx] if 0 <= target_idx < len(all_dod) else None

    _ed_anim0 = time.monotonic()  # shimmer title clock
    while True:
        try:
            key = _key(timeout=0.05)
        except TypeError:
            key = _key()

        old_row = cursor_row
        if key and key != "" and key != "ctrl+v":
            _core._set_editor_notice("")

        if key == "esc":
            logger.info("editor: story edit cancelled: %s", story.id)
            _core._set_editor_notice("")
            return None
        elif key == "ctrl+s":
            logger.info("editor: story edit saved: %s", story.id)
            _core._set_editor_notice("")
            return _parse_edited_story("\n".join(buffer), story)
        elif key == "ctrl+v":
            # Image paste is not supported inside artifact editors — a chip would
            # become literal story text. Use Regenerate feedback for screenshots.
            from yeaboi.ui.shared._attachments import unsupported_notice

            unsupported_notice(_core._set_editor_notice)
        elif key == "enter":
            if _is_add_marker(buffer, cursor_row):
                ac_count = sum(1 for ln in buffer if re.match(r"^\[\d+\]\s*Given", ln))
                new_num = ac_count + 1
                insert_at = cursor_row
                need_sep = insert_at > 0 and buffer[insert_at - 1].strip() != ""
                new_lines = [""] if need_sep else []
                new_lines += [f"[{new_num}] Given: ", f"    {'When:':<7}", f"    {'Then:':<7}"]
                for i, nl in enumerate(new_lines):
                    buffer.insert(insert_at + i, nl)
                cursor_row = insert_at + (1 if need_sep else 0)
                cursor_col = _editable_start(buffer[cursor_row]) or 0
                _update_fade_targets()
            elif _is_dod_checkbox(buffer, cursor_row):
                line = buffer[cursor_row]
                buffer[cursor_row] = ("-" if line.strip().startswith("+") else "+") + line.strip()[1:]
                btn_fades[cursor_row] = 0.0
        elif key == "backspace":
            min_col = _editable_start(buffer[cursor_row])
            if min_col is not None and cursor_col > min_col:
                line = buffer[cursor_row]
                buffer[cursor_row] = line[: cursor_col - 1] + line[cursor_col:]
                cursor_col -= 1
        elif key == "delete":
            min_col = _editable_start(buffer[cursor_row])
            if min_col is not None and cursor_col >= min_col and cursor_col < len(buffer[cursor_row]):
                line = buffer[cursor_row]
                buffer[cursor_row] = line[:cursor_col] + line[cursor_col + 1 :]
        elif key == "up":
            if _is_dod_checkbox(buffer, cursor_row):
                target = _dod_grid_navigate(-1)
                if target is not None:
                    cursor_row, cursor_col = target, 0
                else:
                    r = cursor_row - 1
                    while r >= 0 and (_is_dod_checkbox(buffer, r) or not buffer[r].strip()):
                        r -= 1
                    if r >= 0:
                        cursor_row, cursor_col = _clamp_cursor_to_editable(buffer, r, cursor_col, prefer_forward=False)
            elif cursor_row > 0:
                cursor_row -= 1
                cursor_col = min(cursor_col, len(buffer[cursor_row]))
                cursor_row, cursor_col = _clamp_cursor_to_editable(buffer, cursor_row, cursor_col, prefer_forward=False)
        elif key == "down":
            if _is_dod_checkbox(buffer, cursor_row):
                target = _dod_grid_navigate(1)
                if target is not None:
                    cursor_row, cursor_col = target, 0
            elif cursor_row < len(buffer) - 1:
                cursor_row += 1
                cursor_col = min(cursor_col, len(buffer[cursor_row]))
                cursor_row, cursor_col = _clamp_cursor_to_editable(buffer, cursor_row, cursor_col)
        elif key == "left":
            if _is_dod_checkbox(buffer, cursor_row):
                prev = _adjacent_dod(-1)
                if prev is not None:
                    cursor_row, cursor_col = prev, 0
                else:
                    cursor_row, cursor_col = _clamp_cursor_to_editable(
                        buffer,
                        cursor_row - 1,
                        cursor_col,
                        prefer_forward=False,
                    )
            elif _is_add_marker(buffer, cursor_row):
                cursor_row, cursor_col = _clamp_cursor_to_editable(
                    buffer,
                    cursor_row - 1,
                    cursor_col,
                    prefer_forward=False,
                )
            else:
                min_col = _editable_start(buffer[cursor_row])
                if min_col is not None and cursor_col > min_col:
                    cursor_col -= 1
                elif cursor_row > 0:
                    cursor_row -= 1
                    cursor_col = len(buffer[cursor_row])
                    cursor_row, cursor_col = _clamp_cursor_to_editable(
                        buffer,
                        cursor_row,
                        cursor_col,
                        prefer_forward=False,
                    )
        elif key == "right":
            if _is_dod_checkbox(buffer, cursor_row):
                nxt = _adjacent_dod(1)
                if nxt is not None:
                    cursor_row, cursor_col = nxt, 0
            elif _is_add_marker(buffer, cursor_row):
                cursor_row, cursor_col = _clamp_cursor_to_editable(buffer, cursor_row + 1, 0)
            else:
                if cursor_col < len(buffer[cursor_row]):
                    cursor_col += 1
                elif cursor_row < len(buffer) - 1:
                    cursor_row, cursor_col = _clamp_cursor_to_editable(buffer, cursor_row + 1, 0)
        elif key == "home":
            min_col = _editable_start(buffer[cursor_row])
            cursor_col = min_col if min_col is not None else 0
        elif key == "end":
            cursor_col = len(buffer[cursor_row])
        elif key == "shift+left":
            min_col = _editable_start(buffer[cursor_row])
            if min_col is not None:
                cursor_col = max(_word_boundary_left(buffer[cursor_row], cursor_col), min_col)
        elif key == "shift+right":
            min_col = _editable_start(buffer[cursor_row])
            if min_col is not None:
                cursor_col = _word_boundary_right(buffer[cursor_row], cursor_col)
        elif key == "word_backspace":
            min_col = _editable_start(buffer[cursor_row])
            if min_col is not None and cursor_col > min_col:
                ws = max(_word_boundary_left(buffer[cursor_row], cursor_col), min_col)
                buffer[cursor_row] = buffer[cursor_row][:ws] + buffer[cursor_row][cursor_col:]
                cursor_col = ws
        elif key == "word_delete":
            min_col = _editable_start(buffer[cursor_row])
            if min_col is not None and cursor_col >= min_col:
                we = _word_boundary_right(buffer[cursor_row], cursor_col)
                buffer[cursor_row] = buffer[cursor_row][:cursor_col] + buffer[cursor_row][we:]
        elif isinstance(key, str) and key.startswith("paste:"):
            min_col = _editable_start(buffer[cursor_row])
            if min_col is not None and cursor_col >= min_col:
                pasted = key[6:].replace("\n", " ")
                buffer[cursor_row] = buffer[cursor_row][:cursor_col] + pasted + buffer[cursor_row][cursor_col:]
                cursor_col += len(pasted)
        elif isinstance(key, str) and len(key) == 1 and key.isprintable():
            min_col = _editable_start(buffer[cursor_row])
            if min_col is not None and cursor_col >= min_col:
                buffer[cursor_row] = buffer[cursor_row][:cursor_col] + key + buffer[cursor_row][cursor_col:]
                cursor_col += 1
        elif key == "":
            pass

        if old_row != cursor_row:
            _cleanup_empty_ac(old_row)
            cursor_row = min(cursor_row, len(buffer) - 1)
            cursor_row, cursor_col = _clamp_cursor_to_editable(buffer, cursor_row, cursor_col)

        _step_fades()
        w, h = console.size
        panel, scroll_offset = _render_editor(
            buffer,
            cursor_row,
            cursor_col,
            scroll_offset,
            width=w,
            height=h,
            story_id=story.id,
            fades=btn_fades,
            shimmer_tick=time.monotonic() - _ed_anim0,
        )
        live.update(panel)
