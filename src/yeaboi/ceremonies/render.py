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


def local_stamp(fired_at: str, *, with_date: bool = True) -> str:
    """An ISO timestamp as local wall-clock time, for display only.

    The ledger stores UTC (with its offset), but a ceremony's ``at`` is local —
    launchd and cron fire in local time. Slicing the stored string would show a
    run at 17:13 for a ceremony the user scheduled at 18:13, which reads as a
    bug in the scheduler rather than a timezone in the renderer.

    Unparseable input is returned trimmed rather than raising: a history row
    with an odd stamp is still a row worth showing.
    """
    try:
        moment = datetime.fromisoformat(fired_at).astimezone()
    except (TypeError, ValueError):
        return (fired_at or "")[:16].replace("T", " ")
    return moment.strftime("%Y-%m-%d %H:%M" if with_date else "%m-%d %H:%M")


# Outcome → (glyph, tone). A skip is amber, not red: it is a decision the
# guards made, and colouring it like a failure teaches people to ignore both.
#
# A *tone* rather than a colour, and public rather than private, because two
# surfaces read this and only one of them has a palette. The CLI renders to a
# bare console and wants Rich's own colour names; the TUI screen has a Theme and
# must not spell a colour at all (tui-standards rule 2). Naming the meaning and
# letting each side resolve it is the same split the web payloads use.
OUTCOME_MARKS: dict[str, tuple[str, str]] = {
    "ok": ("✓", "good"),
    "failed": ("✗", "bad"),
    "skipped_stale": ("⏱", "warn"),
    "skipped_over_cap": ("$", "warn"),
    "skipped_paused": ("⏸", "warn"),
    "skipped_once": ("⤼", "warn"),
}

# What each tone means to a console with no Theme behind it.
_CONSOLE_TONE = {"good": "green", "warn": "yellow", "bad": "red"}


def outcome_mark(outcome: str) -> tuple[str, str]:
    """(glyph, tone) for a run outcome; an unrecognised one is a dim question mark."""
    return OUTCOME_MARKS.get(outcome, ("?", "dim"))


def outcome_chip(run: CeremonyRun | None) -> Text:
    """One-glance verdict for a ceremony's most recent run."""
    if run is None:
        return Text("— never run", style="dim")
    glyph, tone = outcome_mark(run.outcome)
    when = local_stamp(run.fired_at)
    return Text(f"{glyph} {run.outcome} · {when}", style=_CONSOLE_TONE.get(tone, "dim"))


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
        glyph, tone = outcome_mark(run.outcome)
        style = _CONSOLE_TONE.get(tone, "dim")
        delivered = ", ".join(f"{ch}{'' if ok else ' ✗'}" for ch, ok in run.delivery) or "—"
        table.add_row(
            local_stamp(run.fired_at),
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
    if ceremony.skip_next:
        # Distinct from "paused", and the date is the point: a one-shot skip
        # shown as a pause reads as something nobody expects to end by itself.
        return f"{cadence_label(ceremony)} · skipping {ceremony.skip_next}"
    return cadence_label(ceremony)
