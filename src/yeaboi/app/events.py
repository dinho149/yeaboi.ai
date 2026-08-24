"""The ambient event bus — one SSE feed for everything that isn't a response.

Consent requests, board/tunnel lifecycle, run-id announcements, ceremony
outcomes, desktop notifications: anything the backend needs to *tell* the
shell (rather than answer it) is published here and drained by the single
``GET /api/events`` connection. Request-scoped streams (chat tokens, engine
progress for a call the client is awaiting) are NOT this — they ride the
chunked NDJSON body of their own request.

Distinct from ``sharing/events.py`` on purpose: that hub serves remote
browsers through a Cloudflare tunnel and therefore long-polls; this one serves
exactly one local process, so a plain server-sent-event stream is correct.

Delivery is best-effort with a bounded queue: a subscriber that stops reading
loses oldest-first rather than wedging publishers. The stream writes a comment
ping every ``PING_SECONDS`` so a dead peer is discovered by the broken pipe,
and the generator's ``finally`` unsubscribes — a killed Electron cannot leak
handler threads.
"""

from __future__ import annotations

import itertools
import json
import logging
import queue
import threading
import time
from collections.abc import Iterator

logger = logging.getLogger(__name__)

#: Comment-ping cadence; also the poll timeout that paces the stream loop.
PING_SECONDS = 15.0

#: Bounded per-subscriber buffer; overflow drops oldest.
MAX_QUEUED_EVENTS = 256

#: The shell opens one feed; a handful allows a dev tool alongside it.
MAX_SUBSCRIBERS = 8


class EventBus:
    """Publish/subscribe with SSE framing. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue[dict]] = []
        self._seq = itertools.count(1)

    def publish(self, type_: str, **fields: object) -> dict:
        """Publish one event; returns it (with ``seq`` and ``ts`` stamped)."""
        event = {"type": type_, "seq": next(self._seq), "ts": time.time(), **fields}
        with self._lock:
            targets = list(self._subscribers)
        for q in targets:
            try:
                q.put_nowait(event)
            except queue.Full:
                try:  # drop the oldest so the feed stays live, not exact
                    q.get_nowait()
                except queue.Empty:  # pragma: no cover - racing consumer
                    pass
                try:
                    q.put_nowait(event)
                except queue.Full:  # pragma: no cover - racing publisher
                    pass
        logger.debug("event published: type=%s seq=%d subscribers=%d", type_, event["seq"], len(targets))
        return event

    def subscribe(self) -> queue.Queue[dict]:
        with self._lock:
            if len(self._subscribers) >= MAX_SUBSCRIBERS:
                raise RuntimeError("too many event subscribers")
            q: queue.Queue[dict] = queue.Queue(maxsize=MAX_QUEUED_EVENTS)
            self._subscribers.append(q)
        logger.info("event subscriber attached (%d active)", self.subscriber_count)
        return q

    def unsubscribe(self, q: queue.Queue[dict]) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)
        logger.info("event subscriber detached (%d active)", self.subscriber_count)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def sse_stream(self, *, ping_seconds: float = PING_SECONDS) -> Iterator[bytes]:
        """A server-sent-events byte stream over a fresh subscription.

        Yields ``data:`` frames for events and ``: ping`` comments when idle.
        The subscription is torn down in ``finally`` however the consumer
        stops — close, broken pipe, or GeneratorExit.

        Subscribing happens here rather than on first iteration, so a refused
        subscription is an error the caller can still answer with a status
        code; inside the generator it would land after ``200 OK`` and read as
        a stream that simply never says anything.
        """
        return self._frames(self.subscribe(), ping_seconds)

    def _frames(self, q: queue.Queue[dict], ping_seconds: float) -> Iterator[bytes]:
        try:
            yield b": connected\n\n"
            while True:
                try:
                    event = q.get(timeout=ping_seconds)
                except queue.Empty:
                    yield b": ping\n\n"
                    continue
                payload = json.dumps(event, separators=(",", ":"), sort_keys=True, default=str)
                yield f"data: {payload}\n\n".encode()
        finally:
            self.unsubscribe(q)
