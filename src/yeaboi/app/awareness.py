"""Awareness — what the duck is told about while nobody is looking.

The renderer's duck already speaks for the things you just did and are watching
happen: a run's own stream says when it finished, and the page quips off that.
This is the other half — things that happen with the window closed, or on
another screen, or at 06:00 while the machine is asleep to everyone but launchd.

Two of those exist today and both are polled rather than pushed, because
neither happens in this process: a ceremony fires from an OS job and records a
row, and a ship run reaches its approval gate on a worker thread that writes to
the store. The watcher reads both, notices what is new since the last look, and
publishes a :data:`NOTICES` line on the ambient feed. The desktop shell forwards
it to the pet.

The rule the vocabulary encodes: only one of these is ``sticky``. A ceremony
that fired is news; a gate waiting for an approval is a question, and a question
that fades out unanswered is worse than one that was never asked.

Nothing here reaches for an LLM, and nothing here is a notification the user
cannot dismiss by ignoring it.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: How often the two sources are read. A ceremony is a once-a-day event and a
#: gate waits for minutes, so this is deliberately unhurried.
POLL_SECONDS = 20.0


@dataclass(frozen=True)
class Notice:
    """One thing the duck may announce, and where clicking it should land."""

    kind: str
    quip: str
    #: Stays up until it is answered, rather than fading with the others.
    sticky: bool = False
    #: The desktop route that answers the notice.
    route: str = ""


NOTICES: dict[str, Notice] = {
    "ceremony_ran": Notice("ceremony_ran", "A ceremony fired!", route="/ceremonies"),
    "ceremony_failed": Notice("ceremony_failed", "A ceremony went wrong.", route="/ceremonies"),
    "ship_gate": Notice("ship_gate", "A diff needs you.", sticky=True, route="/humans/ship/run"),
}


class AwarenessWatcher:
    """Polls the out-of-window sources and publishes what changed."""

    def __init__(self, bus, *, ships=None, poll_seconds: float = POLL_SECONDS, db_path=None) -> None:
        self._bus = bus
        self._ships = ships
        self._poll_seconds = poll_seconds
        self._db_path = db_path
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: The newest ceremony run already announced, so a restart of the loop
        #: does not re-announce this morning's standup.
        self._last_run: tuple[str, str] | None = None
        self._announced_gates: set[str] = set()
        self._primed = False

    # ── lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="app-awareness", daemon=True)
        self._thread.start()
        logger.info("awareness watcher started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        # The first pass runs immediately and only records where things stand:
        # everything already in the store happened before the app opened, and
        # announcing it would greet every launch with a week of stale news.
        self._safe_poll()
        while not self._stop.wait(self._poll_seconds):
            self._safe_poll()

    def _safe_poll(self) -> None:
        try:
            self.poll()
        except Exception:  # noqa: BLE001 - awareness must never take the backend down
            logger.warning("awareness poll failed", exc_info=True)

    # ── the sources ────────────────────────────────────────────────────

    def poll(self) -> list[dict]:
        """Read both sources once and publish what is new. Returns the events."""
        events = [*self._poll_ceremonies(), *self._poll_gates()]
        self._primed = True
        return events

    def _poll_ceremonies(self) -> list[dict]:
        from yeaboi.ceremonies.setup import current_session
        from yeaboi.ceremonies.store import CeremonyStore

        session_id = current_session()
        if not session_id:
            return []
        with CeremonyStore(self._db_path) as store:
            runs = store.runs(session_id, limit=1)
        if not runs:
            return []
        newest = runs[0]
        marker = (newest.ceremony, newest.fired_at)
        if marker == self._last_run:
            return []
        self._last_run = marker
        if not self._primed:
            return []  # first look: learn where we are, announce nothing
        kind = "ceremony_ran" if newest.outcome == "ok" else "ceremony_failed"
        return [
            self._announce(
                kind,
                ceremony=newest.ceremony,
                outcome=newest.outcome,
                scheduled=newest.scheduled,
                detail=newest.detail or newest.error,
            )
        ]

    def _poll_gates(self) -> list[dict]:
        if self._ships is None:
            return []
        events = []
        open_now = set()
        for run in self._ships.runs():
            if not run.get("gate"):
                continue
            key = str(run.get("key", ""))
            open_now.add(key)
            if key in self._announced_gates:
                continue
            self._announced_gates.add(key)
            if not self._primed:
                continue  # a gate that was already open is not news
            events.append(self._announce("ship_gate", key=key, story=run.get("story_title", "")))
        # A gate that closed may legitimately open again on the next run.
        self._announced_gates &= open_now
        return events

    def _announce(self, kind: str, **fields: object) -> dict:
        notice = NOTICES[kind]
        logger.info("awareness: %s (%s)", kind, ", ".join(f"{k}={v}" for k, v in fields.items()))
        return self._bus.publish(
            "notice", kind=kind, quip=notice.quip, sticky=notice.sticky, route=notice.route, **fields
        )
