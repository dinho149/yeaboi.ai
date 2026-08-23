"""Row accumulator + height-aware viewport packing, shared by sectioned pages.

A sectioned page builds its body as a list of renderables and a parallel list of
their measured terminal heights, then shows the window of items that fits. Two
modes do this (Standup, Performance) and Analysis does a variant, so the
accumulator and the packing loop live here rather than being copied a third time.

Scroll offsets index *items*, not terminal rows: ``item_heights`` is what maps
between the two, and getting it wrong is what makes a scrollbar thumb lie and a
report's tail unreachable.
"""

from __future__ import annotations

import io
import textwrap
from collections.abc import Sequence

from rich.console import Console
from rich.padding import Padding
from rich.table import Table as RichTable
from rich.text import Text

from yeaboi.ui.shared._components import PAD, Theme

# Panel border + padding (6) plus the scrollbar column (1). One char over and a
# row wraps, silently eating a viewport row.
_CHROME_W = 7


class RowCtx:
    """Renderable accumulator that records each item's measured height.

    Most rows are height-one ``Text`` values; ``add_renderable`` measures
    anything taller (a panel, a tile grid) so the packer below can honour it.
    """

    def __init__(self, theme: Theme, width: int) -> None:
        self.lines: list = []
        self.item_heights: list[int] = []
        self.theme = theme
        self.width = width

    def add(self, line, rendered_h: int = 1) -> None:
        self.lines.append(line)
        self.item_heights.append(rendered_h)

    def add_renderable(self, renderable) -> None:
        """Add a padded Rich renderable with its actual terminal height."""
        padded = Padding(renderable, (0, 1, 0, len(PAD)))
        console = Console(width=max(10, self.width - _CHROME_W), file=io.StringIO(), legacy_windows=False)
        self.add(padded, max(1, len(console.render_lines(padded, pad=False))))

    def add_table(self, table: RichTable) -> None:
        self.add_renderable(table)

    def blank(self) -> None:
        self.add(Text(""))

    def heading(self, text: str) -> None:
        self.blank()
        h = Text(PAD + "  ", justify="left")
        h.append(text, style=f"bold {self.theme.accent}")
        self.add(h)
        self.add(Text(PAD + "  " + "─" * min(len(text), 40), style=self.theme.sep, justify="left"))

    def row(self, label: str, value: str, value_style: str = "") -> None:
        r = Text(PAD + "    ", justify="left")
        r.append(f"{label}:  ", style=self.theme.muted)
        r.append(str(value), style=value_style or self.theme.value)
        self.add(r)

    def line(self, text: str, style: str = "") -> None:
        self.add(Text(PAD + "    " + text, style=style or self.theme.value, justify="left"))

    def wrapped(
        self,
        text: str,
        style: str,
        *,
        indent: str = "    ",
        preserve_newlines: bool = False,
        hanging: int = 0,
    ) -> None:
        """Append word-wrapped lines; optionally honour explicit newlines.

        preserve_newlines keeps the author's own paragraph breaks (a multi-line
        self-report, an email body) instead of collapsing them. ``hanging``
        indents continuation rows by that many extra columns, so a bullet's
        second line lines up under its text rather than under its marker.
        """
        wrap_w = max(24, self.width - len(PAD) - len(indent) - _CHROME_W)
        paragraphs = text.splitlines() if preserve_newlines else [text]
        for para in paragraphs or [""]:
            chunks = textwrap.wrap(para, width=wrap_w, subsequent_indent=" " * hanging) or [""]
            for chunk in chunks:
                self.add(Text(PAD + indent + chunk, style=style, justify="left"))


def max_scroll_for(heights: Sequence[int], viewport_h: int) -> int:
    """Return the largest useful scroll offset: the earliest item whose tail fits.

    Scrolling past this would show blank space below the last item, so the
    scrollbar and the key handler both stop here.
    """
    total = len(heights)
    if total == 0:
        return 0
    tail_h = 0
    max_scroll = max(0, total - 1)
    for i in range(total - 1, -1, -1):
        item_h = heights[i] if i < len(heights) else 1
        if tail_h and tail_h + item_h > viewport_h:
            break
        tail_h += item_h
        max_scroll = i
    return max_scroll


def pack_viewport(items: Sequence, heights: Sequence[int], offset: int, viewport_h: int) -> tuple[list, int]:
    """Return the items visible from ``offset`` and their total rendered height.

    An item taller than the whole viewport is admitted anyway when it lands
    first and let Rich clip it — dropping it would render an empty viewport the
    reader has no key to escape from.
    """
    visible: list = []
    visible_h = 0
    for i in range(max(0, offset), len(items)):
        item_h = heights[i] if i < len(heights) else 1
        if visible_h + item_h > viewport_h:
            if visible:
                break
            visible.append(items[i])
            visible_h = viewport_h
            break
        visible.append(items[i])
        visible_h += item_h
    return visible, visible_h
