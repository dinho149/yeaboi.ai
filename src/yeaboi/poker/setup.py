"""What a poker setup wizard asks, and which of its steps apply.

The rules the wizard runs on, with nothing rendered: which sources are on offer,
which steps a given source and scope earn, and what the resulting session is
called. :mod:`yeaboi.poker.tickets` still owns the tracker calls — this module
owns the *shape* of the conversation around them, so the TUI and the desktop ask
the same four questions in the same order and skip the same ones.

Deliberately ``setup.py`` and not ``engine.py``: the parity registry treats every
public name in an ``engine.py`` as a capability of its own, and a wizard's step
table is not a capability.
"""

from __future__ import annotations

from yeaboi.poker.tickets import (
    SOURCE_DEMO,
    SOURCE_JIRA,
    TICKET_TYPE_LABELS,
    TICKET_TYPES,
    available_sources,
    default_include_types,
    source_label,
)

#: Every step, in the order they are asked.
STEPS: tuple[str, ...] = ("source", "scope", "sprint", "types")

STEP_TITLES: dict[str, str] = {
    "source": "Where do the tickets come from?",
    "scope": "Which tickets should the team estimate?",
    "sprint": "Which sprint?",
    "types": "Which ticket types should be estimated?",
}

SCOPE_SPRINT = "sprint"
SCOPE_BACKLOG = "backlog"

#: The label a demo session carries; there is no tracker scope to name.
DEMO_SCOPE_LABEL = "Demo"

BACKLOG_SCOPE_LABEL = "Backlog"

_TYPE_SUBLABELS = {
    SOURCE_JIRA: {"story": "issuetype Story", "bug": "issuetype Bug", "task": "issuetype Task"},
    "default": {
        "story": "User Story / Product Backlog Item",
        "bug": "Bug",
        "task": "child tasks — usually not estimated",
    },
}


def source_options() -> list[dict]:
    """The tracker choices, configured ones first and Demo always last.

    Demo needs no tracker and its write-back is a no-op, so it is offered even
    when nothing is configured — that is the whole point of it.
    """
    options = [{"key": source, "label": source_label(source), "sub": ""} for source in available_sources()]
    options.append(
        {"key": SOURCE_DEMO, "label": "Demo tickets", "sub": "no tracker needed — try the flow with sample tickets"}
    )
    return options


def source_hint() -> str:
    """The line under the source picker, which depends on what is configured."""
    configured = available_sources()
    if len(configured) > 1:
        return "Both boards are configured — pick which one this session estimates against."
    if not configured:
        return "No tracker configured — add Jira or Azure DevOps credentials in Settings."
    return ""


def scope_options() -> list[dict]:
    """Sprint or backlog. Only asked of a real tracker."""
    return [
        {"key": SCOPE_SPRINT, "label": "A sprint", "sub": "pick one from the board's sprint list"},
        {"key": SCOPE_BACKLOG, "label": "The backlog", "sub": "open items not in any sprint"},
    ]


def sprint_options(sprints: list[dict]) -> list[dict]:
    """One row per sprint, subtitled with its dates and state."""
    return [
        {
            "key": sprint.get("name", "?"),
            "label": sprint.get("name", "?"),
            "sub": " · ".join(
                part for part in (sprint.get("start_date"), sprint.get("end_date"), sprint.get("state")) if part
            ),
        }
        for sprint in sprints
    ]


def default_sprint_index(sprints: list[dict]) -> int:
    """Which sprint to land the cursor on — the active one, else the last."""
    if not sprints:
        return 0
    for index, sprint in enumerate(sprints):
        if sprint.get("state") == "active":
            return index
    return len(sprints) - 1


def type_options(source: str) -> list[dict]:
    """The ticket-type toggles for one source, pre-checked to its defaults."""
    sublabels = _TYPE_SUBLABELS.get(source, _TYPE_SUBLABELS["default"])
    defaults = default_include_types(source)
    return [
        {
            "key": kind,
            "label": TICKET_TYPE_LABELS[kind],
            "sub": sublabels[kind],
            "checked": kind in defaults,
        }
        for kind in TICKET_TYPES
    ]


def type_hint(source: str) -> str:
    """The line under the type toggle — Jira's sub-tasks are never included."""
    if source == SOURCE_JIRA:
        return "Space toggles · Sub-tasks are never included."
    return "Space toggles · pick the work-item types to estimate."


def step_applies(step: str, *, source: str, scope: str = "") -> bool:
    """Whether *step* is asked, given the answers so far.

    Demo skips everything after the source — it has no board to scope against
    and no type mapping to apply. A real tracker's sprint list is only asked for
    when the scope is a sprint.
    """
    if step == "source":
        return True
    if source == SOURCE_DEMO:
        return False
    if step == "sprint":
        return scope == SCOPE_SPRINT
    return step in ("scope", "types")


def steps_for(*, source: str, scope: str = "") -> tuple[str, ...]:
    """The steps this configuration actually asks, in order."""
    return tuple(step for step in STEPS if step_applies(step, source=source, scope=scope))


def scope_label_for(*, source: str, scope: str = "", sprint: dict | None = None) -> str:
    """What to call this session's scope on the board and in its history."""
    if source == SOURCE_DEMO:
        return DEMO_SCOPE_LABEL
    if scope == SCOPE_SPRINT:
        return (sprint or {}).get("name") or "Sprint"
    return BACKLOG_SCOPE_LABEL


def include_types_for(source: str, selected: list[str] | tuple[str, ...] | None) -> tuple[str, ...] | None:
    """Normalise a type selection to what ``fetch_tickets`` expects.

    ``None`` means "the source's own default", which is what a demo session and
    an unanswered toggle both mean.
    """
    if source == SOURCE_DEMO or selected is None:
        return None
    chosen = tuple(kind for kind in TICKET_TYPES if kind in set(selected))
    return chosen or None


def empty_result_message(source: str, scope_label: str) -> str:
    """What to say when the tracker answered with nothing."""
    return f"{source_label(source)} returned nothing for {scope_label} — check credentials (see logs)."
