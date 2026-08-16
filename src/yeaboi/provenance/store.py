"""The hash-chained decision log — append-only SQLite, shared sessions.db.

Vendored and adapted from Semantica (https://github.com/semantica-agi/semantica),
``semantica/provenance/storage.py`` and the chain half of
``semantica/provenance/manager.py`` at commit 15171fd3 — Copyright (c) 2026
Hawksight AI, licensed under the MIT License (see THIRD_PARTY_NOTICES.md).
Changes from upstream: rows are append-only with ``sequence_id`` as the
primary key (upstream upserted by ``entity_id`` and relabeled rows to fake
history); columns are named, never positional; invalidation appends a
tombstone record instead of mutating columns; the storage ABC, in-memory
backend, migrations, lineage BFS and rdflib export were all dropped.

The chain lives in the shared ``sessions.db`` (every yeaboi store does — see
``agentwatch/store.py``), as its own additive tables: the schema below runs
idempotently on every open, so existing databases gain it without a
``CURRENT_SCHEMA_VERSION`` bump and the Go sidecar's schema ceiling is
untouched.

What makes it tamper-evident (the part worth reading twice): every record's
checksum covers its ``previous_checksum``, sequence ids are assigned head+1
under a writer lock, and ``verify`` re-walks the whole chain checking the
three row invariants plus the persisted head anchor. An edited row fails its
own checksum; a deleted middle row leaves a sequence gap AND orphans its
successor's ``previous_checksum``; a renumbered row breaks the arithmetic;
and a truncated *tail* — which satisfies all three row invariants by
construction — falls short of ``provenance_head``, the (max sequence_id,
checksum) marker written in the same transaction as every append. None of
that *prevents* tampering — sqlite is a local file, and an adversary who
rewrites the rows and the anchor together is outside what a purely local
file can prove — it makes tampering visible, which is what an audit trail
is for.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from yeaboi.provenance.integrity import compute_checksum, verify_checksum
from yeaboi.provenance.records import (
    KIND_INVALIDATION,
    ChainBreak,
    ChainVerification,
    DecisionRecord,
)

logger = logging.getLogger(__name__)

# A concurrent writer can win the head race; the append retries with a fresh
# head rather than failing the caller (same bound as artifacts/store.py).
_APPEND_ATTEMPTS = 8

_PROVENANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS provenance_records (
    sequence_id INTEGER PRIMARY KEY,
    entity_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    record_kind TEXT NOT NULL DEFAULT 'decision',
    activity_id TEXT NOT NULL DEFAULT '',
    agent_id TEXT NOT NULL DEFAULT '',
    agent_type TEXT NOT NULL DEFAULT 'software_agent',
    is_automated INTEGER NOT NULL DEFAULT 1,
    role TEXT NOT NULL DEFAULT '',
    source_document TEXT NOT NULL DEFAULT '',
    timestamp TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    inputs TEXT NOT NULL DEFAULT '[]',
    parent_entity_id TEXT NOT NULL DEFAULT '',
    previous_version_id TEXT NOT NULL DEFAULT '',
    derived_from_id TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    extras TEXT NOT NULL DEFAULT '[]',
    previous_checksum TEXT NOT NULL DEFAULT '',
    checksum TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provenance_head (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    sequence_id INTEGER NOT NULL,
    checksum TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provenance_entity ON provenance_records(entity_id);
CREATE INDEX IF NOT EXISTS idx_provenance_activity ON provenance_records(activity_id);
CREATE INDEX IF NOT EXISTS idx_provenance_type ON provenance_records(entity_type);
CREATE INDEX IF NOT EXISTS idx_provenance_timestamp ON provenance_records(timestamp);
"""

_COLUMNS = (
    "sequence_id",
    "entity_id",
    "entity_type",
    "record_kind",
    "activity_id",
    "agent_id",
    "agent_type",
    "is_automated",
    "role",
    "source_document",
    "timestamp",
    "confidence",
    "inputs",
    "parent_entity_id",
    "previous_version_id",
    "derived_from_id",
    "detail",
    "extras",
    "previous_checksum",
    "checksum",
)
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM provenance_records"  # noqa: S608 — constant column list


def _record_to_row(record: DecisionRecord) -> tuple:
    return (
        record.sequence_id,
        record.entity_id,
        record.entity_type,
        record.record_kind,
        record.activity_id,
        record.agent_id,
        record.agent_type,
        1 if record.is_automated else 0,
        record.role,
        record.source_document,
        record.timestamp,
        float(record.confidence),
        json.dumps(list(record.inputs), ensure_ascii=False),
        record.parent_entity_id,
        record.previous_version_id,
        record.derived_from_id,
        record.detail,
        json.dumps([list(pair) for pair in record.extras], ensure_ascii=False),
        record.previous_checksum,
        record.checksum,
    )


def _row_to_record(row: sqlite3.Row) -> DecisionRecord:
    def _pairs(raw: str) -> tuple[tuple[str, str], ...]:
        try:
            loaded = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return ()
        if not isinstance(loaded, list):
            return ()
        return tuple((str(p[0]), str(p[1])) for p in loaded if isinstance(p, (list, tuple)) and len(p) == 2)

    def _strings(raw: str) -> tuple[str, ...]:
        try:
            loaded = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return ()
        return tuple(str(v) for v in loaded) if isinstance(loaded, list) else ()

    return DecisionRecord(
        sequence_id=int(row["sequence_id"]),
        entity_id=row["entity_id"],
        entity_type=row["entity_type"],
        record_kind=row["record_kind"],
        activity_id=row["activity_id"],
        agent_id=row["agent_id"],
        agent_type=row["agent_type"],
        is_automated=bool(row["is_automated"]),
        role=row["role"],
        source_document=row["source_document"],
        timestamp=row["timestamp"],
        confidence=float(row["confidence"]),
        inputs=_strings(row["inputs"]),
        parent_entity_id=row["parent_entity_id"],
        previous_version_id=row["previous_version_id"],
        derived_from_id=row["derived_from_id"],
        detail=row["detail"],
        extras=_pairs(row["extras"]),
        previous_checksum=row["previous_checksum"],
        checksum=row["checksum"],
    )


class ProvenanceChain:
    """Append, look up, and verify decision records in one database."""

    def __init__(self, db_path: Path | str) -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.isolation_level = None  # autocommit; appends take explicit locks
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.executescript(_PROVENANCE_SCHEMA)

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:  # pragma: no cover — close is best-effort
            pass

    def __enter__(self) -> ProvenanceChain:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover — GC safety net
        try:
            self.close()
        except Exception:  # noqa: BLE001 — interpreter teardown
            pass

    # -- writes ------------------------------------------------------------

    def append(self, record: DecisionRecord) -> DecisionRecord:
        """Chain one record and return the stamped copy that was stored.

        The caller supplies the decision; the chain assigns ``sequence_id``
        (head + 1), ``previous_checksum`` (head's checksum), a UTC
        ``timestamp`` when the record carries none, ``previous_version_id``
        when an earlier record exists for the same entity, and the checksum
        over all of it. Retries the head race a bounded number of times.
        """
        return self.append_all([record])[0]

    def append_all(self, records: list[DecisionRecord]) -> list[DecisionRecord]:
        """Chain several records in one writer transaction (one head race)."""
        if not records:
            return []
        last_error: Exception | None = None
        for _ in range(_APPEND_ATTEMPTS):
            try:
                self._conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                last_error = exc
                continue
            try:
                stamped = [self._stamp_and_insert(record) for record in records]
                self._conn.execute("COMMIT")
                return stamped
            except sqlite3.Error as exc:
                self._conn.execute("ROLLBACK")
                last_error = exc
        raise RuntimeError(f"provenance append failed after {_APPEND_ATTEMPTS} attempts") from last_error

    def _stamp_and_insert(self, record: DecisionRecord) -> DecisionRecord:
        head = self._conn.execute(
            "SELECT sequence_id, checksum FROM provenance_records ORDER BY sequence_id DESC LIMIT 1"
        ).fetchone()
        changes: dict[str, object] = {
            "sequence_id": (int(head["sequence_id"]) + 1) if head else 1,
            "previous_checksum": head["checksum"] if head else "",
        }
        if not record.timestamp:
            changes["timestamp"] = datetime.now(UTC).isoformat()
        if not record.previous_version_id and record.entity_id:
            prior = self._conn.execute(
                "SELECT sequence_id FROM provenance_records WHERE entity_id = ? ORDER BY sequence_id DESC LIMIT 1",
                (record.entity_id,),
            ).fetchone()
            if prior:
                changes["previous_version_id"] = f"seq:{int(prior['sequence_id'])}"
        stamped = record.stamped(**changes)
        stamped = stamped.stamped(checksum=compute_checksum(stamped))
        self._conn.execute(
            f"INSERT INTO provenance_records ({', '.join(_COLUMNS)}) "  # noqa: S608 — constant column list
            f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
            _record_to_row(stamped),
        )
        # The head anchor advances in the SAME transaction as the row: a
        # truncated tail satisfies every row invariant by construction, and
        # this marker is what lets verify() see the rows that are missing.
        self._conn.execute(
            "INSERT INTO provenance_head (id, sequence_id, checksum) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET sequence_id = excluded.sequence_id, checksum = excluded.checksum",
            (stamped.sequence_id, stamped.checksum),
        )
        return stamped

    def invalidate(self, entity_id: str, *, agent_id: str, reason: str = "", agent_type: str = "") -> DecisionRecord:
        """Retract a decision by appending a tombstone record.

        The original record stays in the chain — deleting history is the one
        thing this store exists to make detectable. Raises ``ValueError`` for
        an entity the chain has never seen.
        """
        latest = self.get(entity_id)
        if latest is None:
            raise ValueError(f"unknown provenance entity: {entity_id}")
        tombstone = DecisionRecord(
            entity_id=entity_id,
            entity_type=latest.entity_type,
            record_kind=KIND_INVALIDATION,
            activity_id=latest.activity_id,
            agent_id=agent_id,
            agent_type=agent_type or latest.agent_type,
            role="invalidator",
            detail=reason,
            parent_entity_id=latest.entity_id,
        )
        return self.append(tombstone)

    # -- reads -------------------------------------------------------------

    def get(self, entity_id: str) -> DecisionRecord | None:
        """The newest record for an entity (a tombstone, if it was retracted)."""
        row = self._conn.execute(
            f"{_SELECT} WHERE entity_id = ? ORDER BY sequence_id DESC LIMIT 1", (entity_id,)
        ).fetchone()
        return _row_to_record(row) if row else None

    def is_invalidated(self, entity_id: str) -> bool:
        latest = self.get(entity_id)
        return latest is not None and latest.record_kind == KIND_INVALIDATION

    def history(self, entity_id: str) -> list[DecisionRecord]:
        """Every record for an entity, oldest first."""
        rows = self._conn.execute(f"{_SELECT} WHERE entity_id = ? ORDER BY sequence_id ASC", (entity_id,)).fetchall()
        return [_row_to_record(row) for row in rows]

    def trace(self, entity_id: str, *, depth: int = 2, limit: int = 50) -> list[DecisionRecord]:
        """The "why" trail: the entity's records plus, breadth-first, the
        latest record behind each of its inputs, up to ``depth`` hops."""
        seen: set[str] = set()
        out: list[DecisionRecord] = []
        frontier = [entity_id]
        for _ in range(max(1, depth)):
            next_frontier: list[str] = []
            for eid in frontier:
                if eid in seen or len(out) >= limit:
                    continue
                seen.add(eid)
                for record in self.history(eid):
                    if len(out) >= limit:
                        break
                    out.append(record)
                    next_frontier.extend(i for i in record.inputs if i not in seen)
            frontier = next_frontier
            if not frontier:
                break
        return out

    def records(self, *, since: str = "", entity_type: str = "", limit: int = 200) -> list[DecisionRecord]:
        """Newest records first, optionally filtered by time and type."""
        clauses, params = [], []
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if entity_type:
            clauses.append("entity_type = ?")
            params.append(entity_type)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"{_SELECT}{where} ORDER BY sequence_id DESC LIMIT ?",  # noqa: S608 — clauses are literals
            (*params, int(limit)),
        ).fetchall()
        return [_row_to_record(row) for row in rows]

    def counts_by_type(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT entity_type, COUNT(*) AS n FROM provenance_records GROUP BY entity_type ORDER BY n DESC"
        ).fetchall()
        return {row["entity_type"]: int(row["n"]) for row in rows}

    def total(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM provenance_records").fetchone()
        return int(row["n"])

    def count_since(self, since: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM provenance_records WHERE timestamp >= ?", (since,)
        ).fetchone()
        return int(row["n"])

    # -- verification ------------------------------------------------------

    def verify(self) -> ChainVerification:
        """Walk the whole chain and check the row invariants plus the anchor.

        State advances from each record's *stored* fields whether or not it
        was flagged, so one corrupted row reports once instead of cascading
        into N spurious breaks (upstream got this right; kept). The final
        check compares the walk's last record against ``provenance_head``:
        the three row invariants are all relative to the rows present, so a
        truncated tail (``DELETE … WHERE sequence_id > n``, or the whole
        table) satisfies every one of them — only the anchor, written in the
        same transaction as each append, can see the rows that are missing.
        """
        broken: list[ChainBreak] = []
        previous: DecisionRecord | None = None
        count = 0
        for row in self._conn.execute(f"{_SELECT} ORDER BY sequence_id ASC"):
            record = _row_to_record(row)
            count += 1
            if not verify_checksum(record):
                broken.append(
                    ChainBreak(
                        sequence_id=record.sequence_id,
                        entity_id=record.entity_id,
                        reason="checksum_mismatch",
                    )
                )
            if previous is not None:
                if record.previous_checksum != previous.checksum:
                    broken.append(
                        ChainBreak(
                            sequence_id=record.sequence_id,
                            entity_id=record.entity_id,
                            reason="chain_break",
                            expected_previous_checksum=previous.checksum,
                            actual_previous_checksum=record.previous_checksum,
                        )
                    )
                if record.sequence_id != previous.sequence_id + 1:
                    broken.append(
                        ChainBreak(
                            sequence_id=record.sequence_id,
                            entity_id=record.entity_id,
                            reason="chain_break",
                            expected_sequence_id=previous.sequence_id + 1,
                        )
                    )
            elif record.sequence_id != 1:
                broken.append(
                    ChainBreak(
                        sequence_id=record.sequence_id,
                        entity_id=record.entity_id,
                        reason="chain_break",
                        expected_sequence_id=1,
                    )
                )
            previous = record

        anchor = self._conn.execute("SELECT sequence_id, checksum FROM provenance_head WHERE id = 1").fetchone()
        if anchor is not None:
            expected_seq = int(anchor["sequence_id"])
            if previous is None or previous.sequence_id != expected_seq or previous.checksum != anchor["checksum"]:
                broken.append(
                    ChainBreak(
                        sequence_id=previous.sequence_id if previous is not None else 0,
                        entity_id=previous.entity_id if previous is not None else "",
                        reason="truncated_tail",
                        expected_previous_checksum=str(anchor["checksum"]),
                        actual_previous_checksum=previous.checksum if previous is not None else "",
                        expected_sequence_id=expected_seq,
                    )
                )
        return ChainVerification(valid=not broken, total_records=count, broken=tuple(broken))
