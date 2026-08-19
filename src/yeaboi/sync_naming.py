"""Board-aware sprint naming and numbering, shared by the Jira and AzDO syncs.

# See docs: "Scrum Standards" — sprint planning

The sync modules rename the plan's generic "Sprint N" artifacts to match the
board's existing convention ("PSOT Sprint 107") and pick the number the new
sprints should start at. Both derivations used to live inline in each sync
module and shared three defects:

- ``starting_sprint_number`` uses ``-1`` as the "no tracker sprint picked"
  sentinel, and a truthiness check let it through — boards got "Sprint -1",
  "Sprint 0", … The guard here is ``> 0``, matching every other consumer.
- The naming prefix was taken from whichever sprint name carried the highest
  trailing integer, so a stray "Hardening 2024" hijacked the convention from
  fifty "PSOT Sprint N" names. The prefix is now the *consensus* (most common)
  prefix among numbered names.
- A computed name could land on a *closed* sprint, which the reuse branch then
  targeted (Jira rejects adding issues to a completed sprint). Planned batches
  are shifted past the board's maximum instead.

This module is dependency-free on purpose: it is imported by both sync modules
and unit-tested without either SDK.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

# Non-greedy prefix + greedy trailing digit run: "PI 2024.3" → ("PI 2024.", 3),
# "PSOT Sprint 107" → ("PSOT Sprint ", 107). Names without a trailing number
# ("Hardening", "Sprint 42 - Alpha") do not participate in numbering.
_NUMBERED_NAME_RE = re.compile(r"^(.*?)(\d+)\s*$")


@dataclass(frozen=True)
class BoardNumbering:
    """The board's sprint-naming convention, derived from its existing sprints."""

    prefix: str = ""  # consensus prefix among numbered names ("" = none)
    max_number: int = 0  # highest number among consensus-prefix names
    closed_names: frozenset[str] = frozenset()


def derive_board_numbering(sprints: Iterable[tuple[str, str]]) -> BoardNumbering:
    """Derive the naming convention from (name, state) pairs of board sprints.

    The prefix is chosen by consensus — the most common prefix among names with
    a trailing number — so one oddly named sprint cannot hijack the convention.
    Ties break toward the prefix holding the highest number (the live sequence).
    """
    parsed: list[tuple[str, int, str]] = []  # (prefix, number, state)
    closed: set[str] = set()
    for name, state in sprints:
        if state == "closed":
            closed.add(name)
        match = _NUMBERED_NAME_RE.match(name or "")
        if match:
            parsed.append((match.group(1), int(match.group(2)), state))

    if not parsed:
        return BoardNumbering(closed_names=frozenset(closed))

    counts = Counter(prefix for prefix, _num, _state in parsed)
    max_by_prefix: dict[str, int] = {}
    for prefix, num, _state in parsed:
        max_by_prefix[prefix] = max(max_by_prefix.get(prefix, 0), num)
    consensus = max(counts, key=lambda p: (counts[p], max_by_prefix[p]))

    return BoardNumbering(
        prefix=consensus,
        max_number=max_by_prefix[consensus],
        closed_names=frozenset(closed),
    )


def resolve_starting_number(configured: int, numbering: BoardNumbering) -> int:
    """Pick the number the first new sprint should carry.

    A positive configured number (the user's intake pick) wins. Anything else —
    including the ``-1`` "no tracker" sentinel — falls through to one past the
    board's highest existing number, or 0 when the board has no numbered
    sprints (callers then keep the plan's generic names).
    """
    if configured > 0:
        return configured
    if numbering.max_number > 0:
        return numbering.max_number + 1
    return 0


def advance_past_closed(start: int, count: int, numbering: BoardNumbering) -> tuple[int, str]:
    """Shift a planned batch of sprint numbers past any closed sprint it would hit.

    Returns (starting_number, warning). The whole batch moves together so the
    sequence stays contiguous; the warning is empty when nothing moved.
    """
    if start <= 0 or not numbering.prefix or not numbering.closed_names:
        return start, ""
    planned = [f"{numbering.prefix}{n}" for n in range(start, start + count)]
    collisions = [name for name in planned if name in numbering.closed_names]
    if not collisions:
        return start, ""
    new_start = numbering.max_number + 1
    warning = (
        f"Planned sprint number(s) collide with closed sprint(s) ({', '.join(collisions)}) — "
        f"renumbered to start at {numbering.prefix}{new_start}."
    )
    return new_start, warning
