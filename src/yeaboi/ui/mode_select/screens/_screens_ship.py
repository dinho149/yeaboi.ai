"""Screen builders for the Ship mode page (story → coding agent → PR).

Same shared-component structure as every other mode page (tui-standards):
pinned wordmark title + subtitle + content, wrapped in ``build_page_panel``
with the ship theme.

The gate screen is the exception to "lists are capped, not scrolled", because
it is the only control between agent-authored code and a pushed branch: the
patch gets a real scrolling viewport, and everything else on the screen is
elastic around it. The layout's one invariant is that the **buttons always
render** — a Panel of fixed height crops from the bottom, and a gate whose
button row is off screen still answers Enter with "Approve".

Builders are pure (no clocks, no logging — they run every frame); the page
loop in ``_ship.py`` owns time, keys, and state.
"""

from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.agent.state import ShipRun
from yeaboi.ship.render import safe_console_text
from yeaboi.ui.mode_select.screens._screens_agents import _build_agent_progress_body, _fmt_elapsed
from yeaboi.ui.shared._components import (
    PAD,
    SHIP_THEME,
    TITLE_ROWS,
    build_action_buttons,
    build_page_panel,
    build_reveal_subtitle,
    build_scrollbar,
    ship_title,
)
from yeaboi.ui.shared._scroll import clamp_scroll, max_scroll, publish_geometry

SHIP_PICK_ACTIONS = ["Launch", "Back"]
SHIP_GATE_ACTIONS = ["Approve", "Reject", "Cancel Run"]
SHIP_RESULT_ACTIONS = ["Copy", "Back"]

_MAX_DIFF_ROWS = 8
_MAX_TAIL_ROWS = 6
_MAX_FINDING_ROWS = 4
_MIN_DIFF_PANE_ROWS = 3  # below this the pane is dropped, never overlapped onto the buttons
PANEL_CHROME_ROWS = 4  # build_page_panel's border (2) + padding (2), same as calc_viewport's
# Inner content width: the border (2) plus build_page_panel's padding=(1, 2) (4).
# Every row the gate draws is clipped to this and marked no_wrap, because a row
# that wraps is a row the layout did not count — and uncounted rows are what
# push the buttons off a fixed-height Panel.
_PANEL_SIDE_COLS = 6

# The engine's component ids (ship/engine.py emits these); the screen owns
# what "pending" looks like.
SHIP_PHASES: tuple[tuple[str, str], ...] = (
    ("ship-setup", "Prepare isolated worktree"),
    ("ship-implement", "Run the coding agent"),
    ("ship-validate", "Validate the diff"),
    ("ship-gate", "Await your approval"),
    ("ship-finalize", "Push branch, open PR"),
)


def _field_row(label: str, value: str, *, editing: bool, theme) -> Text:
    """One settings-style field line: label, value, and an edit cursor."""
    row = Text()
    row.append(PAD)
    row.append(f"{label}: ", style=theme.muted)
    if editing:
        row.append(value, style="bold white")
        row.append("▏", style=theme.accent_bright)
    else:
        row.append(value or "(none)", style="rgb(200,200,210)" if value else "rgb(110,110,125)")
    return row


# What a launch produces. "one" is one branch and one PR for the whole item;
# "split" fans an epic out into one stacked run per story.
SCOPE_ONE = "one"
SCOPE_SPLIT = "split"

_GLYPH = {"epic": "◆", "story": "○", "task": "·"}

# Every row the pick screen spends outside the outline: the panel border and
# padding (4), the title (2) and subtitle (1), the two overflow markers, the
# Repo/Check/Scope rows, the hint, the message, the three button rows, and the
# five blank separators. A Panel of fixed height crops from the bottom, so
# under-counting here is what pushes the button row off screen.
_PICK_CHROME_ROWS = 23
_MIN_OUTLINE_ROWS = 3


def outline_window(total: int, selected: int, height: int) -> tuple[int, int]:
    """(start, count) of the outline slice to draw — a window that follows the selection.

    Height-derived rather than a constant: a three-level tree is far taller than
    the flat story list this replaced, and the one invariant the gate and pick
    screens share is that the button row always renders.
    """
    rows = max(_MIN_OUTLINE_ROWS, height - _PICK_CHROME_ROWS)
    if total <= rows:
        return 0, total
    start = max(0, min(selected - rows // 2, total - rows))
    return start, rows


def _outline_row(row, *, selected: bool, expanded: bool, has_children: bool, width: int, theme) -> Text:
    """One tree line: indent, chevron, id, title, detail. Never wraps."""
    text = Text(no_wrap=True, overflow="ellipsis")
    text.append(PAD)
    text.append("▸ " if selected else "  ", style=theme.accent_bright if selected else "dim")
    text.append("  " * row.depth, style="dim")
    # The expand state rides at the END of the row, as standup's team row does:
    # a leading chevron would be the same glyph as the selection cursor, and on
    # a selected collapsed row the two are indistinguishable.
    text.append(f"{_GLYPH.get(row.level, '·')} ", style="dim")
    text.append(row.id, style=theme.id if selected else "rgb(120,140,160)")
    budget = max(10, width - 36 - row.depth * 2 - len(row.id))
    text.append(f"  {row.title[:budget]}", style="bold white" if selected else "rgb(160,160,175)")
    if row.detail:
        text.append(f"  · {row.detail}", style=theme.muted)
    if has_children:
        text.append("  ▾" if expanded else "  ▸", style=theme.accent if selected else "dim")
    return text


def _scope_value(row, scope_mode: str, split_count: int) -> str:
    """What the Scope field says, given what is selected.

    Only an epic can split — a story and a task have no child stories — so on
    anything else the field states that rather than offering a toggle that
    would silently do nothing.
    """
    if row is None:
        return ""
    if row.level != "epic":
        return f"Together — one branch, one PR (a {row.level} is a single unit)"
    if scope_mode == SCOPE_SPLIT:
        return f"Separately — one stacked PR per story ({split_count} runs)"
    return "Together — one branch, one PR for the whole epic"


def _build_ship_pick_screen(
    rows: list,
    selected: int,
    *,
    expanded: set | None = None,
    has_children: set | None = None,
    scope_mode: str = SCOPE_ONE,
    split_count: int = 0,
    repo: str,
    check_command: str,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float | None = None,
    action_sel: int = 0,
    message: str = "",
    edit_field: str = "",
    edit_buf: str = "",
) -> Panel:
    """The launch screen: pick any plan item, confirm repo, check command and scope.

    ``rows`` is the *visible* outline (``ship.scope.OutlineRow``) — the page loop
    has already dropped the children of collapsed parents. ``expanded`` and
    ``has_children`` are key sets used only for the chevrons.
    """
    theme = SHIP_THEME
    expanded = expanded or set()
    has_children = has_children or set()
    inner = max(20, width - _PANEL_SIDE_COLS)
    parts: list = [
        Text(""),
        ship_title(shimmer_tick, width=width),
        build_reveal_subtitle("Any epic, story or task — implemented behind your approval", None, justify="center"),
        Text(""),
    ]
    if not rows:
        parts.append(Text(""))
        parts.append(Text("No plan found.", style="rgb(200,200,210)", justify="center"))
        parts.append(
            Text("Generate a plan in Planning first — Ship implements its items.", style="dim", justify="center")
        )
    else:
        selected = max(0, min(selected, len(rows) - 1))
        start, count = outline_window(len(rows), selected, height)
        if start:
            parts.append(Text(f"{PAD}… {start} above", style="dim", no_wrap=True))
        for index in range(start, start + count):
            row = rows[index]
            parts.append(
                _outline_row(
                    row,
                    selected=index == selected,
                    expanded=row.key in expanded,
                    has_children=row.key in has_children,
                    width=inner,
                    theme=theme,
                )
            )
        remaining = len(rows) - (start + count)
        if remaining > 0:
            parts.append(Text(f"{PAD}… and {remaining} more", style="dim", no_wrap=True))
        parts.append(Text(""))
        parts.append(
            _field_row("Repo", edit_buf if edit_field == "repo" else repo, editing=edit_field == "repo", theme=theme)
        )
        parts.append(
            _field_row(
                "Check",
                edit_buf if edit_field == "check" else check_command,
                editing=edit_field == "check",
                theme=theme,
            )
        )
        parts.append(
            _field_row("Scope", _scope_value(rows[selected], scope_mode, split_count), editing=False, theme=theme)
        )
        parts.append(Text(f"{PAD}↑/↓ move · space expand · s scope · r repo · c check", style="dim", no_wrap=True))
    parts.append(Text(""))
    parts.append(Text(message, style=theme.warn if message else "", justify="center"))
    parts.append(Text(""))
    parts.extend(build_action_buttons(SHIP_PICK_ACTIONS, action_sel))
    return build_page_panel(Group(*parts), theme=theme, height=height)


def _build_ship_progress_screen(
    progress: list,
    *,
    tick: float,
    width: int = 80,
    height: int = 24,
    status: str = "",
    board_link: str = "",
    board_code: str = "",
    batch_note: str = "",
) -> Panel:
    """The in-flight page: the phase checklist fed by the engine's events."""
    theme = SHIP_THEME
    parts: list = [
        Text(""),
        ship_title(None, width=width),
        build_reveal_subtitle("Supervising the coding agent", None, justify="center"),
    ]
    if batch_note:
        # The checklist is keyed by phase, so a batch overwrites it per member;
        # without this the screen looks like one run restarting over and over.
        parts.append(Text(batch_note, style=theme.accent, justify="center", no_wrap=True))
    if board_link:
        # The shareable board line, once the tunnel is up. It carries no secret
        # (the join code is separate), so it is safe to render.
        parts.append(Text(f"📺 watch/share: {board_link}", style=theme.accent, justify="center", no_wrap=True))
        if board_code:
            # A teammate opening the share link lands on the join gate and needs
            # this code. Without it here the code lives only in the log file, so
            # the headline capability is unusable — retro and poker both show it.
            parts.append(Text(f"join code: {board_code}", style=f"bold {theme.accent}", justify="center", no_wrap=True))
    parts.extend(_build_agent_progress_body(SHIP_PHASES, progress, tick=tick, theme=theme, status=status))
    parts.append(Text(""))
    parts.append(
        Text("esc cancels the run — the agent is stopped and nothing is pushed", style="dim", justify="center")
    )
    return build_page_panel(Group(*parts), theme=theme, height=height)


def _one_row(text: str, *, width: int, style: str = "") -> Text:
    """One row that is guaranteed to stay one row.

    ``no_wrap`` + ``crop`` rather than arithmetic alone: a wrapped row is a row
    the layout did not count, and uncounted rows crop the buttons off the
    bottom of a fixed-height Panel. (``no_wrap`` only takes effect when the
    Text is rendered inside a Panel/Group, which is how every gate row is
    drawn — a bare ``console.print`` of the same Text would ignore it.)
    """
    return Text(
        safe_console_text(text)[: max(20, width - _PANEL_SIDE_COLS)], style=style, no_wrap=True, overflow="crop"
    )


def _gate_line(label: str, value: str, *, style: str = "rgb(200,200,210)", width: int = 80) -> Text:
    row = _one_row(f"{PAD}{label}  ", width=width, style="rgb(110,110,125)")
    row.append(safe_console_text(value), style=style)
    row.truncate(max(20, width - _PANEL_SIDE_COLS), overflow="ellipsis")
    return row


def _diff_row(line: str, *, width: int, theme) -> Text:
    """One patch line, tinted by what it does to the file."""
    style = {
        "+": theme.good,
        "-": theme.bad,
        "@": "rgb(120,170,200)",
    }.get(line[:1], "rgb(160,160,175)")
    if line.startswith(("+++", "---", "diff --git")):
        style = "bold rgb(200,200,210)"
    return _one_row(f"{PAD}  {line}", width=width, style=style)


def _rows_of(parts: list) -> int:
    """How many terminal rows ``parts`` occupies.

    Every entry is a single ``Text`` row except the ASCII wordmark, which is
    ``TITLE_ROWS`` tall — the shared constant, never a second copy of the
    number. The gate needs an exact count, not an estimate: a row too many
    crops the button block off the bottom of a fixed-height Panel, and a gate
    whose buttons are off screen still answers Enter with "Approve".
    """
    return sum(TITLE_ROWS if isinstance(part, Text) and "█" in part.plain else 1 for part in parts)


def _diff_viewport(run: ShipRun, *, width: int, rows: int, offset: int, theme) -> tuple[object, int, int]:
    """(renderable, clamped offset, max offset) for the scrollable patch pane."""
    lines = run.diff_text.splitlines()
    if not lines:
        return (
            Text(
                f"{PAD}  the diff could not be read — inspect the worktree before approving",
                style=theme.warn,
            ),
            0,
            0,
        )
    max_start = max_scroll(len(lines), rows)
    start = clamp_scroll(offset, len(lines), rows)
    visible = [_diff_row(line, width=width, theme=theme) for line in lines[start : start + rows]]
    visible.extend(Text("") for _ in range(max(0, rows - len(visible))))
    scrollbar = build_scrollbar(rows, len(lines), start, max_start)
    if scrollbar is None:
        return Group(*visible), start, max_start
    shell = Table.grid(expand=True, padding=0)
    shell.add_column(ratio=1)
    shell.add_column(width=1)
    shell.add_row(Group(*visible), scrollbar)
    return shell, start, max_start


_SNAPSHOT_SUBTITLES = {
    "approved": "Shipped — this diff was approved and pushed",
    "rejected": "Rejected — the agent ran out of rework attempts",
    "failed": "Failed — nothing was pushed",
    "cancelled": "Cancelled — nothing was pushed",
    "awaiting_approval": "Still waiting at the gate — resume to finish it",
}


# The gate rows are hand-aligned to a six-column label.
_LEVEL_LABEL = {"epic": "Epic  ", "story": "Story ", "task": "Task  "}


def _item_label(run: ShipRun) -> str:
    return _LEVEL_LABEL.get(run.level, "Item  ")


def _snapshot_subtitle(run: ShipRun) -> str:
    """What a saved run's header says instead of asking for a decision."""
    return _SNAPSHOT_SUBTITLES.get(run.status, f"Saved run — {run.status}")


def _build_ship_gate_screen(
    run: ShipRun,
    *,
    action_sel: int = 0,
    width: int = 80,
    height: int = 24,
    comment_edit: str | None = None,
    message: str = "",
    diff_offset: int = 0,
    scroll_meta: dict | None = None,
    actions: list[str] | None = None,
    snapshot: bool = False,
) -> Panel:
    """The approval gate: what the agent did, what was proven, your call.

    ``comment_edit`` non-None means the rejection comment is being typed; it
    renders an input line and the buttons step back. ``diff_offset`` scrolls
    the patch pane — the gate is the only control before a push, so it shows
    the change itself and the worktree path, never just a file count — and the
    builder publishes the pane's real geometry into ``scroll_meta`` so the
    loop's offset can never run past what is on screen.

    ``snapshot=True`` re-uses this exact layout to show a *saved* run in the
    hub: same rows, same patch pane, a subtitle that says the run is finished
    rather than asking for a decision. ``actions`` lets the caller supply its own
    button row (the hub's is Export/Delete/Back, not Approve/Reject); it defaults
    to the live gate's.
    """
    theme = SHIP_THEME
    subtitle = _snapshot_subtitle(run) if snapshot else "Review the diff like a stranger wrote it — one did"
    head: list = [
        Text(""),
        ship_title(None, width=width),
        build_reveal_subtitle(subtitle, None, justify="center"),
        Text(""),
        _gate_line(_item_label(run), run.item_id, width=width),
        _gate_line("Branch", run.branch, style=theme.id, width=width),
    ]
    if run.batch_total:
        head.append(_gate_line("Batch ", f"story {run.batch_index} of {run.batch_total}", width=width))
    if run.worktree:
        # Where to look when the pane is not enough — the patch is capped, the
        # checkout is not.
        head.append(_gate_line("Tree  ", run.worktree, style="rgb(140,140,155)", width=width))

    # The elastic sections, each with a floor it may not drop below. When the
    # window cannot hold everything these give way, in this order, so the
    # fixed rows and the buttons always survive: losing the tail of a failure
    # log is a far cheaper loss than losing the controls.
    stat_rows = [
        _one_row(f"{PAD}  {line}", width=width, style="rgb(160,160,175)") for line in run.diff_stat.splitlines()
    ]
    tail_rows: list = []
    check_rows: list = []
    if run.validation.configured:
        state = "✓ passed" if run.validation.passed else f"✗ FAILED (exit {run.validation.exit_code})"
        style = theme.good if run.validation.passed else theme.bad
        check_rows.append(_gate_line("Checks", f"{run.validation.command} — {state}", style=style, width=width))
        if not run.validation.passed:
            tail_rows = [
                _one_row(f"{PAD}  {line}", width=width, style="rgb(140,120,120)")
                for line in run.validation.output_tail.splitlines()[-_MAX_TAIL_ROWS:]
            ]
    else:
        check_rows.append(_gate_line("Checks", "none configured — nothing was proven", style=theme.warn))
    finding_rows: list = []
    if run.transcript_findings:
        finding_rows.append(
            _gate_line(
                "Safety", f"{len(run.transcript_findings)} transcript finding(s):", style=theme.warn, width=width
            )
        )
        finding_rows += [
            _one_row(f"{PAD}  {severity}: {label} ({kind})", width=width, style=theme.warn)
            for kind, severity, label in run.transcript_findings[:_MAX_FINDING_ROWS]
        ]
    extra_rows: list = []
    if run.cost_usd:
        extra_rows.append(_gate_line("Cost  ", f"${run.cost_usd:.2f}", width=width))
    if run.rejection_count:
        extra_rows.append(_gate_line("Rework", f"attempt {run.rejection_count + 1} after your feedback", width=width))

    # Everything below the pane is fixed, so build it first: what is left over
    # decides how much elastic content — and then how much patch — fits.
    foot: list = []
    if comment_edit is not None:
        prompt = Text()
        prompt.append(PAD)
        prompt.append("Why reject? ", style=theme.muted)
        prompt.append(comment_edit, style="bold white")
        prompt.append("▏", style=theme.accent_bright)
        foot.append(prompt)
        foot.append(
            _one_row(f"{PAD}enter sends the feedback to the agent · esc keeps reviewing", width=width, style="dim")
        )
    foot.append(Text(message, style=theme.warn if message else "", justify="center"))
    foot.append(Text(""))
    foot.extend(build_action_buttons(actions or SHIP_GATE_ACTIONS, action_sel))

    # PANEL_CHROME_ROWS is what build_page_panel's border and padding cost;
    # calc_viewport documents the same 4 for the screens that can use it.
    budget = (
        height
        - PANEL_CHROME_ROWS
        - _rows_of(head)
        - len(check_rows)
        - len(extra_rows)
        - _rows_of(foot)
        - 2  # the spacer below the stat and the one above the buttons
    )
    label = _one_row(f"{PAD}the change itself  ↑↓ pgup/pgdn home/end scroll", width=width, style="rgb(110,110,125)")

    # Which END a section sheds from is not arbitrary: a failure tail's LAST
    # lines are the actual error, and the stat's last line is the "N files
    # changed" total. Both shed from the front so the row that carries the
    # meaning is the one that survives.
    def _shed(rows: list, floor: int, wanted: int) -> int:
        while wanted > 0 and len(rows) > floor:
            rows.pop(0)
            wanted -= 1
        return wanted

    elastic = ((tail_rows, 0), (finding_rows, 1), (stat_rows, 1))
    pane_rows = 0
    if comment_edit is None:
        pane_rows = budget - len(stat_rows) - len(tail_rows) - len(finding_rows) - 1  # 1 for the label row
        for rows, floor in elastic:
            while len(rows) > floor and pane_rows < _MIN_DIFF_PANE_ROWS:
                rows.pop(0)
                pane_rows += 1
    over = len(stat_rows) + len(tail_rows) + len(finding_rows) + max(0, pane_rows) - budget
    for rows, floor in elastic:
        over = _shed(rows, floor, over)

    parts: list = [*head, *stat_rows, Text(""), *check_rows, *tail_rows, *extra_rows, *finding_rows, Text("")]
    if comment_edit is None and pane_rows >= _MIN_DIFF_PANE_ROWS:
        viewport, diff_offset, max_offset = _diff_viewport(
            run, width=width, rows=pane_rows, offset=diff_offset, theme=theme
        )
        publish_geometry(scroll_meta, max_offset, pane_rows)
        parts.append(label)
        parts.append(viewport)
    elif comment_edit is None:
        # Too short to show a patch AND the buttons. Say where the patch is
        # rather than rendering a gate that looks reviewed when it was not.
        publish_geometry(scroll_meta, 0, 1)
        parts.append(
            Text(
                f"{PAD}patch hidden — window too short; read it in {run.worktree or 'the worktree'}",
                style=theme.warn,
            )
        )
    else:
        publish_geometry(scroll_meta, 0, 1)
    parts.extend(foot)
    return build_page_panel(Group(*parts), theme=theme, height=height)


def _build_ship_result_screen(
    run: ShipRun,
    *,
    action_sel: int = 0,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float | None = None,
    notice: str = "",
) -> Panel:
    """The terminal page: what happened, where the PR is, what it cost."""
    theme = SHIP_THEME
    headline = {
        "approved": ("✓ Shipped", theme.good),
        "rejected": ("✗ Rejected at the gate", theme.bad),
        "failed": ("✗ Run failed", theme.bad),
        "cancelled": ("Run cancelled", theme.warn),
    }.get(run.status, (run.status, theme.muted))
    parts: list = [
        Text(""),
        ship_title(shimmer_tick, width=width),
        build_reveal_subtitle("Run finished", None, justify="center"),
        Text(""),
        Text(f"{PAD}{headline[0]}", style=f"bold {headline[1]}" if not headline[1].startswith("bold") else headline[1]),
        Text(""),
    ]
    if run.item_id:
        parts.append(_gate_line(_item_label(run), run.item_id))
    if run.batch_total:
        parts.append(_gate_line("Batch ", f"story {run.batch_index} of {run.batch_total}"))
    if run.branch:
        parts.append(_gate_line("Branch", run.branch, style=theme.id))
    if run.pr_url:
        parts.append(_gate_line("PR    ", run.pr_url, style=theme.accent_bright))
    if run.cost_usd:
        parts.append(_gate_line("Cost  ", f"${run.cost_usd:.2f}"))
    for phase in run.phases:
        mark = {"completed": "✓", "failed": "✗", "skipped": "○"}.get(phase.status, "○")
        style = {"✓": theme.good, "✗": theme.bad}.get(mark, "dim")
        detail = f" · {phase.detail}" if phase.detail else ""
        stamp = f" ({_fmt_elapsed(phase.duration_s)})" if phase.duration_s >= 1 else ""
        parts.append(Text(f"{PAD}{mark} {phase.name}{detail}{stamp}"[: max(30, width - 6)], style=style))
    for warning in run.warnings[:3]:
        parts.append(Text(f"{PAD}⚠ {warning[: max(30, width - 8)]}", style=theme.warn))
    parts.append(Text(""))
    parts.append(Text(notice, style=theme.accent if notice else "", justify="center"))
    parts.append(Text(""))
    parts.extend(build_action_buttons(SHIP_RESULT_ACTIONS, action_sel))
    return build_page_panel(Group(*parts), theme=theme, height=height)
