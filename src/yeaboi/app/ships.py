"""Live ship runs, owned by the backend rather than by a window.

A ship run is the one thing yeaboi does that lasts tens of minutes and stops
halfway to ask a human a question. Streaming it down a single HTTP connection
would tie the run's life to a renderer that can reload, navigate away or crash;
so the run lives here, in the process that outlives every window, and a surface
*polls* a snapshot — exactly what the terminal does against the same store.

The gate is not this module's decision to make. It is resolved through
``ShipStore.resolve_gate``, the one seam a CLI approver also uses, so the
database's compare-and-swap arbitrates whoever answers first no matter which
surface they answered on.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ShipRunSession:
    """One run in flight: its worker, its board, and what a surface may see."""

    key: str
    story_id: str
    story_title: str
    repo: str
    session_id: str
    check_command: str
    started_at: str
    cancel: threading.Event
    #: The engine's own id, once it exists. Everything else in the shared store
    #: belongs to another run, and a surface must never open a gate over a diff
    #: this user did not launch.
    run_id: str = ""
    #: Latest lifecycle event per component, in arrival order.
    components: dict = field(default_factory=dict)
    result: object | None = None
    failure: str = ""
    board: object | None = None
    thread: threading.Thread | None = None

    @property
    def finished(self) -> bool:
        return self.thread is not None and not self.thread.is_alive()

    def snapshot(self, *, db_path=None) -> dict:
        """What a polling surface sees. Reads the store for the gate, by id."""
        from yeaboi.mcp.runtime import to_jsonable

        board = self.board
        share_url = getattr(board, "share_url", "") if board is not None else ""
        return {
            "key": self.key,
            "run_id": self.run_id,
            "story_id": self.story_id,
            "story_title": self.story_title,
            "repo": self.repo,
            "check_command": self.check_command,
            "started_at": self.started_at,
            "finished": self.finished,
            "cancelling": self.cancel.is_set(),
            "phases": list(self.components.values()),
            "gate": to_jsonable(self._gate(db_path)),
            "result": to_jsonable(self.result) if self.result is not None else None,
            "failure": self.failure,
            "board": {
                "url": share_url,
                # A join code without a link is not an invite — the code is
                # only meaningful once there is somewhere to type it.
                "code": getattr(board, "display_code", "") if share_url else "",
            },
        }

    def _gate(self, db_path):
        """The open gate for *this* run, or None. Never raises."""
        if not self.run_id:
            return None
        from yeaboi.ship.store import ShipStore

        try:
            with ShipStore(db_path) as store:
                row = store.get_run(self.run_id)
        except Exception:  # noqa: BLE001 — a store hiccup is not an open gate
            logger.warning("ship supervisor: could not read run %s", self.run_id, exc_info=True)
            return None
        if row is None or row.status != "awaiting_approval" or row.gate_resolution:
            return None
        return row

    def stop(self) -> None:
        """Ask the run to wind down. The engine notices the event at its next check."""
        if not self.cancel.is_set():
            logger.info("ship supervisor: cancel requested for %s", self.run_id or self.key)
            self.cancel.set()

    def close_board(self) -> None:
        board, self.board = self.board, None
        if board is not None:
            try:
                board.stop()
            except Exception:  # noqa: BLE001 — teardown must not raise into shutdown
                logger.warning("ship supervisor: board teardown failed", exc_info=True)


class ShipSupervisor:
    """Every ship run this process has launched, by key."""

    def __init__(self, *, db_path=None, driver=None, board_factory=None) -> None:
        self._db_path = db_path
        #: Injection seams, per the ship engine's own: a test drives a run
        #: end to end without a coding agent or a tunnel.
        self._driver = driver
        self._board_factory = board_factory
        self._runs: dict[str, ShipRunSession] = {}
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    def start(self, *, story_id: str, story_title: str, repo: str, session_id: str, check_command: str) -> dict:
        """Launch one run on a daemon thread and return its first snapshot."""
        import uuid

        session = ShipRunSession(
            key=uuid.uuid4().hex[:12],
            story_id=story_id,
            story_title=story_title,
            repo=repo,
            session_id=session_id,
            check_command=check_command,
            started_at=_now_iso(),
            cancel=threading.Event(),
        )
        session.thread = threading.Thread(
            target=self._work, args=(session,), name=f"ship-run-{session.key}", daemon=True
        )
        with self._lock:
            self._runs[session.key] = session
        session.thread.start()
        logger.info("ship supervisor: launched %s against %s (key %s)", story_id, repo, session.key)
        return session.snapshot(db_path=self._db_path)

    def _work(self, session: ShipRunSession) -> None:
        from yeaboi.analysis.progress import is_component_progress
        from yeaboi.ship import engine

        def _on_run_id(run_id: str) -> None:
            session.run_id = run_id
            self._maybe_start_board(session)

        def _on_progress(item: object) -> None:
            if not is_component_progress(item):
                return
            session.components[item["component_id"]] = item
            board = session.board
            if board is not None:
                board.note_component(item)

        def _on_agent_line(line: str) -> None:
            board = session.board
            if board is not None:
                board.note_agent_line(line)

        try:
            # Deliberately NOT under mcp.runtime's _ENGINE_LOCK, which every
            # other engine call takes. A ship run parks at its approval gate
            # until a human answers, so holding a process-wide mutex across it
            # would freeze the chat, every dashboard and every tool for as long
            # as the diff sits unread. A run isolates itself in its own
            # worktree and the store's CAS arbitrates the gate, so nothing here
            # needs the lock.
            session.result = engine.run_ship(
                session.story_id,
                session.repo,
                # The desktop picker lists stories, so say so rather than
                # letting the engine infer a level from a colliding id.
                level="story",
                session_id=session.session_id,
                check_command=session.check_command,
                db_path=self._db_path,
                on_progress=_on_progress,
                on_run_id=_on_run_id,
                # Enabling the board turns on the driver's stream-json path;
                # a plain run keeps the unchanged one-shot json flow.
                on_agent_line=_on_agent_line if self._board_enabled() else None,
                cancel_event=session.cancel,
                driver=self._driver,
            )
        except BaseException as exc:  # noqa: BLE001 — the engine shouldn't raise; belt and braces
            session.failure = f"The run stopped unexpectedly: {exc}"
            logger.error("ship supervisor: run %s crashed", session.key, exc_info=True)
        finally:
            session.close_board()

    def _board_enabled(self) -> bool:
        if self._board_factory is not None:
            return True
        from yeaboi.config import get_ship_board_enabled

        return get_ship_board_enabled()

    def _maybe_start_board(self, session: ShipRunSession) -> None:
        """Bring the live board up. Never raises — a board failure is not a run failure."""
        if not self._board_enabled():
            return
        try:
            if self._board_factory is not None:
                board = self._board_factory(session.run_id)
            else:
                from yeaboi.ship.live import ShipBoardSession

                board = ShipBoardSession(session.run_id, story_title=session.story_title or session.story_id)
            board.start()
            session.board = board
        except Exception:  # noqa: BLE001 — a board failure must never sink the run
            logger.warning("ship supervisor: could not start the live board", exc_info=True)

    # -- reads -------------------------------------------------------------

    def run(self, key: str) -> ShipRunSession | None:
        with self._lock:
            return self._runs.get(key)

    def runs(self) -> list[dict]:
        with self._lock:
            sessions = list(self._runs.values())
        return [session.snapshot(db_path=self._db_path) for session in sessions]

    def snapshot(self, key: str) -> dict | None:
        session = self.run(key)
        return None if session is None else session.snapshot(db_path=self._db_path)

    # -- the gate ----------------------------------------------------------

    def resolve_gate(self, key: str, resolution: str, comment: str = "") -> bool:
        """Answer this run's open gate. False when someone else answered first."""
        from yeaboi.ship.store import ShipStore

        session = self.run(key)
        if session is None or not session.run_id:
            return False
        with ShipStore(self._db_path) as store:
            answered = store.resolve_gate(session.run_id, resolution, comment)
        logger.info(
            "ship supervisor: gate %s for %s -> %s", resolution, session.run_id, "taken" if answered else "already"
        )
        return answered

    # -- shutdown ----------------------------------------------------------

    def stop_all(self) -> None:
        """Cancel every live run and tear its board down. Never raises."""
        with self._lock:
            sessions = list(self._runs.values())
        for session in sessions:
            if not session.finished:
                session.stop()
            session.close_board()
