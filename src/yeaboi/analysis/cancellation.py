"""Cooperative cancellation for the team-analysis pipeline.

Lives in its own module (not ``engine.py``) so the fetch/scan layers
(``tools/team_learning.py``, ``analysis/ai_usage.py``) can raise and check the
cancel seam without importing the engine — the engine imports *them*, so the
reverse import would be a cycle.
"""

from __future__ import annotations

import threading


class AnalysisCancelledError(RuntimeError):
    """A caller's ``cancel_event`` aborted the run before anything was saved.

    Raised (not returned) so the TUI worker's except-chain can distinguish
    cancellation from real failures; nothing is persisted once it fires."""


def raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    """Raise ``AnalysisCancelledError`` if the injected cancel seam is set.

    Call between units of work (stories, repos, sprints) — never mid-HTTP-call;
    an in-flight request finishes and its result is simply discarded upstream.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise AnalysisCancelledError("Analysis cancelled")
