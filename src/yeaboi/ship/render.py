"""Rich text rendering for ship runs — the CLI's text format.

Same convention as ``provenance/render.py``: pure formatting over the frozen
artifacts, shared by whichever surface wants a terminal rendering. No colour
is load-bearing; the JSON format carries the same facts.
"""

from __future__ import annotations

from rich.console import Group
from rich.text import Text

from yeaboi.agent.state import ShipRun
from yeaboi.ship.budget import BudgetStatus

_STATUS_STYLE = {
    "approved": "bold green",
    "rejected": "bold red",
    "failed": "bold red",
    "cancelled": "yellow",
    "awaiting_approval": "bold yellow",
    "running": "cyan",
    "planned": "dim",
}


def format_diff_rich(diff: str) -> Group:
    """The patch, tinted. Additions green, removals red, hunk headers cyan."""
    lines: list[Text] = []
    for raw in diff.splitlines():
        if raw.startswith("+++") or raw.startswith("---"):
            style = "bold"
        elif raw.startswith("+"):
            style = "green"
        elif raw.startswith("-"):
            style = "red"
        elif raw.startswith("@@"):
            style = "cyan"
        elif raw.startswith("diff --git"):
            style = "bold"
        else:
            style = ""
        lines.append(Text(raw, style=style))
    return Group(*lines)


def format_run_rich(run: ShipRun, *, show_diff: bool = False) -> Group:
    """One run, in full — the gate summary and the terminal report.

    ``show_diff`` is what the *gate* passes: approving a push on a file count
    is not review, so the prompt renders the patch and the worktree path.
    """
    lines: list[Text] = []
    header = Text()
    header.append(f"{run.story_id or '(no story)'} ", style="bold")
    header.append(f"[{run.status}]", style=_STATUS_STYLE.get(run.status, ""))
    if run.run_id:
        header.append(f"  {run.run_id}", style="dim")
    lines.append(header)
    if run.branch:
        lines.append(Text(f"  branch    {run.branch}"))
    if run.worktree:
        lines.append(Text(f"  worktree  {run.worktree}", style="dim"))
    if run.diff_stat:
        # The gate wants every file; a status line wants the totals. A 60-file
        # run should not print 60 rows every time someone asks what happened.
        stat_lines = run.diff_stat.splitlines() if show_diff else run.diff_stat.splitlines()[-1:]
        for index, stat_line in enumerate(stat_lines):
            label = "diff     " if index == 0 else "         "
            lines.append(Text(f"  {label} {stat_line.strip()}"))
    if run.validation.configured:
        verdict = "passed" if run.validation.passed else f"FAILED (exit {run.validation.exit_code})"
        style = "green" if run.validation.passed else "red"
        lines.append(Text(f"  checks    {run.validation.command} — {verdict}", style=style))
    else:
        lines.append(Text("  checks    none configured — nothing was proven", style="yellow"))
    if run.cost_usd:
        lines.append(Text(f"  cost      ${run.cost_usd:.2f}"))
    for kind, severity, label in run.transcript_findings:
        lines.append(Text(f"  finding   {severity}: {label} ({kind})", style="yellow"))
    if run.pr_url:
        lines.append(Text(f"  pr        {run.pr_url}", style="bold cyan"))
    for warning in run.warnings:
        lines.append(Text(f"  ⚠ {warning}", style="yellow"))
    parts: list = [Group(*lines)]
    if show_diff:
        parts.append(Text(""))
        if run.diff_text:
            parts.append(Text("  the change itself:", style="bold"))
            parts.append(format_diff_rich(run.diff_text))
        else:
            parts.append(
                Text(
                    "  the diff could not be read — inspect the worktree before approving",
                    style="yellow",
                )
            )
    return Group(*parts)


def format_history_rich(runs: list[ShipRun]) -> Group:
    """Recent runs, one line each, newest first."""
    if not runs:
        return Group(Text("No ship runs yet — `yeaboi ship run <STORY>` starts one.", style="dim"))
    lines: list[Text] = []
    for run in runs:
        line = Text()
        line.append(f"{run.created_at[:16]:16}  ", style="dim")
        line.append(f"{run.story_id:12}  ", style="bold")
        line.append(f"{run.status:18}", style=_STATUS_STYLE.get(run.status, ""))
        if run.cost_usd:
            line.append(f"  ${run.cost_usd:.2f}", style="dim")
        if run.pr_url:
            line.append(f"  {run.pr_url}", style="cyan")
        lines.append(line)
    return Group(*lines)


def format_budget_rich(status: BudgetStatus) -> Group:
    """The launch budget's current posture."""
    lines = [
        Text("Launch budget", style="bold"),
        Text(
            f"  active {status.active}/{status.max_concurrent}"
            f" · last hour {status.launched_last_hour}/{status.max_per_hour}"
            f" · last 24h {status.launched_last_day}/{status.max_per_day}"
        ),
    ]
    if status.paused_until:
        lines.append(Text(f"  circuit OPEN: {status.paused_reason}", style="bold red"))
    return Group(*lines)
