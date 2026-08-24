"""The filesystem-consent desk — sandbox denials, asked and answered over the wire.

# See docs: "Guardrails" — human-in-the-loop; this is the desktop consent surface
# for the filesystem sandbox (src/yeaboi/fs_policy.py).

``fs_policy`` in interactive mode queues a :class:`~yeaboi.fs_policy.ConsentRequest`
for every denial instead of failing forever. The TUI pops that queue between
graph turns and shows a popup. Here there is no turn to be between: a denial can
come from a tool call, from a native route, from a board thread or from a run
that is streaming NDJSON at the time. So the desk polls the queue on its own
thread and publishes each request on the ambient event feed; the shell shows a
modal, and the answer comes back through :meth:`ConsentDesk.resolve`.

The raise still happens — the access that triggered the request has already
failed. Consent is for the retry, exactly as in the TUI.

Requests are kept only while they matter: :data:`MAX_OPEN` bounds the table so a
loop denying the same path under different contexts cannot grow it without end.
"""

from __future__ import annotations

import itertools
import logging
import threading

from yeaboi.fs_policy import CONSENT_CHOICES, ConsentRequest, apply_consent

logger = logging.getLogger(__name__)

#: How often the denial queue is drained. Fast enough that a modal appears
#: while the person still remembers what they clicked, cheap enough to ignore.
POLL_SECONDS = 0.5

#: Ceiling on open requests. Oldest-first eviction: an unanswered request is
#: only a modal nobody answered, and the access it guarded already failed.
MAX_OPEN = 32


class ConsentDesk:
    """Turns queued sandbox denials into ambient events, and answers into grants."""

    def __init__(self, bus, *, poll_seconds: float = POLL_SECONDS) -> None:
        self._bus = bus
        self._poll_seconds = poll_seconds
        self._lock = threading.Lock()
        self._open: dict[str, ConsentRequest] = {}
        self._ids = itertools.count(1)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="app-consent", daemon=True)
        self._thread.start()
        logger.info("consent desk watching for sandbox denials")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(self._poll_seconds):
            try:
                self.drain()
            except Exception:  # noqa: BLE001 - a desk that dies takes consent with it
                logger.warning("consent desk drain failed", exc_info=True)

    # ── the queue ──────────────────────────────────────────────────────

    def drain(self) -> list[dict]:
        """Publish every queued denial as a ``consent_request`` event.

        Returns the events published, so a test (and the poll loop) can see
        what happened without subscribing to the bus.
        """
        from yeaboi.fs_policy import pop_pending_denials

        published = []
        for req in pop_pending_denials():
            req_id = f"fs-{next(self._ids)}"
            with self._lock:
                self._open[req_id] = req
                while len(self._open) > MAX_OPEN:
                    evicted = next(iter(self._open))  # dicts keep insertion order
                    del self._open[evicted]
                    logger.info("consent request %s evicted unanswered", evicted)
            fields = {
                "req_id": req_id,
                "path": str(req.path),
                "mode": req.mode,
                "context": req.context,
                "choices": list(CONSENT_CHOICES),
            }
            logger.info("consent requested: %s %s (%s)", req.mode, req.path, req.context or "-")
            published.append(self._bus.publish("consent_request", **fields))
        return published

    def open_requests(self) -> list[dict]:
        """Everything still waiting on an answer, oldest first."""
        with self._lock:
            return [
                {"req_id": req_id, "path": str(req.path), "mode": req.mode, "context": req.context}
                for req_id, req in self._open.items()
            ]

    def resolve(self, req_id: str, choice: str) -> bool:
        """Apply one answer. Returns whether access was granted.

        Raises :class:`KeyError` for an unknown id — an answer to a request
        nobody is holding is a bug in the caller, not a denial.
        """
        with self._lock:
            req = self._open.pop(req_id, None)
        if req is None:
            raise KeyError(req_id)
        granted = apply_consent(choice, req)
        self._bus.publish("consent_resolved", req_id=req_id, path=str(req.path), choice=choice, granted=granted)
        return granted
