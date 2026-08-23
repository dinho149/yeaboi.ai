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

CREATE TABLE IF NOT EXISTS slack_inbound (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key  TEXT NOT NULL,
    channel    TEXT NOT NULL DEFAULT '',
    anchor_ts  TEXT NOT NULL DEFAULT '',
    act        TEXT NOT NULL DEFAULT '',
    intent     TEXT NOT NULL DEFAULT '',
    slack_user TEXT NOT NULL DEFAULT '',
    member     TEXT NOT NULL DEFAULT '',
    outcome    TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    claimed_at TEXT NOT NULL DEFAULT '',
    settled_at TEXT NOT NULL DEFAULT '',
    UNIQUE(event_key)
);
CREATE INDEX IF NOT EXISTS idx_slack_inbound_claimed ON slack_inbound (claimed_at);

CREATE TABLE IF NOT EXISTS slack_identities (
    session_id TEXT NOT NULL,
    slack_user TEXT NOT NULL,
    member     TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (session_id, slack_user)
);

CREATE TABLE IF NOT EXISTS slack_polls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    polled_at       TEXT NOT NULL DEFAULT '',
    window_start    TEXT NOT NULL DEFAULT '',
    outcome         TEXT NOT NULL DEFAULT '',
    messages_read   INTEGER NOT NULL DEFAULT 0,
    events_seen     INTEGER NOT NULL DEFAULT 0,
    events_new      INTEGER NOT NULL DEFAULT 0,
    events_applied  INTEGER NOT NULL DEFAULT 0,
    duration_s      REAL NOT NULL DEFAULT 0,
    detail          TEXT NOT NULL DEFAULT '',
    error           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_slack_polls_recent ON slack_polls (polled_at);
"""

# What a considered event ended up as. Every one is recorded, including the
# refusals: the fleet learned the expensive version of this lesson, where the
# *fact* of a refusal was durable and the *reason* was free text nobody kept.
# "You are not on the list", "I could not tell what you meant" and "the write
# said no" are different problems, and only some of them are anyone's to fix.
OUTCOME_CLAIMED = ""  # claimed, not yet settled — a crash leaves this behind
OUTCOME_APPLIED = "applied"
OUTCOME_IGNORED = "ignored"  # not part of the grammar, or not for us
OUTCOME_UNAUTHORIZED = "unauthorized"
OUTCOME_STALE = "stale"  # the anchor has expired
OUTCOME_REFUSED = "refused"  # the write path itself said no
OUTCOME_FAILED = "failed"
#: Applied, but only the half of it nobody else was writing. Today that is a
#: thumbs-down on a report somebody has open in an editable share: the permanent
#: excusal lands, and the removal from *that* report waits for the next run.
#: A word of its own because "we did what you asked, later than you expected" is
#: a different fact from "the write said no", and only one of them is a problem.
OUTCOME_DEFERRED = "deferred"

#: How many corrections one anchor may accept in a day. A bot loop or a thread
#: argument must not be able to walk a document to ``MAX_ANNOTATIONS``, and the
#: bound belongs on the *anchor* rather than on the channel: one noisy standup
#: thread should not spend the whole day's budget for every other ceremony.
#:
#: Corrections only, deliberately. A cap that refused a *pause* because twenty
#: notes were written that day would disarm the one act whose entire purpose is
#: stopping something — and the other two acts are already bounded, by three
#: emoji and by one signal reply each.
MAX_CORRECTIONS_PER_DAY = 20

POLL_OK = "ok"
POLL_FAILED = "failed"
POLL_NO_TOKEN = "skipped_no_token"  # noqa: S105 — an outcome label, not a credential
POLL_NO_CHANNEL = "skipped_no_channel"
POLL_NO_ALLOWLIST = "skipped_no_allowlist"
POLL_LOCKED = "skipped_locked"


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
        """True once this anchor is too old to act on (unparseable = expired).

        Both sides are forced to aware-UTC before they are compared. Every row
        this package writes carries an offset, but a caller passing a naive
        ``now`` would otherwise raise ``TypeError`` mid-comparison — and from
        ``_handle`` that escapes all the way out to ``run_poll``'s handler and
        fails the whole poll over one anchor's arithmetic. This is documented
        as tolerant, so it has to be tolerant of that too.
        """
        if not self.expires_at:
            return False
        try:
            deadline = datetime.fromisoformat(self.expires_at)
        except ValueError:
            logger.warning("slack anchor %s/%s has an unparseable expires_at", self.channel, self.ts)
            return True
        moment = now or datetime.now(UTC)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return moment >= deadline


@dataclass(frozen=True)
class InboundEvent:
    """One thing an allowlisted human did to a message yeaboi posted.

    ``event_key`` is the identity the ledger dedupes on, and it is derived
    entirely from Slack's own facts — the channel, the message, who acted and
    what they did — so replaying a window produces the same keys and therefore
    no second write. Un-reacting and re-reacting is a fidget, not a second
    instruction.
    """

    event_key: str = ""
    channel: str = ""
    anchor_ts: str = ""  # the message being answered (ours)
    reply_ts: str = ""  # the reply carrying the answer, when there is one
    act: str = ""
    intent: str = ""
    payload: str = ""  # a correction's text; empty otherwise
    slack_user: str = ""
    member: str = ""  # resolved roster name, when one is known
    anchor: SlackAnchor | None = None


def reaction_key(channel: str, ts: str, actor: str, emoji: str) -> str:
    return f"react:{channel}:{ts}:{actor}:{emoji}"


def reply_key(channel: str, reply_ts: str) -> str:
    """One message, one interpretation — so a verb and a correction share a key."""
    return f"reply:{channel}:{reply_ts}"


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

    def claim(self, event: InboundEvent, *, now: datetime | None = None) -> bool:
        """Take ownership of one event. True means this run should act on it.

        ``INSERT OR IGNORE`` on ``event_key``, and the rowcount **is** the
        answer — which is what makes the overlapping read window free. The poll
        re-reads the same 48 hours every time (a gap after a failed run would
        drop somebody's vote on the floor), so almost every event it sees has
        already been handled, and saying so costs one refused insert.

        Claimed *before* acting, deliberately. A crash between the two loses one
        event and leaves a visible unsettled row; the other order double-applies
        a write. Every act here mutates something, so at-most-once with a
        visible remainder is the right way round.
        """
        cur = self._conn.execute(
            """INSERT OR IGNORE INTO slack_inbound
               (event_key, channel, anchor_ts, act, intent, slack_user, member, claimed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_key,
                event.channel,
                event.anchor_ts,
                event.act,
                event.intent,
                event.slack_user,
                event.member,
                _stamp(now or _now()),
            ),
        )
        self._conn.commit()
        return bool(cur.rowcount)

    def settle(self, event_key: str, *, outcome: str, reason: str = "", now: datetime | None = None) -> None:
        """Record what happened to a claimed event."""
        self._conn.execute(
            "UPDATE slack_inbound SET outcome = ?, reason = ?, settled_at = ? WHERE event_key = ?",
            (outcome, reason[:500], _stamp(now or _now()), event_key),
        )
        self._conn.commit()
        logger.info("slack event %s → %s%s", event_key, outcome, f" ({reason})" if reason else "")

    def settled_count(self, *, channel: str, anchor_ts: str, act: str, since: str, outcomes: tuple[str, ...]) -> int:
        """How many events of one act on one anchor settled a given way since ``since``.

        One ``COUNT(*)`` over columns the ledger already carries, so the cap
        costs no schema. The claim is written before the act runs and carries
        ``outcome = ''``, so counting only settled rows excludes the event
        currently being decided — which is what makes "the twenty-first is
        refused" mean what it says.
        """
        if not outcomes:
            return 0
        slots = ", ".join("?" for _ in outcomes)
        row = self._conn.execute(
            f"""SELECT COUNT(*) AS n FROM slack_inbound
                WHERE channel = ? AND anchor_ts = ? AND act = ?
                  AND outcome IN ({slots}) AND claimed_at >= ?""",  # noqa: S608 — placeholders, not values
            (channel, anchor_ts, act, *outcomes, since),
        ).fetchone()
        return int(row["n"]) if row else 0

    def unsettled(self, *, limit: int = 50) -> list[dict]:
        """Events claimed but never settled — what a crash mid-apply leaves."""
        rows = self._conn.execute(
            "SELECT * FROM slack_inbound WHERE outcome = '' ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def history(self, *, limit: int = 20) -> list[dict]:
        """The most recent inbound events, newest first."""
        rows = self._conn.execute("SELECT * FROM slack_inbound ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ── identities ────────────────────────────────────────────────────────
    #
    # The one thing in this package that a human curates, and the only table
    # ``prune`` deliberately does not touch: the other three are telemetry with
    # a shelf life, this is configuration somebody typed.

    def link_identity(self, session_id: str, slack_user: str, member: str, *, now: datetime | None = None) -> None:
        """Bind one Slack id to one roster name. Re-linking replaces."""
        self._conn.execute(
            """INSERT INTO slack_identities (session_id, slack_user, member, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(session_id, slack_user) DO UPDATE SET
                   member = excluded.member, created_at = excluded.created_at""",
            (session_id, slack_user.upper(), member, _stamp(now or _now())),
        )
        self._conn.commit()
        logger.info("slack identity: %s → %s in session %s", slack_user.upper(), member, session_id)

    def unlink_identity(self, session_id: str, slack_user: str) -> bool:
        """Drop one binding. False when there was nothing to drop."""
        cur = self._conn.execute(
            "DELETE FROM slack_identities WHERE session_id = ? AND slack_user = ?",
            (session_id, slack_user.upper()),
        )
        self._conn.commit()
        return bool(cur.rowcount)

    def identity(self, session_id: str, slack_user: str) -> str:
        """The roster name bound to a Slack id, or '' when there is none."""
        row = self._conn.execute(
            "SELECT member FROM slack_identities WHERE session_id = ? AND slack_user = ?",
            (session_id, slack_user.upper()),
        ).fetchone()
        return str(row["member"]) if row else ""

    def identities(self, session_id: str) -> list[dict]:
        """Every binding in one session, in the order they were made."""
        rows = self._conn.execute(
            """SELECT slack_user, member, created_at FROM slack_identities
               WHERE session_id = ? ORDER BY created_at, slack_user""",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def record_poll(self, poll: dict) -> None:
        """One row per poll, whatever happened — including the declines.

        The ceremonies ledger's discipline, for the same reason: a job that
        fires unattended and stops working is indistinguishable from one that
        had nothing to do, unless it says which.
        """
        self._conn.execute(
            """INSERT INTO slack_polls
               (polled_at, window_start, outcome, messages_read, events_seen,
                events_new, events_applied, duration_s, detail, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                poll.get("polled_at", _stamp(_now())),
                poll.get("window_start", ""),
                poll.get("outcome", ""),
                int(poll.get("messages_read", 0)),
                int(poll.get("events_seen", 0)),
                int(poll.get("events_new", 0)),
                int(poll.get("events_applied", 0)),
                float(poll.get("duration_s", 0.0)),
                str(poll.get("detail", ""))[:500],
                str(poll.get("error", ""))[:500],
            ),
        )
        self._conn.commit()

    def polls(self, *, limit: int = 20) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM slack_polls ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def last_poll(self, *, ok_only: bool = True) -> dict | None:
        clause = "WHERE outcome = 'ok' " if ok_only else ""
        row = self._conn.execute(f"SELECT * FROM slack_polls {clause}ORDER BY id DESC LIMIT 1").fetchone()  # noqa: S608
        return dict(row) if row else None

    def prune(self, *, keep_days: int = 30, now: datetime | None = None) -> int:
        """Drop rows older than ``keep_days``; return how many went.

        A job that fires every ten minutes and never collects its own garbage
        grows without limit, so this runs at the tail of a successful poll.
        """
        cutoff = _stamp((now or _now()) - timedelta(days=keep_days))
        gone = 0
        for table, column in (
            ("slack_anchors", "posted_at"),
            ("slack_inbound", "claimed_at"),
            ("slack_polls", "polled_at"),
        ):
            cur = self._conn.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff,))  # noqa: S608 — literal names
            gone += cur.rowcount or 0
        self._conn.commit()
        return gone

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
