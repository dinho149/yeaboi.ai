"""Which modes may run on a cadence, and how to call each one.

This is the admission test for the whole feature, and it is deliberately a
table rather than a paragraph: a mode absent from ``CATALOG`` cannot be
scheduled from the TUI, the CLI or MCP, because every surface resolves through
:func:`lookup`. ``UNSCHEDULABLE`` records the ones left out *with their reason*,
so "why can't I schedule a retro" has an answer in the same file.

Engines and renderers are named as ``(module, attribute)`` strings and imported
lazily. Importing seven engine modules to draw a list of ceremony names would
pull LangChain into the TUI's start-up path, and the surface-parity check
AST-scans these modules rather than importing them.

Every declared parameter is a *string* on the wire (see ``state.Ceremony``);
:func:`engine_kwargs` coerces them to what the engine actually takes.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Parameter kinds a ceremony may declare. Deliberately small: a ceremony is a
# saved setup plus a clock, not a second copy of each mode's wizard.
PARAM_KINDS = ("str", "int", "bool")

# "" for an int parameter means "leave it to the engine's own default" — which
# is not the same as 0 (``days=0`` is an empty window, ``days=None`` is the
# working-day window the standup wants).
UNSET = ""


@dataclass(frozen=True)
class CeremonyParam:
    """One knob a ceremony may declare, with the wizard's label for it."""

    name: str
    kind: str  # one of PARAM_KINDS
    default: str = UNSET
    label: str = ""
    help: str = ""


@dataclass(frozen=True)
class CeremonyMode:
    """One schedulable mode: what to call, what to declare, what it costs."""

    key: str
    label: str
    blurb: str
    engine: tuple[str, str]  # (module, attribute) — imported lazily
    renderer: tuple[str, str]  # (module, attribute) → Dispatch
    params: tuple[CeremonyParam, ...] = ()
    session_param: str = ""  # keyword the engine takes session_id as; "" = none
    # Engine keywords the *ceremony* owns and the user may not set. The one that
    # matters is ``deliver``: the standup engine can deliver to the session's
    # saved channels itself, and a ceremony that let it would post the standup
    # twice — once from the engine, once from the ceremony's own channels.
    fixed_flags: tuple[tuple[str, bool], ...] = ()
    est_cost_usd: float = 0.0  # rough per-run LLM spend, for the authoring screen
    default_weekdays: str = "1-5"
    default_at: str = "09:00"


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------

CATALOG: tuple[CeremonyMode, ...] = (
    CeremonyMode(
        key="standup",
        label="Daily Standup",
        blurb="Who did what, what's blocked, and how confident the picture is.",
        engine=("yeaboi.standup.engine", "run_standup"),
        renderer=("yeaboi.ceremonies.renderers", "standup_dispatch"),
        params=(
            CeremonyParam(
                name="days",
                kind="int",
                label="Look-back (days)",
                help="Blank uses the working-day window — a Monday run still covers Friday.",
            ),
        ),
        session_param="session_id",
        fixed_flags=(("deliver", False),),
        est_cost_usd=0.15,
        default_at="09:00",
    ),
    CeremonyMode(
        key="report",
        label="Delivery report",
        blurb="The stakeholder-facing narrative of what shipped.",
        engine=("yeaboi.reporting.engine", "run_delivery_report"),
        renderer=("yeaboi.ceremonies.renderers", "report_dispatch"),
        params=(
            CeremonyParam(
                name="period",
                kind="str",
                default="last_week",
                label="Period",
                help="last_sprint | last_week | last_month | quarter",
            ),
        ),
        session_param="session_id",
        est_cost_usd=0.30,
        default_weekdays="1",
        default_at="08:00",
    ),
    CeremonyMode(
        key="agents-usage",
        label="Agent cost",
        blurb="What the AI coding agents spent, by model and project.",
        engine=("yeaboi.agentwatch.engine", "run_agent_usage"),
        renderer=("yeaboi.ceremonies.renderers", "agent_usage_dispatch"),
        params=(CeremonyParam(name="window_days", kind="int", default="30", label="Window (days)"),),
        est_cost_usd=0.10,
        default_weekdays="1",
        default_at="08:30",
    ),
    CeremonyMode(
        key="agents-advisor",
        label="Agent advisor",
        blurb="Recoverable spend and cache health across the window's agent sessions.",
        engine=("yeaboi.agentwatch.advisor", "run_agent_advisor"),
        renderer=("yeaboi.ceremonies.renderers", "agent_advisor_dispatch"),
        params=(CeremonyParam(name="window_days", kind="int", default="30", label="Window (days)"),),
        est_cost_usd=0.10,
        default_weekdays="1",
        default_at="08:35",
    ),
    CeremonyMode(
        key="agents-standup",
        label="Agent standup",
        blurb="What the agents did yesterday, in the same shape as the human one.",
        engine=("yeaboi.agentwatch.engine", "run_agent_standup"),
        renderer=("yeaboi.ceremonies.renderers", "agent_standup_dispatch"),
        params=(CeremonyParam(name="days", kind="int", label="Look-back (days)"),),
        fixed_flags=(("deliver", False),),
        est_cost_usd=0.12,
        default_at="09:05",
    ),
    CeremonyMode(
        key="agents-security",
        label="Agent security",
        blurb="Posture of the local agent setup: settings, MCP servers, secrets.",
        engine=("yeaboi.agentwatch.engine", "run_agent_security"),
        renderer=("yeaboi.ceremonies.renderers", "agent_security_dispatch"),
        params=(CeremonyParam(name="deep", kind="bool", default="false", label="Deep re-scan"),),
        est_cost_usd=0.10,
        default_weekdays="1",
        default_at="07:30",
    ),
)


# Modes deliberately left out, each with the reason. Kept beside the catalog
# because "why can't I schedule this" is asked of the same file that answers
# "what can I schedule", and a reason that lives only in a review comment is one
# nobody finds.
UNSCHEDULABLE: dict[str, str] = {
    "retro": "hosts a live board that needs the team in a room — history stays readable on demand",
    "poker": "hosts a live voting board; an estimate nobody attends is not an estimate",
    "performance": "1:1 prep needs a named engineer and lands in a human conversation, not a channel",
    "ship": "launches a coding agent behind an approval gate a human answers at a terminal",
    "planning": "an intake conversation, not a recurring readout",
    "roadmap": "an intake conversation, not a recurring readout",
    # The one genuine follow-up rather than a design refusal: team analysis is a
    # long multi-credential scan whose component/member scope only its own wizard
    # builds, and it returns a nested dict rather than an artifact — so there is
    # nothing to render into a Dispatch yet. It becomes schedulable when it has a
    # saved scope to reuse, the way the standup has one.
    "analyze": "needs a saved component/member scope before a cadence means anything",
}


_BY_KEY = {mode.key: mode for mode in CATALOG}


def schedulable_modes() -> tuple[CeremonyMode, ...]:
    """Every mode that may be scheduled, in presentation order."""
    return CATALOG


def lookup(key: str) -> CeremonyMode | None:
    """The catalog entry for ``key``, or None. Every surface resolves through this."""
    return _BY_KEY.get((key or "").strip().lower())


def refuse_reason(key: str) -> str:
    """Why ``key`` cannot be scheduled — the recorded reason, or a generic one."""
    key = (key or "").strip().lower()
    if key in _BY_KEY:
        return ""
    known = UNSCHEDULABLE.get(key)
    if known:
        return f"{key} cannot run on a cadence: {known}"
    return f"unknown ceremony mode {key!r} — try one of: {', '.join(sorted(_BY_KEY))}"


def _coerce(param: CeremonyParam, raw: str) -> object | None:
    """One declared string → the engine's type. None means "do not pass it"."""
    value = (raw or "").strip()
    if value == UNSET:
        return None
    if param.kind == "int":
        try:
            return int(value)
        except ValueError:
            logger.warning("ceremony param %s: %r is not an integer — ignoring", param.name, value)
            return None
    if param.kind == "bool":
        return value.lower() in ("1", "true", "yes", "on")
    return value


def engine_kwargs(mode: CeremonyMode, args: tuple[tuple[str, str], ...], *, session_id: str = "") -> dict:
    """Build the engine's keyword arguments from a ceremony's declared args.

    Unknown keys are dropped with a warning rather than raising: a ceremony
    declared against an older catalog should degrade to that mode's defaults,
    not stop firing. An unparseable value does the same — the engine's own
    default is always a safe answer, and a ceremony that refuses to run is a
    silence nobody notices.
    """
    declared = dict(args)
    kwargs: dict = {}
    for param in mode.params:
        coerced = _coerce(param, declared.pop(param.name, param.default))
        if coerced is not None:
            kwargs[param.name] = coerced
    for leftover in declared:
        logger.warning("ceremony mode %s: ignoring unknown arg %r", mode.key, leftover)
    for flag, value in mode.fixed_flags:
        kwargs[flag] = value
    if mode.session_param and session_id:
        kwargs[mode.session_param] = session_id
    return kwargs


def _resolve(target: tuple[str, str]) -> Callable:
    module, attr = target
    return getattr(importlib.import_module(module), attr)


def engine_callable(mode: CeremonyMode) -> Callable:
    """Import and return the mode's engine entry point."""
    return _resolve(mode.engine)


def renderer_callable(mode: CeremonyMode) -> Callable:
    """Import and return the mode's artifact → Dispatch renderer."""
    return _resolve(mode.renderer)
