"""Niko — yeaboi's global assistant.

A read-only conversation over every yeaboi surface: it answers questions about
what yeaboi does, reads the user's own delivery and agent data, and points at
the screen that does the thing. It never changes anything.

Public API:
    ask()          — one turn (yeaboi.niko.engine)
    NikoStore      — conversation persistence
    for_route()    — the chips a screen offers before anything is typed
"""

from yeaboi.niko.engine import ask
from yeaboi.niko.store import NikoStore
from yeaboi.niko.suggestions import for_route

__all__ = ["NikoStore", "ask", "for_route"]
