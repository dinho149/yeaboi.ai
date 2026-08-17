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


def format_run_rich(run: ShipRun) -> Group:
    """One run, in full — the gate summary and the terminal report."""
    lines: list[Text] = []
    header = Text()
    header.append(f"{run.story_id or '(no story)'} ", style="bold")
    header.append(f"[{run.status}]", style=_STATUS_STYLE.get(run.status, ""))
    if run.run_id:
        header.append(f"  {run.run_id}", style="dim")
    lines.append(header)
    if run.branch:
        lines.append(Text(f"  branch    {run.branch}"))
    if run.diff_stat:
        stat_tail = run.diff_stat.splitlines()[-1].strip()
        lines.append(Text(f"  diff      {stat_tail}"))
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
    return Group(*lines)


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
