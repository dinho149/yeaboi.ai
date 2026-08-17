"""Persistence for declared ceremonies and the run ledger.

Shares ``sessions.db`` the way the ship/roadmap/agentwatch stores do: an
additive ``CREATE TABLE IF NOT EXISTS`` schema executed on open — self-healing,
no ``CURRENT_SCHEMA_VERSION`` bump, which also leaves the Go sidecar's schema
ceiling untouched.

Two tables, and the second one is the point:

- ``ceremonies`` — what the team declared. The store is the source of truth for
  every surface; the OS job is downstream of it, never the other way round.
- ``ceremony_runs`` — what actually fired. A scheduled run that fails at 06:00
  with nobody watching is how this feature dies quietly, so a row is written
  whatever happened, including for the runs the guards *declined*. The reason is
  a column: the fleet already learned the expensive version of this lesson, where
  the fact of a failure was durable and the reason was free text nobody kept.

One store instance owns one SQLite connection and is not shared across threads.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

from yeaboi.agent.state import CEREMONY_OUTCOMES, Ceremony, CeremonyRun

logger = logging.getLogger(__name__)

# A ceremony name becomes part of a launchd label, a plist FILENAME and a
# crontab marker, so it is whitelisted rather than escaped. Same shape as the
# ship run-id whitelist, and for the same reason: the safe set is small and
# everything outside it is somebody else's quoting bug.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_CEREMONY_SCHEMA = """
CREATE TABLE IF NOT EXISTS ceremonies (
    session_id TEXT NOT NULL,
    name TEXT NOT NULL,
    ceremony_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (session_id, name)
);
CREATE TABLE IF NOT EXISTS ceremony_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    ceremony TEXT NOT NULL,
    fired_at TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    scheduled INTEGER NOT NULL DEFAULT 0,
    duration_s REAL NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    run_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ceremony_runs_lookup
    ON ceremony_runs (session_id, ceremony, fired_at);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def valid_name(name: str) -> bool:
    """True when ``name`` is safe to put in a job label, a filename and a cron line."""
    return bool(_NAME_RE.match(name or ""))


def month_key(stamp: str) -> str:
    """The ``YYYY-MM`` an ISO timestamp falls in ('' when unparseable)."""
    return stamp[:7] if len(stamp or "") >= 7 else ""


def _ceremony_to_json(ceremony: Ceremony) -> str:
    return json.dumps(asdict(ceremony), ensure_ascii=False)


def _dict_to_ceremony(data: dict) -> Ceremony:
    """Rebuild the frozen record from JSON; tolerant of missing keys.

    Tolerant on purpose: a ceremony declared by a newer yeaboi and read by an
    older one should lose the field it does not know, not stop firing.
    """
    return Ceremony(
        session_id=str(data.get("session_id", "")),
        name=str(data.get("name", "")),
        mode=str(data.get("mode", "")),
        args=tuple(
            (str(pair[0]), str(pair[1]))
            for pair in data.get("args") or ()
            if isinstance(pair, list | tuple) and len(pair) >= 2
        ),
        weekdays=str(data.get("weekdays", "1-5")),
        at=str(data.get("at", "09:00")),
        channels=tuple(str(c) for c in data.get("channels") or ("terminal",)),
        enabled=bool(data.get("enabled", True)),
        stale_after_min=int(data.get("stale_after_min", 120)),
        monthly_cap_usd=float(data.get("monthly_cap_usd", 0.0)),
        last_fired_at=str(data.get("last_fired_at", "")),
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
    )


def _dict_to_run(data: dict) -> CeremonyRun:
    return CeremonyRun(
        ceremony=str(data.get("ceremony", "")),
        session_id=str(data.get("session_id", "")),
        fired_at=str(data.get("fired_at", "")),
        outcome=str(data.get("outcome", "")),
        scheduled=bool(data.get("scheduled", False)),
        duration_s=float(data.get("duration_s", 0.0)),
        cost_usd=float(data.get("cost_usd", 0.0)),
        delivery=tuple(
            (str(pair[0]), bool(pair[1]))
            for pair in data.get("delivery") or ()
            if isinstance(pair, list | tuple) and len(pair) >= 2
        ),
        detail=str(data.get("detail", "")),
        error=str(data.get("error", "")),
    )


class CeremonyStore:
    """Declared ceremonies + their run ledger, in the shared sessions database."""

    def __init__(self, db_path: Path | None = None) -> None:
        # Lazy import so tests that monkeypatch yeaboi.paths.get_db_path
        # redirect this store too (the ship/provenance store convention).
        from yeaboi.paths import get_db_path

        self._path = db_path or get_db_path()
        self._conn = sqlite3.connect(str(self._path))
        self._conn.executescript(_CEREMONY_SCHEMA)

    def __enter__(self) -> CeremonyStore:
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

    # -- ceremonies --------------------------------------------------------

    def save(self, ceremony: Ceremony) -> Ceremony:
        """Insert or replace one ceremony. Returns the stamped record.

        Validation lives here rather than at each surface: the TUI, the CLI and
        a future importer all write through this one method, and a name that is
        rejected in one place and accepted in another is how a plist ends up
        named after something nobody can remove.

        **Everything the reading surfaces will later parse is validated here**,
        because a row that stores cleanly and crashes on read is worse than a
        rejected write. ``at`` and ``weekdays`` are re-parsed by the installer,
        by ``cadence_label`` in every listing and by ``next_fire`` on the TUI
        page — so an unparseable one is not a bad row, it is a row that takes
        the Ceremonies screen down every time it is drawn, recoverable only by
        removing it from the terminal.
        """
        if not ceremony.session_id:
            raise ValueError("a ceremony needs a session")
        if not valid_name(ceremony.name):
            raise ValueError(
                f"ceremony name {ceremony.name!r} must be lowercase letters, digits, dot, dash or underscore "
                "(it becomes a scheduled-job label and a filename)"
            )
        from yeaboi.ceremonies.catalog import lookup, refuse_reason
        from yeaboi.ceremonies.delivery import ALL_CHANNELS
        from yeaboi.ceremonies.scheduler import parse_time, weekday_list

        if lookup(ceremony.mode) is None:
            raise ValueError(refuse_reason(ceremony.mode))
        if not ceremony.channels:
            raise ValueError("a ceremony with no delivery channel would run and tell nobody")
        # A misspelled channel is the quieter version of no channel at all: the
        # fan-out records it as undelivered and the run still reports "ok".
        unknown = [c for c in ceremony.channels if c not in ALL_CHANNELS]
        if unknown:
            raise ValueError(
                f"unknown delivery channel(s) {', '.join(map(repr, unknown))} — choose from {', '.join(ALL_CHANNELS)}"
            )
        try:
            parse_time(ceremony.at)
        except ValueError as exc:
            raise ValueError(f"invalid time {ceremony.at!r} — use HH:MM in 24-hour form") from exc
        try:
            days = weekday_list(ceremony.weekdays)
        except ValueError as exc:
            raise ValueError(
                f"invalid weekdays {ceremony.weekdays!r} — use numbers Mon=1..Sun=7, e.g. '1-5' or '1,3,5'"
            ) from exc
        if any(day < 1 or day > 7 for day in days):
            raise ValueError(f"invalid weekdays {ceremony.weekdays!r} — days run Mon=1 to Sun=7")

        existing = self.get(ceremony.session_id, ceremony.name)
        stamped = replace(
            ceremony,
            created_at=ceremony.created_at or (existing.created_at if existing else "") or _now(),
            updated_at=_now(),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO ceremonies "
            "(session_id, name, ceremony_json, enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                stamped.session_id,
                stamped.name,
                _ceremony_to_json(stamped),
                1 if stamped.enabled else 0,
                stamped.created_at,
                stamped.updated_at,
            ),
        )
        self._conn.commit()
        logger.info(
            "ceremony saved: %s/%s (%s, enabled=%s)", stamped.session_id, stamped.name, stamped.mode, stamped.enabled
        )
        return stamped

    def get(self, session_id: str, name: str) -> Ceremony | None:
        row = self._conn.execute(
            "SELECT ceremony_json FROM ceremonies WHERE session_id = ? AND name = ?",
            (session_id, name),
        ).fetchone()
        if row is None:
            return None
        try:
            return _dict_to_ceremony(json.loads(row[0]))
        except ValueError:
            logger.warning("corrupt ceremony_json for %s/%s", session_id, name)
            return None

    def list(self, session_id: str = "") -> list[Ceremony]:
        """Declared ceremonies, by name. Blank session lists every session's."""
        if session_id:
            rows = self._conn.execute(
                "SELECT ceremony_json FROM ceremonies WHERE session_id = ? ORDER BY name",
                (session_id,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT ceremony_json FROM ceremonies ORDER BY session_id, name").fetchall()
        out: list[Ceremony] = []
        for (raw,) in rows:
            try:
                out.append(_dict_to_ceremony(json.loads(raw)))
            except ValueError:
                continue
        return out

    def remove(self, session_id: str, name: str) -> bool:
        """Forget one ceremony. False when there was nothing to forget.

        The declaration only — tearing down the OS job is the scheduler's half,
        and the caller does both. Reporting True for a delete that removed
        nothing is how a job outlives the thing that describes it.
        """
        cursor = self._conn.execute(
            "DELETE FROM ceremonies WHERE session_id = ? AND name = ?",
            (session_id, name),
        )
        self._conn.commit()
        removed = cursor.rowcount > 0
        logger.info("ceremony remove: %s/%s removed=%s", session_id, name, removed)
        return removed

    def set_enabled(self, session_id: str, name: str, enabled: bool) -> Ceremony | None:
        """Pause/resume one ceremony. None when it does not exist."""
        current = self.get(session_id, name)
        if current is None:
            return None
        return self.save(replace(current, enabled=enabled))

    def mark_fired(self, session_id: str, name: str, when: str = "") -> None:
        """Stamp ``last_fired_at``. Silent when the ceremony is already gone.

        Silent because this runs *after* a fire: a ceremony removed while its
        run was in flight should not turn a delivered standup into an error.
        """
        current = self.get(session_id, name)
        if current is None:
            logger.info("mark_fired: %s/%s no longer declared", session_id, name)
            return
        self.save(replace(current, last_fired_at=when or _now()))

    # -- the run ledger ----------------------------------------------------

    def record_run(self, run: CeremonyRun) -> CeremonyRun:
        """Append one run to the ledger. Never raises on an unknown outcome —
        an outcome we cannot name is still a run that happened."""
        if run.outcome not in CEREMONY_OUTCOMES:
            logger.warning("recording ceremony run with unknown outcome %r", run.outcome)
        stamped = replace(run, fired_at=run.fired_at or _now())
        self._conn.execute(
            "INSERT INTO ceremony_runs "
            "(session_id, ceremony, fired_at, outcome, scheduled, duration_s, cost_usd, run_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stamped.session_id,
                stamped.ceremony,
                stamped.fired_at,
                stamped.outcome,
                1 if stamped.scheduled else 0,
                stamped.duration_s,
                stamped.cost_usd,
                json.dumps(asdict(stamped), ensure_ascii=False),
            ),
        )
        self._conn.commit()
        logger.info(
            "ceremony run recorded: %s/%s outcome=%s cost=%.4f",
            stamped.session_id,
            stamped.ceremony,
            stamped.outcome,
            stamped.cost_usd,
        )
        return stamped

    def runs(self, session_id: str, ceremony: str = "", *, limit: int = 20) -> list[CeremonyRun]:
        """Ledger rows, newest first. Blank ``ceremony`` covers the session."""
        sql = "SELECT run_json FROM ceremony_runs WHERE session_id = ?"
        params: list = [session_id]
        if ceremony:
            sql += " AND ceremony = ?"
            params.append(ceremony)
        sql += " ORDER BY fired_at DESC, id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        out: list[CeremonyRun] = []
        for (raw,) in self._conn.execute(sql, params).fetchall():
            try:
                out.append(_dict_to_run(json.loads(raw)))
            except ValueError:
                continue
        return out

    def last_run(self, session_id: str, ceremony: str) -> CeremonyRun | None:
        rows = self.runs(session_id, ceremony, limit=1)
        return rows[0] if rows else None

    def month_spend(self, session_id: str, ceremony: str, month: str = "") -> float:
        """This ceremony's recorded spend in ``month`` (``YYYY-MM``, default now).

        Both outcomes that reach an engine count. A guard declines *before* the
        engine is called, so a ``skipped_*`` row spent nothing and is excluded —
        counting one would let the cap latch permanently the first time it bit,
        on money that was never spent.

        ``failed`` is inside the sum, and that is the half worth writing down:
        an engine that makes its LLM call and then raises on the way out — a
        renderer change, a dead export path — has already spent the money.
        Filtering to ``ok`` would let a ceremony burn its cap every day forever
        without the cap ever seeing a cent.
        """
        target = month or month_key(_now())
        row = self._conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM ceremony_runs "
            "WHERE session_id = ? AND ceremony = ? AND outcome IN ('ok', 'failed') AND fired_at LIKE ?",
            (session_id, ceremony, f"{target}%"),
        ).fetchone()
        return float(row[0] or 0.0)
