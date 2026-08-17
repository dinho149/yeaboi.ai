"""Persistence for the two-way Slack lane.

Shares ``sessions.db`` the way the ceremonies/ship/artifacts stores do: an
additive ``CREATE TABLE IF NOT EXISTS`` schema executed on open — self-healing,
no ``CURRENT_SCHEMA_VERSION`` bump, which also leaves the Go sidecar's schema
ceiling untouched.

``slack_anchors`` is the whole semantic layer. A row says "this Slack message
is *that* run of *that* ceremony", and every inbound event resolves through it
rather than through anything a human typed. Two shapes:

- ``kind='post'`` — the dispatch itself. A reaction here means ceremony
  control, because a ceremony is what the post is about.
- ``kind='signal'`` — one threaded reply per votable practice signal, carrying
  the ``member`` and ``rule`` it is about. A reaction here is a verdict, and
  ``(session_id, run_id, member, rule)`` is already exactly
  ``practice_feedback.apply_verdict``'s signature.

Splitting them is what makes 👍/👎 unambiguous at all: a standup post carries
every member's signals in one block of plaintext, so a reaction on it cannot
say which one it means — and inferring one is the guess ``habits.py`` exists to
refuse.

One store instance owns one SQLite connection and is not shared across threads.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

#: How long an anchor stays answerable. A reaction on an ancient post should
#: resolve to nothing: ``apply_verdict`` would refuse a stale run anyway, but a
#: stale *ceremony control* would happily fire.
ANCHOR_TTL_DAYS = 7

KIND_POST = "post"
KIND_SIGNAL = "signal"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS slack_anchors (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    channel       TEXT NOT NULL,
    ts            TEXT NOT NULL,
    root_ts       TEXT NOT NULL DEFAULT '',
    kind          TEXT NOT NULL DEFAULT 'post',
    session_id    TEXT NOT NULL DEFAULT '',
    ceremony      TEXT NOT NULL DEFAULT '',
    mode          TEXT NOT NULL DEFAULT '',
    artifact_kind TEXT NOT NULL DEFAULT '',
    run_id        INTEGER NOT NULL DEFAULT 0,
    member        TEXT NOT NULL DEFAULT '',
    rule          TEXT NOT NULL DEFAULT '',
    posted_at     TEXT NOT NULL DEFAULT '',
    expires_at    TEXT NOT NULL DEFAULT '',
    anchor_json   TEXT NOT NULL DEFAULT '{}',
    UNIQUE(channel, ts)
);
CREATE INDEX IF NOT EXISTS idx_slack_anchors_recent ON slack_anchors (posted_at);
CREATE INDEX IF NOT EXISTS idx_slack_anchors_root ON slack_anchors (channel, root_ts);
"""


@dataclass(frozen=True)
class SlackAnchor:
    """One yeaboi message in Slack, and what answering it would mean."""

    channel: str = ""
    ts: str = ""
    root_ts: str = ""
    kind: str = KIND_POST
    session_id: str = ""
    ceremony: str = ""
    mode: str = ""
    artifact_kind: str = ""
    run_id: int = 0
    member: str = ""
    rule: str = ""
    posted_at: str = ""
    expires_at: str = ""

    @property
    def is_signal(self) -> bool:
        return self.kind == KIND_SIGNAL

    def expired(self, now: datetime | None = None) -> bool:
        """True once this anchor is too old to act on (unparseable = expired)."""
        if not self.expires_at:
            return False
        try:
            deadline = datetime.fromisoformat(self.expires_at)
        except ValueError:
            logger.warning("slack anchor %s/%s has an unparseable expires_at", self.channel, self.ts)
            return True
        return (now or datetime.now(UTC)) >= deadline


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _dict_to_anchor(data: dict) -> SlackAnchor:
    """Rebuild the frozen record; tolerant of missing keys.

    Tolerant on purpose, for the reason the ceremony hydrator is: a row written
    by a newer yeaboi and read by an older one should lose the field it does not
    know, not raise on a poll nobody is watching.
    """
    return SlackAnchor(
        channel=str(data.get("channel", "")),
        ts=str(data.get("ts", "")),
        root_ts=str(data.get("root_ts", "")),
        kind=str(data.get("kind", KIND_POST)),
        session_id=str(data.get("session_id", "")),
        ceremony=str(data.get("ceremony", "")),
        mode=str(data.get("mode", "")),
        artifact_kind=str(data.get("artifact_kind", "")),
        run_id=int(data.get("run_id", 0) or 0),
        member=str(data.get("member", "")),
        rule=str(data.get("rule", "")),
        posted_at=str(data.get("posted_at", "")),
        expires_at=str(data.get("expires_at", "")),
    )


class SlackStore:
    """Anchors for the two-way lane. Context manager; one connection."""

    def __init__(self, db_path: Path | None = None) -> None:
        from yeaboi.paths import get_db_path

        self.db_path = db_path or get_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def __enter__(self) -> SlackStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:  # pragma: no cover — closing twice is not an error worth raising
            logger.debug("slack store: close raised", exc_info=True)

    # ── writes ────────────────────────────────────────────────────────────

    def record_anchor(self, anchor: SlackAnchor, *, now: datetime | None = None) -> SlackAnchor:
        """Store one anchor, stamping ``posted_at``/``expires_at`` if unset.

        ``INSERT OR REPLACE`` on ``(channel, ts)``: a Slack ts is unique within
        a channel, so re-recording one is always a retry of the same post.
        """
        moment = now or _now()
        stamped = replace(
            anchor,
            posted_at=anchor.posted_at or _stamp(moment),
            expires_at=anchor.expires_at or _stamp(moment + timedelta(days=ANCHOR_TTL_DAYS)),
        )
        self._conn.execute(
            """INSERT OR REPLACE INTO slack_anchors
               (channel, ts, root_ts, kind, session_id, ceremony, mode, artifact_kind,
                run_id, member, rule, posted_at, expires_at, anchor_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                stamped.channel,
                stamped.ts,
                stamped.root_ts,
                stamped.kind,
                stamped.session_id,
                stamped.ceremony,
                stamped.mode,
                stamped.artifact_kind,
                stamped.run_id,
                stamped.member,
                stamped.rule,
                stamped.posted_at,
                stamped.expires_at,
                json.dumps(asdict(stamped), ensure_ascii=False),
            ),
        )
        self._conn.commit()
        logger.info(
            "slack anchor recorded: %s/%s kind=%s ceremony=%s run=%d",
            stamped.channel,
            stamped.ts,
            stamped.kind,
            stamped.ceremony or "-",
            stamped.run_id,
        )
        return stamped

    def prune(self, *, keep_days: int = 30, now: datetime | None = None) -> int:
        """Drop anchors older than ``keep_days``; return how many went."""
        cutoff = _stamp((now or _now()) - timedelta(days=keep_days))
        cur = self._conn.execute("DELETE FROM slack_anchors WHERE posted_at < ?", (cutoff,))
        self._conn.commit()
        return cur.rowcount or 0

    # ── reads ─────────────────────────────────────────────────────────────

    def anchor(self, channel: str, ts: str) -> SlackAnchor | None:
        """The anchor for one message, or None when we did not post it."""
        row = self._conn.execute(
            "SELECT anchor_json FROM slack_anchors WHERE channel = ? AND ts = ?", (channel, ts)
        ).fetchone()
        return _dict_to_anchor(json.loads(row["anchor_json"])) if row else None

    def anchors_since(self, oldest: str) -> list[SlackAnchor]:
        """Every anchor posted at or after ``oldest`` (an ISO stamp), newest first."""
        rows = self._conn.execute(
            "SELECT anchor_json FROM slack_anchors WHERE posted_at >= ? ORDER BY posted_at DESC",
            (oldest,),
        ).fetchall()
        return [_dict_to_anchor(json.loads(r["anchor_json"])) for r in rows]

    def thread(self, channel: str, root_ts: str) -> list[SlackAnchor]:
        """Every signal anchor hanging under one post."""
        rows = self._conn.execute(
            "SELECT anchor_json FROM slack_anchors WHERE channel = ? AND root_ts = ? ORDER BY id",
            (channel, root_ts),
        ).fetchall()
        return [_dict_to_anchor(json.loads(r["anchor_json"])) for r in rows]


def record_post(
    ref,
    *,
    session_id: str = "",
    ceremony: str = "",
    mode: str = "",
    artifact_kind: str = "",
    run_id: int = 0,
    db_path: Path | None = None,
) -> SlackAnchor | None:
    """Record a delivered post as an anchor. Never raises.

    The convenience wrapper the delivering engines call from an ``on_receipt``
    callback. Swallows everything: a store that cannot record an anchor costs
    the team the ability to answer *that* message, and must never cost them the
    message itself.
    """
    if not getattr(ref, "ts", "") or not getattr(ref, "channel", ""):
        return None
    try:
        with SlackStore(db_path) as store:
            return store.record_anchor(
                SlackAnchor(
                    channel=ref.channel,
                    ts=ref.ts,
                    kind=KIND_POST,
                    session_id=session_id,
                    ceremony=ceremony,
                    mode=mode,
                    artifact_kind=artifact_kind,
                    run_id=run_id,
                )
            )
    except (sqlite3.Error, OSError):
        logger.warning("slack: could not record the anchor for %s", getattr(ref, "ts", "?"), exc_info=True)
        return None
