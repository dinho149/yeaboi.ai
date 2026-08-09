"""Shared UI constants and reusable components for the TUI screens.

# See docs: "Architecture" — shared UI component layer.
# Provides Theme dataclass, action buttons, scrollbar, progress dots,
# viewport helpers, and popup builder — used across all TUI screens
# for visual consistency. Think of these as React-like primitives.
"""

from __future__ import annotations

from dataclasses import dataclass

import rich.box
from rich.panel import Panel
from rich.text import Text

from yeaboi.beta import BETA_RGB
from yeaboi.ui.shared._animations import COLOR_RGB
from yeaboi.ui.shared._ascii_font import render_ascii_text

# Height of a page's title art. Titles now use the compact two-line font (the
# same one the main-menu rows and the select→page slide use), so headers are two
# rows tall — the viewport `header_h` values are sized to match.
TITLE_ROWS = 2

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

PAD = "    "


# ---------------------------------------------------------------------------
# Theme — centralised color palette for both modes
# ---------------------------------------------------------------------------

# Neutral dark page background for screens that don't belong to a tinted mode
# (home, splash, provider select, settings/changelog/feedback). Every page paints
# its own background so all users see the same TUI regardless of their
# terminal's color scheme.
NEUTRAL_BG = "rgb(16,16,20)"


@dataclass(frozen=True)
class Theme:
    """Color palette for TUI screens. Use ANALYSIS_THEME or PLANNING_THEME."""

    accent: str = "rgb(100,180,100)"
    accent_bright: str = "rgb(80,220,120)"
    muted: str = "rgb(120,120,140)"
    value: str = "bold white"
    good: str = "rgb(80,220,120)"
    warn: str = "rgb(220,180,60)"
    bad: str = "rgb(220,80,80)"
    dim: str = "dim"
    sep: str = "rgb(50,60,80)"
    id: str = "cyan"
    desc: str = "rgb(160,160,160)"
    # Page background: one neutral dark base shared by every screen, applied by
    # build_page_panel as "on {bg}" so the whole terminal shows a single
    # consistent colour rather than the user's terminal background. Modes keep
    # their distinct accent hues (titles, separators) but share this backdrop —
    # per-mode background tints were dropped for a uniform look across screens.
    bg: str = NEUTRAL_BG
    # Elevated-surface tint for in-page cards/bubbles (a dark shade of the mode
    # accent, channels kept ≤ 40 so foreground styles stay readable). Empty
    # string = the mode has no card surfaces; renderers must skip the tint.
    card_bg: str = ""


ANALYSIS_THEME = Theme()
PLANNING_THEME = Theme(accent="rgb(110,140,220)", accent_bright="rgb(140,170,255)", card_bg="rgb(20,24,38)")
USAGE_THEME = Theme(accent="rgb(220,160,60)", accent_bright="rgb(255,200,80)")
SETTINGS_THEME = Theme(accent="rgb(160,160,180)", accent_bright="rgb(200,200,220)")
STANDUP_THEME = Theme(accent="rgb(200,100,180)", accent_bright="rgb(255,150,220)")
RETRO_THEME = Theme(accent="rgb(80,190,190)", accent_bright="rgb(120,230,230)")
# Gold, not table-felt green — analysis owns green, and the two cards sat side
# by side looking like twins. Gold keeps the casino identity (chips) instead.
POKER_THEME = Theme(accent="rgb(230,200,70)", accent_bright="rgb(255,235,110)")
PERFORMANCE_THEME = Theme(accent="rgb(220,110,90)", accent_bright="rgb(255,150,120)")
REPORTING_THEME = Theme(accent="rgb(140,120,230)", accent_bright="rgb(180,160,255)")
# Silver chrome on purpose — the changelog page's per-feature area tags carry the
# colour (each tag uses its mode's accent), so the page frame stays neutral.
CHANGELOG_THEME = Theme(accent="rgb(160,160,180)", accent_bright="rgb(200,200,220)")
# Same rationale as CHANGELOG_THEME: the feedback form's area chip carries the
# selected mode's colour, so the page frame stays neutral silver.
FEEDBACK_THEME = Theme(accent="rgb(160,160,180)", accent_bright="rgb(200,200,220)")
# The two sides of the landing split. HUMANS_THEME is the Theme default palette
# named, so the existing modes are unchanged; AGENTS_THEME opens the family's
# "machine" palette, distinct per mode like the human modes: steel blue for the
# category card, cyan/mint/rose per mode below it. The landing split reads its
# card accents from these two — a card that hardcoded the same rgb() triples
# would drift the moment either palette moved.
HUMANS_THEME = Theme()
AGENTS_THEME = Theme(accent="rgb(90,160,210)", accent_bright="rgb(130,200,255)")
AGENT_USAGE_THEME = Theme(accent="rgb(70,190,230)", accent_bright="rgb(110,225,255)")
AGENT_STANDUP_THEME = Theme(accent="rgb(120,210,170)", accent_bright="rgb(160,245,205)")
AGENT_SECURITY_THEME = Theme(accent="rgb(230,90,120)", accent_bright="rgb(255,130,160)")

# Button color scheme: (accent_border, accent_label, grey_border, grey_label)
_BTN_COLORS: dict[str, tuple[str, str, str, str]] = {
    "Accept": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    "Done": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    "Continue": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    "Run Analysis": ("rgb(60,160,80)", "rgb(80,220,120)", "rgb(40,50,40)", "rgb(50,60,50)"),
    # Board setup's go button — green like the other "proceed" verbs.
    "Connect": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    "Edit": ("rgb(100,100,120)", "rgb(140,140,160)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Regenerate": ("rgb(100,100,120)", "rgb(140,140,160)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Export": ("rgb(70,100,180)", "rgb(100,140,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    # Export destination picker (shared across every mode's Export button).
    "Files": ("rgb(70,100,180)", "rgb(100,140,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Notion": ("rgb(70,100,180)", "rgb(100,140,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Confluence": ("rgb(70,100,180)", "rgb(100,140,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    # Settings page: data-directory (YEABOI_HOME) editor.
    "Data Dir": ("rgb(160,160,180)", "rgb(200,200,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    # Settings page: sandbox allowed-paths (YEABOI_ALLOWED_PATHS) editor.
    "Paths": ("rgb(160,160,180)", "rgb(200,200,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    # Filesystem-sandbox consent popup — Allow once (session-only, green like
    # Accept), Always allow (persists to .env, amber to signal permanence),
    # Deny (safe default, grey like Leave).
    "Allow once": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    "Always allow": ("rgb(180,140,60)", "rgb(220,180,90)", "rgb(50,46,36)", "rgb(60,56,46)"),
    "Deny": ("rgb(100,100,120)", "rgb(140,140,160)", "rgb(40,40,50)", "rgb(50,50,60)"),
    # Blocked-destination warning popup in the export picker.
    "Open Setup": ("rgb(160,160,180)", "rgb(200,200,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    # Data-dir change: move the existing tree or leave it in place.
    "Move": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    "Leave": ("rgb(100,100,120)", "rgb(140,140,160)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Jira": ("rgb(70,100,180)", "rgb(100,140,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Azure DevOps": ("rgb(70,100,180)", "rgb(100,140,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Configure": ("rgb(160,160,180)", "rgb(200,200,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    # Standup identity flow (repo path + aliases; schedule setup lives on the hub).
    "Identity": ("rgb(160,160,180)", "rgb(200,200,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    # Settings page: cycles LOG_LEVEL (DEBUG → INFO → WARNING → ERROR).
    "Log Level": ("rgb(160,160,180)", "rgb(200,200,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    # Analysis-mode ticket-generation confirmation screen.
    "Generate tickets": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    "Not now": ("rgb(100,100,120)", "rgb(140,140,160)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Generate": ("rgb(180,80,160)", "rgb(220,120,200)", "rgb(50,40,50)", "rgb(60,50,60)"),
    "Team": ("rgb(180,80,160)", "rgb(220,120,200)", "rgb(50,40,50)", "rgb(60,50,60)"),
    # Standup Generate gate: reuse the saved setup (green "go") or re-pick it (grey).
    "Use saved": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    "Change": ("rgb(100,100,120)", "rgb(140,140,160)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Generate Action Items": ("rgb(50,170,170)", "rgb(90,220,220)", "rgb(40,52,52)", "rgb(50,62,62)"),
    "Close": ("rgb(100,100,120)", "rgb(140,140,160)", "rgb(40,40,50)", "rgb(50,50,60)"),
    # Live boards: shown only when the board's secure link failed to come up.
    "Retry Link": ("rgb(50,170,170)", "rgb(90,220,220)", "rgb(40,52,52)", "rgb(50,62,62)"),
    "Share Online": ("rgb(70,100,180)", "rgb(100,140,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Copy Invite": ("rgb(70,100,180)", "rgb(100,140,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Stop Sharing": ("rgb(180,140,60)", "rgb(220,180,90)", "rgb(50,46,36)", "rgb(60,56,46)"),
    # Amber rather than red: it throws away corrections in this document, but
    # the edit log keeps them — destructive enough to warn, not to alarm.
    "Discard Edits": ("rgb(180,140,60)", "rgb(220,180,90)", "rgb(50,46,36)", "rgb(60,56,46)"),
    # Quit-time popup: stop the local Ollama server before exiting.
    "Stop": ("rgb(180,140,60)", "rgb(220,180,90)", "rgb(50,46,36)", "rgb(60,56,46)"),
    # Performance mode actions (coral accent).
    "1:1 Prep": ("rgb(200,90,70)", "rgb(240,130,110)", "rgb(52,42,40)", "rgb(62,52,50)"),
    "1:1 Complete": ("rgb(200,90,70)", "rgb(240,130,110)", "rgb(52,42,40)", "rgb(62,52,50)"),
    "6mo Review": ("rgb(200,90,70)", "rgb(240,130,110)", "rgb(52,42,40)", "rgb(62,52,50)"),
    "Notes": ("rgb(160,160,180)", "rgb(200,200,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    # Reporting mode actions (indigo accent). PowerPoint sits in the Export blue family.
    "Generate Report": ("rgb(120,100,220)", "rgb(170,150,255)", "rgb(44,40,58)", "rgb(54,50,68)"),
    "Period": ("rgb(120,100,220)", "rgb(170,150,255)", "rgb(44,40,58)", "rgb(54,50,68)"),
    "Theme": ("rgb(120,100,220)", "rgb(170,150,255)", "rgb(44,40,58)", "rgb(54,50,68)"),
    "Style": ("rgb(120,100,220)", "rgb(170,150,255)", "rgb(44,40,58)", "rgb(54,50,68)"),
    # Style screen: Save persists (green, like Done), Reset restores the deck-style
    # defaults (amber signals "destructive-ish").
    "Save": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    "Reset": ("rgb(180,140,60)", "rgb(220,180,90)", "rgb(50,46,36)", "rgb(60,56,46)"),
    "PowerPoint": ("rgb(70,100,180)", "rgb(100,140,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Back": ("rgb(100,100,120)", "rgb(140,140,160)", "rgb(40,40,50)", "rgb(50,50,60)"),
    # Advisory action on the analysis review when a Small project looks bigger.
    "Switch to Large": ("rgb(180,140,60)", "rgb(220,180,90)", "rgb(50,46,36)", "rgb(60,56,46)"),
    # Feedback form (silver chrome; Submit green like Accept, Open Browser blue like Export).
    "Submit": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    "AI Polish": ("rgb(160,160,180)", "rgb(200,200,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Keep Original": ("rgb(100,100,120)", "rgb(140,140,160)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Open Browser": ("rgb(70,100,180)", "rgb(100,140,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    # Roadmap intake card actions (Planning blue family; Plan This is the "go" green).
    "Plan This": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    "Re-analyze": ("rgb(70,100,180)", "rgb(100,140,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Change Source": ("rgb(100,100,120)", "rgb(140,140,160)", "rgb(40,40,50)", "rgb(50,50,60)"),
    "Select": ("rgb(70,100,180)", "rgb(100,140,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
    # Anonymize action (available on every mode's result screen) — a slate/violet
    # "shield" tone, distinct from the blue Export family; Copy sits beside it.
    "Anonymize": ("rgb(120,110,170)", "rgb(165,150,220)", "rgb(44,42,54)", "rgb(54,52,64)"),
    "Copy": ("rgb(120,110,170)", "rgb(165,150,220)", "rgb(44,42,54)", "rgb(54,52,64)"),
    "Adjust": ("rgb(100,100,120)", "rgb(140,140,160)", "rgb(40,40,50)", "rgb(50,50,60)"),
    # Poker mode actions (green accent).
    "Start Session": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    "New Session": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    # Saved-runs hub actions (standup / retro / reporting / performance history).
    "Delete": ("rgb(220,60,60)", "rgb(240,90,90)", "rgb(52,38,38)", "rgb(62,48,48)"),
    "Run again": ("rgb(60,160,80)", "rgb(80,200,100)", "rgb(40,50,40)", "rgb(50,60,50)"),
    "History": ("rgb(160,160,180)", "rgb(200,200,220)", "rgb(40,40,50)", "rgb(50,50,60)"),
}
_BTN_DEFAULT = ("rgb(100,100,120)", "rgb(140,140,160)", "rgb(40,40,50)", "rgb(50,50,60)")
_BTN_MIN_W = 12
_BTN_GAP = 2

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def build_page_panel(
    content,
    *,
    theme: Theme | None = None,
    bg: str | None = None,
    border_style: str = "white",
    height: int,
    padding: tuple[int, int] = (1, 2),
    **panel_kwargs,
) -> Panel:
    """Build the full-screen root Panel every TUI page must return.

    Rich cascades a Panel's ``style`` onto every child segment that has no
    explicit background of its own, so setting ``style="on {bg}"`` here tints
    content rows, heading spacers, scroll filler lines and padding alike — the
    whole terminal shows the mode's background colour with no seams, instead of
    the user's terminal default.

    ``bg`` overrides ``theme.bg``; with neither, the neutral dark base is used.
    Never return a raw full-screen ``Panel`` from a screen builder — a unit test
    (tests/unit/test_screen_backgrounds.py) enforces this.
    """
    color = bg or (theme.bg if theme is not None else NEUTRAL_BG)
    return Panel(
        content,
        style=f"on {color}",
        border_style=border_style,
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=padding,
        **panel_kwargs,
    )


def center_label(label: str, width: int) -> str:
    """Center a label string within the given width, padding with spaces.

    Previously duplicated in _project_cards.py and _screens.py.
    """
    pad_l = (width - len(label)) // 2
    pad_r = width - len(label) - pad_l
    return " " * pad_l + label + " " * pad_r


def _title_rows(word: str, available_width: int) -> list[str]:
    """Return equal-width rows for *word*'s header art in the compact two-line font.

    This is the SAME font the main-menu mode rows and the select→page slide use
    (:func:`render_ascii_text`), so the title the menu slides up to the top reads
    as one continuous element when the page takes over — rather than jumping to a
    header twice its size. ``available_width`` is accepted for call-site
    compatibility but no longer switches fonts (the tall ANSI-Shadow wordmark is
    retired here).
    """
    lines = render_ascii_text(word)
    block_w = max((len(line) for line in lines), default=0)
    return [line.ljust(block_w) for line in lines]


def build_ascii_title(word: str, color: str, *, shimmer_tick: float | None = None, width: int | None = None) -> Text:
    """Return a compact two-line ASCII-art title for ``word`` in ``color``.

    ``TITLE_ROWS`` rows tall (see ``_title_rows``) — the same compact font the
    main-menu rows and the select→page slide use, so the slid menu title reads as
    the page title rather than jumping to a header twice its size. When
    ``shimmer_tick`` is None the title is a solid bold colour (the static look);
    when a float is passed, a travelling white highlight sweeps across the glyphs,
    so a page's header can animate by feeding it a monotonic clock each frame.

    ``color`` is an ``"rgb(r,g,b)"`` key present in COLOR_RGB (the shimmer needs
    it registered). ``width`` is accepted for call-site compatibility (the font no
    longer varies with width); defaults to a standard 80-col terminal's inner width.
    This is the single implementation the per-page ``*_title()`` helpers delegate
    to — keeping every header visually identical.
    """
    lines = _title_rows(word, (width - 6) if width else 74)
    total = max(len(line) for line in lines)
    title = Text(justify="left")

    if shimmer_tick is None:
        base_r, base_g, base_b = COLOR_RGB.get(color, (180, 180, 180))
        style = f"bold rgb({base_r},{base_g},{base_b})"
        for idx, line in enumerate(lines):
            title.append(PAD + line, style=style)
            if idx < len(lines) - 1:
                title.append("\n")
        return title

    from yeaboi.ui.shared._animations import shimmer_style

    for idx, line in enumerate(lines):
        title.append(PAD)
        for i, ch in enumerate(line):
            title.append(ch, style=shimmer_style(color, i, total, shimmer_tick))
        if idx < len(lines) - 1:
            title.append("\n")
    return title


def build_reveal_subtitle(
    text: str, reveal: float | None = None, *, style: str = "dim", pad: str = PAD, justify: str = "left"
) -> Text:
    """Return a subtitle line, optionally revealed typewriter-style.

    ``reveal`` None (default) shows the whole string — byte-identical to the
    previous ``Text(PAD + text, style="dim")`` every page used. A float reveals
    only the first ``int(reveal)`` characters, so a page can type its subtitle in
    by feeding an increasing value each frame (paired with an animated title).
    """
    shown = text if reveal is None else text[: max(0, int(reveal))]
    return Text(pad + shown, style=style, justify=justify)


def planning_title(shimmer_tick: float | None = None, *, width: int | None = None) -> Text:
    """Return the Planning ASCII title (brand blue). Optionally shimmering.

    # See docs: "Architecture" — the "Planning" header is pinned at the
    # top of every screen in the planning flow.

    Pass ``width`` (the panel width) so wide wordmarks can use the tall ANSI
    Shadow art where they fit and gracefully fall back on narrow terminals.
    """
    return build_ascii_title("Planning", "rgb(110,140,220)", shimmer_tick=shimmer_tick, width=width)


def analysis_title(shimmer_tick: float | None = None, *, width: int | None = None) -> Text:
    """Return the Analysis ASCII title (green accent). Optionally shimmering."""
    return build_ascii_title("Analysis", "rgb(100,180,100)", shimmer_tick=shimmer_tick, width=width)


def usage_title(shimmer_tick: float | None = None, *, width: int | None = None) -> Text:
    """Return the Usage ASCII title (amber accent). Optionally shimmering."""
    return build_ascii_title("Usage", "rgb(220,160,60)", shimmer_tick=shimmer_tick, width=width)


def settings_title(shimmer_tick: float | None = None, *, width: int | None = None) -> Text:
    """Return the Settings ASCII title (silver accent). Optionally shimmering."""
    return build_ascii_title("Settings", "rgb(160,160,180)", shimmer_tick=shimmer_tick, width=width)


def standup_title(shimmer_tick: float | None = None, *, width: int | None = None) -> Text:
    """Return the Daily Standup ASCII title (magenta accent). Optionally shimmering."""
    return build_ascii_title("Standup", "rgb(200,100,180)", shimmer_tick=shimmer_tick, width=width)


def retro_title(shimmer_tick: float | None = None, *, width: int | None = None) -> Text:
    """Return the Retro ASCII title (teal accent). Optionally shimmering."""
    return build_ascii_title("Retro", "rgb(80,190,190)", shimmer_tick=shimmer_tick, width=width)


def poker_title(shimmer_tick: float | None = None, *, width: int | None = None) -> Text:
    """Return the Poker ASCII title (gold accent). Optionally shimmering."""
    return build_ascii_title("Poker", "rgb(230,200,70)", shimmer_tick=shimmer_tick, width=width)


def performance_title(shimmer_tick: float | None = None, *, width: int | None = None) -> Text:
    """Return the Performance ASCII title (coral accent). Optionally shimmering."""
    return build_ascii_title("Performance", "rgb(220,110,90)", shimmer_tick=shimmer_tick, width=width)


def reporting_title(shimmer_tick: float | None = None, *, width: int | None = None) -> Text:
    """Return the Reporting ASCII title (indigo accent). Optionally shimmering."""
    return build_ascii_title("Reporting", "rgb(140,120,230)", shimmer_tick=shimmer_tick, width=width)


def changelog_title(shimmer_tick: float | None = None, *, width: int | None = None) -> Text:
    """Return the Changelog ASCII title (silver accent). Optionally shimmering."""
    return build_ascii_title("Changelog", "rgb(160,160,180)", shimmer_tick=shimmer_tick, width=width)


def feedback_title(shimmer_tick: float | None = None, *, width: int | None = None) -> Text:
    """Return the Feedback ASCII title (silver accent). Optionally shimmering."""
    return build_ascii_title("Feedback", "rgb(160,160,180)", shimmer_tick=shimmer_tick, width=width)


def agent_usage_title(shimmer_tick: float | None = None, *, width: int | None = None) -> Text:
    """Return the Agent Usage ASCII title (cyan accent). Optionally shimmering."""
    return build_ascii_title("Usage", "rgb(70,190,230)", shimmer_tick=shimmer_tick, width=width)


def agent_standup_title(shimmer_tick: float | None = None, *, width: int | None = None) -> Text:
    """Return the Agent Standup ASCII title (mint accent). Optionally shimmering."""
    return build_ascii_title("Standup", "rgb(120,210,170)", shimmer_tick=shimmer_tick, width=width)


def agent_security_title(shimmer_tick: float | None = None, *, width: int | None = None) -> Text:
    """Return the Agent Security ASCII title (rose accent). Optionally shimmering."""
    return build_ascii_title("Security", "rgb(230,90,120)", shimmer_tick=shimmer_tick, width=width)


def tips_title(shimmer_tick: float | None = None, *, width: int | None = None) -> Text:
    """Return the Tips ASCII title (silver accent). Optionally shimmering."""
    return build_ascii_title("Tips", "rgb(160,160,180)", shimmer_tick=shimmer_tick, width=width)


def build_badge(label: str, *, rgb: tuple[int, int, int] = BETA_RGB, dim: bool = False) -> Text:
    """Build a small inverse-video status chip (BETA, COMING SOON).

    Rendered as ``" LABEL "`` in bold black on the given colour — a solid block
    reads as a status marker at a glance, where coloured text alone reads as
    emphasis. Deliberately plain text plus a background: the mode-card click
    hit-testing in ``mode_select`` locates rows by scanning for block-font glyphs
    (``█▀▄``), so a chip drawn with box characters would be mistaken for a title.

    Takes an rgb tuple rather than a Rich colour string because the ``dim``
    variant has to do arithmetic on the channels, and the ``COLOR_RGB`` lookup
    only knows the mode accents — a chip colour absent from it would silently
    fall through to a default grey.

    Args:
        label: Chip text. Kept short — it sits beside a wordmark.
        rgb: Chip background channels. Defaults to the beta amber.
        dim: Halve the channels for unselected rows, matching the mode-row
            treatment so a chip never out-shouts the title it annotates.
    """
    r, g, b = rgb
    if dim:
        r, g, b = max(40, r // 2), max(40, g // 2), max(40, b // 2)
    return Text(f" {label} ", style=f"bold black on rgb({r},{g},{b})")


def build_popup(
    message: str,
    *,
    width: int = 50,
    border_style: str = "rgb(220,60,60)",
) -> Panel:
    """Build a popup rectangle for confirmation dialogs.

    Returns a rounded Panel that slides up from the bottom of the screen,
    matching the slide animation pattern used by _build_slide_frame.
    The popup is 5 rows tall (border + padding + message + padding + border)
    for a balanced visual appearance.

    Args:
        message: The text to display inside the popup.
        width: Total width of the popup panel.
        border_style: Rich style string for the panel border.
    """
    content = Text(message, style="bold white", justify="center")
    return Panel(
        content,
        border_style=border_style,
        box=rich.box.ROUNDED,
        width=width,
        padding=(1, 2),
    )


# ---------------------------------------------------------------------------
# Reusable UI primitives
# ---------------------------------------------------------------------------


def _button_width(label: str) -> int:
    """Total columns one button occupies, borders included."""
    return max(_BTN_MIN_W - 2, len(label) + 2) + 2


def _wrap_actions(actions: list[str], width: int | None, pad: str) -> list[list[int]]:
    """Group action indices into rows that fit *width*.

    ``width`` is the panel's inner width; ``None`` means "one row, however long",
    which is what :func:`build_action_buttons` passes and what every screen did
    before rows existed.

    A row always takes at least one button, even one wider than the terminal —
    the alternative is an empty row and a button that can be selected but is
    never drawn.
    """
    if width is None:
        return [list(range(len(actions)))] if actions else []

    budget = max(_BTN_MIN_W, width - len(pad))
    rows: list[list[int]] = []
    row: list[int] = []
    used = 0
    for i, label in enumerate(actions):
        size = _button_width(label)
        extra = size + (_BTN_GAP if row else 0)
        if row and used + extra > budget:
            rows.append(row)
            row, used = [i], size
        else:
            row.append(i)
            used += extra
    if row:
        rows.append(row)
    return rows


def action_rows_height(actions: list[str], width: int | None = None, *, pad: str = PAD) -> int:
    """Rows of terminal the action area will occupy, for :func:`calc_viewport`.

    Screens pass this instead of a hardcoded ``action_h`` so a bar that wraps
    takes its extra height out of the scroll viewport rather than off the bottom
    of the panel.

    Four lines per row: three of button (top/label/bottom) plus one blank. For
    the first row that blank is the separator above the bar, and for each row
    after it the one between that row and the previous — so the arithmetic is
    the same either way, and a single row comes to the 4 that every screen still
    hardcodes.
    """
    return 4 * max(1, len(_wrap_actions(actions, width, pad)))


def build_action_rows(
    actions: list[str],
    selected: int,
    *,
    width: int | None = None,
    pad: str = PAD,
) -> list[Text]:
    """Build the action bar as a flat list of Text lines, wrapping to fit *width*.

    Each button is a rounded box-drawing rectangle; the *selected* one takes its
    accent colour and the rest are greyed out.

    Wrapping exists because the bar had quietly outgrown an 80-column terminal.
    The retro board's five buttons come to 92 columns, so the last of them was
    drawn past the edge of the panel — reachable with the arrow keys and
    invisible to the person pressing them. Nothing caught it, because a Rich
    ``Text`` is perfectly happy to be wider than the console; it just gets
    clipped.

    Callers that pass no ``width`` keep the old single-row behaviour exactly.
    """
    lines: list[Text] = []

    for row_no, row in enumerate(_wrap_actions(actions, width, pad)):
        if row_no:
            # Buttons are three lines with no internal gap, so two stacked rows
            # would have their borders touch and read as one grid.
            lines.append(Text(""))

        btn_top = Text(pad, justify="left")
        btn_mid = Text(pad, justify="left")
        btn_bot = Text(pad, justify="left")

        for slot, i in enumerate(row):
            label = actions[i]
            if slot > 0:
                btn_top.append(" " * _BTN_GAP)
                btn_mid.append(" " * _BTN_GAP)
                btn_bot.append(" " * _BTN_GAP)

            inner_w = max(_BTN_MIN_W - 2, len(label) + 2)
            pad_l = (inner_w - len(label)) // 2
            pad_r = inner_w - len(label) - pad_l
            centered = " " * pad_l + label + " " * pad_r

            accent_b, accent_l, grey_b, grey_l = _BTN_COLORS.get(label, _BTN_DEFAULT)
            if i == selected:
                b_style, l_style = accent_b, f"bold {accent_l}"
            else:
                b_style, l_style = grey_b, grey_l

            btn_top.append("\u256d" + "\u2500" * inner_w + "\u256e", style=b_style)
            btn_mid.append("\u2502" + centered + "\u2502", style=l_style)
            btn_bot.append("\u2570" + "\u2500" * inner_w + "\u256f", style=b_style)

        lines.extend((btn_top, btn_mid, btn_bot))

    return lines


def build_action_buttons(
    actions: list[str],
    selected: int,
    *,
    pad: str = PAD,
) -> tuple[Text, Text, Text]:
    """Build the 3 Text lines (top/mid/bot) for a single row of action buttons.

    The original shape, kept because some forty screens unpack exactly three
    values from it. It is now :func:`build_action_rows` with no width, so the
    output is identical to what it has always been; a screen that wants wrapping
    calls that instead and pairs it with :func:`action_rows_height`.

    Returns (btn_top, btn_mid, btn_bot) — three Text objects to append to a Group.
    """
    rows = build_action_rows(actions, selected, width=None, pad=pad)
    if not rows:  # no actions at all: three empty lines, as before
        return Text(pad, justify="left"), Text(pad, justify="left"), Text(pad, justify="left")
    return rows[0], rows[1], rows[2]


def build_scrollbar(
    viewport_h: int, total_lines: int, scroll_offset: int, max_scroll: int, *, always_show: bool = False
) -> Text | None:
    """Build a scrollbar Text column, or None if content fits.

    Returns a Text object with viewport_h rows of thin/thick vertical bars,
    or None if total_lines <= viewport_h (no scrollbar needed).
    When always_show=True, renders a dim track even when content fits.
    """
    if total_lines <= viewport_h and not always_show:
        return None
    if total_lines <= viewport_h:
        # Show dim track only (no thumb needed)
        sb = Text(justify="left")
        for _ in range(viewport_h):
            sb.append("\u2502\n", style="rgb(50,50,60)")
        # A trailing newline would render as an extra row and push the last
        # content row (the buttons' bottom border) off the fixed-height panel.
        sb.rstrip()
        return sb

    thumb_size = max(1, round(viewport_h * viewport_h / max(total_lines, 1)))
    thumb_pos = round(scroll_offset / max(max_scroll, 1) * (viewport_h - thumb_size)) if max_scroll > 0 else 0

    sb = Text(justify="left")
    for i in range(viewport_h):
        is_thumb = thumb_pos <= i < thumb_pos + thumb_size
        if is_thumb:
            sb.append("\u2503\n", style="rgb(100,100,120)")
        else:
            sb.append("\u2502\n", style="rgb(50,50,60)")
    sb.rstrip()  # same: keep the Text exactly viewport_h rows tall
    return sb


def build_progress_dots(
    stages: list[str],
    current: int,
    *,
    pad: str = PAD,
    theme: Theme | None = None,
    mark_done: bool = False,
) -> Text:
    """Build a progress indicator: ● Instructions  ● Epic  ○ Stories ...

    Filled dots for completed stages, bright dot for current, hollow for future.

    ``mark_done`` also carries "done" in the LABEL — a finished stage keeps its
    name uppercased and in the accent colour, so a glance down the row says what
    is behind you without decoding dot fills. Off by default: the flows that
    predate it read as a plain stage list.
    """
    _theme = theme or ANALYSIS_THEME
    progress = Text(pad, justify="left")
    for i, stage_name in enumerate(stages):
        if i > 0:
            progress.append("  ", style="dim")
        if i < current:
            progress.append("\u25cf", style=_theme.accent)
        elif i == current:
            progress.append("\u25cf", style=_theme.accent_bright)
        else:
            progress.append("\u25cb", style="rgb(60,60,70)")
        if i == current:
            label, style = (stage_name.upper() if mark_done else stage_name), "bold white"
        elif i < current and mark_done:
            label, style = stage_name.upper(), _theme.accent
        else:
            label, style = stage_name, "dim"
        progress.append(f" {label}", style=style)
    return progress


def build_key_hints(pairs: list[tuple[str, str]], *, pad: str = "") -> Text:
    """Build a keycap hint row: key in bright grey, label in dim grey.

    The welcome screen's hint idiom (`[ prev  ] next  g open`) promoted to a
    shared primitive so chat and future pages render keyboard guidance the
    same way. Keys read as caps because they are the only bright tokens on an
    otherwise quiet row — no boxes, no inverse video.
    """
    row = Text(pad, justify="left")
    for i, (key_label, what) in enumerate(pairs):
        if i:
            row.append("   ")
        row.append(key_label, style="bold rgb(210,210,220)")
        row.append(f" {what}", style="rgb(110,110,125)")
    return row


def build_meter(
    filled: int,
    total: int,
    *,
    width: int = 10,
    theme: Theme | None = None,
    style: str = "",
) -> Text:
    """Build a compact horizontal meter: ▰▰▰▰▰▰▰▰▱▱ (filled/total scaled to width).

    Used for at-a-glance ratios (sprint day, confidence %) where a bar reads
    faster than digits. ``style`` overrides the filled colour (e.g. a
    confidence meter coloured good/warn/bad); the empty track matches the
    hollow-dot colour of build_progress_dots.
    """
    _theme = theme or ANALYSIS_THEME
    total = max(1, total)
    n = round(max(0, min(filled, total)) / total * width)
    meter = Text(justify="left")
    meter.append("▰" * n, style=style or _theme.accent)
    meter.append("▱" * (width - n), style="rgb(60,60,70)")
    return meter


# Where each named strip was last left. Module state for the same reason the
# section rule's position is: a strip is drawn from scratch every frame and has
# nowhere else to keep it. Keyed by strip so two strips on one page cannot
# remember for each other.
_TAB_MEMORY: dict[str, str] = {}


def remember_tab(strip: str, current) -> None:
    """Note where ``strip`` was left, so it opens there next time.

    Called wherever the live tab is known — leaving a page is not a reliable
    moment to do it, because a page can be left in more ways than it has exits.
    """
    if current:
        _TAB_MEMORY[strip] = current


def remembered_tab(strip: str, tabs, fallback):
    """Where ``strip`` was left, or ``fallback`` if that tab is not in it now.

    A remembered tab can name something this run does not have — a section
    whose scan did not happen, an artifact not generated yet — and reopening on
    it would be a lookup for a tab that is not drawn.
    """
    was = _TAB_MEMORY.get(strip)
    return was if was in tuple(tabs or ()) else fallback


def forget_tabs() -> None:
    """Drop every strip's remembered position (tests, and a fresh session)."""
    _TAB_MEMORY.clear()


def step_tab(tabs, current, key: str):
    """The tab ←/→ lands on, coming round at the ends.

    A strip of tabs is a ring, not a slider: holding one arrow reaches every
    tab rather than stopping at an end and going quiet. Shared by every strip
    in the app, because "does this one wrap?" is not a question a strip should
    be able to answer differently from the one beside it.

    Returns ``current`` untouched for any other key, for an empty strip, and
    for a ``current`` the strip does not contain — the caller's own fallback
    then decides, rather than this silently landing on the first tab.
    """
    if not tabs or key not in ("left", "right") or current not in tabs:
        return current
    at = list(tabs).index(current)
    return tabs[(at + (1 if key == "right" else -1)) % len(tabs)]


def calc_viewport(height: int, *, header_h: int = 7, action_h: int = 4) -> int:
    """Calculate viewport height from terminal height.

    Accounts for panel border (2) + padding (2) = 4 rows overhead,
    then subtracts header and action areas. Returns at least 3 rows
    even on very small terminals to prevent render crashes.
    """
    inner_h = max(0, height - 4)
    return max(3, inner_h - header_h - action_h)


# Minimum terminal size for the TUI to function
MIN_TERMINAL_HEIGHT = 10
MIN_TERMINAL_WIDTH = 40
