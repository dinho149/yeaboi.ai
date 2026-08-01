"""SQLite store for the append-only artifact edit log.

One table in the shared ``~/.scrum-agent/sessions.db``: ``artifact_edits``. Every
correction a reader makes is a row, and the rows **are** the version history —
version N of a document is its base plus the first N rows, which is what makes
"show me what changed" a query rather than a diff.

Follows the store patterns the modes already use (``ReportingStore`` /
``RetroStore``): its own connection to the same DB, autocommit, context-manager
support, an idempotent ``CREATE IF NOT EXISTS`` schema also referenced by
``sessions.py``'s v21 migration.

What is deliberately not here
-----------------------------

**No edit_count column anywhere.** A denormalised counter is a second source of
truth for something a ``COUNT(*)`` over an indexed column answers exactly, and a
count that can drift is worse than a count you have to ask for.

**No delete.** Reverting is an *edit* (see :mod:`yeaboi.artifacts.edits`), so it
appends rather than removes. A log you can quietly delete from is not a history,
and the whole reason this table exists is that somebody wanted to know who
changed the number.

# See docs: "Session Management" — SQLite persistence, schema versioning
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from yeaboi.artifacts.edits import Edit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema — referenced by sessions.py migration v21 AND created on store open
# ---------------------------------------------------------------------------

_ARTIFACT_EDITS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS artifact_edits (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    edit_id       TEXT    NOT NULL,
    share_id      TEXT    NOT NULL DEFAULT '',
    artifact_kind TEXT    NOT NULL DEFAULT '',
    artifact_ref  TEXT    NOT NULL DEFAULT '',
    base_hash     TEXT    NOT NULL DEFAULT '',
    seq           INTEGER NOT NULL DEFAULT 0,
    op            TEXT    NOT NULL DEFAULT '',
    path          TEXT    NOT NULL DEFAULT '',
    value         TEXT    NOT NULL DEFAULT '',
    base          TEXT    NOT NULL DEFAULT '',
    label         TEXT    NOT NULL DEFAULT '',
    target        TEXT    NOT NULL DEFAULT '',
    author        TEXT    NOT NULL DEFAULT '',
    avatar        TEXT    NOT NULL DEFAULT '',
    pid           TEXT    NOT NULL DEFAULT '',
    ip_hash       TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_artifact_edits_edit_id
    ON artifact_edits(edit_id);
CREATE INDEX IF NOT EXISTS idx_artifact_edits_ref
    ON artifact_edits(artifact_kind, artifact_ref, seq);"""


def artifact_ref(kind: str, *, session_id: str = "", run_id: int = 0, engineer: str = "") -> str:
    """Return the stable handle for one artifact instance.

    Different modes identify a run differently — standup and reporting by a
    history row id, performance by engineer, team profile by neither — so the
    reference is a formatted string rather than a foreign key. It is opaque
    everywhere except here, and it is what a later ``list_edits`` matches on.
    """
    if run_id:
        return f"{kind}:{run_id}"
    if engineer:
        return f"{kind}:engineer:{engineer}"
    return f"{kind}:session:{session_id}"


def base_hash(artifact: object) -> str:
    """Return a stable digest of the artifact a log was written against.

    Pinned on the first edit of a share so a later replay can tell that the base
    moved underneath it — a standup re-run produces a different report at the
    same paths, and a log replayed against it without this would look like it
    applied cleanly while quietly rewriting prose nobody had read.

    ``sort_keys`` because the digest has to survive a Python version that
    reorders a dict; ``default=str`` because it must never raise on a field type
    it has not met.
    """
    payload = json.dumps(asdict(artifact), sort_keys=True, ensure_ascii=False, default=str)  # type: ignore[call-overload]
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash_ip(ip: str, salt: str) -> str:
    """Return a salted digest of a client address, or ``""``.

    Stored instead of the address itself. It is enough to tell two editors apart
    during one share — which is the only question anyone actually asks of it —
    without the log becoming a record of who was on which network, kept
    indefinitely in a file that gets copied between machines.
    """
    if not ip:
        return ""
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:16]


# The row order the INSERT above expects. Spelled out there rather than joined
# from here: a query assembled from a tuple reads as a SQL-injection vector
# even when the tuple is a constant, and the literal is easier to check.
_COLUMNS = (
    "edit_id",
    "share_id",
    "artifact_kind",
    "artifact_ref",
    "base_hash",
    "seq",
    "op",
    "path",
    "value",
    "base",
    "label",
    "target",
    "author",
    "avatar",
    "pid",
    "ip_hash",
    "created_at",
)


def _row_to_edit(row: sqlite3.Row) -> Edit:
    return Edit(
        edit_id=row["edit_id"],
        seq=int(row["seq"] or 0),
        op=row["op"],
        path=row["path"],
        value=row["value"],
        base=row["base"],
        label=row["label"],
        target=row["target"],
        author=row["author"],
        avatar=row["avatar"],
        pid=row["pid"],
        at=row["created_at"],
    )


class ArtifactEditStore:
    """SQLite-backed append-only log of reader corrections."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.isolation_level = None  # autocommit
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_ARTIFACT_EDITS_SCHEMA)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> ArtifactEditStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    # ── Writing ───────────────────────────────────────────────────────────

    def record(
        self,
        edit: Edit,
        *,
        kind: str,
        ref: str,
        share_id: str = "",
        base: str = "",
        ip_hash: str = "",
    ) -> int:
        """Append one validated edit, returning its assigned sequence number.

        **Idempotent by ``edit_id``.** A client that retries a POST it never saw
        the answer to — a dropped tunnel, a backgrounded phone — must not append
        the correction twice. The unique index makes that a database fact rather
        than a race between two request threads, and a duplicate returns the
        original's sequence number so the caller answers the retry with the same
        state it answered the first attempt with.
        """
        seq = self.next_seq(kind, ref)
        row = (
            edit.edit_id,
            share_id,
            kind,
            ref,
            base,
            seq,
            edit.op,
            edit.path,
            edit.value,
            edit.base,
            edit.label,
            edit.target,
            edit.author,
            edit.avatar,
            edit.pid,
            ip_hash,
            edit.at or self._now(),
        )
        try:
            self._conn.execute(
                """INSERT INTO artifact_edits
                       (edit_id, share_id, artifact_kind, artifact_ref, base_hash, seq, op, path,
                        value, base, label, target, author, avatar, pid, ip_hash, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                row,
            )
        except sqlite3.IntegrityError:
            existing = self._conn.execute(
                "SELECT seq FROM artifact_edits WHERE edit_id = ?", (edit.edit_id,)
            ).fetchone()
            logger.info("Duplicate edit ignored (kind=%s op=%s edit_id=%s)", kind, edit.op, edit.edit_id[:8])
            return int(existing["seq"]) if existing else seq
        # Never the value: an edit body is the team's own prose, and a log file
        # is copied around far more casually than a database is.
        logger.info(
            "Recorded edit: kind=%s ref=%s op=%s path=%s seq=%d author_len=%d pid=%s",
            kind,
            ref,
            edit.op,
            edit.path,
            seq,
            len(edit.author),
            edit.pid[:8],
        )
        return seq

    def next_seq(self, kind: str, ref: str) -> int:
        """Return the sequence number the next edit for this artifact would take."""
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS top FROM artifact_edits WHERE artifact_kind = ? AND artifact_ref = ?",
            (kind, ref),
        ).fetchone()
        return int(row["top"]) + 1

    # ── Reading ───────────────────────────────────────────────────────────

    def list_edits(self, kind: str, ref: str, *, limit: int = 0) -> tuple[Edit, ...]:
        """Return this artifact's edits in the order they were accepted.

        Ordered by ``seq`` and then ``id``, never by timestamp: two edits inside
        the same clock tick would otherwise replay in an arbitrary order, and
        materialisation is only deterministic if the order is.
        """
        sql = "SELECT * FROM artifact_edits WHERE artifact_kind = ? AND artifact_ref = ? ORDER BY seq ASC, id ASC"
        params: tuple = (kind, ref)
        if limit > 0:
            sql += " LIMIT ?"
            params = (*params, limit)
        return tuple(_row_to_edit(row) for row in self._conn.execute(sql, params))

    def count_edits(self, kind: str, ref: str) -> int:
        """How many corrections this artifact carries. Counted, never cached."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM artifact_edits WHERE artifact_kind = ? AND artifact_ref = ?",
            (kind, ref),
        ).fetchone()
        return int(row["n"])

    def recorded_base_hash(self, kind: str, ref: str) -> str:
        """The base digest pinned by this artifact's first edit, or ``""``."""
        row = self._conn.execute(
            "SELECT base_hash FROM artifact_edits "
            "WHERE artifact_kind = ? AND artifact_ref = ? AND base_hash != '' "
            "ORDER BY seq ASC LIMIT 1",
            (kind, ref),
        ).fetchone()
        return row["base_hash"] if row else ""

    def editors(self, kind: str, ref: str) -> tuple[str, ...]:
        """Distinct self-declared names that have edited this artifact.

        Self-declared: see :mod:`yeaboi.artifacts.edits`. Used for a count in the
        TUI ("3 edits by 2 people"), never as an identity claim.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT author FROM artifact_edits "
            "WHERE artifact_kind = ? AND artifact_ref = ? AND author != '' ORDER BY author",
            (kind, ref),
        )
        return tuple(row["author"] for row in rows)
