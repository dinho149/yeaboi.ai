"""Project card rendering and viewport scrolling for the project list screen.

# See docs: "Architecture" — this module builds the project list UI:
# project cards with metadata, action buttons (Delete/Export), viewport
# scrolling with peek stubs, and the "+ New Project" button.
"""

from __future__ import annotations

from dataclasses import dataclass

import rich.box
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.shared._animations import BLACK_RGB, lerp_color
from yeaboi.ui.shared._components import PAD, center_label

# ---------------------------------------------------------------------------
# Project data
# ---------------------------------------------------------------------------


@dataclass
class ProjectSummary:
    """Lightweight project metadata for the project list screen."""

    name: str
    id: str = ""  # UUID from persistence
    created: str = ""  # human-readable date, e.g. "2 days ago"
    status: str = ""  # e.g. "In Progress", "Complete"
    feature_count: int = 0
    story_count: int = 0
    task_count: int = 0
    sprint_count: int = 0
    jira_summary: str = ""  # e.g. "3/4 epics synced"
    progress: str = ""  # e.g. "3/7 stages complete"
    kind: str = "project"  # "project" | "roadmap" — roadmap rows open the roadmap results view
    roadmap_id: int = 0  # RoadmapStore row id when kind == "roadmap"
    updated_at: str = ""  # ISO UTC timestamp — merged-list sort key


@dataclass
class RunSummary:
    """One saved run in a mode's saved-runs hub (standup / retro / reporting / performance).

    A run is a stored report snapshot — not a resumable graph session — so it carries
    only what the hub list needs: the store row ``run_id`` (+ ``kind`` for performance's
    mixed artifact table), a ``title``/``subtitle`` for the card, and ``run_at`` for the
    relative-time line. ``to_project`` maps it onto a ProjectSummary so the existing
    ``_build_project_card`` renders it identically to a planning/analysis card.
    """

    mode: str  # "standup" | "retro" | "reporting" | "performance"
    run_id: int
    title: str
    subtitle: str = ""
    run_at: str = ""  # relative time, e.g. "2 days ago"
    kind: str = ""  # performance only: "prep" | "completion" | "review" | "note"
    session_id: str = ""

    def to_project(self) -> ProjectSummary:
        """Adapt this run to a ProjectSummary for the shared card renderer."""
        return ProjectSummary(
            name=self.title,
            created=self.run_at,
            progress=self.subtitle,
            kind="run",  # suppresses the [roadmap] tag; no count metadata
        )


@dataclass
class ProfileSummary:
    """Lightweight team profile metadata for the project list screen."""

    team_id: str
    source: str  # "jira" or "azdevops"
    project_key: str
    sample_sprints: int = 0
    velocity_avg: float = 0.0
    sample_stories: int = 0
    updated: str = ""  # relative time "2 days ago"
    staleness_days: int = 0
    preview_complete: bool = False  # True when preview flow finished


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PAD = PAD  # alias for backward compatibility within this module

# Viewport scrolling constants for the project list.
# See docs: "Architecture" — when more cards exist than fit on screen,
# the list scrolls to keep the selected card visible. Cards just outside
# the viewport show a 2-row "peek" stub (top half above, bottom half below).
_CARD_H = 5  # standard card height (border + up to 3 content + border)
_CARD_SPACING = 1  # blank line between cards
_PEEK_H = 2  # peek stub: border line + title line at viewport edge

# Button width for action buttons placed beside project cards.
_BTN_W = 10

_BTN_GREY = (60, 60, 70)  # default grey for unfocused buttons


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _center_label(label: str, width: int) -> str:
    """Center a label string within the given width, padding with spaces."""
    return center_label(label, width)


def _build_button_row(
    labels: tuple[str, str],
    *,
    selected: bool,
    opacity: float = 1.0,
    btn_w: int = 14,
    btn_gap: int = 2,
) -> tuple[Text, Text, Text]:
    """Build three Text lines (top/mid/bot) for a pair of equal-width rounded buttons.

    # See docs: "Architecture" — button rendering for project card actions.
    # Each button is drawn with Unicode box-drawing characters to
    # create a rounded-corner box appearance in the terminal.

    Returns (btn_top, btn_mid, btn_bot) Text objects.
    labels: (left_label, right_label) — e.g. ("Delete", "Export").
    """
    btn_inner = btn_w - 2  # content width inside │...│

    if selected:
        del_border = lerp_color(opacity, BLACK_RGB, (180, 60, 60))
        del_label = f"bold {lerp_color(opacity, BLACK_RGB, (220, 80, 80))}"
        exp_border = lerp_color(opacity, BLACK_RGB, (70, 100, 180))
        exp_label = f"bold {lerp_color(opacity, BLACK_RGB, (100, 140, 220))}"
    else:
        del_border = lerp_color(opacity, BLACK_RGB, (50, 40, 40))
        del_label = lerp_color(opacity, BLACK_RGB, (80, 60, 60))
        exp_border = lerp_color(opacity, BLACK_RGB, (40, 40, 50))
        exp_label = lerp_color(opacity, BLACK_RGB, (60, 60, 80))

    # Top border
    top = Text(justify="left")
    top.append("\u256d" + "\u2500" * btn_inner + "\u256e", style=del_border)
    top.append(" " * btn_gap)
    top.append("\u256d" + "\u2500" * btn_inner + "\u256e", style=exp_border)

    # Middle (labels)
    mid = Text(justify="left")
    mid.append("\u2502", style=del_border)
    mid.append(_center_label(labels[0], btn_inner), style=del_label)
    mid.append("\u2502", style=del_border)
    mid.append(" " * btn_gap)
    mid.append("\u2502", style=exp_border)
    mid.append(_center_label(labels[1], btn_inner), style=exp_label)
    mid.append("\u2502", style=exp_border)

    # Bottom border
    bot = Text(justify="left")
    bot.append("\u2570" + "\u2500" * btn_inner + "\u256f", style=del_border)
    bot.append(" " * btn_gap)
    bot.append("\u2570" + "\u2500" * btn_inner + "\u256f", style=exp_border)

    return top, mid, bot


def _build_project_card(
    project: ProjectSummary,
    *,
    selected: bool,
    box_w: int = 64,
    opacity: float = 1.0,
    card_fade: float = 0.0,
    pulse: float = 0.0,
) -> Panel:
    """Build a single project card showing name + metadata.

    opacity: 0.0–1.0 controls the fade-in from near-black to full colour.
    card_fade: 0.0–1.0 controls the border transition from dim to selected blue.
               Only applies when selected is True; unselected cards stay dim.
    pulse: 0.0–1.0 one-shot white flash on Enter (1.0 = full white, decays to 0).
    """
    title_text = Text(justify="left")
    if selected:
        title_text.append(project.name, style=f"bold {lerp_color(opacity, BLACK_RGB, (255, 255, 255))}")
    else:
        title_text.append(project.name, style=lerp_color(opacity, BLACK_RGB, (140, 140, 140)))
    if getattr(project, "kind", "project") == "roadmap":
        # Amber tag marking a saved roadmap row in the merged "Your projects"
        # list — same amber as the results view's [Large] size badge.
        title_text.append("  ")
        title_text.append("[roadmap]", style=lerp_color(opacity, BLACK_RGB, (220, 180, 60)))

    if selected:
        meta_style = lerp_color(opacity, BLACK_RGB, (140, 140, 160))
    else:
        meta_style = lerp_color(opacity, BLACK_RGB, (100, 100, 100))

    # Build metadata line with color-coded status word inline.
    # Status is styled green (Complete) or amber (In Progress) while the
    # rest of the metadata stays in the default dim colour.
    meta_text = Text(justify="left")
    sep = " \u00b7 "
    if project.created:
        meta_text.append(project.created, style=meta_style)
    if project.status:
        if meta_text.plain:
            meta_text.append(sep, style=meta_style)
        if project.status == "Complete":
            meta_text.append(project.status, style=lerp_color(opacity, BLACK_RGB, (80, 220, 120)))
        elif project.status == "In Progress":
            meta_text.append(project.status, style=lerp_color(opacity, BLACK_RGB, (220, 180, 60)))
        else:
            meta_text.append(project.status, style=meta_style)
    counts = []
    if project.feature_count:
        counts.append(f"{project.feature_count} feature{'s' if project.feature_count != 1 else ''}")
    if project.story_count:
        counts.append(f"{project.story_count} stor{'ies' if project.story_count != 1 else 'y'}")
    if project.task_count:
        counts.append(f"{project.task_count} task{'s' if project.task_count != 1 else ''}")
    if project.sprint_count:
        counts.append(f"{project.sprint_count} sprint{'s' if project.sprint_count != 1 else ''}")
    if counts:
        if meta_text.plain:
            meta_text.append(sep, style=meta_style)
        meta_text.append(sep.join(counts), style=meta_style)
    if project.jira_summary:
        if meta_text.plain:
            meta_text.append(sep, style=meta_style)
        meta_text.append(project.jira_summary, style=meta_style)

    # Progress line (e.g. "3/7 stages complete") — shown as a third line if present
    progress_line = Text(justify="left")
    if project.progress:
        progress_line.append(project.progress, style=meta_style)

    if progress_line.plain:
        content = Group(title_text, meta_text, progress_line)
    else:
        content = Group(title_text, meta_text)
    _dim_border = (35, 35, 45)
    _sel_border = (70, 100, 180)
    _white = (255, 255, 255)
    if selected:
        # Interpolate between dim and blue based on card_fade
        blended = (
            int(_dim_border[0] + (_sel_border[0] - _dim_border[0]) * card_fade),
            int(_dim_border[1] + (_sel_border[1] - _dim_border[1]) * card_fade),
            int(_dim_border[2] + (_sel_border[2] - _dim_border[2]) * card_fade),
        )
        # One-shot white flash on Enter — pulse decays from 1.0 → 0.0
        if pulse > 0:
            blended = (
                int(blended[0] + (_white[0] - blended[0]) * pulse),
                int(blended[1] + (_white[1] - blended[1]) * pulse),
                int(blended[2] + (_white[2] - blended[2]) * pulse),
            )
        border = lerp_color(opacity, BLACK_RGB, blended)
    else:
        border = lerp_color(opacity, BLACK_RGB, _dim_border)

    return Panel(
        content,
        border_style=border,
        box=rich.box.ROUNDED,
        padding=(0, 2),
        width=box_w,
        height=_CARD_H,
    )


def _build_action_button(
    label: str,
    *,
    focused: bool = False,
    card_selected: bool = False,
    color: tuple[int, int, int] = (180, 60, 60),
    opacity: float = 1.0,
    fade_t: float = 0.0,
    btn_w: int = _BTN_W,
) -> Panel:
    """Build a tall action button Panel placed to the right of a project card.

    # See docs: "Architecture" — action buttons sit beside each project card.
    # Buttons are the same height as the card (_CARD_H) so they form a clean row.
    # The 'focused' state is reached by pressing right-arrow from the card.

    The button has rounded corners and the label is vertically centred.
    focused: this button has arrow-key focus (bright highlight).
    card_selected: the parent project row is selected (dim highlight).
    color: RGB tuple for the button's accent colour.
    fade_t: 0.0–1.0 interpolation between grey (0) and accent colour (1).
            When focused, this animates from 0→1 (fade in).
            When unfocused, this animates from 1→0 (fade out).
            If not animating, focused buttons use 1.0 and others use 0.0.
    """
    if card_selected:
        # Interpolate between grey and the accent colour based on fade_t
        current = (
            int(_BTN_GREY[0] + (color[0] - _BTN_GREY[0]) * fade_t),
            int(_BTN_GREY[1] + (color[1] - _BTN_GREY[1]) * fade_t),
            int(_BTN_GREY[2] + (color[2] - _BTN_GREY[2]) * fade_t),
        )
        border = lerp_color(opacity, BLACK_RGB, current)
        if fade_t > 0.5:
            label_style = f"bold {border}"
        else:
            label_style = border
    else:
        border = lerp_color(opacity, BLACK_RGB, (35, 35, 45))
        label_style = lerp_color(opacity, BLACK_RGB, (50, 50, 50))

    text = Text(label, style=label_style, justify="center")

    # Vertically centre: _CARD_H=5 → 3 content lines → blank / label / blank
    content = Group(Text(""), text, Text(""))

    return Panel(
        content,
        border_style=border,
        box=rich.box.ROUNDED,
        width=btn_w,
        height=_CARD_H,
        padding=(0, 0),
    )


# Width for the HTML / Markdown export sub-buttons (wider than _BTN_W
# so "Markdown" has breathing room inside the rounded border).
_EXPORT_SUB_BTN_W = 12


def _build_new_project_card(
    *,
    selected: bool,
    box_w: int = 64,
    opacity: float = 1.0,
    label_text: str = "+ New Project",
) -> Panel:
    """Build the '+ New Project' button card (label_text re-brands it, e.g. '+ New Roadmap')."""
    label = Text(justify="left")
    if selected:
        label.append(label_text, style=f"bold {lerp_color(opacity, BLACK_RGB, (255, 255, 255))}")
    else:
        label.append(label_text, style=lerp_color(opacity, BLACK_RGB, (100, 100, 100)))

    if selected:
        border = lerp_color(opacity, BLACK_RGB, (70, 100, 180))
    else:
        border = lerp_color(opacity, BLACK_RGB, (35, 35, 45))

    return Panel(
        label,
        border_style=border,
        box=rich.box.ROUNDED,
        padding=(0, 2),
        width=box_w,
    )


def _build_profile_card(
    profile: ProfileSummary,
    *,
    selected: bool,
    box_w: int = 64,
    opacity: float = 1.0,
    card_fade: float = 0.0,
    pulse: float = 0.0,
) -> Panel:
    """Build a team analysis profile card showing source, sprints, velocity.

    Same visual pattern as _build_project_card — rounded Panel with
    title + metadata lines, animated border on selection.
    """
    title_text = Text(justify="left")
    _team = getattr(profile, "team_name", "")
    title_label = f"{profile.source}/{profile.project_key}"
    if _team:
        title_label += f" — {_team}"
    if selected:
        title_text.append(title_label, style=f"bold {lerp_color(opacity, BLACK_RGB, (255, 255, 255))}")
    else:
        title_text.append(title_label, style=lerp_color(opacity, BLACK_RGB, (140, 140, 140)))

    meta_style = lerp_color(opacity, BLACK_RGB, (140, 140, 160) if selected else (100, 100, 100))
    sep = " \u00b7 "
    meta_text = Text(justify="left")
    parts = [f"{profile.sample_sprints} sprints"]
    if profile.velocity_avg > 0:
        parts.append(f"{profile.velocity_avg} pts/sprint")
    if profile.sample_stories > 0:
        parts.append(f"{profile.sample_stories} stories")
    if profile.updated:
        parts.append(profile.updated)
    meta_text.append(sep.join(parts), style=meta_style)
    if profile.preview_complete:
        meta_text.append("  ")
        meta_text.append(
            "\u2713 Preview complete",
            style=lerp_color(opacity, BLACK_RGB, (80, 200, 100)),
        )

    # Staleness hint (amber line if >30 days old)
    if profile.staleness_days > 30:
        stale_text = Text(justify="left")
        stale_text.append(
            f"\u21bb Re-analysis recommended ({profile.staleness_days} days old)",
            style=lerp_color(opacity, BLACK_RGB, (220, 180, 60)),
        )
        content = Group(title_text, meta_text, stale_text)
    else:
        content = Group(title_text, meta_text)

    _dim_border = (35, 35, 45)
    _sel_border = (70, 100, 180)
    _white = (255, 255, 255)
    if selected:
        blended = (
            int(_dim_border[0] + (_sel_border[0] - _dim_border[0]) * card_fade),
            int(_dim_border[1] + (_sel_border[1] - _dim_border[1]) * card_fade),
            int(_dim_border[2] + (_sel_border[2] - _dim_border[2]) * card_fade),
        )
        if pulse > 0:
            blended = (
                int(blended[0] + (_white[0] - blended[0]) * pulse),
                int(blended[1] + (_white[1] - blended[1]) * pulse),
                int(blended[2] + (_white[2] - blended[2]) * pulse),
            )
        border = lerp_color(opacity, BLACK_RGB, blended)
    else:
        border = lerp_color(opacity, BLACK_RGB, _dim_border)

    return Panel(
        content,
        border_style=border,
        box=rich.box.ROUNDED,
        padding=(0, 2),
        width=box_w,
        height=_CARD_H,
    )


def _build_new_analysis_card(
    *,
    label: str = "+ New Analysis",
    selected: bool,
    box_w: int = 64,
    opacity: float = 1.0,
) -> Panel:
    """Build the '+ New Analysis' button card.

    label can be customised for dual-board: '+ Analyse Jira Board' etc.
    """
    text = Text(justify="left")
    if selected:
        text.append(label, style=f"bold {lerp_color(opacity, BLACK_RGB, (255, 255, 255))}")
    else:
        text.append(label, style=lerp_color(opacity, BLACK_RGB, (100, 100, 100)))

    if selected:
        border = lerp_color(opacity, BLACK_RGB, (70, 100, 180))
    else:
        border = lerp_color(opacity, BLACK_RGB, (35, 35, 45))

    return Panel(
        text,
        border_style=border,
        box=rich.box.ROUNDED,
        padding=(0, 2),
        width=box_w,
    )


def _build_empty_state_card(
    *,
    selected: bool,
    box_w: int = 64,
    opacity: float = 1.0,
    title: str = "No projects yet",
    subtitle: str = "Press Enter to create your first project",
) -> Panel:
    """Build the empty-state prompt when no items exist (title/subtitle re-brand it)."""
    if selected:
        title_style = f"bold {lerp_color(opacity, BLACK_RGB, (255, 255, 255))}"
    else:
        title_style = lerp_color(opacity, BLACK_RGB, (140, 140, 140))
    sub_style = lerp_color(opacity, BLACK_RGB, (100, 100, 100))

    content = Group(
        Text(title, style=title_style, justify="left"),
        Text(subtitle, style=sub_style, justify="left"),
    )

    if selected:
        border = lerp_color(opacity, BLACK_RGB, (70, 100, 180))
    else:
        border = lerp_color(opacity, BLACK_RGB, (35, 35, 45))

    return Panel(
        content,
        border_style=border,
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=box_w,
    )


def _compute_viewport(
    n_items: int,
    selected: int,
    available_h: int,
) -> tuple[int, int, bool, bool]:
    """Compute visible window for scrolling project list.

    # See docs: "Architecture" — viewport scrolling for the project list.
    # When more project cards exist than fit on screen, the list scrolls to
    # keep the selected card visible. Cards just outside the viewport show
    # a single-line border "peek" that connects directly to the adjacent
    # full card (no gap), matching the curriculum module scroll pattern.

    Returns (start, end, show_peek_above, show_peek_below) where start/end
    are indices into the item list (end exclusive).
    """
    card_slot = _CARD_H + _CARD_SPACING

    # Check if all items fit without scrolling
    total_h = n_items * _CARD_H + max(0, n_items - 1) * _CARD_SPACING
    if total_h <= available_h:
        return 0, n_items, False, False

    # Terminal too small for even one card — just show the selected one
    if available_h < _CARD_H:
        return selected, min(n_items, selected + 1), False, False

    # Try reserving space for 0, 1, or 2 peek stubs and pick the
    # configuration that shows the most full cards while being consistent
    # (actual peeks needed <= reserved space).
    # Peeks are 1 line each and connect directly to the adjacent card
    # (no spacing), so they only consume _PEEK_H lines of space.
    best: tuple[int, int, bool, bool] = (selected, min(n_items, selected + 1), False, False)
    best_vis = 0

    for n_peeks in (0, 1, 2):
        usable = available_h - n_peeks * _PEEK_H
        max_vis = max(1, (usable + _CARD_SPACING) // card_slot)

        # Center viewport on the selected item
        half = max_vis // 2
        start = max(0, selected - half)
        end = min(n_items, start + max_vis)
        start = max(0, end - max_vis)

        show_above = start > 0
        show_below = end < n_items
        actual_peeks = int(show_above) + int(show_below)

        if actual_peeks <= n_peeks and max_vis > best_vis:
            best = (start, end, show_above, show_below)
            best_vis = max_vis

    return best


def _build_peek_above(*, box_w: int = 64, opacity: float = 1.0, title: str = "") -> Group:
    """Build 2-line peek for a card above the viewport.

    Shows a top-border + title, hinting at a card that continues above.
    The open side faces downward toward the viewport, matching the
    original single-line convention:
          ╭──────────────────────────╮
          │  Project Name            │
        ╭──────────────────────────────╮  ← first visible card
    """
    inner_w = box_w - 4 - 2  # 2 spaces narrower on each side, minus border chars
    style = lerp_color(opacity, BLACK_RGB, (60, 60, 70))
    title_style = lerp_color(opacity, BLACK_RGB, (80, 80, 90))

    border_line = Text(justify="left")
    border_line.append("  \u256d" + "\u2500" * inner_w + "\u256e", style=style)

    # Title line — truncate if needed, pad to fill the box width
    content_w = inner_w - 4  # 2 padding each side inside the side borders
    display_title = (title or "")[:content_w]
    pad_r = content_w - len(display_title)

    title_line = Text(justify="left")
    title_line.append("  \u2502  ", style=style)
    title_line.append(display_title + " " * pad_r, style=title_style)
    title_line.append("  \u2502", style=style)

    return Group(border_line, title_line)


def _build_peek_below(*, box_w: int = 64, opacity: float = 1.0, title: str = "") -> Group:
    """Build 2-line peek for a card below the viewport.

    Shows a title + bottom-border, hinting at a card that continues below.
    The open side faces upward toward the viewport, matching the
    original single-line convention:
        ╰──────────────────────────────╯  ← last visible card
          │  Project Name            │
          ╰──────────────────────────╯
    """
    inner_w = box_w - 4 - 2  # 2 spaces narrower on each side, minus border chars
    style = lerp_color(opacity, BLACK_RGB, (60, 60, 70))
    title_style = lerp_color(opacity, BLACK_RGB, (80, 80, 90))

    # Title line — truncate if needed, pad to fill the box width
    content_w = inner_w - 4  # 2 padding each side inside the side borders
    display_title = (title or "")[:content_w]
    pad_r = content_w - len(display_title)

    title_line = Text(justify="left")
    title_line.append("  \u2502  ", style=style)
    title_line.append(display_title + " " * pad_r, style=title_style)
    title_line.append("  \u2502", style=style)

    border_line = Text(justify="left")
    border_line.append("  \u2570" + "\u2500" * inner_w + "\u256f", style=style)

    return Group(title_line, border_line)


# ---------------------------------------------------------------------------
# Roadmap results-view cards
# ---------------------------------------------------------------------------
#
# The Roadmap "results" view (ranked candidate projects) uses the same bordered
# card language as the project list, but the *selected* card expands to reveal
# the project's full description + rationale inline \u2014 unselected cards stay
# compact (title + meta). Because heights vary, the fixed-height _compute_viewport
# above can't be reused; _window_project_cards handles the variable-height window.

_ROADMAP_UNSEL_H = 4  # compact card: border + title + meta + border


def _build_roadmap_project_card(
    project,
    *,
    index: int,
    selected: bool,
    box_w: int = 64,
    body_lines: tuple[str, ...] = (),
    opacity: float = 1.0,
) -> Panel:
    """Build one recommended-project card for the roadmap results view.

    Mirrors _build_project_card's styling (rounded Panel, dim\u2192blue border on
    selection, inline colour-coded token) but the selected card grows to show
    ``body_lines`` \u2014 the caller-wrapped full description + rationale. Unselected
    cards render just the title + meta (height _ROADMAP_UNSEL_H).

    index: 1-based display position. body_lines: pre-wrapped body text, only
    rendered when ``selected`` (the caller caps the count so the card fits the
    viewport).
    """
    from yeaboi.roadmap.render import size_badge

    # Title: "\u25b8 N. Name  [Small]/[Large]" \u2014 badge coloured inline (amber large /
    # green small), the same inline-token convention as _build_project_card.
    title_text = Text(justify="left")
    if selected:
        title_text.append("\u25b8 ", style=lerp_color(opacity, BLACK_RGB, (140, 170, 255)))
        title_text.append(f"{index}. {project.name}", style=f"bold {lerp_color(opacity, BLACK_RGB, (255, 255, 255))}")
    else:
        title_text.append(f"{index}. {project.name}", style=lerp_color(opacity, BLACK_RGB, (140, 140, 140)))
    title_text.append("  ")
    badge_rgb = (220, 180, 60) if getattr(project, "size", "") == "large" else (80, 220, 120)
    title_text.append(size_badge(project), style=lerp_color(opacity, BLACK_RGB, badge_rgb))

    meta_style = lerp_color(opacity, BLACK_RGB, (140, 140, 160) if selected else (100, 100, 100))
    meta = " \u00b7 ".join(x for x in (getattr(project, "quarter", ""), ", ".join(getattr(project, "themes", ()))) if x)
    meta_text = Text(meta, style=meta_style, justify="left")

    parts: list = [title_text, meta_text]
    height = _ROADMAP_UNSEL_H
    if selected and body_lines:
        body_style = lerp_color(opacity, BLACK_RGB, (170, 170, 175))
        parts.append(Text(""))
        for line in body_lines:
            parts.append(Text(line, style=body_style, justify="left"))
        # border(2) + title + meta + blank + body lines
        height = _ROADMAP_UNSEL_H + 1 + len(body_lines)

    border = lerp_color(opacity, BLACK_RGB, (70, 100, 180) if selected else (35, 35, 45))
    return Panel(
        Group(*parts),
        border_style=border,
        box=rich.box.ROUNDED,
        padding=(0, 2),
        width=box_w,
        height=height,
    )


def _build_roadmap_notices_card(warnings: tuple[str, ...], *, box_w: int = 64, opacity: float = 1.0) -> Panel:
    """Build the \u26a0 Notices card for the roadmap results view (amber-bordered)."""
    amber = lerp_color(opacity, BLACK_RGB, (220, 180, 60))
    muted = lerp_color(opacity, BLACK_RGB, (150, 150, 155))
    lines: list = [Text("\u26a0 Notices", style=f"bold {amber}", justify="left")]
    for w in warnings:
        lines.append(Text(f"\u2022 {w}", style=muted, justify="left"))
    return Panel(
        Group(*lines),
        border_style=lerp_color(opacity, BLACK_RGB, (120, 90, 40)),
        box=rich.box.ROUNDED,
        padding=(0, 2),
        width=box_w,
    )


def _window_project_cards(heights: list[int], selected: int, budget: int) -> tuple[int, int, bool, bool]:
    """Variable-height card window keeping the selected card fully visible.

    Cards have differing heights (the selected one is taller), so the fixed-height
    _compute_viewport can't be used. If every card + spacing fits ``budget`` it
    shows all (no peeks). Otherwise it starts from the selected card and greedily
    extends downward then upward, reserving _PEEK_H for each side that still has a
    hidden card \u2014 so the window + its peek stubs never exceed ``budget`` (which is
    why the caller caps the selected card's height to leave room for the stubs).

    Returns (start, end, peek_above, peek_below) \u2014 end exclusive.
    """
    n = len(heights)
    spacing = _CARD_SPACING

    def _span(a: int, b: int) -> int:
        if b <= a:
            return 0
        return sum(heights[a:b]) + (b - a - 1) * spacing

    if _span(0, n) <= budget:
        return 0, n, False, False

    def _used(a: int, b: int) -> int:
        return _span(a, b) + (_PEEK_H if a > 0 else 0) + (_PEEK_H if b < n else 0)

    start, end = selected, selected + 1
    grew = True
    while grew:
        grew = False
        if end < n and _used(start, end + 1) <= budget:
            end += 1
            grew = True
        if start > 0 and _used(start - 1, end) <= budget:
            start -= 1
            grew = True
    return start, end, start > 0, end < n
