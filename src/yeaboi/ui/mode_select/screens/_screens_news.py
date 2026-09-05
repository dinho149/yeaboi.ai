"""The front page: the desktop home's newspaper, as a page of its own.

A sheet laid on the desk, one story up at a time: the nameplate and folio, a
double rule in the world's accent, the kicker and the counter, the headline, and
the spread — the persona duck standing in the story's scene on a tinted plate,
the caption under it, the standfirst with a drop cap, the byline and the read
link beside it. "Inside this edition" unfolds into the index. Pure builders only
— the run loop lives in :mod:`yeaboi.ui.mode_select` (``_run_front_page_page``).

# See docs: "TUI system" — shared component structure
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from rich.align import Align
from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from yeaboi.news import edition
from yeaboi.news.edition import Page
from yeaboi.news.paper import Paper
from yeaboi.news.parse import NewsItem
from yeaboi.ui.shared._ascii_font import BLOCK_GLYPHS, render_ascii_text, render_ascii_text_large
from yeaboi.ui.shared._components import (
    CHANGELOG_THEME,
    LANDING_DETAIL_RESTING,
    LANDING_DETAIL_SELECTED,
    LANDING_VERB_SELECTED,
    TEAM_THEME,
    Theme,
    build_key_hints,
    build_page_panel,
)
from yeaboi.ui.shared._mascot import persona_cells
from yeaboi.ui.shared._scene_backdrops import DUCK_X, PLATE_COLS, PLATE_ROWS, backdrop, scaled

PAPER_MAX_W = 104
LARGE_PAPER_MAX_W = 116  # room for the twice-size picture beside a readable column
# The page's rows besides the spread: nameplate 4, hairline, folio, rule, kicker, headline 2,
# rule, blank, then blank, hairline, inside, blank, colophon, blank, hints, blank; plus the
# page's own border and padding. The picture doubles when the extra 13 rows fit.
_ROWS_BESIDES_SPREAD = 21 + 4
_SHEET_BG = "rgb(21,21,27)"  # a paper a shade lighter than the desk it lies on
_HAIRLINE = "rgb(58,62,72)"
_KEYCAP = "bold rgb(210,210,220)"
_OUTLET_COLS = 14
_HEADLINE_W = 70
_GUTTER = "   "
DEFAULT_CARD: dict[str, Any] = {"color": TEAM_THEME.accent, "tint": "rgb(17,28,20)", "mascot": "duck"}
_HINTS = [("←/→", "turn"), ("o", "open"), ("tab", "inside"), ("r", "refresh"), ("esc", "back")]
_INDEX_HINTS = [("↑/↓", "pick"), ("enter", "turn to it"), ("tab", "fold"), ("esc", "back")]


def _line(text: str = "", style: str | None = None, *, justify: str = "left") -> Text:
    return Text(text, style=style or "", justify=justify, no_wrap=True, overflow="ellipsis")


def _joined(parts: list[tuple[str, str]]) -> Text:
    """One row of ``part · part``, empties dropped."""
    row = _line()
    for part, style in parts:
        if not part:
            continue
        if row.plain:
            row.append(" · ", style=_HAIRLINE)
        row.append(part, style=style)
    return row


def _spaced(left: Text, right: Text, *, width: int) -> Text:
    """``left`` at the left edge and ``right`` at the right edge of a row ``width`` wide."""
    gap = width - left.cell_len - right.cell_len
    if gap < 1:
        return left
    row = left.copy()
    row.append(" " * gap)
    row.append_text(right)
    row.no_wrap = True
    row.overflow = "ellipsis"
    return row


def _folio(left: str, centre: str, right: str, *, width: int) -> Text:
    """Dateline left, the volume in the middle, the edition line right, as a newspaper's folio."""
    row = _line(left, LANDING_DETAIL_RESTING)
    mid = max(0, (width - len(centre)) // 2 - len(left))
    row.append(" " * mid + centre)
    tail = width - row.cell_len - len(right)
    if tail > 0:
        row.append(" " * tail + right)
    return row


def picture_scale(height: int, width: int) -> int:
    """Twice the picture when the page has thirteen rows to spare for it and a column left to read."""
    tall = height >= _ROWS_BESIDES_SPREAD + PLATE_ROWS * 2 + 1
    wide = width - 6 - 4 >= PLATE_COLS * 2 + len(_GUTTER) + 40
    return 2 if tall and wide else 1


def _plate_rows(page: Page | None, *, tint: str, mascot: str, scale: int = 1) -> list[Text]:
    """The picture: the scene's ink backdrop with the persona duck standing in it, on the tinted plate."""
    scene = page.scene if page is not None else edition.DEFAULT_SCENE
    plate_rows = PLATE_ROWS * scale
    canvas = [
        [(" ", None) if ch == "." else (ch, LANDING_DETAIL_RESTING) for ch in row]
        for row in scaled(backdrop(scene), scale)
    ]
    if page is not None:
        cells = persona_cells(page.persona, mascot=mascot, scale=scale)
        top = plate_rows - len(cells)
        for y, row in enumerate(cells):
            for x, cell in enumerate(row):
                if cell[1] is not None and 0 <= top + y < plate_rows:
                    canvas[top + y][DUCK_X * scale + x] = cell
    rows: list[Text] = []
    for row in canvas:
        text = Text(no_wrap=True, overflow="crop")
        for glyph, style in row:
            # Two-colour half-blocks carry their own background; everything else sits on the plate.
            text.append(glyph, style=style if style and " on " in style else f"{style or ''} on {tint}".strip())
        rows.append(text)
    return rows


def _standfirst(summary: str, *, text_w: int, color: str) -> list[Text]:
    """The summary with a drop cap: the first letter two rows tall, the first two lines set beside it."""
    first = summary[:1]
    cap = render_ascii_text(first) if first.upper() in BLOCK_GLYPHS and first.strip() else []
    if not cap or not cap[0]:
        return [_line(line, LANDING_DETAIL_SELECTED) for line in textwrap.wrap(summary, text_w)]
    cap_w = len(cap[0]) + 1
    beside = textwrap.wrap(summary[1:].lstrip(), text_w - cap_w)
    rows: list[Text] = []
    for i in range(2):
        row = Text(cap[i].ljust(cap_w), style=f"bold {color}", no_wrap=True, overflow="ellipsis")
        row.append(beside[i] if i < len(beside) else "", style=LANDING_DETAIL_SELECTED)
        rows.append(row)
    rest = " ".join(beside[2:])
    rows += [_line(line, LANDING_DETAIL_SELECTED) for line in textwrap.wrap(rest, text_w)]
    return rows


def _story_column(page: Page | None, *, card: dict[str, Any], text_w: int, rows_h: int) -> list[Text]:
    """The words beside the picture: standfirst, byline, the read link — exactly ``rows_h`` rows."""
    rows: list[Text] = []
    if page is None:
        rows.append(_line(edition.EMPTY_LINE, LANDING_VERB_SELECTED))
    else:
        if page.item.summary:
            rows += _standfirst(page.item.summary, text_w=text_w, color=card["color"])[: rows_h - 4]
            rows.append(_line(" "))
        rows.append(_line(page.byline, LANDING_DETAIL_SELECTED))
        rows.append(_line(" "))
        read = _line()
        read.append("o", style=_KEYCAP)
        read.append(f"  {page.read}", style=LANDING_DETAIL_RESTING)
        rows.append(read)
    rows = rows[:rows_h]
    rows += [_line(" ") for _ in range(rows_h - len(rows))]
    return rows


def story_row(item: NewsItem, *, page: int, selected: bool, theme: Theme, pad: str = "") -> Text:
    """One index row: the page numeral, the outlet, the headline."""
    row = Text(no_wrap=True, overflow="ellipsis")
    row.append(pad)
    row.append("▸ " if selected else "  ", style=theme.accent_bright)
    row.append(f"{page:>2}  ", style=theme.accent_bright if selected else theme.muted)
    outlet = edition.source_tag(item)[:_OUTLET_COLS]
    row.append(outlet.ljust(_OUTLET_COLS + 2), style=theme.accent if selected else theme.muted)
    row.append(item.title, style=f"bold {theme.value}" if selected else theme.value)
    return row


def index_lines(stories: Sequence[NewsItem], current: int) -> list[tuple[int, NewsItem]]:
    """Every story but the one that is up, with its 1-based page; nothing with fewer than two others."""
    if len(stories) <= 2:
        return []
    return [(i + 1, item) for i, item in enumerate(stories) if i != current]


def _index_rows(stories: Sequence[NewsItem], *, current: int, selected: int, theme: Theme, rows_h: int) -> list[Text]:
    """The index box in the spread's rows: the other stories, the picked one bright."""
    rows = [
        story_row(item, page=number, selected=i == selected, theme=theme)
        for i, (number, item) in enumerate(index_lines(stories, current))
    ]
    rows = rows[:rows_h]
    rows += [_line(" ") for _ in range(rows_h - len(rows))]
    return rows


def _spread_rows(page: Page | None, *, card: dict[str, Any], text_w: int, scale: int) -> list[Text]:
    """The picture beside the words, one Text per row, then the caption under the picture."""
    mascot = "robo" if card["mascot"] == "robo" else "duck"
    plate = _plate_rows(page, tint=card["tint"], mascot=mascot, scale=scale)
    words = _story_column(page, card=card, text_w=text_w, rows_h=PLATE_ROWS * scale)
    rows: list[Text] = []
    for left, right in zip(plate, words, strict=True):
        row = Text.assemble(left, _GUTTER, right)
        row.no_wrap = True
        row.overflow = "ellipsis"
        rows.append(row)
    rows.append(_line(page.caption if page is not None else "", LANDING_DETAIL_RESTING))
    return rows


def _build_front_page_screen(
    page: Page | None,
    *,
    stories: Sequence[NewsItem],
    paper: Paper,
    current: int = 0,
    selected: int = 0,
    index_open: bool = False,
    card: dict[str, Any] | None = None,
    width: int = 80,
    height: int = 24,
    now: datetime,
    enabled: bool = True,
    version: str = "",
) -> Panel:
    """Build the front page: the sheet on the desk, the story that is up, and the index when unfolded."""
    theme = CHANGELOG_THEME
    card = card or DEFAULT_CARD
    accent = card["color"]
    scale = picture_scale(height, width)
    inner_w = width - 6  # the page's border and its (1, 2) padding
    paper_w = min(inner_w - 4, LARGE_PAPER_MAX_W if scale > 1 else PAPER_MAX_W)  # less the sheet's own side padding
    text_w = max(20, paper_w - PLATE_COLS * scale - len(_GUTTER))
    headline_w = min(paper_w, _HEADLINE_W)

    rows: list[Text] = [
        _line(line, LANDING_VERB_SELECTED, justify="center") for line in render_ascii_text_large("yeaboi", 2)
    ]
    rows.append(_line("─" * paper_w, _HAIRLINE))
    rows.append(
        _folio(
            edition.dateline(now),
            edition.volume_line(version),
            edition.edition_line(paper, now, enabled=enabled),
            width=paper_w,
        )
    )
    rows.append(_line("═" * paper_w, accent))

    if page is not None:
        source = edition.source_tag(page.item)
        if page.kicker.endswith(f" {source}"):
            source = ""  # "From yeaboi · yeaboi" says it twice
        kicker = _joined([(page.kicker, LANDING_DETAIL_RESTING), (source, LANDING_DETAIL_SELECTED)])
        counter = Text()
        if page.counter:
            counter.append("‹ ", style=LANDING_DETAIL_RESTING)
            counter.append(page.counter, style=LANDING_DETAIL_SELECTED)
            counter.append(" ›", style=LANDING_DETAIL_RESTING)
        rows.append(_spaced(kicker, counter, width=paper_w))
        headline = textwrap.wrap(page.item.title, headline_w)[:2]
    else:
        rows.append(_line(" "))
        headline = []
    rows += [_line(line, LANDING_VERB_SELECTED) for line in headline]
    rows += [_line(" ") for _ in range(2 - len(headline))]
    rows.append(_line("─" * headline_w, accent))
    rows.append(_line(" "))

    spread_h = PLATE_ROWS * scale + 1
    if index_open:
        rows += _index_rows(stories, current=current, selected=selected, theme=theme, rows_h=spread_h)
    else:
        rows += _spread_rows(page, card=card, text_w=text_w, scale=scale)

    rows.append(_line(" "))
    rows.append(_line("─" * paper_w, _HAIRLINE))
    inside = edition.inside_label(len(stories))
    if inside:
        rows.append(_line(f"{edition.INSIDE_TITLE}  ▴" if index_open else f"{inside}  ▾", LANDING_DETAIL_SELECTED))
    else:
        rows.append(_line(" "))
    rows.append(_line(" "))
    rows.append(_line(edition.sources_line(paper) or " ", LANDING_DETAIL_RESTING))
    rows.append(_line(" "))
    rows.append(build_key_hints(_INDEX_HINTS if index_open else _HINTS))
    rows.append(_line(" "))

    # The sheet lies in the middle of the desk when the desk is taller than it.
    sheet = Padding(Group(*rows), (0, 2), style=f"on {_SHEET_BG}")
    placed = Align(sheet, "center", vertical="middle", width=paper_w + 4, height=max(len(rows), height - 4))
    return build_page_panel(placed, theme=theme, height=height)
