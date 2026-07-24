"""Screen builder functions for the provider selection wizard.

# See docs: "Architecture" — UI rendering layer for the setup wizard.
# Each function builds a Rich renderable for a specific wizard screen.
# These are pure rendering functions with no side effects.
"""

from __future__ import annotations

import re
from typing import Any

from rich.align import Align
from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.provider_select._constants import _PROVIDER_CARDS, TOKEN_HELP
from yeaboi.ui.provider_select._verification import _validate_key
from yeaboi.ui.shared._animations import shimmer_style
from yeaboi.ui.shared._ascii_font import render_ascii_text
from yeaboi.ui.shared._wordmarks import get_shadow_wordmark

# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

_STEPS = ["LLM Provider", "Issue Tracking", "Docs", "Version Control"]

# Brand blue accent — the same rgb the app's project cards / action buttons and
# the setup intro wordmark use. Applied to the frame border, title, idle input
# borders and the active progress step so the whole wizard reads blue, not grey.
_ACCENT = "rgb(70,100,180)"

# The frame title always reserves this many rows (the tall ANSI-Shadow wordmark
# height) so the layout is stable whether a screen's title renders as the tall
# wordmark or falls back to the compact 2-line font on a narrow terminal. Kept in
# sync with the per-screen vertical budgets below (model list / issue-tracking).
_FRAME_TITLE_ROWS = 6
_FRAME_HEADER_H = _FRAME_TITLE_ROWS + 3  # title + blank + subtitle + blank
_FRAME_FOOTER_H = 2  # blank + progress bar

# Field-hint palette. The where-to-get-it line is styled as *help*, distinct from
# the pure-dim keyboard footer and the red error line: soft blue-grey lead-in
# prose with a brighter/underlined + clickable URL so the eye lands on the
# actionable part. Shared with _screens_vc.py's hint renderer.
_HINT_MUTED = "rgb(120,130,150)"
_HINT_URL = "rgb(140,170,235)"
_SCOPE_LOCK = "rgb(150,150,120)"

# Matches the first URL / domain-path token in a hint (the actionable bit): a
# full http(s) URL, or a lowercase domain (any 2+ letter TLD — .com, .so, .net…)
# with an optional path. Bounded by whitespace/commas so trailing prose
# ("…, then share …") isn't swallowed, and lowercase-only so uppercase prose like
# "MYPROJ-123" never false-matches.
_HINT_URL_RE = re.compile(r"https?://[^\s,]+|[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(?:/[^\s,]*)?")


def _link_target(matched: str) -> str:
    """Full https URL for an OSC-8 hyperlink from a matched URL/domain token."""
    return matched if matched.startswith("http") else f"https://{matched}"


def _linkify(text: str, *, lead_style: str, url_style: str, justify: str = "center") -> Text:
    """Render prose with its first URL/domain token as a clickable OSC-8 link.

    The matched URL span carries a ``link <url>`` attribute (Rich emits an OSC-8
    hyperlink) plus ``url_style`` + underline; surrounding prose uses
    ``lead_style``. Text with no URL renders entirely in ``lead_style``. Pure
    rendering — returns a Rich ``Text``.
    """
    out = Text(justify=justify)
    match = _HINT_URL_RE.search(text)
    if match:
        url = _link_target(match.group(0))
        out.append(text[: match.start()], style=lead_style)
        out.append(match.group(0), style=f"{url_style} underline link {url}")
        out.append(text[match.end() :], style=lead_style)
    else:
        out.append(text, style=lead_style)
    return out


def _build_scope_text(scope: str, *, style: str = _HINT_MUTED) -> Text:
    """Render a required-scope help line for a credential token.

    A leading lock glyph flags the line as an access requirement; the scope text
    is rendered in the muted help style. Pairs with the where-to-get-it link so a
    user filling in a token sees both *where to create it* and *what access to
    grant it*. Pure rendering.

    The line is forced to a single visual row (``no_wrap`` + ellipsis): the
    callers reserve exactly one row in their ``body_height`` math, so a wrapped
    scope would drift the frame's centering / progress footer. Full scope shows on
    normal-width terminals and truncates gracefully on narrow ones (mirrors the
    settings dashboard's token-help lines).
    """
    text = Text(justify="center", no_wrap=True, overflow="ellipsis")
    text.append("\U0001f512  ", style=_SCOPE_LOCK)  # 🔒 lock glyph
    text.append(f"Scope: {scope}", style=style)
    return text


def _build_progress(current_step: int) -> Text:
    """Build a progress bar of space-separated filled/empty parallelogram blocks."""
    active_bg = _ACCENT
    done_bg = "rgb(30,80,50)"
    bar = Text(justify="center")
    for i, label in enumerate(_STEPS):
        if i < current_step:
            bar.append("\u259f", style=f"{done_bg} on default")
            bar.append(f" {label} ", style=f"bold white on {done_bg}")
            bar.append("\u259b", style=f"{done_bg} on default")
        elif i == current_step:
            bar.append("\u259f", style=f"{active_bg} on default")
            bar.append(f" {label} ", style=f"bold white on {active_bg}")
            bar.append("\u259b", style=f"{active_bg} on default")
        else:
            dim_bg = "rgb(35,35,45)"
            bar.append("\u259f", style=f"{dim_bg} on default")
            bar.append(f" {label} ", style=f"dim on {dim_bg}")
            bar.append("\u259b", style=f"{dim_bg} on default")
        if i < len(_STEPS) - 1:
            bar.append("  ")
    return bar


def _build_provider_row(
    provider: dict[str, Any], *, selected: bool, override_style: str = "", shimmer_tick: float = 0.0
) -> Text:
    """Render a provider name as two-line ASCII art text."""
    lines = render_ascii_text(provider["name"])
    rendered = Text(justify="center")

    if override_style:
        rendered.append(lines[0] + "\n", style=override_style)
        rendered.append(lines[1], style=override_style)
    elif selected:
        # Per-character shimmer effect on selected item
        total = max(len(lines[0]), len(lines[1]))
        for i, ch in enumerate(lines[0]):
            rendered.append(ch, style=shimmer_style(provider["color"], i, total, shimmer_tick))
        rendered.append("\n")
        for i, ch in enumerate(lines[1]):
            rendered.append(ch, style=shimmer_style(provider["color"], i, total, shimmer_tick))
    else:
        rendered.append(lines[0] + "\n", style="dim")
        rendered.append(lines[1], style="dim")

    return rendered


def _frame_title_rows(word: str, inner_w: int) -> list[str]:
    """Return exactly ``_FRAME_TITLE_ROWS`` equal-width rows for *word*'s title art.

    Uses the tall ANSI-Shadow wordmark when one is baked for *word* and it fits
    ``inner_w`` (provider/integration names + "Setup" all have bakes); otherwise
    falls back to the compact two-line font, vertically centred in the reserved
    block so the header height stays constant. Mirrors ``_components._title_rows``
    but stays centre-justifiable (the setup frame centres its whole header).
    """
    shadow = get_shadow_wordmark(word)
    if shadow is not None and len(shadow[0]) <= inner_w:
        return shadow

    lines = render_ascii_text(word)
    block_w = max((len(line) for line in lines), default=0)
    lines = [line.ljust(block_w) for line in lines]
    pad_total = _FRAME_TITLE_ROWS - len(lines)
    top = pad_total // 2
    return [" " * block_w] * top + lines + [" " * block_w] * (pad_total - top)


def _build_screen_frame(
    *,
    subtitle: str,
    step: int,
    body_items: list,
    body_height: int,
    width: int = 80,
    height: int = 24,
    title_text: str = "",
) -> Panel:
    """Shared screen frame: ASCII title at top, subtitle + progress at bottom.

    body_items: list of Rich renderables to vertically centre in the middle.
    body_height: estimated line count of body_items (for centering math).
    title_text: text to render as ASCII art title. Defaults to "Setup". Rendered
    in the tall ANSI-Shadow font (brand blue) to match the app's mode screens.
    """
    import rich.box
    from rich.console import Group

    display_title = title_text or "Setup"
    title_rows = _frame_title_rows(display_title, max(10, width - 6))
    # Left-justify the equal-width block and let Align.center (below) centre it as a
    # unit — per-line justify="center" drifts because rows have different trailing
    # whitespace. (Same approach as shared._components.build_ascii_title.)
    title = Text(justify="left")
    for idx, row in enumerate(title_rows):
        title.append(row, style=f"bold {_ACCENT}")
        if idx < len(title_rows) - 1:
            title.append("\n")

    sub = Text(subtitle, style="dim", justify="center")
    progress = _build_progress(current_step=step)

    inner_h = height - 4  # panel border + padding
    middle_h = max(0, inner_h - _FRAME_HEADER_H - _FRAME_FOOTER_H)
    mid_top = max(0, (middle_h - body_height) // 2)
    mid_bot = max(0, middle_h - body_height - mid_top)

    content = Group(
        Align.center(title),
        Text(""),
        Align.center(sub),
        Text(""),
        *[Text("") for _ in range(mid_top)],
        *body_items,
        *[Text("") for _ in range(mid_bot)],
        Text(""),
        Align.center(progress),
    )

    return Panel(
        content,
        border_style=_ACCENT,
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_select_screen(
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    visible: list[int] | None = None,
    step: int = 0,
    fade_style: str = "",
    fade_indices: list[int] | None = None,
    shimmer_tick: float = 0.0,
    selected_style: str = "",
) -> Panel:
    """Build the provider selection screen."""
    show = visible if visible is not None else list(range(len(_PROVIDER_CARDS)))
    fading = fade_indices or []

    rows: list[tuple[int, Text]] = []
    for i, p in enumerate(_PROVIDER_CARDS):
        if i in show:
            if i == selected and selected_style:
                override = selected_style
            elif i in fading and fade_style:
                override = fade_style
            else:
                override = ""
            rows.append(
                (
                    i,
                    _build_provider_row(
                        p,
                        selected=(i == selected),
                        override_style=override,
                        shimmer_tick=shimmer_tick,
                    ),
                )
            )

    body: list = []
    body_h = 0
    for pos, (i, row) in enumerate(rows):
        body.append(Align.center(row))
        body_h += 2
        # The selected card's separator row doubles as its tagline — a one-line
        # "what am I signing up for" (e.g. Ollama's "Free · local · no API
        # key") so the free option is visible before any card is entered.
        tagline = _PROVIDER_CARDS[i].get("tagline", "") if i == selected else ""
        if tagline:
            body.append(Align.center(Text(tagline, style="dim")))
            body_h += 1
        elif pos < len(rows) - 1:
            body.append(Text(""))
            body_h += 1

    return _build_screen_frame(
        subtitle="Select your LLM provider",
        step=step,
        body_items=body,
        body_height=body_h,
        width=width,
        height=height,
    )


def _build_input_screen(
    provider: dict[str, Any],
    input_value: str,
    *,
    width: int = 80,
    height: int = 24,
    error: str = "",
    masked: bool = True,
    verified: bool | None = None,
    verifying: bool = False,
    input_fade: str = "",
    border_override: str = "",
) -> Panel:
    """Build the API key input screen.

    verified: None=not checked, True=verified OK, False=verification failed.
    verifying: True while the verification API call is in progress.
    input_fade: override style for fade-in animation on the input elements.
    """
    import rich.box

    # The provider identity is carried by the tall ANSI-Shadow frame title now, so
    # the body starts at the instructions (no duplicate compact provider art).
    # The "get yours at: <url>" line renders the URL as a clickable OSC-8 link;
    # during the fade-in animation we keep it flat so the whole line fades evenly.
    if input_fade:
        instructions = Text(provider["instructions"], style=input_fade, justify="center")
    else:
        instructions = _linkify(provider["instructions"], lead_style="dim", url_style=_HINT_URL)

    # Realtime format validation
    status, validation_hint = _validate_key(provider, input_value)

    # Input box content — env var label goes in the panel border title.
    # Scroll: only show the rightmost chars that fit in one line.
    box_inner_w = min(70, width - 10) - 2 - 4  # panel border(2) + padding(4)
    # Bedrock uses a region name and Ollama a base URL (not secrets) — never mask
    if provider.get("is_region_input") or provider.get("is_base_url_input"):
        masked = False
    display_val = "\u2022" * len(input_value) if masked else input_value
    cursor = "\u2588" if not verifying else ""
    full_text = display_val + cursor
    avail = box_inner_w - 4  # reserve space for overflow indicators + padding
    text_style = input_fade if input_fade else "bold white"
    dim_style = input_fade if input_fade else "dim"

    input_content = Text(justify="left", no_wrap=True, overflow="crop")
    if len(full_text) <= avail:
        input_content.append("  " + full_text, style=text_style)
    else:
        visible = full_text[-(avail - 1) :]
        input_content.append(" \u25c2", style=dim_style)
        input_content.append(visible, style=text_style)

    # Border colour logic
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
        title=f" {provider['env_var']} ",
        title_align="left",
        border_style=border_color,
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=min(70, width - 10),
    )

    # Status line below input
    if verifying:
        status_text = Text("")
    elif verified is True:
        status_text = Text("")
    elif verified is False:
        status_text = Text(f"\u2717 {error}", style="bright_red", justify="center")
    elif validation_hint and input_value and status in ("bad_prefix", "too_short"):
        hint_style = "bright_red" if status == "bad_prefix" else "yellow"
        status_text = Text(validation_hint, style=input_fade or hint_style, justify="center")
    else:
        status_text = Text("")

    # Error (only for non-validation errors like empty submit)
    if error and verified is None:
        error_text = Text(error, style="bright_red", justify="center")
    else:
        error_text = Text("")

    # Keyboard hint — makes editing/clearing discoverable. Without this, a saved
    # value pre-filled on a Settings → Configure re-run looks un-editable because
    # typing just appends to the (masked) existing value. Hidden while verifying,
    # after success, and during fade animations.
    if verifying or verified is True or input_fade:
        hint_text = Text("")
    else:
        hint_text = Text(
            "⌫ backspace  ·  Ctrl+U clear  ·  Enter to verify  ·  Esc back",
            style="dim",
            justify="center",
        )

    # Required-scope line for credential tokens (not shown for LLM API keys, which
    # don't have granular scopes). Suppressed during the fade animation so the
    # intro reads cleanly, matching the keyboard hint above.
    _help = TOKEN_HELP.get(provider["env_var"])
    show_scope = _help is not None and not input_fade

    body = [Align.center(instructions)]
    if show_scope:
        body.append(Align.center(_build_scope_text(_help["scope"])))
    body += [
        Text(""),
        Align.center(input_box),
        Align.center(status_text),
        Align.center(error_text),
        Align.center(hint_text),
    ]
    body_h = 9 + (1 if show_scope else 0)  # instructions[+scope] + blank + input_box(5) + status + error + hint

    return _build_screen_frame(
        subtitle="Enter your API key",
        step=0,
        body_items=body,
        body_height=body_h,
        width=width,
        height=height,
        title_text=provider["name"],
    )


# Model-card colours — kept in sync with the app's project cards / action
# buttons (rgb(70,100,180) accent, rgb(35,35,45) dim) so the setup wizard's
# model list reads as the same "card" component used elsewhere in the TUI.
_MODEL_CARD_SEL_BORDER = "rgb(70,100,180)"
_MODEL_CARD_DIM_BORDER = "rgb(35,35,45)"
_MODEL_CARD_DIM_TEXT = "rgb(140,140,140)"
_MODEL_CARD_H = 3  # rows per card: top border + label + bottom border


def _build_model_card(label: str, *, selected: bool, inner_w: int) -> list[Align]:
    """Build one rounded model card as three centred rows (top/label/bottom).

    Matches ``build_action_buttons`` / ``_build_project_card``: accent border +
    bold white label when selected, dim border + grey label otherwise.
    """
    border = _MODEL_CARD_SEL_BORDER if selected else _MODEL_CARD_DIM_BORDER
    text_style = "bold white" if selected else _MODEL_CARD_DIM_TEXT

    # Truncate over-long ids (e.g. bedrock inference profiles) so the box stays
    # aligned; middle-ellipsis keeps both the family and the version visible.
    if len(label) > inner_w:
        keep = inner_w - 1
        head = keep // 2
        label = label[:head] + "…" + label[len(label) - (keep - head) :]

    pad_l = (inner_w - len(label)) // 2
    pad_r = inner_w - len(label) - pad_l
    centered = " " * pad_l + label + " " * pad_r

    top = Text("╭" + "─" * inner_w + "╮", style=border)
    mid = Text()
    mid.append("│", style=border)
    mid.append(centered, style=text_style)
    mid.append("│", style=border)
    bot = Text("╰" + "─" * inner_w + "╯", style=border)
    return [Align.center(top), Align.center(mid), Align.center(bot)]


def _build_model_select_screen(
    provider: dict[str, Any],
    entries: list[str],
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float = 0.0,  # accepted for caller compatibility; cards are static
    error: str = "",
    status: str = "",
) -> Panel:
    """Build the model-selection screen — a stack of rounded, arrow-selectable cards.

    ``status`` is a neutral live-progress line (e.g. an in-flight Ollama model
    download) rendered in the accent colour where ``error`` would go.

    Each ``entries`` item (presets/live models + a trailing "Custom…") renders as
    a rounded card matching the app's project cards / action buttons. The list is
    windowed to the available height so it never overflows the (non-scrolling)
    frame — the window follows the selection and shows a "N more" hint when the
    list is longer than fits.
    """
    # Card inner width: fit the longest label, clamped to the screen.
    longest = max((len(e) for e in entries), default=8)
    max_inner = max(16, width - 16)
    inner_w = min(max(longest, 16), max_inner)

    # Vertical budget: the frame's header + footer inside its inner height.
    middle_h = max(_MODEL_CARD_H, (height - 4) - _FRAME_HEADER_H - _FRAME_FOOTER_H)
    reserve = 2 if (error or status) else 0
    avail = max(_MODEL_CARD_H, middle_h - reserve)
    max_cards = max(1, avail // _MODEL_CARD_H)

    truncated = len(entries) > max_cards
    if truncated:
        max_cards = max(1, (avail - 1) // _MODEL_CARD_H)  # reserve a row for the hint
        # Window follows the selection, keeping it roughly centred.
        start = min(max(0, selected - max_cards // 2), len(entries) - max_cards)
    else:
        start = 0
    window = range(start, min(start + max_cards, len(entries)))

    body: list = []
    for i in window:
        body.extend(_build_model_card(entries[i], selected=(i == selected), inner_w=inner_w))
    body_h = len(list(window)) * _MODEL_CARD_H

    if truncated:
        hidden = len(entries) - len(list(window))
        body.append(Align.center(Text(f"↑↓  {hidden} more", style="dim")))
        body_h += 1

    if status:
        body.append(Text(""))
        body.append(Align.center(Text(status, style=_ACCENT)))
        body_h += 2
    elif error:
        body.append(Text(""))
        body.append(Align.center(Text(f"✗ {error}", style="bright_red")))
        body_h += 2

    return _build_screen_frame(
        subtitle="Choose a model",
        step=0,
        body_items=body,
        body_height=body_h,
        width=width,
        height=height,
        title_text=provider["name"],
    )


def _build_model_loading_screen(
    provider: dict[str, Any],
    tick: float,
    *,
    width: int = 80,
    height: int = 24,
) -> Panel:
    """Build the "discovering models" screen shown while live discovery runs.

    ``tick`` is a monotonic elapsed-seconds value; a travelling accent highlight
    sweeps across the message so the frame keeps animating while the (threaded)
    ``fetch_available_models`` network call is in flight — no frozen screen.
    """
    msg = "Discovering available models…"
    text = Text(justify="center")
    for i, ch in enumerate(msg):
        text.append(ch, style=shimmer_style(_ACCENT, i, len(msg), tick))

    return _build_screen_frame(
        subtitle="Choose a model",
        step=0,
        body_items=[Align.center(text)],
        body_height=1,
        width=width,
        height=height,
        title_text=provider["name"],
    )


def _build_model_input_screen(
    provider: dict[str, Any],
    input_value: str,
    *,
    width: int = 80,
    height: int = 24,
    error: str = "",
    verified: bool | None = None,
    verifying: bool = False,
    border_override: str = "",
    status: str = "",
) -> Panel:
    """Build the custom-model text-input screen.

    ``status`` is a neutral live-progress line (e.g. an in-flight Ollama model
    download) rendered in the accent colour in place of the error/hint lines.

    A near-clone of _build_input_screen, but model ids are never secrets — the
    value is always shown in plaintext (no masking).
    """
    import rich.box

    # Provider identity is in the tall frame title; body starts at the instructions.
    instructions = Text("Enter any model id supported by your account", style="dim", justify="center")

    box_inner_w = min(70, width - 10) - 2 - 4
    display_val = input_value
    cursor = "█" if not verifying else ""
    full_text = display_val + cursor
    avail = box_inner_w - 4

    input_content = Text(justify="left", no_wrap=True, overflow="crop")
    if len(full_text) <= avail:
        input_content.append("  " + full_text, style="bold white")
    else:
        visible = full_text[-(avail - 1) :]
        input_content.append(" ◂", style="dim")
        input_content.append(visible, style="bold white")

    if border_override:
        border_color = border_override
    elif verified is True:
        border_color = "bright_green"
    elif verified is False or error:
        border_color = "bright_red"
    else:
        border_color = _ACCENT

    input_box = Panel(
        input_content,
        title=" LLM_MODEL ",
        title_align="left",
        border_style=border_color,
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=min(70, width - 10),
    )

    if status:
        status_text = Text(status, style=_ACCENT, justify="center")
    elif verifying or verified is True:
        status_text = Text("")
    elif verified is False or error:
        status_text = Text(f"✗ {error}", style="bright_red", justify="center")
    else:
        status_text = Text("")

    if status or verifying or verified is True:
        hint_text = Text("")
    else:
        hint_text = Text(
            "⌫ backspace  ·  Ctrl+U clear  ·  Enter to verify  ·  Esc back",
            style="dim",
            justify="center",
        )

    body = [
        Align.center(instructions),
        Text(""),
        Align.center(input_box),
        Align.center(status_text),
        Align.center(hint_text),
    ]
    body_h = 8  # instructions(1) + blank + input_box(5) + status + hint

    return _build_screen_frame(
        subtitle="Type a model id",
        step=0,
        body_items=body,
        body_height=body_h,
        width=width,
        height=height,
        title_text=provider["name"],
    )
