"""Persistence for Niko conversations.

Shares ``sessions.db`` the way the ceremonies/ship/agentwatch stores do: an
additive ``CREATE TABLE IF NOT EXISTS`` schema executed on open — self-healing,
no ``CURRENT_SCHEMA_VERSION`` bump.

Two tables. ``niko_conversations`` is the thread; ``niko_messages`` is every
turn in it, tool calls included. Storing the tool calls is the point: a
conversation replayed without them shows an answer with no visible reason for
it, and "which numbers did you read?" stops being answerable one restart later.

Archiving rather than deleting, because a conversation is a record of what the
user was told. ``purge`` exists for the saved-sessions hub, where deleting is
what the user actually asked for.

One store instance owns one SQLite connection and is not shared across threads.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from yeaboi.agent.state import NikoConversation, NikoMessage, NikoToolCall

logger = logging.getLogger(__name__)

_NIKO_SCHEMA = """
CREATE TABLE IF NOT EXISTS niko_conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS niko_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    content TEXT NOT NULL DEFAULT '',
    tool_calls_json TEXT NOT NULL DEFAULT '[]',
    route TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_niko_messages_lookup
    ON niko_messages (conversation_id, created_at);
"""

#: Longest title kept. The auto-titler is asked for 3-5 words; this is the
#: guard against a model that answers with a paragraph.
MAX_TITLE_CHARS = 120


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _calls_to_json(calls: tuple[NikoToolCall, ...]) -> str:
    return json.dumps([asdict(call) for call in calls], ensure_ascii=False, default=str)


def _json_to_calls(raw: str) -> tuple[NikoToolCall, ...]:
    """Rebuild the frozen tool records; tolerant of missing keys.

    Tolerant on purpose: a row written by a newer yeaboi should lose the field
    an older one does not know, not make the whole conversation unopenable.
    """
    try:
        rows = json.loads(raw or "[]")
    except (TypeError, ValueError):
        logger.warning("niko: unreadable tool_calls_json, dropping")
        return ()
    if not isinstance(rows, list):
        return ()
    return tuple(
        NikoToolCall(
            name=str(row.get("name", "")),
            arguments=row.get("arguments") if isinstance(row.get("arguments"), dict) else {},
            ok=bool(row.get("ok", True)),
            result=row.get("result"),
            error=str(row.get("error", "")),
        )
        for row in rows
        if isinstance(row, dict)
    )


class NikoStore:
    """Niko's conversations and their turns, in the shared sessions database."""

    def __init__(self, db_path: Path | None = None) -> None:
        # Lazy import so tests that monkeypatch yeaboi.paths.get_db_path
        # redirect this store too (the ceremonies/ship store convention).
        from yeaboi.paths import get_db_path

        self._path = db_path or get_db_path()
        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_NIKO_SCHEMA)

    def __enter__(self) -> NikoStore:
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

    # -- conversations -----------------------------------------------------

    def create(self, *, title: str = "") -> NikoConversation:
        """Open a new conversation and return it."""
        stamp = _now()
        conversation = NikoConversation(
            id=uuid.uuid4().hex, title=title[:MAX_TITLE_CHARS], created_at=stamp, updated_at=stamp
        )
        self._conn.execute(
            "INSERT INTO niko_conversations (id, title, created_at, updated_at, archived) VALUES (?, ?, ?, ?, 0)",
            (conversation.id, conversation.title, stamp, stamp),
        )
        self._conn.commit()
        logger.info("niko: conversation opened id=%s", conversation.id)
        return conversation

    def get(self, conversation_id: str) -> NikoConversation | None:
        """One conversation, or None when it does not exist."""
        row = self._conn.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM niko_messages m WHERE m.conversation_id = c.id) AS n "
            "FROM niko_conversations c WHERE c.id = ?",
            (conversation_id,),
        ).fetchone()
        return _row_to_conversation(row) if row else None

    def conversations(self, *, limit: int = 20, include_archived: bool = False) -> list[NikoConversation]:
        """Conversations, most recently used first."""
        clause = "" if include_archived else "WHERE c.archived = 0"
        rows = self._conn.execute(
            f"SELECT c.*, (SELECT COUNT(*) FROM niko_messages m WHERE m.conversation_id = c.id) AS n "  # noqa: S608 - `clause` is a literal chosen here, never caller input
            f"FROM niko_conversations c {clause} ORDER BY c.updated_at DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [_row_to_conversation(row) for row in rows]

    def set_title(self, conversation_id: str, title: str) -> None:
        """Name a conversation. Called once, from the opening question."""
        self._conn.execute(
            "UPDATE niko_conversations SET title = ? WHERE id = ?",
            (title[:MAX_TITLE_CHARS], conversation_id),
        )
        self._conn.commit()

    def archive(self, conversation_id: str) -> bool:
        """Hide a conversation. Returns False when there was nothing to hide."""
        cursor = self._conn.execute(
            "UPDATE niko_conversations SET archived = 1 WHERE id = ? AND archived = 0", (conversation_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def purge(self, conversation_id: str) -> bool:
        """Delete a conversation and its turns for good."""
        self._conn.execute("DELETE FROM niko_messages WHERE conversation_id = ?", (conversation_id,))
        cursor = self._conn.execute("DELETE FROM niko_conversations WHERE id = ?", (conversation_id,))
        self._conn.commit()
        if cursor.rowcount > 0:
            logger.info("niko: conversation purged id=%s", conversation_id)
        return cursor.rowcount > 0

    # -- messages ----------------------------------------------------------

    def add_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str = "",
        tool_calls: tuple[NikoToolCall, ...] = (),
        route: str = "",
    ) -> NikoMessage:
        """Append one turn and bump the conversation's ``updated_at``."""
        stamp = _now()
        message = NikoMessage(
            id=uuid.uuid4().hex,
            conversation_id=conversation_id,
            role=role,
            content=content,
            tool_calls=tuple(tool_calls),
            route=route,
            created_at=stamp,
        )
        self._conn.execute(
            "INSERT INTO niko_messages (id, conversation_id, role, content, tool_calls_json, route, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                message.id,
                conversation_id,
                role,
                content,
                _calls_to_json(message.tool_calls),
                route,
                stamp,
            ),
        )
        self._conn.execute("UPDATE niko_conversations SET updated_at = ? WHERE id = ?", (stamp, conversation_id))
        self._conn.commit()
        return message

    def messages(self, conversation_id: str, *, limit: int = 200) -> list[NikoMessage]:
        """Every turn in a conversation, oldest first."""
        rows = self._conn.execute(
            "SELECT * FROM niko_messages WHERE conversation_id = ? ORDER BY created_at, rowid LIMIT ?",
            (conversation_id, max(1, int(limit))),
        ).fetchall()
        return [
            NikoMessage(
                id=row["id"],
                conversation_id=row["conversation_id"],
                role=row["role"],
                content=row["content"],
                tool_calls=_json_to_calls(row["tool_calls_json"]),
                route=row["route"],
                created_at=row["created_at"],
            )
            for row in rows
        ]


def _row_to_conversation(row: sqlite3.Row) -> NikoConversation:
    return NikoConversation(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        archived=bool(row["archived"]),
        message_count=int(row["n"] if "n" in row.keys() else 0),
    )
