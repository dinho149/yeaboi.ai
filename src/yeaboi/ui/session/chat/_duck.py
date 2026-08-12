"""The planning chat's duck voice — a private arbiter for the corner duck.

The arbiter class itself lives in ui/shared/_duck_voice.py as
:class:`~yeaboi.ui.shared._duck_voice.DuckVoice`, because every page now
speaks through one; the chat keeps its own instance (stamped through its
reading-column fence in the driver) plus the intake-specific quip table
below. ``ChatDuck`` is the historical name — same class.
"""

from __future__ import annotations

from yeaboi.ui.shared._duck_voice import (
    COACH_HOLD,
    PRIORITY_COACH,
    PRIORITY_EVENT,
    DuckVoice,
)

ChatDuck = DuckVoice

# What the duck quacks at each intake phase boundary. Deliberately a few words
# — NOT the full-sentence PHASE_INTROS, which the node already prepends into
# the transcript; the bubble is a nudge, not a second narrator.
PHASE_QUIPS: dict[str, str] = {
    "project_context": "First: the project itself.",
    "team_and_capacity": "Now — who's building it?",
    "technical_context": "Tech stack time.",
    "codebase_context": "Let's peek at the code.",
    "risks_and_unknowns": "What could go wrong?",
    "preferences": "How do you like to work?",
    "capacity_planning": "Last stretch: capacity.",
}

# What the duck says while a turn is grinding — rotated by the driver's
# _entertain_duck every few seconds so a long wait reads as showtime, not a
# stall. Same ≤40-char bubble budget as DUCK_QUIPS (tested).
WORKING_QUIPS: tuple[str, ...] = (
    "Crunching the numbers…",
    "Paddling hard below the surface…",
    "Untangling the backlog…",
    "Sharpening story points…",
    "Consulting the rubber-duck council…",
    "Herding user stories…",
    "Negotiating with the sprint gods…",
    "Stacking epics very carefully…",
    "Waterproofing the plan…",
    "Still quacking along…",
)

__all__ = [
    "COACH_HOLD",
    "PRIORITY_COACH",
    "PRIORITY_EVENT",
    "ChatDuck",
    "PHASE_QUIPS",
    "WORKING_QUIPS",
]
