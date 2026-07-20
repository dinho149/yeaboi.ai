"""Section navigation for the setup wizard's step state machine.

# See docs: "Architecture" — the setup wizard (``select_provider``) is a step
# state machine that, by default, runs LLM Provider → Issue Tracking → Docs →
# Version Control in order. This module lets the user move between those sections
# with the ← / → arrow keys (a "tab bar" over the progress chips) and finish from
# anywhere with F, instead of only advancing linearly.
#
# The mechanism is a small sentinel: a phase's picker loop calls ``nav_for_key``
# on each keypress and, when it returns a ``StepNav``, hands that back to the main
# loop instead of a result dict. The main loop then jumps to ``target`` (or
# returns the collected config when ``finish`` is set). The LLM step stays a
# required gate — the master config dict is seeded from the provider + key — so
# free movement is offered only once the LLM step is done.
"""

from __future__ import annotations

# Step indices — mirror ``_STEPS`` in ``screens/_screens.py``.
_STEP_LLM = 0
_STEP_ISSUE_TRACKING = 1
_STEP_DOCS = 2
_STEP_VERSION_CONTROL = 3
_LAST_STEP = _STEP_VERSION_CONTROL


class StepNav:
    """A phase returns this (instead of its result dict) when the user pressed a
    section-switch key. ``target`` is the step index to jump to; ``finish`` is
    True when the user asked to finish the wizard from the current section.
    """

    __slots__ = ("target", "finish")

    def __init__(self, target: int | None = None, *, finish: bool = False):
        self.target = target
        self.finish = finish

    def __eq__(self, other: object) -> bool:
        # Value equality keeps the unit tests (and any de-dup) straightforward.
        return isinstance(other, StepNav) and other.target == self.target and other.finish == self.finish

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"StepNav(target={self.target}, finish={self.finish})"


def nav_for_key(key: str, current_step: int) -> StepNav | None:
    """Map a raw keypress to a section-navigation intent, or ``None``.

    - ``F`` → finish the wizard from here (``StepNav(finish=True)``).
    - ``←`` → previous chip, clamped so it never goes below ``_STEP_LLM``.
    - ``→`` → next chip, clamped so it never goes past ``_LAST_STEP``.

    Any other key (or an arrow at the clamp boundary) returns ``None`` so the
    caller falls through to its own ↑/↓/Enter/Esc/typing handling.
    """
    if key in ("f", "F"):
        return StepNav(finish=True)
    if key == "left" and current_step > _STEP_LLM:
        return StepNav(target=current_step - 1)
    if key == "right" and current_step < _LAST_STEP:
        return StepNav(target=current_step + 1)
    return None
