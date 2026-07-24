"""Pipeline, chat, and edit prompt screen builders for the TUI session.

# See docs: "Architecture" — pure functions that build Rich Panel screens.
# Each function takes state and returns a Panel renderable. No side effects.
"""

from __future__ import annotations

import math
import re

import rich.box
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.session._utils import _pad_left, _wrap_text
from yeaboi.ui.session.screens._screens import _build_action_bar, _planning_title
from yeaboi.ui.session.screens._screens_input import _image_hint
from yeaboi.ui.shared._animations import scrollbar_column
from yeaboi.ui.shared._components import PAD
from yeaboi.ui.shared._scroll import publish_geometry

_PAD = PAD

# Consistent width for all input boxes across screens (question, chat, edit).
_INPUT_BOX_W_MAX = 74


# ---------------------------------------------------------------------------
# Screen builders — Pipeline Stage (Phase D)
# ---------------------------------------------------------------------------


def _build_pipeline_screen(
    stage_label: str,
    progress: str,
    content_lines: list[str],
    scroll_offset: int,
    menu_selected: int,
    *,
    status: str = "complete",
    width: int = 80,
    height: int = 24,
    tick: float = 0.0,
    status_msg: str = "",
    btn_fades: list[float] | None = None,
    step: int = 0,
    total: int = 5,
    sticky_headers: list[tuple[int, str]] | None = None,
    actions: list[str] | None = None,
    warning_text: str = "",
    popup_msg: str = "",
    popup_options: list[str] | None = None,
    popup_selected: int = 0,
    popup_t: float = 0.0,
    popup_pulse: float = 0.0,
    shimmer_tick: float | None = None,
    scroll_meta: dict | None = None,
) -> Panel:
    """Build the pipeline stage screen (processing + result).

    status: "processing" shows pulsing border + spinner, "complete" shows artifact + menu.
    step/total: pipeline step for the progress bar (e.g. 1/5).
    sticky_headers: list of (line_index, ansi_text) for group headers that pin
        at the top of the viewport when scrolled past.
    actions: list of action button labels (default: ["Accept", "Edit", "Export"]).
        The story_writer stage uses ["Accept", "Edit", "Regenerate", "Export"].
    warning_text: optional warning banner shown above the content (e.g. capacity warning).
    popup_msg/popup_options/popup_selected/popup_t/popup_pulse: popup overlay params,
        matching the delete confirmation pattern from mode_select/_project_cards.py.
    """
    if actions is None:
        actions = ["Accept", "Edit", "Export"]
    title = _planning_title(shimmer_tick)

    # Subtitle: stage label + inline progress bar
    sub = Text(_PAD, justify="left")
    sub.append(stage_label, style="dim")
    if total > 0:
        bar_w = 15
        filled = int(bar_w * step / total) if total else 0
        empty = bar_w - filled
        sub.append("  ")
        sub.append("\u2501" * filled, style="rgb(70,100,180)")
        sub.append("\u2501" * empty, style="rgb(40,40,50)")
        sub.append(f"  {step}/{total}", style="dim")

    inner_h = height - 4
    header_h = 10
    action_h = 4  # blank + 3 button lines

    # Warning banner (e.g. capacity warning) — rendered between subtitle and content.
    # Consumes lines from the viewport so content doesn't get clipped.
    warning_lines: list[Text] = []
    if warning_text and status == "complete":
        wrap_w = width - 12
        for wl in _wrap_text(warning_text, wrap_w):
            warning_lines.append(Text(_PAD + wl, style="rgb(200,160,60)", justify="left"))
        warning_lines.append(Text(""))  # blank separator

    warning_h = len(warning_lines)
    viewport_h = max(3, inner_h - header_h - action_h - warning_h)

    body: list = []

    if status == "processing":
        # Show content lines if provided (e.g. Jira sync progress log),
        # otherwise fall back to generic "Processing..." with pulsing dots.
        body.append(Text(""))
        if content_lines:
            visible = content_lines[-(viewport_h - 2) :] if len(content_lines) > viewport_h - 2 else content_lines
            for line in visible:
                body.append(Text.from_ansi(_PAD + line, justify="left"))
            for _ in range(max(0, viewport_h - 2 - len(visible))):
                body.append(Text(""))
        else:
            intensity = (math.sin(tick * 6) + 1) / 2
            v = int(60 + 140 * intensity)
            dots = "." * (int(tick * 3) % 4)
            body.append(Text(""))
            body.append(Text(_PAD + f"  Processing{dots}", style=f"rgb({v},{v},{v})", justify="left"))
            for _ in range(max(0, viewport_h - 5)):
                body.append(Text(""))
        border_style = "white"
    else:
        # Scrollable content with action bar.
        # Sticky headers consume 2 lines (header + padding) when active.
        # Use the reduced effective_h for max_scroll even at scroll_offset=0
        # so the scrollbar thumb size stays consistent (no jump on first scroll).
        has_sticky_headers = bool(sticky_headers)
        effective_h = viewport_h - 2 if has_sticky_headers else viewport_h
        max_scroll = max(0, len(content_lines) - effective_h)
        scroll_offset = max(0, min(scroll_offset, max_scroll))
        # Hand the real geometry back to the scroll loop so its counter clamps to
        # exactly what's displayed (see yeaboi.ui.shared._scroll.publish_geometry).
        publish_geometry(scroll_meta, max_scroll, effective_h)
        has_above = scroll_offset > 0

        # Determine sticky header — find the most recent group header above viewport.
        # When the next section's header is approaching the viewport top, morph the
        # pinned text character-by-character from the current to the next header
        # (decryption-style scramble over morph_steps scroll positions).
        morph_steps = 8
        glitch_chars = "abcdefghijklmnopqrstuvwxyz"
        pinned_text: Text | None = None
        if sticky_headers and has_above:
            # Find current header (most recent above viewport)
            current_hdr: str | None = None
            next_hdr: str | None = None
            next_hdr_dist = 0  # how many lines until next header enters viewport
            for hdr_idx, hdr_text in reversed(sticky_headers):
                if hdr_idx < scroll_offset:
                    current_hdr = hdr_text
                    break

            # Find the next header at or below scroll_offset (approaching from below)
            if current_hdr is not None:
                for hdr_idx, hdr_text in sticky_headers:
                    if hdr_idx >= scroll_offset:
                        next_hdr = hdr_text
                        next_hdr_dist = hdr_idx - scroll_offset
                        break

            if current_hdr is not None:
                # Strip ANSI to get plain text for morphing
                import random

                cur_plain = re.sub(r"\x1b\[[0-9;]*m", "", current_hdr).strip()
                if next_hdr is not None and next_hdr_dist < morph_steps:
                    nxt_plain = re.sub(r"\x1b\[[0-9;]*m", "", next_hdr).strip()
                    # Morph progress: 1.0 = fully next, 0.0 = fully current
                    morph_progress = 1.0 - (next_hdr_dist / morph_steps)
                    # Build morphed string character by character
                    max_len = max(len(cur_plain), len(nxt_plain))
                    morphed = Text(justify="left")
                    rng = random.Random(scroll_offset)  # noqa: S311 - deterministic UI text scramble, not security
                    for ci in range(max_len):
                        cur_ch = cur_plain[ci] if ci < len(cur_plain) else " "
                        nxt_ch = nxt_plain[ci] if ci < len(nxt_plain) else " "
                        if rng.random() < morph_progress:
                            # Show next char or glitch
                            if rng.random() < 0.4 and morph_progress < 0.9:
                                morphed.append(rng.choice(glitch_chars), style="rgb(70,100,180)")
                            else:
                                morphed.append(nxt_ch, style="bold rgb(70,100,180)")
                        else:
                            morphed.append(cur_ch, style="bold rgb(70,100,180)")
                    pinned_text = morphed
                else:
                    pinned_text = Text.from_ansi(current_hdr)

        # Always reserve space for the sticky header when headers exist,
        # even at scroll_offset=0. This prevents the viewport from jumping
        # by 2 lines when the sticky header first activates on scroll.
        content_viewport_h = effective_h
        if has_sticky_headers:
            if pinned_text is not None:
                body.append(Text(_PAD, justify="left").append_text(pinned_text))
            elif sticky_headers:
                # At scroll_offset=0: show the first header as the pinned element
                body.append(Text(_PAD, justify="left").append_text(Text.from_ansi(sticky_headers[0][1])))
            body.append(Text(""))  # padding between header and content

        # Hide header lines in the viewport that are represented by the pinned header.
        # Always hide the currently pinned header from the scrollable content so
        # it doesn't appear twice (once pinned, once in-line).
        _hide_lines: set[int] = set()
        if has_sticky_headers:
            for hdr_idx, _ in sticky_headers:
                if hdr_idx >= scroll_offset and hdr_idx < scroll_offset + content_viewport_h:
                    _hide_lines.add(hdr_idx)

        # Build scrollbar using effective_h for consistent thumb size.
        sb = scrollbar_column(effective_h, len(content_lines), scroll_offset)
        while len(sb) < content_viewport_h:
            sb.append("")
        # Consistent width so the scrollbar forms a right-aligned column
        content_w = width - 8  # panel border(2) + padding(4) + scrollbar(2)

        visible_count = 0
        line_idx = scroll_offset
        while visible_count < content_viewport_h and line_idx < len(content_lines):
            if line_idx in _hide_lines:
                line_idx += 1
                continue
            row = Text.from_ansi(_PAD + content_lines[line_idx], justify="left")
            if sb[visible_count]:
                pad_needed = max(0, content_w - row.cell_len)
                row.append(" " * pad_needed)
                row.append_text(Text.from_markup(sb[visible_count]))
            body.append(row)
            visible_count += 1
            line_idx += 1
        for i in range(visible_count, content_viewport_h):
            row = Text("", justify="left")
            if sb[i]:
                row.append(" " * content_w)
                row.append_text(Text.from_markup(sb[i]))
            body.append(row)

        body.append(Text(""))
        btn_top, btn_mid, btn_bot = _build_action_bar(actions, menu_selected, fades=btn_fades)
        body.append(btn_top)
        body.append(btn_mid)
        body.append(btn_bot)

        if status_msg:
            body.append(Text(_PAD + status_msg, style="bright_green", justify="left"))

        border_style = "white"

    # Popup overlay — renders "on top" of the bottom of the screen.
    popup_rendered: list[Text] = []
    if popup_msg and popup_options and popup_t > 0 and status == "complete":
        popup_rendered = _build_popup_overlay(popup_msg, popup_options, popup_selected, popup_t, popup_pulse, width)
        # Remove body lines from the bottom to make room
        while len(popup_rendered) > 0 and len(body) > 0:
            body_target = inner_h - header_h - warning_h - len(popup_rendered)
            if len(body) > body_target:
                body.pop()
            else:
                break

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *warning_lines,
        *body,
        *popup_rendered,
    )

    return Panel(
        content,
        border_style=border_style,
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_popup_overlay(
    popup_msg: str,
    popup_options: list[str],
    popup_selected: int,
    popup_t: float,
    popup_pulse: float,
    width: int,
) -> list[Text]:
    """Build the popup overlay lines for the pipeline screen.

    Returns a list of Text lines to render at the bottom of the screen.
    Matches the delete confirmation popup pattern from mode_select/_project_cards.py.
    """
    panel_inner_w = width - 6  # panel border(2) + panel padding(4)

    # Build option lines
    opt_lines: list[str] = []
    for i, opt in enumerate(popup_options):
        marker = "\u203a" if i == popup_selected else " "
        opt_lines.append(f"  {marker} {opt}")

    # Widest line determines popup width
    all_text = [popup_msg] + opt_lines
    max_line = max(len(line) for line in all_text)
    popup_w = min(panel_inner_w, max(40, max_line + 8))
    inner_w = popup_w - 2

    # Amber pulsing border (sine-wave oscillation)
    dark_amber = (160, 120, 30)
    bright_amber = (255, 200, 60)
    osc = (math.cos(popup_pulse * 3) + 1) / 2
    pr = int(dark_amber[0] + (bright_amber[0] - dark_amber[0]) * osc)
    pg = int(dark_amber[1] + (bright_amber[1] - dark_amber[1]) * osc)
    pb = int(dark_amber[2] + (bright_amber[2] - dark_amber[2]) * osc)
    popup_border = f"rgb({pr},{pg},{pb})"

    h_pad = " " * max(0, (panel_inner_w - popup_w) // 2)

    rendered: list[Text] = []

    # Top border
    line_top = Text(h_pad, justify="left")
    line_top.append("\u256d" + "\u2500" * inner_w + "\u256e", style=popup_border)
    rendered.append(line_top)

    # Blank line
    lb1 = Text(h_pad, justify="left")
    lb1.append("\u2502" + " " * inner_w + "\u2502", style=popup_border)
    rendered.append(lb1)

    # Message line — centered
    msg_pad_l = max(0, (inner_w - len(popup_msg)) // 2)
    msg_pad_r = max(0, inner_w - len(popup_msg) - msg_pad_l)
    lm = Text(h_pad, justify="left")
    lm.append("\u2502", style=popup_border)
    lm.append(" " * msg_pad_l + popup_msg + " " * msg_pad_r, style="bold white")
    lm.append("\u2502", style=popup_border)
    rendered.append(lm)

    # Blank line
    lb2 = Text(h_pad, justify="left")
    lb2.append("\u2502" + " " * inner_w + "\u2502", style=popup_border)
    rendered.append(lb2)

    # Option lines
    for i, opt_text in enumerate(opt_lines):
        opt_pad_r = max(0, inner_w - len(opt_text))
        lo = Text(h_pad, justify="left")
        lo.append("\u2502", style=popup_border)
        style = "bold white" if i == popup_selected else "dim"
        lo.append(opt_text + " " * opt_pad_r, style=style)
        lo.append("\u2502", style=popup_border)
        rendered.append(lo)

    # Blank line
    lb3 = Text(h_pad, justify="left")
    lb3.append("\u2502" + " " * inner_w + "\u2502", style=popup_border)
    rendered.append(lb3)

    # Bottom border
    line_bot = Text(h_pad, justify="left")
    line_bot.append("\u2570" + "\u2500" * inner_w + "\u256f", style=popup_border)
    rendered.append(line_bot)

    # Slide-up: trim based on popup_t animation (0→1)
    popup_total_h = len(rendered)
    visible_popup_lines = max(0, int(popup_total_h * popup_t))
    return rendered[:visible_popup_lines]


# ---------------------------------------------------------------------------
# Screen builders — Chat (Phase E)
# ---------------------------------------------------------------------------


def _build_chat_screen(
    messages: list[tuple[str, str]],
    input_value: str,
    scroll_offset: int,
    *,
    width: int = 80,
    height: int = 24,
    processing: bool = False,
    tick: float = 0.0,
    shimmer_tick: float | None = None,
    scroll_meta: dict | None = None,
    notice: str = "",
) -> Panel:
    """Build the post-pipeline chat screen.

    messages: list of (role, text) tuples — role is "user" or "ai".
    shimmer_tick: if set, animates the title's travelling highlight.
    notice: transient status text under the input box (e.g. Ctrl+V image paste).
    """
    from yeaboi.ui.shared._animations import loading_border_color

    title = _planning_title(shimmer_tick)
    sub = Text(_PAD + "Plan complete \u2014 ask questions or type 'export' to save", style="dim", justify="left")

    inner_h = height - 4
    header_h = 10
    input_h = 6  # input box area + hint/notice line
    viewport_h = max(3, inner_h - header_h - input_h)

    # Render messages to lines
    msg_lines: list[str] = []
    for role, text in messages:
        label = "You:" if role == "user" else "Scrum AI:"
        msg_lines.append(f"{label} {text[: width - 20]}")
        # Wrap long messages
        remaining = text[width - 20 :]
        while remaining:
            msg_lines.append(f"  {remaining[: width - 16]}")
            remaining = remaining[width - 16 :]
        msg_lines.append("")

    max_scroll = max(0, len(msg_lines) - viewport_h)
    scroll_offset = max(0, min(scroll_offset, max_scroll))
    # Hand the real geometry (msg_lines is built here, not in the loop) back so
    # the chat loop can clamp/jump-to-bottom exactly. See _scroll.publish_geometry.
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    has_above = scroll_offset > 0
    has_below = scroll_offset + viewport_h < len(msg_lines)

    body: list = []

    if has_above:
        body.append(Text(_PAD + "\u25b2 scroll up", style="dim", justify="left"))
    else:
        body.append(Text(""))

    visible = msg_lines[scroll_offset : scroll_offset + viewport_h]
    for line in visible:
        body.append(Text(_PAD + line, justify="left"))
    for _ in range(len(visible), viewport_h):
        body.append(Text(""))

    if has_below:
        body.append(Text(_PAD + "\u25bc scroll down", style="dim", justify="left"))
    else:
        body.append(Text(""))

    # Input box
    box_w = min(_INPUT_BOX_W_MAX, width - 12)
    if processing:
        border_color = loading_border_color(tick)
        display = "Processing..."
        text_style = "dim"
    else:
        display = (input_value + "\u2588") if input_value else "\u2588"
        text_style = "bold white"
        border_color = "white"

    input_content = Text(justify="left", no_wrap=True, overflow="crop")
    input_content.append("  " + display, style=text_style)

    input_box = Panel(
        input_content,
        title=" Message ",
        title_align="left",
        border_style=border_color,
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=box_w,
    )
    body.append(Text(""))
    body.append(_pad_left(input_box))
    if notice:
        body.append(Text(_PAD + notice, style="bold white", justify="left"))
    else:
        body.append(Text(_PAD + "Enter send · Esc back" + _image_hint(), style="dim", justify="left"))

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
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
# Screen builder — Edit feedback prompt
# ---------------------------------------------------------------------------


def _build_edit_prompt_screen(
    prompt_text: str,
    input_value: str,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float | None = None,
    notice: str = "",
) -> Panel:
    """Build a screen prompting for edit feedback (used by both intake and pipeline edits).

    notice: if set, replaces the submit hint with this transient status text
        (e.g. Ctrl+V image-paste feedback).
    """
    title = _planning_title(shimmer_tick)
    sub = Text(_PAD + "What changes would you like?", style="dim", justify="left")

    body: list = []
    for line in _wrap_text(prompt_text, width - 12):
        body.append(Text(_PAD + line, style="white", justify="left"))
    body.append(Text(""))

    box_w = min(_INPUT_BOX_W_MAX, width - 12)
    display = (input_value + "\u2588") if input_value else "\u2588"
    input_content = Text(justify="left", no_wrap=True, overflow="crop")
    input_content.append("  " + display, style="bold white")

    input_box = Panel(
        input_content,
        title=" Your feedback ",
        title_align="left",
        border_style="white",
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=box_w,
    )
    body.append(_pad_left(input_box))
    body.append(Text(""))
    if notice:
        body.append(Text(_PAD + notice, style="bold white", justify="left"))
    else:
        body.append(Text(_PAD + "Enter/Ctrl+S submit \u00b7 Esc cancel" + _image_hint(), style="dim", justify="left"))

    inner_h = height - 4
    header_h = 10
    body_h = len(body)
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
