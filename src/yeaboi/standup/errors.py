"""Typed errors for the Daily Standup subsystem.

The recent-activity helpers normally degrade to ``[]`` on failure so a standup
never crashes. But a failure the user must ACT on is different: a silent empty
result looks identical to "no activity", hiding a misconfigured token or an
inaccessible repository. So the helpers raise ``StandupSourceError`` for those —
401/403 (bad credentials, missing scope, SSO), 404 (renamed, deleted, or invisible
to this token) and rate limiting — and the collector catches it per source and
records a warning that ends up on the StandupReport.

# See docs: "Daily Standup" — recent-activity collection, warnings
"""

from __future__ import annotations


class StandupSourceError(Exception):
    """An activity source failed in a way the user must see (auth, access, or rate limit).

    Attributes:
        source: the source identifier (e.g. "jira", "github").
        message: a short, user-facing explanation.
    """

    def __init__(self, source: str, message: str) -> None:
        super().__init__(f"{source}: {message}")
        self.source = source
        self.message = message
