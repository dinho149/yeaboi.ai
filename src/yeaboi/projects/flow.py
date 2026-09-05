"""The context flow — what each mode leaves for the others inside a project.

One fact table for every surface that explains a project: the TUI's Projects
page draws it as a strip, and the desktop keeps an equal copy in
``lib/yeaboi/reads.ts``. ``reads`` are the ``CONTEXT_DEP_TOKENS`` a mode
consumes (see ``projects/scope.py`` and each engine's scope call site);
``leaves`` is the one fragment that says what the run leaves behind.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class FlowStep:
    key: str  # the mode card's key
    label: str
    reads: tuple[str, ...]  # context-dep tokens this mode consumes
    leaves: str  # what it leaves for the others, sentence case, one fragment


FLOW: tuple[FlowStep, ...] = (
    FlowStep(
        "project-planning",
        "Plan",
        ("retro", "standup", "analysis"),
        "the sprint plan every other run frames itself with",
    ),
    FlowStep("team-analysis", "Analysis", (), "the team profile a scoped plan starts from"),
    FlowStep("daily-standup", "Standup", ("plan",), "blockers and a confidence trend"),
    FlowStep("poker", "Poker", ("plan", "standup", "retro", "analysis"), "estimates sized to this project"),
    FlowStep("retro", "Retro", ("retro", "standup"), "action items and carry-over"),
    FlowStep("reporting", "Report", ("plan",), "a report about this project alone"),
)

# The Agents world's projects scope reports to a repo, not to each other.
AGENTS_FLOW_LINE = "Agents projects scope their reports to one repository."


def flow_for(world: str, available: Iterable[str]) -> tuple[FlowStep, ...]:
    """The steps a world's menu can run, in ``FLOW`` order; none for Agents."""
    if world == "agents":
        return ()
    keys = set(available)
    return tuple(step for step in FLOW if step.key in keys)
