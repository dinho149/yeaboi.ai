"""Secondary screen builders for mode selection: intake, offline, export, import, team analysis.

# See docs: "Architecture" — this module contains rendering functions
# for the intake mode selection, offline sub-menu, export success,
# import file path input, project export success, and team analysis screens.
# These are pure functions that return Rich Panel renderables — no I/O or state.
"""

from __future__ import annotations

import textwrap
import time

import rich.box
from rich.cells import cell_len
from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.beta import BETA_LABEL, BETA_RGB
from yeaboi.config import SCREENSAVER_STYLES, VALID_LOG_LEVELS
from yeaboi.ui.mode_select.screens._analysis_sections import (
    _TA_CARDS,
    _measure_render_height,
    _ta_glossary_lines,
    _ta_narrative_block,
    _ta_overview,
    _TaCtx,
)
from yeaboi.ui.mode_select.screens._screens import _INTAKE_CARDS, _OFFLINE_CARDS, _build_mode_row
from yeaboi.ui.shared._animations import ease_out_cubic
from yeaboi.ui.shared._ascii_font import render_ascii_text
from yeaboi.ui.shared._components import (
    ANALYSIS_THEME,
    PAD,
    PLANNING_THEME,
    TITLE_ROWS,
    action_rows_height,
    build_action_buttons,
    build_action_rows,
    build_page_panel,
    build_progress_dots,
    build_scrollbar,
    calc_viewport,
    planning_title,
)
from yeaboi.ui.shared._scroll import publish_geometry

# ---------------------------------------------------------------------------
# Shared analysis review screen builder (mirrors planning mode layout)
# ---------------------------------------------------------------------------

_ANALYSIS_STAGES = ["Instructions", "Epic", "Stories", "Tasks", "Sprint"]

# The preview page's four work-item tabs. Instructions is not among them: it is
# the calibration every one of these was generated FROM, so it is reference you
# read *while* reviewing them, not a fifth thing to review. It holds the left
# column and never leaves the screen.
_PREVIEW_TABS = ("Epic", "Stories", "Tasks", "Sprint")
_SPLIT_GUTTER = 4  # blank columns between the two, in place of a divider rule
_PREVIEW_SUBTITLES = (
    "Does this epic match your team's style?",
    "Do these stories match your team's style?",
    "Do these tasks match your team's decomposition style?",
    "Does this sprint plan match your team's capacity?",
)
# "Accept" on every tab, including the last. The forward slot is a fixed piece of
# chrome and the tabs are free to move between, so relabelling it "Done" on the
# Sprint tab made a tab leave and another arrive in the same slot — the entrance
# animation then flashed the old label for a few frames every time you crossed
# between Tasks and Sprint. Sprint has no Edit because there is no sprint editor;
# that difference is real, and the one above was not.
# No Accept. It was the forward step of a march, and these four stopped being a
# march when they became tabs — you move with the strip, and "accepting" a tab
# you can leave and come back to says nothing. What is left acts on the artifact
# in front of you. Sprint has no Edit because there is no sprint editor.
_PREVIEW_ACTIONS = (
    ["Edit", "Regenerate", "Export"],
    ["Edit", "Regenerate", "Export"],
    ["Edit", "Regenerate", "Export"],
    ["Regenerate", "Export"],
)
# Derived, not chosen: the stage body builders floor their wrap width at 40, so a
# column narrower than that plus its indent and scrollbar draws rules and rows
# that overhang it.
_SPLIT_MIN_COL = 40 + len(PAD) + 2
# The left column ends on the flow's column rather than at half the content
# (see preview_split_widths), which puts it two narrower than an even split —
# so the width at which it reaches the minimum is higher than a halving would
# suggest. Solved for that, not for the halving: left(w) = (w - 6)//2 - 4.
_SPLIT_MIN_W = (_SPLIT_MIN_COL + 4) * 2 + 6


# The analysis flow's two pages, declared once. Every page in the flow shows the
# same pair and marks itself live, so the control does not move, rename itself or
# change what it crosses depending on where you are standing.
# The coaching plan is a section OF the analysis — it has its own tab in the
# section bar — so the second page was named after content it was showing twice.
# It is named for what it is for instead: the work items drafted from the
# analysis.
ANALYSIS_PAGES = ("Analysis", "Work items")


def analysis_pager(active: int) -> tuple:
    """The bottom-centre pager for an analysis page, with ``active`` live."""
    return (*ANALYSIS_PAGES, 1 if active else 0)


def analysis_divider_x(width: int) -> int:
    """The one column the analysis flow splits its pager over.

    Every page in the flow has some vertical near the middle — the results
    page's scrollbar, the plan page's gutter — but they do not fall in the same
    column, and a control that lands somewhere different on each page is one
    you have to find again on each page. So the flow picks ONE column: the
    results page's, which is where the scrollbar runs.
    """
    return max(1, (width - 6) // 2 - 2)


def preview_is_split(width: int) -> bool:
    """Whether the preview page has room for two columns.

    Under it the page keeps the instructions as a fifth tab instead — reference
    you can still reach, rather than a column too narrow to read.
    """
    return width >= _SPLIT_MIN_W


def preview_split_widths(width: int) -> tuple[int, int]:
    """``(left, right)`` content widths for the split preview page.

    Public because the caller has to build each column's body AT its own width —
    wrapping is done by the body builders, and a body wrapped to the page would
    overflow the column it was put in.
    """
    content = max(20, width - 6)
    # The left column ends ON the flow's column, so its scrollbar runs down the
    # same line as the results page's and as the pager's own split. Halving the
    # content put it two columns right of that, which is a difference you can
    # see and cannot explain.
    left = max(10, min(content - _SPLIT_GUTTER - 10, analysis_divider_x(width) - _TAB_COL_OFFSET + 2))
    return left, content - _SPLIT_GUTTER - left


def _hang_wrap(rows: list, width: int, indent: str = "  ") -> list:
    """Re-wrap finished rows so an overrun continues under its own text.

    Rich has no hanging indent: a Text that overruns wraps its tail to column
    zero, and every row here is already indented by PAD, so the tail landed
    further LEFT than the sentence it belonged to and read as a new block.

    A post-pass rather than a rule each branch has to remember. ``Text.wrap``
    keeps the styling, so a row built from three differently-styled runs comes
    back out as the same three runs — which is why this can run over the finished
    body instead of every place that appends to it.
    """
    from io import StringIO

    from rich.console import Console as _Console

    probe = _Console(file=StringIO(), width=max(20, width), force_terminal=False)
    room = max(10, width - len(_PAD) - len(indent))
    out: list = []
    for row in rows:
        if not isinstance(row, Text) or row.cell_len <= width:
            out.append(row)
            continue
        for i, part in enumerate(row.wrap(probe, room)):
            if i == 0:
                out.append(part)  # already carries its own PAD
            else:
                out.append(Text(_PAD + indent, justify="left").append_text(part))
    return out


def _scrolled_column(
    body_lines: list,
    *,
    scroll_offset: int,
    scroll_meta: dict | None,
    viewport_h: int,
    content_w: int,
):
    """Clip a body to ``viewport_h`` *rendered* rows, pad it out, and hang a
    scrollbar off the right when there is more than fits.

    Measured, not counted: one item can wrap to several rows at this width, so a
    viewport counted in list entries silently ate the bottom of the page.
    """
    heights = [max(1, _measure_render_height(bl, content_w)) for bl in body_lines]
    total = sum(heights)
    max_off, acc = 0, 0
    for i in range(len(body_lines) - 1, -1, -1):
        acc += heights[i]
        if acc >= viewport_h:
            max_off = i
            break
    actual = min(scroll_offset, max_off)
    publish_geometry(scroll_meta, max_off, viewport_h)

    visible, vis_h = [], 0
    for i in range(actual, len(body_lines)):
        # As above: show the top of an over-tall item rather than nothing.
        if visible and vis_h + heights[i] > viewport_h:
            break
        visible.append(body_lines[i])
        vis_h += heights[i]
    rows = list(visible) + [Text("") for _ in range(max(0, viewport_h - vis_h))]

    bar = build_scrollbar(viewport_h, total, actual, max_off)
    if bar is None:
        return Group(*rows)
    grid = Table.grid(padding=0)
    grid.add_column(width=max(1, content_w))
    grid.add_column(width=1)
    grid.add_row(Group(*rows), bar)
    return grid


def _build_preview_split_screen(
    instr_lines: list,
    item_lines: list,
    *,
    stage_index: int = 0,
    instr_scroll: int = 0,
    item_scroll: int = 0,
    instr_meta: dict | None = None,
    item_meta: dict | None = None,
    focus_instructions: bool = False,
    width: int = 80,
    height: int = 24,
    actions: list[str] | None = None,
    subtitle: str = "",
    ready: tuple[bool, ...] | None = None,
    shimmer_tick: float | None = None,
) -> Panel:
    """The preview page: instructions on the left, the work item on the right.

    The four generated artifacts replaced the instructions on screen when they
    were tabs of the same column — and the instructions are the calibration they
    were generated from, which is exactly what you want in front of you while
    judging whether they match the team. So the page is two columns: the
    calibration stays put on the left, and the tab strip and whichever work item
    it selects own the right.

    Both columns scroll, independently, because both are longer than a screen.
    ``focus_instructions`` says which one the scroll keys are driving; the loop
    flips it with Tab and the focused column's header lights up to show it.
    """
    from yeaboi.ui.shared._components import analysis_title

    _here = max(0, min(stage_index, len(_PREVIEW_TABS) - 1))
    _actions = list(actions or _PREVIEW_ACTIONS[_here])
    if focus_instructions and "Edit" not in _actions:
        # Edit acts on the focused column, so the Sprint tab — which has no
        # editor of its own — still offers one while the instructions are focused.
        _actions.insert(0, "Edit")
    left_w, right_w = preview_split_widths(width)
    _rdy = tuple(ready) if ready is not None else (True,) * len(_PREVIEW_TABS)

    # Built against the RIGHT column's width, then offset into place: the bar
    # spreads over the column it belongs to, not over the page.
    tab_rows, tab_spans = _settings_tab_bar(
        list(_PREVIEW_TABS),
        _here,
        ANALYSIS_THEME,
        right_w + 6,
        pos=_preview_tab_pos(stage_index, len(_PREVIEW_TABS)),
        taper=False,
        spread=True,
        muted=tuple(not r for r in _rdy),
    )

    # 3 above the grid — the leading blank and the two wordmark rows — and 1
    # below, because the chrome band overwrites the last THREE rendered rows and
    # two of those are the panel's own bottom padding and border. Reserving three
    # left five rows of nothing between the last line and the pockets.
    col_h = calc_viewport(height, header_h=3, action_h=2)
    body_h = max(1, col_h - 4)  # each column's own header rows, inside the grid

    # no_wrap on both header captions: a fixed header row that wraps steals a row
    # from the body it sits over, and only on one side, so the two columns stop
    # lining up at exactly the widths where that matters most.
    # The left column starts straight under the wordmark. It has no header of its
    # own any more — the caption is gone and the tabs opposite are not its — so
    # holding three blank rows to stay level with them only pushed the first line
    # of the calibration three rows further from the title naming it. It gets
    # those rows as content instead.
    left = _scrolled_column(
        instr_lines,
        scroll_offset=instr_scroll,
        scroll_meta=instr_meta,
        viewport_h=col_h,
        content_w=max(20, left_w - 1),
    )
    right = Group(
        *tab_rows,
        # A line between the rule and the first words under it. Hard against the
        # rule, the caption read as part of the strip rather than as the start
        # of what the strip opened.
        Text(""),
        Text(_PAD + (subtitle or _PREVIEW_SUBTITLES[_here]), style="dim", no_wrap=True, overflow="ellipsis"),
        _scrolled_column(
            item_lines,
            scroll_offset=item_scroll,
            scroll_meta=item_meta,
            viewport_h=body_h,
            content_w=max(20, right_w - 1),
        ),
    )

    body = Table.grid(padding=0)
    body.add_column(width=left_w)
    body.add_column(width=_SPLIT_GUTTER)  # a gutter, not a divider: one more
    body.add_column(width=right_w)  # vertical rule on this page reads as a frame
    body.add_row(left, Text(""), right)

    panel = build_page_panel(Group(Text(""), analysis_title(shimmer_tick), body), theme=ANALYSIS_THEME, height=height)
    # The strip's spans are relative to the right column, so shift them across it.
    _tab_top = 3 + 1 + TITLE_ROWS
    _shift = _TAB_COL_OFFSET + left_w + _SPLIT_GUTTER
    panel._section_tabs = [
        (_tab_top, _tab_top + 1, _shift + a, _shift + b - 1, key.lower())
        for (a, b), key in zip(tab_spans, _PREVIEW_TABS, strict=True)
    ]
    # This page has a pager too. Every page in the analysis flow shows itself and
    # the neighbour it came from, so the bottom-centre control is in the same
    # place throughout rather than vanishing once you reach the plan.
    panel._pager = analysis_pager(1)
    # The last terminal column belonging to the left column, so a wheel tick can
    # be routed to whichever side the pointer was over.
    panel._split_left_end = _TAB_COL_OFFSET + left_w - 1
    # The flow's column, not this page's own gutter: the pill has to be in the
    # same place on every page of the flow before it can be in the right place
    # on any one of them.
    panel._pager_divider_x = analysis_divider_x(width)
    panel._page_tabs = [(name, f"act:{name}") for name in _actions]

    return panel


def _title_with_crumb(title, crumb_text: str, *, style: str = "bold white"):
    """The wordmark with its crumb beside it, on the wordmark's last row.

    The crumb names the team, source and window this page is about — it belongs
    to the title rather than to the body, and on its own row it cost every page a
    line to say what the heading was already introducing. Blank cell first, so it
    sits on the wordmark's baseline: two rows of block glyphs with one line of
    text against their cap reads as unaligned.
    """
    if not crumb_text:
        return title
    grid = Table.grid(padding=(0, 3))
    grid.add_column()
    grid.add_column(ratio=1)
    grid.add_row(title, Group(Text(""), Text(crumb_text, style=style, no_wrap=True, overflow="ellipsis")))
    return grid


def _build_analysis_review_screen(
    body_lines: list,
    *,
    stage_index: int = 0,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    actions: list[str] | None = None,
    subtitle: str = "",
    chrome_actions: bool = True,
    ready: tuple[bool, ...] | None = None,
    crumb: str = "",
    show_stages: bool = True,
    column_body: bool = False,
    shimmer_tick: float | None = None,
) -> Panel:
    """Shared screen builder for all analysis preview pages.

    ``chrome_actions`` puts the actions in the chrome — forward in the slot
    beside the music pocket, the rest as tabs beside back. Every caller wants
    that: a row of buttons in the body sits directly on top of the back tab,
    and the gate's own buttons were the last pair still doing it.

    Uses shared UI primitives (build_action_buttons, build_scrollbar,
    build_progress_dots, calc_viewport) for visual consistency.
    """
    from yeaboi.ui.shared._components import analysis_title

    _actions = actions or ["Accept", "Edit", "Regenerate", "Export"]
    title = analysis_title(shimmer_tick)
    # A tab bar, not progress dots: these five are pages you move between, and
    # dots describe a march you have to complete in order. ``ready`` dims the
    # ones whose content has not arrived yet — they are still shown, because a
    # tab that appears when its content does is a menu that moves under you.
    stages = list(_ANALYSIS_STAGES)
    _rdy = tuple(ready) if ready is not None else (True,) * len(stages)
    progress_rows, _tab_spans = _settings_tab_bar(
        stages,
        max(0, min(stage_index, len(stages) - 1)),
        ANALYSIS_THEME,
        width,
        pos=_preview_tab_pos(stage_index, len(stages)),
        taper=False,
        muted=tuple(not r for r in _rdy),
        # Half the page, parked right: packed against the edge the five read as
        # one clump in the corner, and spread over the whole width they run back
        # under the content the strip was moved out of the way of.
        spread=(width - 6) // 2,
        align_right=True,
    )
    # An empty subtitle takes no row at all, rather than a blank one: the page's
    # own heading is right underneath it, so a spacer between the two only
    # pushed the content down to say nothing.
    sub = Text(_PAD + subtitle, style="dim", justify="left") if subtitle else None

    # ── Viewport (height-aware for line wrapping)
    # 2 for the chrome band, not 3: it overwrites the last THREE *rendered* rows,
    # and calc_viewport has already taken two of those off as the panel's bottom
    # padding and border — so one row covers it, and the second is a single line
    # of air so the last sentence is not sitting on the pockets' roof.
    # 6, not 7: the spacer between the wordmark and the stage strip is gone.
    # 6 with the stage strip, 4 without it — a page that does not draw the strip
    # must not go on reserving its two rows.
    viewport_h = calc_viewport(height, header_h=6 if show_stages else 5, action_h=2 if chrome_actions else 4)

    # Measure actual terminal height. Most pages pass Text, while the redesigned
    # Team Insights page also passes Rich panels and tables.
    _content_w = max(20, width - 7)
    _item_heights: list[int] = []
    _total_rendered = 0
    for bl in body_lines:
        h = max(1, _measure_render_height(bl, _content_w))
        _item_heights.append(h)
        _total_rendered += h

    # Find max scroll offset
    max_scroll = max(0, len(body_lines) - 1)
    _acc = 0
    for _ms in range(len(body_lines) - 1, -1, -1):
        _acc += _item_heights[_ms]
        if _acc >= viewport_h:
            max_scroll = _ms
            break
    else:
        max_scroll = 0
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)

    # Collect visible items
    visible: list = []
    _vis_h = 0
    for i in range(actual_scroll, len(body_lines)):
        ih = _item_heights[i]
        # Always take the first one, even if it is taller than the viewport: an
        # item is a whole card or table, and dropping it for not fitting left
        # the page blank on a short terminal rather than showing its top.
        if visible and _vis_h + ih > viewport_h:
            break
        visible.append(body_lines[i])
        _vis_h += ih

    # Scrollbar + content padding
    _sb_text = build_scrollbar(viewport_h, _total_rendered, actual_scroll, max_scroll)
    padded_lines: list = list(visible)
    for _i in range(max(0, viewport_h - _vis_h)):
        padded_lines.append(Text(""))

    # The buttons move into the chrome, the same way the results page's did:
    # a row of them under the content collided with the back tab, and the one
    # that moves you forward belongs in the forward slot rather than in a row
    # with the ones that act on what you are looking at.
    _forward = _actions[0] if _actions and chrome_actions else ""
    _tabbed = list(_actions[1:]) if chrome_actions else []
    btn_top, btn_mid, btn_bot = (None, None, None) if chrome_actions else build_action_buttons(_actions, action_sel)

    # Build viewport with optional scrollbar
    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    # Every page in the flow runs its bar down the flow's own column, so the bar
    # and the pager's split are one line wherever you are. Without this the Work
    # items page put its bar on the page's right border while its pill was split
    # halfway across — the two furthest-apart verticals on one screen.
    _reserve = max(0, width - analysis_divider_x(width) - 4) if column_body else 0
    if _reserve:
        viewport_renderable = Padding(viewport_renderable, (0, _reserve, 0, 0))

    if not show_stages:
        # One blank in the strip's place, not none. Without a strip the body
        # started on the wordmark's own last row, level with the crumb, so the
        # first heading read as part of the title rather than under it.
        progress_rows, _tab_spans = [Text("")], []
    content = Group(
        Text(""),
        _title_with_crumb(title, crumb),
        # No spacer between the wordmark and the strip. The strip only occupies
        # the right half, so on the left it is two blank rows already — a third
        # one above it pushed the first line of every page that much further
        # from the only thing naming it.
        *progress_rows,
        *([sub] if sub is not None else []),
        viewport_renderable,
        *([] if chrome_actions else [Text(""), btn_top, btn_mid, btn_bot]),
    )

    panel = build_page_panel(content, theme=ANALYSIS_THEME, height=height)
    # (labels row, underline row, x0, x1, page key) so a click on a tab opens it.
    # +3: panel border, top padding, then the leading blank and the wordmark.
    _tab_top = 3 + 1 + TITLE_ROWS
    panel._section_tabs = [
        (_tab_top, _tab_top + 1, _TAB_COL_OFFSET + a, _TAB_COL_OFFSET + b - 1, key.lower())
        for (a, b), key in zip(_tab_spans, stages[: len(_tab_spans)], strict=True)
    ]
    # "act:<name>" so a click on a tab comes back as the action's own name and
    # rejoins the keyboard path — see the results page for the same contract.
    # Every action in the LEFT strip, the forward one included. It used to ride
    # the bottom-right slot opposite back, which reads as the far end of a wizard
    # — but these pages are not a wizard any more, and an action that acts on
    # what you are looking at belongs with the others that do.
    panel._page_tabs = [(name, f"act:{name}") for name in _actions]
    # Kept off the panel's rendered chrome but still named, so Enter knows which
    # of the actions it means without the tab being drawn twice.
    panel._forward_action = _forward or (_actions[0] if _actions else "")
    return panel


# 2-space content indent for every secondary screen: headings then land at the
# title's column and value rows one level in, matching the Settings screen. This
# is the single knob — all these screens indent via _PAD (and size via len(_PAD)).
# One page pad, shared with the rest of the app.
#
# This was its own tighter two-space value, which meant every screen in this file
# put its body text two columns left of its own title art and button row — both of
# which come from _components.py at the four-space PAD. The results views made it
# worse by importing the shared PAD directly, so a single screen mixed both.
# Kept as a name because 240-odd call sites read better than a rename would.
_PAD = PAD

# The two columns a Panel's left and right borders take. Anything measured
# against the panel's usable interior has to come off the console width first.
_PANEL_BORDER_W = 2
# …and the padding inside those borders. build_page_panel pads (1, 2), so two
# more columns a side. Border alone is not enough for anything that PACKS to its
# budget (the action bar): four columns of slack is the difference between a row
# that fits and a row Rich soft-wraps into a bank of invisible buttons.
_PANEL_PADDING_W = 4


def _link_lines(
    label: str, url: str, *, width: int, label_style: str, url_style: str, indent: str = "    "
) -> list[Text]:
    """Render a ``label: url`` row as lines that each occupy exactly one terminal row.

    Every other row in these screens is one Text and therefore one row, which is
    what lets the viewport reserve a fixed number of lines. A URL breaks that
    assumption: a tunnel hostname or a host link carrying a token is routinely
    wider than the panel, Rich silently soft-wraps it, and the viewport then
    draws more rows than it reserved — pushing whatever is below it off the
    bottom. That was invisible while the action bar was one row of buttons that
    happened to survive being nudged; with a wrapping bar it costs a whole row of
    buttons.

    So the wrapping happens here, where it can be counted. The URL moves to its
    own indented line when the pair will not fit, and is hard-split if even that
    is too narrow — mid-token, deliberately: a URL has no safe break point, and a
    host reading one off the screen is better served by all of it on two lines
    than by most of it on one.
    """
    # Budget, measured rather than guessed: the panel takes 2 border + 4 padding
    # columns and the viewport reserves 2 more for the scrollbar and its gap, so
    # a body line has `width - 8` to play with. Subtract this row's own indent
    # (_PAD + *indent*) and what is left is what the label and URL have to share.
    # `indent` is a parameter because the join blocks sit level with their
    # heading while value rows elsewhere sit one step in — and a budget computed
    # for the wrong one wraps a line that fits, or fails to wrap one that doesn't.
    avail = max(24, width - 14 - len(indent))
    one_line = f"{label}:  {url}"
    if len(one_line) <= avail:
        row = Text(_PAD + indent, justify="left")
        row.append(f"{label}:  ", style=label_style)
        row.append(url, style=url_style)
        return [row]

    head = Text(_PAD + indent, justify="left")
    head.append(f"{label}:", style=label_style)
    lines = [head]
    # The continuation lines are indented two further columns, so they get two
    # fewer than `avail` — overshooting here is what makes Rich ellipsize the
    # tail of a token the host is trying to read off the screen.
    chunk = max(16, width - 16 - len(indent))
    for i in range(0, len(url), chunk):
        lines.append(Text(_PAD + indent + "  " + url[i : i + chunk], style=url_style, justify="left"))
    return lines


def _build_generate_confirm_screen(
    *,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    subtitle: str = "",
) -> Panel:
    """Confirmation screen shown between team/board analysis and ticket generation.

    Separates the two concerns: the user has just analysed the team/board and is
    now explicitly asked whether they want yeaboi to draft a sample epic/stories/
    tasks/sprint (which runs the LLM) — rather than the app assuming they do.

    Delegates to ``_build_analysis_review_screen`` so the layout (title, progress
    dots, viewport, action buttons) stays identical to the rest of analysis mode.
    """
    c_label = "bold white"
    c_body = "rgb(180,180,200)"
    c_bullet = "rgb(100,180,100)"
    c_muted = "rgb(120,120,140)"

    def _bullet(text: str) -> Text:
        t = Text(_PAD + "  ", justify="left")
        t.append("• ", style=c_bullet)
        t.append(text, style=c_body)
        return t

    # The question leads the body so it stays above the fold even on short
    # terminals, where the viewport shows only a few rows. The action buttons
    # below are always visible; the explanation and bullets follow.
    body_lines: list = [
        Text(""),
        Text(
            _PAD + "Analysis complete — generate sample tickets now?",
            style=c_label,
            justify="left",
        ),
        Text(""),
        Text(
            _PAD + "yeaboi can draft a sample set, calibrated to these patterns:",
            style=c_body,
            justify="left",
        ),
        _bullet("a sample epic"),
        _bullet("sample user stories"),
        _bullet("sample tasks"),
        _bullet("a sample sprint plan"),
        Text(""),
        Text(
            _PAD + "This runs the LLM. You can edit, regenerate, or export each step.",
            style=c_muted,
            justify="left",
        ),
    ]

    return _build_analysis_review_screen(
        body_lines,
        stage_index=0,
        width=width,
        height=height,
        action_sel=action_sel,
        actions=["Generate tickets", "Not now"],
        subtitle=subtitle,
    )


def _build_team_insights_screen(
    profile,
    *,
    examples: dict | None = None,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    subtitle: str = "",
    shimmer_tick: float | None = None,
) -> Panel:
    """The Work items page — the second half of the analysis pager.

    The work items drafted from the analysis: the offer to draft them, or the
    note that a plan is waiting. It used to open with the coaching plan, but
    that is a section OF the analysis with a tab of its own, so this page was
    drawing the other page's content and had no subject left of its own.

    Delegates to ``_build_analysis_review_screen`` so the layout (title,
    viewport, scrollbar, action buttons) stays identical to the rest of
    analysis mode.
    """
    # No leading blank: the tab rule directly above is the page's own header
    # edge, and a spacer under it left the heading floating between the two.
    # What the separate confirm gate used to ask, folded in. It was one decision
    # across two screens — this page's Continue only led to a page asking whether
    # you meant it — so the second Continue is gone and this one runs it.
    #
    # There is no second state for "already made", because this page is not
    # drawn once a plan exists: crossing to Work items IS the plan by then, and
    # a page whose only content was that the thing behind it was ready is a gate
    # in front of what you asked for.
    body_lines: list = [
        Text(_PAD + "Generate sample tickets from this?", style="bold white", justify="left"),
        Text(
            _PAD + "yeaboi can draft a sample set calibrated to these patterns: an epic, its user",
            style="rgb(120,120,140)",
            justify="left",
        ),
        Text(
            _PAD + "stories, their tasks and a sprint plan. This runs the LLM; you can edit,",
            style="rgb(120,120,140)",
            justify="left",
        ),
        Text(
            _PAD + "regenerate or export each one.",
            style="rgb(120,120,140)",
            justify="left",
        ),
    ]

    # Built from the profile, not from ``subtitle``: that argument carries this
    # loop's transient status ("Team profile exported"), so hanging the crumb on
    # it left the title with nothing beside it whenever nothing had happened yet.
    _src = getattr(profile, "source", "") or ""
    _key = getattr(profile, "project_key", "") or ""
    _bits = [f"{_src}/{_key}".strip("/")] if (_src or _key) else []
    for _attr, _what in (("sample_sprints", "sprints"), ("sample_stories", "stories")):
        _n = getattr(profile, _attr, 0)
        if _n:
            _bits.append(f"{_n} {_what}")
    # Named for THIS page, not for the mode. The scope after it is the same on
    # both — same team, same window — but a crumb that opens with the previous
    # page's name is telling you where you were, not where you are.
    _crumb = "  ·  ".join([ANALYSIS_PAGES[1], *_bits])

    panel = _build_analysis_review_screen(
        body_lines,
        stage_index=0,
        scroll_offset=scroll_offset,
        scroll_meta=scroll_meta,
        width=width,
        height=height,
        action_sel=action_sel,
        # No Back: the chrome's own back tab covers leaving, and a "Back" tab
        # sitting immediately beside it offered the same thing twice.
        actions=["Continue", "Export"],
        crumb=_crumb,
        show_stages=False,
        # In the flow, so its bar runs down the flow's column like every other
        # page's — the pager's split is on that line.
        column_body=True,
        shimmer_tick=shimmer_tick,
    )
    # The other half of the results page's pager — same two names, this one live,
    # and split over the same column so it does not move when you cross.
    panel._pager = analysis_pager(1)
    panel._pager_divider_x = analysis_divider_x(width)
    return panel


# Actions that act ON the report rather than navigating it. They ride in the
# chrome's left strip; the driver reads the same tuple so its click hit-testing
# cannot drift from what was drawn.
# Short names for the section tab bar. A card's own title reads as a heading
# ("Velocity & Sprints"); eleven of those on one row wrapped at any width a
# terminal actually has. The heading still leads the section it opens, so the tab
# only has to be the shortest thing that names it.
_TA_TAB_LABELS: dict[str, str] = {
    "velocity": "Velocity",
    "team": "Team",
    "estimation": "Estimates",
    "workflow": "Workflow",
    "writing": "Writing",
    "trends": "Trends",
    "recommendations": "Actions",
    "ai-adoption": "AI Usage",
    "code-health": "Code",
    "documentation": "Docs",
    "insights": "Insights",
}

# The two ways off this page and onto a different analysis. Named here, beside
# the strip that draws them, so the loop offering them and the page rendering
# them cannot disagree about what they are called — an action this page doesn't
# recognise falls through to the body and gets drawn a second time as a button
# floating above the chrome.
ANALYSIS_NAV_ACTIONS: tuple[str, ...] = ("Switch analysis", "New analysis", "Settings")

RESULTS_TAB_ACTIONS: tuple[str, ...] = (
    "Export",
    "Share Online",
    "Anonymize",
    "Adjust",
    "Revert",
    "Retry failed",
    *ANALYSIS_NAV_ACTIONS,
)


# Where the results tab underline is, so it can travel to where it belongs.
# Module state for the same reason the setup rule's is (see _rule_slide): the
# builder is called once per frame and has nowhere else to keep it.
# Under this the band stacks instead of splitting — half the page cannot hold
# the widest stat value without wrapping it mid-row.
_BAND_TWO_COL_MIN_W = 118
_TAB_SLIDE_SECS = 0.2
_tab_slide: dict = {"to": None, "at": 0.0, "from": 0.0, "drawn": 0.0}


def reset_section_tabs() -> None:
    """Forget where the underline was, so the next page draws it in place."""
    _tab_slide.update({"to": None, "at": 0.0, "from": 0.0, "drawn": 0.0})


def _fit_section_tabs(labels: list[str], keys: list[str], at: int, strip_w: int) -> tuple[list, list, int]:
    """Window the section strip down to what fits, centred on the live tab.

    Returns the labels, their keys and the live tab's new index. An "…" at
    either end stands for the tabs beyond it and carries the key of the first
    one hidden there, so it steps the window instead of being a dead mark.
    """
    if len(labels) <= 1:
        return labels, keys, at

    def _fits(items: list[str]) -> bool:
        return sum(len(x) + _TAB_GAP for x in items) - _TAB_GAP <= strip_w

    if _fits(labels):
        return labels, keys, at

    lo = hi = at  # grow outward from the live tab, right first so it reads forward
    while True:
        grew = False
        for nxt in (hi + 1, lo - 1):
            if not (0 <= nxt < len(labels)):
                continue
            _lo, _hi = min(lo, nxt), max(hi, nxt)
            trial = labels[_lo : _hi + 1]
            # Room for the ellipsis that will stand for whatever is left over.
            trial = (["…"] if _lo > 0 else []) + trial + (["…"] if _hi < len(labels) - 1 else [])
            if _fits(trial):
                lo, hi, grew = _lo, _hi, True
        if not grew:
            break

    out_labels = labels[lo : hi + 1]
    out_keys = keys[lo : hi + 1]
    shift = 0
    if lo > 0:
        out_labels, out_keys, shift = ["…", *out_labels], [keys[lo - 1], *out_keys], 1
    if hi < len(labels) - 1:
        out_labels, out_keys = [*out_labels, "…"], [*out_keys, keys[hi + 1]]
    return out_labels, out_keys, at - lo + shift


def _section_tab_pos(active: int, count: int) -> float:
    """The underline's current fractional tab position, easing toward *active*.

    Starts from where it was last DRAWN rather than the previous tab's slot, so
    changing your mind mid-slide carries on from where the bar actually is.
    """
    if count <= 0:
        return 0.0
    now = time.monotonic()
    if _tab_slide["to"] != active:
        _tab_slide["from"] = float(active) if _tab_slide["to"] is None else _tab_slide["drawn"]
        _tab_slide["to"] = active
        _tab_slide["at"] = now
    progress = (now - _tab_slide["at"]) / _TAB_SLIDE_SECS
    if progress >= 1.0:
        _tab_slide["drawn"] = float(active)
    else:
        start = _tab_slide["from"]
        _tab_slide["drawn"] = start + (active - start) * ease_out_cubic(max(0.0, progress))
    return _tab_slide["drawn"]


_preview_slide: dict = {"to": None, "at": 0.0, "from": 0.0, "drawn": 0.0}


def reset_preview_tabs() -> None:
    """Forget where the preview underline was, so the next page draws it in place."""
    _preview_slide.update({"to": None, "at": 0.0, "from": 0.0, "drawn": 0.0})


def _preview_tab_pos(active: int, count: int) -> float:
    """The preview underline's fractional position, easing toward *active*."""
    if count <= 0:
        return 0.0
    now = time.monotonic()
    if _preview_slide["to"] != active:
        _preview_slide["from"] = float(active) if _preview_slide["to"] is None else _preview_slide["drawn"]
        _preview_slide["to"] = active
        _preview_slide["at"] = now
    progress = (now - _preview_slide["at"]) / _TAB_SLIDE_SECS
    if progress >= 1.0:
        _preview_slide["drawn"] = float(active)
    else:
        start = _preview_slide["from"]
        _preview_slide["drawn"] = start + (active - start) * ease_out_cubic(max(0.0, progress))
    return _preview_slide["drawn"]


def section_tab_click(panel, x: int, y: int) -> str | None:
    """Which section tab a click landed on, or None.

    The tab bar is the results page's primary navigation, so a click is tested
    against it before the body buttons — the same order the eye reads them in.
    """
    for y0, y1, x0, x1, key in getattr(panel, "_section_tabs", ()):
        if x0 <= x <= x1 and y0 <= y <= y1:
            return key
    return None


def results_body_actions(actions: list[str]) -> list[str]:
    """The subset of *actions* still drawn as buttons in the body."""
    return [a for a in actions if a not in RESULTS_TAB_ACTIONS and a != "Continue"]


def _build_team_analysis_screen(
    profile,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    export_sel: int = 0,
    examples: dict | None = None,
    sprint_names: list[str] | None = None,
    team_name: str = "",
    view: str = "overview",
    selected_card: int = 0,
    actions: list[str] | None = None,
    shimmer_tick: float | None = None,
    anon_note: str = "",
    source_toggle: list[str] | None = None,
    active_source: str = "",
    comparison: list | None = None,
    source: str = "",
    project_key: str = "",
    code_signal=None,
    code_examples: dict | None = None,
    doc_signal=None,
    doc_examples: dict | None = None,
    analysis_features: list[str] | None = None,
) -> Panel:
    """Build the team analysis results screen (overview + section cards).

    ``view`` is ``"overview"`` (headline stats, AI executive summary and the
    selectable section-card list) or a ``_TA_CARDS`` key (a focused section
    detail view with its AI "What this means" narrative and jargon glossary).
    Section rendering lives in ``_analysis_sections.py``.

    In 'both' mode ``source_toggle`` (ordered tracker keys) renders a
    ``[ Jira ] Azure DevOps`` switch line under the header and ``comparison``
    (side-by-side headline rows) is shown atop the overview — the two trackers'
    figures stay clearly separate, never blended.
    """
    from yeaboi.tools.team_learning import compute_headline_stats

    # A delivery-off run (docs-only / code-only) has no TeamProfile; fall back to the
    # caller-supplied source/project and describe which components ran in the header.
    if profile is not None:
        src = profile.source
        key = profile.project_key
        sprints = profile.sample_sprints
        stories = profile.sample_stories
    else:
        src = source
        key = project_key

    # Build header: show team name for AzDO, board name for Jira
    board_label = key
    if team_name:
        board_label = f"{team_name} ({key})"
    if profile is not None:
        header_str = f"Team Analysis  ·  {src}/{board_label}  ·  {sprints} sprints  ·  {stories} stories"
    else:
        _ex = examples or {}
        bits = []
        if code_examples or _ex.get("ai_adoption"):
            enabled = set((code_examples or {}).get("enabled_features", ()))
            if "ai_footprint" in enabled:
                bits.append("AI footprint")
            if "code_health" in enabled:
                bits.append("code health")
            if not enabled:
                bits.append("code scan")
        if doc_signal is not None or _ex.get("doc_quality"):
            bits.append("docs")
        header_str = f"Team Analysis  ·  {src}/{board_label}  ·  {' + '.join(bits) or 'components'} only"

    # 'Both'-mode source toggle line: highlight the active tracker.
    toggle_line: Text | None = None
    if source_toggle and len(source_toggle) > 1:
        _labels = {"jira": "Jira", "azdevops": "Azure DevOps"}
        toggle_line = Text(_PAD, justify="left")
        for i, s in enumerate(source_toggle):
            if i > 0:
                toggle_line.append("   ")
            lbl = _labels.get(s, s)
            if s == active_source:
                toggle_line.append(f"[ {lbl} ]", style="bold #22c55e")
            else:
                toggle_line.append(f"  {lbl}  ", style="dim")
        toggle_line.append("    (Tab: switch source)", style="rgb(90,90,110)")

    from yeaboi.ui.mode_select.screens._analysis_sections import visible_card_order

    stats = compute_headline_stats(profile, examples) if profile is not None else {}
    ctx = _TaCtx(width, examples, sprint_names=sprint_names, stats=stats)
    ctx.comparison = comparison
    # Code/Docs are GLOBAL scans passed in from the top-level result — feed them so
    # the two cards render regardless of the active delivery tracker. When viewing a
    # stored profile (no top-level signals) they come off the profile itself, where
    # the global scan was persisted.
    ctx.ai_sig = code_signal
    ctx.doc_sig = doc_signal
    ctx.code_blob = code_examples or {}
    ctx.doc_blob = doc_examples or {}
    ctx.analysis_features = tuple(analysis_features or ())
    _prof_ai = getattr(profile, "ai_adoption", None)
    _prof_doc = getattr(profile, "doc_quality", None)
    has_code = code_signal is not None or bool(_prof_ai and (_prof_ai.scanned_commits + _prof_ai.scanned_prs) > 0)
    has_code_health = bool(
        (code_examples or {}).get("repository_health")
        or (examples or {}).get("ai_adoption", {}).get("repository_health")
        or (code_examples is not None and "code_health" in set(analysis_features or ()))
    )
    has_docs = doc_signal is not None or bool(_prof_doc and _prof_doc.pages_scanned > 0)
    ctx.visible_order = visible_card_order(
        profile,
        has_code,
        has_docs,
        has_code_health=has_code_health,
        analysis_features=analysis_features,
    )

    # The headline band — At a Glance beside the AI summary — sits ABOVE the tab
    # bar, on every section. It is what the whole analysis says in five numbers
    # and a paragraph; hiding it behind a tab made you leave the answer to look
    # at the workings. Built in its own ctx so it never joins the scrolled body.
    _hdr_ctx = _TaCtx(width, examples, sprint_names=sprint_names, stats=stats)
    _hdr_ctx.comparison = comparison
    _hdr_ctx.visible_order = ctx.visible_order
    # Spread the stats across their half of the band rather than huddling them at
    # its far left with the summary a screen away. Measured in a second pass: how
    # far the labels can move is set by the LONGEST VALUE, and a guessed reserve
    # wrapped "90% of committed scope delivered" out of its own column.
    _col_w = (width - 6) // 2
    # The summary is the one thing built at one width and laid out at another.
    # Tell it which, or it wraps to the page and the half-width cell it lands in
    # wraps every line a second time.
    _summary_w = _col_w if width >= _BAND_TWO_COL_MIN_W else 0
    _hdr_ctx.summary_w = _summary_w
    _ta_overview(_hdr_ctx, profile, 0)
    _widest = max((row.cell_len for row in _hdr_ctx.lines if isinstance(row, Text)), default=_col_w)
    _slack = _col_w - _widest - 1
    if _slack > 0:
        _hdr_ctx = _TaCtx(width, examples, sprint_names=sprint_names, stats=stats)
        _hdr_ctx.comparison = comparison
        _hdr_ctx.visible_order = ctx.visible_order
        _hdr_ctx.kv_w = 24 + _slack
        _hdr_ctx.summary_w = _summary_w
        _ta_overview(_hdr_ctx, profile, 0)
    _at = _hdr_ctx.summary_at
    _glance = _hdr_ctx.lines[:_at] if _at is not None else _hdr_ctx.lines
    _summary = _hdr_ctx.lines[_at:] if _at is not None else []
    if view == "overview":
        view = ctx.visible_order[0] if ctx.visible_order else "overview"
    # Everything under the tabs belongs to the left column when the band is
    # split, because the right one is the summary's — and the summary can be
    # long. The strip is already capped there; a body that ran the full width
    # under it read as overrunning its own tabs, and would have run under the
    # summary the moment there was one worth reading.
    if _glance and _summary and width >= _BAND_TWO_COL_MIN_W:
        # The reserve is whatever puts the scrollbar ON the flow's column: the
        # bar is the last cell of the body, four in from the reserve's edge.
        # Derived rather than chosen, so the bar and the pager's split cannot
        # drift apart when one of them moves.
        ctx.right_pad = max(0, width - analysis_divider_x(width) - 4)
        ctx.width = max(20, width - 6 - ctx.right_pad)

    if view == "overview":
        crumb_text = ""
        _actions = actions or ["Export", "Continue"]
    else:
        card = _TA_CARDS[view]
        # The tab bar says which section you are in, and its own heading repeats
        # the full title — a breadcrumb between the two said it a third time.
        crumb_text = ""
        _ta_narrative_block(ctx, view)
        _before = len(ctx.lines)
        for build_section in card["builders"]:
            build_section(ctx, profile)
        # Say so rather than showing nothing. A section builder emits nothing at
        # all when the run had no data for it (no repos scanned, nothing
        # flagged) — as a page you had opened, that read as "still loading"; as a
        # tab you can land on by pressing →, it reads as broken.
        if len(ctx.lines) == _before:
            ctx.heading(card["title"])
            ctx.add(
                Text(
                    PAD + "Nothing to show here — this analysis produced no data for this section.",
                    style=ANALYSIS_THEME.dim,
                )
            )
        _ta_glossary_lines(ctx, card["glossary"])
        _actions = actions or ["Export", "Continue"]

    # ── Layout matching planning mode ──────────────────────────────────
    from yeaboi.ui.shared._components import analysis_title

    title = analysis_title(shimmer_tick)

    # Most of this page's actions move into the chrome: three of them act on the
    # report rather than navigating it, and a row of five buttons under the
    # content had the actions competing with the thing they act on. Export /
    # Share / Anonymize join the back tab in the bottom-left strip; Continue
    # takes the forward slot beside the music pocket, where "next" lives on
    # every other page. What is left in the body is the one that moves you.
    # The sections are a tab bar: each shows different content, so choosing one
    # and opening it were the same intent expressed twice.
    _two_col_band = bool(_glance and _summary and width >= _BAND_TWO_COL_MIN_W)
    _tab_keys = list(ctx.visible_order)
    _tab_labels = [_TA_TAB_LABELS.get(k, _TA_CARDS[k]["title"]) for k in _tab_keys]
    _tab_at = _tab_keys.index(view) if view in _tab_keys else 0
    # The strip only has half the page, and eight labels do not fit in it on a
    # small terminal — they crush together and then overflow the rule. Show a
    # window of them around the one you are on, with "…" standing for the rest.
    # The ellipsis carries the key of the tab just outside the window, so it
    # steps the window rather than being a mark you cannot use.
    _strip_w = (ctx.width if ctx.right_pad else width - 6) - _TAB_INDENT
    _tab_labels, _tab_keys, _tab_at = _fit_section_tabs(_tab_labels, _tab_keys, _tab_at, _strip_w)
    _tab_lines, _tab_spans = _settings_tab_bar(
        _tab_labels,
        _tab_at,
        ANALYSIS_THEME,
        width,
        pos=_section_tab_pos(_tab_at, len(_tab_keys)),
        taper=False,
        # Only as far as the summary starts: run the bar the whole way and a
        # summary that needs a second line has nowhere to put it but under a
        # rule that belongs to something else.
        spread=(width - 6) // 2 if _two_col_band else True,
    )
    _body_actions = results_body_actions(_actions)
    _tabbed = [a for a in _actions if a in RESULTS_TAB_ACTIONS]
    _forward = "Continue" if "Continue" in _actions else ""
    btn_top, btn_mid, btn_bot = build_action_buttons(_body_actions, export_sel)
    if anon_note:  # anonymized: the crumb line carries the "N masked — review" indicator
        crumb_text = anon_note

    # The 'both'-mode toggle line adds one header row; shrink the viewport to match.
    _header_items = [Text(""), _title_with_crumb(title, header_str), Text("")]
    if crumb_text:
        _header_items.append(Text(_PAD + crumb_text, style="rgb(120,120,140)", justify="left"))
    if toggle_line is not None:
        _header_items.append(toggle_line)
    if _two_col_band:
        _band = Table.grid(padding=(0, 0), expand=True)
        _band.add_column(ratio=1)
        _band.add_column(ratio=1)
        _band.add_row(Group(*_glance), Group(*_summary))
        _header_items.append(_band)
    elif _glance:
        # Too narrow to split: half of it cannot hold "90% of committed scope
        # delivered", and a value that wraps mid-row is worse than a tall band.
        _header_items.extend(_glance)
        _header_items.extend(_summary)
    # Measured, not counted: the headline band's height depends on how far the
    # summary wrapped, and a hand-counted header silently ate the bottom of every
    # section (the velocity card's glossary went first).
    _head = _rendered_height(Group(*_header_items, Text(""), Text(""), Text("")), width - 6)
    body_h = calc_viewport(height, header_h=_head, action_h=4)

    # Scroll by renderable rather than pretending every item is one terminal row.
    # Dashboard tiles/tables/cards are atomic Rich renderables with measured heights.
    # Choose the earliest trailing item that still lets the bottom of the report fit.
    tail_h = 0
    max_scroll = max(0, len(ctx.lines) - 1)
    for i in range(len(ctx.lines) - 1, -1, -1):
        ih = ctx.item_heights[i] if i < len(ctx.item_heights) else 1
        if tail_h and tail_h + ih > body_h:
            break
        tail_h += ih
        max_scroll = i
    # (The overview used to keep a selected card row in view. There is no card
    # list to keep in view now — the tab bar is the selection.)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, body_h)

    _vis_items: list = []
    _vis_h = 0
    for i in range(actual_scroll, len(ctx.lines)):
        ih = ctx.item_heights[i] if i < len(ctx.item_heights) else 1
        # Always take the first one, even if it is taller than the viewport: an
        # item is a whole card or table, and dropping it for not fitting left
        # the section blank on a short terminal rather than showing its top.
        if _vis_items and _vis_h + ih > body_h:
            break
        _vis_items.append(ctx.lines[i])
        _vis_h += ih

    remaining = max(0, body_h - _vis_h)
    # Rendered ROWS, not the number of items. build_scrollbar compares its total
    # against the viewport's height, so handing it a count of cards said eight
    # things fit in twenty-five rows — no bar, on a section that was being cut
    # off. Items here are whole cards and tables; one of them is many rows.
    _total_rows = sum(ctx.item_heights[i] if i < len(ctx.item_heights) else 1 for i in range(len(ctx.lines)))
    _sb_text = build_scrollbar(body_h, _total_rows, actual_scroll, max_scroll)

    # Build viewport with optional scrollbar
    _body_group = Group(*_vis_items, *[Text("") for _ in range(remaining)])
    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(_body_group, _sb_text)
        # Beside the content it scrolls, not at the far edge of the page. With
        # the body held to the left column, a bar on the page's right border sat
        # past the summary with nothing under it — present, and nowhere you
        # would look for it. Padding rather than a third column, so the body
        # keeps the same width it was measured at.
        viewport_renderable = Padding(_vp_table, (0, ctx.right_pad, 0, 0)) if ctx.right_pad else _vp_table
    elif ctx.right_pad:
        viewport_renderable = Padding(_body_group, (0, ctx.right_pad, 0, 0))
    else:
        viewport_renderable = _body_group

    content = Group(
        *_header_items,
        Text(""),
        *_tab_lines,
        viewport_renderable,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )

    # The neutral base, like every other page. This was the one screen still
    # tinting its whole background with the green card colour, which made the
    # results read as a different application from the setup flow that leads into
    # it — the same reason the setup review dropped the tint.
    panel = build_page_panel(content, theme=ANALYSIS_THEME, height=height)
    # Tables and meters run to width-7 — no free margin, so the duck's shared
    # bubble is suppressed here (he still bobs and quacks).
    panel._bubble_room = 0
    # "act:<name>" rather than a letter: a click on a tab comes back as the key it
    # carries, and the loop turns that straight into the action it already has a
    # branch for — no second dispatch table to drift, and no letters to collide
    # with the page's own keys.
    # The two halves of the bottom-centre pager: this page, and the one Continue
    # leads to. The PAGE names them — the chrome only draws it and reports which
    # half was clicked.
    panel._pager = analysis_pager(0)
    # Split the pill over the page's own divider — the right edge of the body
    # column, which is the column its scrollbar runs down. Measured against a
    # render rather than derived from the padding constants, and published only
    # when the body IS a column: with no reserve there is no such line, and the
    # pill goes back to the centre of the page.
    if ctx.right_pad:
        panel._pager_divider_x = analysis_divider_x(width)
    panel._page_tabs = [(name, f"act:{name}") for name in _actions]
    # (labels row, underline row, x0, x1, section key) so a click on a tab opens
    # that section — the same rows the settings page publishes, plus the key.
    # Rendered rows, not list entries: the wordmark is one entry and two rows,
    # and the toggle line and crumb come and go. Measured, so a header that grows
    # a row can never leave the click targets on the row above the tabs.
    _tab_top = 3 + _rendered_height(Group(*_header_items, Text("")), width - 6)
    panel._section_tabs = [
        (_tab_top, _tab_top + 1, _TAB_COL_OFFSET + a, _TAB_COL_OFFSET + b - 1, key)
        for (a, b), key in zip(_tab_spans, _tab_keys, strict=True)
    ]
    if _forward:
        panel._forward_action = _forward

    return panel


# Component picker — order + friendly labels. Each component runs over its OWN
# sub-sources (a ragged grid: different columns per row). ``_COMPONENT_LABELS`` keeps
# the "Name — description" form for back-compat; the picker splits it on the em dash.
_COMPONENT_KEYS: tuple[str, ...] = ("delivery", "code", "docs")
_COMPONENT_NAMES: dict[str, str] = {"delivery": "Delivery", "code": "Code", "docs": "Docs"}
_COMPONENT_DESCS: dict[str, str] = {
    "delivery": "velocity, calibration, contributors",
    "code": "AI footprint + repository health",
    "docs": "clarity + usefulness + ownership",
}
_COMPONENT_LABELS: dict[str, str] = {k: f"{_COMPONENT_NAMES[k]} — {_COMPONENT_DESCS[k]}" for k in _COMPONENT_KEYS}
_SUBSOURCE_TITLES: dict[str, str] = {
    "jira": "Jira",
    "azdevops": "Azure DevOps",
    "azuredevops": "Azure DevOps",  # reporting's canonical spelling of the same tracker
    "github": "GitHub",
    "azdo": "Azure Repos",
    "confluence": "Confluence",
    "notion": "Notion",
}
_ANALYSIS_FEATURE_KEYS: tuple[str, ...] = (
    "delivery",
    "ai_footprint",
    "code_health",
    "documentation",
)
_ANALYSIS_FEATURE_LABELS: dict[str, tuple[str, str]] = {
    "delivery": ("Delivery", "velocity, estimation, workflow, and team patterns"),
    "ai_footprint": ("AI footprint", "detectable AI markers in selected-user commits and PRs"),
    "code_health": ("Code health", "health of files changed by the selected users"),
    "documentation": ("Documentation", "clarity, usefulness, structure, and ownership"),
}

# Elevated-surface background for the analysis setup/review cards and the whole
# analysis results viewport (see _build_team_analysis_screen) — a touch lighter
# than ANALYSIS_THEME.bg so cards read as raised above the page tint. Page-wide
# backgrounds now come from Theme.bg via build_page_panel.
_ANALYSIS_CARD_BG_RGB = "rgb(13,31,27)"
_ANALYSIS_CARD_BG = f"on {_ANALYSIS_CARD_BG_RGB}"


def _analysis_toggle_row(
    title: str,
    description: str,
    *,
    focused: bool,
    selected: bool = False,
    enabled: bool = True,
    note: str = "",
    theme=None,
    label_w: int = 0,
) -> Text:
    """The shared setup-wizard option row (``‹ ● Title ›  ·  hint``).

    Defaults to the Analysis look; ``theme`` re-brands it for other modes'
    setup screens (Reporting passes REPORTING_THEME).

    ``label_w`` is the widest title in the group. Pass it and every row's ``·``
    lands in the same column; leave it 0 and the separator tracks each title's
    own length, which reads as ragged down a list. Callers know their siblings,
    a single row does not, so it has to come in from outside.
    """
    theme = theme or ANALYSIS_THEME
    # Flush with the section header, no caret and no separator dot: the bullet
    # already says selected, weight already says focused, and the columns already
    # separate label from description — the extra glyphs were decoration that
    # pushed the whole list in from the page edge.
    row = Text(_PAD, justify="left", overflow="ellipsis", no_wrap=True)
    row.append(
        "●" if selected and enabled else "○",
        style=theme.accent_bright if selected and enabled else theme.dim,
    )
    row.append(
        f" {title}",
        style="bold white" if focused and enabled else theme.accent if selected and enabled else theme.desc,
    )
    row.append(" " * max(0, label_w - len(title)))
    detail = "Unavailable" if not enabled else note or description
    if detail:
        row.append(f"   {detail}", style=theme.dim if not enabled else theme.muted)
    return row


def _analysis_toggle_viewport(
    rows: list[Text],
    cursor: int,
    *,
    height: int,
    header_h: int = 11,
) -> object:
    """Window long toggle lists using the same one-column list treatment."""
    visible_h = max(1, height - header_h)
    total = len(rows)
    max_start = max(0, total - visible_h)
    start = max(0, min(cursor - visible_h // 2, max_start))
    visible = list(rows[start : start + visible_h])
    visible.extend(Text("") for _ in range(max(0, visible_h - len(visible))))
    scrollbar = build_scrollbar(visible_h, total, start, max_start)
    if scrollbar is None:
        return Group(*visible)
    shell = Table.grid(expand=True, padding=0)
    shell.add_column(ratio=1)
    shell.add_column(width=1)
    shell.add_row(Group(*visible), scrollbar)
    return shell


def _analysis_card_grid(
    cards: list[Panel],
    *,
    width: int,
    columns: int | None = None,
) -> Table:
    """Responsive card grid shared by every Analysis setup selector.

    Indented to PAD like every other body element — the grid used to sit hard
    against the panel padding while the header and hints above it were four
    columns further in. Columns are given an explicit width rather than
    ``ratio=1``: Rich hands the division's remainder to the last column, so on an
    odd inner width the right-hand card came out a cell wider than the left.
    """
    ncols = columns or (2 if width >= 88 else 1)
    ncols = max(1, min(ncols, max(1, len(cards))))
    # Panel borders (2) + the page panel's own padding (4) + our PAD either side.
    inner = max(ncols * 8, width - 6 - 2 * len(PAD))
    gap = 1  # Table.grid padding, between columns only
    col_w = max(4, (inner - gap * (ncols - 1)) // ncols)
    grid = Table.grid(padding=(0, gap))
    for _ in range(ncols):
        grid.add_column(width=col_w)
    for start in range(0, len(cards), ncols):
        row = list(cards[start : start + ncols])
        row.extend(Text("") for _ in range(ncols - len(row)))
        grid.add_row(*row)
    return Padding(grid, (0, 0, 0, len(PAD)))


# Depth and Time-window options, at module scope because BOTH the expanded stage
# and its one-line collapsed form draw from them — two copies would be two
# chances for the summary to disagree with the control it summarises.
_ANALYSIS_DEPTH_OPTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "QUICK",
        "Metrics only · fastest",
        "Computed metrics, deterministic summaries and coaching. No LLM wait.",
    ),
    (
        "DEEP",
        "Recommended · exhaustive",
        "Covers every eligible asset and produces evidence-backed actions. Slower.",
    ),
)
_ANALYSIS_WINDOW_OPTIONS: tuple[tuple[int, str], ...] = (
    (30, "Last month"),
    (90, "Last quarter"),
    (120, "Recommended"),
    (365, "Last year"),
)


# The setup steps in the order you meet them, for the breadcrumb trail. Optional
# screens (Azure projects, Model) are absent on purpose — they only appear when
# they actually run, and are appended by name when they do.
_ANALYSIS_SETUP_TRAIL: tuple[str, ...] = ("Areas", "Sources", "Depth", "Time window", "People")


def _analysis_setup_header(
    section: str, help_text: str, *, message: str = "", brand: str = "ANALYSIS SETUP", theme=None
) -> list:
    """Consistent hierarchy for every pre-run setup selector (breadcrumb + hint + ⚠).

    Defaults to the Analysis branding; ``brand``/``theme`` re-brand it for other
    modes' setup screens (Reporting passes "REPORTING SETUP" + REPORTING_THEME).
    """
    theme = theme or ANALYSIS_THEME
    # PAD, not _PAD: this file's _PAD is 2 and the shared PAD is 4, so the header
    # used to sit two columns left of both the wordmark above it and the option
    # rows below — reading as an outdent rather than a heading.
    #
    # The trail shows the whole setup, not just where you are: steps already done
    # read dim, the current one is lit, the rest are muted. Matching on the
    # section name keeps every caller unchanged; a step outside the canonical
    # order (the optional Azure-projects and Model screens) still lights up on
    # its own by falling through to the tail.
    trail = list(_ANALYSIS_SETUP_TRAIL)
    upper = section.upper()
    here = next((i for i, name in enumerate(trail) if name.upper() == upper), None)
    # Only Analysis's own canonical steps get the trail. Reporting borrows this
    # header with its own brand and step names, and an optional screen (Azure
    # projects, Model) has no fixed place in the order — showing either against
    # Analysis's trail would state something untrue, so both keep the plain crumb.
    show_trail = here is not None and brand == "ANALYSIS SETUP"
    crumbs = Text(PAD, justify="left", no_wrap=True, overflow="ellipsis")
    crumbs.append(brand, style=f"bold {theme.accent_bright}")
    crumbs.append("  ›  ", style=theme.dim)
    if show_trail:
        # One line, not two: the stage row already names where you are, so a
        # separate "› REVIEW" crumb above it just said it twice. build_progress_dots
        # is the app's existing stage indicator (the initial setup wizard uses it),
        # so this reads like the rest of the app rather than a second style.
        crumbs.append_text(build_progress_dots(list(trail), here, pad="", theme=theme, mark_done=True))
    else:
        crumbs.append(upper, style=f"bold {theme.accent_bright}")
    out: list = [crumbs, Text(PAD + help_text, style=theme.muted)]
    if message:
        out.extend((Text(PAD + "⚠  " + message, style=theme.warn), Text("")))
    else:
        out.append(Text(""))
    return out


def _analysis_setup_title(width: int, height: int):
    """Use compact branding when the full wordmark would crowd out choices."""
    if height < 28:
        return Text(_PAD + "ANALYSIS", style=f"bold {ANALYSIS_THEME.accent_bright}")
    from yeaboi.ui.shared._components import analysis_title

    return analysis_title(width=width)


# The setup sidebar: every stage and the choice made at it, standing beside the
# stage you are on. The wizard is a sequence of pages, so without it the only
# record of what you picked three screens ago was the trail's on/off dot.
#
# ``_SETUP_SIDEBAR_W`` is the column it takes; below ``_SETUP_SIDEBAR_MIN`` of
# total width the page drops it entirely rather than squeezing the controls,
# which is the half the user is actually operating.
_STACKED_ROSTER_ROWS = 6  # roster rows visible when every set shares the page
_SETUP_SIDEBAR_W = 30
_SETUP_SIDEBAR_MIN = 96
# Rows above the sidebar's first stage line, for click hit-testing. Derived, not
# guessed: panel border + top padding (2), a blank, the title (2 rows of wordmark,
# or 1 when the compact branding kicks in under height 28), a blank, the header
# block, then the sidebar's own top border. A unit test pins it against a real
# render so it cannot drift when the header gains a row.
_SETUP_SIDEBAR_CHROME = 2 + 1 + 1 + 1 + 1  # border+pad, blank, blank, sidebar border, first line
# Trail name → the wizard step key the sequencer indexes by (see _WIZARD_STEPS).
SETUP_STAGE_STEPS: dict[str, str] = {
    "Areas": "features",
    "Sources": "sources",
    "Depth": "depth",
    "Time window": "window",
    "People": "members",
}


def _rendered_height(renderable, width: int) -> int:
    """How many terminal rows *renderable* actually occupies at *width*.

    The active stage's control block is a Group whose height depends on a
    viewport, a wrapped description or a divider — there is no counting it by
    eye, and every collapsed set below it is positioned from this number.
    """
    from rich.console import Console as _Console

    probe = _Console(width=max(10, width), height=200, force_terminal=False)
    return len(probe.render_lines(renderable, probe.options.update_width(max(10, width)), pad=False))


# Rows the active set's heading occupies in each face. The two-row block face is
# the app's own display type (render_ascii_text — the same one every mode title is
# set in); a character grid has no half-step between it and body text, so this is
# the only "h2" available. It is worth two rows only when there are rows to spare.
_STAGE_HEADING_TALL_MIN_H = 34


def _analysis_stage_heading(stage: str, theme, *, tall: bool) -> tuple[object, int]:
    """The lit name of the set being configured, and how many rows it takes.

    ``tall`` sets it in the two-row display face, which is what gives the page a
    middle tier between the wordmark and body text. Short terminals get the
    one-row form with a caret instead — the hierarchy is worth less than the rows.
    """
    if not tall:
        row = Text(PAD[:-2], justify="left", no_wrap=True, overflow="ellipsis")
        row.append("▸ ", style=theme.accent_bright)
        row.append(stage, style=f"bold {theme.accent_bright}")
        return row, 1
    top, bottom = render_ascii_text(stage)
    art = Text(justify="left", no_wrap=True, overflow="ellipsis")
    art.append(PAD + top + "\n", style=f"bold {theme.accent_bright}")
    art.append(PAD + bottom, style=f"bold {theme.accent_bright}")
    return art, 2


def _with_live(state: dict | None, **live) -> dict | None:
    """Overlay a stage's LIVE working values onto the wizard snapshot.

    ``state`` comes from the wizard and only carries what has been *committed* —
    a value is written when you press Enter on its stage. The stage you are
    standing on is still being edited, so its committed value is one step stale.
    Every builder overlays its own in-progress selection (and its cursor) here,
    which is what lets one renderer draw the focused set and the unfocused ones
    from the same dict.
    """
    if state is None:
        return None
    return {**state, **live}


def _sources_selected(state: dict) -> dict[str, set[str]]:
    """Component → the sub-sources selected under it, by NAME.

    ``state["components"]`` is the wizard's ``{component: [sub-source names]}``,
    while the picker works in indices — comparing the two directly silently
    matched nothing, so every source read as unselected away from its own stage.
    A component missing from the dict has not been visited yet, and the picker
    starts everything checked, so absent means all rather than none.
    """
    grid = state.get("grid") or {}
    picked = state.get("components")
    out: dict[str, set[str]] = {}
    for ckey, subs in grid.items():
        if not subs:
            continue
        chosen = None if not picked else picked.get(ckey)
        out[ckey] = set(subs) if chosen is None else {s for s in subs if s in set(chosen)}
    return out


def _analysis_stage_line(stage: str, state: dict, theme) -> Text:
    """A config set collapsed to ONE line that still shows every option.

    Not a value summary: the point of putting all the sets on one page is that
    you can see what each of them is set to without going there, so the line
    carries the same ● / ○ marks the expanded stage does. A stage you have not
    reached shows its options unset rather than a dash — "nothing chosen yet"
    and "chose nothing" look different that way.
    """
    row = Text(PAD, justify="left", no_wrap=True, overflow="ellipsis")
    row.append(f"{stage:<14}", style=theme.muted)

    def _opt(label: str, on: bool, enabled: bool = True) -> None:
        if len(row) > len(PAD) + 14:
            row.append("   ")
        row.append("● " if on else "○ ", style=theme.accent if on else theme.dim)
        row.append(label, style=theme.desc if enabled else theme.dim)

    if stage == "Areas":
        available = state.get("available") or {}
        checked = state.get("features") or set()
        for key in _ANALYSIS_FEATURE_KEYS:
            _opt(_ANALYSIS_FEATURE_LABELS[key][0], key in checked, bool(available.get(key)))
    elif stage == "Sources":
        picked = _sources_selected(state)
        for name, subs in (state.get("grid") or {}).items():
            for sub in subs:
                _opt(_SUBSOURCE_TITLES.get(sub, sub), sub in picked.get(name, set()))
    elif stage == "Depth":
        for i, (name, _label, _detail) in enumerate(_ANALYSIS_DEPTH_OPTIONS):
            _opt(name.title(), i == state.get("depth", 1))
    elif stage == "Time window":
        for i, (days, _label) in enumerate(_ANALYSIS_WINDOW_OPTIONS):
            _opt(f"{days}d", i == state.get("window", 2))
    elif stage == "People":
        roster = state.get("roster") or []
        picked = state.get("members")
        if not roster:
            row.append("everyone on the board", style=theme.dim)
        elif picked is None or len(picked) == len(roster):
            row.append(f"all {len(roster)} members", style=theme.desc)
        else:
            row.append(f"{len(picked)} of {len(roster)} members", style=theme.desc)
    return row


# ── Side-by-side setup: every config set as its own column ──────────────────
# Stacked down the page, five sets separated by blank rows read as one long
# ribbon whichever way the gaps are tuned — the eye has nothing but whitespace
# to group by. Given the width, columns do the grouping structurally: the
# breadcrumb trail becomes the header row, and each set's options hang under
# their own crumb.
_SETUP_COL_MIN_W = 16  # " ● Documentation" — the widest option in the wizard
_SETUP_COL_MAX_W = 24  # a long roster name ellipsises rather than starving the rest
_SETUP_COL_GAP = 3  # the air between columns, counted into the span geometry
# An unfocused column is REFERENCE, not a control: you read it to remember what
# you set, and the rest of the time it should be nearly page. theme.muted/dim are
# tuned for a page whose foreground competes with nothing, and six of them side
# by side made five sets shout as loudly as the one being edited. These sit just
# far enough above rgb(16,16,20) to be legible when looked at directly.
_OFF_LABEL_ON = "rgb(66,72,66)"  # a value that is set
_OFF_LABEL_OFF = "rgb(42,44,50)"  # one that is not
_OFF_MARK_ON = "rgb(48,86,54)"  # its bullet, dark green so "set" still reads
_OFF_MARK_OFF = "rgb(36,38,44)"
_OFF_HEADING = "rgb(52,56,62)"  # a source group's name
_OFF_CRUMB_DONE = "rgb(52,94,60)"  # a set already passed
_OFF_CRUMB_TODO = "rgb(46,48,54)"  # one not reached
# Below this the six columns cannot each hold their own crumb without
# truncating it, so the page keeps stacking. DERIVED, not chosen: a guessed
# threshold that let the columns overflow put every click region a column off
# while the page still looked plausible.
_SETUP_COLUMNS_MIN_W = (
    sum(max(_SETUP_COL_MIN_W, len(name) + 3) for name in _ANALYSIS_SETUP_TRAIL)
    + (len(_ANALYSIS_SETUP_TRAIL) - 1) * _SETUP_COL_GAP
    + len(PAD)
    + 6  # panel border + padding, both sides
)


def _analysis_stage_column(
    stage: str,
    state: dict,
    theme,
    *,
    active: bool,
    rows_cap: int,
) -> tuple[list[Text], list[object]]:
    """One config set's options as a column of rows, lit only when it is active.

    The same renderer draws the set you are on and the four you are not — the
    difference is colour, not content. Every option is legible from anywhere in
    the wizard, which is the whole point of putting them side by side; the
    unfocused ones simply drop to the muted/dim pair so focus stays unambiguous.

    ``state["cursor"]`` is the active stage's live cursor (an int, or a
    ``(row, col)`` pair for the ragged Sources grid) — see _with_live.

    Returns the rows and, per row, the cursor value that row stands for (None
    for a heading, rule or blank). That second list is what makes the options
    clickable: it is built as the rows are, so a heading gained or a roster
    windowed cannot slide the hit targets off the labels they belong to.
    """
    cursor = state.get("cursor") if active else None
    rows: list[Text] = []
    targets: list[object] = []

    def opt(label: str, *, on: bool, focused: bool = False, enabled: bool = True, target: object = None) -> None:
        row = Text("", no_wrap=True, overflow="ellipsis")
        row.append("▸" if focused else " ", style=theme.accent_bright)
        lit = on and enabled
        if active:
            mark = theme.accent_bright if lit else theme.dim
        else:
            mark = _OFF_MARK_ON if lit else _OFF_MARK_OFF
        row.append("●" if lit else "○", style=mark)
        if not active:
            style = _OFF_LABEL_ON if lit else _OFF_LABEL_OFF
        elif not enabled:
            style = theme.dim
        elif focused:
            style = "bold white"
        else:
            style = theme.accent if on else theme.desc
        row.append(f" {label}", style=style)
        rows.append(row)
        targets.append(target if enabled else None)

    if stage == "Areas":
        available = state.get("available") or {}
        checked = state.get("features") or set()
        runnable = {f for f in _ANALYSIS_FEATURE_KEYS if available.get(f)}
        opt(
            "Analyse all",
            on=bool(runnable) and runnable <= checked,
            focused=cursor == 0,
            enabled=bool(runnable),
            target=0,
        )
        # Same rule the stacked layout draws: "Analyse all" acts ON the four
        # below rather than being a fifth peer. Its length tracks the labels it
        # underlines, not the column — the column is sized FROM these rows.
        # +2 for the "● " every label carries, and the row's own leading space is
        # already counted — one more and the divider alone made this column the
        # widest on the page, which then pushed the crumb row past the panel.
        rule_w = max(len(_ANALYSIS_FEATURE_LABELS[k][0]) for k in _ANALYSIS_FEATURE_KEYS) + 2
        rows.append(Text(" " + "─" * rule_w, style=theme.sep))
        targets.append(None)
        for index, key in enumerate(_ANALYSIS_FEATURE_KEYS, start=1):
            opt(
                _ANALYSIS_FEATURE_LABELS[key][0],
                on=key in checked,
                focused=cursor == index,
                enabled=bool(available.get(key)),
                target=index,
            )
    elif stage == "Sources":
        grid = state.get("grid") or {}
        picked = _sources_selected(state)
        order = [c for c in _COMPONENT_KEYS if grid.get(c)]
        row_idx, col_idx = cursor if isinstance(cursor, tuple) else (-1, -1)
        for r, ckey in enumerate(order):
            if rows:
                rows.append(Text(""))
                targets.append(None)
            head = Text(" ", no_wrap=True, overflow="ellipsis")
            head.append(
                _COMPONENT_NAMES.get(ckey, ckey).upper(),
                style=f"bold {theme.accent_bright}"
                if active and r == row_idx
                else (theme.accent if active else _OFF_HEADING),
            )
            rows.append(head)
            targets.append(None)
            for s, sub in enumerate(grid[ckey]):
                opt(
                    _SUBSOURCE_TITLES.get(sub, sub),
                    on=sub in picked.get(ckey, set()),
                    focused=r == row_idx and s == col_idx,
                    target=(r, s),
                )
    elif stage == "Depth":
        chosen = cursor if isinstance(cursor, int) else state.get("depth", 1)
        for index, (name, _label, _detail) in enumerate(_ANALYSIS_DEPTH_OPTIONS):
            opt(name.title(), on=index == chosen, focused=cursor == index, target=index)
    elif stage == "Time window":
        chosen = cursor if isinstance(cursor, int) else state.get("window", 2)
        for index, (days, _label) in enumerate(_ANALYSIS_WINDOW_OPTIONS):
            opt(f"{days}d", on=index == chosen, focused=cursor == index, target=index)
    elif stage == "People":
        roster = state.get("roster") or []
        picked = state.get("members")
        if not roster:
            rows.append(Text("  everyone on the board", style=theme.dim if active else _OFF_LABEL_OFF))
            targets.append(None)
        else:
            chosen = set(roster) if picked is None else set(picked)
            # Window the roster around the cursor: a team of thirty would push
            # every column below the page, and the column has no scrollbar to
            # explain itself. The active stage still scrolls, just in place.
            start = 0
            if len(roster) > rows_cap:
                start = max(0, min(len(roster) - rows_cap, (cursor or 0) - rows_cap // 2))
            for index, name in enumerate(roster[start : start + rows_cap], start=start):
                opt(name, on=name in chosen, focused=cursor == index, target=index)
            if len(roster) > rows_cap:
                rows.append(Text(f"  +{len(roster) - rows_cap} more", style=theme.dim))
                targets.append(None)
    if len(rows) > rows_cap:
        keep = max(1, rows_cap - 1)
        rows = rows[:keep] + [Text("  …", style=theme.dim)]
        targets = targets[:keep] + [None]
    return rows, targets


def _analysis_focus_detail(stage: str, state: dict, theme) -> Text:
    """The one line of prose the columns have no room for: the focused option.

    Side by side, a column is about seventeen columns wide — enough for
    "● Documentation" and nothing else. The descriptions still matter, so the
    cursor's own is spelled out full-width beneath the grid, where it has room.
    """
    cursor = state.get("cursor")
    row = Text(PAD, no_wrap=True, overflow="ellipsis")

    def say(name: str, detail: str, note: str = "") -> Text:
        row.append(name, style=f"bold {theme.accent_bright}")
        if detail:
            row.append("  ·  ", style=theme.dim)
            row.append(detail, style=theme.muted)
        if note:
            row.append("     ", style=theme.dim)
            row.append(note, style=theme.accent)
        return row

    if stage == "Areas":
        available = state.get("available") or {}
        checked = state.get("features") or set()
        runnable = {f for f in _ANALYSIS_FEATURE_KEYS if available.get(f)}
        note = f"{len(checked & runnable)}/{len(runnable)} selected" if runnable else ""
        if cursor == 0:
            return say("Analyse all", "Select every available analysis area", note)
        key = _ANALYSIS_FEATURE_KEYS[max(0, min(len(_ANALYSIS_FEATURE_KEYS) - 1, (cursor or 1) - 1))]
        label, detail = _ANALYSIS_FEATURE_LABELS[key]
        return say(label, detail if available.get(key) else "Unavailable — no source configured", note)
    if stage == "Sources":
        grid = state.get("grid") or {}
        order = [c for c in _COMPONENT_KEYS if grid.get(c)]
        row_idx = cursor[0] if isinstance(cursor, tuple) else 0
        ckey = order[max(0, min(len(order) - 1, row_idx))] if order else ""
        picked = _sources_selected(state)
        total = sum(len(v) for v in picked.values())
        return say(
            _COMPONENT_NAMES.get(ckey, ckey),
            _COMPONENT_DESCS.get(ckey, ""),
            f"{total} source{'' if total == 1 else 's'}",
        )
    if stage == "Depth":
        index = cursor if isinstance(cursor, int) else state.get("depth", 1)
        name, label, detail = _ANALYSIS_DEPTH_OPTIONS[max(0, min(len(_ANALYSIS_DEPTH_OPTIONS) - 1, index))]
        return say(name.title(), f"{label} · {detail}")
    if stage == "Time window":
        index = cursor if isinstance(cursor, int) else state.get("window", 2)
        days, label = _ANALYSIS_WINDOW_OPTIONS[max(0, min(len(_ANALYSIS_WINDOW_OPTIONS) - 1, index))]
        return say(f"{days} days", label)
    if stage == "People":
        roster = state.get("roster") or []
        picked = state.get("members")
        if not roster:
            return say("People", "everyone on the board")
        count = len(roster) if picked is None else len(picked)
        return say("People", "who the delivery and code numbers are about", f"{count} of {len(roster)} selected")
    return row


# Where the active-crumb rule was last DRAWN, and where its current slide began.
# Module state, like the music bar's tab presence: the wizard is a sequence of
# separate blocking loops, so a slide that outlives the step it started in has
# nowhere else to live. Positions are stored, not stage names — the column widths
# are recomputed every frame, so a name would resolve to a slot the rule may
# never have been at.
_RULE_SLIDE_SECS = 0.22
_rule: dict = {"stage": None, "at": 0.0, "from": (0, 0), "drawn": (0, 0)}


def reset_setup_rule() -> None:
    """Forget where the rule was, so the next page draws it in place.

    Called when the wizard is entered afresh: sliding in from the set you
    happened to leave off at last time is motion that means nothing.
    """
    _rule.update({"stage": None, "at": 0.0, "from": (0, 0), "drawn": (0, 0)})


def _rule_slide(active: str, x_to: int, w_to: int) -> tuple[int, int]:
    """The rule's current ``(offset, width)``, easing towards the active set.

    A new destination starts the slide from wherever the rule was last drawn —
    NOT from the previous set's slot. Turning back half way through a move is
    the common case (←, ←) and snapping to the column you never reached before
    setting off again is exactly the teleport the slide exists to avoid.

    Returns the destination unchanged on the first draw and once the slide has
    run out, so a page that is not moving renders identically every frame.
    """
    now = time.monotonic()
    if _rule["stage"] != active:
        _rule["from"] = (x_to, w_to) if _rule["stage"] is None else _rule["drawn"]
        _rule["stage"] = active
        _rule["at"] = now
    progress = (now - _rule["at"]) / _RULE_SLIDE_SECS
    if progress >= 1.0:
        _rule["drawn"] = (x_to, w_to)
    else:
        eased = ease_out_cubic(max(0.0, progress))
        x_from, w_from = _rule["from"]
        _rule["drawn"] = (
            round(x_from + (x_to - x_from) * eased),
            max(1, round(w_from + (w_to - w_from) * eased)),
        )
    return _rule["drawn"]


def _stage_neighbours(active: str, order: list[str], skip: set[str] | None = None) -> tuple[str | None, str | None]:
    """The sets either side of *active*, for ←/→. The ends do not wrap.

    ``skip`` names the sets this configuration does not use — a docs-only run
    has no Depth and no People. Their columns are still drawn (the trail is the
    whole trail), but stepping onto one would land on a step the wizard skips,
    which is a keypress that appears to do nothing or, from the last set, walks
    off the end of the sequence entirely.
    """
    skipped = {name.upper() for name in (skip or ())}
    at = next((i for i, stage in enumerate(order) if stage.upper() == active.upper()), 0)
    before = next((s for s in reversed(order[:at]) if s.upper() not in skipped), None)
    after = next((s for s in order[at + 1 :] if s.upper() not in skipped), None)
    return before, after


def _setup_column_natural_w(stage: str, body: list[Text]) -> int:
    """The narrowest a column can be without truncating its own crumb or rows.

    A roster name can be arbitrarily long, so it is capped — one over-long name
    must not squeeze the other five columns off the page. That name ellipsises;
    everything else stays whole.
    """
    head = len(stage) + 3  # leading space, dot, space
    return min(_SETUP_COL_MAX_W, max(head, *(len(row.plain) for row in body), _SETUP_COL_MIN_W))


def _analysis_setup_columns(
    active: str,
    state: dict,
    theme,
    *,
    width: int,
    body_rows: int,
) -> tuple[Table, list[tuple[int, int, str]], list[tuple[int, int, int, object]]]:
    """The whole wizard as one grid: trail across the top, options beneath.

    Returns the grid, each column's ``(x0, x1, stage)`` span, and the active
    column's ``(x0, x1, row, cursor_value)`` option targets — the first two in
    1-based terminal columns, the row relative to the grid's own top. The
    columns are a fixed width rather than rich's ``expand`` share-out precisely
    so those spans can be computed instead of guessed — a click that lands one
    column over is worse than no click at all.

    Nothing is ruled between the columns: standing apart already separates
    them, and the one rule the page draws — under the active crumb — says the
    only thing a rule needs to here, which set you are editing.
    """
    stages = list(_ANALYSIS_SETUP_TRAIL)
    gap = _SETUP_COL_GAP
    inner = width - 6 - len(PAD)  # panel border + padding, then the left spacer
    here = next((i for i, name in enumerate(stages) if name.upper() == active.upper()), 0)

    drawn = [
        _analysis_stage_column(
            stage,
            state,
            theme,
            active=stage.upper() == active.upper(),
            rows_cap=body_rows,
        )
        for stage in stages
    ]
    bodies = [rows for rows, _targets in drawn]
    # Size each column to what it holds, then share the slack out evenly. Six
    # equal columns spend as much room on Depth (two options, five letters) as
    # on a roster; worse, at the widths where equal columns stop fitting, a
    # minimum-width floor made the span arithmetic disagree with what rich drew,
    # so the click regions sat a column off with nothing to show for it.
    widths = [_setup_column_natural_w(stage, body) for stage, body in zip(stages, bodies, strict=True)]
    share, extra = divmod(max(0, inner - sum(widths) - (len(stages) - 1) * gap), len(stages))
    widths = [w + share for w in widths]
    widths[-1] += extra
    # Never wider than the page: the crumb row is one Text now, so an overrun of
    # even a column costs the last crumb to an ellipsis. Shrink the widest until
    # it fits — its own content ellipsises instead, which is the cheaper loss.
    while sum(widths) + (len(stages) - 1) * gap > inner and max(widths) > 1:
        widths[widths.index(max(widths))] -= 1

    # The crumb row and the rule under it are laid out by hand rather than as
    # grid cells: the rule SLIDES between columns, so it has to be one run of
    # glyphs at an arbitrary offset, not a cell that belongs to a column.
    offsets = [sum(widths[:i]) + i * gap for i in range(len(stages))]
    crumbs = Text(PAD, no_wrap=True, overflow="ellipsis")
    for index, stage in enumerate(stages):
        if index:
            crumbs.append(" " * gap)
        # A leading space so the crumb's dot sits over its options' dots — the
        # option rows spend their first column on the cursor caret.
        crumbs.append(" ")
        if index == here:
            crumbs.append("● ", style=theme.accent_bright)
            crumbs.append(stage.upper(), style="bold white")
        elif index < here:
            crumbs.append("● ", style=_OFF_CRUMB_DONE)
            crumbs.append(stage.upper(), style=_OFF_CRUMB_DONE)
        else:
            crumbs.append("○ ", style=_OFF_CRUMB_TODO)
            crumbs.append(stage, style=_OFF_CRUMB_TODO)
        crumbs.append(" " * max(0, widths[index] - (len(stage) + 3)))

    # Underline the ACTIVE crumb only, easing across from wherever it last was.
    # The columns are already separated by standing apart; ruling between them
    # boxed the page in for no extra information, and every rule drawn is one
    # more thing competing with the single mark that matters — which set you are
    # editing. Sliding it is what carries that mark across the move: the rule
    # arriving instantly somewhere else reads as a redraw, not as travel.
    slide_x, slide_w = _rule_slide(active, offsets[here], widths[here])
    rule = Text(PAD + " " * slide_x, no_wrap=True, overflow="ellipsis")
    rule.append("─" * slide_w, style=theme.accent_bright)

    grid = Table.grid(padding=(0, 0))
    grid.add_column(width=len(PAD))
    for index in range(len(stages)):
        if index:
            grid.add_column(width=gap)
        grid.add_column(width=widths[index], no_wrap=True, overflow="ellipsis")

    # Drawn to the tallest column, not to the page floor: with nothing ruled
    # between them, filler rows show nothing at all — and the focused option's
    # description follows the grid, so every empty row is one more between the
    # item and what it says about itself.
    for r in range(max((len(body) for body in bodies), default=0)):
        row: list = [""]
        for index, body in enumerate(bodies):
            if index:
                row.append("")
            row.append(body[r] if r < len(body) else Text(""))
        grid.add_row(*row)

    # x = 1 (panel border) + 2 (panel padding) + len(PAD) (spacer) + 1 (1-based).
    x = 1 + 2 + len(PAD) + 1
    spans: list[tuple[int, int, str]] = []
    options: list[tuple[int, int, int, object]] = []
    for index, stage in enumerate(stages):
        spans.append((x, x + widths[index] - 1, stage))
        if index == here:
            # Body row r sits two rows under the crumb row (the crumb, then the
            # rule). Rows relative to the grid; the page adds its own offset.
            options += [
                (x, x + widths[index] - 1, r + 2, target)
                for r, target in enumerate(drawn[index][1])
                if target is not None
            ]
        x += widths[index] + gap
    return crumbs, rule, grid, spans, options


def _analysis_setup_sidebar(active: str, summary: dict[str, str] | None, theme) -> Panel:
    """The stage list: name, chosen value, and a caret on the one you are at."""
    rows: list[Text] = []
    for stage in _ANALYSIS_SETUP_TRAIL:
        here = stage.upper() == active.upper()
        value = (summary or {}).get(stage, "")
        row = Text(justify="left", no_wrap=True, overflow="ellipsis")
        row.append("▸" if here else " ", style=theme.accent_bright)
        row.append(f"{stage:<12}", style=f"bold {theme.accent_bright}" if here else theme.muted)
        # An em dash for a stage not yet reached, so "nothing chosen" never reads
        # as "chose nothing" — the two matter on People, where empty means all.
        row.append(value or "—", style=theme.value if here else (theme.desc if value else theme.dim))
        rows.append(row)
    return Panel(
        Group(*rows),
        box=rich.box.ROUNDED,
        border_style=theme.sep,
        padding=(0, 1),
        width=_SETUP_SIDEBAR_W,
    )


def _analysis_setup_page(
    active: str,
    help_text: str,
    *block,
    state: dict | None = None,
    summary: dict[str, str] | None = None,
    width: int,
    height: int,
    message: str = "",
    next_tab: bool | str = True,
):
    """The scaffold every setup stage draws into: wordmark, trail, then EVERY
    config set — the one you are on expanded with its descriptions and cursor,
    the rest collapsed to a line that still shows all of their options.

    The wizard is a sequence of pages, and each page used to show one set with
    two thirds of the terminal empty beneath it. Showing them all costs nothing
    and means a choice three steps back is visible while you make this one.

    ``block`` is the active stage's control rows; ``state`` supplies every other
    stage's values (see _analysis_stage_line). Without ``state`` the page falls
    back to drawing the active stage alone, which is what the preview tool and
    the per-stage unit tests do.

    Given the width (``_SETUP_COLUMNS_MIN_W``) it lays the sets out side by side
    instead — the trail becomes the header row and ``block`` is not drawn at all,
    since the active column carries the same options with the cursor on them.
    """
    theme = ANALYSIS_THEME
    title_h = 2 if height >= 28 else 1
    if state is not None and width >= _SETUP_COLUMNS_MIN_W:
        return _analysis_setup_columns_page(
            active,
            help_text,
            state,
            theme,
            width=width,
            height=height,
            title_h=title_h,
            message=message,
            next_tab=next_tab,
        )
    hdr = _analysis_setup_header(active, help_text, message=message)
    rows: list = [Text(""), _analysis_setup_title(width, height), Text("")]
    rows += hdr
    stage_rows: dict[str, int] = {}
    if state is None:
        rows.extend(block)
    else:
        # Rendered rows, not list entries: the wordmark is one entry and two rows,
        # and the active stage's block is one entry of unknown height. Counting
        # entries put every click region a row or more above its line.
        drawn = (2 if height >= 28 else 1) + 1 + 1 + len(hdr)  # title, the two blanks, header
        tall = height >= _STAGE_HEADING_TALL_MIN_H
        heading, head_h = _analysis_stage_heading(active, theme, tall=tall)
        block_h = _rendered_height(Group(*block), width - 6)
        sets = list(_ANALYSIS_SETUP_TRAIL)
        # Spread the sets down the page instead of stacking them against the top.
        # A wizard step is short and a terminal is not: bunched at the top, two
        # thirds of the screen read as broken rather than roomy. The gap is what
        # is spare, shared between the sets and capped so a very tall terminal
        # does not scatter them.
        body_h = head_h + block_h + len(sets) - 1
        spare = height - 4 - drawn - body_h  # 4 = top border+pad, bottom pad+border
        gap = max(1, min(3, spare // max(1, len(sets))))
        for i, stage in enumerate(sets):
            if i:
                rows.extend(Text("") for _ in range(gap))
                drawn += gap
            stage_rows[stage] = drawn
            if stage.upper() == active.upper():
                rows.append(heading)
                rows.extend(block)
                drawn += head_h + block_h
            else:
                rows.append(_analysis_stage_line(stage, state, theme))
                drawn += 1
    panel = build_page_panel(Group(*rows), theme=theme, height=height)
    panel._next_tab = next_tab
    if stage_rows:
        # (x0, y0, x1, y1, stage), 1-based terminal coords, so clicking a collapsed
        # set jumps the wizard to it. Rows are counted as the page is built rather
        # than derived from a constant — the header grows a line when it carries a
        # message, and a guessed offset would land on the wrong set exactly then.
        # +1: `drawn` counts rows already laid down, so the stage sits on the NEXT one.
        # A short terminal runs the last set past the bottom border. Publishing a
        # region there offers a jump on a row the set is not drawn on, so the
        # off-page ones are dropped rather than shipped as dead targets.
        pre = 2 + 1  # panel border + top padding, then the row itself
        panel._stage_regions = [
            (2, pre + row, width - 2, pre + row, stage)
            for stage, row in stage_rows.items()
            if stage.upper() != active.upper()
            and pre + row <= height - 2
            and stage.upper() not in {name.upper() for name in (state.get("skip") or ())}
        ]
        # ←/→ walk the sets here too. Stacked, they are still all on the page —
        # one line each — and Esc leaves the setup now, so without this a narrow
        # terminal would have no keyboard way back to a set at all.
        panel._stage_neighbours = _stage_neighbours(active, list(stage_rows), state.get("skip"))
    return panel


def _analysis_setup_columns_page(
    active: str,
    help_text: str,
    state: dict,
    theme,
    *,
    width: int,
    height: int,
    title_h: int,
    message: str = "",
    next_tab: bool | str = True,
):
    """The wide-terminal setup page: wordmark, then the whole wizard as columns.

    The crumb line loses its trail here because the grid's header row *is* the
    trail — spelling it twice, once as a line and again as headers, was the
    first thing that read as clutter.
    """
    brand = Text(PAD, justify="left", no_wrap=True, overflow="ellipsis")
    brand.append("ANALYSIS SETUP", style=f"bold {theme.accent_bright}")
    # ←/→ walk the sets here, so a stage that offers them as a second way to
    # move within its own list would be describing a key it no longer owns.
    hint = help_text.replace("←/→ or ↑/↓", "↑/↓").replace("←/→ and ↑/↓", "↑/↓")
    if "between sets" not in hint:  # review already leads with it
        hint += " · ←/→ between sets"
    hdr: list = [brand, Text(PAD + hint, style=theme.muted)]
    if message:
        hdr.append(Text(PAD + "⚠  " + message, style=theme.warn))
    hdr.append(Text(""))

    # Rows above the grid's header row, counted rather than assumed: the leading
    # blank, the wordmark, the blank under it, then the header block (which grows
    # a line when it carries a message — the click regions must move with it).
    pre = 1 + title_h + 1 + len(hdr)
    # 4 = panel border + padding, top and bottom; 2 = the grid's crumb row and
    # the rule under it; 1 = the focused-option line that sits at the foot.
    body_rows = max(3, height - 4 - pre - 2 - 1)
    crumbs, rule, grid, spans, options = _analysis_setup_columns(active, state, theme, width=width, body_rows=body_rows)

    rows: list = [Text(""), _analysis_setup_title(width, height), Text("")]
    rows += hdr
    rows.extend((crumbs, rule, grid, Text("")))
    rows.append(_analysis_focus_detail(active, state, theme))

    panel = build_page_panel(Group(*rows), theme=theme, height=height)
    # The last set in play has nothing after it, so its forward action IS the
    # run — there is no separate review page left to say so.
    panel._next_tab = "Run Analysis" if state.get("last") else next_tab
    # +3: panel border, top padding, then `pre` rows already laid down, so the
    # crumbs sit on the next one. A whole COLUMN is the jump target, not just its
    # crumb — the set is drawn under it, and a click landing on "Deep" in a column
    # you are not editing plainly means "take me there" rather than nothing.
    y = 3 + pre
    skipped = {name.upper() for name in (state.get("skip") or ())}
    panel._stage_regions = [
        (x0, y, x1, height - 2, stage)
        for x0, x1, stage in spans
        if stage.upper() != active.upper() and stage.upper() not in skipped
    ]
    # (x0, y0, x1, y1, cursor value) for the set being edited. The loops check
    # these first; the active column overlaps no stage region, so order only
    # matters for reading it, not for correctness.
    panel._option_regions = [(x0, y + row, x1, y + row, target) for x0, x1, row, target in options]
    # The sets either side of this one, for ←/→. Published only here, so on a
    # narrow terminal (no columns) the arrows keep their in-list meaning.
    panel._stage_neighbours = _stage_neighbours(active, [stage for _x0, _x1, stage in spans], state.get("skip"))
    return panel


def _build_analysis_feature_screen(
    available: dict[str, bool],
    checked: set[str],
    cursor: int,
    *,
    width: int = 80,
    height: int = 24,
    message: str = "",
    summary: dict[str, str] | None = None,
    state: dict | None = None,
) -> Panel:
    """First Analysis card: choose independently runnable result areas."""
    theme = ANALYSIS_THEME
    runnable = {feature for feature in _ANALYSIS_FEATURE_KEYS if available.get(feature)}
    all_checked = bool(runnable) and runnable <= checked
    label_w = max(
        len("Analyse all"),
        *(len(_ANALYSIS_FEATURE_LABELS[f][0]) for f in _ANALYSIS_FEATURE_KEYS),
    )
    rows = [
        _analysis_toggle_row(
            "Analyse all",
            "Select every available analysis area",
            focused=cursor == 0,
            selected=all_checked,
            enabled=bool(runnable),
            note=f"{len(checked)}/{len(runnable)} selected" if runnable else "",
            label_w=label_w,
        )
    ]
    # "Analyse all" acts ON the four below rather than being a fifth peer, so a
    # rule separates it from the things it toggles. Purely decorative — the
    # cursor still indexes the option rows, so it must not join `rows`.
    divider = Text(_PAD + "─" * (label_w + 4), style=theme.sep)
    for index, feature in enumerate(_ANALYSIS_FEATURE_KEYS, start=1):
        label, detail = _ANALYSIS_FEATURE_LABELS[feature]
        enabled = feature in runnable
        rows.append(
            _analysis_toggle_row(
                label,
                detail,
                focused=index == cursor,
                selected=feature in checked,
                enabled=enabled,
                label_w=label_w,
            )
        )
    footer = f"{len(checked)} selected" if checked else "Select at least one available area"
    return _analysis_setup_page(
        "Areas",
        "Arrows move · Space selects · A selects all · Enter continues",
        rows[0],
        divider,
        # In stacked mode the viewport must NOT fill the page: it sits between the
        # sets above and below it, and a height-filling list pushed every one of
        # them off the bottom. Sized to its own rows instead (four areas — it has
        # never needed to scroll); the standalone page keeps the full viewport.
        _analysis_toggle_viewport(
            rows[1:],
            max(0, cursor - 1),
            height=(13 + len(rows) - 1) if state is not None else height,
            header_h=13,
        ),
        Text(_PAD + footer, style=theme.accent_bright),
        summary=summary,
        state=_with_live(state, available=available, features=checked, cursor=cursor),
        width=width,
        height=height,
        message=message,
    )


def _build_component_select_screen(
    grid: dict[str, list[str]],
    rows_order: list[str],
    checked: dict[str, set[int]],
    row_idx: int,
    col_idx: int,
    *,
    width: int = 80,
    height: int = 24,
    message: str = "",
    descriptions: dict[str, str] | None = None,
    theme=None,
    brand: str = "ANALYSIS SETUP",
    title_builder=None,
    footer_verb: str = "analyse",
    summary: dict[str, str] | None = None,
    state: dict | None = None,
) -> Panel:
    """Ragged component × sub-source multi-select.

    ``grid`` maps each component to its CONFIGURED sub-sources (delivery ←
    jira/azdevops, code ← github/azdo, docs ← confluence/notion). ``rows_order`` is
    the components with at least one sub-source. ``checked`` maps component → set of
    selected sub-source indices. ``row_idx``/``col_idx`` locate the focused cell.

    Defaults to the Analysis look; ``theme``/``brand``/``title_builder`` (a
    callable(width, height) → renderable) / ``footer_verb`` re-brand it for other
    modes' setup screens (Reporting passes REPORTING_THEME + "REPORTING SETUP")."""
    theme = theme or ANALYSIS_THEME
    title = (title_builder or _analysis_setup_title)(width, height)
    sections: list = []

    per_component: list[tuple[str, int]] = []
    total_selected = 0
    for ci, ckey in enumerate(rows_order):
        subs = grid.get(ckey, [])
        focused_row = ci == row_idx
        # Flush with its own source rows below: the header sat two columns right
        # of the things it labels, so the group read as indented under nothing.
        header = Text(_PAD, justify="left")
        header.append(
            _COMPONENT_NAMES.get(ckey, ckey).upper(),
            style=f"bold {theme.accent_bright if focused_row else theme.accent}",
        )
        header.append(
            f"  ·  {(descriptions or {}).get(ckey, _COMPONENT_DESCS.get(ckey, ''))}",
            style=theme.dim,
        )
        n_checked = 0
        source_rows: list[Text] = []
        for si, s in enumerate(subs):
            is_focused = focused_row and si == col_idx
            is_checked = si in checked.get(ckey, set())
            if is_checked:
                n_checked += 1
                total_selected += 1
            name = _SUBSOURCE_TITLES.get(s, s)
            source_rows.append(
                _analysis_toggle_row(
                    name,
                    "",
                    focused=is_focused,
                    selected=is_checked,
                    theme=theme,
                )
            )
        sections.extend((header, Group(*source_rows), Text("")))
        per_component.append((_COMPONENT_NAMES.get(ckey, ckey), n_checked))

    # Status footer: total + per-component counts (or the at-least-one guard).
    footer = Text(_PAD + "  ", justify="left")
    if total_selected:
        footer.append(f"{total_selected} sources", style=theme.accent_bright)
        footer.append("  ·  " + "  ·  ".join(f"{nm} {n}" for nm, n in per_component), style=theme.muted)
        footer.append("     Enter ⏎", style=theme.dim)
    else:
        footer.append(f"Select at least one source to {footer_verb}", style=theme.accent_bright)
    header = _analysis_setup_header(
        "Sources",
        "Arrows move · Space selects · Enter continues",
        message=message,
        brand=brand,
        theme=theme,
    )
    if width < 70 or height < 28:
        sections = list(sections[row_idx * 3 : row_idx * 3 + 3])
        counts = "  ·  ".join(f"{name} {count}" for name, count in per_component)
        sections.insert(0, Text(_PAD + counts, style=theme.muted))
    # Reporting borrows this builder with its own brand and theme; only Analysis
    # has the stage sidebar, so anything re-branded keeps the plain full-width page.
    if brand != "ANALYSIS SETUP":
        content = Group(Text(""), title, Text(""), *header, Group(*sections), footer)
        panel = build_page_panel(content, theme=theme, height=height)
        panel._next_tab = True
        return panel
    return _analysis_setup_page(
        "Sources",
        "Arrows move · Space selects · Enter continues",
        Group(*sections),
        footer,
        summary=summary,
        state=_with_live(
            state,
            grid=grid,
            components={c: [grid[c][i] for i in sorted(checked.get(c, set()))] for c in rows_order},
            cursor=(row_idx, col_idx),
        ),
        width=width,
        height=height,
        message=message,
    )


# ── Board setup (the "no tracker configured" entry gate) ────────────────────
# Analysis reads sprints and stories from a tracker, so it can't start without
# one. This screen replaces what used to be a dead end — a static "set these in
# your .env" message that bounced you back to the menu on any key — with the
# fields themselves, editable in place. Same trade the settings page makes:
# tell the user what's missing AND let them fix it where they are.

_BOARD_TRACKERS = ("Jira", "Azure DevOps")

# Left margin on a field row. Named because the focus bar has to start exactly
# where it ends — styling from column 0 would run the bar out into the margin.
_BOARD_ROW_INDENT = PAD + "  "


def board_setup_fields(tracker: int) -> list[dict]:
    """The credential fields for tracker index ``tracker`` (0=Jira, 1=Azure DevOps).

    Reuses the setup wizard's field definitions rather than restating them, so
    labels, placeholders, masking and the where-to-get-it hints stay in one place.
    """
    from yeaboi.ui.provider_select._constants import _AZDEVOPS_TRACKING_FIELDS, _ISSUE_TRACKING_FIELDS

    return _ISSUE_TRACKING_FIELDS if tracker == 0 else _AZDEVOPS_TRACKING_FIELDS


def board_setup_ready(tracker: int, values: dict[str, str]) -> bool:
    """Whether every ``required`` field of this tracker has a value.

    Drives the Connect button's enabled state. Deliberately stricter than
    ``is_jira_configured()`` (which only checks the token): a token with no base
    URL passes that check and then fails on the first request.
    """
    return all(str(values.get(f["env_var"], "")).strip() for f in board_setup_fields(tracker) if f.get("required"))


def _board_value_cell(
    field: dict,
    value: str,
    *,
    editing: tuple[str, str, int] | None,
    theme,
    avail: int,
) -> Text:
    """Render one field's value cell: the live edit buffer, a mask, or 'not set'."""
    cell = Text(justify="left")
    env, masked = field["env_var"], field.get("masked", False)
    if editing is not None and editing[0] == env:
        # Window the buffer to what's left of the row so the cursor stays on
        # screen — otherwise a long token scrolls out exactly where you're typing.
        buf, pos = editing[1], max(0, min(editing[2], len(editing[1])))
        lo = max(0, pos - avail + 1)
        win, wc = buf[lo : lo + avail], pos - lo
        cell.append(win[:wc], style=theme.value)
        cell.append(win[wc : wc + 1] or " ", style="reverse bold")  # block cursor
        cell.append(win[wc + 1 :], style=theme.value)
    elif masked and value:
        shown = value[:4] + "•" * min(12, len(value) - 4) if len(value) > 4 else "•" * len(value)
        cell.append(shown, style=theme.dim)
    elif value:
        cell.append(value, style=theme.value)
    elif field.get("required"):
        cell.append("required", style=theme.muted)
    else:
        cell.append("optional", style=theme.dim)
    return cell


def _build_analysis_board_setup_screen(
    values: dict[str, str],
    *,
    tracker: int = 0,
    selected: int = 0,
    editing: tuple[str, str, int] | None = None,
    action_sel: int = 0,
    message: str = "",
    width: int = 80,
    height: int = 24,
    shimmer_tick: float | None = None,
) -> Panel:
    """Build the Analysis board-setup gate: pick a tracker, fill in its credentials.

    ``values`` maps env var -> current value; ``editing`` is the open in-place
    edit as ``(env_var, buffer, cursor)``. Publishes ``_row_regions`` (one per
    field row) and ``_tab_regions`` (the tracker switch) so the loop can
    hit-test clicks, matching the settings page's click-to-edit.
    """
    from yeaboi.ui.shared._components import analysis_title

    theme = ANALYSIS_THEME
    fields = board_setup_fields(tracker)
    ready = board_setup_ready(tracker, values)

    title = analysis_title(shimmer_tick)
    subtitle = Text(PAD + "Connect a board", style=f"bold {theme.accent}")
    blurb = Text(
        PAD + "Analysis reads sprints and stories from your tracker. Fill these in to continue —",
        style=theme.muted,
    )
    blurb2 = Text(PAD + "they're saved to your .env, so this is a one-off.", style=theme.muted)

    # Tracker switch. Rendered as one row so the two options read as alternatives
    # rather than a list you scroll — you only ever need one of them configured.
    tabs = Text(PAD, justify="left")
    tab_regions: list[tuple[int, int, int]] = []
    for i, name in enumerate(_BOARD_TRACKERS):
        if i:
            tabs.append("   ")
        label = f"[ {name} ]" if i == tracker else f"  {name}  "
        x0 = tabs.cell_len
        tabs.append(label, style=f"bold {theme.accent}" if i == tracker else theme.dim)
        tab_regions.append((x0, tabs.cell_len - 1, i))

    label_w = max(len(f["label"]) for f in fields) + 2
    body: list[Text] = []
    row_regions: list[tuple[int, str, str, bool]] = []
    striped: list[int] = []  # rows that get the focus bar, applied once the width is known
    for i, field in enumerate(fields):
        focused = i == selected and editing is None
        env = field["env_var"]
        row = Text(_BOARD_ROW_INDENT, justify="left", no_wrap=True, overflow="ellipsis")
        marker_style = theme.accent if str(values.get(env, "")).strip() else theme.muted
        row.append("● " if str(values.get(env, "")).strip() else "○ ", style=marker_style)
        row.append(field["label"].ljust(label_w), style=theme.value if focused else theme.muted)
        row.append(
            _board_value_cell(
                field,
                str(values.get(env, "")),
                editing=editing,
                theme=theme,
                avail=max(8, width - len(PAD) - label_w - 12),
            )
        )
        if focused or (editing is not None and editing[0] == env):
            striped.append(i)
        row_regions.append((len(body), env, field["label"], field.get("masked", False)))
        body.append(row)

    # The focus bar spans the FIELD BLOCK, not the terminal — a stripe running the
    # full width reads as a separator rather than a cursor. Measured off the widest
    # row so every row highlights to the same edge, the way the settings sections do.
    stripe_w = max(r.cell_len for r in body) + 2
    for i in striped:
        body[i].append(" " * max(0, stripe_w - body[i].cell_len))
        # stylize(), NOT .style: a Text's `style` also paints the padding Rich adds
        # out to the full line width, which is what made the bar span the terminal.
        # Starting at the indent's end keeps it off the left margin too, so the bar
        # is exactly the row — bounded on both sides.
        body[i].stylize(f"on {_SETTINGS_FOCUS_BG}", len(_BOARD_ROW_INDENT))

    # Where-to-get-it hint for the focused field only — the full stack of hints
    # would bury the fields themselves.
    hint_rows: list[Text] = []
    if 0 <= selected < len(fields):
        hint = fields[selected].get("hint", "")
        if hint:
            h = Text(PAD + "  ", justify="left", no_wrap=True, overflow="ellipsis")
            h.append("↳ ", style=theme.muted)
            h.append(hint, style=theme.dim)
            hint_rows.append(h)

    status = Text(PAD, justify="left")
    if message:
        status.append(message, style=theme.accent)
    elif ready:
        status.append("All set — press Continue to start.", style=theme.accent)
    else:
        missing = [f["label"] for f in fields if f.get("required") and not str(values.get(f["env_var"], "")).strip()]
        status.append(f"Still needed: {', '.join(missing)}", style=theme.muted)

    # No Back button: the chrome's own back tab already covers leaving, so a second
    # affordance for it is noise. "Continue" is the one thing this page adds, and
    # only once there is something to continue to.
    actions = ["Continue"] if ready else []
    btn_top, btn_mid, btn_bot = build_action_buttons(actions, action_sel) if actions else (None, None, None)

    # Controls ride in the bottom-left pocket beside "back" (_hint_tab), the same
    # as the settings page — they don't take a body row of their own.
    hint = Text(justify="left", no_wrap=True)  # drawn inside a chrome tab, so no body pad
    if editing is not None:
        hint.append("type to edit", style=theme.accent)
        hint.append("  ·  ", style=theme.muted)
        hint.append("Enter", style=theme.accent)
        hint.append("  save  ·  ", style=theme.muted)
        hint.append("Esc", style=theme.accent)
        hint.append("  cancel  ·  '-' clears", style=theme.muted)
    else:
        hint.append("↑/↓", style=theme.accent)
        hint.append("  pick field  ·  ", style=theme.muted)
        hint.append("←/→", style=theme.accent)
        hint.append("  switch tracker  ·  ", style=theme.muted)
        hint.append("Enter", style=theme.accent)
        hint.append("  edit", style=theme.muted)  # 'Esc back' dropped — the back tab covers it

    content = Group(
        Text(""),
        title,
        Text(""),
        subtitle,
        Text(""),
        blurb,
        blurb2,
        Text(""),
        tabs,
        Text(""),
        *body,
        Text(""),
        *hint_rows,
        Text(""),
        status,
        Text(""),
        *([btn_top, btn_mid, btn_bot] if actions else []),
    )
    panel = build_page_panel(content, theme=theme, height=height)
    panel._hint_tab = hint
    panel._board_actions = actions
    panel._row_regions = row_regions  # (body_index, env, label, masked) per field row
    panel._tab_regions = tab_regions  # (x0, x1, tracker_index) on the switch row
    return panel


def _build_analysis_depth_screen(
    selected: int = 0,
    *,
    width: int = 80,
    height: int = 24,
    summary: dict[str, str] | None = None,
    state: dict | None = None,
) -> Panel:
    """Choose Quick (zero LLM calls) or Deep (cached AI enrichment)."""
    options = _ANALYSIS_DEPTH_OPTIONS
    rows: list[Text] = []
    label_w = max(len(name) for name, _label, _detail in options)
    for idx, (name, label, detail) in enumerate(options):
        focused = idx == selected
        rows.append(
            _analysis_toggle_row(
                name,
                detail,
                focused=focused,
                selected=focused,
                note=f"{label} · {detail}",
                label_w=label_w,
            )
        )

    return _analysis_setup_page(
        "Depth",
        "←/→ or ↑/↓ choose · Enter continue · Esc cancel",
        Group(*rows),
        summary=summary,
        state=_with_live(state, depth=selected, cursor=selected),
        width=width,
        height=height,
    )


def _build_analysis_model_offer_screen(
    current_model: str,
    recommended_model: str,
    predicted_seconds: int,
    selected: int = 0,
    *,
    target_seconds: int = 600,
    width: int = 80,
    height: int = 24,
) -> Panel:
    """Offer a faster installed Ollama model when preflight predicts a slow run."""
    theme = ANALYSIS_THEME
    minutes = max(1, round(predicted_seconds / 60))
    target_minutes = max(1, round(target_seconds / 60))
    options = (
        (recommended_model, "Use for structured Analysis calls"),
        (current_model, f"Keep current model · estimated {minutes} min"),
    )
    rows: list[Text] = []
    label_w = max(len(model or "current model") for model, _detail in options)
    for index, (model, detail) in enumerate(options):
        focused = index == selected
        rows.append(
            _analysis_toggle_row(
                model or "current model",
                detail,
                focused=focused,
                selected=focused,
                note=f"{'Faster' if index == 0 else 'Current'} · {detail}",
                label_w=label_w,
            )
        )
    content = Group(
        Text(""),
        _analysis_setup_title(width, height),
        Text(""),
        *_analysis_setup_header("Model", "←/→ or ↑/↓ choose · Enter continue · Esc cancel"),
        Text(
            _PAD + f"The current model is unlikely to finish Deep analysis within {target_minutes} minutes.",
            style=theme.muted,
        ),
        Text(_PAD + "Choose a faster installed model or continue with the original ETA.", style=theme.dim),
        Text(""),
        Group(*rows),
    )
    return build_page_panel(content, theme=ANALYSIS_THEME, border_style=theme.accent, height=height)


def _build_analysis_window_screen(
    selected: int = 2,
    *,
    width: int = 80,
    height: int = 24,
    summary: dict[str, str] | None = None,
    state: dict | None = None,
) -> Panel:
    """Choose the changed-content window shared by Code and Docs."""
    options = _ANALYSIS_WINDOW_OPTIONS
    rows: list[Text] = []
    label_w = max(len(f"{days} DAYS") for days, _label in options)
    for idx, (days, label) in enumerate(options):
        focused = idx == selected
        rows.append(
            _analysis_toggle_row(
                f"{days} DAYS",
                label,
                focused=focused,
                selected=focused,
                note=label,
                label_w=label_w,
            )
        )
    return _analysis_setup_page(
        "Time window",
        "←/→ and ↑/↓ choose · Enter continue · Esc cancel",
        Group(*rows),
        summary=summary,
        state=_with_live(state, window=selected, cursor=selected),
        width=width,
        height=height,
    )


def _build_member_select_screen(
    roster: list[str],
    checked: set[int],
    cursor: int,
    *,
    width: int = 80,
    height: int = 24,
    message: str = "",
    summary: dict[str, str] | None = None,
    state: dict | None = None,
) -> Panel:
    """Roster multi-select with an explicit checked state for every member."""
    theme = ANALYSIS_THEME
    n_checked = len(checked)
    scope = f"{n_checked} of {len(roster)} selected"
    rows: list[Text] = []
    for idx, name in enumerate(roster):
        rows.append(
            _analysis_toggle_row(
                name,
                "",
                focused=idx == cursor,
                selected=idx in checked,
            )
        )
    if not roster:
        viewport_renderable = _analysis_toggle_row(
            "No members found",
            "",
            focused=False,
            enabled=False,
        )
    else:
        # Stacked, the roster gets a fixed six-row window with its scrollbar rather
        # than the rest of the terminal — the sets below it need somewhere to be.
        viewport_renderable = _analysis_toggle_viewport(
            rows, cursor, height=(12 + _STACKED_ROSTER_ROWS) if state is not None else height, header_h=12
        )

    return _analysis_setup_page(
        "People",
        "Arrows move · Space selects · A selects all · Enter continues",
        Text(_PAD + scope, style=theme.accent_bright),
        Text(""),
        viewport_renderable,
        summary=summary,
        state=_with_live(
            state,
            roster=roster,
            members=[roster[i] for i in sorted(checked) if i < len(roster)],
            cursor=cursor,
        ),
        width=width,
        height=height,
        message=message,
    )


def _build_code_scope_select_screen(
    items: list[str],
    checked: set[int],
    cursor: int,
    *,
    heading: str = "Azure projects",
    unit: str = "projects",
    empty_label: str = "No projects found",
    hint: str = "",
    width: int = 80,
    height: int = 24,
    message: str = "",
) -> Panel:
    """Code-scope multi-select for one Analysis run (Azure projects, GitHub owners).

    One screen for both hosts: they differ only in wording, and a second copy
    would drift the moment either gains a state. ``hint`` states what selecting an
    entry costs — GitHub owners expand to every active repo underneath them, which
    the user has no other way to see before pressing Enter."""
    theme = ANALYSIS_THEME
    rows: list[Text] = []
    for idx, item in enumerate(items):
        rows.append(
            _analysis_toggle_row(
                item,
                "",
                focused=idx == cursor,
                selected=idx in checked,
            )
        )
    header = _analysis_setup_header(
        heading,
        "Arrows move · Space selects · A selects all · Enter continues",
        message=message,
    )
    # Empty discovery is a real outcome (a token with no visible orgs, a PAT
    # scoped to nothing) — say so in the list rather than render a blank viewport.
    if not items:
        viewport_renderable = _analysis_toggle_row(empty_label, "", focused=False, enabled=False)
    else:
        viewport_renderable = _analysis_toggle_viewport(rows, cursor, height=height, header_h=12)
    scope_lines = [Text(_PAD + f"{len(checked)} of {len(items)} {unit} selected", style=theme.accent_bright)]
    if hint:
        scope_lines.append(Text(_PAD + hint, style=theme.muted))
    # The code-scope sub-step is not one of the six trail stages, so it keeps the
    # plain full-width page — a sidebar would have to light a stage that is not
    # where you are.
    panel = build_page_panel(
        Group(
            Text(""),
            _analysis_setup_title(width, height),
            Text(""),
            *header,
            *scope_lines,
            Text(""),
            viewport_renderable,
        ),
        theme=ANALYSIS_THEME,
        height=height,
    )
    panel._next_tab = True
    return panel


def _instructions_body(instructions_text: str, *, width: int = 80) -> list:
    """The planning-instructions body: the team calibration this preview was built from.


    Split out from the screen builder so the preview page can compose it
    beside another column instead of only as a page of its own.
    """
    import re as _re

    c_section = "bold #22c55e"
    c_subsection = "bold rgb(180,200,220)"
    c_label = "bold white"
    c_value = "rgb(180,180,200)"
    c_muted = "rgb(120,120,140)"
    c_accent = "rgb(100,180,100)"
    c_warn = "rgb(220,180,60)"
    c_arrow = "rgb(100,180,220)"
    c_sep = "rgb(50,60,80)"
    c_dim = "rgb(80,80,100)"

    body_lines: list = []
    wrap_w = max(40, width - len(_PAD) - 14)

    def _wrap_append(text: str, style: str, indent: str = "", *, hang: bool = False) -> None:
        """Word-wrap text into body_lines.

        ``hang`` puts ``indent`` on the continuation lines only — for a paragraph
        that starts at the page margin and whose overflow should sit under it.
        Without it the indent applies to every line, which is what the callers
        passing an already-split tail want.
        """
        # Strip markdown bold markers for display
        text = _re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        words = text.split()
        buf = ""
        _n = 0

        def _emit(chunk: str) -> None:
            nonlocal _n
            pre = "" if (hang and _n == 0) else indent
            body_lines.append(Text(_PAD + pre + chunk, style=style, justify="left"))
            _n += 1

        for word in words:
            if buf and len(buf) + len(word) + 1 > wrap_w:
                _emit(buf)
                buf = word
            else:
                buf = (buf + " " + word).strip()
        if buf:
            _emit(buf)

    def _label_row(label: str, sep: str, value: str, label_style: str, value_style: str, indent: str = "  ") -> None:
        """A "label: value" row whose overflow keeps a hanging indent.

        Rich has no hanging indent, so a single Text that overruns wraps its tail
        to column zero — on a page that indents every line by PAD, the tail lands
        further LEFT than the sentence it belongs to and reads as a new block.
        Wrapping it here keeps the continuation under the value instead.
        """
        head = len(label) + len(sep)
        first_room = max(10, wrap_w - head)
        rest_room = max(10, wrap_w - len(indent))
        lines: list[str] = []
        buf = ""
        for word in value.split():
            room = first_room if not lines else rest_room
            if buf and len(buf) + len(word) + 1 > room:
                lines.append(buf)
                buf = word
            else:
                buf = (buf + " " + word).strip()
        if buf or not lines:
            lines.append(buf)
        row = Text(_PAD, justify="left")
        row.append(label, style=label_style)
        if lines[0]:
            row.append(sep + lines[0], style=value_style)
        body_lines.append(row)
        for extra in lines[1:]:
            body_lines.append(Text(_PAD + indent + extra, style=value_style, justify="left"))

    def _styled_bullet(text: str) -> None:
        """Parse a markdown bullet line into styled Rich Text."""
        # Strip leading "- "
        text = text.strip()
        if text.startswith("- "):
            text = text[2:].strip()

        # Strip markdown bold from entire text for processing
        clean = _re.sub(r"\*\*([^*]+)\*\*", r"\1", text)

        # Pattern: "**N pt**: description" — point calibration
        pt_match = _re.match(r"(\d+)\s*pt\b[s]?[*]*:\s*(.*)", clean)
        if pt_match:
            pts, desc = pt_match.group(1), pt_match.group(2)
            row = Text(_PAD, justify="left")
            row.append(f"{pts} pt", style=f"bold {c_accent}")
            row.append("  ", style=c_dim)
            # Wrap long descriptions
            if len(desc) > wrap_w - 10:
                row.append(desc[: wrap_w - 10], style=c_value)
                body_lines.append(row)
                _wrap_append(desc[wrap_w - 10 :], c_value, indent="  ")
            else:
                row.append(desc, style=c_value)
                body_lines.append(row)
            return

        # Pattern: "**label** stories: stats" — discipline shape
        disc_match = _re.match(r"(\w[\w\-]*)\s+stories:\s*(.*)", clean)
        if disc_match:
            disc, stats = disc_match.group(1), disc_match.group(2)
            _label_row(f"{disc:<16s}", "", stats, c_label, c_muted, indent=" " * 16)
            return

        # Pattern: "label — value" or "label: value"
        for sep in [" — ", "\u2014", ": "]:
            if sep in clean:
                parts = clean.split(sep, 1)
                lbl, val = parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""
                _label_row(lbl, "  ", val, c_label, c_value)
                return

        # Fallback: plain bullet
        _wrap_append(clean, c_value)

    for line in instructions_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # ## Section header
        if stripped.startswith("## "):
            body_lines.append(Text(""))
            title_text = stripped.lstrip("#").strip().rstrip(":")
            body_lines.append(Text(_PAD + title_text, style=c_section, justify="left"))
            body_lines.append(Text(_PAD + "\u2500" * min(len(title_text), 40), style=c_sep, justify="left"))
            continue

        # ### Subsection header
        if stripped.startswith("### "):
            body_lines.append(Text(""))
            body_lines.append(Text(_PAD + stripped.lstrip("#").strip().rstrip(":"), style=c_subsection, justify="left"))
            continue

        # → Arrow directives
        if stripped.startswith("\u2192") or stripped.startswith("→"):
            clean = _re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)
            _wrap_append(clean, f"bold {c_arrow}", indent="  ", hang=True)
            continue

        # Bullet items
        if stripped.startswith("- "):
            _styled_bullet(stripped)
            continue

        # Standalone key: value lines (e.g. "Velocity: 14 ± 7")
        if ":" in stripped and not stripped.startswith("Weight"):
            clean = _re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)
            k, _, v = clean.partition(":")
            _label_row(k.strip(), ": ", v.strip(), f"bold {c_warn}", c_value)
            continue

        # Fallback: plain text
        clean = _re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)
        _wrap_append(clean, c_muted)

    return _hang_wrap(body_lines, width)


def _build_instructions_review_screen(
    instructions_text: str,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    editing: bool = False,
    ready: tuple[bool, ...] | None = None,
) -> Panel:
    """Build the page for this body on its own."""
    return _build_analysis_review_screen(
        _instructions_body(instructions_text, width=width),
        stage_index=0,
        scroll_offset=scroll_offset,
        scroll_meta=scroll_meta,
        width=width,
        height=height,
        action_sel=action_sel,
        actions=["Accept", "Edit", "Export"],
        subtitle="Review planning instructions",
        ready=ready,
    )


def _sample_epic_body(epic: dict, *, width: int = 80, examples: dict | None = None) -> list:
    """The sample epic body — the card, then why it matches the team.

    Shows the generated epic card with description sections properly parsed,
    followed by a compact "why this matches" rationale and pattern summary.

    Split out from the screen builder so the preview page can compose it
    beside another column instead of only as a page of its own.
    """
    c_accent = "#22c55e"
    c_muted = "rgb(120,120,140)"
    c_value = "bold white"
    c_id = "cyan"
    c_desc = "rgb(160,160,160)"
    c_sep = "rgb(40,40,50)"
    c_section = f"bold {c_accent}"
    c_label = "rgb(220,180,60)"
    c_dim = "dim"

    _ex = examples or {}
    body_lines: list = []
    wrap_w = max(40, width - len(_PAD) - 14)

    def _wrap_text(text: str, style: str, indent: str = "    ") -> None:
        """Word-wrap text into body_lines with given style and indent."""
        words = text.split()
        line_buf = ""
        for word in words:
            if line_buf and len(line_buf) + len(word) + 1 > wrap_w:
                body_lines.append(Text(_PAD + indent + line_buf, style=style, justify="left"))
                line_buf = word
            else:
                line_buf = (line_buf + " " + word).strip()
        if line_buf:
            body_lines.append(Text(_PAD + indent + line_buf, style=style, justify="left"))

    # ── Epic Header ───────────────────────────────────────────────
    title = epic.get("title", "Sample Epic")
    priority = epic.get("priority", "high")
    _prio_colors = {"critical": "bold red", "high": "yellow", "medium": "rgb(70,100,180)", "low": "dim"}
    _prio_style = _prio_colors.get(priority, "yellow")

    hdr = Text(_PAD, justify="left")
    hdr.append("[F1]", style=c_id)
    hdr.append("  \u00b7  ", style=c_dim)
    hdr.append(title, style=c_value)
    hdr.append("  \u00b7  ", style=c_dim)
    hdr.append(priority, style=_prio_style)
    body_lines.append(hdr)

    # Metadata line
    stories_est = epic.get("stories_estimate", 0)
    points_est = epic.get("points_estimate", 0)
    meta = Text(_PAD, justify="left")
    meta.append(f"~{stories_est} stories", style=c_muted)
    meta.append("  \u00b7  ", style=c_dim)
    meta.append(f"~{points_est} story points", style=c_muted)
    body_lines.append(meta)
    body_lines.append(Text(_PAD + "\u2500" * min(40, wrap_w), style=c_sep, justify="left"))
    body_lines.append(Text(""))

    # ── Description — parse section markers into styled blocks ──
    desc = epic.get("description", "")
    if desc:
        import re as _re

        # Try **Bold** markers first, then ## Heading markers
        _section_re = _re.compile(r"\*\*([^*]+)\*\*\s*")
        parts = _section_re.split(desc)
        if len(parts) <= 2:
            # No **bold** markers — try ## heading markers
            _heading_re = _re.compile(r"#{1,3}\s+([^\n?]+\??)\s*")
            parts = _heading_re.split(desc)

        if len(parts) > 2:
            # parts = [text_before, section_title, section_body, title2, body2, ...]
            if parts[0].strip():
                _wrap_text(parts[0].strip(), c_desc, indent="")
                body_lines.append(Text(""))

            i = 1
            while i < len(parts) - 1:
                section_title = parts[i].strip().rstrip("?")
                section_body = parts[i + 1].strip() if i + 1 < len(parts) else ""
                body_lines.append(Text(_PAD + section_title, style=f"bold {c_label}", justify="left"))
                if section_body:
                    _wrap_text(section_body, c_desc, indent="")
                body_lines.append(Text(""))
                i += 2
        else:
            # No section markers at all — show raw description
            _wrap_text(desc, c_desc, indent="")
            body_lines.append(Text(""))
    else:
        body_lines.append(Text(_PAD + "No description provided.", style=c_muted, justify="left"))
        body_lines.append(Text(""))

    # ── Rationale ─────────────────────────────────────────────────
    rationale = epic.get("rationale", "")
    if rationale:
        body_lines.append(Text(_PAD + "Why this matches your team", style=c_section, justify="left"))
        _wrap_text(rationale, c_muted, indent="")
        body_lines.append(Text(""))

    # ── Pattern Summary (compact) ─────────────────────────────────
    _naming = _ex.get("naming_conventions", {})
    _epic_style = _naming.get("epic_naming_style", "")
    _epic_ex = _naming.get("epic_examples", [])

    if _epic_style or _epic_ex:
        body_lines.append(Text(_PAD + "Team Patterns", style=c_section, justify="left"))
        if _epic_style:
            row = Text(_PAD, justify="left")
            row.append("Naming: ", style=c_dim)
            row.append(_epic_style, style=c_muted)
            body_lines.append(row)
        if _epic_ex:
            row = Text(_PAD, justify="left")
            row.append("Examples: ", style=c_dim)
            row.append(", ".join(f'"{e}"' for e in _epic_ex[:3]), style=c_muted)
            body_lines.append(row)

    return body_lines


def _build_sample_epic_screen(
    epic: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    examples: dict | None = None,
    ready: tuple[bool, ...] | None = None,
) -> Panel:
    """Build the page for this body on its own."""
    return _build_analysis_review_screen(
        _sample_epic_body(epic, width=width, examples=examples),
        stage_index=1,
        scroll_offset=scroll_offset,
        scroll_meta=scroll_meta,
        width=width,
        height=height,
        action_sel=action_sel,
        subtitle=_PREVIEW_SUBTITLES[0],
        ready=ready,
    )


def _sample_stories_body(
    stories: list[dict], *, width: int = 80, epic_title: str = "", examples: dict | None = None
) -> list:
    """The sample stories body — one card per story.


    Split out from the screen builder so the preview page can compose it
    beside another column instead of only as a page of its own.
    """
    c_accent = "#22c55e"
    c_id = "cyan"
    c_muted = "rgb(120,120,140)"
    c_desc = "rgb(160,160,160)"
    c_sep = "rgb(40,40,50)"
    c_section = f"bold {c_accent}"
    c_given = "rgb(100,180,100)"
    c_when = "rgb(220,180,60)"
    c_then = "rgb(100,140,220)"
    _prio_colors = {
        "critical": "bold red",
        "high": "yellow",
        "medium": "rgb(70,100,180)",
        "low": "dim",
    }

    body_lines: list = []
    max_w = max(40, width - len(_PAD) - 12)

    # Pattern breakdown
    body_lines.append(Text(_PAD + "Story Design Patterns", style=c_section, justify="left"))
    if epic_title:
        body_lines.append(Text(_PAD + f"Epic: {epic_title}", style=c_muted, justify="left"))
    body_lines.append(
        Text(
            _PAD + f"{len(stories)} sample stories generated",
            style=c_muted,
            justify="left",
        )
    )
    body_lines.append(Text(""))
    body_lines.append(Text(_PAD + "\u2500" * 36, style=c_sep, justify="left"))
    body_lines.append(Text(""))

    # Story cards
    for idx, story in enumerate(stories):
        sid = story.get("id", f"S{idx + 1}")
        title = story.get("title", "")
        pts = story.get("story_points", 3)
        priority = story.get("priority", "medium")
        discipline = story.get("discipline", "fullstack")
        persona = story.get("persona", "user")
        goal = story.get("goal", "")
        benefit = story.get("benefit", "")

        # Header: S1 · 3 pts · high · infrastructure
        hdr = Text(_PAD, justify="left")
        hdr.append(sid, style=c_id)
        hdr.append("  \u00b7  ", style="dim")
        hdr.append(f"{pts} pts", style="dim")
        hdr.append("  \u00b7  ", style="dim")
        hdr.append(priority, style=_prio_colors.get(priority, "yellow"))
        hdr.append("  \u00b7  ", style="dim")
        hdr.append(discipline, style="dim")
        body_lines.append(hdr)

        if title:
            body_lines.append(Text(_PAD + f"{title}", style="bold white", justify="left"))

        # Description
        body_lines.append(Text(_PAD + "Description", style=f"bold {c_muted}", justify="left"))
        story_text = f"As a {persona}, I want to {goal}, so that {benefit}."
        words = story_text.split()
        buf = ""
        for word in words:
            if len(buf) + len(word) + 1 > max_w:
                body_lines.append(Text(_PAD + "" + buf, style=c_desc, justify="left"))
                buf = word
            else:
                buf = (buf + " " + word).strip()
        if buf:
            body_lines.append(Text(_PAD + "" + buf, style=c_desc, justify="left"))

        # Acceptance Criteria
        acs = story.get("acceptance_criteria", [])
        if acs:
            body_lines.append(Text(""))
            body_lines.append(Text(_PAD + "Acceptance Criteria", style=f"bold {c_muted}", justify="left"))
            for ac in acs[:3]:
                if isinstance(ac, dict):
                    for kw, style in [("given", c_given), ("when", c_when), ("then", c_then)]:
                        val = ac.get(kw, "")
                        if val:
                            row = Text(_PAD + "", justify="left")
                            row.append(f"{kw.capitalize():5s} ", style=f"bold {style}")
                            row.append(val, style=c_desc)
                            body_lines.append(row)
                    body_lines.append(Text(""))

        # Definition of Done — from LLM response, or fall back to team's proposed DoD
        dod = story.get("definition_of_done", [])
        if not dod and examples:
            proposed = examples.get("proposed_dod", {})
            if isinstance(proposed, dict):
                dod = [
                    it["practice"]
                    for it in proposed.get("items", [])
                    if isinstance(it, dict) and it.get("status") in ("established", "emerging")
                ]
        if dod:
            body_lines.append(Text(_PAD + "Definition of Done", style=f"bold {c_muted}", justify="left"))
            for item in dod:
                row = Text(_PAD + "", justify="left")
                row.append("\u2713 ", style="rgb(80,180,80)")
                row.append(str(item), style=c_desc)
                body_lines.append(row)
            body_lines.append(Text(""))

        if idx < len(stories) - 1:
            body_lines.append(Text(_PAD + "\u2500" * 36, style=c_sep, justify="left"))
            body_lines.append(Text(""))

    return body_lines


def _build_sample_stories_screen(
    stories: list[dict],
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    epic_title: str = "",
    examples: dict | None = None,
    ready: tuple[bool, ...] | None = None,
) -> Panel:
    """Build the page for this body on its own."""
    return _build_analysis_review_screen(
        _sample_stories_body(stories, width=width, epic_title=epic_title, examples=examples),
        stage_index=2,
        scroll_offset=scroll_offset,
        scroll_meta=scroll_meta,
        width=width,
        height=height,
        action_sel=action_sel,
        subtitle=_PREVIEW_SUBTITLES[1],
        ready=ready,
    )


def _sample_tasks_body(tasks: list[dict], *, width: int = 80, stories: list[dict] | None = None) -> list:
    """The sample tasks body — tasks grouped under their story.


    Split out from the screen builder so the preview page can compose it
    beside another column instead of only as a page of its own.
    """
    c_accent = "#22c55e"
    c_id = "cyan"
    c_muted = "rgb(120,120,140)"
    c_desc = "rgb(160,160,160)"
    c_sep = "rgb(40,40,50)"
    c_section = f"bold {c_accent}"
    _label_colors = {
        "code": "rgb(100,140,220)",
        "testing": "rgb(220,180,60)",
        "documentation": "rgb(160,100,220)",
        "infrastructure": "rgb(100,180,100)",
    }

    body_lines: list = []
    max_w = max(40, width - len(_PAD) - 12)

    # Group tasks by story
    _by_story: dict[str, list[dict]] = {}
    for t in tasks:
        sid = t.get("story_id", "?")
        _by_story.setdefault(sid, []).append(t)

    # Pattern breakdown
    body_lines.append(
        Text(
            _PAD + "Task Decomposition Preview",
            style=c_section,
            justify="left",
        )
    )
    body_lines.append(
        Text(
            _PAD + f"{len(tasks)} tasks across {len(_by_story)} stories",
            style=c_muted,
            justify="left",
        )
    )
    body_lines.append(Text(""))
    body_lines.append(Text(_PAD + "\u2500" * 36, style=c_sep, justify="left"))
    body_lines.append(Text(""))

    # Render tasks grouped by story
    # Build story title lookup
    _story_titles: dict[str, str] = {}
    if stories:
        for s in stories:
            _story_titles[s.get("id", "")] = s.get("title", "")

    for s_idx, (sid, story_tasks) in enumerate(_by_story.items()):
        # Story header with title
        hdr = Text(_PAD, justify="left")
        hdr.append(sid, style=f"bold {c_id}")
        story_title = _story_titles.get(sid, "")
        if story_title:
            hdr.append(f"  {story_title}", style="bold white")
        hdr.append(f"  ({len(story_tasks)} tasks)", style="dim")
        body_lines.append(hdr)
        body_lines.append(Text(""))

        for t in story_tasks:
            tid = t.get("id", "T-?")
            title = t.get("title", "")
            label = t.get("label", "Code")
            desc = t.get("description", "")
            test_plan = t.get("test_plan", "")
            label_sty = _label_colors.get(label.lower(), c_muted)

            # Task header: T-S1-01 · [Code] · Title
            row = Text(_PAD, justify="left")
            row.append(tid, style=c_id)
            row.append("  ", style="dim")
            row.append(f"[{label}]", style=label_sty)
            row.append("  ", style="dim")
            row.append(title, style="bold white")
            body_lines.append(row)

            # Description (wrapped)
            if desc:
                words = desc.split()
                buf = ""
                for word in words:
                    if len(buf) + len(word) + 1 > max_w:
                        body_lines.append(
                            Text(
                                _PAD + "   " + buf,
                                style=c_desc,
                                justify="left",
                            )
                        )
                        buf = word
                    else:
                        buf = (buf + " " + word).strip()
                if buf:
                    body_lines.append(
                        Text(
                            _PAD + "   " + buf,
                            style=c_desc,
                            justify="left",
                        )
                    )

            # Test plan
            if test_plan:
                tp_row = Text(_PAD + "   ", justify="left")
                tp_row.append("Test: ", style="bold rgb(220,180,60)")
                tp_row.append(test_plan[:60], style=c_desc)
                body_lines.append(tp_row)

            body_lines.append(Text(""))

        if s_idx < len(_by_story) - 1:
            body_lines.append(
                Text(
                    _PAD + "\u2500" * 36,
                    style=c_sep,
                    justify="left",
                )
            )
            body_lines.append(Text(""))

    return body_lines


def _build_sample_tasks_screen(
    tasks: list[dict],
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    stories: list[dict] | None = None,
    ready: tuple[bool, ...] | None = None,
) -> Panel:
    """Build the page for this body on its own."""
    return _build_analysis_review_screen(
        _sample_tasks_body(tasks, width=width, stories=stories),
        stage_index=3,
        scroll_offset=scroll_offset,
        scroll_meta=scroll_meta,
        width=width,
        height=height,
        action_sel=action_sel,
        subtitle=_PREVIEW_SUBTITLES[2],
        ready=ready,
    )


def _sample_sprint_body(sprint: dict, stories: list[dict], *, width: int = 80) -> list:
    """The sample sprint body — the plan, its capacity notes and its risks.


    Split out from the screen builder so the preview page can compose it
    beside another column instead of only as a page of its own.
    """
    c_accent = "#22c55e"
    c_muted = "rgb(120,120,140)"
    c_desc = "rgb(160,160,160)"
    c_sep = "rgb(40,40,50)"
    c_section = f"bold {c_accent}"
    c_standalone = "rgb(220,180,60)"

    body_lines: list = []
    max_w = max(40, width - len(_PAD) - 12)

    # Sprint header
    sprint_name = sprint.get("sprint_name", "Sprint 1")
    vel_target = sprint.get("velocity_target", 0)
    total_pts = sprint.get("total_points", 0)

    body_lines.append(
        Text(
            _PAD + "Sprint Plan Preview",
            style=c_section,
            justify="left",
        )
    )
    body_lines.append(Text(""))

    # Sprint card
    hdr = Text(_PAD, justify="left")
    hdr.append(sprint_name, style="bold white")
    hdr.append(f"  \u00b7  {total_pts} pts", style=c_muted)
    hdr.append(f"  \u00b7  capacity {vel_target} pts", style=c_muted)
    body_lines.append(hdr)
    body_lines.append(Text(""))

    # Capacity notes
    cap_notes = sprint.get("capacity_notes", "")
    if cap_notes:
        body_lines.append(
            Text(
                _PAD + f"{cap_notes}",
                style=c_standalone,
                justify="left",
            )
        )
        body_lines.append(Text(""))

    # Stories in sprint
    included = sprint.get("stories_included", [])
    if included:
        body_lines.append(
            Text(
                _PAD + "Stories included:",
                style=f"bold {c_muted}",
                justify="left",
            )
        )
        for sid in included:
            # Find matching story
            story = next((s for s in stories if s.get("id") == sid), None)
            row = Text(_PAD, justify="left")
            row.append(sid, style="cyan")
            if story:
                row.append(f"  {story.get('title', '')}  ", style="white")
                row.append(f"{story.get('story_points', '?')} pts", style="dim")
            body_lines.append(row)
        body_lines.append(Text(""))

    # Utilisation
    if vel_target > 0 and total_pts > 0:
        util_pct = round(total_pts / vel_target * 100)
        util_style = c_accent if 70 <= util_pct <= 90 else (c_standalone if util_pct < 70 else "bold red")
        body_lines.append(
            Text(
                _PAD + f"Sprint utilisation: {util_pct}%",
                style=util_style,
                justify="left",
            )
        )
        body_lines.append(Text(""))

    # Risks
    risks = sprint.get("risks", [])
    if risks:
        body_lines.append(Text(_PAD + "\u2500" * 36, style=c_sep, justify="left"))
        body_lines.append(Text(""))
        body_lines.append(Text(_PAD + "Risks:", style=f"bold {c_standalone}", justify="left"))
        for risk in risks[:5]:
            body_lines.append(Text(_PAD + f"\u26a0 {risk}", style=c_desc, justify="left"))
        body_lines.append(Text(""))

    # Rationale
    rationale = sprint.get("rationale", "")
    if rationale:
        body_lines.append(Text(_PAD + "\u2500" * 36, style=c_sep, justify="left"))
        body_lines.append(Text(""))
        body_lines.append(
            Text(
                _PAD + "Why this sprint plan matches your team",
                style=f"bold {c_muted}",
                justify="left",
            )
        )
        words = rationale.split()
        buf = ""
        for word in words:
            if len(buf) + len(word) + 1 > max_w:
                body_lines.append(Text(_PAD + buf, style=c_desc, justify="left"))
                buf = word
            else:
                buf = (buf + " " + word).strip()
        if buf:
            body_lines.append(Text(_PAD + buf, style=c_desc, justify="left"))

    return body_lines


def _build_sample_sprint_screen(
    sprint: dict,
    stories: list[dict],
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    ready: tuple[bool, ...] | None = None,
) -> Panel:
    """Build the page for this body on its own."""
    return _build_analysis_review_screen(
        _sample_sprint_body(sprint, stories, width=width),
        stage_index=4,
        scroll_offset=scroll_offset,
        scroll_meta=scroll_meta,
        width=width,
        height=height,
        action_sel=action_sel,
        actions=_PREVIEW_ACTIONS[3],
        subtitle=_PREVIEW_SUBTITLES[3],
        ready=ready,
    )


def _build_intake_screen(
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float = 0.0,
    desc_reveal: float = 0.0,
    visible_items: int = -1,
) -> Panel:
    """Build the intake mode selection screen with Planning title pinned at top.

    Shown after the user selects '+ New Project' on the project list.
    Uses the same ASCII art + shimmer + typewriter pattern as the top-level mode screen.
    visible_items: how many intake options to show (-1 = all). For staggered fade-in.
    """
    # Planning title pinned at top
    title = planning_title()

    sub = Text(_PAD + "Select intake mode", style="dim", justify="left")

    # Intake option rows — same rendering as mode rows
    show_n = len(_INTAKE_CARDS) if visible_items < 0 else min(visible_items, len(_INTAKE_CARDS))
    body: list = []
    body_h = 0

    for i in range(show_n):
        card = _INTAKE_CARDS[i]
        is_sel = i == selected
        items = _build_mode_row(
            card,
            selected=is_sel,
            shimmer_tick=shimmer_tick,
            desc_reveal=desc_reveal if is_sel else 0,
        )
        body.extend(items)
        body_h += 2 + (2 if is_sel else 0)
        if i < show_n - 1:
            body.append(Text(""))
            body_h += 1

    # Layout: blank + title(2) + blank + subtitle + blank + [body]
    inner_h = height - 4
    header_h = 6  # blank + title(2) + blank + subtitle + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return build_page_panel(content, theme=PLANNING_THEME, height=height)


def _build_offline_screen(
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float = 0.0,
    desc_reveal: float = 0.0,
    visible_items: int = -1,
) -> Panel:
    """Build the offline sub-menu screen with Planning title pinned at top.

    Shown after the user selects 'Offline' on the intake screen.
    Uses the same ASCII art + shimmer + typewriter pattern as the intake mode screen.
    visible_items: how many offline options to show (-1 = all). For staggered reveal.
    """
    # Planning title pinned at top
    title = planning_title()

    sub = Text(_PAD + "Offline questionnaire", style="dim", justify="left")

    # Offline option rows — same rendering as mode rows
    show_n = len(_OFFLINE_CARDS) if visible_items < 0 else min(visible_items, len(_OFFLINE_CARDS))
    body: list = []
    body_h = 0

    for i in range(show_n):
        card = _OFFLINE_CARDS[i]
        is_sel = i == selected
        items = _build_mode_row(
            card,
            selected=is_sel,
            shimmer_tick=shimmer_tick,
            desc_reveal=desc_reveal if is_sel else 0,
        )
        body.extend(items)
        body_h += 2 + (2 if is_sel else 0)
        if i < show_n - 1:
            body.append(Text(""))
            body_h += 1

    # Layout: blank + title(2) + blank + subtitle + blank + [body]
    inner_h = height - 4
    header_h = 6  # blank + title(2) + blank + subtitle + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return build_page_panel(content, theme=PLANNING_THEME, height=height)


def _build_export_success_screen(
    file_path: str,
    *,
    width: int = 80,
    height: int = 24,
) -> Panel:
    """Build the export success screen with Planning title pinned at top.

    Shown after a blank questionnaire template is exported.
    Displays confirmation, file path, and a hint to re-run the agent.
    """
    # Planning title pinned at top
    title = planning_title()

    # Success message body
    body: list = []
    body.append(Text(_PAD + "Questionnaire exported", style="bold bright_green", justify="left"))
    body.append(Text(""))
    body.append(Text(_PAD + f"Saved to: {file_path}", style="white", justify="left"))
    body.append(Text(""))
    body.append(
        Text(
            _PAD + "Fill it in at your own pace, then re-run the agent and select Import.",
            style="dim",
            justify="left",
        )
    )
    body.append(Text(""))
    body.append(Text(_PAD + "Press any key to exit.", style="dim", justify="left"))
    body_h = 7

    # Layout: blank + title(2) + blank + [body]
    inner_h = height - 4
    header_h = 4  # blank + title(2) + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return build_page_panel(content, theme=PLANNING_THEME, height=height)


def _build_import_screen(
    input_value: str,
    *,
    width: int = 80,
    height: int = 24,
    error: str = "",
    placeholder: str = "scrum-questionnaire.md",
) -> Panel:
    """Build the import file path input screen with Planning title pinned at top.

    Shown when the user selects 'Import' from the offline sub-menu.
    Same text input pattern as provider_select.py API key input.
    """
    # Planning title pinned at top
    title = planning_title()

    sub = Text(_PAD + "Import questionnaire", style="dim", justify="left")

    # Input box
    box_w = min(70, width - 16)
    box_inner_w = box_w - 2 - 4  # panel border(2) + padding(4)

    if input_value:
        display = input_value + "\u2588"
        text_style = "bold white"
    else:
        display = placeholder + "\u2588"
        text_style = "rgb(80,80,80)"

    avail = box_inner_w - 4
    input_content = Text(justify="left", no_wrap=True, overflow="crop")
    if len(display) <= avail:
        input_content.append("  " + display, style=text_style)
    else:
        visible = display[-(avail - 1) :]
        input_content.append(" \u25c2", style="dim")
        input_content.append(visible, style=text_style)

    if error:
        border_color = "bright_red"
    else:
        border_color = "white"

    input_box = Panel(
        input_content,
        title=" File path ",
        title_align="left",
        border_style=border_color,
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=box_w,
    )

    # Error text
    error_text = Text(_PAD + error, style="bright_red", justify="left") if error else Text("")

    # Hint
    hint = Text(
        _PAD + "Enter path to a filled .md questionnaire file. Press Enter to confirm.",
        style="dim",
        justify="left",
    )

    body: list = [
        Padding(input_box, (0, 0, 0, len(_PAD))),
        error_text,
        Text(""),
        hint,
    ]
    body_h = 8  # input_box(5) + error(1) + blank + hint(1)

    # Layout: blank + title(2) + blank + subtitle + blank + [body]
    inner_h = height - 4
    header_h = 6  # blank + title(2) + blank + subtitle + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return build_page_panel(content, theme=PLANNING_THEME, height=height)


def _with_bubble_room(panel, width: int):
    """Opt this page into the shared duck bubble (ordinary lines are opt-in).

    Only pages whose right side is dependably free declare a room; everywhere
    else the duck still quacks but never draws a bubble over content.
    """
    from yeaboi.ui.shared._duck_voice import default_bubble_room

    panel._bubble_room = default_bubble_room(width)
    return panel


# Braille spinner for the active progress row — the same cadence the planning
# chat's build checklist uses, so every loading screen animates identically.
_ACTIVITY_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Per-stage first-seen clock, keyed by component id. The progress events carry
# no timestamps, so the renderer notes when it first saw each stage (in
# anim_tick time) to show a per-stage elapsed. Cleared whenever anim_tick jumps
# backwards — that's a new run starting its clock at zero.
_activity_first_seen: dict[str, float] = {}
_activity_last_tick = 0.0


def _activity_stage_elapsed(component_id: str, anim_tick: float) -> float:
    global _activity_last_tick
    if anim_tick < _activity_last_tick - 1.0:
        _activity_first_seen.clear()
    _activity_last_tick = anim_tick
    return anim_tick - _activity_first_seen.setdefault(component_id, anim_tick)


def _fmt_mmss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _build_activity_progress_rows(
    progress: list,
    *,
    theme,
    anim_tick: float,
) -> list[Text]:
    """Render honest, theme-aware lifecycle rows for a worker's activity.

    Structured component events carry an authoritative lifecycle state. Plain
    string callbacks only announce that work started, so earlier strings remain
    activity history instead of being incorrectly promoted to "completed".
    Active rows spin (braille, like the chat's build checklist) and carry a
    per-stage elapsed; structured runs get a ``[n/total] · total m:ss`` footer.
    """
    from yeaboi.analysis.progress import is_component_progress

    component_states: dict[str, dict] = {}
    component_order: list[str] = []
    legacy_activity: list[str] = []
    for item in progress:
        if is_component_progress(item):
            component_id = item["component_id"]
            if component_id not in component_states:
                component_order.append(component_id)
            component_states[component_id] = item
        elif isinstance(item, str):
            legacy_activity.append(item)

    dots = "." * (int(anim_tick * 2) % 4)
    spin = _ACTIVITY_SPINNER[int(anim_tick * 10) % len(_ACTIVITY_SPINNER)]
    rows: list[Text] = []
    if component_order:
        for component_id in component_order:
            event = component_states[component_id]
            status = event["status"]
            label = event["label"]
            detail = str(event.get("detail", "") or "")
            if status == "completed":
                marker, style, suffix = "✓", theme.accent, f" · {detail}" if detail else ""
            elif status == "partial":
                partial_detail = f" · {detail}" if detail else ""
                marker, style, suffix = "!", theme.warn, f" · partial{partial_detail}"
            elif status == "no_data":
                no_data_detail = f" · {detail}" if detail else ""
                marker, style, suffix = "○", theme.muted, f" · no matching data{no_data_detail}"
            elif status == "fallback":
                fallback_detail = f" ({detail})" if detail else ""
                marker, style, suffix = "~", theme.warn, f" · deterministic fallback{fallback_detail}"
            elif status == "failed":
                marker, style, suffix = "✗", theme.bad, f" · {detail}" if detail else ""
            else:
                phase = str(event.get("phase", "") or "")
                current = event.get("current")
                total = event.get("total")
                unit = str(event.get("unit", "") or "")
                secondary_count = event.get("secondary_count")
                secondary_unit = str(event.get("secondary_unit", "") or "")
                parts: list[str] = []
                if phase:
                    parts.append(phase)
                if isinstance(current, int) and isinstance(total, int) and total > 0:
                    pct = min(100, max(0, round(current / total * 100)))
                    count_label = f"{current:,}/{total:,}"
                    if unit:
                        count_label += f" {unit}"
                    parts.append(f"{pct}% · {count_label}")
                elif isinstance(current, int) and unit:
                    parts.append(f"{current:,} {unit}")
                if isinstance(secondary_count, int) and secondary_unit:
                    parts.append(f"{secondary_count:,} {secondary_unit}")
                if detail:
                    parts.append(detail)
                parts.append(_fmt_mmss(_activity_stage_elapsed(component_id, anim_tick)))
                suffix = f"{dots} · " + " · ".join(parts) if parts else dots
                marker, style = spin, f"bold {theme.accent_bright}"
            rows.append(Text(_PAD + f"  {marker} {label}{suffix}", style=style, justify="left"))

        if any(bool(component_states[item].get("read_only")) for item in component_order):
            rows.append(
                Text(
                    _PAD + "      Repository access is read-only — no files are modified.",
                    style=theme.muted,
                    justify="left",
                )
            )
        if legacy_activity:
            rows.append(Text(_PAD + f"      ↳ {legacy_activity[-1]}", style=theme.muted, justify="left"))
        _terminal = {"completed", "partial", "no_data", "fallback", "failed"}
        resolved = sum(1 for c in component_order if component_states[c]["status"] in _terminal)
        rows.append(
            Text(
                _PAD + f"  [{resolved}/{len(component_order)}] · total {_fmt_mmss(anim_tick)}",
                style=theme.muted,
                justify="left",
            )
        )
        return rows

    for activity in legacy_activity[:-1]:
        rows.append(Text(_PAD + f"  • {activity}", style=theme.accent, justify="left"))
    if legacy_activity:
        rows.append(
            Text(
                _PAD + f"  {spin} {legacy_activity[-1]}{dots}",
                style=f"bold {theme.accent_bright}",
                justify="left",
            )
        )
    return rows


def _build_analysis_progress_screen(
    progress: list,
    *,
    width: int = 80,
    height: int = 24,
    elapsed: float = 0.0,
    anim_tick: float = 0.0,
    source: str = "",
    mode: str = "planning",
) -> Panel:
    """Build the team analysis progress screen with spinner and step indicators.

    Shows a visual progress display while the analysis thread runs in the background.
    """
    from yeaboi.ui.shared._components import analysis_title

    theme = ANALYSIS_THEME if mode == "analysis" else PLANNING_THEME
    title = analysis_title() if mode == "analysis" else planning_title()

    # Spinner frames
    _spinners = ["\u25d0", "\u25d3", "\u25d1", "\u25d2"]
    spinner = _spinners[int(anim_tick * 4) % len(_spinners)]

    # Elapsed time display
    mins = int(elapsed) // 60
    secs = int(elapsed) % 60
    time_str = f"{mins}:{secs:02d}" if mins > 0 else f"{secs}s"

    # Header
    source_label = f" ({source})" if source else ""
    body: list = [
        Text(
            _PAD + f"{spinner}  Analysing team board{source_label}",
            style=f"bold {theme.accent_bright}",
            justify="left",
        ),
        Text(_PAD + f"   Elapsed: {time_str}", style=theme.dim, justify="left"),
        Text(""),
    ]

    body.extend(_build_activity_progress_rows(progress, theme=theme, anim_tick=anim_tick))

    # Fill remaining space
    body_h = 4 + len(body)
    inner_h = height - 4
    remaining = max(0, inner_h - 4 - body_h)
    body.extend([Text("") for _ in range(remaining)])

    content = Group(Text(""), title, Text(""), *body)

    # The checklist hugs the left gutter, so the loading screen's right side is
    # dependably free for the duck's bubble. Plain white border like every other
    # page: an accent frame reads as the whole terminal lighting up, which is a
    # lot of signal for "a job is running".
    return _with_bubble_room(build_page_panel(content, theme=theme, height=height), width)


def _build_project_export_success_screen(
    file_path: str,
    *,
    width: int = 80,
    height: int = 24,
    subtitle: str = "Plan exported",
    hint: str = "Press any key to continue.",
    mode: str = "planning",
    shimmer_tick: float | None = None,
) -> Panel:
    """Build the project export success/status screen.

    Shown after exporting a project's plan as Markdown and HTML,
    or during/after Jira sync operations. subtitle and hint can
    be customised for different contexts (e.g. loading states).
    shimmer_tick: if set, animates the title's travelling highlight.
    """
    if mode == "analysis":
        from yeaboi.ui.shared._components import analysis_title

        title = analysis_title(shimmer_tick)
    else:
        title = planning_title(shimmer_tick)

    # Subtitle, message body and hint all align on the same column (_PAD + 2)
    # as the heading — the body is not extra-indented.
    body: list = [
        Text(_PAD + "  " + subtitle, style="bold bright_green", justify="left"),
        Text(""),
    ]
    for line in file_path.splitlines():
        body.append(Text(_PAD + f"  {line}", style="white", justify="left"))
    if hint:
        body.extend(
            [
                Text(""),
                Text(_PAD + "  " + hint, style="dim", justify="left"),
            ]
        )
    body_h = 3 + len(file_path.splitlines()) + 2

    inner_h = height - 4
    header_h = 4  # blank + title(2) + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return build_page_panel(content, theme=PLANNING_THEME, height=height)


# ---------------------------------------------------------------------------
# Usage screen
# ---------------------------------------------------------------------------


# Usage section boxes: the narrowest a box may get before the grid drops a column,
# and the most columns it will ever use (three reads well at 200 columns; four
# leaves the label/value rows too cramped).
_USAGE_MIN_BOX_W = 34
_USAGE_MAX_COLS = 3
# The column width at which a ``wide`` Usage section no longer needs its own row:
# the longest thing it carries is a session-DB path, and ~52 columns holds a
# typical one without ellipsizing. Below this they still stack full-width, so a
# normal terminal is unchanged.
_USAGE_WIDE_COL_W = 52


def _render_to_lines(renderable, render_w: int, left_pad: str) -> list:
    """Flatten any renderable to a list of ``Text`` lines.

    Multi-row renderables (a grid of boxed panels, a four-column table) break the
    "one body line == one rendered row" assumption that the flat-list viewport
    math relies on. Rendering them off-screen at a known width and re-emitting the
    result as one ``Text`` per rendered row restores it, so the block scrolls
    line-by-line with everything else. Each line is prefixed with ``left_pad`` so
    the block lines up with the rest of the page content.
    """
    from rich.console import Console as _Console

    _c = _Console(width=render_w, height=400)
    out: list = []
    for seg_line in _c.render_lines(renderable, _c.options.update_width(render_w), pad=True):
        t = Text(left_pad, justify="left")
        for seg in seg_line:
            t.append(seg.text, style=seg.style)
        out.append(t)
    return out


def _build_usage_screen(
    usage_data: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    actions: list[str] | None = None,
    message: str = "",
) -> Panel:
    """Build the usage dashboard screen using shared TUI components.

    Shows API token usage, session history, provider info, and cost estimates.
    Uses USAGE_THEME (amber) with shared buttons, scrollbar, and viewport.
    ``actions`` defaults to ["Back"]; the Copy button passes ["Copy", "Back"].
    """
    from yeaboi.ui.shared._components import USAGE_THEME, build_reveal_subtitle, usage_title

    theme = USAGE_THEME
    title = usage_title(shimmer_tick)
    sub = build_reveal_subtitle("API usage and session history", sub_reveal, pad=_PAD + "  ")

    # The transient status ("Copied to clipboard") is spoken by the companion duck
    # (see _duck_say on the returned panel) rather than taking a body row.
    body_lines: list = []

    # Rows are collected per section rather than into one flat list: each section
    # becomes its own bordered box below, and the boxes are laid out in a grid
    # whose column count follows the terminal width.
    # ``wide`` sections get a full-width box of their own below the grid — their
    # values (timestamps, DB paths) don't fit a third-width column without
    # ellipsizing, which is what made them read as cut off.
    sections: list[tuple[str, list, bool]] = []
    _cur: list = []

    def _heading(text: str, *, wide: bool = False) -> None:
        """Open a new section box. Its title is drawn as the box title; ``wide``
        gives it a full-width row instead of a slot in the column grid."""
        nonlocal _cur
        _cur = []
        sections.append((text, _cur, wide))

    def _row(label: str, value: str, value_style: str = "") -> None:
        # no_wrap + ellipsis: long model names / DB paths crop instead of wrapping,
        # which would give the box an unpredictable height and break the grid.
        r = Text(justify="left", no_wrap=True, overflow="ellipsis")
        r.append(f"{label}:  ", style=theme.muted)
        r.append(str(value), style=value_style or theme.value)
        _cur.append(r)

    def _note(text: str, style: str) -> None:
        _cur.append(Text(text, style=style, justify="left", no_wrap=True, overflow="ellipsis"))

    # ── Provider Info ──────────────────────────────────────────────
    _heading("LLM Provider")
    _row("Provider", usage_data.get("provider", "unknown"))
    _row("Model", usage_data.get("model", "unknown"))
    api_status = usage_data.get("api_key_status", "not configured")
    status_style = theme.good if api_status == "configured" else theme.bad
    _row("API key", api_status, status_style)

    # ── Lifetime Token Usage (persisted across all sessions) ────
    lifetime = usage_data.get("lifetime_tokens", {})
    if lifetime:
        _heading("Lifetime Token Usage")
        _row("Total LLM calls", f"{lifetime.get('calls', 0):,}")
        _row("Input tokens", f"{lifetime.get('input', 0):,}")
        _row("Output tokens", f"{lifetime.get('output', 0):,}")
        _row("Total tokens", f"{lifetime.get('total', 0):,}")
        lt_cost = lifetime.get("estimated_cost", 0.0)
        if lt_cost > 0:
            _row("Estimated total cost", f"${lt_cost:.4f}", theme.warn)
        elif usage_data.get("provider") == "ollama":
            _row("Estimated total cost", "$0.00 — local model, runs on your hardware", theme.good)

    # ── Current Session Usage ─────────────────────────────────────
    _heading("Current Session")
    tokens = usage_data.get("tokens", {})
    if tokens:
        _row("LLM calls", f"{tokens.get('calls', 0):,}")
        _row("Input tokens", f"{tokens.get('input', 0):,}")
        _row("Output tokens", f"{tokens.get('output', 0):,}")
        _row("Total tokens", f"{tokens.get('total', 0):,}")
        cost = tokens.get("estimated_cost", 0.0)
        if cost > 0:
            _row("Session cost", f"${cost:.4f}", theme.warn)
        elif usage_data.get("provider") == "ollama":
            _row("Session cost", "$0.00 — local model", theme.good)
    else:
        _note("No calls in this session yet.", theme.muted)

    if not lifetime and not tokens:
        _note("Token tracking starts when you run analysis or planning.", theme.dim)

    # ── Local Model Performance ───────────────────────────────────
    # Only present once a local (Ollama) call has recorded timing — hidden
    # entirely for cloud-only histories.
    perf = usage_data.get("local_performance", {})
    if perf:
        _heading("Local Model Performance")
        _row("Ollama calls", f"{perf.get('calls', 0):,}")
        _row("Avg speed", f"{perf.get('avg_tps', 0):.1f} tok/s")
        _row("Max speed", f"{perf.get('max_tps', 0):.1f} tok/s")
        _row("Avg call duration", f"{perf.get('avg_duration_ms', 0) / 1000:.1f}s")
        _row("Avg model load", f"{perf.get('avg_load_ms', 0) / 1000:.1f}s")
        last_call = perf.get("last") or {}
        if last_call:
            _row("Last call", f"{last_call.get('model', '?')} · {last_call.get('tps', 0):.1f} tok/s")

    # ── Session History ───────────────────────────────────────────
    _heading("Session History", wide=True)  # timestamps need the full width
    sessions = usage_data.get("sessions", {})
    _row("Total sessions", str(sessions.get("total", 0)))
    _row("Planning sessions", str(sessions.get("planning", 0)))
    _row("Analysis sessions", str(sessions.get("analysis", 0)))
    last = sessions.get("last_used", "")
    if last:
        _row("Last session", last)

    # ── Environment ───────────────────────────────────────────────
    _heading("Environment", wide=True)  # the session DB path needs the full width
    _row("Version", usage_data.get("version", "?"))
    _row("Python", usage_data.get("python_version", "?"))
    langsmith = usage_data.get("langsmith", "disabled")
    ls_style = theme.good if langsmith == "enabled" else theme.dim
    _row("LangSmith", langsmith, ls_style)
    _row("Session DB", usage_data.get("db_path", "~/.scrum-agent/sessions.db"))

    # ── Team Profiles ─────────────────────────────────────────────
    profiles = usage_data.get("profiles", [])
    if profiles:
        _heading("Team Profiles")
        for p in profiles:
            r = Text(justify="left", no_wrap=True, overflow="ellipsis")
            r.append(p.get("name", "?"), style=theme.value)
            r.append(f"  {p.get('source', '')} \u00b7 {p.get('sprints', 0)} sprints", style=theme.muted)
            age = p.get("age", "")
            if age:
                r.append(f"  \u00b7 {age}", style=theme.dim)
            _cur.append(r)

    # ── Section boxes, laid out in an adaptive-width grid ─────────
    # Each section gets its own rounded box, and the boxes sit side by side in a
    # table whose column count comes from the available width (1 when narrow, up
    # to _USAGE_MAX_COLS when wide). Fewer columns beats squashed boxes, so the
    # count drops as soon as a column would fall below _USAGE_MIN_BOX_W.
    # Two columns out from the subtitle, so the box's TEXT lands where the
    # subtitle's does. Lining the border up with the words instead put
    # everything inside the box a column right of everything outside it.
    _grid_indent = _PAD
    grid_w = max(24, width - 4 - len(_grid_indent) - 2)  # panel border/pad + indent + scrollbar gutter
    # A "wide" section is only wide because a timestamp or a DB path won't fit a
    # column on a normal terminal. Once a column clears _USAGE_WIDE_COL_W it will,
    # so the section joins the grid rather than stacking full-width beneath it —
    # otherwise a big screen shows three boxes across the top and then two lonely
    # full-width strips under them, with everything below the fold empty.
    _wide_fits_col = (grid_w - 2 - 2 * (_USAGE_MAX_COLS - 1)) // _USAGE_MAX_COLS >= _USAGE_WIDE_COL_W
    _narrow = [s for s in sections if not s[2] or _wide_fits_col]
    _wide = [s for s in sections if s[2] and not _wide_fits_col]
    n_cols = max(1, min(_USAGE_MAX_COLS, grid_w // _USAGE_MIN_BOX_W, len(_narrow) or 1))
    # padding=(0,1) with pad_edge=False → a 2-column gutter between boxes only.
    # Two columns of slack keep the table clear of the render width (sitting
    # exactly on it wraps the last column).
    col_w = max(20, (grid_w - 2 - 2 * (n_cols - 1)) // n_cols)
    # A wide box spans TWO columns (plus the gutter between them) rather than the
    # whole grid — enough for timestamps and DB paths without dwarfing the row of
    # narrow boxes above it. Capped so it still fits when there are fewer columns.
    full_w = min(grid_w - 2, col_w * 2 + 2)

    def _section_box(sec_title: str, rows: list, box_h: int, box_w: int) -> Panel:
        head = Text(sec_title, style=f"bold {theme.accent}", no_wrap=True, overflow="ellipsis")
        return Panel(
            Group(*(rows or [Text("")])),
            title=head,
            title_align="left",
            box=rich.box.ROUNDED,
            border_style=theme.sep,
            padding=(0, 1),
            width=box_w,
            height=box_h,
        )

    _grid = Table(show_header=False, show_edge=False, box=None, padding=(0, 1), pad_edge=False)
    for _ in range(n_cols):
        _grid.add_column(width=col_w, overflow="crop")
    _rows_added = 0
    for _i in range(0, len(_narrow), n_cols):
        _chunk = _narrow[_i : _i + n_cols]
        if _rows_added:
            _grid.add_row(*[Text("")] * n_cols)  # one blank line between grid rows
        # Boxes in the same row share a height so their bottom borders line up.
        _box_h = max(len(_r) for _, _r, _ in _chunk) + 2
        _cells: list = [_section_box(_t, _r, _box_h, col_w) for _t, _r, _ in _chunk]
        _cells += [Text("")] * (n_cols - len(_chunk))
        _grid.add_row(*_cells)
        _rows_added += 1

    body_lines.append(Text(""))  # the blank the old first heading used to supply
    body_lines.extend(_render_to_lines(_grid, grid_w, _grid_indent))
    # Wide sections stack below the grid, each spanning the full width so long
    # values (timestamps, DB paths) show in full instead of ellipsizing.
    for _t, _r, _ in _wide:
        body_lines.append(Text(""))
        body_lines.extend(_render_to_lines(_section_box(_t, _r, len(_r) + 2, full_w), grid_w, _grid_indent))

    # ── Layout using shared components ────────────────────────────
    # header = blank + title(2) + blank + sub (the grid block leads with its own
    # blank, so no extra blank after sub); action_h = blank + hint + pocket blank.
    # body_lines now holds *rendered* lines (the boxed grid flattened by
    # _render_to_lines), so the one-line-per-entry viewport math below still holds
    # and build_scrollbar gets an accurate total / max_scroll.
    viewport_h = calc_viewport(height, header_h=5, action_h=3)
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    # Only show the scrollbar when the boxes actually overflow — the grid usually
    # fits, and an always-on track just leaves a stray rail down the right edge.
    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    # No button row and no inline hint — the bottom-left chrome carries both
    # affordances now: the back tab, plus a 'c copy' tab beside it (see _copy_tab).
    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        viewport_renderable,
        Text(""),
        Text(""),  # keeps the content above the music pocket band
        Text(""),
    )

    panel = build_page_panel(content, theme=USAGE_THEME, height=height)
    panel._copy_tab = bool(actions and "Copy" in actions)  # show the 'c copy' tab
    # The copy toast is spoken through the shared duck voice by the usage loop.
    # Deliberate opt-in: the box grid can reach the duck's rows, but the toast
    # is this page's only feedback and brief — the trade-off this page always
    # shipped with, now bounded by the fence instead of unfenced.
    return _with_bubble_room(panel, width)


def _build_standup_screen(
    standup_data: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    view: str = "overview",
    selected_card: int = 0,
    actions: list[str] | None = None,
    anon_note: str = "",
) -> Panel:
    """Build the Daily Standup screen (compact dashboard + expandable section cards).

    A pinned status strip under the subtitle carries the sprint/day/confidence
    facts as meters (visible on every view, never scrolls away), with an
    optional one-row notice banner (transient message or first warning). The
    overview body is just the selectable section list (Team Summary, My
    Update, Team — which expands inline into per-member sub-rows — Activity,
    Schedule, Notices); ``view`` set to a card key from ``standup_card_order``
    renders that section's detail. Uses STANDUP_THEME (magenta) with shared
    buttons, scrollbar, meters, and viewport.

    standup_data keys: session_name, my_name, config (dict|None), schedule
    (dict), report (StandupReport|None), message (str, transient status line),
    team_expanded (bool, inline Team-row expansion).

    # See docs: "Daily Standup" — TUI page
    """
    from yeaboi.ui.mode_select.screens._standup_sections import (
        _confidence_style,
        _StandupCtx,
        build_standup_detail,
        build_standup_overview,
        standup_card_title,
    )
    from yeaboi.ui.shared._components import STANDUP_THEME, build_meter, build_reveal_subtitle, standup_title

    theme = STANDUP_THEME
    title = standup_title(shimmer_tick)
    session_name = standup_data.get("session_name", "")
    if view == "overview":
        base = f"Daily standup for {session_name}" if session_name else "Daily standup"
        sub_text = f"{base}  ·  ↑/↓ sections · Enter open · ←/→ buttons"
        if len(_PAD) + len(sub_text) > width - 6:  # hint doesn't fit → keep just the base
            sub_text = base
    else:
        sub_text = f"Overview › {standup_card_title(view, standup_data)}"
    if anon_note:  # anonymized: the subtitle becomes the "N masked — review" indicator
        sub_text = anon_note
    sub = build_reveal_subtitle(sub_text, sub_reveal, pad=_PAD + "  ")
    sub.no_wrap = True  # the header row budget counts the subtitle as one row
    sub.overflow = "ellipsis"

    report = standup_data.get("report")
    # Pinned status strip: the sprint facts stay visible while the body scrolls.
    # no_wrap on strip + banner is load-bearing: header_h counts each as ONE row,
    # so a wrap would push the button bottom border off the fixed-height panel.
    strip = Text(_PAD, justify="left", no_wrap=True, overflow="ellipsis")
    if report is not None:
        strip.append(f"Sprint {report.sprint_name or 'unknown'}", style=theme.value)
        if report.sprint_total_days:
            strip.append("   ")
            strip.append(f"Day {report.sprint_day}/{report.sprint_total_days} ", style=theme.muted)
            strip.append_text(build_meter(report.sprint_day, report.sprint_total_days, theme=theme))
        if report.confidence_label:
            conf_style = _confidence_style(theme, report.confidence_label)
            strip.append("   ")
            strip.append(f"{report.confidence_label} ", style=conf_style)
            if report.confidence_label != "Insufficient data":
                strip.append_text(build_meter(report.confidence_pct, 100, theme=theme, style=conf_style))
                strip.append(f" {report.confidence_pct}%", style=conf_style)
                trend = getattr(report, "confidence_trend", "")
                delta = getattr(report, "confidence_delta", 0)
                if trend == "improving":
                    strip.append(f" ▲+{delta}", style=theme.good)
                elif trend == "declining":
                    strip.append(f" ▼{abs(delta)}", style=theme.bad)
    else:
        strip.append("No standup yet — Generate creates today's standup", style=theme.muted)

    # One-row banner: a transient status message wins, else the first warning.
    message = standup_data.get("message", "")
    warnings = tuple(getattr(report, "warnings", ()) or ()) if report is not None else ()
    banner: Text | None = None
    if message:
        banner = Text(_PAD + message, style=theme.accent_bright, justify="left", no_wrap=True, overflow="ellipsis")
    elif warnings:
        n = len(warnings)
        prefix = f"⚠ {n} notice{'s' if n != 1 else ''} · "
        # Panel border (2) + padding (4) leave width-6 columns. Keep a 3-col safety
        # margin so an ambiguous-width glyph (⚠ / — count as 1 cell to Rich but often
        # render as 2 in the terminal) can't nudge the line past the right border, and
        # cap the gist so this stays a readable teaser instead of stretching edge-to-edge
        # on ultra-wide terminals — the full warning list lives in the Notices section.
        avail = (width - 6) - len(_PAD) - len(prefix) - 3
        room = max(16, min(avail, 90))
        gist = warnings[0]
        if len(gist) > room:
            gist = gist[: room - 1] + "…"
        banner = Text(_PAD + prefix + gist, style=theme.warn, justify="left", no_wrap=True, overflow="ellipsis")

    ctx = _StandupCtx(theme, width)
    if view == "overview":
        build_standup_overview(ctx, standup_data, selected_card)
    else:
        build_standup_detail(ctx, view, standup_data)
    body_lines = ctx.lines

    # ── Layout using shared components ────────────────────────────
    # header_h must match the Group rows above the viewport exactly (blank +
    # title(2) + blanks + sub + strip + optional banner),
    # else the button bottom border falls off the fixed-height panel.
    header_h = 8 + (1 if banner is not None else 0)
    if (height - 4) - header_h - 4 < 3:
        # Terminal too short for the strip — drop it rather than push the
        # buttons off the panel (same floor as before the strip existed).
        strip = None
        banner = None
        header_h = 6
    # The action bar wraps when it outgrows the terminal, so its height has to
    # be measured rather than assumed — six buttons come to well over 80
    # columns, and a hardcoded action_h=4 would draw the overflow row off the
    # bottom of the panel (reachable with the arrow keys, invisible on screen).
    #
    # Budget against the panel's INNER width, not the console's: a row that
    # packs to exactly the console width still overflows the interior, so Rich
    # soft-wraps it and the last bank of buttons drops off the bottom — the very
    # bug the measuring was added to fix. build_page_panel pads (1, 2), so the
    # interior is the border (2) plus 2 columns of padding a side (4) narrower,
    # the same `width - 2 - 4` the section screens below use. Getting this wrong
    # by even a column or two is invisible at 80 and at 100: the six-button bar
    # only lands in the gap at 87-88 columns, the seven-button one at 91-96 and
    # 105-106, which is why the test sweeps a range rather than sampling a width.
    _actions = (
        actions
        if actions is not None
        else (["Generate", "Review", "Team", "Identity", "Back"] if view == "overview" else ["Back", "Export"])
    )
    inner_w = width - _PANEL_BORDER_W - _PANEL_PADDING_W
    action_h = action_rows_height(_actions, inner_w)
    viewport_h = calc_viewport(height, header_h=header_h, action_h=action_h)
    total_items = len(body_lines)
    total_rendered = sum(ctx.item_heights)
    # Scroll offsets identify renderable items, not terminal rows. Find the
    # earliest trailing item from which the report's tail fits in the viewport.
    tail_h = 0
    max_scroll = max(0, total_items - 1)
    for i in range(total_items - 1, -1, -1):
        item_h = ctx.item_heights[i] if i < len(ctx.item_heights) else 1
        if tail_h and tail_h + item_h > viewport_h:
            break
        tail_h += item_h
        max_scroll = i
    # On the overview every item remains a one-row Text value. Keep the whole
    # selected card visible, including a wrapped summary teaser.
    if view == "overview" and ctx.card_rows and selected_card < len(ctx.card_rows):
        row_top = ctx.card_rows[selected_card]
        row_bot = ctx.card_rows[selected_card + 1] - 1 if selected_card + 1 < len(ctx.card_rows) else total_items - 1
        if row_bot + 1 > scroll_offset + viewport_h:
            scroll_offset = row_bot + 1 - viewport_h
        if row_top < scroll_offset:
            scroll_offset = row_top
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible: list = []
    visible_h = 0
    for i in range(actual_scroll, total_items):
        item_h = ctx.item_heights[i] if i < len(ctx.item_heights) else 1
        if visible_h + item_h > viewport_h:
            break
        visible.append(body_lines[i])
        visible_h += item_h

    _sb_text = build_scrollbar(viewport_h, total_rendered, actual_scroll, max_scroll, always_show=True)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - visible_h)):
        padded_lines.append(Text(""))

    action_lines = build_action_rows(_actions, action_sel, width=inner_w)

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    header_rows: list = [] if strip is None else [Text(""), strip]
    if banner is not None:
        header_rows.append(banner)
    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        *header_rows,
        Text(""),
        viewport_renderable,
        Text(""),
        *action_lines,
    )

    return build_page_panel(content, theme=STANDUP_THEME, height=height)


# Button labels for the saved-setup gate. The driver imports these too, so the
# click-hit-testing (button_click) can't drift from what was drawn.
_SAVED_SETUP_ACTIONS = ["Use saved", "Change", "Back"]

# Elevated surface for the saved-setup cards — the standup analogue of
# _ANALYSIS_CARD_BG_RGB, a magenta tint of the same neutral base so the cards
# read as raised above the page. The *page* background stays Theme.bg via
# build_page_panel: per-mode page tints were deliberately dropped, and modes
# keep distinct accents only (see _components.Theme).
_STANDUP_CARD_BG_RGB = "rgb(28,16,26)"
_STANDUP_CARD_BG = f"on {_STANDUP_CARD_BG_RGB}"

# Row label → glyph. Presentation lives here, not in the payload the driver
# passes, so the summary stays plain strings. A label with no glyph gets the
# neutral bullet rather than raising, so adding a row can never break the gate.
_SAVED_SETUP_SYMBOLS = {
    "Trackers": "◆",
    "Members": "●",
    "Code": "✦",
    "Docs": "▤",
    "Last run": "◷",
}

# A card is a top border, a header row, its body rows, and a bottom border.
_SAVED_SETUP_CARD_CHROME = 3


def _saved_setup_card(label: str, lines: list[str], theme) -> Panel:
    """One summary card: glyph + label header over pre-wrapped value lines.

    Lines arrive already wrapped and padded to a common count by the caller, so
    every card in a row is the same height and the viewport maths below is exact.
    They render ``no_wrap`` — a value that was measured slightly too wide is
    ellipsized rather than silently taking a second row and pushing the buttons
    off the bottom.
    """
    head = Text(overflow="ellipsis", no_wrap=True)
    head.append(f"{_SAVED_SETUP_SYMBOLS.get(label, '·')} ", style=theme.accent_bright)
    head.append(label.upper(), style=f"bold {theme.accent_bright}")
    body = [Text(line, style="white", overflow="ellipsis", no_wrap=True) for line in lines]
    return Panel(
        Group(head, *body),
        box=rich.box.ROUNDED,
        border_style=theme.accent,
        style=_STANDUP_CARD_BG,
        padding=(0, 1),
        expand=True,
    )


def _build_standup_saved_setup_screen(
    rows: list[tuple[str, str]],
    *,
    action_sel: int = 0,
    width: int = 80,
    height: int = 24,
) -> Panel:
    """Build the Generate gate: reuse the saved setup, or walk the pickers again.

    ``rows`` are plain ``(label, value)`` strings built by the caller — this
    builder decides the colours, so the summary never carries presentation. A
    value may contain newlines; each becomes its own line in the card (and is
    flattened to " · " in the compact layout).

    Layout mirrors the Analysis setup review (the other "confirm what you already
    chose" screen): breadcrumb, question, cards, then the reassurance that
    nothing has started yet. Below 88 columns — or when the cards would not fit
    the viewport — it degrades to the aligned label/value list this screen
    started as, which stays readable down to the minimum terminal size.
    """
    from yeaboi.ui.shared._components import STANDUP_THEME, standup_title

    theme = STANDUP_THEME
    # A short terminal spends its rows on the summary, not on breathing room: the
    # blank spacers and the reassurance line go first, and the wordmark shrinks to
    # a text label. Losing a summary row to make space for a blank one would be
    # the wrong trade — the summary is the whole point of the screen.
    tight = height < 26
    title_h = TITLE_ROWS if height >= 28 else 1
    # blank + title + blank + breadcrumb + hint + blank + question + blank
    # (tight drops the two blank spacers around the title)
    header_h = (5 if tight else 7) + title_h

    # Two columns is also a ceiling, not just what fits: button_click finds the
    # action row by looking for the first row carrying exactly len(labels)
    # ``╭──╮`` runs, and there are three actions — a three-card row would draw
    # three of them and swallow every click on the buttons below it.
    ncols = 2 if width >= 88 else 1
    # Page borders (2) + build_page_panel's horizontal padding (2×2) + the indent
    # that lines the cards up with the title, then per column the grid's own cell
    # padding (2) and each card's border + padding (4).
    text_w = max(12, (width - _PANEL_BORDER_W - 4 - len(PAD) - 2 * ncols) // ncols - 4)
    wrapped = [
        (
            label,
            [piece for line in value.split("\n") for piece in (textwrap.wrap(line, text_w) or [""])],
        )
        for label, value in rows
    ]
    grid_rows = -(-len(wrapped) // ncols) if wrapped else 0
    # Every card gets the same number of body lines so the bottom borders align.
    # Shrink that count (dropping the tail of the longest value) before giving up
    # on cards entirely — a 5-row summary in a short terminal still reads better
    # as cards than as a list.
    needed = min(max((len(lines) for _, lines in wrapped), default=1), 3)

    def _fits(vh: int) -> int:
        """Largest uniform card body that fits ``vh`` rows, or 0 for none."""
        return next((c for c in range(needed, 0, -1) if grid_rows * (c + _SAVED_SETUP_CARD_CHROME) <= vh), 0)

    # The reassurance line is a nicety; the summary is the screen. So try the
    # roomy layout first and fall back to spending its two rows on the cards —
    # a truncated value is a worse outcome than a missing reminder.
    body_lines, viewport_h, show_note = 0, 0, False
    for note in (True, False) if not tight else (False,):
        # note: blank + reassurance + blank + 3 button rows; without it, just the
        # blank and the buttons.
        candidate_vh = calc_viewport(height, header_h=header_h, action_h=6 if note else 4)
        candidate = _fits(candidate_vh)
        if candidate > body_lines or not viewport_h:
            body_lines, viewport_h, show_note = candidate, candidate_vh, note
        if body_lines >= needed:
            break

    if width >= 88 and body_lines:
        cards = []
        for label, lines in wrapped:
            shown = lines[:body_lines]
            if len(lines) > body_lines and shown:
                shown[-1] = shown[-1][: max(0, text_w - 1)] + "…"
            shown.extend("" for _ in range(body_lines - len(shown)))
            cards.append(_saved_setup_card(label, shown, theme))
        # Indented to the title/button column so the cards read as one block
        # with the rest of the page rather than hanging off its left edge.
        summary = Padding(_analysis_card_grid(cards, width=width, columns=ncols), (0, 0, 0, len(PAD)))
        summary_h = grid_rows * (body_lines + _SAVED_SETUP_CARD_CHROME)
    else:
        compact = []
        for label, value in rows:
            line = Text(PAD, overflow="ellipsis", no_wrap=True)
            line.append(f"{label:<10}", style=f"bold {theme.accent_bright}")
            line.append(value.replace("\n", " · ") or "none", style="white")
            compact.append(line)
        summary = Group(*compact)
        summary_h = len(compact)

    btn_top, btn_mid, btn_bot = build_action_buttons(_SAVED_SETUP_ACTIONS, action_sel)
    spacer = [] if tight else [Text("")]
    title = standup_title(width=width) if height >= 28 else Text(PAD + "STANDUP", style=f"bold {theme.accent_bright}")
    reassurance = (
        [
            Text(""),
            Text(PAD + "Nothing runs until you choose · Change re-walks every picker.", style=theme.muted),
        ]
        if show_note
        else []
    )
    content = Group(
        *spacer,
        title,
        *spacer,
        *_analysis_setup_header(
            "Saved setup",
            "←/→ move · Enter choose · Esc cancel",
            brand="STANDUP",
            theme=theme,
        ),
        Text(PAD + "Use your saved setup?", style="bold white"),
        Text(""),
        summary,
        *[Text("") for _ in range(max(0, viewport_h - summary_h))],
        *reassurance,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )
    return build_page_panel(content, theme=STANDUP_THEME, height=height)


def _build_standup_team_source_screen(
    sources: list[tuple[str, str]],
    checked: set[int],
    cursor: int,
    *,
    width: int = 80,
    height: int = 24,
    message: str = "",
    heading: str = "Choose update sources",
) -> Panel:
    """Build the first Team step: Jira/Azure DevOps tracker multi-select."""
    from yeaboi.ui.shared._components import STANDUP_THEME, standup_title

    theme = STANDUP_THEME
    rows: list[Text] = []
    if message:
        rows.extend((Text(_PAD + message, style=theme.warn), Text("")))
    rows.append(Text(_PAD + f"{len(checked)} of {len(sources)} selected", style=theme.muted))
    rows.append(Text(""))
    for idx, (_key, label) in enumerate(sources):
        selected = idx in checked
        active = idx == cursor
        row = Text(_PAD + "  ")
        row.append("‹ " if active else "  ", style=theme.accent_bright)
        row.append("●" if selected else "○", style=theme.accent_bright if selected else theme.dim)
        row.append(f" {label}", style="bold white" if active else (theme.accent if selected else theme.desc))
        if active:
            row.append(" ›", style=theme.accent_bright)
        rows.append(row)
    viewport_h = calc_viewport(height, header_h=6, action_h=2)
    rows.extend(Text("") for _ in range(max(0, viewport_h - len(rows))))
    content = Group(
        Text(""),
        standup_title(),
        Text(""),
        Text(_PAD + heading, style="bold white"),
        Text(_PAD + "↑/↓ move · Space toggle · Enter continue · Esc cancel", style=theme.muted),
        Text(""),
        Group(*rows[:viewport_h]),
    )
    return build_page_panel(content, theme=STANDUP_THEME, height=height)


def _build_standup_team_member_screen(
    roster: list[str],
    checked: set[int],
    cursor: int,
    *,
    width: int = 80,
    height: int = 24,
    message: str = "",
    heading: str = "Choose team members",
    empty_message: str = "No members found in the selected tracker(s).",
) -> Panel:
    """Build the second Team step: authoritative member multi-select."""
    from yeaboi.ui.shared._components import STANDUP_THEME, standup_title

    theme = STANDUP_THEME
    rows: list[Text] = []
    if message:
        rows.extend((Text(_PAD + message, style=theme.warn), Text("")))
    rows.extend(
        (
            Text(_PAD + f"{len(checked)} of {len(roster)} selected", style=theme.muted),
            Text(""),
        )
    )
    for idx, name in enumerate(roster):
        selected = idx in checked
        active = idx == cursor
        row = Text(_PAD + "  ")
        row.append("‹ " if active else "  ", style=theme.accent_bright)
        row.append("●" if selected else "○", style=theme.accent_bright if selected else theme.dim)
        row.append(f" {name}", style="bold white" if active else (theme.accent if selected else theme.desc))
        if active:
            row.append(" ›", style=theme.accent_bright)
        rows.append(row)
    if not roster:
        rows.append(Text(_PAD + empty_message, style=theme.muted))

    viewport_h = calc_viewport(height, header_h=6, action_h=2)
    total = len(rows)
    max_scroll = max(0, total - viewport_h)
    start = min(max(0, cursor - viewport_h // 2 + 2), max_scroll) if roster else 0
    visible = rows[start : start + viewport_h]
    visible.extend(Text("") for _ in range(max(0, viewport_h - len(visible))))
    scrollbar = build_scrollbar(viewport_h, total, start, max_scroll, always_show=True)
    if scrollbar is not None:
        from rich.table import Table

        viewport = Table(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
        viewport.add_column(ratio=1)
        viewport.add_column(width=1)
        viewport.add_row(Group(*visible), scrollbar)
    else:
        viewport = Group(*visible)
    content = Group(
        Text(""),
        standup_title(),
        Text(""),
        Text(_PAD + heading, style="bold white"),
        Text(_PAD + "↑/↓ move · Space toggle · A toggle all · Enter save · Esc cancel", style=theme.muted),
        Text(""),
        viewport,
    )
    return build_page_panel(content, theme=STANDUP_THEME, height=height)


#: Verdict → (marker, style attribute on the theme). ``None`` is "not voted on",
#: which is the resting state and must not look like a judgement either way.
_PRACTICE_VERDICT_MARKS = {
    "up": ("▲", "good"),
    "down": ("▼", "warn"),
    None: ("·", "dim"),
}


def _build_standup_practice_review_screen(
    member: str,
    signals: list,
    verdicts: dict[int, str],
    cursor: int,
    *,
    width: int = 80,
    height: int = 24,
    message: str = "",
) -> Panel:
    """Review one member's practice signals: was each one right about them?

    A verdict per signal rather than a multi-select, because the two answers are
    not "on/off" — a thumbs-down suppresses the signal and teaches the matcher,
    a thumbs-up only teaches it. Unvoted rows are the resting state and stay
    unstyled: this screen must not read as a list of accusations to confirm.
    """
    from yeaboi.ui.shared._components import STANDUP_THEME, standup_title

    theme = STANDUP_THEME
    rows: list[Text] = []
    if message:
        rows.extend((Text(_PAD + message, style=theme.warn), Text("")))
    voted = sum(1 for v in verdicts.values() if v)
    rows.extend((Text(_PAD + f"{voted} of {len(signals)} answered", style=theme.muted), Text("")))

    for idx, signal in enumerate(signals):
        verdict = verdicts.get(idx)
        mark, tone = _PRACTICE_VERDICT_MARKS[verdict]
        active = idx == cursor
        row = Text(_PAD + "  ")
        row.append("‹ " if active else "  ", style=theme.accent_bright)
        row.append(mark, style=getattr(theme, tone))
        row.append(f" {getattr(signal, 'title', '')}", style="bold white" if active else theme.value)
        if active:
            row.append(" ›", style=theme.accent_bright)
        rows.append(row)
        # The sentence the member would read, dimmed under its own title — the
        # verdict is about this wording, so voting blind on the label alone
        # would be voting on the rule rather than on the call it made.
        detail = " ".join(str(getattr(signal, "detail", "")).split())
        # One line each, ellipsised — the row arithmetic below assumes three rows
        # per signal, and a wrapped sentence would silently make it four.
        # no_wrap/overflow only bind inside a Panel, which build_page_panel gives us.
        rows.append(Text(_PAD + "      " + detail, style=theme.desc, no_wrap=True, overflow="ellipsis"))
        rows.append(Text(""))
    if not signals:
        rows.append(Text(_PAD + "No practice signals to review for this member.", style=theme.muted))

    viewport_h = calc_viewport(height, header_h=7, action_h=2)
    total = len(rows)
    max_scroll = max(0, total - viewport_h)
    # Three rows per signal, so centring on the cursor has to scale by three or
    # the selected row drifts off-screen well before the list ends.
    start = min(max(0, cursor * 3 - viewport_h // 2 + 2), max_scroll) if signals else 0
    visible = rows[start : start + viewport_h]
    visible.extend(Text("") for _ in range(max(0, viewport_h - len(visible))))
    scrollbar = build_scrollbar(viewport_h, total, start, max_scroll, always_show=True)
    if scrollbar is not None:
        viewport = Table(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
        viewport.add_column(ratio=1)
        viewport.add_column(width=1)
        viewport.add_row(Group(*visible), scrollbar)
    else:
        viewport = Group(*visible)
    content = Group(
        Text(""),
        standup_title(),
        Text(""),
        Text(_PAD + f"Was this right about {member}?", style="bold white"),
        Text(
            _PAD + "▼ hides it and stops it coming back · ▲ confirms it",
            style=theme.muted,
        ),
        Text(_PAD + "↑/↓ move · Y right · N wrong · C clear · Enter save · Esc cancel", style=theme.muted),
        Text(""),
        viewport,
    )
    return build_page_panel(content, theme=STANDUP_THEME, height=height)


_SCHEDULE_STEP_NAMES = ["Time", "Lead", "Days", "Channels", "Enable", "Remind"]


def _build_standup_schedule_step_screen(
    options: list[tuple[str, str]],
    cursor: int,
    *,
    checked: set[int] | None = None,
    step_index: int = 0,
    heading: str = "",
    width: int = 80,
    height: int = 24,
    message: str = "",
    step_names: list[str] | None = None,
) -> Panel:
    """Build one step of a standup option-list wizard: radio or checkbox.

    ``checked is None`` renders a single-select (radio) step — the cursor row IS
    the selection, Enter confirms it. A ``set`` renders a multi-select step where
    Space toggles membership. ``options`` are ``(label, description)`` pairs; the
    description renders dimmed after the label.

    ``step_names`` labels the progress dots, defaulting to the schedule wizard's
    five steps. It is a parameter rather than a constant so other standup
    wizards (the transcript source picker) reuse this whole screen — the
    scrolling, the back-navigation and their render tests — instead of growing a
    near-copy.
    """
    from yeaboi.ui.shared._components import STANDUP_THEME, standup_title

    theme = STANDUP_THEME
    multi = checked is not None
    rows: list[Text] = []
    if message:
        rows.extend((Text(_PAD + message, style=theme.warn), Text("")))
    if multi:
        rows.append(Text(_PAD + f"{len(checked)} of {len(options)} selected", style=theme.muted))
        rows.append(Text(""))
    for idx, (label, desc) in enumerate(options):
        selected = (idx in checked) if multi else (idx == cursor)
        active = idx == cursor
        row = Text(_PAD + "  ")
        row.append("‹ " if active else "  ", style=theme.accent_bright)
        row.append("●" if selected else "○", style=theme.accent_bright if selected else theme.dim)
        row.append(f" {label}", style="bold white" if active else (theme.accent if selected else theme.desc))
        if active:
            row.append(" ›", style=theme.accent_bright)
        if desc:
            row.append(f"  ·  {desc}", style=theme.muted if active else theme.dim)
        rows.append(row)

    hints = "↑/↓ move · Space toggle · Enter continue · Esc back" if multi else "↑/↓ move · Enter continue · Esc back"
    viewport_h = calc_viewport(height, header_h=11, action_h=2)
    total = len(rows)
    max_scroll = max(0, total - viewport_h)
    start = min(max(0, cursor - viewport_h // 2 + 2), max_scroll)
    visible = rows[start : start + viewport_h]
    visible.extend(Text("") for _ in range(max(0, viewport_h - len(visible))))
    scrollbar = build_scrollbar(viewport_h, total, start, max_scroll)
    if scrollbar is not None:
        viewport: Table | Group = Table(
            show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True
        )
        viewport.add_column(ratio=1)
        viewport.add_column(width=1)
        viewport.add_row(Group(*visible), scrollbar)
    else:
        viewport = Group(*visible)
    content = Group(
        Text(""),
        standup_title(),
        Text(""),
        Text(_PAD + heading, style="bold white"),
        build_progress_dots(step_names or _SCHEDULE_STEP_NAMES, step_index, theme=theme),
        Text(_PAD + hints, style=theme.muted),
        Text(""),
        viewport,
    )
    return build_page_panel(content, theme=theme, height=height)


def _build_changelog_screen(
    entries: list,
    *,
    update_status: dict | None = None,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    message: str = "",
) -> Panel:
    """Build the Changelog page: per-version AI-written notes with area tags.

    ``entries`` is ``changelog.load_changelog()`` output (newest-first). Each
    highlight's feature-area tags render in that mode's accent colour
    (``changelog.AREA_COLORS``) so a change reads as the feature the user already
    knows by colour. ``update_status`` (``update_check.get_update_status()``)
    drives an upgrade banner at the top when a newer PyPI release is known.
    """
    import textwrap

    from yeaboi.changelog import AREA_COLORS
    from yeaboi.ui.shared._components import CHANGELOG_THEME, build_reveal_subtitle, changelog_title

    theme = CHANGELOG_THEME
    title = changelog_title(shimmer_tick, width=width)
    sub = build_reveal_subtitle("What's new in yeaboi", sub_reveal, pad=_PAD + "  ")

    body_lines: list = []
    if message:
        body_lines.append(Text(_PAD + "  " + message, style=theme.accent_bright, justify="left"))
        body_lines.append(Text(""))
    wrap_w = max(24, width - len(_PAD) - 12)

    def _wrapped(text: str, style: str, *, indent: str = "    ") -> None:
        for chunk in textwrap.wrap(text, width=wrap_w) or [""]:
            body_lines.append(Text(_PAD + indent + chunk, style=style, justify="left"))

    # ── Upgrade banner — newer release known from the background PyPI check ──
    status = update_status or {}
    if status.get("update_available"):
        banner = Text(_PAD + "  ", justify="left")
        banner.append("⬆ ", style=theme.warn)
        banner.append(f"v{status.get('latest', '')} is available", style=f"bold {theme.warn}")
        banner.append("  —  run: ", style=theme.muted)
        banner.append(status.get("upgrade_command", ""), style=theme.warn)
        body_lines.append(banner)
        body_lines.append(Text(_PAD + "  " + "─" * min(wrap_w, 40), style=theme.sep, justify="left"))

    if not entries:
        body_lines.append(Text(""))
        body_lines.append(Text(_PAD + "    No changelog data available.", style=theme.muted, justify="left"))

    for entry in entries:
        body_lines.append(Text(""))
        heading = Text(_PAD + "  ", justify="left")
        heading.append(f"v{entry.version}", style=f"bold {theme.accent_bright}")
        if entry.date:
            heading.append("  ·  ", style=theme.sep)
            heading.append(entry.date, style=theme.muted)
        body_lines.append(heading)
        body_lines.append(Text(_PAD + "  " + "─" * min(wrap_w, 40), style=theme.sep, justify="left"))
        if entry.summary:
            _wrapped(entry.summary, theme.desc)
        for hl in entry.highlights:
            # Reserve room on the bullet's last line for the coloured area tags.
            tags_len = sum(len(a) + 2 for a in hl.areas)
            chunks = textwrap.wrap(hl.text, width=max(24, wrap_w - 3)) or [""]
            for i, chunk in enumerate(chunks):
                # Flush with the version heading above it (see the tips gallery).
                prefix = "  •  " if i == 0 else "     "
                line = Text(_PAD + prefix + chunk, style=theme.value, justify="left")
                if i == len(chunks) - 1 and len(prefix) + len(chunk) + tags_len <= wrap_w + 8:
                    for area in hl.areas:
                        line.append("  ")
                        line.append(area, style=f"bold {AREA_COLORS.get(area, theme.muted)}")
                    body_lines.append(line)
                elif i == len(chunks) - 1:
                    body_lines.append(line)
                    tag_line = Text(_PAD + "       ", justify="left")
                    for area in hl.areas:
                        tag_line.append(area, style=f"bold {AREA_COLORS.get(area, theme.muted)}")
                        tag_line.append("  ")
                    body_lines.append(tag_line)
                else:
                    body_lines.append(line)

    # ── Layout using shared components ────────────────────────────
    # No button row — a keyboard hint replaces it (aligned with the headings),
    # matching the Usage page. action_h = blank + hint + pocket blank. The no_wrap
    # pinning below keeps the viewport at exactly viewport_h rows so the hint can't
    # be shoved off the bottom.
    viewport_h = calc_viewport(height, header_h=6, action_h=3)
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll, always_show=True)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    # header_h/viewport_h count every body line as ONE row. A long entry (its
    # inline area tags in particular) can otherwise wrap inside the scrollbar cell,
    # render two rows, and shove the keyboard hint off the bottom where the crop and
    # the music pocket swallow it. Pin each line to a single row (crop the overflow).
    for _ln in padded_lines:
        _ln.no_wrap = True
        _ln.overflow = "crop"

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        viewport_renderable,
        Text(""),
        Text(""),  # the copy hint used to sit here; kept blank to hold the layout
        Text(""),  # keeps the content above the music pocket band
    )

    return build_page_panel(content, theme=CHANGELOG_THEME, height=height)


def _build_all_tips_screen(
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    message: str = "",
) -> Panel:
    """Build the All Tips gallery page: every discoverability tip in one scroll.

    Same scrollable Panel skeleton as :func:`_build_changelog_screen`. Content is
    the live ``get_tips()`` list, grouped into modes, workflows, and setup so the
    gallery scans like the other sectioned pages. Freshly-shipped features get a
    gold ``NEW`` badge and tips that map to a home card note the mode they open.
    Read-only, with no actions of its own — going back is the app-wide back tab.
    """
    import textwrap

    from yeaboi.ui.mode_select.screens._screens import _MODE_CARDS, _TIP_DOT_ON
    from yeaboi.ui.shared._components import CHANGELOG_THEME, build_reveal_subtitle, tips_title
    from yeaboi.ui.shared._tips import get_tips

    theme = CHANGELOG_THEME
    title = tips_title(shimmer_tick, width=width)
    sub = build_reveal_subtitle("Everything yeaboi can do", sub_reveal, pad=_PAD + "  ")
    cards = {card["key"]: card for card in _MODE_CARDS}
    gold = f"rgb({_TIP_DOT_ON[0]},{_TIP_DOT_ON[1]},{_TIP_DOT_ON[2]})"
    beta_c = f"rgb({BETA_RGB[0]},{BETA_RGB[1]},{BETA_RGB[2]})"

    body_lines: list = []
    if message:
        body_lines.append(Text(_PAD + "  " + message, style=theme.accent_bright, justify="left"))
        body_lines.append(Text(""))

    # Account explicitly for the frame, horizontal panel padding, scrollbar,
    # and the gutter beside it. Keeping wrapping inside this budget prevents
    # wide glyphs or metadata from visually colliding with the right frame.
    panel_inner_w = max(20, width - 2 - 4)
    viewport_body_w = max(18, panel_inner_w - 3)
    # The bullet lines up with the START of the section heading (which sits at
    # _PAD + 2), rather than being indented under it — the list reads as the
    # section's content, not as a sub-level of it.
    bullet_prefix = _PAD + "  " + "•  "
    continuation_prefix = _PAD + "  " + "   "
    # -4, not -1: the viewport puts the body in a table beside the scrollbar, so
    # the cell the line lands in is narrower than the panel. Wrapping to the
    # panel let Rich crop the tail — silently, with no ellipsis to show it had.
    tip_wrap_w = max(16, viewport_body_w - len(bullet_prefix) - 4)
    separator_w = max(8, min(viewport_body_w - len(_PAD) - 2, 40))

    tips = get_tips()
    grouped_tips = (
        ("Modes", [tip for tip in tips if tip.mode_key]),
        (
            "More workflows",
            [
                tip
                for tip in tips
                if not tip.mode_key and tip.key not in {"voice", "music"} and not tip.key.startswith("meta:")
            ],
        ),
        (
            "Shortcuts & setup",
            [tip for tip in tips if tip.key in {"voice", "music"} or tip.key.startswith("meta:")],
        ),
    )

    rendered_section = False
    for section, section_tips in grouped_tips:
        if not section_tips:
            continue
        if rendered_section:
            body_lines.append(Text(""))
        rendered_section = True
        heading = Text(_PAD + "  ", justify="left")
        heading.append(section, style=f"bold {theme.accent_bright}")
        body_lines.append(heading)
        body_lines.append(Text(_PAD + "  " + "─" * separator_w, style=theme.sep, justify="left"))

        for tip in section_tips:
            # Emoji variation selectors are not measured consistently across
            # terminals. On a full-width panel that disagreement shifts Rich's
            # final frame cell and makes the right border appear fragmented.
            # The gallery already has a bullet and colour-coded mode metadata,
            # so omit the decorative "<emoji> Tip:" prefix here. The canonical
            # tip text (and therefore Copy all) remains unchanged.
            _prefix, marker, display_text = tip.text.partition("Tip: ")
            if not marker:
                display_text = tip.text
            chunks = textwrap.wrap(display_text, width=tip_wrap_w) or [""]
            for i, chunk in enumerate(chunks):
                prefix = bullet_prefix if i == 0 else continuation_prefix
                body_lines.append(Text(prefix + chunk, style=theme.value, justify="left"))

            if tip.is_beta or tip.is_new or (tip.mode_key and tip.mode_key in cards):
                metadata = Text(continuation_prefix, justify="left")
                # Same precedence as the welcome tip row: BETA outranks NEW.
                if tip.is_beta:
                    metadata.append(BETA_LABEL, style=f"bold {beta_c}")
                elif tip.is_new:
                    metadata.append("NEW", style=f"bold {gold}")
                if tip.mode_key and tip.mode_key in cards:
                    if tip.is_beta or tip.is_new:
                        metadata.append("  ·  ", style=theme.sep)
                    metadata.append("opens ", style=theme.muted)
                    card = cards[tip.mode_key]
                    metadata.append(card["title"], style=f"bold {card['color']}")
                body_lines.append(metadata)

            # A deliberate spacer makes each wrapped record read as one unit.
            body_lines.append(Text(""))

    # ── Layout using shared components (identical to the changelog page) ──
    # No button row — a keyboard hint replaces it. action_h = blank + hint + blank.
    viewport_h = calc_viewport(height, header_h=6, action_h=3)
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll, always_show=True)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    # Pin each line to one row (see the changelog page) so a wrapped tip can't grow
    # the viewport and push the keyboard hint off the bottom.
    for _ln in padded_lines:
        _ln.no_wrap = True
        _ln.overflow = "crop"

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_column(width=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), Text(""), _sb_text, Text(""))
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        viewport_renderable,
        Text(""),
        Text(""),  # the copy-all hint used to sit here; kept blank to hold the layout
        Text(""),
        Text(""),  # keeps the content above the music pocket band
    )

    return build_page_panel(content, theme=CHANGELOG_THEME, height=height)


def _build_feedback_screen(
    view: str,
    *,
    kind_idx: int = 0,
    area_idx: int = 0,
    title_text: str = "",
    description: str = "",
    attachments_count: int = 0,
    field_sel: int = 0,
    focus: str = "fields",
    action_sel: int = 0,
    polished: tuple[str, str] | None = None,
    result_url: str = "",
    show_open_browser: bool = False,
    status: str = "",
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    border_style: str = "",
) -> Panel:
    """Build the Feedback page (opened with `f` from mode select).

    ``view`` selects the screen state: ``"form"`` (type/area/title/description
    rows + Submit / AI Polish / Back), ``"busy"`` (worker running — the caller
    animates ``border_style`` for the pulsing frame), ``"polish_preview"``
    (AI-rewritten draft + Accept / Keep Original) and ``"result"`` (submission
    outcome + Done / Open Browser). The area chip renders in that mode's accent
    colour (``changelog.AREA_COLORS``) — the frame itself stays neutral silver
    like the changelog page.
    """
    import textwrap

    from yeaboi.changelog import AREA_COLORS
    from yeaboi.feedback import FEEDBACK_AREAS, FEEDBACK_TYPES
    from yeaboi.ui.shared._components import FEEDBACK_THEME, build_reveal_subtitle, feedback_title

    theme = FEEDBACK_THEME
    title = feedback_title(shimmer_tick, width=width)
    subtitles = {
        "form": "Report a bug or request a feature — filed as a GitHub issue",
        "busy": "Working…",
        "polish_preview": "AI-polished draft — accept it or keep your original",
        "result": "Submission result",
    }
    sub = build_reveal_subtitle(subtitles.get(view, ""), sub_reveal, pad=_PAD + "  ")

    body_lines: list = []
    wrap_w = max(24, width - len(_PAD) - 12)

    def _wrapped(text: str, style: str, *, indent: str = "    ") -> None:
        for seg in text.split("\n"):
            for chunk in textwrap.wrap(seg, width=wrap_w) or [""]:
                body_lines.append(Text(_PAD + indent + chunk, style=style, justify="left"))

    kind = FEEDBACK_TYPES[kind_idx % len(FEEDBACK_TYPES)]
    area = FEEDBACK_AREAS[area_idx % len(FEEDBACK_AREAS)]
    area_color = AREA_COLORS.get(area, theme.muted)

    if view in ("form", "busy"):
        fields_focused = focus == "fields" and view == "form"

        def _row(idx: int, label: str, render_value) -> None:
            is_sel = fields_focused and field_sel == idx
            line = Text(_PAD + ("  ❯ " if is_sel else "    "), justify="left")
            line.stylize(f"bold {theme.accent_bright}" if is_sel else theme.dim)
            line.append(f"{label:<13}", style=f"bold {theme.accent_bright}" if is_sel else theme.muted)
            render_value(line, is_sel)
            body_lines.append(line)
            body_lines.append(Text(""))

        def _kind_value(line: Text, is_sel: bool) -> None:
            line.append("◄ " if is_sel else "  ", style=theme.dim)
            line.append(kind, style=f"bold {theme.value}" if is_sel else theme.desc)
            line.append(" ►" if is_sel else "", style=theme.dim)

        def _area_value(line: Text, is_sel: bool) -> None:
            line.append("◄ " if is_sel else "  ", style=theme.dim)
            line.append(area, style=f"bold {area_color}")
            line.append(" ►" if is_sel else "", style=theme.dim)

        def _title_value(line: Text, is_sel: bool) -> None:
            if title_text:
                shown = title_text if len(title_text) <= wrap_w - 20 else title_text[: wrap_w - 21] + "…"
                line.append(shown, style=theme.value)
            else:
                line.append("(required — press Enter to write)", style=theme.dim)

        # The value column starts after the 4-space selector gutter + 13-char label.
        _val_indent = " " * 17
        desc_wrap_w = max(24, wrap_w - len(_val_indent))
        desc_lines: list[str] = []
        for seg in description.split("\n"):
            desc_lines.extend(textwrap.wrap(seg, width=desc_wrap_w) or [""])

        def _desc_value(line: Text, is_sel: bool) -> None:
            if description.strip():
                line.append(desc_lines[0], style=theme.value)
            else:
                line.append("(press Enter to write — voice + Ctrl+V screenshots)", style=theme.dim)
            if attachments_count:
                line.append(f"  📎 {attachments_count}", style=theme.warn)

        body_lines.append(Text(""))
        _row(0, "Type", _kind_value)
        _row(1, "Area", _area_value)
        _row(2, "Title", _title_value)
        _row(3, "Description", _desc_value)

        # Continuation lines: fill the otherwise-empty viewport with the rest of
        # the description instead of hiding it behind a "+N more lines" note —
        # that note now only appears when the text genuinely overflows the page.
        # (The blank line _row() appended after the Description row is dropped so
        # the continuation reads as one block.)
        continuations = desc_lines[1:] if description.strip() else []
        if continuations:
            body_lines.pop()
            # Rows consumed above the continuation block: top blank + 4 field
            # rows + 3 blanks between them, plus one trailing status/spacer row.
            desc_budget = max(1, calc_viewport(height, header_h=6, action_h=4) - 10)
            for cont in continuations[:desc_budget]:
                body_lines.append(Text(_PAD + _val_indent + cont, style=theme.value, justify="left"))
            hidden = len(continuations) - desc_budget
            if hidden > 0:
                body_lines.append(
                    Text(
                        _PAD + _val_indent + f"(+{hidden} more line{'s' if hidden > 1 else ''})",
                        style=theme.dim,
                        justify="left",
                    )
                )
            body_lines.append(Text(""))

    elif view == "polish_preview" and polished is not None:
        p_title, p_desc = polished
        body_lines.append(Text(""))
        heading = Text(_PAD + "  ", justify="left")
        heading.append(f"[{kind}] {p_title}", style=f"bold {theme.accent_bright}")
        body_lines.append(heading)
        body_lines.append(Text(_PAD + "  " + "─" * min(wrap_w, 40), style=theme.sep, justify="left"))
        _wrapped(p_desc, theme.value)

    elif view == "result":
        body_lines.append(Text(""))
        _wrapped(status, theme.value, indent="  ")
        if result_url:
            body_lines.append(Text(""))
            _wrapped(result_url, f"bold {theme.accent_bright}", indent="  ")

    if status and view not in ("result",):
        body_lines.append(Text(""))
        _wrapped(status, theme.warn, indent="  ")

    # ── Layout using shared components (same skeleton as the changelog page) ──
    viewport_h = calc_viewport(height, header_h=6, action_h=4)
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    if view == "form" and focus == "fields":
        # Auto-scroll so the selected field row stays visible on short
        # terminals (the form itself has no manual scroll keys — up/down
        # move the selection instead).
        sel_line = 1 + 2 * field_sel  # body index: top blank + 2 rows per field
        scroll_offset = max(scroll_offset, sel_line - viewport_h + 1)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll, always_show=view == "polish_preview")
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    buttons_by_view = {
        "form": ["Submit", "AI Polish", "Back"],
        "busy": [],
        "polish_preview": ["Accept", "Keep Original"],
        "result": (["Done", "Open Browser"] if show_open_browser else ["Done"]),
    }
    labels = buttons_by_view.get(view, ["Back"])
    highlight = action_sel if (focus == "buttons" or view != "form") else -1
    if labels:
        btn_top, btn_mid, btn_bot = build_action_buttons(labels, highlight)
    else:
        btn_top, btn_mid, btn_bot = Text(""), Text(""), Text("")

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        viewport_renderable,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )

    return build_page_panel(content, theme=FEEDBACK_THEME, border_style=border_style or "white", height=height)


def _performance_roster_window(selected: int, n: int, budget: int) -> tuple[int, int]:
    """Return the [start, end) engineer window that fits ``budget`` visual lines.

    Big ASCII rows are ~3 lines each (the selected one ~5 with its description), so
    only a few engineers show at once. The window always contains ``selected`` and
    grows outward alternately (below first) so the selection stays comfortably in
    view; callers mark any hidden engineers with ▲/▼ counters.
    """
    if n <= 0:
        return 0, 0
    used = 5  # the selected row (2 ASCII lines + blank + description + spacer)
    start, end = selected, selected + 1
    grew = True
    while grew:
        grew = False
        if end < n and used + 3 <= budget:
            used += 3
            end += 1
            grew = True
        if start > 0 and used + 3 <= budget:
            used += 3
            start -= 1
            grew = True
    return start, end


def _build_performance_screen(
    performance_data: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float = 0.0,
    desc_reveal: float = 0.0,
    sub_reveal: float | None = None,
    anon_note: str = "",
) -> Panel:
    """Build the Performance dashboard screen using shared TUI components.

    Two views, both rendered here (the run page owns which is active):
    - "roster": a selectable list of engineers (from Jira / Azure DevOps) — up/down
      moves the selection, the action buttons run a workflow for the selected person.
    - "detail": the artifact produced by an action (1:1 prep / completion / review),
      scrollable, with Back / Export buttons.

    performance_data keys: session_name, view ("roster"|"detail"), roster (list[str]),
    selected_idx (int), detail_lines (list[str] plaintext), detail_title (str),
    actions (list[str]), message (str, transient status line).

    Uses PERFORMANCE_THEME (coral) with shared buttons, scrollbar, and viewport.

    # See docs: "Performance Mode" — TUI page
    """
    from yeaboi.ui.mode_select.screens._screens import _build_mode_row
    from yeaboi.ui.shared._components import (
        PERFORMANCE_THEME,
        build_badge,
        build_reveal_subtitle,
        performance_title,
    )

    theme = PERFORMANCE_THEME
    _accent = "rgb(220,110,90)"  # PERFORMANCE_THEME accent — the mode-row colour key
    title = performance_title(shimmer_tick, width=width)
    # The BETA chip is the standing reminder once the one-time entry notice has
    # been dismissed, so it sits on the header of both views. Appended here rather
    # than inside performance_title(): that wordmark is shared with the export
    # picker and the run hub, which we didn't decide to label. no_wrap keeps the
    # header at TITLE_ROWS rows, which the viewport maths assumes.
    title.append("  ")
    title.append_text(build_badge(BETA_LABEL))
    title.no_wrap = True
    title.overflow = "crop"
    view = performance_data.get("view", "roster")
    session_name = performance_data.get("session_name", "")

    if view == "detail":
        sub_text = performance_data.get("detail_title", "") or "Performance"
    else:
        sub_text = f"Team performance — {session_name}" if session_name else "Team performance"
    if anon_note:  # anonymized detail view: the subtitle carries the "N masked" indicator
        sub_text = anon_note
    sub = build_reveal_subtitle(sub_text, sub_reveal, pad=_PAD + "  ")

    message = performance_data.get("message", "")

    def _styled(line: str) -> Text:
        """Style a plaintext artifact line: headers accent, bullets value, notices warn."""
        stripped = line.strip()
        style = theme.value
        if not stripped:
            return Text("")
        if stripped.startswith("⚠"):
            style = theme.warn
        elif stripped.startswith(("•", "-", "☐", "↺")) or line.startswith("  "):
            style = theme.desc
        elif stripped.endswith(":") or line == line.lstrip():
            style = f"bold {theme.accent}"
        return Text(_PAD + "  " + line, style=style, justify="left")

    actions = performance_data.get("actions") or ["1:1 Prep", "1:1 Complete", "6mo Review", "Notes", "Export", "Back"]
    btn_top, btn_mid, btn_bot = build_action_buttons(actions, action_sel)

    # ── Roster view — big ASCII engineer names (mirrors the intake mode picker) ──
    if view != "detail":
        roster = performance_data.get("roster", []) or []
        hints = performance_data.get("roster_hints", []) or []
        selected_idx = max(0, min(performance_data.get("selected_idx", 0), len(roster) - 1)) if roster else 0

        body: list = []
        if message:
            body.append(Text(_PAD + message, style=theme.accent_bright, justify="left"))
            body.append(Text(""))

        if not roster:
            body.append(Text(_PAD + "  No engineers found.", style=theme.muted, justify="left"))
            body.append(Text(""))
            body.append(Text(_PAD + "  Connect Jira or Azure DevOps (see Settings) — the roster is", style=theme.muted))
            body.append(Text(_PAD + "  built from the people assigned work on your board.", style=theme.muted))
        else:
            # Window the engineers that fit vertically, centred on the selection —
            # big ASCII rows are tall, so only a few show at once (▲/▼ mark the rest).
            budget = max(6, height - 16 - (2 if message else 0))
            start, end = _performance_roster_window(selected_idx, len(roster), budget)
            if start > 0:
                body.append(Text(_PAD + f"▲ {start} more", style=theme.dim, justify="left"))
                body.append(Text(""))
            for idx in range(start, end):
                name = roster[idx]
                hint = hints[idx] if idx < len(hints) else "1:1 prep · completion · 6-month review"
                card = {"title": name, "color": _accent, "available": True, "description": hint}
                is_sel = idx == selected_idx
                body.extend(
                    _build_mode_row(
                        card,
                        selected=is_sel,
                        shimmer_tick=shimmer_tick,
                        desc_reveal=desc_reveal if is_sel else 0,
                    )
                )
                if idx < end - 1:
                    body.append(Text(""))
            if end < len(roster):
                body.append(Text(""))
                body.append(Text(_PAD + f"▼ {len(roster) - end} more", style=theme.dim, justify="left"))

        content = Group(
            Text(""),
            title,
            Text(""),
            sub,
            Text(""),
            *body,
            Text(""),
            btn_top,
            btn_mid,
            btn_bot,
        )
        return build_page_panel(content, theme=PERFORMANCE_THEME, height=height)

    # ── Detail view — the produced artifact, scrollable ──────────────────────────
    body_lines: list = []
    if message:
        body_lines.append(Text(_PAD + "  " + message, style=theme.accent_bright, justify="left"))
        body_lines.append(Text(""))
    for line in performance_data.get("detail_lines", []) or ["(nothing to show)"]:
        body_lines.append(_styled(line))

    viewport_h = calc_viewport(height, header_h=6, action_h=4)
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll, always_show=True)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        viewport_renderable,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )

    return build_page_panel(content, theme=PERFORMANCE_THEME, height=height)


def _reporting_theme_swatch(palette: dict) -> Text:
    """A small strip of colored blocks previewing a palette's key roles."""
    swatch = Text()
    for role in ("bg2", "accent", "accent2", "fg", "muted"):
        swatch.append("  ", style=f"on {palette.get(role, '#888888')}")
    return swatch


def _reporting_detail_rows(report, theme, width: int) -> list:
    """One-line Text renderables for the report detail viewport.

    Renders the DeliveryReport artifact directly (headline banner, metric meters,
    titled theme/highlight sections, delivered-items table) instead of coloring
    pre-rendered plaintext by prefix. Every renderable is exactly one terminal
    line (prose is wrapped manually) so the line-based scroll math stays exact.
    """
    import textwrap

    from yeaboi.ui.shared._components import build_meter

    if report is None:
        return [Text(PAD + "  (nothing to show)", style=theme.muted, justify="left")]

    wrap_w = max(24, width - len(PAD) - 12)
    rows: list = []

    def _emoji_for(slot: str, default: str) -> str:
        for s, e in report.emoji_theme:
            if s == slot and e:
                return e
        return default

    def _wrapped(text: str, style: str, indent: str = "  ") -> None:
        for seg in textwrap.wrap(str(text), wrap_w) or [""]:
            rows.append(Text(PAD + indent + seg, style=style, justify="left"))

    def _section(slot: str, default_emoji: str, label: str) -> None:
        rows.append(Text(""))
        rows.append(Text(PAD + f"  {_emoji_for(slot, default_emoji)} {label}", style=f"bold {theme.accent}"))

    # Headline banner + period framing.
    if report.headline:
        _wrapped(report.headline, f"bold {theme.accent_bright}")
    dates = f"{report.period_start} → {report.period_end}".strip(" →")
    period_line = report.period_label + (f"  ·  {dates}" if dates else "")
    if report.sprint_names:
        period_line += f"  ·  {', '.join(report.sprint_names)}"
    rows.append(Text(PAD + "  " + period_line, style=theme.muted, justify="left", overflow="ellipsis", no_wrap=True))

    # Metrics — value + label + a compact meter scaled to the largest number.
    if report.metrics:
        _section("metrics", "📊", "By the numbers")
        numeric: dict[str, int] = {}
        for label, value in report.metrics:
            try:
                numeric[label] = int(str(value))
            except ValueError:
                pass
        max_n = max(numeric.values(), default=0)
        label_w = max(len(str(label)) for label, _ in report.metrics)
        for label, value in report.metrics:
            row = Text(PAD + "  ", justify="left", overflow="ellipsis", no_wrap=True)
            row.append(f"{label:<{label_w}}  ", style=theme.desc)
            row.append(f"{value:>4}", style=f"bold {theme.accent_bright}")
            if label in numeric and max_n > 0:
                row.append("  ")
                row.append_text(build_meter(numeric[label], max_n, width=12, theme=theme))
            rows.append(row)

    # Supporting signals — code/docs corroboration (reference, never the subject).
    if getattr(report, "supporting_signals", ()):
        _section("metrics", "🧾", "Supporting signals")
        kind_labels = {"pull_requests": "Pull requests", "commits": "Commits", "doc_updates": "Doc updates"}
        source_labels = {
            "github": "GitHub",
            "azuredevops": "Azure DevOps",
            "confluence": "Confluence",
            "notion": "Notion",
        }
        for sig in report.supporting_signals:
            row = Text(PAD + "  ", justify="left", overflow="ellipsis", no_wrap=True)
            row.append(f"{kind_labels.get(sig.kind, sig.kind)} · {source_labels.get(sig.source, sig.source)}  ")
            row.stylize(theme.desc)
            row.append(str(sig.count), style=f"bold {theme.accent_bright}")
            rows.append(row)
            for sample in sig.samples[:2]:
                rows.append(
                    Text(PAD + "    • " + sample, style=theme.muted, justify="left", no_wrap=True, overflow="ellipsis")
                )

    if report.executive_summary:
        _section("summary", "📋", "Executive summary")
        _wrapped(report.executive_summary, theme.value)

    for ttitle, outcomes in report.themes:
        _section("themes", "🧩", ttitle)
        for outcome in outcomes:
            for i, seg in enumerate(textwrap.wrap(str(outcome), wrap_w - 2) or [""]):
                bullet = "• " if i == 0 else "  "
                rows.append(Text(PAD + "  " + bullet + seg, style=theme.desc, justify="left"))

    if report.highlights:
        _section("highlights", "⭐", "Highlights")
        for hl in report.highlights:
            for i, seg in enumerate(textwrap.wrap(str(hl), wrap_w - 2) or [""]):
                bullet = "• " if i == 0 else "  "
                rows.append(Text(PAD + "  " + bullet + seg, style=theme.desc, justify="left"))

    if report.delivered_items:
        _section("thanks", "✅", f"Delivered items ({len(report.delivered_items)})")
        shown = report.delivered_items[:30]
        key_w = max((len(i.key) for i in shown if i.key), default=0)
        for item in shown:
            row = Text(PAD + "  ", justify="left", overflow="ellipsis", no_wrap=True)
            if key_w:
                row.append(f"{item.key:<{key_w}}  ", style=theme.id)
            row.append(item.title, style=theme.value)
            if item.status:
                row.append(f"  · {item.status}", style=theme.good)
            if item.assignee:
                row.append(f"  · {item.assignee}", style=theme.muted)
            rows.append(row)
        if len(report.delivered_items) > 30:
            rows.append(Text(PAD + f"  … and {len(report.delivered_items) - 30} more", style=theme.dim))

    if report.warnings:
        _section("summary", "⚠", "Notices")
        for warning in report.warnings:
            _wrapped(warning, theme.warn)

    return rows


def _build_reporting_theme_screen(
    reporting_data: dict,
    *,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
) -> Panel:
    """The Reporting palette picker — every theme previewed as a color swatch strip.

    Lists the built-in deck palettes plus any custom ones from
    ``~/.yeaboi/data/reporting_themes.json``; the footer tells the user where to
    add their own.
    """
    from yeaboi.ui.shared._components import REPORTING_THEME, build_reveal_subtitle, reporting_title

    theme = REPORTING_THEME
    title = reporting_title(shimmer_tick, width=width)
    sub = build_reveal_subtitle("Choose a presentation palette", sub_reveal, pad=PAD)
    names = reporting_data.get("theme_names", []) or ["midnight"]
    palettes = reporting_data.get("palettes", {}) or {}
    cursor = max(0, min(reporting_data.get("theme_cursor", 0), len(names) - 1))
    current = reporting_data.get("theme", "midnight")
    from yeaboi.reporting.themes import BUILTIN_PALETTES

    builtin_count = len(BUILTIN_PALETTES)  # built-ins are listed first (themes.all_palettes order)

    rows: list = []
    for idx, name in enumerate(names):
        focused = idx == cursor
        is_current = name == current
        row = Text(PAD + "  ", justify="left", overflow="ellipsis", no_wrap=True)
        row.append("‹ " if focused else "  ", style=theme.accent_bright)
        row.append("●" if is_current else "○", style=theme.accent_bright if is_current else theme.dim)
        row.append(f" {name:<12}", style="bold white" if focused else theme.accent if is_current else theme.desc)
        if focused:
            row.append("› ", style=theme.accent_bright)
        else:
            row.append("  ")
        row.append_text(_reporting_theme_swatch(palettes.get(name, {})))
        if idx >= builtin_count:
            row.append("  · custom", style=theme.dim)
        rows.append(row)

    header = _analysis_setup_header(
        "Theme",
        "↑/↓ preview · Enter selects · Esc keeps the current palette",
        brand="REPORTING SETUP",
        theme=theme,
    )
    footer = Text(PAD + "Add your own palettes in ~/.yeaboi/data/reporting_themes.json", style=theme.dim)
    # header_h counts ALL non-viewport rows: borders+padding 4, title block 9
    # (blank/title/blank/sub/blank), setup header 3, footer block 3, buttons 3+blank.
    viewport = _analysis_toggle_viewport(rows, cursor, height=height, header_h=23)

    actions = reporting_data.get("actions") or ["Select", "Back"]
    btn_top, btn_mid, btn_bot = build_action_buttons(actions, action_sel)
    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *header,
        viewport,
        Text(""),
        footer,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )
    return build_page_panel(content, theme=REPORTING_THEME, height=height)


def _build_reporting_style_screen(
    reporting_data: dict,
    *,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
) -> Panel:
    """The Reporting deck-style options list — one row per DeckStyle field.

    ↑/↓ move the cursor, Space changes the focused option in a working copy; the
    runner's Save button persists it to ``~/.yeaboi/data/reporting_prefs.json``,
    Reset restores the defaults, Back/Esc discards unsaved edits. Color rows
    preview their resolved hex as a small swatch against the currently selected
    palette.
    """
    from yeaboi.reporting.style import CONTENT_FIT_LABELS, FONT_PRESETS, STYLE_FIELDS, DeckStyle, resolve_color
    from yeaboi.ui.shared._components import REPORTING_THEME, build_reveal_subtitle, reporting_title

    theme = REPORTING_THEME
    title = reporting_title(shimmer_tick, width=width)
    sub = build_reveal_subtitle("Customize the presentation style", sub_reveal, pad=PAD)
    style = reporting_data.get("style") or DeckStyle()
    palette = (reporting_data.get("palettes", {}) or {}).get(reporting_data.get("theme", "midnight"), {})
    cursor = max(0, min(reporting_data.get("style_cursor", 0), len(STYLE_FIELDS) - 1))

    rows: list = []
    for idx, (field, label, kind) in enumerate(STYLE_FIELDS):
        focused = idx == cursor
        value = getattr(style, field)
        row = Text(PAD + "  ", justify="left", overflow="ellipsis", no_wrap=True)
        row.append("‹ " if focused else "  ", style=theme.accent_bright)
        row.append(f"{label:<28}", style="bold white" if focused else theme.desc)
        row.append("› " if focused else "  ", style=theme.accent_bright)
        if kind == "bool":
            row.append("● on" if value else "○ off", style=theme.good if value else theme.dim)
        elif kind == "color":
            row.append(value if value else "theme default", style=theme.accent if value else theme.dim)
            resolved = resolve_color(value, palette, "")
            if resolved:
                row.append("  ")
                row.append("  ", style=f"on {resolved}")
        elif kind == "choice":
            if field == "font_family":
                pretty = FONT_PRESETS[value]["label"]
            elif field == "content_fit":
                pretty = CONTENT_FIT_LABELS.get(value, value)
            else:
                pretty = value
            row.append(str(pretty), style=theme.accent)
        elif kind == "int":
            row.append(str(value), style=theme.accent)
        else:  # text
            row.append(value if value else "(none)", style=theme.accent if value else theme.dim)
        rows.append(row)

    header = _analysis_setup_header(
        "Style",
        "↑/↓ choose · Space changes · Save persists · Esc discards",
        brand="REPORTING SETUP",
        theme=theme,
    )
    footer = Text(
        PAD + "Save writes ~/.yeaboi/data/reporting_prefs.json — applies to the deck and .pptx", style=theme.dim
    )
    # header_h counts ALL non-viewport rows: borders+padding 4, title block 9
    # (blank/title/blank/sub/blank), setup header 3, footer block 3, buttons 3+blank.
    viewport = _analysis_toggle_viewport(rows, cursor, height=height, header_h=23)

    actions = reporting_data.get("actions") or ["Save", "Reset", "Back"]
    btn_top, btn_mid, btn_bot = build_action_buttons(actions, action_sel)
    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *header,
        viewport,
        Text(""),
        footer,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )
    return build_page_panel(content, theme=REPORTING_THEME, height=height)


def _build_reporting_screen(
    reporting_data: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    anon_note: str = "",
) -> Panel:
    """Build the Reporting screen using shared TUI components.

    Four views, all rendered here (the run page owns which is active):
    - "picker": choose a reporting period (Last week / Last sprint / Last month /
      Whole quarter / Custom date range) as analysis-style setup toggle rows.
    - "sprint_select": for a quarter, a multi-select of sprints (same toggle-row
      treatment) with the quarter's sprints pre-checked — Space toggles, Enter
      generates.
    - "theme_select": palette list with color-swatch previews (built-ins + custom)
      — delegated to ``_build_reporting_theme_screen``.
    - "style_select": the persisted deck-style options list — delegated to
      ``_build_reporting_style_screen``.
    - "detail": the generated report rendered richly from the DeliveryReport
      artifact (``_reporting_detail_rows``), scrollable, with Export / Share /
      Anonymize / Theme / Back buttons.

    reporting_data keys: session_name, view, periods (list[(key, label, hint)]),
    selected_idx (int), theme (str), report (DeliveryReport | None), detail_title
    (str), actions (list[str]), message (str), sources_summary (str — the picker's
    "Sources: … · Code: … · Docs: …" status line), quarter_label (str), sprints
    (list[SprintRef]), sprint_cursor (int), sprint_checked (set[int]),
    theme_names (list[str]), palettes (dict), theme_cursor (int), style
    (DeckStyle), style_cursor (int), style_summary (str — the picker's
    "Style: …" status line).

    Uses REPORTING_THEME (indigo) with shared buttons, scrollbar, and viewport.

    # See docs: "Reporting Mode" — TUI page
    """
    from yeaboi.ui.shared._components import REPORTING_THEME, build_reveal_subtitle, reporting_title

    theme = REPORTING_THEME
    view = reporting_data.get("view", "picker")
    if view == "theme_select":
        return _build_reporting_theme_screen(
            reporting_data,
            width=width,
            height=height,
            action_sel=action_sel,
            shimmer_tick=shimmer_tick,
            sub_reveal=sub_reveal,
        )
    if view == "style_select":
        return _build_reporting_style_screen(
            reporting_data,
            width=width,
            height=height,
            action_sel=action_sel,
            shimmer_tick=shimmer_tick,
            sub_reveal=sub_reveal,
        )

    title = reporting_title(shimmer_tick, width=width)
    session_name = reporting_data.get("session_name", "")
    deck_theme = reporting_data.get("theme", "midnight")
    palettes = reporting_data.get("palettes", {}) or {}
    message = reporting_data.get("message", "")

    if view == "detail":
        sub_text = reporting_data.get("detail_title", "") or "Delivery Report"
    elif view == "sprint_select":
        sub_text = f"Select sprints for {reporting_data.get('quarter_label', 'the quarter')}"
    else:
        sub_text = f"Report delivered work — {session_name}" if session_name else "Report delivered work"
    if anon_note:  # anonymized detail view: the subtitle carries the "N masked" indicator
        sub_text = anon_note
    sub = build_reveal_subtitle(sub_text, sub_reveal, pad=_PAD + "  ")

    actions = reporting_data.get("actions") or ["Generate Report", "Theme", "Back"]
    btn_top, btn_mid, btn_bot = build_action_buttons(actions, action_sel)

    # ── Sprint-select view — multi-select of the quarter's sprints ───────────────
    if view == "sprint_select":
        sprints = reporting_data.get("sprints", []) or []
        cursor = max(0, min(reporting_data.get("sprint_cursor", 0), len(sprints) - 1)) if sprints else 0
        checked = reporting_data.get("sprint_checked", set()) or set()

        rows: list = []
        if message:
            rows.append(Text(_PAD + "  " + message, style=theme.accent_bright, justify="left"))
            rows.append(Text(""))
        for idx, sp in enumerate(sprints):
            rng = f"{sp.start_date} → {sp.end_date}" if sp.start_date else "no dates"
            note = rng + ("  · in quarter" if getattr(sp, "in_quarter", False) else "")
            rows.append(
                _analysis_toggle_row(
                    sp.name, "", focused=idx == cursor, selected=idx in checked, note=note, theme=theme
                )
            )
        if not sprints:
            rows.append(Text(_PAD + "  No sprints found.", style=theme.muted, justify="left"))

        header = _analysis_setup_header(
            "Sprints",
            f"Space toggles · {len(checked)} selected · Enter generates · Esc back",
            brand="REPORTING SETUP",
            theme=theme,
        )
        viewport_h = calc_viewport(height, header_h=13, action_h=4)
        total_lines = len(rows)
        # Window around the cursor row so it stays visible as you move.
        cursor_line = min(total_lines - 1, cursor + (2 if message else 0)) if sprints else 0
        max_scroll = max(0, total_lines - viewport_h)
        start = 0 if total_lines <= viewport_h else max(0, min(cursor_line - viewport_h // 2, max_scroll))
        visible = rows[start : start + viewport_h]

        _sb_text = build_scrollbar(viewport_h, total_lines, start, max_scroll)
        padded_lines = list(visible)
        for _ in range(max(0, viewport_h - len(visible))):
            padded_lines.append(Text(""))
        if _sb_text is not None:
            from rich.table import Table as _SbTable

            _vp = _SbTable(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
            _vp.add_column(ratio=1)
            _vp.add_column(width=1)
            _vp.add_row(Group(*padded_lines), _sb_text)
            viewport_renderable = _vp
        else:
            viewport_renderable = Group(*padded_lines)

        content = Group(
            Text(""),
            title,
            Text(""),
            sub,
            Text(""),
            *header,
            viewport_renderable,
            Text(""),
            btn_top,
            btn_mid,
            btn_bot,
        )
        return build_page_panel(content, theme=REPORTING_THEME, height=height)

    # ── Picker view — choose the reporting period (setup toggle rows) ────────────
    if view != "detail":
        periods = reporting_data.get("periods", []) or []
        selected_idx = max(0, min(reporting_data.get("selected_idx", 0), len(periods) - 1)) if periods else 0

        body: list = []
        if message:
            body.append(Text(_PAD + message, style=theme.accent_bright, justify="left"))
            body.append(Text(""))
        body.extend(
            _analysis_setup_header(
                "Period",
                "↑/↓ choose · ←/→ buttons · Enter generates",
                brand="REPORTING SETUP",
                theme=theme,
            )
        )
        for idx, (_key, label, hint) in enumerate(periods):
            is_sel = idx == selected_idx
            body.append(_analysis_toggle_row(label, hint, focused=is_sel, selected=is_sel, theme=theme))
        body.append(Text(""))
        theme_row = Text(PAD + f"Presentation theme: {deck_theme}  ", style=theme.muted, justify="left")
        theme_row.append_text(_reporting_theme_swatch(palettes.get(deck_theme, {})))
        body.append(theme_row)
        sources_summary = reporting_data.get("sources_summary", "")
        if sources_summary:
            body.append(
                Text(PAD + sources_summary, style=theme.muted, justify="left", no_wrap=True, overflow="ellipsis")
            )
        style_summary = reporting_data.get("style_summary", "")
        if style_summary:
            body.append(
                Text(
                    PAD + f"Style: {style_summary}",
                    style=theme.muted,
                    justify="left",
                    no_wrap=True,
                    overflow="ellipsis",
                )
            )

        content = Group(
            Text(""),
            title,
            Text(""),
            sub,
            Text(""),
            *body,
            Text(""),
            btn_top,
            btn_mid,
            btn_bot,
        )
        return build_page_panel(content, theme=REPORTING_THEME, height=height)

    # ── Detail view — the generated report, rendered richly, scrollable ──────────
    # Transient status messages (e.g. "Exported PowerPoint to …") render as a
    # PINNED one-row banner in the header area, never inside the scroll viewport —
    # otherwise a scrolled-down reader would miss them entirely (standup pattern).
    banner: Text | None = None
    if message:
        # no_wrap is load-bearing: header_h counts the banner as exactly one row.
        banner = Text(PAD + message, style=theme.accent_bright, justify="left", no_wrap=True, overflow="ellipsis")

    body_lines: list = list(_reporting_detail_rows(reporting_data.get("report"), theme, width))

    # header_h must match the Group rows above the viewport exactly (blank +
    # 6-row title + blank + sub + optional banner + blank), else the button
    # bottom border falls off the fixed-height panel.
    header_h = 10 + (1 if banner is not None else 0)
    if banner is not None and (height - 4) - header_h - 4 < 3:
        # Terminal too short — drop the banner rather than push the buttons off.
        banner = None
        header_h = 10
    viewport_h = calc_viewport(height, header_h=header_h, action_h=4)
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        *((banner,) if banner is not None else ()),
        Text(""),
        viewport_renderable,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )
    return build_page_panel(content, theme=REPORTING_THEME, height=height)


def _build_roadmap_screen(
    roadmap_data: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    anon_note: str = "",
) -> Panel:
    """Build the Roadmap intake screen using shared TUI components.

    Two views, both rendered here (the run page owns which is active):
    - "source": choose where the quarterly roadmap lives (Confluence / Notion /
      local file) with ▲/▼, then Select to enter the page URL / file path.
    - "results": the analysis — summary, a *selectable* recommended-project list
      (▸ cursor, [Small]/[Large] badges, rationale), and a ⚠ Notices block —
      with Plan This / Re-analyze / Change Source / Back buttons.

    Saved roadmaps are listed as amber-tagged cards inside the Planning
    "Your projects" list (see _build_project_list_screen), not here.

    roadmap_data keys: view ("source"|"results"), sources (list[(key, label,
    hint)]), selected_idx (int), analysis (RoadmapAnalysis | None),
    project_cursor (int), actions (list[str]), message (str), busy (bool),
    source_label (str), analyzed_at (str).

    A Planning sub-page — uses PLANNING_THEME + planning_title (not a new mode
    theme), with shared buttons, scrollbar, and viewport.

    # See docs: "Roadmap Intake" — TUI page
    """
    from yeaboi.ui.shared._components import PLANNING_THEME, build_reveal_subtitle

    theme = PLANNING_THEME
    title = planning_title(shimmer_tick, width=width)
    view = roadmap_data.get("view", "source")
    message = roadmap_data.get("message", "")
    actions = roadmap_data.get("actions") or ["Select", "Back"]
    btn_top, btn_mid, btn_bot = build_action_buttons(actions, action_sel)

    busy = bool(roadmap_data.get("busy"))
    if busy:
        sub_text = "Analyzing your roadmap…"
    elif view == "results":
        source_label = roadmap_data.get("source_label", "")
        analyzed_at = roadmap_data.get("analyzed_at", "")
        sub_text = " · ".join(x for x in (source_label, f"analyzed {analyzed_at}" if analyzed_at else "") if x)
        sub_text = sub_text or "Roadmap analysis"
    else:
        sub_text = "Where does your quarterly roadmap live?"
    if anon_note and not busy:  # anonymized results: the subtitle carries the "N masked" indicator
        sub_text = anon_note
    sub = build_reveal_subtitle(sub_text, sub_reveal, pad=_PAD + "  ")

    # ── Busy overlay — while the analysis worker runs, show only the spinner so
    # the source options / buttons underneath don't confuse the user. ─────────────
    if busy:
        spinner = Text(_PAD + message, style=theme.accent_bright, justify="left") if message else Text("")
        return build_page_panel(
            Group(Text(""), title, Text(""), sub, Text(""), Text(""), spinner),
            theme=PLANNING_THEME,
            height=height,
        )

    # ── Source view — pick where the roadmap lives ───────────────────────────────
    if view == "source":
        sources = roadmap_data.get("sources", []) or []
        selected_idx = max(0, min(roadmap_data.get("selected_idx", 0), len(sources) - 1)) if sources else 0

        body: list = []
        if message:
            body.append(Text(_PAD + message, style=theme.accent_bright, justify="left"))
            body.append(Text(""))
        body.append(Text(_PAD + "Choose a roadmap source:", style=f"bold {theme.accent}", justify="left"))
        body.append(Text(""))
        for idx, (_key, label, hint) in enumerate(sources):
            is_sel = idx == selected_idx
            marker = "▸ " if is_sel else "  "
            row = Text(justify="left")
            row.append(_PAD + marker, style=theme.accent_bright if is_sel else theme.dim)
            row.append(label, style=theme.value if is_sel else theme.desc)
            body.append(row)
            if hint:
                body.append(Text(_PAD + "    " + hint, style=theme.muted, justify="left"))
            body.append(Text(""))

        content = Group(
            Text(""),
            title,
            Text(""),
            sub,
            Text(""),
            *body,
            Text(""),
            btn_top,
            btn_mid,
            btn_bot,
        )
        return build_page_panel(content, theme=PLANNING_THEME, height=height)

    # ── Results view — summary + bordered project cards (selected expands) ────
    # Mirrors the list branch above: _Padding-wrapped rounded cards with peek
    # stubs and no scrollbar. Unlike the fixed-height project list, the selected
    # card grows to reveal the full description + rationale, so a variable-height
    # window (_window_project_cards) replaces _compute_viewport.
    import textwrap

    from rich.padding import Padding as _Padding

    from yeaboi.ui.mode_select.screens._project_cards import (
        _PEEK_H,
        _ROADMAP_UNSEL_H,
        _build_empty_state_card,
        _build_peek_above,
        _build_peek_below,
        _build_roadmap_notices_card,
        _build_roadmap_project_card,
        _window_project_cards,
    )

    analysis = roadmap_data.get("analysis")
    cursor = roadmap_data.get("project_cursor", 0)
    projects = tuple(getattr(analysis, "projects", ()) or ())
    cursor = max(0, min(cursor, len(projects) - 1)) if projects else 0
    summary = getattr(analysis, "summary", "") if analysis is not None else ""
    warnings = tuple(getattr(analysis, "warnings", ()) or ()) if analysis is not None else ()

    box_w = min(72, max(32, width - len(_PAD) - 4))
    inner_w = max(16, box_w - 6)  # border(2) + padding(4)
    card_pad = (0, 0, 0, len(_PAD))

    body: list = []
    if message:
        body.append(Text(_PAD + "  " + message, style=theme.accent_bright, justify="left"))
        body.append(Text(""))
    if summary:
        body.append(Text(_PAD + "  " + summary, style=theme.desc, justify="left"))
        body.append(Text(""))

    if not projects:
        body.append(
            _Padding(
                _build_empty_state_card(
                    selected=False,
                    title="No projects extracted",
                    subtitle="from the roadmap — Re-analyze or Change Source",
                    box_w=box_w,
                ),
                card_pad,
            )
        )
        # The zero-project fallback is exactly where the warnings carry the
        # failure reason (LLM/auth/ingest errors) — always show them here.
        if warnings:
            base = calc_viewport(height, header_h=6, action_h=4) - len(body)
            if base >= 2 + 2 + len(warnings):  # blank + borders + title + bullets
                body.append(Text(""))
                body.append(_Padding(_build_roadmap_notices_card(warnings, box_w=box_w), card_pad))
            else:
                plural = "s" if len(warnings) != 1 else ""
                body.append(
                    Text(
                        _PAD + f"⚠ {len(warnings)} Notice{plural} — enlarge the window to view",
                        style=theme.muted,
                        justify="left",
                    )
                )
    else:
        # Key hint (like the list view's) then the card viewport.
        body.append(Text(_PAD + "↑/↓ choose a project · Plan This to plan it", style=theme.muted, justify="left"))
        body.append(Text(""))

        # Budget: the shared viewport line count, minus the fixed lines already
        # placed above the cards, minus room reserved for the notices block (so
        # the cards never push the bottom buttons off-panel). Peek-stub space is
        # accounted for inside _window_project_cards, not reserved here.
        base = calc_viewport(height, header_h=6, action_h=4) - len(body)
        notices_full = (1 + 2 + len(warnings)) if warnings else 0  # blank + border + title + bullets
        notices_mode = "card" if warnings else "none"
        available_h = base - notices_full
        if warnings and available_h < _ROADMAP_UNSEL_H:
            notices_mode = "hint"  # not enough room for the card — one-line hint instead
            available_h = base - 1
        available_h = max(_ROADMAP_UNSEL_H, available_h)

        # Wrap the selected project's full description + rationale, capped so its
        # (taller) card — plus room for peek stubs when there are other cards —
        # still fits the viewport.
        sel = projects[cursor]
        wrapped: list[str] = []
        for para in (getattr(sel, "description", "") or "").strip().splitlines() or [""]:
            wrapped.extend(textwrap.wrap(para, inner_w) or [""])
        rationale = (getattr(sel, "rationale", "") or "").strip()
        if rationale:
            wrapped.append("")
            wrapped.extend(textwrap.wrap("Why now: " + rationale, inner_w))
        peek_reserve = 2 * _PEEK_H if len(projects) > 1 else 0
        max_body = max(0, available_h - _ROADMAP_UNSEL_H - 1 - peek_reserve)
        if len(wrapped) > max_body:
            wrapped = wrapped[:max_body]
            if wrapped:
                wrapped[-1] = (wrapped[-1][: max(0, inner_w - 1)]).rstrip() + "…"
        body_lines = tuple(x for x in wrapped if x is not None)

        heights = [
            (_ROADMAP_UNSEL_H + 1 + len(body_lines)) if (idx == cursor and body_lines) else _ROADMAP_UNSEL_H
            for idx in range(len(projects))
        ]
        start, end, peek_above, peek_below = _window_project_cards(heights, cursor, available_h)

        def _pname(i: int) -> str:
            return getattr(projects[i], "name", "") or "(unnamed)"

        if peek_above:
            body.append(_Padding(_build_peek_above(title=_pname(start - 1), box_w=box_w), card_pad))
        for idx in range(start, end):
            index = getattr(projects[idx], "priority", 0) or (idx + 1)
            card = _build_roadmap_project_card(
                projects[idx],
                index=index,
                selected=(idx == cursor),
                box_w=box_w,
                body_lines=body_lines if idx == cursor else (),
            )
            body.append(_Padding(card, card_pad))
            if idx < end - 1:
                body.append(Text(""))
        if peek_below:
            body.append(_Padding(_build_peek_below(title=_pname(end), box_w=box_w), card_pad))

        # Notices below the cards (a distinct amber card, or a one-line hint).
        if notices_mode == "card":
            body.append(Text(""))
            body.append(_Padding(_build_roadmap_notices_card(warnings, box_w=box_w), card_pad))
        elif notices_mode == "hint":
            plural = "s" if len(warnings) != 1 else ""
            body.append(Text(_PAD + f"⚠ {len(warnings)} Notice{plural} — enlarge the window to view", style=theme.warn))

    # No scrollbar geometry to publish — the card viewport uses peek stubs, like
    # the list view (publish an empty geometry so stale scroll state clears).
    publish_geometry(scroll_meta, 0, 0)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )
    return build_page_panel(content, theme=PLANNING_THEME, height=height)


def _build_retro_screen(
    retro_data: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    anon_note: str = "",
) -> Panel:
    """Build the Retro board screen using shared TUI components.

    Shows the live share code + URL teammates use to join, then the four retro
    grids (What went well / What didn't go well / Action items / Demos) with the
    cards added so far. Uses RETRO_THEME (teal) with shared buttons, scrollbar,
    and viewport. The host's TUI is a monitoring view — the four-column layout
    lives in the browser; here the grids stack vertically so narrow terminals and
    the shared scrollbar behave like every other page.

    retro_data keys: session_name, display_code, public_url (the Cloudflare tunnel
    URL — the only participant link there is, and empty until it comes up),
    link_failed (the tunnel gave up; empty public_url means "waiting" without it),
    host_url (optional private token'd host link), message (transient status),
    grids (dict[grid_key -> list[RetroCard]]), actions (optional button-label
    list).

    # See docs: "Retro" — TUI page
    """
    from yeaboi.retro.board import CARRIED_STATUS_LABELS, RETRO_GRID_LABELS, RETRO_GRIDS
    from yeaboi.ui.shared._components import RETRO_THEME, build_reveal_subtitle, retro_title

    theme = RETRO_THEME
    title = retro_title(shimmer_tick)
    session_name = retro_data.get("session_name", "")
    sub_text = f"Sprint retro for {session_name}" if session_name else "Sprint retro"
    if anon_note:  # anonymized: the subtitle carries the "N masked — review" indicator
        sub_text = anon_note
    sub = build_reveal_subtitle(sub_text, sub_reveal, pad=_PAD + "  ")

    body_lines: list = []

    def _heading(text: str) -> None:
        # The first heading needs no leading blank: the subtitle's trailing blank
        # already spaces it. Emitting one here on top of that gave a doubled blank
        # under the subtitle. A message (when present) is the first body line, so
        # this still separates the message from the section that follows it.
        if body_lines:
            body_lines.append(Text(""))
        h = Text(_PAD + "  ", justify="left")
        h.append(text, style=f"bold {theme.accent}")
        body_lines.append(h)
        body_lines.append(Text(_PAD + "  " + "─" * min(len(text), 40), style=theme.sep, justify="left"))

    def _row(label: str, value: str, value_style: str = "") -> None:
        r = Text(_PAD + "    ", justify="left")
        r.append(f"{label}:  ", style=theme.muted)
        r.append(str(value), style=value_style or theme.value)
        body_lines.append(r)

    def _line(text: str, style: str = "") -> None:
        body_lines.append(Text(_PAD + "    " + text, style=style or theme.value, justify="left"))

    def _wrapped(text: str, style: str, *, indent: str = "      ") -> None:
        import textwrap

        wrap_w = max(24, width - len(_PAD) - len(indent) - 6)
        for chunk in textwrap.wrap(text, width=wrap_w) or [""]:
            body_lines.append(Text(_PAD + indent + chunk, style=style, justify="left"))

    # The four-column grid below is flattened with the module-level
    # _render_to_lines helper so it scrolls line-by-line with everything else.

    # ── Join info, at the top ─────────────────────────────────────
    # Live-board only: a saved-run snapshot has no share code or link, so the hub
    # passes snapshot=True to suppress this whole block (the report replays the
    # grids + carried actions, not a resumable board). Content is unindented —
    # aligned with the heading — and the server-ready/status note sits right under
    # the heading.
    #
    # Two audiences, told apart. This was a flat list of four labels — Share
    # code, LAN URL, Remote URL, Host link — which reads fine until you are
    # mid-ceremony working out which one to paste into the team chat, and the
    # wrong answer hands over the admin secret. The participant's link and code
    # now lead, and the host's sits below a blank line under a label that says
    # whose it is.
    #
    # Kept to within a line of the old block's height on purpose: this sits above
    # the grids on every frame, and every row added here is a row of cards pushed
    # off the viewport.
    message = retro_data.get("message", "")

    def _jrow(label: str, value: str, value_style: str = "") -> None:
        r = Text(_PAD + "  ", justify="left")
        r.append(f"{label}:  ", style=theme.muted)
        r.append(str(value), style=value_style or theme.value)
        body_lines.append(r)

    def _jline(text: str, style: str = "") -> None:
        body_lines.append(Text(_PAD + "  " + text, style=style or theme.value, justify="left"))

    def _jlink(label: str, url: str, value_style: str = "") -> None:
        """A join-block label+URL row: level with the heading, and never soft-wrapped."""
        body_lines.extend(
            _link_lines(
                label,
                str(url),
                width=width,
                label_style=theme.muted,
                url_style=value_style or theme.value,
                indent="  ",
            )
        )

    if not retro_data.get("snapshot"):
        _heading("Send this to your team")
        if message:
            body_lines.append(Text(_PAD + "  " + message, style=theme.accent_bright, justify="left"))
        # The tunnel URL is the only participant link there is — the board itself
        # binds loopback. Until it lands there is nothing to show, so the row says
        # so rather than printing an address (an earlier LAN link here worked only
        # for the same Wi-Fi, and reliably got pasted to someone remote).
        # Labels stay short so the URL fits beside them at 80 columns; the reach
        # note goes in the muted line below, where it costs nothing when it wraps.
        public_url = retro_data.get("public_url", "")
        link_failed = bool(retro_data.get("link_failed"))
        if public_url:
            _jlink("Participant link", public_url, f"bold {theme.accent_bright}")
        else:
            _jrow("Participant link", "unavailable" if link_failed else "preparing…", theme.muted)
        _jrow("Share code", retro_data.get("display_code", "—"), f"bold {theme.accent_bright}")
        # One line, whichever state: this block sits above the grids on every
        # frame, so a taller variant would push cards off the viewport. The
        # failed case needs its own line — left on "a few seconds" it would keep
        # promising a link that is never coming, directly under a status saying
        # the opposite.
        if public_url:
            _jline("Copy Invite sends one link that carries the code — one click and they're in.", theme.muted)
        elif link_failed:
            _jline("The secure link didn't start — press Retry Link to try again.", theme.warn)
        else:
            _jline("Setting up the secure link — a few seconds. The code is already live.", theme.muted)

        host_url = retro_data.get("host_url", "")
        if host_url:
            body_lines.append(Text(""))
            _jlink("Host link (yours only)", host_url, theme.muted)
            _jline("Skips the code and holds the host controls — never send it.", theme.muted)
        body_lines.append(Text(""))
    elif message:
        body_lines.append(Text(_PAD + "  " + message, style=theme.accent_bright, justify="left"))

    # ── The four grids, as a four-column table ────────────────────
    from rich.table import Table as _GridTable

    grids = retro_data.get("grids") or {}
    _grid_indent = _PAD + "  "
    grid_w = max(40, width - 4 - len(_grid_indent) - 2)  # panel/pad + left indent + scrollbar gutter
    # Four columns with a 2-space gap between each (padding (0,1), no edge pad):
    # total ≈ 4·col_w + 3·2. Leave a couple columns of slack so the table never sits
    # exactly at the render width (which wraps the last column) and never overflows.
    col_w = max(12, (grid_w - 8) // 4)

    def _grid_column(key: str):
        import textwrap

        cards = grids.get(key, [])
        col: list = []
        head = Text(no_wrap=True, overflow="crop")
        head.append(RETRO_GRID_LABELS[key], style=f"bold {theme.accent}")
        head.append(f"  ({len(cards)})", style=theme.muted)
        col.append(head)
        col.append(Text("─" * col_w, style=theme.sep, no_wrap=True, overflow="crop"))
        if not cards:
            col.append(Text("No cards yet.", style=theme.muted, no_wrap=True, overflow="crop"))
        for c in cards:
            origin = getattr(c, "origin", "web")
            if origin == "ai":
                who, card_style = "🤖 AI", theme.accent
            elif origin == "carryover":
                who, card_style = "↩ carried", theme.accent
            else:
                who, card_style = (getattr(c, "author", "") or "anon"), theme.value
            for i, chunk in enumerate(textwrap.wrap(c.text, width=max(6, col_w - 2)) or [""]):
                line = Text(no_wrap=True, overflow="crop")
                line.append(("• " if i == 0 else "  "), style=card_style)
                line.append(chunk, style=card_style)
                col.append(line)
            col.append(Text(f"  — {who}", style=theme.dim, no_wrap=True, overflow="crop"))
        return Group(*col)

    _table = _GridTable(show_header=False, show_edge=False, box=None, padding=(0, 1), pad_edge=False)
    for _ in range(len(RETRO_GRIDS)):
        _table.add_column(width=col_w, overflow="crop")
    _table.add_row(*[_grid_column(key) for key in RETRO_GRIDS])
    body_lines.extend(_render_to_lines(_table, grid_w, _grid_indent))

    # ── Last sprint's actions (progress review) ───────────────────
    # Set from teammates' browsers (the review column); the host view is read-only,
    # matching the live-board-is-browser model. Hidden when there's no prior retro.
    carried = retro_data.get("carried") or []
    if carried:
        done_n = sum(1 for c in carried if getattr(c, "status", "") in ("done", "not_relevant"))
        _heading(f"Last sprint's actions  ({done_n}/{len(carried)} resolved)")
        _line("Teammates set each status in the browser — review before generating new actions.", theme.muted)
        for c in carried:
            status = getattr(c, "status", "") or "pending"
            badge = CARRIED_STATUS_LABELS.get(status, status)
            _wrapped(c.text, theme.value, indent="    • ")
            body_lines.append(Text(_PAD + "        " + f"[{badge}]", style=theme.dim, justify="left"))

    # ── Layout using shared components ────────────────────────────
    # The bar wraps, so its height is measured rather than assumed — seven
    # buttons do not fit an 80-column terminal on one line, and a hardcoded
    # action_h would push the second row off the bottom of the panel.
    #
    # `width - _PANEL_BORDER_W`, because _wrap_actions budgets against the panel's
    # INNER width and `width` is the console's. Passing the outer width let a row
    # pack to exactly the border: at 80 columns the bar came to 80 and the panel
    # interior is 78, so each row soft-wrapped and shoved the second bank of
    # buttons off the bottom — selectable, invisible, the very bug the wrapping
    # was added to fix.
    actions = retro_data.get("actions") or ["Generate Action Items", "Export", "Close"]
    inner_w = width - _PANEL_BORDER_W
    action_lines = build_action_rows(actions, action_sel, width=inner_w)

    viewport_h = calc_viewport(height, header_h=6, action_h=action_rows_height(actions, inner_w))
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll, always_show=True)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        viewport_renderable,
        Text(""),
        *action_lines,
    )

    panel = build_page_panel(content, theme=RETRO_THEME, height=height)
    # The four-column card grid runs to the right edge — no free margin, so
    # the duck's shared bubble is suppressed here (he still bobs and quacks).
    panel._bubble_room = 0
    return panel


_voice_hint_cache: str | None = None


def _voice_download_hint() -> str:
    """First-run speech-model hint, probed ONCE per process.

    Screen builders run per frame — importlib probes inside the render loop
    would run dozens of times per second while the transcribing state shows.
    """
    global _voice_hint_cache
    if _voice_hint_cache is None:
        try:
            from yeaboi import voice

            _voice_hint_cache = (
                " (downloading the speech model on first run)"
                if voice.is_voice_available()[0] and not voice.is_model_loaded()
                else ""
            )
        except Exception:  # a broken optional dep must never kill a render
            _voice_hint_cache = ""
    return _voice_hint_cache


def _build_poker_screen(
    poker_data: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
) -> Panel:
    """Build the Scrum Poker screen using shared TUI components.

    Three dict-driven views (the retro/reporting convention):
      * ``pick``     — the setup wizard's ↑/↓ option list (source / scope / sprint).
      * ``state``    — the live monitoring view: join info + the current ticket,
                       who has voted (values only after the reveal — the same
                       secrecy the browser gets), and the ticket list. The host
                       *drives* the session from their browser (the admin link);
                       this view is for monitoring, like retro's.
      * ``snapshot`` — a saved run replayed from its PokerReport (the hub).

    poker_data keys: message, actions, session_name, display_code, host_url,
    public_url (the Cloudflare tunnel URL — the only participant link there is,
    and empty until it comes up), link_failed (the tunnel gave up), pick {title,
    hint, options[(label, sub)], sel}, state (a ``PokerBoard.state_snapshot()``
    dict), report (a PokerReport for snapshots).

    # See docs: "Poker" — TUI page
    """
    from yeaboi.ui.shared._components import POKER_THEME, build_reveal_subtitle, poker_title

    theme = POKER_THEME
    title = poker_title(shimmer_tick)
    session_name = poker_data.get("session_name", "")
    sub_text = poker_data.get("subtitle") or (
        f"Planning poker for {session_name}" if session_name else "Planning poker"
    )
    sub = build_reveal_subtitle(sub_text, sub_reveal, pad=_PAD + "  ")

    body_lines: list = []

    def _heading(text: str) -> None:
        # The first heading needs no leading blank: the subtitle's trailing blank
        # already spaces it. Emitting one here on top of that gave a doubled blank
        # under the subtitle. A message (when present) is the first body line, so
        # this still separates the message from the section that follows it.
        if body_lines:
            body_lines.append(Text(""))
        h = Text(_PAD + "  ", justify="left")
        h.append(text, style=f"bold {theme.accent}")
        body_lines.append(h)
        body_lines.append(Text(_PAD + "  " + "─" * min(len(text), 40), style=theme.sep, justify="left"))

    def _row(label: str, value: str, value_style: str = "") -> None:
        r = Text(_PAD + "    ", justify="left")
        r.append(f"{label}:  ", style=theme.muted)
        r.append(str(value), style=value_style or theme.value)
        body_lines.append(r)

    def _link(label: str, url: str, value_style: str = "") -> None:
        """A label+URL row that never soft-wraps out of the viewport's budget."""
        body_lines.extend(
            _link_lines(label, str(url), width=width, label_style=theme.muted, url_style=value_style or theme.value)
        )

    def _line(text: str, style: str = "") -> None:
        body_lines.append(Text(_PAD + "    " + text, style=style or theme.value, justify="left"))

    def _wrapped(text: str, style: str, *, indent: str = "      ") -> None:
        import textwrap

        wrap_w = max(24, width - len(_PAD) - len(indent) - 6)
        for chunk in textwrap.wrap(text, width=wrap_w) or [""]:
            body_lines.append(Text(_PAD + indent + chunk, style=style, justify="left"))

    def _pts(value) -> str:
        if value is None:
            return "—"
        return str(int(value)) if float(value) == int(value) else str(value)

    message = poker_data.get("message", "")
    if message:
        body_lines.append(Text(_PAD + "  " + message, style=theme.accent_bright, justify="left"))

    pick = poker_data.get("pick")
    state = poker_data.get("state")
    report = poker_data.get("report")
    sel_line: int | None = None  # pick view: the selected row's line index (auto-scroll target)

    if pick is not None:
        # ── Wizard picker view ────────────────────────────────────
        _heading(pick.get("title", "Choose"))
        if pick.get("hint"):
            _line(pick["hint"], theme.muted)
            body_lines.append(Text(""))
        sel_i = pick.get("sel", 0)
        for i, (label, sublabel) in enumerate(pick.get("options", [])):
            if i == sel_i:
                sel_line = len(body_lines)
            marker = "►" if i == sel_i else " "
            style = f"bold {theme.accent_bright}" if i == sel_i else theme.value
            r = Text(_PAD + f"  {marker} ", style=style, justify="left")
            r.append(label, style=style)
            if sublabel:
                r.append(f"   {sublabel}", style=theme.muted)
            body_lines.append(r)

    elif state is not None:
        # ── Live monitoring view ──────────────────────────────────
        # Grouped by audience, matching the retro board — see the note there,
        # including why this block stays about as short as the one it replaced.
        if not poker_data.get("snapshot"):
            _heading("Send this to your team")
            # The tunnel URL is the only participant link there is — see the retro
            # board for the full note.
            public_url = poker_data.get("public_url", "")
            link_failed = bool(poker_data.get("link_failed"))
            if public_url:
                _link("Participant link", public_url, f"bold {theme.accent_bright}")
            else:
                _row("Participant link", "unavailable" if link_failed else "preparing…", theme.muted)
            _row("Share code", poker_data.get("display_code", "—"), f"bold {theme.accent_bright}")
            if public_url:
                _line("Copy Invite sends one link that carries the code — one click and they're in.", theme.muted)
            elif link_failed:
                _line("The secure link didn't start — press Retry Link to try again.", theme.warn)
            else:
                _line("Setting up the secure link — a few seconds. The code is already live.", theme.muted)

            host_url = poker_data.get("host_url", "")
            if host_url:
                body_lines.append(Text(""))
                _link("Host link (yours only)", host_url, theme.muted)
                _line("Open in YOUR browser — it holds reveal, save, edit and AI.", theme.muted)

        notice = state.get("notice", "")
        if notice:
            body_lines.append(Text(""))
            body_lines.append(Text(_PAD + "  ⚠ " + notice, style=theme.bad, justify="left"))

        progress = state.get("progress") or {}
        ticket = state.get("ticket")
        revealed = state.get("phase") == "revealed"
        dueling = state.get("phase") == "duel"
        _heading(
            f"Ticket {state.get('ticket_index', 0) + 1}/{state.get('ticket_count', 0)}"
            f"  ·  {progress.get('estimated', 0)} estimated"
        )
        if ticket is None:
            _line("No tickets loaded.", theme.muted)
        else:
            _wrapped(f"{ticket.get('key', '')}  {ticket.get('summary', '')}", f"bold {theme.value}", indent="    ")
            bits = [f"points {_pts(ticket.get('story_points'))}"]
            if ticket.get("type"):
                bits.append(ticket["type"])
            if ticket.get("state"):
                bits.append(ticket["state"])
            if ticket.get("assignee"):
                bits.append(ticket["assignee"])
            _line(" · ".join(bits), theme.muted)
            acceptance = (ticket.get("acceptance_text") or "").strip()
            if acceptance:
                excerpt = acceptance[:240] + ("…" if len(acceptance) > 240 else "")
                _line("Acceptance criteria", theme.muted)
                _wrapped(excerpt, theme.value, indent="      ")
            phase_label = "duel — the floor is open" if dueling else ("votes revealed" if revealed else "voting")
            _row("Phase", phase_label, theme.accent_bright if (revealed or dueling) else theme.value)
            votes = state.get("votes") or []
            # During a duel the votes are already public (post-reveal shape).
            if revealed or dueling:
                if votes:
                    _line("   ".join(f"{v.get('name', 'anon')} → {v.get('value', '?')}" for v in votes), theme.value)
                    if state.get("suggestion") is not None:
                        _row("Suggested", _pts(state.get("suggestion")), f"bold {theme.accent_bright}")
                else:
                    _line("No votes were cast this round.", theme.muted)
            elif votes:
                voted_n = sum(1 for v in votes if v.get("voted"))
                _line(
                    "   ".join(f"{v.get('name', 'anon')} {'✓' if v.get('voted') else '…'}" for v in votes),
                    theme.value,
                )
                _line(f"{voted_n}/{len(votes)} voted — reveal from your admin browser link.", theme.muted)
            else:
                _line("Waiting for teammates to join…", theme.muted)
            duel = state.get("duel") or {}
            duel_status = duel.get("status", "")
            if duel_status == "live":
                low, high = duel.get("low") or {}, duel.get("high") or {}
                _line(
                    f"⚔ Duel — {low.get('name', '?')} ({low.get('value', '?')})"
                    f" vs {high.get('name', '?')} ({high.get('value', '?')})",
                    theme.accent_bright,
                )
                speaker = low if duel.get("turn") == "low" else high
                _line(f"    Turn {duel.get('turn_no', 1)}/2 — {speaker.get('name', '?')} has the floor", theme.value)
                rec = duel.get("recording") or {}
                mics = sum(1 for r in ("low", "high") if rec.get(r))
                if rec.get("host") or mics:
                    # Recording must never be invisible — mirror the browser's REC pill.
                    _line(
                        f"    ● RECORDING — host mic {'on' if rec.get('host') else 'off'} · {mics} browser mic(s)",
                        theme.bad,
                    )
                else:
                    _line("    Not recording — no mic source available.", theme.muted)
            elif duel_status == "transcribing":
                _line(f"⚔ Duel — transcribing the debate…{_voice_download_hint()}", theme.muted)
            elif duel_status == "done":
                transcript = duel.get("transcript") or ""
                _line(f"⚔ Duel transcript captured ({len(transcript)} chars)", theme.accent)
                if transcript:
                    _wrapped(transcript[:160] + ("…" if len(transcript) > 160 else ""), theme.muted, indent="      ")
            elif duel_status == "failed":
                _line(f"⚔ Duel — {duel.get('error') or 'recording failed'}", theme.bad)
            ai = state.get("ai") or {}
            if ai.get("pending"):
                _line("🤖 AI perspective — thinking…", theme.muted)
            elif ai.get("note"):
                _wrapped(f"🤖 {ai['note']}", theme.accent, indent="    ")
                if ai.get("confidence"):
                    _line(f"    AI confidence: {ai['confidence']}", theme.muted)
                for ev in ai.get("evidence") or ():
                    _wrapped(f"• {ev}", theme.muted, indent="      ")

        tickets_meta = state.get("tickets_meta") or []
        if tickets_meta:
            _heading("Tickets")
            for i, t in enumerate(tickets_meta):
                current = i == state.get("ticket_index")
                mark = "►" if current else ("✓" if t.get("estimated") else "·")
                style = (
                    f"bold {theme.accent_bright}" if current else (theme.value if t.get("estimated") else theme.muted)
                )
                pts = f"  [{_pts(t.get('final_points'))}]" if t.get("estimated") else ""
                _wrapped(f"{mark} {t.get('key', '')}  {t.get('summary', '')}{pts}", style, indent="    ")

    elif report is not None:
        # ── Saved-run snapshot view ───────────────────────────────
        estimated = sum(1 for t in report.tickets if t.estimated)
        _heading(f"Session summary  ({estimated}/{len(report.tickets)} estimated)")
        _row("Source", f"{report.source or '—'} · {report.scope_label or '—'}", theme.value)
        if report.participants:
            _row("Participants", ", ".join(report.participants), theme.value)
        for t in report.tickets:
            body_lines.append(Text(""))
            _wrapped(f"{t.key}  {t.summary}", f"bold {theme.value}", indent="    ")
            if t.estimated:
                move = f"{_pts(t.initial_points)} → {_pts(t.final_points)} points"
                _line(move, theme.accent_bright)
                if t.votes:
                    _line("   ".join(f"{v.voter} {v.value}" for v in t.votes), theme.muted)
            else:
                _line("not estimated", theme.muted)
            if t.ai_note:
                _wrapped(f"🤖 {t.ai_note}", theme.accent, indent="      ")
            if t.duel_transcript:
                _line(f"⚔ Duel: {t.duel_low} vs {t.duel_high}", theme.accent)
                _wrapped(
                    t.duel_transcript[:160] + ("…" if len(t.duel_transcript) > 160 else ""),
                    theme.muted,
                    indent="      ",
                )

    # ── Layout using shared components ────────────────────────────
    # Measured, not assumed: the bar wraps once the copy buttons are on it, and
    # a hardcoded action_h would push the second row off the panel.
    actions = poker_data.get("actions") or ["Close"]
    inner_w = width - _PANEL_BORDER_W  # see the retro screen for why the borders come off
    action_lines = build_action_rows(actions, action_sel, width=inner_w)

    viewport_h = calc_viewport(height, header_h=10, action_h=action_rows_height(actions, inner_w))
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    actual_scroll = min(scroll_offset, max_scroll)
    # Pick view: ↑/↓ move the selection, so auto-scroll to keep it visible
    # (a long sprint list must never leave the ► row off-screen).
    if sel_line is not None:
        if sel_line < actual_scroll:
            actual_scroll = sel_line
        elif sel_line >= actual_scroll + viewport_h:
            actual_scroll = min(max_scroll, sel_line - viewport_h + 1)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll, always_show=True)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        viewport_renderable,
        Text(""),
        *action_lines,
    )

    return build_page_panel(content, theme=POKER_THEME, height=height)


def _build_standup_progress_screen(
    progress: list[str],
    *,
    width: int = 80,
    height: int = 24,
    elapsed: float = 0.0,
    anim_tick: float = 0.0,
    theme=None,
    title=None,
    label: str = "Generating standup",
) -> Panel:
    """Build a worker-thread progress screen (spinner + phase steps).

    Shown while a long pipeline (``run_standup``, ``run_anonymize``, ...) runs on a
    worker thread — it makes tracker + LLM network calls that can take many seconds,
    so the user must see live progress instead of a frozen input box. Defaults to the
    Daily Standup look; ``theme``/``title``/``label`` let any mode reuse the identical
    screen with its own accent (this is "the consistent loading screen").
    """
    from yeaboi.ui.shared._components import STANDUP_THEME, standup_title

    if theme is None:
        theme = STANDUP_THEME
    if title is None:
        # width picks the tall ANSI wordmark where it fits; without it the title
        # helper assumes 80 cols and drops to the pixel-block fallback art.
        title = standup_title(width=width)

    _spinners = ["◐", "◓", "◑", "◒"]
    spinner = _spinners[int(anim_tick * 4) % len(_spinners)]
    mins, secs = int(elapsed) // 60, int(elapsed) % 60
    time_str = f"{mins}:{secs:02d}" if mins > 0 else f"{secs}s"

    body: list = [
        Text(_PAD + f"{spinner}  {label}", style=f"bold {theme.accent_bright}", justify="left"),
        Text(_PAD + f"   Elapsed: {time_str}", style=theme.dim, justify="left"),
        Text(""),
    ]

    progress_rows = _build_activity_progress_rows(progress, theme=theme, anim_tick=anim_tick)
    max_visible_steps = max(2, height - 15)
    body.extend(progress_rows[-max_visible_steps:])

    inner_h = height - 4
    remaining = max(0, inner_h - 8 - len(body))
    body.extend(Text("") for _ in range(remaining))

    content = Group(Text(""), title, Text(""), *body)
    # theme, not STANDUP_THEME: this loading screen is shared — poker ticket
    # fetch and the anonymize pass reuse it with their own mode's theme. Its
    # left-gutter checklist leaves the right side free for the duck's bubble.
    return _with_bubble_room(build_page_panel(content, theme=theme, border_style=theme.accent, height=height), width)


def _build_standup_input_screen(
    prompt: str,
    value: str,
    *,
    step: str = "",
    default: str = "",
    width: int = 80,
    height: int = 24,
    border_style: str = "",
    status: str = "",
    theme=None,
    title=None,
    box_rows: int = 1,
    show_image_hint: bool = False,
) -> Panel:
    """Build a themed single-line input screen for the Daily Standup flows.

    Stays inside the Live display (driven by read_key), so it matches the app's
    full-screen style and never drops to a raw terminal prompt. Supports voice
    dictation (double-tap Space): pass ``border_style``/``status`` to show the
    recording/transcribing indicator on the same screen.

    Other pages reuse this screen with their own branding by passing ``theme``
    (a Theme constant) and ``title`` (a rendered ASCII-art title); defaults
    keep the standup look. ``box_rows > 1`` renders a large multi-row text box
    (the value wraps across rows and honours explicit ``\\n`` newlines from
    Alt+Enter; the cursor row always stays visible) for longer free-text
    answers like standup updates — Enter confirms.

    # See docs: "Daily Standup" — TUI page
    # See docs: "TUI system" — voice input overlay
    """
    from yeaboi.ui.session.screens._screens_input import _image_hint, _voice_hint
    from yeaboi.ui.shared._components import STANDUP_THEME, standup_title
    from yeaboi.ui.shared._voice_input import voice_chip

    theme = theme or STANDUP_THEME
    title = title if title is not None else standup_title()
    sub = Text(_PAD + (step or "Configure standup"), style="dim", justify="left")
    box_style = border_style or theme.accent

    # Prompt label + a bordered input field showing the current value and a cursor.
    label = Text(_PAD + "  ", justify="left")
    label.append(prompt, style=f"bold {theme.accent}")
    if default:
        label.append(f"   (default: {default})", style=theme.dim)
    label.no_wrap = True
    label.overflow = "ellipsis"

    def _box_top(inner_w: int) -> Text:
        """Top border with the dictation chip inlaid, Panel-title style.

        This box is hand-drawn, so there is no Panel title to hang the chip on —
        but the border is the one row that is never cropped, which is the whole
        reason the chip moved off the hint line. Putting it on the *label* would
        just have reproduced the original bug: the label is no_wrap/ellipsis, so
        a trailing chip is the first thing dropped on a narrow terminal. The chip
        is omitted when the border is too short to hold it and still read as a
        border.
        """
        chip, chip_style = voice_chip()
        # 6 = the two ─ before the chip, a space either side, and 2 ─ after.
        if inner_w < cell_len(chip) + 6:
            return Text(_PAD + "  ╭" + "─" * inner_w + "╮", style=box_style)
        top = Text(_PAD + "  ╭─ ", style=box_style)
        top.append(chip, style=chip_style)
        top.append(" " + "─" * (inner_w - cell_len(chip) - 3) + "╮", style=box_style)
        return top

    if box_rows <= 1:
        field_inner = f" {value}█ "
        box_top = _box_top(max(len(field_inner), 40))
        box_mid = Text(_PAD + "  │", style=box_style)
        box_mid.append(field_inner.ljust(max(len(field_inner), 40)), style=f"bold {theme.accent_bright}")
        box_mid.append("│", style=box_style)
        box_bot = Text(_PAD + "  ╰" + "─" * max(len(field_inner), 40) + "╯", style=box_style)
        box_lines = [box_top, box_mid, box_bot]
    else:
        # Large text box: wide, several rows, the value wraps across them.
        # Clamp the row count so the box + hint always fit the terminal
        # (label + 2 blanks + hint = 4 rows, box borders = 2 rows).
        rows = max(2, min(box_rows, calc_viewport(height, header_h=6, action_h=1) - 6))
        inner_w = max(46, min(width - len(_PAD) - 12, 110))
        text_w = inner_w - 2  # one space of padding each side
        raw = value + "█"
        # Newline-aware chunking: split on explicit newlines (Alt+Enter) first,
        # then width-wrap each segment; an empty segment still takes one row.
        chunks = []
        for seg in raw.split("\n"):
            chunks.extend([seg[i : i + text_w] for i in range(0, len(seg), text_w)] or [""])
        chunks = chunks[-rows:]  # keep the cursor row visible when the text overflows
        while len(chunks) < rows:
            chunks.append("")
        box_lines = [_box_top(inner_w)]
        for chunk in chunks:
            row = Text(_PAD + "  │", style=box_style)
            row.append(f" {chunk}".ljust(inner_w), style=f"bold {theme.accent_bright}")
            row.append("│", style=box_style)
            box_lines.append(row)
        box_lines.append(Text(_PAD + "  ╰" + "─" * inner_w + "╯", style=box_style))

    # While recording/transcribing, the voice status replaces the usual hint.
    if status:
        # One row, always: pad_rows below budgets exactly one for this line.
        hint_line = Text(
            _PAD + "  " + status,
            style=box_style or theme.accent,
            justify="left",
            no_wrap=True,
            overflow="ellipsis",
        )
    else:
        newline_hint = "  ·  Alt+Enter (or Ctrl+N) for a new line" if box_rows > 1 else ""
        hints = (
            "Enter to confirm  ·  Esc to cancel"
            + newline_hint
            + _voice_hint()
            + (_image_hint() if show_image_hint else "")
        )
        hint_line = Text(_PAD + "  " + hints, style=theme.dim, justify="left")

    # Vertically pad the middle so the field sits in the upper-third like the dashboard.
    body: list = [label, Text(""), *box_lines, Text(""), hint_line]
    pad_rows = max(0, calc_viewport(height, header_h=6, action_h=1) - len(body))
    body.extend(Text("") for _ in range(pad_rows))

    content = Group(Text(""), title, Text(""), sub, Text(""), *body)
    return build_page_panel(content, theme=STANDUP_THEME, height=height)


# ---------------------------------------------------------------------------
# Profile picker screen (planning mode — select which analysis to use)
# ---------------------------------------------------------------------------


def _build_profile_picker_screen(
    profiles: list,
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
) -> Panel:
    """Build the analysis profile picker shown before planning intake.

    Lists available team analysis profiles as styled cards + a Skip option.
    Uses PLANNING_THEME and shared components for visual consistency.
    """
    from yeaboi.ui.shared._components import PLANNING_THEME, planning_title

    theme = PLANNING_THEME
    title = planning_title()
    sub = Text(_PAD + "Use a team analysis to calibrate planning?", style="dim", justify="left")

    body_lines: list = []
    _source_icons = {"jira": "\U0001f4cb", "azdevops": "\u2601"}  # 📋 for Jira, ☁ for AzDO
    card_w = min(60, width - len(_PAD) - 10)

    for i, p in enumerate(profiles):
        is_sel = i == selected
        team_id = getattr(p, "team_id", "?")
        source = getattr(p, "source", "?")
        sprints = getattr(p, "sample_sprints", 0)
        stories = getattr(p, "sample_stories", 0)
        vel = getattr(p, "velocity_avg", 0.0)
        updated = getattr(p, "updated_at", "")
        completion = getattr(p, "sprint_completion_rate", 0.0)

        # Compute age
        age_str = ""
        stale = False
        if updated:
            try:
                from datetime import UTC, datetime

                _up = datetime.fromisoformat(updated)
                days = (datetime.now(UTC) - _up).days
                age_str = "today" if days == 0 else (f"{days}d ago")
                stale = days > 30
            except Exception:
                pass

        # Card border
        sel_border = theme.accent if is_sel else "rgb(50,50,60)"
        icon = _source_icons.get(source, "\u25cb")

        body_lines.append(Text(""))

        # Top border
        body_lines.append(Text(_PAD + "  \u256d" + "\u2500" * card_w + "\u256e", style=sel_border, justify="left"))

        # Title row
        title_row = Text(_PAD + "  \u2502 ", justify="left")
        title_row.append(f" {icon} ", style=sel_border)
        # Display name: strip source prefix for cleaner look
        display_name = team_id.split("-", 1)[1] if "-" in team_id else team_id
        title_row.append(display_name, style="bold white" if is_sel else theme.muted)
        # Pad to card width
        used = len(title_row.plain) - len(_PAD) - 4
        title_row.append(" " * max(1, card_w - used), style="")
        title_row.append("\u2502", style=sel_border)
        body_lines.append(title_row)

        # Stats row
        stats_row = Text(_PAD + "  \u2502  ", justify="left")
        stat_parts = [f"{sprints} sprints", f"{stories} stories"]
        if vel > 0:
            stat_parts.append(f"{vel:.0f} pts/sprint")
        if completion > 0:
            stat_parts.append(f"{completion:.0f}% completion")
        stats_str = "  \u00b7  ".join(stat_parts)
        stats_row.append(f"  {stats_str}", style=theme.muted)
        used = len(stats_row.plain) - len(_PAD) - 4
        stats_row.append(" " * max(1, card_w - used), style="")
        stats_row.append("\u2502", style=sel_border)
        body_lines.append(stats_row)

        # Source + age row
        meta_row = Text(_PAD + "  \u2502  ", justify="left")
        meta_row.append(f"  {source}", style=theme.dim)
        if age_str:
            meta_row.append("  \u00b7  ", style=theme.dim)
            if stale:
                meta_row.append(f"\u26a0 {age_str}", style=theme.warn)
            else:
                meta_row.append(f"\u2713 {age_str}", style="rgb(80,180,80)")
        used = len(meta_row.plain) - len(_PAD) - 4
        meta_row.append(" " * max(1, card_w - used), style="")
        meta_row.append("\u2502", style=sel_border)
        body_lines.append(meta_row)

        # Bottom border
        body_lines.append(Text(_PAD + "  \u2570" + "\u2500" * card_w + "\u256f", style=sel_border, justify="left"))

    # Skip option — simple row, no card
    body_lines.append(Text(""))
    is_skip_sel = selected == len(profiles)
    skip_border = theme.accent if is_skip_sel else "rgb(50,50,60)"
    body_lines.append(Text(_PAD + "  \u256d" + "\u2500" * card_w + "\u256e", style=skip_border, justify="left"))
    skip_row = Text(_PAD + "  \u2502 ", justify="left")
    skip_row.append(" \u2192 ", style=skip_border)
    skip_row.append("Skip — plan without analysis", style="bold white" if is_skip_sel else theme.muted)
    used = len(skip_row.plain) - len(_PAD) - 4
    skip_row.append(" " * max(1, card_w - used), style="")
    skip_row.append("\u2502", style=skip_border)
    body_lines.append(skip_row)
    skip_detail = Text(_PAD + "  \u2502  ", justify="left")
    skip_detail.append("  Planning will use generic Fibonacci defaults", style=theme.dim)
    used = len(skip_detail.plain) - len(_PAD) - 4
    skip_detail.append(" " * max(1, card_w - used), style="")
    skip_detail.append("\u2502", style=skip_border)
    body_lines.append(skip_detail)
    body_lines.append(Text(_PAD + "  \u2570" + "\u2500" * card_w + "\u256f", style=skip_border, justify="left"))

    # Layout
    viewport_h = calc_viewport(height, header_h=6, action_h=4)
    visible = body_lines[:viewport_h]

    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    btn_top, btn_mid, btn_bot = build_action_buttons(["Select"], 0)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        Group(*padded_lines),
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )

    return build_page_panel(content, theme=PLANNING_THEME, height=height)


# ---------------------------------------------------------------------------
# Settings screen
# ---------------------------------------------------------------------------


class _EditableRow(Text):
    """A settings config row that remembers the env var it edits.

    Rich's :class:`Text` defines ``__slots__``; subclassing without slots gives
    instances a ``__dict__`` so the builder can tag the row with ``env``/``label``/
    ``masked`` for click-to-edit (see the settings loop's ``_row_regions``).
    """


# Settings is a tabbed view. A few broad tabs group the config; this order drives
# both the tab bar and the loop's Enter action (see settings_tab_action).
_SETTINGS_TABS: list[str] = ["Credentials", "System"]

# The heading sections each tab renders, in order. Storage is one row, so it
# lives under System rather than owning a tab of its own.
_SETTINGS_TAB_SECTIONS: dict[str, list[str]] = {
    "Credentials": ["provider", "jira", "azure", "github", "notion"],
    "System": ["storage", "standup", "voice", "bedrock", "advanced"],
}

# Sections whose box spans the full grid width instead of taking a column slot.
# These carry token-help sub-lines (a creation URL + a scope sentence) that a
# half-width column would ellipsize away — same reasoning as the Usage dashboard's
# ``wide`` sections (see _build_usage_screen).
_WIDE_SETTINGS_SECTIONS = {"jira", "azure", "github", "notion"}

# Absolute rows the tab bar occupies (labels + underline), for click hit-testing.
# The header above it is fixed height: top border + top pad + blank + title (2
# rows) + blank → the labels row is the 7th terminal row.
_TAB_LABELS_ROW = 7
_TAB_UNDERLINE_ROW = 8
_TAB_COL_OFFSET = 4  # panel border (1) + left padding (2) → first content column is 4

# Settings section boxes, mirroring the Usage dashboard's grid (see _build_usage_screen):
# the narrowest a box may get before the grid drops a column, and the most columns it
# will ever use. Two reads better than three here — settings rows are "label:  value"
# pairs whose values (URLs, model names) are longer than Usage's counters.
_SETTINGS_MIN_BOX_W = 38
_SETTINGS_MAX_COLS = 2

# Settings rows that pick from a fixed set rather than taking typed text. Enter
# steps to the next option and saves it — see _choice_row for the drawing and
# _s_begin_edit for the keystroke. Anything not listed here still opens the
# in-place text editor, which is right for URLs, keys and free-form values.
#
# The values are what lands in .env, so the booleans stay "true"/"false" — that is
# what is_tips_enabled/is_duck_enabled read, and writing "off" would silently turn
# the feature ON (anything that is not the literal "false" counts as enabled).
#
# Session Prune Days is deliberately NOT here: it is an arbitrary integer, and a
# list of presets would take away every value that is not on the list.
SETTINGS_CHOICES: dict[str, tuple[str, ...]] = {
    "SCREENSAVER_STYLE": SCREENSAVER_STYLES,
    "TIPS_ENABLED": ("true", "false"),
    "DUCK_ENABLED": ("true", "false"),
    "LANGSMITH_TRACING": ("true", "false"),
    "LOG_LEVEL": VALID_LOG_LEVELS,
}

# How a stored choice is spelled on the row, where the two differ. Nobody wants to
# read "true / false" for a switch that has always been drawn as on/off.
SETTINGS_CHOICE_LABELS: dict[str, dict[str, str]] = {
    "TIPS_ENABLED": {"true": "on", "false": "off"},
    "DUCK_ENABLED": {"true": "on", "false": "off"},
    "LANGSMITH_TRACING": {"true": "enabled", "false": "disabled"},
}

# What an unset var behaves as. These are NOT all the first option — tracing is off
# by default while tips and the duck are on, and WARNING sits third in the level
# list — so neither the row nor the cycle can assume index 0.
SETTINGS_CHOICE_DEFAULTS: dict[str, str] = {
    "SCREENSAVER_STYLE": "ducks",
    "TIPS_ENABLED": "true",
    "DUCK_ENABLED": "true",
    "LANGSMITH_TRACING": "false",
    "LOG_LEVEL": "WARNING",
}


def settings_choice_value(env: str, stored: str) -> str:
    """The option a stored value behaves as, in the casing the option list uses.

    One resolution for both halves of a choice row: the builder lights this option
    and the settings loop steps on from it. Splitting them is how "unset" ended up
    lighting WARNING while Enter jumped to INFO.
    """
    folded = {opt.lower(): opt for opt in SETTINGS_CHOICES[env]}
    return folded.get((stored or "").strip().lower(), SETTINGS_CHOICE_DEFAULTS[env])


# The column width at which a _WIDE_SETTINGS_SECTIONS box stops needing the whole
# row. Those sections are wide only because their token-help sub-lines don't fit a
# half-width column on an 80-column terminal; give a column this much and they do
# (the longest creation URL line is ~70 columns, and the scope sentence wraps).
# Below it they still span, so nothing changes on a normal-sized terminal — but on
# a wide one the Credentials tab was one column of full-width boxes with most of
# the screen empty beside it, because only its provider box was ever "narrow".
_SETTINGS_WIDE_COL_W = 76
# The focused value is marked by a full-width bar behind the row rather than a
# leading glyph: a marker needs a gutter on EVERY row to avoid text jumping when
# focus lands, and that gutter reads as a stray indent inside an already-indented
# box. A background stripe costs no columns.
_SETTINGS_FOCUS_BG = "rgb(44,52,68)"
# Rows a short column may gain so its bottom border lines up with the tall one.
# Beyond this the stretch reads as padding rather than alignment, so the leftover
# is simply left as space below the column. The balancing pass keeps the shortfall
# small, so this is enough to land level in practice — it exists to stop a lone
# one-row box being blown up to match a column of six-row ones.
_SETTINGS_MAX_STRETCH = 5  # per-box leveling allowance — grew again with the Microphone row

_TAB_NOT_READY = "rgb(74,74,90)"  # visibly present, clearly behind the ready ones
_TAB_INDENT = 4  # left margin of the tab bar — aligned with the SETTINGS title
_TAB_GAP = 3  # spaces between tab labels


def settings_tab_action(active_tab: int) -> str:
    """Return what Enter does on a settings tab: 'loglevel' (System → cycles the log
    level) or 'setup' (Credentials → wizard). The data directory is no longer a tab
    action — it's the Storage box's row, opened like any other value."""
    label = _SETTINGS_TABS[active_tab] if 0 <= active_tab < len(_SETTINGS_TABS) else ""
    if label == "System":
        return "loglevel"
    return "setup"


def settings_focus_move(
    key: str,
    box_cols: list[list[int]],
    box_tail: list[int],
    box_fields: list[list[tuple[str, str, bool]]],
    sel_box: int,
    sel_field: int,
) -> tuple[int, int]:
    """Resolve an arrow key against the settings screen's three focus levels.

    Kept out of the TUI loop (and pure) so the state machine is testable. It takes
    the navigation map the last render published — ``box_cols`` (the balanced
    columns of section boxes, each listed top to bottom) and ``box_tail`` (the
    full-width boxes stacked underneath them), plus ``box_fields`` (each section's
    editable rows) — and the current ``(sel_box, sel_field)``, and returns the next.

    ``(-1, -1)`` is the tab bar: only Down enters the grid from there (Left/Right
    belong to the tabs and never reach this). ``(b, -1)`` walks the boxes — Up/Down
    within a column, Left/Right across columns, Down off the last box into the
    full-width tail — and Up off the top hands focus back to the tab bar. ``(b, f)``
    walks the values inside box *b*, where Left/Right do nothing: the box is the
    unit you stepped into.
    """
    first = box_cols[0][0] if box_cols and box_cols[0] else (box_tail[0] if box_tail else -1)
    if first < 0:
        return -1, -1
    if sel_box < 0:
        return (first, -1) if key == "down" else (sel_box, sel_field)
    if not any(sel_box in c for c in box_cols) and sel_box not in box_tail:
        return first, -1  # stale index — the tab's sections changed under us

    if sel_field >= 0:
        fields = box_fields[sel_box] if 0 <= sel_box < len(box_fields) else []
        if key in ("up", "down") and fields:
            step = 1 if key == "down" else -1
            return sel_box, max(0, min(len(fields) - 1, sel_field + step))
        return sel_box, sel_field

    col = next((c for c, boxes in enumerate(box_cols) if sel_box in boxes), -1)
    if col >= 0:
        idx = box_cols[col].index(sel_box)
        if key in ("left", "right"):
            ncol = max(0, min(len(box_cols) - 1, col + (1 if key == "right" else -1)))
            target = box_cols[ncol] or box_cols[col]
            return target[min(idx, len(target) - 1)], -1
        if key == "down":
            if idx + 1 < len(box_cols[col]):
                return box_cols[col][idx + 1], -1
            return (box_tail[0], -1) if box_tail else (sel_box, -1)
        return (box_cols[col][idx - 1], -1) if idx else (-1, -1)  # up off the top → tabs

    if sel_box in box_tail:  # one of the full-width boxes below the columns
        t = box_tail.index(sel_box)
        if key in ("left", "right"):
            return sel_box, -1
        if key == "down":
            return (box_tail[t + 1] if t + 1 < len(box_tail) else sel_box), -1
        if t:
            return box_tail[t - 1], -1
        return (box_cols[0][-1], -1) if box_cols and box_cols[0] else (-1, -1)

    return sel_box, -1


def _settings_tab_bar(
    labels: list[str],
    active: int,
    theme,
    width: int,
    *,
    pos: float | None = None,
    taper: bool = True,
    spread: int | bool = False,
    muted: tuple[bool, ...] = (),
    align_right: bool = False,
) -> tuple[list, list]:
    """Render the underline-style tab bar: a row of labels over one continuous
    horizontal rule. Under the active tab the rule is accent-bright, tapering in
    three steps: the last char on either end steps down to the mid accent, the two
    chars flanking it are dimmer still, and the rest is the neutral separator
    colour — so the bright bar fades out instead of ending on a hard edge. No
    vertical dividers.

    Returns ``(lines, spans)`` where ``spans[i]`` is the 0-based ``(start, end)``
    column range (within the content) of label *i* — used to hit-test tab clicks.
    """
    # ``spread`` shares the page's width out between the tabs instead of packing
    # them at the left. A bar that stops a third of the way across reads as a
    # list that ran out, not as the full set of what there is.
    # An int spreads to THAT width rather than the page's, so a bar under a
    # two-column band can stop where the other column starts and leave it free.
    gap = _TAB_GAP
    if spread and len(labels) > 1:
        _to = spread if isinstance(spread, int) and spread is not True else width - 6
        # A left-aligned bar keeps a margin at each end; a right-parked one is
        # pushed against the edge afterwards, so it fills ``_to`` exactly and has
        # no left margin to subtract — subtracting one anyway left the strip
        # short of the width it was asked to spread over.
        _margins = 0 if align_right else _TAB_INDENT * 2
        room = max(0, _to - _margins - sum(len(x) for x in labels))
        gap = max(_TAB_GAP, room // (len(labels) - 1))
    # ``align_right`` parks the whole strip against the right edge instead of the
    # left margin, for a page whose content leads at the left and wants its
    # stage list out of the way of the first word on every line.
    indent = _TAB_INDENT
    if align_right:
        natural = sum(len(x) for x in labels) + gap * max(0, len(labels) - 1)
        # Held off the edge by the same margin the left-aligned bar keeps: run it
        # to the last content column and the final tab sits against the frame
        # with nothing either side of it.
        indent = max(_TAB_INDENT, (width - 6) - _TAB_INDENT - natural)
    labels_line = Text(" " * indent, justify="left")
    spans: list = []
    col = indent
    for i, label in enumerate(labels):
        if i > 0:
            labels_line.append(" " * gap)
            col += gap
        start = col
        if i == active:
            _style = f"bold {theme.accent_bright}"
        elif i < len(muted) and muted[i]:
            # A concrete colour, not `dim`: Rich's dim attribute against this
            # page's near-black background renders as very nearly nothing, so a
            # tab with no content behind it yet did not read as "not ready" — it
            # read as gone, and the strip looked like it had lost three of four.
            _style = _TAB_NOT_READY
        else:
            _style = theme.muted
        labels_line.append(label, style=_style)
        col += len(label)
        spans.append((start, col))  # [start, end)

    # The rule spans the tab strip plus a 2-char margin each side, so the dimmer
    # "shoulder" chars beside the active tab stay visible even when the first or
    # last tab is selected — but it still stops well short of the full width.
    rule_start = max(0, (spans[0][0] if spans else indent) - 2)
    # Clamped: the two-column shoulder past the last tab has nowhere to go when
    # the strip is already against the right edge, and a rule one char too wide
    # wraps to the next line as a stray stub.
    rule_end = min((width - 6), (spans[-1][1] if spans else indent) + 2)
    if pos is not None and spans:
        # Animated: slide the bright underline between adjacent tab spans by the
        # fractional position (the loop eases `pos` toward the active tab index).
        import math as _m

        _p = max(0.0, min(len(spans) - 1, pos))
        _lo = int(_m.floor(_p))
        _hi = min(len(spans) - 1, _lo + 1)
        _f = _p - _lo
        _s0, _e0 = spans[_lo]
        _s1, _e1 = spans[_hi]
        a_start = int(round(_s0 + (_s1 - _s0) * _f))
        a_end = int(round(_e0 + (_e1 - _e0) * _f))
    else:
        a_start, a_end = spans[active] if 0 <= active < len(spans) else (0, 0)
    underline = Text(" " * rule_start, justify="left")  # blank left margin up to the first tab
    for c in range(rule_start, rule_end):
        if a_start <= c < a_end:
            # ``taper`` off = one flat colour end to end. A bar that changes shade
            # along its own length reads as two marks while it is sliding, which
            # is exactly when it most needs to read as one object moving.
            if taper and (c == a_start or c == a_end - 1):
                style = theme.accent  # the last char on either end steps down
            else:
                style = f"bold {theme.accent_bright}"  # full brightness across the middle
        elif taper and (a_start - 2 <= c < a_start or a_end <= c < a_end + 2):
            style = theme.dim  # slightly dimmer just to either side
        else:
            style = theme.sep  # the neutral continuous rule
        underline.append("─", style=style)
    return [labels_line, underline], spans


def _wrap_value(value: str, width: int, head: int, indent: int = 2) -> list[str]:
    """Greedily wrap ``value`` for a settings row: the first line shares the row
    with a ``head``-wide label, later lines are indented under it."""
    out: list[str] = []
    budget = max(8, width - head)
    line = ""
    for word in value.split():
        candidate = f"{line} {word}" if line else word
        # A word longer than the whole budget still takes the line it starts (it
        # ellipsizes there) — breaking before it would emit an empty line.
        if not line or len(candidate) <= budget:
            line = candidate
            continue
        out.append(line)
        line, budget = word, max(8, width - indent)
    out.append(line)
    return out


def _build_settings_screen(
    config_data: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    active_tab: int = 0,
    tab_pos: float | None = None,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    editing: tuple | None = None,
    sel_box: int = -1,
    sel_field: int = -1,
) -> Panel:
    """Build the settings dashboard showing current configuration.

    Displays all config values grouped by category with secrets masked.
    Uses SETTINGS_THEME (silver) with shared components.

    Keyboard focus has three levels, driven by ``sel_box`` / ``sel_field``:
    ``-1/-1`` is the tab bar (left/right switch tabs), ``b/-1`` highlights section
    box *b* (arrows walk the box grid), and ``b/f`` highlights one value inside it
    (up/down walk the values, Enter edits). The loop owns the level; this builder
    just draws it and publishes the navigation map (``_box_grid``/``_box_fields``)
    the loop needs to move around. Selecting off-screen content scrolls it into
    view — the effective offset comes back through ``scroll_meta["scroll"]``.
    """
    from yeaboi.ui.shared._components import SETTINGS_THEME, settings_title

    theme = SETTINGS_THEME
    title = settings_title(shimmer_tick)

    # The transient status ("Anthropic Key updated") is spoken through the
    # shared duck voice by the settings loop — nothing to lay out here.

    # ── Box geometry, resolved BEFORE the rows are built ──────────
    # Each section becomes its own bordered box laid out in an adaptive-width grid
    # (the Usage dashboard's treatment — see _build_usage_screen). The widths have
    # to be known up front because an in-place edit windows its buffer to the box
    # it will end up sitting in.
    # A box's left border lands on the tab-bar rule, which puts the rows INSIDE it
    # (border + 1 pad) at the same column the unboxed rows used and the tab labels
    # start at — so boxing the sections doesn't shift any text sideways.
    _grid_indent = _PAD
    # panel chrome (border + 2 pad, both sides) + the indent + the scrollbar gutter.
    grid_w = max(24, width - 6 - len(_grid_indent) - 1)
    _tab_name = _SETTINGS_TABS[max(0, min(active_tab, len(_SETTINGS_TABS) - 1))]
    _tab_sections = _SETTINGS_TAB_SECTIONS[_tab_name]
    _n_narrow = sum(1 for _s in _tab_sections if _s not in _WIDE_SETTINGS_SECTIONS)
    # Would a column still be wide enough for a token-help section? If so the wide
    # sections join the grid instead of spanning it, and the box count that drives
    # the column choice is every section rather than just the narrow ones. This is
    # what stops the Credentials tab rendering as a single stack of full-width
    # boxes on a large terminal: only "provider" is narrow, so _n_narrow was 1 and
    # the grid collapsed to one column no matter how much room there was.
    _wide_fits_col = (grid_w - 4) // 2 >= _SETTINGS_WIDE_COL_W
    _n_boxes = len(_tab_sections) if _wide_fits_col else (_n_narrow or 1)
    n_cols = max(1, min(_SETTINGS_MAX_COLS, grid_w // _SETTINGS_MIN_BOX_W, _n_boxes))
    # padding=(0,1) with pad_edge=False → a 2-column gutter between boxes only; two
    # columns of slack keep the table clear of the render width.
    col_w = max(20, (grid_w - 2 - 2 * (n_cols - 1)) // n_cols)
    full_w = grid_w - 2  # a wide box takes the whole grid row

    # (title, rows, wide) per section; ``_cur`` is the open section's row list and
    # ``_cur_fields`` its editable rows in order — the unit keyboard focus moves over.
    sections: list[tuple[str, list, bool]] = []
    box_fields: list[list[tuple[str, str, bool]]] = []
    _cur: list = []
    _cur_fields: list[tuple[str, str, bool]] = []
    _cur_w = col_w - 4  # inner text width available to the open section

    def _heading(text: str, *, wide: bool = False) -> None:
        """Open a new section box — its heading is drawn as the box title.

        ``wide`` is a request, not a guarantee: once a column is roomy enough for
        the token help (``_wide_fits_col``) the section takes a column like any
        other, and only the narrow terminal still gets a full-width row.
        """
        nonlocal _cur, _cur_fields, _cur_w
        wide = wide and not _wide_fits_col
        _cur, _cur_fields = [], []
        _cur_w = (full_w if wide else col_w) - 4  # borders (2) + padding (2)
        sections.append((text, _cur, wide))
        box_fields.append(_cur_fields)

    def _choice_row(label: str, env: str) -> None:
        """A settings row that picks from a fixed set instead of taking free text.

        Every option is on the row with the live one lit, so the choice is visible
        without entering anything — and Enter steps to the next rather than opening
        the editor (see SETTINGS_CHOICES and _s_begin_edit). Typing "classic", or
        "false", into a text box to flip a switch was the wrong gesture for it.

        The live option comes from settings_choice_value, the same resolver the
        cycle uses; SETTINGS_CHOICE_LABELS decides how each one reads.
        """
        choices = SETTINGS_CHOICES[env]
        _labels = SETTINGS_CHOICE_LABELS.get(env, {})
        _live = settings_choice_value(env, config_data.get(env, ""))
        _kw = {"justify": "left", "no_wrap": True, "overflow": "ellipsis"}
        r = _EditableRow("", **_kw)
        _focused = len(sections) - 1 == sel_box and len(_cur_fields) == sel_field
        r.append(f"{label}:  ", style=f"bold {theme.accent_bright}" if _focused else theme.muted)
        for _i, _opt in enumerate(choices):
            if _i:
                r.append("  ", style=theme.muted)
            r.append(_labels.get(_opt, _opt), style=f"bold {theme.good}" if _opt == _live else theme.dim)
        if _focused:
            r.append(" " * max(0, _cur_w - r.cell_len))
            r.style = f"on {_SETTINGS_FOCUS_BG}"
        r.env, r.label, r.masked = env, label, False
        _cur_fields.append((env, label, False))
        _cur.append(r)

    def _row(
        label: str, value: str, value_style: str = "", masked: bool = False, env: str = "", wrap: bool = False
    ) -> None:
        # Editable rows use _EditableRow (a Text subclass with a __dict__) so a
        # click can recover the env var; plain rows stay Text.
        # no_wrap + ellipsis: a long value crops instead of wrapping, which would
        # give the box an unpredictable height and break the grid.
        _kw = {"justify": "left", "no_wrap": True, "overflow": "ellipsis"}
        r = _EditableRow("", **_kw) if env else Text("", **_kw)
        _focused = bool(env) and len(sections) - 1 == sel_box and len(_cur_fields) == sel_field
        r.append(f"{label}:  ", style=f"bold {theme.accent_bright}" if _focused else theme.muted)
        if wrap and not env and value:
            # A read-only status whose text can't fit a column (the voice hint
            # carries an install command) flows onto continuation lines. Wrapping
            # HERE rather than at render time keeps one body line per rendered row,
            # so the box height and the click regions still add up.
            _pre = _wrap_value(str(value), _cur_w, len(label) + 3)
            r.append(_pre[0], style=value_style or theme.value)
            _cur.append(r)
            for _more in _pre[1:]:
                _c = Text("  ", justify="left", no_wrap=True, overflow="ellipsis")
                _c.append(_more, style=value_style or theme.value)
                _cur.append(_c)
            return
        if editing is not None and env and editing[0] == env:
            # This row is being edited in place: show the buffer with a block cursor.
            # The buffer is windowed to what is left of the box so the cursor stays
            # visible — otherwise a long key would ellipsize exactly where you type.
            _buf, _pos = editing[1], editing[2]
            _pos = max(0, min(_pos, len(_buf)))
            _avail = max(8, _cur_w - len(label) - 3)
            _lo = max(0, _pos - _avail + 1)
            _win, _wc = _buf[_lo : _lo + _avail], _pos - _lo
            r.append(_win[:_wc], style=theme.value)
            r.append(_win[_wc : _wc + 1] or " ", style="reverse bold")  # cursor cell
            r.append(_win[_wc + 1 :], style=theme.value)
        elif masked and value:
            display = value[:4] + "\u2022" * min(12, len(value) - 4) if len(value) > 4 else "\u2022" * len(value)
            r.append(display, style=value_style or theme.dim)
        elif value:
            r.append(str(value), style=value_style or theme.value)
        else:
            r.append("not set", style=theme.dim)
        if _focused:
            # Pad out to the box's inner width so the stripe runs the full row —
            # the span styles only set colours, so this base style shows through.
            r.append(" " * max(0, _cur_w - r.cell_len))
            r.style = f"on {_SETTINGS_FOCUS_BG}"
        if env:
            r.env, r.label, r.masked = env, label, masked
            _cur_fields.append((env, label, masked))
        _cur.append(r)

    # Token help sub-lines: where to create the token + the minimum scope it needs.
    # Sourced from the shared TOKEN_HELP registry (same one the setup wizard uses)
    # so both token surfaces stay consistent. The creation URL is a clickable
    # OSC-8 hyperlink; both lines are dim so they read as a secondary hint.
    #
    # Each line MUST render as exactly one visual row — its section's box is built
    # at a fixed height (rows + 2), so a wrapped line would overflow the border. We
    # force single-row with no_wrap + ellipsis; the full scope is always visible in
    # the setup wizard, and these sections get a full-width box so wide terminals
    # show it in full here too.
    from yeaboi.ui.provider_select._constants import TOKEN_HELP

    def _token_help(env_var: str) -> None:
        entry = TOKEN_HELP.get(env_var)
        if not entry:
            return
        link = Text("  ", justify="left", no_wrap=True, overflow="ellipsis")
        link.append("↳ create: ", style=theme.muted)
        link.append(entry["url"], style=f"{theme.dim} underline link {entry['url']}")
        _cur.append(link)
        # The scope sentence wraps rather than ellipsizing: it is the one line here
        # long enough to overrun a column, and losing its tail costs the reader the
        # permissions they came for. Wrapping HERE keeps one body line per rendered
        # row, which the box heights and click regions depend on. The URL above it
        # stays no_wrap — a split link is not a link.
        _scope_lines = _wrap_value(str(entry["scope"]), _cur_w, 4 + len("scope: "), indent=6)
        scope = Text("    ", justify="left", no_wrap=True, overflow="ellipsis")
        scope.append("scope: ", style=theme.muted)
        scope.append(_scope_lines[0], style=theme.dim)
        _cur.append(scope)
        for _more in _scope_lines[1:]:
            _c = Text("      ", justify="left", no_wrap=True, overflow="ellipsis")
            _c.append(_more, style=theme.dim)
            _cur.append(_c)

    # ── Section builders (one per tab) — only the active one is rendered ──
    def _sec_provider() -> None:
        _heading("LLM Provider")
        _row("Provider", config_data.get("LLM_PROVIDER", "anthropic"), env="LLM_PROVIDER")
        _row("Model", config_data.get("LLM_MODEL", "(default)"), env="LLM_MODEL")
        _row("Anthropic Key", config_data.get("ANTHROPIC_API_KEY", ""), masked=True, env="ANTHROPIC_API_KEY")
        _row("OpenAI Key", config_data.get("OPENAI_API_KEY", ""), masked=True, env="OPENAI_API_KEY")
        _row("Google Key", config_data.get("GOOGLE_API_KEY", ""), masked=True, env="GOOGLE_API_KEY")
        # Ollama is keyless — its server URL/context rows only appear when the user
        # runs local mode (or has customised the vars), keeping the page uncluttered.
        if config_data.get("LLM_PROVIDER", "") == "ollama" or config_data.get("OLLAMA_BASE_URL", ""):
            _row(
                "Ollama URL",
                config_data.get("OLLAMA_BASE_URL", "") or "http://localhost:11434 (default)",
                env="OLLAMA_BASE_URL",
            )
            _row("Ollama Context", config_data.get("OLLAMA_NUM_CTX", "") or "16384 (default)", env="OLLAMA_NUM_CTX")

    def _sec_jira() -> None:
        _heading("Jira", wide=True)
        _row("Base URL", config_data.get("JIRA_BASE_URL", ""), env="JIRA_BASE_URL")
        _row("Email", config_data.get("JIRA_EMAIL", ""), env="JIRA_EMAIL")
        _row("API Token", config_data.get("JIRA_API_TOKEN", ""), masked=True, env="JIRA_API_TOKEN")
        _token_help("JIRA_API_TOKEN")
        _row("Project Key", config_data.get("JIRA_PROJECT_KEY", ""), env="JIRA_PROJECT_KEY")
        _row("Confluence Space", config_data.get("CONFLUENCE_SPACE_KEY", ""), env="CONFLUENCE_SPACE_KEY")

    def _sec_azure() -> None:
        _heading("Azure DevOps", wide=True)
        _row("Org URL", config_data.get("AZURE_DEVOPS_ORG_URL", ""), env="AZURE_DEVOPS_ORG_URL")
        _row("Project", config_data.get("AZURE_DEVOPS_PROJECT", ""), env="AZURE_DEVOPS_PROJECT")
        _row("PAT", config_data.get("AZURE_DEVOPS_TOKEN", ""), masked=True, env="AZURE_DEVOPS_TOKEN")
        _token_help("AZURE_DEVOPS_TOKEN")
        _row("Team", config_data.get("AZURE_DEVOPS_TEAM", ""), env="AZURE_DEVOPS_TEAM")

    def _sec_github() -> None:
        _heading("GitHub", wide=True)
        _row("Token", config_data.get("GITHUB_TOKEN", ""), masked=True, env="GITHUB_TOKEN")
        _token_help("GITHUB_TOKEN")
        # The repository estate Analysis scans (comma-separated owners/orgs). The
        # TUI wizard discovers and picks these per run, so this row is the default
        # that lets CLI/MCP/headless runs reach GitHub without --github-owner.
        _gh_owners = config_data.get("TEAM_ANALYSIS_GITHUB_OWNERS", "")
        # Unset does not mean "nothing will be scanned": the getter falls back to
        # the owner of STANDUP_GITHUB_REPO, so name that owner rather than imply
        # headless runs have no estate at all.
        _gh_legacy = (config_data.get("STANDUP_GITHUB_REPO", "") or "").split("/", 1)[0]
        _gh_placeholder = (
            f"{_gh_legacy} (from Standup repo) — chosen per run in Analysis setup"
            if _gh_legacy
            else "not set — chosen per run in Analysis setup"
        )
        _row(
            "Analysis Owners",
            _gh_owners or _gh_placeholder,
            value_style="" if _gh_owners else theme.dim,
            env="TEAM_ANALYSIS_GITHUB_OWNERS",
        )

    def _sec_notion() -> None:
        # Independent doc tool (its own integration token, unlike Confluence).
        _heading("Notion", wide=True)
        _row("Token", config_data.get("NOTION_TOKEN", ""), masked=True, env="NOTION_TOKEN")
        _token_help("NOTION_TOKEN")
        _row("Root Page/DB", config_data.get("NOTION_ROOT_PAGE_ID", ""), env="NOTION_ROOT_PAGE_ID")

    def _sec_storage() -> None:
        # One YEABOI_HOME override relocates the whole data tree (exports, logs,
        # sessions DB…). Clicking it opens the data-dir editor (with the move offer).
        _heading("Storage")
        _row("Data Directory", config_data.get("YEABOI_HOME", "") or "~/.yeaboi (default)", env="YEABOI_HOME")
        # Filesystem-sandbox whitelist (fs_policy.py): the folders yeaboi may touch
        # outside its data home. Comma-separated, edited in place like any other row.
        _allowed = config_data.get("YEABOI_ALLOWED_PATHS", "")
        _row(
            "Allowed Paths",
            _allowed or "none — sandboxed to data dir",
            value_style="" if _allowed else theme.dim,
            env="YEABOI_ALLOWED_PATHS",
        )

    def _sec_standup() -> None:
        # Secrets (Slack webhook, SMTP password) are masked like every other credential.
        _heading("Daily Standup")
        _row("GitHub Repo", config_data.get("STANDUP_GITHUB_REPO", ""), env="STANDUP_GITHUB_REPO")
        _row("Slack Webhook", config_data.get("SLACK_WEBHOOK_URL", ""), masked=True, env="SLACK_WEBHOOK_URL")
        _row("SMTP Host", config_data.get("STANDUP_SMTP_HOST", ""), env="STANDUP_SMTP_HOST")
        _row("SMTP User", config_data.get("STANDUP_SMTP_USER", ""), env="STANDUP_SMTP_USER")
        _row("SMTP Password", config_data.get("STANDUP_SMTP_PASSWORD", ""), masked=True, env="STANDUP_SMTP_PASSWORD")
        _row("Email Recipients", config_data.get("STANDUP_EMAIL_RECIPIENTS", ""), env="STANDUP_EMAIL_RECIPIENTS")

    def _sec_voice() -> None:
        # Local, offline dictation (double-tap Space in any text field) — works with every
        # LLM provider, no API key. See docs: "Voice Input".
        _heading("Voice Input")
        from yeaboi.voice import backend_label, unsupported_blocker, voice_install_command, voice_state

        # Read-only status, worded from the one shared vocabulary so this page
        # cannot disagree with the chip and the tip about the same machine. Any
        # text carrying an install command wraps rather than cropping mid-command.
        _voice_state = voice_state()
        if _voice_state == "ready":
            _row("Dictation", f"available — {backend_label()}", value_style=theme.good, wrap=True)
        elif _voice_state == "installable":
            _row(
                "Dictation",
                "not installed — double-tap Space in any text field, then Enter",
                value_style=theme.warn,
                wrap=True,
            )
        elif _voice_state == "unsupported":
            _row("Dictation", f"unavailable — {unsupported_blocker()}", value_style=theme.warn, wrap=True)
        else:
            _row(
                "Dictation",
                f"not installed — offer dismissed; {voice_install_command()}",
                value_style=theme.warn,
                wrap=True,
            )
        # Editable rather than a button: the user ruled out a Settings *action*,
        # but a permanent "never" needs some way back that is not an env var.
        _row(
            "Install Offer",
            "off"
            if config_data.get("VOICE_INSTALL_OFFER", "").strip().lower() in {"off", "false", "0", "no"}
            else "on",
            env="VOICE_INSTALL_OFFER",
        )
        # Enter on this row opens the device picker (see _pick_voice_device) rather
        # than the free-text editor every other row uses.
        _row(
            "Input Device",
            config_data.get("VOICE_DEVICE", "") or "system default",
            value_style="" if config_data.get("VOICE_DEVICE", "") else theme.dim,
            env="VOICE_DEVICE",
        )
        _row("Model Size", config_data.get("VOICE_MODEL", "") or "base (default)", env="VOICE_MODEL")

    def _sec_bedrock() -> None:
        _heading("AWS Bedrock")
        _row("Region", config_data.get("AWS_REGION", ""), env="AWS_REGION")
        _row("Profile", config_data.get("AWS_PROFILE", ""), env="AWS_PROFILE")

    def _sec_advanced() -> None:
        """Everything here with a closed set of answers is a pick, not a text field.

        Each row reads its live option through settings_choice_value, because the
        defaults are asymmetric — Tips and the Duck are on unless the literal
        "false", LangSmith is off unless the literal "true", and WARNING sits third
        in the level list. Nothing here may assume the first option is the default.
        """
        _heading("Advanced")
        _choice_row("Log Level", "LOG_LEVEL")
        # An arbitrary integer, so it stays typed — presets would rule out every
        # number that is not one of them.
        _row("Session Prune Days", config_data.get("SESSION_PRUNE_DAYS", "30"), env="SESSION_PRUNE_DAYS")
        _choice_row("Tips", "TIPS_ENABLED")
        _choice_row("Duck", "DUCK_ENABLED")
        _choice_row("Screensaver", "SCREENSAVER_STYLE")
        _choice_row("LangSmith", "LANGSMITH_TRACING")
        _row("Config File", config_data.get("_config_path", ""))  # read-only path

    _builders = {
        "provider": _sec_provider,
        "jira": _sec_jira,
        "azure": _sec_azure,
        "github": _sec_github,
        "notion": _sec_notion,
        "storage": _sec_storage,
        "standup": _sec_standup,
        "voice": _sec_voice,
        "bedrock": _sec_bedrock,
        "advanced": _sec_advanced,
    }
    active_tab = max(0, min(active_tab, len(_SETTINGS_TABS) - 1))
    # Render every heading section grouped under the active tab.
    for _section in _SETTINGS_TAB_SECTIONS[_SETTINGS_TABS[active_tab]]:
        _builders[_section]()

    # ── Section boxes, laid out in an adaptive-width grid ─────────
    # Narrow sections fill a column grid; wide ones stack below it at full width.
    # The boxed grid is flattened to one Text per rendered row by _render_to_lines,
    # which keeps the "one body line == one rendered row" assumption the viewport
    # and scrollbar math below rely on.
    def _section_box(sec_title: str, rows: list, box_h: int, box_w: int, *, focused: bool = False) -> Panel:
        head = Text(
            sec_title,
            style=f"bold {theme.accent_bright}" if focused else f"bold {theme.accent}",
            no_wrap=True,
            overflow="ellipsis",
        )
        return Panel(
            Group(*(rows or [Text("")])),
            title=head,
            title_align="left",
            box=rich.box.ROUNDED,
            border_style=theme.accent if focused else theme.sep,
            padding=(0, 1),
            width=box_w,
            height=box_h,
        )

    body_lines: list = [Text("")]  # the blank the first heading used to supply
    # body-line index → [(x0, x1, env, label, masked)] for every editable row on it.
    # Side-by-side boxes put two editable rows on the SAME line, so a click needs the
    # column range as well as the row (see _row_regions / the settings loop).
    _line_meta: dict[int, list[tuple[int, int, str, str, bool]]] = {}
    _abs_x = _TAB_COL_OFFSET + len(_grid_indent)  # grid column 0 in absolute terminal columns

    # Navigation map, published for the loop: the balanced columns (each top to
    # bottom) and the full-width boxes stacked under them. Arrow keys walk this.
    box_cols: list[list[int]] = []
    box_tail: list[int] = []
    box_span: dict[int, tuple[int, int]] = {}  # section index → (first, last) body line
    field_line: dict[tuple[int, int], int] = {}  # (section, field) → body line

    def _mark(line_idx: int, x0: int, x1: int, box_idx: int, rows: list, box_h: int) -> None:
        box_span[box_idx] = (line_idx, line_idx + box_h - 1)
        _fi = 0
        for _k, _ln in enumerate(rows):
            if isinstance(_ln, _EditableRow):
                _line_meta.setdefault(line_idx + 1 + _k, []).append(
                    (_abs_x + x0, _abs_x + x1, _ln.env, _ln.label, bool(_ln.masked))
                )
                field_line[(box_idx, _fi)] = line_idx + 1 + _k
                _fi += 1

    _numbered = [(_i, _t, _r, _w) for _i, (_t, _r, _w) in enumerate(sections)]
    _narrow = [s for s in _numbered if not s[3]]
    _wide = [s for s in _numbered if s[3]]

    if _narrow:
        # Columns, not rows: each box is exactly as tall as its own content and the
        # sections are dealt into the shortest column so far. A row-based grid had to
        # pad every box in a row up to the tallest one, which left a two-row section
        # sitting in a six-row box next to a full one.
        _cols: list[list] = [[] for _ in range(n_cols)]
        _col_h = [0] * n_cols
        for _sec in _narrow:
            _j = _col_h.index(min(_col_h))
            _col_h[_j] += (1 if _cols[_j] else 0) + len(_sec[2]) + 2  # blank + border + rows + border
            _cols[_j].append(_sec)
        _cols = [c for c in _cols if c]  # a column can stay empty when sections < n_cols

        # Even the columns out: a short column shares the shortfall between its
        # boxes (up to _SETTINGS_MAX_STRETCH rows each) so the block reads as one
        # tidy rectangle instead of ragged stacks ending at different depths.
        _natural = [sum(len(_r) + 2 for _, _, _r, _ in c) + len(c) - 1 for c in _cols]
        _target = max(_natural)
        _stretch: list[dict[int, int]] = []
        for _c, _nat in zip(_cols, _natural, strict=True):
            _gain: dict[int, int] = {}
            _left = min(_target - _nat, _SETTINGS_MAX_STRETCH * len(_c))
            _i = 0
            while _left > 0:  # round-robin, so no single box absorbs the whole gap
                _bi = _c[_i % len(_c)][0]
                if _gain.get(_bi, 0) < _SETTINGS_MAX_STRETCH:
                    _gain[_bi] = _gain.get(_bi, 0) + 1
                    _left -= 1
                _i += 1
            _stretch.append(_gain)

        _grid = Table(show_header=False, show_edge=False, box=None, padding=(0, 1), pad_edge=False)
        for _ in _cols:
            _grid.add_column(width=col_w, overflow="crop")
        _base = len(body_lines)
        _cells: list = []
        for _j, _col in enumerate(_cols):
            _stack: list = []
            _off = 0  # line offset within this column (every column starts at _base)
            _cs = _j * (col_w + 2)  # padding=(0,1) both sides → a 2-col gutter
            for _bi, _t, _r, _ in _col:
                if _stack:
                    _stack.append(Text(""))  # one blank line between stacked boxes
                    _off += 1
                _h = len(_r) + 2 + _stretch[_j].get(_bi, 0)
                _stack.append(_section_box(_t, _r, _h, col_w, focused=(_bi == sel_box)))
                _mark(_base + _off, _cs + 1, _cs + col_w - 2, _bi, _r, _h)
                _off += _h
            box_cols.append([_bi for _bi, _, _, _ in _col])
            _cells.append(Group(*_stack))
        _grid.add_row(*_cells)
        body_lines.extend(_render_to_lines(_grid, grid_w, _grid_indent))

    for _bi, _t, _r, _ in _wide:
        body_lines.append(Text(""))
        _base = len(body_lines)
        _box = _section_box(_t, _r, len(_r) + 2, full_w, focused=(_bi == sel_box))
        body_lines.extend(_render_to_lines(_box, grid_w, _grid_indent))
        box_tail.append(_bi)
        _mark(_base, 1, full_w - 2, _bi, _r, len(_r) + 2)

    # ── Layout: tab bar → active section (scrollable) → context hint ──────
    tab_lines, tab_spans = _settings_tab_bar(_SETTINGS_TABS, active_tab, theme, width, pos=tab_pos)
    # header = blank + title(2) + blank + tab bar; action_h reserves blank + hint +
    # a trailing blank so the hint sits ABOVE the app-wide music pocket, which
    # overwrites the bottom-most content row.
    viewport_h = calc_viewport(height, header_h=4 + len(tab_lines), action_h=3)
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    actual_scroll = min(scroll_offset, max_scroll)
    # Keyboard focus drags the viewport with it: land the selected field (or the
    # whole selected box) inside the window, preferring its top when it can't fit.
    if sel_box >= 0:
        _t0, _t1 = box_span.get(sel_box, (0, 0))
        if sel_field >= 0 and (sel_box, sel_field) in field_line:
            _t0 = _t1 = field_line[(sel_box, sel_field)]
        if _t1 >= actual_scroll + viewport_h:
            actual_scroll = _t1 - viewport_h + 1
        if _t0 < actual_scroll:
            actual_scroll = _t0
        actual_scroll = max(0, min(actual_scroll, max_scroll))
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    if scroll_meta is not None:
        scroll_meta["scroll"] = actual_scroll  # hand the auto-scrolled offset back
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    # Editable-row click regions: each visible env-backed row maps to its absolute
    # terminal row plus the column range of the box it sits in. The viewport starts
    # just below the tab bar (labels + underline).
    _viewport_top = _TAB_LABELS_ROW + len(tab_lines)
    row_regions = [
        (_viewport_top + _j, _x0, _x1, _env, _label, _masked)
        for _j, _idx in enumerate(range(actual_scroll, actual_scroll + len(visible)))
        for (_x0, _x1, _env, _label, _masked) in _line_meta.get(_idx, [])
    ]

    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll, always_show=True)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    # Context hint replaces the old button row: the tab bar is the navigation now.
    _enter_label = {"loglevel": "cycle log level"}.get(settings_tab_action(active_tab), "configure")
    hint = Text(justify="left", no_wrap=True)  # drawn inside a chrome tab, so no body pad
    if editing is not None:
        # In-place edit mode: keys go to the field being edited.
        hint.append("type to edit", style=theme.accent)
        hint.append("  ·  ", style=theme.muted)
        hint.append("Enter", style=theme.accent)
        hint.append("  save  ·  ", style=theme.muted)
        hint.append("Esc", style=theme.accent)
        hint.append("  cancel  ·  '-' clears", style=theme.muted)
    elif sel_field >= 0:
        hint.append("↑/↓", style=theme.accent)
        hint.append("  pick value  ·  ", style=theme.muted)
        hint.append("Enter", style=theme.accent)
        hint.append("  edit  ·  ", style=theme.muted)
        hint.append("Esc", style=theme.accent)
        hint.append("  back to sections", style=theme.muted)
    elif sel_box >= 0:
        hint.append("arrows", style=theme.accent)
        hint.append("  pick section  ·  ", style=theme.muted)
        hint.append("Enter", style=theme.accent)
        hint.append("  open  ·  ", style=theme.muted)
        hint.append("Esc", style=theme.accent)
        hint.append("  back to tabs", style=theme.muted)
    else:
        hint.append("←/→", style=theme.accent)
        hint.append("  switch tab  ·  ", style=theme.muted)
        hint.append("↓", style=theme.accent)
        hint.append("  sections  ·  ", style=theme.muted)
        hint.append("Enter", style=theme.accent)
        hint.append(f"  {_enter_label}", style=theme.muted)  # 'Esc back' dropped — the back tab covers it

    content = Group(
        Text(""),
        title,
        Text(""),
        *tab_lines,
        # No blank here: each section's heading already leads with one blank, so a
        # blank after the tab bar would double up before the first heading.
        viewport_renderable,
        Text(""),
        Text(""),  # the hint moved into the bottom pocket (see _hint_tab)
        Text(""),  # keeps the content above the music pocket band
    )

    panel = build_page_panel(content, theme=SETTINGS_THEME, height=height)
    # The controls ride in the bottom-left pocket as one more tab beside "back",
    # instead of taking a body row of their own.
    panel._hint_tab = hint
    # The save toast ("Anthropic Key updated") is spoken through the shared
    # duck voice by the settings loop. Deliberate opt-in, same trade-off as
    # the usage page: brief feedback beats silence, bounded by the fence.
    _with_bubble_room(panel, width)
    # Attach the tab click regions (labels + underline rows, absolute cols) so the
    # loop can hit-test tab clicks — see settings_tab_regions / the settings loop.
    panel._tab_regions = [
        (_TAB_LABELS_ROW, _TAB_UNDERLINE_ROW, _TAB_COL_OFFSET + s, _TAB_COL_OFFSET + e - 1) for (s, e) in tab_spans
    ]
    panel._row_regions = row_regions  # (abs_row, x0, x1, env, label, masked) per visible editable row
    panel._box_cols = box_cols  # balanced columns of section indices — the arrow-key map
    panel._box_tail = box_tail  # the full-width boxes stacked under the columns
    panel._box_fields = box_fields  # per section, its editable (env, label, masked) in order
    return panel


# ---------------------------------------------------------------------------
# Voice input — microphone picker
# ---------------------------------------------------------------------------

# How many device rows fit before the list scrolls. Hosts with a virtual audio
# driver installed can report a dozen inputs.
_MIC_ROWS = 8


def voice_picker_keypress(key: str, state: dict) -> str:
    """Advance the microphone picker one keypress; returns the action to take.

    Split out as a pure function — the picker itself runs a Rich ``Live`` loop,
    which is untestable, but the thing worth testing is exactly this: which key
    moves, which selects, which tests, which backs out. Mutates ``state["sel"]``
    and returns one of ``"select" | "cancel" | "test" | "system" | "none"``.
    """
    count = max(1, len(state.get("devices", [])))
    if key in ("up", "k"):
        state["sel"] = (state.get("sel", 0) - 1) % count
        return "none"
    if key in ("down", "j"):
        state["sel"] = (state.get("sel", 0) + 1) % count
        return "none"
    if key in ("enter", " "):
        return "select"
    if key == "t":
        return "test"
    if key == "d":
        return "system"  # clear the preference — back to the system default
    if key in ("esc", "q"):
        return "cancel"
    return "none"


def _build_voice_device_screen(
    devices: list[dict],
    selected: int,
    *,
    current: str = "",
    width: int = 80,
    height: int = 24,
    testing: bool = False,
    level: float = 0.0,
    notice: str = "",
) -> Panel:
    """Build the microphone picker page.

    ``devices`` are :func:`yeaboi.voice.list_input_devices` dicts. ``testing``
    switches the highlighted row\'s meter live — the only way to answer "is this
    the mic that actually hears me?" without leaving the app.

    # See docs: "TUI system" — Settings sub-page
    """
    from yeaboi.ui.shared._components import SETTINGS_THEME, build_key_hints, build_scrollbar, settings_title
    from yeaboi.ui.shared._voice_input import level_meter

    theme = SETTINGS_THEME
    lines: list = [Text(""), settings_title(width=width), Text("")]
    lines.append(Text(PAD + "Microphone", style="bold white", justify="left"))
    lines.append(
        Text(
            PAD + ("Recording from this input. VOICE_DEVICE remembers it." if devices else ""),
            style=theme.muted,
            justify="left",
        )
    )
    lines.append(Text(""))

    if not devices:
        lines.append(
            Text(
                PAD + "  No microphones detected. Plug one in and reopen this page — the list is rescanned.",
                style=theme.warn,
                justify="left",
            )
        )
    else:
        # Window the list around the selection so a long device table scrolls —
        # a host with a virtual audio driver can report a dozen inputs. The
        # scrollbar is what says the window is a window; without it the rows
        # beyond it simply look absent.
        max_start = max(0, len(devices) - _MIC_ROWS)
        start = max(0, min(selected - _MIC_ROWS // 2, max_start))
        rows: list[Text] = []
        for index, device in enumerate(devices[start : start + _MIC_ROWS], start=start):
            focused = index == selected
            row = Text(PAD + ("  ▸ " if focused else "    "), style=theme.accent if focused else theme.dim)
            row.append(device["name"], style="bold white" if focused else theme.muted)
            tags = []
            if device["is_default"]:
                tags.append("system default")
            if current and current == device["name"]:
                tags.append("selected")
            row.append(f"   {device['channels']} ch · {device['samplerate']} Hz", style=theme.dim)
            if tags:
                row.append(f"   {', '.join(tags)}", style=theme.good if "selected" in tags else theme.dim)
            if focused and testing:
                row.append(f"   {level_meter(level)}", style=theme.good)
            row.no_wrap = True
            row.overflow = "ellipsis"
            rows.append(row)
        scrollbar = build_scrollbar(_MIC_ROWS, len(devices), start, max_start)
        if scrollbar is None:
            lines.extend(rows)
        else:
            rows.extend(Text("") for _ in range(max(0, _MIC_ROWS - len(rows))))
            shell = Table.grid(expand=True, padding=0)
            shell.add_column(ratio=1)
            shell.add_column(width=1)
            shell.add_row(Group(*rows), scrollbar)
            lines.append(shell)

    lines.append(Text(""))
    if notice:
        lines.append(Text(PAD + "  " + notice, style=theme.warn, justify="left", no_wrap=True, overflow="ellipsis"))
    elif testing:
        lines.append(
            Text(PAD + "  Speak now — the bar moves when this mic hears you. Any key stops.", style=theme.good)
        )
    else:
        lines.append(Text(""))

    hint = build_key_hints(
        [("↑/↓", "choose"), ("t", "test mic"), ("Enter", "use"), ("d", "system default"), ("Esc", "back")], pad=PAD
    )
    lines.append(Text(""))
    lines.append(hint)

    return build_page_panel(Group(*lines), theme=theme, border_style=theme.sep, height=height)
