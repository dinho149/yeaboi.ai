"""Persistence for ship runs, including the approval-gate state machine.

Shares ``sessions.db`` the way the roadmap/agentwatch stores do: an additive
``CREATE TABLE IF NOT EXISTS`` schema executed on open (self-healing, no
``CURRENT_SCHEMA_VERSION`` bump — which also leaves the Go sidecar's schema
ceiling untouched).

The gate protocol is archon's, and its core is that **resolution and resume
are two independent compare-and-swap transitions**:

- ``resolve_gate`` answers an *open* gate (``status='awaiting_approval' AND
  gate_resolution=''``) exactly once; a losing concurrent approver writes
  nothing and is told so. The audit event is inserted in the same transaction,
  so a resolved gate can never exist without its trail.
- ``save_run(..., expect_status=…)`` is the engine's guarded write for every
  other transition; a mismatch means someone else moved the run first, and the
  caller re-reads instead of clobbering.

``status`` and ``gate_resolution`` are real columns (the CAS predicates), and
the full artifact rides beside them as JSON; the columns are rewritten from
the artifact on every write so they cannot drift.

One store instance owns one SQLite connection and is **not** shared across
threads — the TUI thread and the engine worker each open their own (the CAS
is in the database, not in Python).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from yeaboi.agent.state import SHIP_STATUSES, ShipPhase, ShipRun, ShipValidation

logger = logging.getLogger(__name__)

# How far back a batch lookup reads. A batch is at most a few runs and is
# continued within hours, so this is generous; the alternative is a story
# column on a table that deliberately stores the artifact as one JSON blob.
_BATCH_SCAN = 200

_SHIP_SCHEMA = """
CREATE TABLE IF NOT EXISTS ship_runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'planned',
    gate_resolution TEXT NOT NULL DEFAULT '',
    run_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ship_gate_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run_to_json(run: ShipRun) -> str:
    return json.dumps(asdict(run), ensure_ascii=False)


def listing_dict(run: ShipRun) -> dict:
    """One run as JSON for a *listing* surface — without the stored patch.

    ``diff_text`` is capped per run, not per response, so a hundred rows of it
    is megabytes of patch nobody asked for; a listing is also the thing people
    poll in a loop. The stat, the branch and the worktree stay, and reading the
    change itself is a git command away — the gate that needs it renders from
    the artifact, not from this.

    ``asdict`` per run rather than on the list: the MCP layer's ``to_jsonable``
    only unpacks a top-level dataclass, so a nested one would arrive as a repr.
    """
    payload = asdict(run)
    payload.pop("diff_text", None)
    # The artifact renamed story_id → item_id when ship learned to target an
    # epic or a task. Listings keep emitting the old key, mirrored, because the
    # MCP ship_history payload and the plugin skill document it.
    payload["story_id"] = payload.get("item_id", "")
    return payload


def _dict_to_run(data: dict) -> ShipRun:
    """Rebuild the frozen artifact from JSON; tolerant of missing keys."""
    validation = data.get("validation") or {}
    return ShipRun(
        run_id=str(data.get("run_id", "")),
        # Old rows wrote the id under story_id; new ones under item_id.
        item_id=str(data.get("item_id") or data.get("story_id") or ""),
        level=str(data.get("level") or "story"),
        session_id=str(data.get("session_id", "")),
        agent_session_id=str(data.get("agent_session_id", "")),
        repo=str(data.get("repo", "")),
        branch=str(data.get("branch", "")),
        worktree=str(data.get("worktree", "")),
        base_sha=str(data.get("base_sha", "")),
        pr_base=str(data.get("pr_base", "")),
        status=str(data.get("status", "planned")),
        phases=tuple(
            ShipPhase(
                name=str(p.get("name", "")),
                status=str(p.get("status", "")),
                detail=str(p.get("detail", "")),
                duration_s=float(p.get("duration_s", 0.0)),
            )
            for p in data.get("phases") or ()
            if isinstance(p, dict)
        ),
        validation=ShipValidation(
            configured=bool(validation.get("configured", False)),
            command=str(validation.get("command", "")),
            passed=bool(validation.get("passed", False)),
            exit_code=int(validation.get("exit_code", -1)),
            output_tail=str(validation.get("output_tail", "")),
        ),
        diff_stat=str(data.get("diff_stat", "")),
        diff_text=str(data.get("diff_text", "")),
        cost_usd=float(data.get("cost_usd", 0.0)),
        transcript_findings=tuple(
            (str(f[0]), str(f[1]), str(f[2]))
            for f in data.get("transcript_findings") or ()
            if isinstance(f, list | tuple) and len(f) >= 3
        ),
        transcript_path=str(data.get("transcript_path", "")),
        pr_url=str(data.get("pr_url", "")),
        gate_resolution=str(data.get("gate_resolution", "")),
        gate_comment=str(data.get("gate_comment", "")),
        rejection_count=int(data.get("rejection_count", 0)),
        batch_id=str(data.get("batch_id", "")),
        batch_item_id=str(data.get("batch_item_id", "")),
        batch_index=int(data.get("batch_index", 0) or 0),
        batch_total=int(data.get("batch_total", 0) or 0),
        owner_pid=int(data.get("owner_pid", 0) or 0),
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
        warnings=tuple(str(w) for w in data.get("warnings") or ()),
    )


class ShipStore:
    """Run history + gate state in the shared sessions database."""

    def __init__(self, db_path: Path | None = None) -> None:
        # Lazy import so tests that monkeypatch yeaboi.paths.get_db_path
        # redirect this store too (the provenance/engine convention).
        from yeaboi.paths import get_db_path

        self._path = db_path or get_db_path()
        self._conn = sqlite3.connect(str(self._path))
        self._conn.isolation_level = None  # explicit BEGIN IMMEDIATE below
        self._conn.executescript(_SHIP_SCHEMA)

    def __enter__(self) -> ShipStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def close(self) -> None:
        if getattr(self, "_conn", None) is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    # -- writes ------------------------------------------------------------

    def record_run(self, run: ShipRun) -> ShipRun:
        """Insert a new run; stamps created_at/updated_at."""
        # `replace`, never ShipRun(**asdict(...)): asdict recurses into the
        # nested frozen dataclasses and would rebuild them as plain dicts.
        # The owning pid is stamped here so a run abandoned at the gate can later
        # be told apart from one a live process is still driving.
        stamped = replace(
            run,
            owner_pid=run.owner_pid or os.getpid(),
            created_at=run.created_at or _now(),
            updated_at=_now(),
        )
        self._conn.execute(
            "INSERT INTO ship_runs (run_id, status, gate_resolution, run_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                stamped.run_id,
                stamped.status,
                stamped.gate_resolution,
                _run_to_json(stamped),
                stamped.created_at,
                stamped.updated_at,
            ),
        )
        logger.info("Recorded ship run %s (%s)", stamped.run_id, stamped.status)
        return stamped

    def save_run(self, run: ShipRun, *, expect_status: str | None = None) -> bool:
        """Guarded full-artifact write. False when the CAS lost — re-read then.

        With ``expect_status`` the write lands only if the stored status still
        matches; without it the write is unconditional (first persist of a
        terminal failure, where there is nothing to race).
        """
        if run.status not in SHIP_STATUSES:
            raise ValueError(f"unknown ship status: {run.status!r}")
        stamped = replace(run, updated_at=_now())
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            if expect_status is not None:
                row = self._conn.execute("SELECT status FROM ship_runs WHERE run_id = ?", (run.run_id,)).fetchone()
                if row is None or row[0] != expect_status:
                    self._conn.execute("ROLLBACK")
                    return False
            cursor = self._conn.execute(
                "UPDATE ship_runs SET status = ?, gate_resolution = ?, run_json = ?, updated_at = ? WHERE run_id = ?",
                (stamped.status, stamped.gate_resolution, _run_to_json(stamped), stamped.updated_at, stamped.run_id),
            )
            if cursor.rowcount == 0:
                # A terminal state for a run that never reached record_run
                # (setup failed before the row existed). An UPDATE matching
                # zero rows "succeeding" is how failures vanish from history,
                # so the unconditional path inserts instead.
                self._conn.execute(
                    "INSERT INTO ship_runs (run_id, status, gate_resolution, run_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        stamped.run_id,
                        stamped.status,
                        stamped.gate_resolution,
                        _run_to_json(stamped),
                        stamped.created_at or stamped.updated_at,
                        stamped.updated_at,
                    ),
                )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        return True

    def resolve_gate(self, run_id: str, resolution: str, comment: str = "") -> bool:
        """Answer an open gate exactly once. False when there was none to answer.

        The predicate is ``status='awaiting_approval' AND gate_resolution=''``
        — a second approver, or an approver racing a cancel, loses cleanly.
        The audit event commits in the same transaction: a resolved gate
        without its trail can never exist.
        """
        if resolution not in ("approved", "rejected"):
            raise ValueError(f"gate resolution must be approved|rejected, got {resolution!r}")
        now = _now()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            row = self._conn.execute(
                "SELECT run_json FROM ship_runs WHERE run_id = ? AND status = 'awaiting_approval' "
                "AND gate_resolution = ''",
                (run_id,),
            ).fetchone()
            if row is None:
                self._conn.execute("ROLLBACK")
                return False
            data = json.loads(row[0])
            data["gate_resolution"] = resolution
            data["gate_comment"] = comment
            if resolution == "rejected":
                data["rejection_count"] = int(data.get("rejection_count", 0)) + 1
            data["updated_at"] = now
            self._conn.execute(
                "UPDATE ship_runs SET gate_resolution = ?, run_json = ?, updated_at = ? "
                "WHERE run_id = ? AND status = 'awaiting_approval' AND gate_resolution = ''",
                (resolution, json.dumps(data, ensure_ascii=False), now, run_id),
            )
            self._conn.execute(
                "INSERT INTO ship_gate_events (run_id, event, detail, created_at) VALUES (?, ?, ?, ?)",
                (run_id, resolution, comment, now),
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        logger.info("Gate for %s resolved: %s", run_id, resolution)
        return True

    def delete_run(self, run_id: str) -> bool:
        """Forget a run: its row, its gate trail, and its checkout. True if a row went.

        The worktree removal is best-effort and deliberately keeps the branch
        (``worktree.remove``'s default): the row is bookkeeping, the branch is the
        work, and discarding a listing must never discard commits.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._conn.execute("DELETE FROM ship_runs WHERE run_id = ?", (run_id,))
            self._conn.execute("DELETE FROM ship_gate_events WHERE run_id = ?", (run_id,))
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        if cursor.rowcount == 0:
            return False
        try:
            from yeaboi.ship import worktree

            worktree.remove(run_id)
        except Exception as exc:  # noqa: BLE001 — a stuck checkout must not fail the delete
            logger.warning("Could not remove the worktree for %s: %s", run_id, exc)
        logger.info("Deleted ship run %s", run_id)
        return True

    # -- reads -------------------------------------------------------------

    def get_run(self, run_id: str) -> ShipRun | None:
        row = self._conn.execute("SELECT run_json FROM ship_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        try:
            return _dict_to_run(json.loads(row[0]))
        except ValueError:
            logger.warning("Corrupt run_json for %s", run_id)
            return None

    def list_runs(self, *, limit: int = 20) -> list[ShipRun]:
        """Newest first."""
        rows = self._conn.execute(
            "SELECT run_json FROM ship_runs ORDER BY created_at DESC, run_id DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        out: list[ShipRun] = []
        for (raw,) in rows:
            try:
                out.append(_dict_to_run(json.loads(raw)))
            except ValueError:
                continue
        return out

    def batch_runs(self, batch_id: str) -> list[ShipRun]:
        """Every member of *batch_id*, oldest first — batch order."""
        if not batch_id:
            return []
        return [r for r in reversed(self.list_runs(limit=_BATCH_SCAN)) if r.batch_id == batch_id]

    def open_batch(self, item_id: str, repo: str, story_ids: tuple[str, ...] = ()) -> tuple[str, list[ShipRun]]:
        """The newest unfinished batch for *item_id* in *repo*: ``(id, members)``.

        Relaunching an epic continues that batch instead of opening a second one
        over the same stories — which is what makes a batch stopped by the launch
        budget resumable with no new command.

        "Unfinished" is measured against *story_ids*, the stories the epic has
        **now**: a batch is done when every one of them has an approved member.
        Counting members instead would read a batch holding a rejected attempt as
        forever unfinished, and a batch whose epic has since grown a story as
        finished — so the same relaunch would re-ship a clean epic from scratch
        while a messy one adopted its old batch.
        """
        if not item_id:
            return "", []
        # One listing, grouped in memory: each row carries the run's stored patch,
        # so re-reading per candidate batch would be megabytes of JSON per call.
        grouped: dict[str, list[ShipRun]] = {}
        order: list[str] = []
        for run in self.list_runs(limit=_BATCH_SCAN):  # newest first
            if not run.batch_id or run.batch_item_id != item_id or run.repo != repo:
                continue
            if run.batch_id not in grouped:
                order.append(run.batch_id)
            grouped.setdefault(run.batch_id, []).append(run)
        for batch_id in order:
            members = list(reversed(grouped[batch_id]))  # oldest first — batch order
            approved = {m.item_id for m in members if m.status == "approved"}
            wanted = set(story_ids) if story_ids else {m.item_id for m in members}
            if not wanted <= approved or len(members) < members[0].batch_total:
                return batch_id, members
        return "", []

    def gate_events(self, run_id: str) -> list[tuple[str, str, str]]:
        """(event, detail, created_at) oldest first — the gate's audit trail."""
        rows = self._conn.execute(
            "SELECT event, detail, created_at FROM ship_gate_events WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        return [(str(e), str(d), str(c)) for e, d, c in rows]
