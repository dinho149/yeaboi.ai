"""Where webhook deliveries land: an additive table in the shared sessions DB.

The connector layer is fetch-on-demand everywhere else; a webhook is push, so
the receiver persists what arrives and the gather reads it back. The table
mirrors :class:`~yeaboi.ops.events.OpsEvent` field for field — nothing with a
body can be stored, because there is no column to put one in.

``UNIQUE(connector_key, delivery_hash)`` is the replay/retry dedupe: SaaS
senders retry on timeout, and an HMAC replay inside its window re-inserts the
same rows, which land on the constraint and change nothing.

Connections are opened per call: the receiver thread writes while a standup
gathers, and short-lived connections are the SQLite arrangement that never
holds a lock across the two.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from yeaboi.ops.events import OpsEvent

logger = logging.getLogger(__name__)

#: Deliveries older than this are pruned on insert — comfortably past the
#: longest window reporting asks for.
RETENTION_DAYS = 90

#: And a hard cap per connection, so one chatty sender cannot grow the DB.
MAX_ROWS_PER_CONNECTION = 5000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS webhook_events (
    connector_key TEXT NOT NULL,
    delivery_hash TEXT NOT NULL,
    row_index INTEGER NOT NULL,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    service TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL,
    UNIQUE(connector_key, delivery_hash, row_index)
);
CREATE INDEX IF NOT EXISTS idx_webhook_events_window
    ON webhook_events(connector_key, started_at);
"""


def _connect() -> sqlite3.Connection:
    from yeaboi.paths import get_db_path

    conn = sqlite3.connect(get_db_path())
    conn.executescript(_SCHEMA)
    return conn


def record_delivery(connector_key: str, delivery_hash: str, events: tuple[OpsEvent, ...]) -> int:
    """Persist one delivery's mapped events. Returns how many rows are new.

    A retried or replayed delivery re-presents the same hash and inserts
    nothing; pruning rides along so retention needs no scheduler.
    """
    if not events:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    with _connect() as conn:
        for index, event in enumerate(events):
            cursor = conn.execute(
                "INSERT OR IGNORE INTO webhook_events "
                "(connector_key, delivery_hash, row_index, kind, ref, title, service,"
                " severity, status, started_at, url, received_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    connector_key,
                    delivery_hash,
                    index,
                    event.kind,
                    event.ref,
                    event.title,
                    event.service,
                    event.severity,
                    event.status,
                    event.started_at,
                    event.url,
                    now,
                ),
            )
            inserted += cursor.rowcount
        horizon = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).isoformat()
        conn.execute("DELETE FROM webhook_events WHERE connector_key = ? AND received_at < ?", (connector_key, horizon))
        conn.execute(
            "DELETE FROM webhook_events WHERE connector_key = ? AND rowid NOT IN ("
            " SELECT rowid FROM webhook_events WHERE connector_key = ?"
            " ORDER BY received_at DESC LIMIT ?)",
            (connector_key, connector_key, MAX_ROWS_PER_CONNECTION),
        )
    logger.info("webhooks: %s stored %d new event(s)", connector_key, inserted)
    return inserted


def events_in_window(connector_key: str, window_start, window_end) -> tuple[OpsEvent, ...]:
    """The stored events for one connection — ``gather``'s read side.

    An event with no ``started_at`` is kept (the ops convention: undated events
    survive windowing) and ``gather``'s own ``within`` re-filter stays the one
    authority on the boundary.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT kind, ref, title, service, severity, status, started_at, url "
            "FROM webhook_events WHERE connector_key = ? ORDER BY started_at DESC LIMIT ?",
            (connector_key, MAX_ROWS_PER_CONNECTION),
        ).fetchall()
    return tuple(
        OpsEvent(
            kind=row[0],
            source=connector_key,
            ref=row[1],
            title=row[2],
            service=row[3],
            severity=row[4],
            status=row[5],
            started_at=row[6],
            url=row[7],
        )
        for row in rows
    )


def last_received_at(connector_key: str) -> str:
    """When the newest delivery landed, ISO UTC — ``""`` while still waiting."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(received_at) FROM webhook_events WHERE connector_key = ?", (connector_key,)
        ).fetchone()
    return str(row[0]) if row and row[0] else ""
