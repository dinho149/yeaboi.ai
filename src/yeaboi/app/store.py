"""Durable application state: users, projects, memberships.

Why SQLite and not the existing ``data/projects.json``. ``persistence.py`` owns
the TUI's model — one machine, one person, whole-file rewrites, last-writer-wins
— and that is correct for a local tool. An application has concurrent writers on
one box and rows that must not be lost when two of them save at once, which is
the one thing a JSON file cannot promise. ``sqlite3`` is in the standard library
and ``langgraph-checkpoint-sqlite`` already put a database next to this one, so
this costs no new dependency.

**This does not replace ``persistence.py``.** The TUI keeps its file store; this
is the app's, in its own file (``app.db``). The two meet only when a project is
imported, which copies rather than shares. Making the TUI depend on a running
server would break ``yeaboi`` offline, and that is not on the table.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from yeaboi.paths import DATA_DIR

#: The app's own database, beside ``sessions.db`` rather than inside it.
APP_DB_PATH = DATA_DIR / "app.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    owner_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS projects_owner ON projects(owner_id);
CREATE TABLE IF NOT EXISTS memberships (
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('owner', 'editor', 'viewer')),
    PRIMARY KEY (project_id, user_id)
);
CREATE TABLE IF NOT EXISTS artifacts (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    -- The report payload, exactly as an exporter would hand it to export_page:
    -- text, numbers and structure, no markup and no presentation. Stored whole
    -- rather than shredded into columns because the bundle's Report switch is
    -- the only thing that reads it, and its shape is that union's business.
    payload     TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS artifacts_project ON artifacts(project_id, created_at DESC);
-- A live ceremony that is running somewhere else.
--
-- The boards are their own servers with their own in-memory state; this table
-- is a *registry*, not a copy. It records where a room is so a teammate can
-- find it, and nothing about what is in it.
--
-- `invite_url` is the participant link and `join_code` the code that goes with
-- it. Neither is a secret: the code is what a reader types to get in, and the
-- URL is the board's public address (see sharing/access.py::invite_payload).
-- The HOST link is deliberately absent and must never be stored here - it
-- carries the admin secret, and every member of a project can read this table.
CREATE TABLE IF NOT EXISTS rooms (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('retro', 'poker')),
    title       TEXT NOT NULL DEFAULT '',
    invite_url  TEXT NOT NULL,
    join_code   TEXT NOT NULL DEFAULT '',
    opened_by   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    opened_at   REAL NOT NULL,
    closed_at   REAL
);
CREATE INDEX IF NOT EXISTS rooms_project ON rooms(project_id, opened_at DESC);
-- One-time sign-in tokens.
--
-- Only the SHA-256 of the token is stored. The raw value exists in the link the
-- user receives and nowhere else, so a stolen database does not hand over the
-- ability to sign in as anyone - which is the whole reason this table is not
-- just an email column.
--
-- `used_at` rather than a delete: a token that has been spent must be
-- distinguishable from one that never existed, or a replayed link and a forged
-- one produce the same answer and neither can be alerted on.
CREATE TABLE IF NOT EXISTS login_tokens (
    token_hash  TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    used_at     REAL
);
CREATE INDEX IF NOT EXISTS login_tokens_email ON login_tokens(email, created_at DESC);
CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
"""


@dataclass(frozen=True)
class User:
    id: str
    email: str
    name: str
    created_at: float


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    owner_id: str
    created_at: float
    updated_at: float
    role: str = "owner"


@dataclass(frozen=True)
class Artifact:
    id: str
    project_id: str
    kind: str
    title: str
    created_at: float
    #: Parsed payload. Absent from list views, which carry only the metadata.
    payload: dict | None = None


@dataclass(frozen=True)
class Room:
    id: str
    project_id: str
    kind: str
    title: str
    invite_url: str
    join_code: str
    opened_by: str
    opened_at: float
    closed_at: float | None = None

    @property
    def live(self) -> bool:
        return self.closed_at is None


#: Ceremonies that have a live room. Mirrors the CHECK constraint above; a
#: third board would need a server before it needed a row here.
ROOM_KINDS = ("retro", "poker")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


class AppStore:
    """The application's durable state.

    One connection per call rather than a shared one: the server is threaded and
    a ``sqlite3.Connection`` is not safe to share across threads without care
    that buys nothing here. WAL is set once so readers never block the writer.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else APP_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        # Enforced per-connection in SQLite, not stored in the file.
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── users ──────────────────────────────────────────────────────────

    def create_user(self, email: str, name: str = "") -> User:
        """Create a user, or return the existing one for that email.

        Idempotent on email because the only sign-in flow so far is "prove you
        own this address", and a second sign-in must not fail or fork an account.
        """
        email = email.strip().lower()
        if not email:
            raise ValueError("email is required")
        existing = self.user_by_email(email)
        if existing:
            return existing
        user = User(id=_new_id("usr"), email=email, name=name.strip(), created_at=time.time())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO users (id, email, name, created_at) VALUES (?, ?, ?, ?)",
                (user.id, user.email, user.name, user.created_at),
            )
        return user

    def count_users(self) -> int:
        """How many accounts exist. Drives the first-run claim, and nothing else."""
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])

    def user_by_email(self, email: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
        return User(**dict(row)) if row else None

    def user(self, user_id: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return User(**dict(row)) if row else None

    # ── projects ───────────────────────────────────────────────────────

    def create_project(self, name: str, owner_id: str) -> Project:
        name = name.strip()
        if not name:
            raise ValueError("project name is required")
        if not self.user(owner_id):
            raise ValueError("no such user")
        now = time.time()
        project = Project(id=_new_id("prj"), name=name, owner_id=owner_id, created_at=now, updated_at=now)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO projects (id, name, owner_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (project.id, project.name, project.owner_id, now, now),
            )
            conn.execute(
                "INSERT INTO memberships (project_id, user_id, role) VALUES (?, ?, 'owner')",
                (project.id, owner_id),
            )
        return project

    def projects_for(self, user_id: str) -> list[Project]:
        """Every project the user can see, newest first, with their role."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT p.*, m.role FROM projects p "
                "JOIN memberships m ON m.project_id = p.id "
                "WHERE m.user_id = ? ORDER BY p.updated_at DESC",
                (user_id,),
            ).fetchall()
        return [Project(**dict(row)) for row in rows]

    def project(self, project_id: str, user_id: str) -> Project | None:
        """One project, **scoped to the asker**.

        There is deliberately no unscoped read. A handler that could fetch by id
        alone would eventually be called with an id from a URL and no membership
        check, which is the standard way an app leaks another tenant's data.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT p.*, m.role FROM projects p "
                "JOIN memberships m ON m.project_id = p.id "
                "WHERE p.id = ? AND m.user_id = ?",
                (project_id, user_id),
            ).fetchone()
        return Project(**dict(row)) if row else None

    def rename_project(self, project_id: str, user_id: str, name: str) -> Project | None:
        name = name.strip()
        if not name:
            raise ValueError("project name is required")
        current = self.project(project_id, user_id)
        if current is None or current.role == "viewer":
            return None
        with self._connect() as conn:
            conn.execute("UPDATE projects SET name = ?, updated_at = ? WHERE id = ?", (name, time.time(), project_id))
        return self.project(project_id, user_id)

    def delete_project(self, project_id: str, user_id: str) -> bool:
        """Delete a project. Owner only — an editor may change it, not destroy it."""
        current = self.project(project_id, user_id)
        if current is None or current.role != "owner":
            return False
        with self._connect() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        return True

    def add_member(self, project_id: str, owner_id: str, user_id: str, role: str = "editor") -> bool:
        if role not in ("owner", "editor", "viewer"):
            raise ValueError(f"unknown role: {role}")
        current = self.project(project_id, owner_id)
        if current is None or current.role != "owner":
            return False
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO memberships (project_id, user_id, role) VALUES (?, ?, ?) "
                "ON CONFLICT(project_id, user_id) DO UPDATE SET role = excluded.role",
                (project_id, user_id, role),
            )
        return True

    def members(self, project_id: str, user_id: str) -> list[dict[str, str]]:
        if self.project(project_id, user_id) is None:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT u.id, u.email, u.name, m.role FROM memberships m "
                "JOIN users u ON u.id = m.user_id WHERE m.project_id = ? ORDER BY m.role, u.email",
                (project_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ── artifacts ──────────────────────────────────────────────────────

    def create_artifact(self, project_id: str, user_id: str, kind: str, title: str, payload: dict) -> Artifact | None:
        """Store a report payload against a project.

        Returns ``None`` when the caller may not write, rather than raising: a
        viewer posting an artifact is an authorisation answer, not an exception.
        """
        if not kind.strip():
            raise ValueError("artifact kind is required")
        if payload.get("kind") != kind:
            # The bundle switches on payload["kind"]; a row whose column and
            # payload disagree renders as something other than what it is
            # filed under, which is the kind of drift nothing else would catch.
            raise ValueError("payload kind must match the artifact kind")
        current = self.project(project_id, user_id)
        if current is None or current.role == "viewer":
            return None
        artifact = Artifact(
            id=_new_id("art"),
            project_id=project_id,
            kind=kind.strip(),
            title=title.strip(),
            created_at=time.time(),
            payload=payload,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO artifacts (id, project_id, kind, title, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (artifact.id, project_id, artifact.kind, artifact.title, json.dumps(payload), artifact.created_at),
            )
            conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (artifact.created_at, project_id))
        return artifact

    def artifacts_for(self, project_id: str, user_id: str) -> list[Artifact]:
        """Metadata only — a list view has no use for ten payloads."""
        if self.project(project_id, user_id) is None:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, project_id, kind, title, created_at FROM artifacts "
                "WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [Artifact(**dict(row)) for row in rows]

    def artifact(self, artifact_id: str, user_id: str) -> Artifact | None:
        """One artifact with its payload, scoped through the project membership.

        The join is what does the scoping: there is no path to a payload that
        does not pass through a membership row, so an id guessed from a URL
        answers nothing.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT a.* FROM artifacts a "
                "JOIN memberships m ON m.project_id = a.project_id "
                "WHERE a.id = ? AND m.user_id = ?",
                (artifact_id, user_id),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        return Artifact(
            id=data["id"],
            project_id=data["project_id"],
            kind=data["kind"],
            title=data["title"],
            created_at=data["created_at"],
            payload=json.loads(data["payload"]),
        )

    def delete_artifact(self, artifact_id: str, user_id: str) -> bool:
        existing = self.artifact(artifact_id, user_id)
        if existing is None:
            return False
        project = self.project(existing.project_id, user_id)
        if project is None or project.role == "viewer":
            return False
        with self._connect() as conn:
            conn.execute("DELETE FROM artifacts WHERE id = ?", (artifact_id,))
        return True

    # ── rooms ──────────────────────────────────────────────────────────

    def open_room(
        self,
        project_id: str,
        user_id: str,
        kind: str,
        invite_url: str,
        *,
        title: str = "",
        join_code: str = "",
    ) -> Room | None:
        """Register a running board against a project.

        Refuses anything that is not an ``http(s)`` URL. The value is rendered
        as an ``href`` a member clicks, so a ``javascript:`` scheme here would
        be stored XSS with a friendly label on it — and unlike the front end's
        ``safeUrl``, this one is worth refusing at the door rather than
        degrading to plain text, because a room nobody can open is useless
        anyway.
        """
        if kind not in ROOM_KINDS:
            raise ValueError(f"unknown room kind: {kind}")
        if not invite_url.startswith(("http://", "https://")):
            raise ValueError("invite_url must be an http(s) URL")
        current = self.project(project_id, user_id)
        if current is None or current.role == "viewer":
            return None
        room = Room(
            id=_new_id("room"),
            project_id=project_id,
            kind=kind,
            title=title.strip(),
            invite_url=invite_url,
            join_code=join_code.strip(),
            opened_by=user_id,
            opened_at=time.time(),
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO rooms (id, project_id, kind, title, invite_url, join_code, opened_by, opened_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    room.id,
                    project_id,
                    room.kind,
                    room.title,
                    room.invite_url,
                    room.join_code,
                    user_id,
                    room.opened_at,
                ),
            )
        return room

    def rooms_for(self, project_id: str, user_id: str, *, live_only: bool = True) -> list[Room]:
        if self.project(project_id, user_id) is None:
            return []
        query = "SELECT * FROM rooms WHERE project_id = ?"
        if live_only:
            query += " AND closed_at IS NULL"
        query += " ORDER BY opened_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, (project_id,)).fetchall()
        return [Room(**dict(row)) for row in rows]

    def close_room(self, room_id: str, user_id: str) -> bool:
        """Mark a room finished. Soft, so the history survives the ceremony."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT r.project_id FROM rooms r JOIN memberships m ON m.project_id = r.project_id "
                "WHERE r.id = ? AND m.user_id = ? AND m.role != 'viewer'",
                (room_id, user_id),
            ).fetchone()
            if row is None:
                return False
            conn.execute("UPDATE rooms SET closed_at = ? WHERE id = ? AND closed_at IS NULL", (time.time(), room_id))
        return True
