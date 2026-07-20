"""Typed errors for the Daily Standup subsystem.

The recent-activity helpers normally degrade to ``[]`` on failure so a standup
never crashes. But an *authentication* failure (401/403) is different: a silent
empty result looks identical to "no activity", hiding a misconfigured token from
the user. So the helpers raise ``StandupSourceError`` on auth failures; the
collector catches it and records a warning that ends up on the StandupReport.

# See docs: "Daily Standup" — recent-activity collection, warnings
"""

from __future__ import annotations


class StandupSourceError(Exception):
    """An activity source failed in a way the user must see (e.g. auth 401/403).

    Attributes:
        source: the source identifier (e.g. "jira", "github").
        message: a short, user-facing explanation.
    """

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source
        self.message = message
