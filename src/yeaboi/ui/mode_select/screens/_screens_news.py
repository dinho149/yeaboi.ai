"""The front page as a reader: the whole edition as an index, one row per story.

The desktop folds this under the story as "Inside this edition"; here it is a
page of its own, opened with ``i`` from the landing split. Pure builders only —
the run loop lives in :mod:`yeaboi.ui.mode_select` (``_run_front_page_page``).

# See docs: "TUI system" — shared component structure
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.news import edition
from yeaboi.news.paper import Paper
from yeaboi.news.parse import NewsItem
from yeaboi.ui.shared._components import (
    CHANGELOG_THEME,
    PAD,
    Theme,
    build_key_hints,
    build_page_panel,
    build_reveal_subtitle,
    build_scrollbar,
    calc_viewport,
    front_page_title,
)

_OUTLET_COLS = 14
_HINTS = [("↑/↓", "select"), ("enter", "open"), ("r", "refresh"), ("esc", "back")]


def story_row(item: NewsItem, *, page: int, selected: bool, theme: Theme) -> Text:
    """One index row: the page numeral, the outlet, the headline."""
    row = Text(no_wrap=True, overflow="ellipsis")
    row.append(PAD)
    row.append("▸ " if selected else "  ", style=theme.accent_bright)
    row.append(f"{page:>2}  ", style=theme.accent_bright if selected else theme.muted)
    outlet = edition.source_tag(item)[:_OUTLET_COLS]
    row.append(outlet.ljust(_OUTLET_COLS + 2), style=theme.accent if selected else theme.muted)
    row.append(item.title, style=f"bold {theme.value}" if selected else theme.value)
    return row


def _build_front_page_screen(
    stories: Sequence[NewsItem],
    *,
    paper: Paper,
    selected: int = 0,
    width: int = 80,
    height: int = 24,
    now: datetime,
    enabled: bool = True,
    sub_reveal: float | None = None,
) -> Panel:
    """Build the reader: the edition's index, the edition line and the colophon under it."""
    theme = CHANGELOG_THEME
    title = front_page_title(None, width=width)
    sub = build_reveal_subtitle(edition.INSIDE_TITLE, sub_reveal, pad=PAD)

    if stories:
        body = [story_row(item, page=i + 1, selected=i == selected, theme=theme) for i, item in enumerate(stories)]
    else:
        body = [Text(PAD + edition.EMPTY_LINE, style=theme.muted)]

    # The viewport follows the selection: a list picker, not a free scroll.
    viewport_h = calc_viewport(height, header_h=6, action_h=5)
    total = len(body)
    max_scroll = max(0, total - viewport_h)
    scroll = min(max(0, selected - viewport_h + 1), max_scroll) if selected >= viewport_h else 0
    visible = body[scroll : scroll + viewport_h]
    visible += [Text("") for _ in range(max(0, viewport_h - len(visible)))]

    scrollbar = build_scrollbar(viewport_h, total, scroll, max_scroll, always_show=True)
    if scrollbar is not None:
        grid = Table(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
        grid.add_column(ratio=1)
        grid.add_column(width=1)
        grid.add_column(width=1)
        grid.add_column(width=1)
        grid.add_row(Group(*visible), Text(""), scrollbar, Text(""))
        viewport = grid
    else:
        viewport = Group(*visible)

    folio = f"{edition.edition_line(paper, now, enabled=enabled)} {edition.sources_line(paper)}".strip()
    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        viewport,
        Text(""),
        Text(PAD + folio, style=theme.muted, no_wrap=True, overflow="ellipsis"),
        build_key_hints(_HINTS, pad=PAD),
        Text(""),
    )
    return build_page_panel(content, theme=theme, height=height)
