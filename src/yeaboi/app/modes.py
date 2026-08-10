"""The mode catalogue, for the web app.

yeaboi is twelve modes in two families, and the app showed none of them — it
opened on an empty project list, which is not what the product is. This is the
list the landing and the navigation rail are built from.

**Why a second list rather than importing the TUI's.** ``_MODE_CARDS`` lives in
``ui/mode_select/screens/_screens.py`` next to render tests that pin exact
output and hardcoded indices. Importing a terminal UI module into the web
server to read data out of it coupls the two in the wrong direction, and
editing that list to suit the web app risks those tests for no reason. So the
catalogue is declared here and a test asserts the two agree on keys, titles and
badges — one source of truth enforced, rather than one file shared.

``accent`` names the ``[data-mode]`` value from ``design/tokens.css``. Eight
exist there — retro, poker, analysis, usage, reporting, standup, planning,
performance — each with a light-theme variant and a contrast audit behind it.
The three Agents modes have colours on the marketing site but **not** in the
design layer, so they wear the default until someone adds them properly: an
accent needs a light variant and has to clear the contrast matrix, which is a
design-layer change rather than a line in this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Family = Literal["humans", "agents"]

#: How much of a mode the web app can currently do.
#:
#: ``run`` — the engine is reachable from a web request today.
#: ``view`` — its artifacts can be read, but running it is not wired yet.
#: ``soon`` — neither, and the mode map says why.
Support = Literal["run", "view", "soon"]


@dataclass(frozen=True)
class Mode:
    key: str
    title: str
    description: str
    family: Family
    #: The [data-mode] accent, or "" for the default.
    accent: str
    support: Support
    beta: bool = False
    #: Why a mode is not runnable yet. Shown in the UI rather than hidden,
    #: because a card that does nothing with no explanation reads as broken.
    note: str = ""


MODES: tuple[Mode, ...] = (
    Mode(
        key="team-analysis",
        title="Analysis",
        description="Analyse your team's board to learn velocity, estimation patterns, and delivery signals.",
        family="humans",
        accent="analysis",
        support="soon",
        note="Needs a tracker connection.",
    ),
    Mode(
        key="project-planning",
        title="Planning",
        description="Decompose your project into epics, user stories, tasks, and a sprint plan.",
        family="humans",
        accent="planning",
        support="view",
        note="Import a plan from the terminal app to read it here.",
    ),
    Mode(
        key="daily-standup",
        title="Standup",
        description="Run a daily standup: detect team activity, sprint-day confidence, and deliver a summary.",
        family="humans",
        accent="standup",
        support="soon",
    ),
    Mode(
        key="retro",
        title="Retro",
        description="Run a collaborative sprint retro: teammates add cards from a browser, then AI drafts actions.",
        family="humans",
        accent="retro",
        support="view",
        note="Live boards are started from the terminal app and appear here.",
    ),
    Mode(
        key="poker",
        title="Poker",
        description="Run planning poker: the team votes on sprint or backlog tickets in a browser; points sync.",
        family="humans",
        accent="poker",
        support="view",
        note="Live boards are started from the terminal app and appear here.",
    ),
    Mode(
        key="performance",
        title="Performance",
        description="Manage each engineer: 1:1 prep, 1:1 summaries, and 6-month reviews from real delivery data.",
        family="humans",
        accent="performance",
        support="soon",
        beta=True,
    ),
    Mode(
        key="reporting",
        title="Reporting",
        description="Summarise delivered work for the business — last sprint or last month, as slides, HTML or MD.",
        family="humans",
        accent="reporting",
        support="soon",
    ),
    Mode(
        key="usage",
        title="Usage",
        description="View API token usage, session history, and cost estimates.",
        family="humans",
        accent="usage",
        support="soon",
        note="No headless entry point yet — it lives inside the terminal UI.",
    ),
    Mode(
        key="settings",
        title="Settings",
        description="Manage API keys, LLM provider, and board configuration.",
        family="humans",
        accent="",
        support="run",
    ),
    Mode(
        key="agent-usage",
        title="Usage",
        description="See what your AI agents cost: tokens, cache, per-model and per-project spend, daily trend.",
        family="agents",
        accent="",  # see the module header: no design-layer accent yet
        support="soon",
        beta=True,
        note="Reads agent logs from the machine the server runs on.",
    ),
    Mode(
        key="agent-standup",
        title="Standup",
        description="A daily digest of what your agents did: sessions worked, commits and PRs, open threads.",
        family="agents",
        accent="",  # see the module header: no design-layer accent yet
        support="soon",
        beta=True,
        note="Reads agent logs from the machine the server runs on.",
    ),
    Mode(
        key="agent-security",
        title="Security",
        description="Audit your agent setup: permissions, MCP servers, secrets exposure, risky commands.",
        family="agents",
        accent="",  # see the module header: no design-layer accent yet
        support="soon",
        beta=True,
        note="Audits the machine the server runs on, which is not yours when hosted.",
    ),
)

BY_KEY: dict[str, Mode] = {mode.key: mode for mode in MODES}


def payload() -> list[dict[str, object]]:
    """The catalogue as the front end reads it."""
    return [
        {
            "key": mode.key,
            "title": mode.title,
            "description": mode.description,
            "family": mode.family,
            "accent": mode.accent,
            "support": mode.support,
            "beta": mode.beta,
            "note": mode.note,
        }
        for mode in MODES
    ]
