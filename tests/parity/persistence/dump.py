"""Canonical database dumper for the W9 persistence parity gate.

``migrate_and_dump(path)`` opens the database through the real
``SessionStore`` — the reference implementation whose open semantics and
ladder the Go port must reproduce — records what the open reported
(``schema_mismatch``, the stamped version), then dumps every table.

The dump is deliberately *not* ``sqlite_master.sql`` text: CPython's SQLite
and modernc.org/sqlite may normalise stored DDL differently, so the canonical
form is the ``PRAGMA table_info`` projection plus all rows in rowid order,
with index *names* (never their SQL) from ``PRAGMA index_list``.
``sqlite_sequence`` is included deliberately — the v11 seeding writes it, and
its counters are part of what both sides must agree on.

Number formatting is Python's ``json.dumps``: a REAL 3.0 serialises as
``3.0``, never ``3`` — the same widening rule the RPC contract's rule 13
pins, and the reason the gate compares exact bytes as well as values.

Runnable as a script (W9 phase 2's Go arm and the final E2E drive it the
same way): ``python dump.py <db-path>`` migrates the database IN PLACE and
prints the canonical JSON on stdout.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def canonical_dump(conn: sqlite3.Connection) -> dict:
    """Every table's schema projection, index names, and rows.

    Tables in name order; rows in rowid order (``sqlite_sequence`` has no
    reliable rowid semantics across engines, so it is ordered by name).
    """
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'sqlite_sequence' ORDER BY name"
        )
    ]
    has_sequence = conn.execute("SELECT name FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone()
    dump: dict = {}
    for table in tables:
        columns = [list(row) for row in conn.execute(f"PRAGMA table_info({table})")]
        indexes = sorted(row[1] for row in conn.execute(f"PRAGMA index_list({table})"))
        rows = [[_value(v) for v in row] for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        dump[table] = {"columns": columns, "indexes": indexes, "rows": rows}
    if has_sequence:
        rows = [[_value(v) for v in row] for row in conn.execute("SELECT * FROM sqlite_sequence ORDER BY name")]
        dump["sqlite_sequence"] = {"columns": [], "indexes": [], "rows": rows}
    return dump


def _value(v):
    """JSON-safe cell value; BLOBs become a tagged hex object."""
    if isinstance(v, bytes):
        return {"__blob__": v.hex()}
    return v


def dump_db(path: Path) -> dict:
    """Canonical dump of a database file, without opening it as a store."""
    conn = sqlite3.connect(str(path))
    try:
        return canonical_dump(conn)
    finally:
        conn.close()


def migrate_and_dump(path: Path) -> dict:
    """Open (and thereby migrate) the database through SessionStore, then
    dump it. The shape mirrors what ``yeaboi __migrate-db`` + ``__dump-db``
    will produce from W9 phase 2 on."""
    from yeaboi.sessions import SessionStore

    store = SessionStore(path)
    schema_mismatch = store.schema_mismatch
    store.close()

    conn = sqlite3.connect(str(path))
    try:
        stamped = conn.execute("SELECT MAX(schema_version) FROM schema_info").fetchone()[0]
        return {
            "open": {"schema_mismatch": schema_mismatch, "stamped_version": stamped},
            "dump": canonical_dump(conn),
        }
    finally:
        conn.close()


def render(dump: dict) -> str:
    """The exact serialisation both the goldens and the byte-compare use."""
    return json.dumps(dump, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: dump.py <db-path>")
    sys.stdout.write(render(migrate_and_dump(Path(sys.argv[1]))))


if __name__ == "__main__":
    main()
