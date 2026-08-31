"""Live boards and temporary shares, owned by the backend rather than a window.

The TUI's boards live inside a frame loop: the page opens the server, brings the
link up, draws, and tears both down in a ``finally``. A desktop window cannot own
them that way — a reload would kill a retro mid-ceremony — so the sessions live
here, in the process that outlives every window, and a surface holds nothing but
an id.

Two kinds of session, and the difference is what they are for:

* a **board** (retro, poker) is a live collaborative surface with a join code;
  closing it flushes the board to its mode's store, because the ceremony that
  just happened is the artifact.
* a **share** is one finished artifact published behind an access code, and
  closing it decides whether the corrections teammates made are kept.

``stop_all`` runs at shutdown so a tunnel never outlives the app.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from yeaboi.sharing.link import SecureLink

logger = logging.getLogger(__name__)

BOARD_RETRO = "retro"
BOARD_POKER = "poker"


@dataclass
class BoardSession:
    """One live board: its data, its loopback server, and its secure link."""

    board_id: str
    kind: str
    title: str
    session_id: str
    board: Any
    server: Any
    link: SecureLink
    started_at: str
    project_name: str = ""
    sprint_name: str = ""
    _stopped: bool = field(default=False, repr=False)

    def snapshot(self) -> dict:
        """Everything a host surface draws, with no secret in it.

        The host URL is deliberately absent: it carries the admin token that
        makes its holder the host, and it is wanted by exactly one caller — the
        thing that opens the board window. That caller asks :attr:`host_url`
        through a route of its own, so the token never rides in the payload
        every board list hands out.
        """
        return {
            "board_id": self.board_id,
            "kind": self.kind,
            "title": self.title,
            "session_id": self.session_id,
            "project_name": self.project_name,
            "started_at": self.started_at,
            "share_url": self.server.share_url,
            "display_code": self.server.display_code,
            "link": self.link.snapshot(),
            "state": self._state(),
        }

    @property
    def host_url(self) -> str:
        """The private host link, admin token and all. Never put it in a snapshot."""
        return self.server.url

    def _state(self) -> dict:
        """The board's own contents — cards for a retro, the table for poker."""
        if self.kind == BOARD_RETRO:
            from yeaboi.mcp.runtime import to_jsonable

            return {
                "grids": to_jsonable(self.board.cards_by_grid()),
                "carried": to_jsonable(self.board.carried_snapshot()),
            }
        return dict(self.board.state_snapshot())

    def report(self):
        """The artifact this board would be flushed as, right now."""
        if self.kind == BOARD_RETRO:
            from yeaboi.retro.board import board_to_report

            return board_to_report(self.board, sprint_name=self.sprint_name)
        from yeaboi.poker.board import board_to_report

        return board_to_report(self.board)

    def stop(self, *, db_path: Path) -> int:
        """Flush to the mode's store, drop the link, close the server.

        Returns the recorded run id (0 when the flush failed). Safe to call more
        than once; the flush happens on the first call only.
        """
        if self._stopped:
            return 0
        self._stopped = True
        run_id = 0
        try:
            run_id = self._flush(db_path)
        except Exception as exc:  # noqa: BLE001 — a failed flush must not leak a server
            logger.warning("%s board: flush to store failed: %s", self.kind, exc)
        self.link.stop()
        self.server.stop()
        logger.info("%s board closed (board_id=%s run_id=%s)", self.kind, self.board_id, run_id)
        return run_id

    def _flush(self, db_path: Path) -> int:
        if self.kind == BOARD_RETRO:
            from yeaboi.retro.store import RetroStore

            with RetroStore(db_path) as store:
                return store.record_run(self.report())
        from yeaboi.poker.store import PokerStore

        with PokerStore(db_path) as store:
            return store.record_run(self.report())


@dataclass
class ShareSession:
    """One artifact published behind an access code."""

    share_id: str
    kind: str
    title: str
    session_id: str
    run_id: int
    server: Any
    link: SecureLink
    started_at: str
    editing: Any = None
    #: How many corrections were already on record when this share opened. The
    #: delta is what a caller decides to commit — a reopened share replays its
    #: whole log before anyone joins, so the total is non-zero from the start.
    baseline_edits: int = 0
    _stopped: bool = field(default=False, repr=False)

    @property
    def edits(self) -> int:
        """Corrections recorded *in this session*."""
        if self.editing is None:
            return 0
        return max(0, len(self.editing.share.document.edits()) - self.baseline_edits)

    def snapshot(self) -> dict:
        return {
            "share_id": self.share_id,
            "kind": self.kind,
            "title": self.title,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "started_at": self.started_at,
            # The link's URL, not the server's: OutputShareServer has no
            # share_url of its own — the tunnel address is all a share has.
            "share_url": self.link.url,
            "display_code": self.server.display_code,
            "editable": self.editing is not None,
            "edits": self.edits,
            "editors": list(self.editing.share.document.editors()) if self.editing is not None else [],
            "link": self.link.snapshot(),
        }

    def stop(self, *, commit: bool) -> int:
        """Close the share; optionally keep what teammates corrected.

        Returns the run id of the corrected row, or 0 when nothing was committed
        — including when ``commit`` is true and nobody changed anything.
        Committing is a decision the caller makes, never something teardown does
        on its own: this also runs on a crash, and a path that rewrites the
        host's stored report from an exception handler is not one anybody asked
        for.
        """
        if self._stopped:
            return 0
        self._stopped = True
        recorded = self.edits
        committed = 0
        self.link.stop()
        self.server.stop()
        if self.editing is not None:
            try:
                if commit and recorded:
                    committed = self.editing.commit()
            finally:
                self.editing.close()
        logger.info("share closed (share_id=%s new edits=%d committed=%s)", self.share_id, recorded, committed)
        return committed


class BoardSupervisor:
    """Every live board and share this process owns, addressed by id."""

    def __init__(self, *, db_path: Path | None = None, tunnel_factory=None) -> None:
        self._db_path = db_path
        self._tunnel_factory = tunnel_factory
        self._lock = threading.Lock()
        self._boards: dict[str, BoardSession] = {}
        self._shares: dict[str, ShareSession] = {}

    @property
    def db_path(self) -> Path:
        from yeaboi.paths import get_db_path

        return self._db_path or get_db_path()

    def _link(self, server, *, surface: str) -> SecureLink:
        link = SecureLink(server, surface=surface, tunnel_factory=self._tunnel_factory)
        link.start()
        return link

    # -- boards ------------------------------------------------------------

    def start_retro(self) -> BoardSession:
        """Open a retro board for the latest session, seeded with carried actions."""
        from yeaboi.config import get_retro_server_port
        from yeaboi.projects.scope import resolve_scope
        from yeaboi.retro.board import RetroBoard
        from yeaboi.retro.engine import carried_action_items_for_session, history_providers, standup_blocker_cards
        from yeaboi.retro.server import RetroServer
        from yeaboi.retro.setup import resolve_session

        target = resolve_session(db_path=self._db_path)
        if not target:
            raise ValueError("no project session yet")
        board = RetroBoard(target.session_id, project_name=target.project_name, sprint_name=target.sprint_name)
        # Seeded before the server starts, so the first browser poll already
        # shows the "Last sprint's actions" column. A project-linked session
        # scopes the carry and adds the project's recent standup blockers.
        scope = resolve_scope(session_id=target.session_id, db_path=self.db_path)
        carried = carried_action_items_for_session(
            target.session_id, project_name=target.project_name, db_path=self.db_path, scope=scope
        )
        carried = (*carried, *standup_blocker_cards(scope, db_path=self.db_path, existing=carried))
        if carried:
            board.seed_carried(list(carried))
        server = RetroServer(board, port=get_retro_server_port())
        # Read lazily, so a store that cannot be opened costs a board with no
        # history rather than a board.
        server.history_list, server.history_report = history_providers(
            project_name=target.project_name, db_path=self.db_path, scope=scope
        )
        server.start()
        session = BoardSession(
            board_id=uuid4().hex[:12],
            kind=BOARD_RETRO,
            title=target.project_name or target.session_name,
            session_id=target.session_id,
            board=board,
            server=server,
            link=self._link(server, surface=BOARD_RETRO),
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            project_name=target.project_name,
            sprint_name=target.sprint_name,
        )
        with self._lock:
            self._boards[session.board_id] = session
        logger.info("retro board opened (board_id=%s session=%s)", session.board_id, target.session_id)
        return session

    def start_poker(self, *, source: str, scope_label: str, tickets: list[dict]) -> BoardSession:
        """Open a poker board over an already-fetched ticket list."""
        from yeaboi.config import get_poker_server_port
        from yeaboi.poker.board import PokerBoard
        from yeaboi.poker.server import PokerServer
        from yeaboi.retro.setup import resolve_session

        if not tickets:
            raise ValueError("no tickets to estimate")
        target = resolve_session(db_path=self._db_path)
        # A poker session does not need a planning session to exist — fall back
        # to a stable quick-session id so history still records and groups.
        session_id = target.session_id or "quick-poker"
        board = PokerBoard(
            session_id,
            project_name=target.project_name if target else "",
            source=source,
            scope_label=scope_label,
            tickets=tickets,
        )
        server = PokerServer(board, port=get_poker_server_port())
        server.start()
        session = BoardSession(
            board_id=uuid4().hex[:12],
            kind=BOARD_POKER,
            title=target.project_name or target.session_name or scope_label,
            session_id=session_id,
            board=board,
            server=server,
            link=self._link(server, surface=BOARD_POKER),
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            project_name=target.project_name if target else "",
        )
        with self._lock:
            self._boards[session.board_id] = session
        logger.info("poker board opened (board_id=%s scope=%s)", session.board_id, scope_label)
        return session

    def board(self, board_id: str) -> BoardSession | None:
        with self._lock:
            return self._boards.get(board_id)

    def boards(self) -> list[BoardSession]:
        with self._lock:
            return list(self._boards.values())

    def stop_board(self, board_id: str) -> int | None:
        """Close one board and flush it. ``None`` when there is no such board."""
        with self._lock:
            session = self._boards.pop(board_id, None)
        if session is None:
            return None
        return session.stop(db_path=self.db_path)

    # -- shares ------------------------------------------------------------

    def start_share(self, resolved, *, editable: bool = True) -> ShareSession:
        """Publish one resolved artifact behind an access code."""
        from yeaboi.sharing import resolve
        from yeaboi.sharing.server import OutputShareServer

        editing = resolve.editable_session(resolved, db_path=self._db_path) if editable else None
        try:
            document = resolve.document(resolved)
            server = OutputShareServer(
                document,
                editable=editing.share if editing is not None else None,
                on_edit=editing.persist if editing is not None else None,
            )
            server.start()
        except Exception:
            if editing is not None:
                editing.close()
            raise
        session = ShareSession(
            share_id=uuid4().hex[:12],
            kind=resolved.kind,
            title=resolved.title,
            session_id=resolved.session_id,
            run_id=resolved.run_id,
            server=server,
            link=self._link(server, surface="share"),
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            editing=editing,
            baseline_edits=len(editing.share.document.edits()) if editing is not None else 0,
        )
        with self._lock:
            self._shares[session.share_id] = session
        logger.info(
            "share opened (share_id=%s kind=%s editable=%s)", session.share_id, resolved.kind, editing is not None
        )
        return session

    def share(self, share_id: str) -> ShareSession | None:
        with self._lock:
            return self._shares.get(share_id)

    def shares(self) -> list[ShareSession]:
        with self._lock:
            return list(self._shares.values())

    def stop_share(self, share_id: str, *, commit: bool) -> int | None:
        """Close one share. ``None`` when there is no such share."""
        with self._lock:
            session = self._shares.pop(share_id, None)
        if session is None:
            return None
        return session.stop(commit=commit)

    # -- shutdown ----------------------------------------------------------

    def stop_all(self) -> None:
        """Close everything. Never commits a share — nobody decided to."""
        with self._lock:
            boards, shares = list(self._boards.values()), list(self._shares.values())
            self._boards.clear()
            self._shares.clear()
        for session in boards:
            try:
                session.stop(db_path=self.db_path)
            except Exception:  # noqa: BLE001 — one bad board must not strand the others
                logger.warning("board %s failed to stop cleanly", session.board_id, exc_info=True)
        for share in shares:
            try:
                share.stop(commit=False)
            except Exception:  # noqa: BLE001
                logger.warning("share %s failed to stop cleanly", share.share_id, exc_info=True)
