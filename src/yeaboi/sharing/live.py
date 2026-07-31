"""Live board updates over HTTP long-polling, on the stdlib ``http.server``.

Replaces the boards' fixed 1200 ms poll. The browser issues
``GET /api/state?wait=25`` carrying the ETag it already holds; the server parks
the request on the board's :class:`~yeaboi.sharing.events.EventHub` and answers
the instant something changes, or returns ``304`` when the wait elapses. The
client re-issues immediately, so a board is effectively push-driven while an
idle one costs ~200 bytes per 25 s instead of ~40 KB per 1.2 s.

Why long-polling and not Server-Sent Events
-------------------------------------------
SSE was the original design and it does not work here. **A Cloudflare quick
tunnel buffers the entire response body until the origin completes it**, so a
stream that never ends delivers nothing at all — the browser sees the response
headers within ~0.1 s and then no body for as long as you care to wait.

That was established by controlled experiment against a live
``trycloudflare.com`` tunnel, not inferred:

==============================================  ==========================
variant                                         result
==============================================  ==========================
SSE, chunked, HTTP/2                            buffered until origin end
SSE, chunked, HTTP/1.1                          buffered until origin end
``text/plain``, chunked                         buffered until origin end
SSE, chunked, 8 KB first write                  buffered until origin end
SSE, close-delimited                            buffered until origin end
**SSE, chunked, direct to origin (control)**    **streamed, first byte 0.0 s**
==============================================  ==========================

So it is not the HTTP version, the content type, the transfer framing, or a
fill-the-buffer threshold — the edge simply will not forward a partial body.
``X-Accel-Buffering: no`` is an nginx-family convention that Cloudflare ignores.

Long-polling is immune by construction: every response is a *complete* HTTP
response, so a proxy that waits for completion flushes it as soon as it is
written. It also needs no ``EventSource``, survives corporate proxies and
TLS-interception antivirus, and keeps latency identical to SSE — the server
still wakes on the same 250 ms change watcher.

The one real constraint is hold time: it must stay comfortably under
Cloudflare's ~100 s origin-response limit, hence :data:`MAX_WAIT_SECONDS`.

# See docs: "Architecture" — the TUI/browser split
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from yeaboi.sharing.events import state_etag
from yeaboi.web.security import send_document, send_headers

if TYPE_CHECKING:  # pragma: no cover - typing only
    from http.server import BaseHTTPRequestHandler

    from yeaboi.sharing.events import EventHub

logger = logging.getLogger(__name__)

# Ceiling on how long one request may be parked. Well under Cloudflare's ~100 s
# origin-response limit and under the default timeout of every HTTP client
# involved, so a held request is never mistaken for a hung one.
MAX_WAIT_SECONDS = 25.0


def send_state(handler: BaseHTTPRequestHandler, snapshot: Mapping[str, object], etag: str, if_none_match: str) -> None:
    """Send ``snapshot`` as JSON, or a bare 304 when the client already has it."""
    # Both branches carry the shared header set from web/security.py. This is the
    # boards' busiest endpoint by a wide margin — every open board polls it
    # continuously — and it wrote its own headers for long enough that it was the
    # last place still sending a bare `Cache-Control: no-store`.
    if if_none_match and if_none_match == etag:
        # A 304 is defined to carry no body, so its framing needs no
        # Content-Length — HTTP/1.1 keep-alive stays intact without one.
        send_headers(handler, 304, extra=(("ETag", etag),))
        handler.end_headers()
        return
    body = json.dumps(snapshot).encode()
    send_document(handler, 200, body, "application/json", extra=(("ETag", etag),))


def serve_state(
    handler: BaseHTTPRequestHandler,
    hub: EventHub,
    build_snapshot: Callable[[], Mapping[str, object]],
    *,
    wait_seconds: float = 0.0,
    max_wait: float = MAX_WAIT_SECONDS,
) -> None:
    """Answer ``GET /api/state``, optionally parking the request until a change.

    With ``wait_seconds <= 0`` this is an ordinary conditional GET — the path a
    client takes on its first request, and the one every non-long-polling
    consumer keeps using.

    With a positive ``wait_seconds`` **and** a client whose ``If-None-Match``
    already matches current state, the request is held on ``hub`` until the
    board changes or the deadline passes. Holding only in that case is what
    makes this safe: a client that is behind gets answered immediately, so a
    slow or reconnecting peer never waits for a change it has already missed.

    The ETag doubles as the cursor, so there is no separate ``since=<revision>``
    parameter to keep in sync and no way for the two to disagree.
    """
    snapshot = build_snapshot()
    etag = state_etag(snapshot)
    if_none_match = handler.headers.get("If-None-Match", "")

    if wait_seconds <= 0 or if_none_match != etag:
        send_state(handler, snapshot, etag, if_none_match)
        return

    sub = hub.subscribe(handler.client_address[0])
    if sub is None:
        # Over a cap: answer 304 now rather than hold a slot. The client just
        # re-polls, which still works — only without the instant wake-up.
        logger.debug("live: hold refused (cap reached) — answering 304")
        send_state(handler, snapshot, etag, if_none_match)
        return

    try:
        deadline = time.monotonic() + min(wait_seconds, max_wait)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sub.wait(remaining)
            if sub.closed:  # the server is shutting down (EventHub.close)
                break
            # Re-derive rather than trusting the wake-up: the watcher publishes
            # on a coarse (revision, presence) probe, so being woken does not
            # prove THIS viewer's snapshot changed. Comparing the ETag is what
            # keeps a 200 meaning "your view really is different".
            snapshot = build_snapshot()
            etag = state_etag(snapshot)
            if etag != if_none_match:
                break
    finally:
        hub.unsubscribe(sub)

    send_state(handler, snapshot, etag, if_none_match)


def parse_wait(raw: str, *, max_wait: float = MAX_WAIT_SECONDS) -> float:
    """Parse a ``?wait=`` value to clamped seconds (0.0 when absent or junk)."""
    if not raw:
        return 0.0
    try:
        return max(0.0, min(float(raw), max_wait))
    except (TypeError, ValueError):
        return 0.0
