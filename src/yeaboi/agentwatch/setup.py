"""The Agents family's mode table, with no screen attached.

Four modes that differ only in which callable runs, which artifact comes back
and which Markdown builder renders it — a fact the TUI encoded four times over
in its page functions and a second surface would have encoded a fifth. One
table, read by both.

Deliberately ``setup.py`` and not ``engine.py``: the engine glob in
``tests/unit/test_surface_parity.py`` forces every public name in an
``engine.py`` into ``CAPABILITIES`` as a capability of its own, and a mode
lookup table is not a capability.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentMode:
    """One agentwatch mode: what runs it, what it stores under, what renders it."""

    key: str  # the TUI mode key and the desktop route segment
    kind: str  # the store/export kind ("usage", "advisor", "standup", "security")
    label: str
    blurb: str
    engine: tuple[str, str]  # (module, attribute) — imported lazily
    artifact: tuple[str, str]  # the frozen dataclass a failure is reported as


MODES: tuple[AgentMode, ...] = (
    AgentMode(
        key="agent-usage",
        kind="usage",
        label="Agent Usage",
        blurb="What the coding agents on this machine cost, by model and by project.",
        engine=("yeaboi.agentwatch.engine", "run_agent_usage"),
        artifact=("yeaboi.agent.state", "AgentUsageReport"),
    ),
    AgentMode(
        key="agent-advisor",
        kind="advisor",
        label="Agent Advisor",
        blurb="Which share of that spend was recoverable, and what to change.",
        engine=("yeaboi.agentwatch.advisor", "run_agent_advisor"),
        artifact=("yeaboi.agent.state", "AgentAdvisorReport"),
    ),
    AgentMode(
        key="agent-standup",
        kind="standup",
        label="Agent Standup",
        blurb="What the agents actually did — a digest of their sessions.",
        engine=("yeaboi.agentwatch.engine", "run_agent_standup"),
        artifact=("yeaboi.agent.state", "AgentStandupDigest"),
    ),
    AgentMode(
        key="agent-security",
        kind="security",
        label="Agent Security",
        blurb="The security posture of the agent setup those sessions ran under.",
        engine=("yeaboi.agentwatch.engine", "run_agent_security"),
        artifact=("yeaboi.agent.state", "AgentSecurityReport"),
    ),
)

RESULT_ACTIONS = ("Export", "Copy", "Re-run", "Back")


def lookup(key: str) -> AgentMode | None:
    """The mode addressed by its key or its store kind, or ``None``."""
    for mode in MODES:
        if key in (mode.key, mode.kind):
            return mode
    return None


def require(key: str) -> AgentMode:
    """:func:`lookup`, raising :class:`ValueError` naming the four valid keys."""
    mode = lookup(key)
    if mode is None:
        raise ValueError(f"unknown agents mode {key!r} — choose from {', '.join(m.key for m in MODES)}")
    return mode


def _resolve(target: tuple[str, str]) -> Callable:
    from importlib import import_module

    module, attribute = target
    return getattr(import_module(module), attribute)


def run(mode: AgentMode, on_progress: Callable[[object], None] | None = None):
    """Run one mode's pipeline. The engines never raise — parse → fallback → format."""
    return _resolve(mode.engine)(on_progress=on_progress)


def failure_artifact(mode: AgentMode, exc: object):
    """The empty artifact a caller shows when the pipeline raised anyway."""
    return _resolve(mode.artifact)(warnings=(f"{mode.label} failed: {exc}",))


def markdown(mode: AgentMode, artifact) -> str:
    """The Markdown both Export and Copy hand out."""
    from yeaboi.agentwatch.export import build_markdown

    return build_markdown(artifact, kind=mode.kind)


def latest_artifact(kind: str, *, db_path=None):
    """The newest saved report of one kind, rehydrated, with its ``created_at``.

    ``None`` when history is empty or the stored payload cannot be rebuilt — the
    caller then falls back to the first-run loading screen. Never raises: a
    broken history row must not take down the page it was meant to speed up.
    """
    from yeaboi.agentwatch.store import AgentWatchStore, report_from_payload
    from yeaboi.paths import get_db_path

    try:
        with AgentWatchStore(db_path or get_db_path()) as store:
            row = store.latest_report(kind)
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent %s: could not read report history: %s", kind, exc)
        return None
    if not row:
        return None
    artifact = report_from_payload(kind, row.get("report"))
    if artifact is None:
        return None
    return artifact, str(row.get("created_at") or "")


def mode_options() -> list[dict]:
    """The four modes as a menu offers them, each with its last report's age."""
    options = []
    for mode in MODES:
        loaded = latest_artifact(mode.kind)
        options.append(
            {
                "key": mode.key,
                "kind": mode.kind,
                "label": mode.label,
                "blurb": mode.blurb,
                "last_report_at": loaded[1] if loaded else "",
            }
        )
    return options
