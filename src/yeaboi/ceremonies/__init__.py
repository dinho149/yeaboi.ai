"""Ceremony cadence — the team's recurring runs, fired by the OS.

yeaboi is a Scrum Master that, until this package, only existed while a terminal
was open. Exactly one ceremony had a cadence (the daily standup); every other
mode ran when a human remembered to open the TUI and click it — even though each
one already had a headless engine behind it.

This package is the clock, the declaration, and the record:

- ``catalog`` — which modes may be scheduled at all, and how to call each one.
  The admission test lives in code rather than in prose: a mode not in the
  catalog cannot be scheduled from any surface.
- ``store`` — the declared ceremonies and the run ledger, in ``sessions.db``.
- ``scheduler`` — the launchd/crontab job installer (promoted out of
  ``standup/scheduler.py``, which is now a shim over it).
- ``renderers`` — one function per mode turning its artifact into a
  ``Dispatch``, the mode-neutral payload every delivery channel accepts.
- ``runner`` / ``engine`` — firing one ceremony, guarding it, delivering it, and
  writing the ledger row whatever happened.

# See docs: "Architecture" — the four layers; ceremonies sit above the engines
"""

from yeaboi.ceremonies.catalog import CeremonyMode, CeremonyParam, lookup, schedulable_modes
from yeaboi.ceremonies.store import CeremonyStore, valid_name

__all__ = [
    "CeremonyMode",
    "CeremonyParam",
    "CeremonyStore",
    "lookup",
    "schedulable_modes",
    "valid_name",
]
