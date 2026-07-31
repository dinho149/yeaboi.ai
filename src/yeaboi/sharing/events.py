"""Change notification for the live boards — the server half of Server-Sent Events.

Three small pieces, deliberately independent of both boards so retro and poker
share one implementation (the same pattern as :mod:`yeaboi.sharing.access`):

* :class:`EventHub` — a fan-out registry of connected browsers. Publishing wakes
  every subscriber and carries **no payload**, because each browser's frame is
  different: retro computes ``mine`` per participant, poker computes
  ``mine_value`` / ``mine_role``. The stream thread builds its own snapshot once
  woken.
* :class:`ChangeWatcher` — polls a caller-supplied *probe* every 250 ms and
  publishes whenever its value changes.
* :func:`state_etag` — the same change detection expressed as an HTTP ETag, so
  the polling fallback also stops re-sending identical state.

Why a watcher thread rather than having the boards call ``hub.publish()`` on
every mutation:

1. The boards stay untouched, so this lands as a purely additive change.
2. It catches mutations that never go through an HTTP request at all — retro's
   ``add_ai_cards``, the poker AI worker's ``set_ai_note``, the transcription
   worker's ``set_duel_transcript``.
3. It catches **presence**, which deliberately does NOT bump ``revision``
   (see :meth:`yeaboi.retro.board.RetroBoard.heartbeat` — heartbeats fire ~1/s
   and bumping would defeat change detection). Miss that and the who's-here row
   would only refresh when something unrelated happened to change.

The cost is up to 250 ms of added latency, still ~5x better than the 1200 ms
poll it replaces.

# See docs: "Guardrails" — token gating / resource caps
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable, Mapping

logger = logging.getLogger(__name__)

# Every open stream pins one handler thread for its whole life, so both caps are
# about bounding threads, not about trust. Refusing is safe: a client that is
# turned away falls back to polling, which keeps working.
MAX_STREAMS = 64  # global
MAX_PER_IP = 4  # a laptop + a phone + a couple of spare tabs

WATCH_INTERVAL = 0.25  # seconds between change probes

_UNSET = object()  # "no probe reading yet" — distinct from any real probe value


class Subscription:
    """One connected browser's wake-up channel.

    Wraps a :class:`threading.Event` rather than a queue because notifications
    carry no payload and repeated changes should **coalesce**: if three cards
    are added while a frame is still being written, the stream should send one
    fresh snapshot afterwards, not three. ``Event.set()`` on an already-set
    event is a no-op, which gives that behaviour for free.
    """

    __slots__ = ("_wake", "closed", "ip")

    def __init__(self, ip: str = "") -> None:
        self.ip = ip
        self.closed = False
        self._wake = threading.Event()

    def notify(self) -> None:
        """Wake the stream so it sends a fresh frame."""
        self._wake.set()

    def close(self) -> None:
        """Mark the stream dead and wake it so its loop can exit promptly."""
        self.closed = True
        self._wake.set()

    def wait(self, timeout: float) -> bool:
        """Block up to ``timeout`` seconds. True = notified, False = timed out.

        Callers must check :attr:`closed` afterwards, since :meth:`close` also
        wakes the event — "notified" and "still alive" are separate questions.
        """
        notified = self._wake.wait(timeout)
        self._wake.clear()
        return notified


class EventHub:
    """Fan-out registry of the live SSE subscribers for one board."""

    def __init__(self, *, max_streams: int = MAX_STREAMS, max_per_ip: int = MAX_PER_IP) -> None:
        self._lock = threading.Lock()
        self._subs: list[Subscription] = []
        self._closed = False
        self._max_streams = max_streams
        self._max_per_ip = max_per_ip

    @property
    def subscriber_count(self) -> int:
        """Number of streams currently open."""
        with self._lock:
            return len(self._subs)

    def subscribe(self, ip: str = "") -> Subscription | None:
        """Register a stream, or return ``None`` when a cap is hit (caller sends 503)."""
        with self._lock:
            if self._closed:
                return None
            if len(self._subs) >= self._max_streams:
                logger.warning("live: global hold cap (%d) reached — refusing subscribe", self._max_streams)
                return None
            if ip and sum(1 for s in self._subs if s.ip == ip) >= self._max_per_ip:
                logger.warning("live: per-IP hold cap (%d) reached — refusing subscribe", self._max_per_ip)
                return None
            sub = Subscription(ip)
            self._subs.append(sub)
            count = len(self._subs)
        logger.info("live: request held (%d parked)", count)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        """Retire a stream. Idempotent — the handler's ``finally`` may double-call."""
        with self._lock:
            try:
                self._subs.remove(sub)
            except ValueError:
                return
            count = len(self._subs)
        sub.close()
        logger.info("live: hold released (%d parked)", count)

    def publish(self) -> None:
        """Wake every subscriber. Cheap and non-blocking — no payload is built here."""
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            sub.notify()

    def close(self) -> None:
        """Wake and retire every stream. Call BEFORE ``httpd.shutdown()``.

        ``daemon_threads = True`` on both servers means ``shutdown()`` never
        joins handler threads, so a stream parked in its 15 s heartbeat wait
        would otherwise sit there until the process exits. Waking it is what
        lets the loop notice :attr:`Subscription.closed` and unwind.
        """
        with self._lock:
            subs, self._subs, self._closed = list(self._subs), [], True
        for sub in subs:
            sub.close()
        if subs:
            logger.info("live: released %d parked request(s)", len(subs))


class ChangeWatcher:
    """Publishes on ``hub`` whenever ``probe()`` returns a different value.

    ``probe`` may return anything comparable with ``!=`` — the servers return a
    tuple of ``(revision, presence, typing)``. Exceptions are logged and the
    watcher keeps going: a transient board error must not silently kill live
    updates for everyone.
    """

    def __init__(
        self,
        hub: EventHub,
        probe: Callable[[], object],
        *,
        interval: float | None = None,
        name: str = "live-watch",
    ) -> None:
        self._hub = hub
        self._probe = probe
        # Resolved here rather than as a default argument so a test can shrink
        # the module constant and have it apply to servers built afterwards —
        # a default is bound once at def time and would ignore the patch.
        self._interval = WATCH_INTERVAL if interval is None else interval
        self._name = name
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._previous: object = _UNSET

    def start(self) -> None:
        """Begin watching on a daemon thread. Idempotent."""
        if self._thread is not None:
            return
        self._stop.clear()
        # Seed synchronously, BEFORE the thread exists. Seeding on the first tick
        # instead would blind the watcher for a whole interval: a change landing
        # in that window becomes the baseline and never publishes. Doing it here
        # means every change after start() returns is guaranteed to be seen.
        self._previous = self._read()
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()
        logger.info("live: change watcher started (%s, %.0f ms)", self._name, self._interval * 1000)

    def _read(self) -> object:
        """Probe once; ``_UNSET`` when it raised (treated as "no reading")."""
        try:
            return self._probe()
        except Exception:
            logger.exception("live: change probe failed — watcher continuing")
            return _UNSET

    def _run(self) -> None:
        # Event.wait() as the sleep, so stop() interrupts immediately instead of
        # leaving shutdown to block for up to one interval.
        while not self._stop.wait(self._interval):
            current = self._read()
            if current is _UNSET:
                continue  # probe failed — keep the old baseline and retry
            if self._previous is _UNSET:
                self._previous = current  # the seed itself failed; seed now
                continue
            if current != self._previous:
                self._previous = current
                self._hub.publish()

    def stop(self) -> None:
        """Stop watching and join the thread. Safe to call when never started."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
            logger.info("live: change watcher stopped (%s)", self._name)


def state_etag(snapshot: Mapping[str, object]) -> str:
    """Return a weak ETag for a board snapshot, ignoring the per-request clock.

    ``state_snapshot()`` embeds ``timer.now_epoch`` (``time.time()`` at request
    time), so a byte-level ETag over the response would never match and the
    header would be pure overhead. Clients derive a clock offset once and tick
    the countdown locally, so a stale ``now_epoch`` costs nothing — which is
    what makes it safe to hash everything *except* that field.

    The tag is weak (``W/``) precisely because it asserts semantic equivalence
    rather than byte equality.
    """
    timer = snapshot.get("timer")
    if isinstance(timer, Mapping) and "now_epoch" in timer:
        stable: Mapping[str, object] = {**snapshot, "timer": {k: v for k, v in timer.items() if k != "now_epoch"}}
    else:
        stable = snapshot
    # sort_keys so an unordered dict rebuild cannot flip the tag; default=str so
    # an unexpected value type degrades to a changed tag rather than a 500.
    raw = json.dumps(stable, sort_keys=True, default=str).encode()
    return f'W/"{hashlib.sha256(raw).hexdigest()[:20]}"'
