"""Screen builders for version control and issue tracking wizard steps.

# See docs: "Architecture" — UI rendering layer for the setup wizard.
# Each function builds a Rich renderable for a specific wizard screen.
# These are pure rendering functions with no side effects.
"""

from __future__ import annotations

from typing import Any

from rich.align import Align
from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.provider_select._constants import _ISSUE_TRACKING_FIELDS, _VC_OPTIONS, TOKEN_HELP
from yeaboi.ui.provider_select.screens._screens import (
    _ACCENT,
    _FRAME_FOOTER_H,
    _FRAME_HEADER_H,
    _HINT_MUTED,
    _HINT_URL,
    _HINT_URL_RE,
    _build_provider_row,
    _build_scope_text,
    _build_screen_frame,
    _link_target,
    _linkify,
)


def _build_hint_text(hint: str) -> Text:
    """Render a where-to-get-it hint with an info glyph and clickable URL.

    The leading ``ⓘ`` glyph (accent blue) flags the line as help; the lead-in
    prose is soft blue-grey; any URL/domain token is brightened, underlined and
    rendered as a clickable OSC-8 hyperlink so the actionable part stands out.
    Hints without a URL render entirely in the muted style. Pure rendering —
    returns a centered Rich ``Text``.
    """
    text = Text(justify="center")
    text.append("ⓘ  ", style=_ACCENT)  # ⓘ info glyph
    match = _HINT_URL_RE.search(hint)
    if match:
        url = _link_target(match.group(0))
        text.append(hint[: match.start()], style=_HINT_MUTED)
        text.append(match.group(0), style=f"{_HINT_URL} underline link {url}")
        text.append(hint[match.end() :], style=_HINT_MUTED)
    else:
        text.append(hint, style=_HINT_MUTED)
    return text


def _build_vc_select_screen(
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    visible: list[int] | None = None,
    fade_style: str = "",
    fade_indices: list[int] | None = None,
    shimmer_tick: float = 0.0,
    selected_style: str = "",
) -> Panel:
    """Build the version control provider selection screen."""
    show = visible if visible is not None else list(range(len(_VC_OPTIONS)))
    fading = fade_indices or []

    rows: list[Text] = []
    for i, vc in enumerate(_VC_OPTIONS):
        if i in show:
            if i == selected and selected_style:
                override = selected_style
            elif i in fading and fade_style:
                override = fade_style
            else:
                override = ""
            rows.append(
                _build_provider_row(
                    vc,
                    selected=(i == selected),
                    override_style=override,
                    shimmer_tick=shimmer_tick,
                )
            )

    body = [item for row in rows for item in (Align.center(row), Text(""))]
    if body:
        body = body[:-1]
    body_h = len(rows) * 3 - 1 if rows else 0

    return _build_screen_frame(
        subtitle="Version Control",
        step=3,
        body_items=body,
        body_height=body_h,
        width=width,
        height=height,
    )


def _build_vc_input_screen(
    vc: dict[str, Any],
    input_value: str,
    *,
    width: int = 80,
    height: int = 24,
    error: str = "",
    verified: bool | None = None,
    verifying: bool = False,
    input_fade: str = "",
    border_override: str = "",
) -> Panel:
    """Build the PAT token input screen for version control."""
    import rich.box

    # Provider identity is carried by the tall ANSI-Shadow frame title. The
    # "get yours at: <url>" line renders the URL as a clickable OSC-8 link; during
    # the fade-in animation we keep it flat so the whole line fades evenly.
    if input_fade:
        instructions = Text(vc["instructions"], style=input_fade, justify="center")
    else:
        instructions = _linkify(vc["instructions"], lead_style="dim", url_style=_HINT_URL)

    box_inner_w = min(70, width - 10) - 2 - 4
    display_val = "\u2022" * len(input_value)
    cursor = "\u2588" if not verifying else ""
    full_text = display_val + cursor
    avail = box_inner_w - 4
    text_style = input_fade if input_fade else "bold white"
    dim_style = input_fade if input_fade else "dim"

    input_content = Text(justify="left", no_wrap=True, overflow="crop")
    if len(full_text) <= avail:
        input_content.append("  " + full_text, style=text_style)
    else:
        visible = full_text[-(avail - 1) :]
        input_content.append(" \u25c2", style=dim_style)
        input_content.append(visible, style=text_style)

    if border_override:
        border_color = border_override
    elif input_fade:
        border_color = input_fade
    elif verified is True:
        border_color = "bright_green"
    elif verified is False or error:
        border_color = "bright_red"
    else:
        border_color = _ACCENT

    input_box = Panel(
        input_content,
        title=f" {vc['env_var']} ",
        title_align="left",
        border_style=border_color,
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=min(70, width - 10),
    )

    if verified is False:
        status_text = Text(f"\u2717 {error}", style="bright_red", justify="center")
    elif error:
        status_text = Text(error, style="bright_red", justify="center")
    else:
        status_text = Text("")

    # Required-scope line for the PAT — shown under the input box so the user
    # sees what access to grant it. Hidden during the fade-in so the intro reads
    # cleanly. GitHub is the only VC token, but keying off TOKEN_HELP keeps this
    # generic for any future provider.
    help_entry = TOKEN_HELP.get(vc["env_var"])
    show_scope = help_entry is not None and not input_fade

    body = [
        Align.center(instructions),
        Text(""),
        Align.center(input_box),
        Align.center(status_text),
    ]
    if show_scope:
        body.append(Align.center(_build_scope_text(help_entry["scope"])))
    body_h = 8 + (1 if show_scope else 0)

    return _build_screen_frame(
        subtitle="Enter your PAT token",
        step=3,
        body_items=body,
        body_height=body_h,
        width=width,
        height=height,
        title_text=vc["name"],
    )


def _build_field_box(
    field: dict[str, Any],
    value: str,
    *,
    is_active: bool,
    box_w: int,
    error: str = "",
    verified_flag: bool | None = None,
    border_override: str = "",
    fade_style: str = "",
) -> Panel:
    """Build a single input field box."""
    import rich.box

    # Display text — active fields NEVER show placeholder, only value + cursor.
    # Inactive fields show value if present, otherwise placeholder in dark grey.
    if is_active:
        if field["masked"] and value:
            display = "\u2022" * len(value)
        else:
            display = value
        display += "\u2588"
        text_style = fade_style if fade_style else "bold white"
    elif value:
        display = "\u2022" * len(value) if field["masked"] else value
        text_style = fade_style if fade_style else "dim white"
    else:
        display = field.get("placeholder", "")
        text_style = fade_style if fade_style else "rgb(40,40,40)"

    inner_w = box_w - 2 - 4 - 4
    dim_style = fade_style if fade_style else "dim"

    input_content = Text(justify="left", no_wrap=True, overflow="crop")
    if len(display) <= inner_w:
        input_content.append("  " + display, style=text_style)
    else:
        visible = display[-(inner_w - 1) :]
        input_content.append(" \u25c2", style=dim_style)
        input_content.append(visible, style=text_style)

    # Border colour
    if border_override:
        border_color = border_override
    elif fade_style:
        border_color = fade_style
    elif verified_flag is True:
        border_color = "bright_green"
    elif error:
        border_color = "bright_red"
    elif is_active:
        border_color = _ACCENT
    else:
        border_color = "rgb(60,60,60)"

    label = f" {field['label']} "
    if not field["required"]:
        label = f" {field['label']} (optional) "

    return Panel(
        input_content,
        title=label,
        title_align="left",
        border_style=border_color,
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=box_w,
    )


def _build_issue_tracking_screen(
    selected: int,
    values: dict[int, str],
    *,
    width: int = 80,
    height: int = 24,
    scroll_offset: int = 0,
    errors: dict[int, str] | None = None,
    verified: dict[int, bool] | None = None,
    border_overrides: dict[int, str] | None = None,
    fade_style: str = "",
    fields: list[dict[str, Any]] | None = None,
    subtitle: str = "Issue tracking",
    title_text: str = "",
    step: int = 1,
) -> Panel:
    """Build the issue tracking multi-field form screen with viewport scrolling.

    scroll_offset: index of the first visible field (0-based).
    Fields that don't fit in the available height are clipped; scroll indicators
    (^/v) show when there's content above or below.
    fields: optional field definitions to use instead of the default Jira fields.
    subtitle: context line shown under the title (e.g. "Issue tracking", "Docs").
    title_text: tall ANSI-Shadow title (e.g. "Jira", "Notion"). Defaults to "Setup".
    step: progress-bar step index into ``_STEPS``. This generic form renders both
        the Issue Tracking step (1) and the Docs step (2, Notion/Confluence), so the
        active chip is caller-driven rather than hardcoded. Defaults to 1.
    """
    errors = errors or {}
    verified = verified or {}
    border_overrides = border_overrides or {}
    active_fields = fields if fields is not None else _ISSUE_TRACKING_FIELDS

    box_w = min(70, width - 10)
    field_h = 5  # each field box is 5 lines tall (padding 1 + content 1 + padding 1 + 2 border)

    # Calculate available height for fields. The frame reserves _FRAME_HEADER_H
    # (6-row ANSI-Shadow title + subtitle) and _FRAME_FOOTER_H (progress); we also
    # reserve 1 row for the keyboard-hint footer + 1 safety margin so the (non-
    # scrolling) field stack never overflows the frame.
    inner_h = height - 4
    chrome_h = _FRAME_HEADER_H + _FRAME_FOOTER_H + 2
    fields_available_h = max(field_h, inner_h - chrome_h)
    max_visible = fields_available_h // field_h

    n = len(active_fields)

    # Clamp scroll_offset so selected field is always visible
    if selected < scroll_offset:
        scroll_offset = selected
    elif selected >= scroll_offset + max_visible:
        scroll_offset = selected - max_visible + 1
    scroll_offset = max(0, min(scroll_offset, n - max_visible))

    visible_end = min(scroll_offset + max_visible, n)

    body: list = []
    body_h = 0

    # Visible fields
    for vi, i in enumerate(range(scroll_offset, visible_end)):
        field = active_fields[i]
        is_active = i == selected
        val = values.get(i, "")
        err = errors.get(i, "")

        input_box = _build_field_box(
            field,
            val,
            is_active=is_active,
            box_w=box_w,
            error=err,
            verified_flag=verified.get(i),
            border_override=border_overrides.get(i, ""),
            fade_style=fade_style,
        )

        body.append(Align.center(input_box))
        body_h += field_h

        # Error text
        if err and is_active:
            body.append(Text(f"  {err}", style="bright_red", justify="center"))
            body_h += 1

        # Where-to-get-it hint for the focused field — mirrors the LLM and
        # GitHub steps, which show a "Get yours at: …" line. Only the active
        # field's hint is shown (keeps the stack uncluttered); it's suppressed
        # while an error is on screen or the verify animation is running
        # (border_overrides) so those states read cleanly.
        hint = field.get("hint", "")
        if hint and is_active and not err and not border_overrides:
            body.append(_build_hint_text(hint))
            body_h += 1

        # Required-scope line for credential-token fields (Azure DevOps PAT, Jira/
        # Notion/Confluence tokens) — shown under the where-to-get-it hint on the
        # focused field so the user sees both where to create the token and what
        # access to grant it. Same suppression rules as the hint above.
        help_entry = TOKEN_HELP.get(field["env_var"])
        if help_entry and is_active and not err and not border_overrides:
            body.append(_build_scope_text(help_entry["scope"]))
            body_h += 1

    # Keyboard hint — makes editing/clearing/skipping discoverable, matching the
    # LLM API-key screen. Hidden while verifying (border_overrides drives the
    # pulse + success flash) so the animation reads cleanly.
    if not border_overrides:
        body.append(Text(""))
        body.append(
            Text(
                "⌫ backspace  ·  Ctrl+U clear  ·  Enter to verify  ·  Esc back",
                style="dim",
                justify="center",
            )
        )
        body_h += 2

    return _build_screen_frame(
        subtitle=subtitle,
        step=step,
        body_items=body,
        body_height=body_h,
        width=width,
        height=height,
        title_text=title_text,
    )
