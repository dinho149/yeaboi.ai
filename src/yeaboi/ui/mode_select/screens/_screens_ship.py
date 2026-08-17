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
from yeaboi.ui.mode_select.screens._screens_agents import _build_agent_progress_body, _fmt_elapsed
from yeaboi.ui.shared._components import (
    PAD,
    SHIP_THEME,
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

_MAX_STORY_ROWS = 8
_MAX_DIFF_ROWS = 8
_MAX_TAIL_ROWS = 6
_MAX_FINDING_ROWS = 4
_MIN_DIFF_PANE_ROWS = 3  # below this the pane is dropped, never overlapped onto the buttons
_TITLE_ROWS = 6  # the ASCII wordmark's own height
PANEL_CHROME_ROWS = 4  # build_page_panel's border (2) + padding (2), same as calc_viewport's

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


def _build_ship_pick_screen(
    stories: list,
    selected: int,
    *,
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
    """The launch screen: pick a story, confirm repo + check command.

    ``stories`` is a list of ``UserStory``; ``edit_field`` is ``""`` (not
    editing), ``"repo"`` or ``"check"``, with ``edit_buf`` as the live buffer.
    """
    theme = SHIP_THEME
    parts: list = [
        Text(""),
        ship_title(shimmer_tick, width=width),
        build_reveal_subtitle("A story from your plan, implemented behind your approval", None, justify="center"),
        Text(""),
    ]
    if not stories:
        parts.append(Text(""))
        parts.append(Text("No stories found in your latest plan.", style="rgb(200,200,210)", justify="center"))
        parts.append(
            Text("Generate a plan in Planning first — Ship implements its stories.", style="dim", justify="center")
        )
    else:
        # A window that follows the selection, not a hard cap: a sprint plan
        # routinely has more than eight stories and every one must be
        # launchable from here.
        start = max(0, min(selected - _MAX_STORY_ROWS // 2, len(stories) - _MAX_STORY_ROWS))
        shown = stories[start : start + _MAX_STORY_ROWS]
        if start:
            parts.append(Text(f"{PAD}… {start} earlier", style="dim"))
        for offset, story in enumerate(shown):
            index = start + offset
            row = Text()
            row.append(PAD)
            marker = "▸ " if index == selected else "  "
            row.append(marker, style=theme.accent_bright if index == selected else "dim")
            row.append(f"{story.id}", style=theme.id if index == selected else "rgb(120,140,160)")
            title = getattr(story, "title", "") or getattr(story, "goal", "")
            row.append(
                f"  {title[: max(10, width - 30)]}", style="bold white" if index == selected else "rgb(160,160,175)"
            )
            points = getattr(story, "story_points", None)
            if points is not None:
                row.append(f"  · {int(points)} pts", style=theme.muted)
            parts.append(row)
        remaining = len(stories) - (start + len(shown))
        if remaining > 0:
            parts.append(Text(f"{PAD}… and {remaining} more", style="dim"))
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
        parts.append(Text(f"{PAD}↑/↓ story · r edit repo · c edit check command", style="dim"))
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
) -> Panel:
    """The in-flight page: the phase checklist fed by the engine's events."""
    theme = SHIP_THEME
    parts: list = [
        Text(""),
        ship_title(None, width=width),
        build_reveal_subtitle("Supervising the coding agent", None, justify="center"),
    ]
    parts.extend(_build_agent_progress_body(SHIP_PHASES, progress, tick=tick, theme=theme, status=status))
    parts.append(Text(""))
    parts.append(
        Text("esc cancels the run — the agent is stopped and nothing is pushed", style="dim", justify="center")
    )
    return build_page_panel(Group(*parts), theme=theme, height=height)


def _gate_line(label: str, value: str, *, style: str = "rgb(200,200,210)") -> Text:
    row = Text()
    row.append(PAD)
    row.append(f"{label}  ", style="rgb(110,110,125)")
    row.append(value, style=style)
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
    return Text(f"{PAD}  {line[: max(20, width - 10)]}", style=style)


def _rows_of(parts: list) -> int:
    """How many terminal rows ``parts`` occupies.

    Every entry is a single ``Text`` row except the ASCII wordmark. The gate
    needs an exact count, not an estimate: a row too many crops the button
    block off the bottom of a fixed-height Panel, and a gate whose buttons are
    off screen still answers Enter with "Approve" — which is a push.
    """
    return sum(_TITLE_ROWS if isinstance(part, Text) and "█" in part.plain else 1 for part in parts)


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
) -> Panel:
    """The approval gate: what the agent did, what was proven, your call.

    ``comment_edit`` non-None means the rejection comment is being typed; it
    renders an input line and the buttons step back. ``diff_offset`` scrolls
    the patch pane — the gate is the only control before a push, so it shows
    the change itself and the worktree path, never just a file count — and the
    builder publishes the pane's real geometry into ``scroll_meta`` so the
    loop's offset can never run past what is on screen.
    """
    theme = SHIP_THEME
    clip = max(20, width - 10)
    head: list = [
        Text(""),
        ship_title(None, width=width),
        build_reveal_subtitle("Review the diff like a stranger wrote it — one did", None, justify="center"),
        Text(""),
        _gate_line("Story ", run.story_id),
        _gate_line("Branch", run.branch, style=theme.id),
    ]
    if run.worktree:
        # Where to look when the pane is not enough — the patch is capped, the
        # checkout is not.
        head.append(_gate_line("Tree  ", run.worktree, style="rgb(140,140,155)"))

    # The elastic sections, each with a floor it may not drop below. When the
    # window cannot hold everything these give way, in this order, so the
    # fixed rows and the buttons always survive: losing the tail of a failure
    # log is a far cheaper loss than losing the controls.
    stat_rows = [Text(f"{PAD}  {line[:clip]}", style="rgb(160,160,175)") for line in run.diff_stat.splitlines()]
    tail_rows: list = []
    check_rows: list = []
    if run.validation.configured:
        state = "✓ passed" if run.validation.passed else f"✗ FAILED (exit {run.validation.exit_code})"
        style = theme.good if run.validation.passed else theme.bad
        check_rows.append(_gate_line("Checks", f"{run.validation.command} — {state}", style=style))
        if not run.validation.passed:
            tail_rows = [
                Text(f"{PAD}  {line[:clip]}", style="rgb(140,120,120)")
                for line in run.validation.output_tail.splitlines()[-_MAX_TAIL_ROWS:]
            ]
    else:
        check_rows.append(_gate_line("Checks", "none configured — nothing was proven", style=theme.warn))
    finding_rows: list = []
    if run.transcript_findings:
        finding_rows.append(
            _gate_line("Safety", f"{len(run.transcript_findings)} transcript finding(s):", style=theme.warn)
        )
        finding_rows += [
            Text(f"{PAD}  {severity}: {label} ({kind})", style=theme.warn)
            for kind, severity, label in run.transcript_findings[:_MAX_FINDING_ROWS]
        ]
    extra_rows: list = []
    if run.cost_usd:
        extra_rows.append(_gate_line("Cost  ", f"${run.cost_usd:.2f}"))
    if run.rejection_count:
        extra_rows.append(_gate_line("Rework", f"attempt {run.rejection_count + 1} after your feedback"))

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
        foot.append(Text(f"{PAD}enter sends the feedback to the agent · esc keeps reviewing", style="dim"))
    foot.append(Text(message, style=theme.warn if message else "", justify="center"))
    foot.append(Text(""))
    foot.extend(build_action_buttons(SHIP_GATE_ACTIONS, action_sel))

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
    label = Text(f"{PAD}the change itself  ↑↓ pgup/pgdn home/end scroll", style="rgb(110,110,125)")
    pane_rows = 0
    if comment_edit is None:
        pane_rows = budget - len(stat_rows) - len(tail_rows) - len(finding_rows) - 1  # 1 for the label row
        for rows, floor in ((tail_rows, 0), (finding_rows, 1), (stat_rows, 1)):
            while len(rows) > floor and pane_rows < _MIN_DIFF_PANE_ROWS:
                rows.pop()
                pane_rows += 1
    over = len(stat_rows) + len(tail_rows) + len(finding_rows) + max(0, pane_rows) - budget
    for rows, floor in ((tail_rows, 0), (finding_rows, 1), (stat_rows, 1)):
        while over > 0 and len(rows) > floor:
            rows.pop()
            over -= 1

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
    if run.story_id:
        parts.append(_gate_line("Story ", run.story_id))
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
