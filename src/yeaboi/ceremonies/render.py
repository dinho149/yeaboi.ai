"""Rich renderings for the ceremonies surfaces.

The listing answers three questions in one glance, and the third is the one
nothing else in yeaboi answers: what is declared, when it next happens, and
**what it did last time**. A schedule you cannot see the outcome of is a
schedule you stop trusting after the first silent morning.
"""

from __future__ import annotations

from datetime import datetime

from rich.console import Group
from rich.table import Table
from rich.text import Text

from yeaboi.agent.state import Ceremony, CeremonyRun
from yeaboi.ceremonies.catalog import CATALOG, UNSCHEDULABLE, lookup
from yeaboi.ceremonies.scheduler import weekday_spec_label

# Outcome → (glyph, style). A skip is amber, not red: it is a decision the
# guards made, and colouring it like a failure teaches people to ignore both.
_OUTCOME_STYLE = {
    "ok": ("✓", "green"),
    "failed": ("✗", "red"),
    "skipped_stale": ("⏱", "yellow"),
    "skipped_over_cap": ("$", "yellow"),
    "skipped_paused": ("⏸", "yellow"),
}


def outcome_chip(run: CeremonyRun | None) -> Text:
    """One-glance verdict for a ceremony's most recent run."""
    if run is None:
        return Text("— never run", style="dim")
    glyph, style = _OUTCOME_STYLE.get(run.outcome, ("?", "dim"))
    when = run.fired_at[:16].replace("T", " ")
    return Text(f"{glyph} {run.outcome} · {when}", style=style)


def cadence_label(ceremony: Ceremony) -> str:
    return f"{weekday_spec_label(ceremony.weekdays)} at {ceremony.at}"


def format_ceremonies_rich(ceremonies: list[Ceremony], last_runs: dict[str, CeremonyRun | None]) -> Group:
    """The listing: what is declared, when, where it lands, and how it went."""
    if not ceremonies:
        return Group(
            Text("No ceremonies declared.", style="dim"),
            Text("Add one with:  yeaboi ceremonies add morning-standup --mode standup --at 09:00", style="dim"),
        )
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Ceremony")
    table.add_column("Mode")
    table.add_column("When")
    table.add_column("Lands in")
    table.add_column("Last run")
    for ceremony in ceremonies:
        mode = lookup(ceremony.mode)
        name = Text(ceremony.name)
        if not ceremony.enabled:
            name = Text(f"{ceremony.name} (paused)", style="dim")
        table.add_row(
            name,
            mode.label if mode else Text(f"{ceremony.mode}?", style="red"),
            cadence_label(ceremony),
            ", ".join(ceremony.channels),
            outcome_chip(last_runs.get(ceremony.name)),
        )
    return Group(table)


def format_history_rich(runs: list[CeremonyRun]) -> Group:
    """The ledger. Errors are shown, not summarised away — the reason a run did
    not happen is the whole point of keeping it."""
    if not runs:
        return Group(Text("Nothing has fired yet.", style="dim"))
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("When")
    table.add_column("Ceremony")
    table.add_column("Outcome")
    table.add_column("Cost")
    table.add_column("Delivered")
    table.add_column("Detail")
    for run in runs:
        glyph, style = _OUTCOME_STYLE.get(run.outcome, ("?", "dim"))
        delivered = ", ".join(f"{ch}{'' if ok else ' ✗'}" for ch, ok in run.delivery) or "—"
        table.add_row(
            run.fired_at[:16].replace("T", " "),
            run.ceremony,
            Text(f"{glyph} {run.outcome}", style=style),
            f"${run.cost_usd:.2f}" if run.cost_usd else "—",
            delivered,
            Text((run.error or run.detail or "")[:80], style="red" if run.error else ""),
        )
    return Group(table)


def format_modes_rich() -> Group:
    """What can run on a cadence, and — just as usefully — what cannot and why."""
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("Mode")
    table.add_column("What it posts")
    table.add_column("Args")
    table.add_column("~ / run")
    for mode in CATALOG:
        args = ", ".join(f"{p.name}={p.default or '…'}" for p in mode.params) or "—"
        table.add_row(mode.key, mode.blurb, args, f"${mode.est_cost_usd:.2f}")

    refused = Table(show_header=True, header_style="bold dim", box=None, pad_edge=False)
    refused.add_column("Not schedulable")
    refused.add_column("Why")
    for key, reason in sorted(UNSCHEDULABLE.items()):
        refused.add_row(Text(key, style="dim"), Text(reason, style="dim"))

    return Group(
        table,
        Text(""),
        Text("Cost is an estimate for the mode's own LLM calls — set --monthly-cap to bound it.", style="dim"),
        Text(""),
        refused,
    )


def next_fire(ceremony: Ceremony, now: datetime | None = None) -> str:
    """A human answer to "when does this happen next?" — or why it will not.

    A paused ceremony reports the pause, not its cadence: the cadence of
    something that will not fire is trivia, and showing it reads as a schedule.
    """
    if not ceremony.enabled:
        return "paused"
    return cadence_label(ceremony)
