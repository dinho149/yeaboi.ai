"""The Ceremonies page — what the team has scheduled, and what it actually did.

One list, one row per ceremony, and the row carries the answer to the question
this page exists for: **did it run, and if not, why not?** A schedule you cannot
see the outcome of is one you stop trusting after the first quiet morning, so
the last-run verdict sits on the row rather than behind a keypress.

Two things are drawn that come from outside the database, because both are
invisible until something fails to happen:

- a **drift** line when the store and the operating system disagree (declared
  with no job, or a job with no declaration), and
- the **month's spend** against each cap, so an unattended run that is about to
  start being skipped says so before it is.

# See docs: "Architecture" — TUI system; this page follows the shared blueprint
"""

from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.agent.state import Ceremony, CeremonyRun
from yeaboi.ceremonies.render import _OUTCOME_STYLE, local_stamp, next_fire
from yeaboi.ui.shared._components import (
    CEREMONIES_THEME,
    PAD,
    TITLE_ROWS,
    build_action_buttons,
    build_page_panel,
    build_reveal_subtitle,
    build_scrollbar,
    calc_viewport,
    ceremonies_title,
)
from yeaboi.ui.shared._scroll import publish_geometry

# Header = blank + title(TITLE_ROWS) + blank + subtitle.
_HEADER_ROWS = 2 + TITLE_ROWS + 1
# Actions = the spacer blank PLUS all three button rows. Counting only the
# buttons loses the bottom border to the crop, because a fixed-height Panel
# crops from the BOTTOM — and a gate whose buttons are half off screen still
# answers Enter.
_ACTION_ROWS = 4


def _cell(text: str, style: str) -> Text:
    """One table cell. ``no_wrap`` is what makes ``overflow`` mean anything —
    without it a long name wraps to a second row and the table stops lining up."""
    return Text(text, style=style, no_wrap=True, overflow="ellipsis")


def _last_run_cell(run: CeremonyRun | None, theme) -> Text:
    if run is None:
        return _cell("never run", theme.dim)
    glyph, style = _OUTCOME_STYLE.get(run.outcome, ("?", theme.dim))
    cell = Text(no_wrap=True, overflow="ellipsis")
    cell.append(f"{glyph} ", style=style)
    # The verdict leads and the timestamp follows, because this column
    # ellipsizes at the minimum terminal width and "stale" is the half worth
    # keeping. A successful run needs no word — the tick already said it.
    if run.outcome != "ok":
        cell.append(f"{run.outcome.removeprefix('skipped_')} ", style=style)
    cell.append(local_stamp(run.fired_at, with_date=False), style=theme.muted)
    return cell


def _spend_cell(ceremony: Ceremony, spent: float, theme) -> Text:
    if not ceremony.monthly_cap_usd:
        return _cell(f"${spent:.2f}" if spent else "—", theme.muted)
    # Amber before it bites, not after: a ceremony that starts being skipped
    # next week should say so this week.
    share = spent / ceremony.monthly_cap_usd
    style = theme.bad if share >= 1 else (theme.warn if share >= 0.8 else theme.muted)
    return _cell(f"${spent:.2f} / ${ceremony.monthly_cap_usd:.0f}", style)


def _render_to_lines(renderable, render_w: int, left_pad: str) -> list:
    """Flatten a renderable to one ``Text`` per rendered row.

    A multi-row table breaks the "one body entry == one rendered row" assumption
    the viewport math depends on, and an unflattened one renders at full height
    regardless of the window — which pushes the action buttons off the bottom of
    a fixed-height Panel. Same helper, same reason, as the usage page's grid.
    """
    from rich.console import Console as _Console

    console = _Console(width=render_w, height=400)
    with console.capture() as capture:
        console.print(renderable)
    return [Text.from_ansi(left_pad + line) for line in capture.get().splitlines()]


def _build_rows(
    ceremonies: list[Ceremony],
    last_runs: dict[str, CeremonyRun | None],
    spend: dict[str, float],
    selected: int,
    theme,
    width: int,
) -> list:
    if not ceremonies:
        return [
            Text(""),
            Text(f"{PAD}Nothing is scheduled yet.", style=theme.muted),
            Text(""),
            Text(f"{PAD}A ceremony puts one mode on the calendar — the standup at 09:00,", style=theme.dim),
            Text(f"{PAD}the delivery report on Monday — and yeaboi runs it while closed.", style=theme.dim),
            Text(""),
            Text(f"{PAD}Press [n] to add one.", style=theme.accent),
        ]

    table = Table(show_header=True, header_style=f"bold {theme.muted}", box=None, padding=(0, 1), pad_edge=False)
    table.add_column(" ", width=2)
    table.add_column("Ceremony", ratio=3, overflow="ellipsis")
    table.add_column("When", ratio=3, overflow="ellipsis")
    table.add_column("Lands in", ratio=2, overflow="ellipsis")
    table.add_column("Last run", ratio=3, overflow="ellipsis")
    table.add_column("This month", ratio=2, overflow="ellipsis")
    for index, ceremony in enumerate(ceremonies):
        chosen = index == selected
        style = theme.dim if not ceremony.enabled else (f"bold {theme.accent_bright}" if chosen else theme.value)
        name = _cell(ceremony.name, style)
        table.add_row(
            Text("▸" if chosen else " ", style=theme.accent),
            name,
            # "paused" replaces the cadence rather than trailing the name: the
            # name column ellipsizes at the minimum width, and a paused ceremony
            # whose state got cropped is the worst cell on the page.
            _cell(next_fire(ceremony, None), theme.muted if ceremony.enabled else theme.warn),
            _cell(", ".join(ceremony.channels), theme.muted),
            _last_run_cell(last_runs.get(ceremony.name), theme),
            _spend_cell(ceremony, spend.get(ceremony.name, 0.0), theme),
        )
    indent = "  "
    return [Text(""), *_render_to_lines(table, max(24, width - 4 - len(indent) - 2), indent)]


def _build_ceremonies_screen(
    ceremonies: list[Ceremony],
    *,
    last_runs: dict[str, CeremonyRun | None] | None = None,
    spend: dict[str, float] | None = None,
    drift: list[str] | None = None,
    selected: int = 0,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    actions: list[str] | None = None,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    message: str = "",
) -> Panel:
    """Build the Ceremonies page: the declared schedule and its outcomes."""
    theme = CEREMONIES_THEME
    title = ceremonies_title(shimmer_tick)
    sub = build_reveal_subtitle("What runs while yeaboi is closed", sub_reveal, pad=PAD + "  ")

    body: list = _build_rows(ceremonies, last_runs or {}, spend or {}, selected, theme, width)

    # Drift is not decoration: a declared ceremony with no installed job will
    # simply never fire, and nothing else in the app would ever say so.
    for line in drift or []:
        body.append(Text(""))
        body.append(Text(f"{PAD}! {line}", style=theme.warn))

    if message:
        body.append(Text(""))
        body.append(Text(f"{PAD}{message}", style=theme.accent))

    viewport_h = calc_viewport(height, header_h=_HEADER_ROWS, action_h=_ACTION_ROWS)
    total = len(body)
    max_scroll = max(0, total - viewport_h)
    offset = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body[offset : offset + viewport_h]
    padded = list(visible) + [Text("")] * max(0, viewport_h - len(visible))

    scrollbar = build_scrollbar(viewport_h, total, offset, max_scroll)
    if scrollbar is not None:
        frame = Table(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
        frame.add_column(ratio=1)
        frame.add_column(width=1)
        frame.add_row(Group(*padded), scrollbar)
        viewport: object = frame
    else:
        viewport = Group(*padded)

    btn_top, btn_mid, btn_bot = build_action_buttons(actions or ["Run now", "Pause", "Back"], action_sel)
    content = Group(Text(""), title, Text(""), sub, viewport, Text(""), btn_top, btn_mid, btn_bot)
    return build_page_panel(content, theme=theme, height=height)
