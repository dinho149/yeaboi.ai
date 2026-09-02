"""Live planning conversations — one :class:`ChatSession` per project id.

Sessions live in the backend, not in the renderer: a reloaded window rejoins
the conversation it left, and the graph is compiled once for the process
rather than once per turn. Persistence is the same project store the TUI
resumes from (``persistence.py``), so a plan started in the terminal opens in
the app and back again.

The graph factory, loader and saver are injected so the whole surface can be
tested without an LLM or a home directory.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from yeaboi.agent.chat_session import ChatSession, start_state

logger = logging.getLogger(__name__)


@dataclass
class LiveChat:
    """One conversation plus the lock that keeps its turns single-file."""

    project_id: str
    session: ChatSession
    turn: threading.Lock = field(default_factory=threading.Lock)


def _compile_graph():
    from yeaboi.agent.graph import create_graph

    return create_graph()


def _load(project_id: str) -> dict | None:
    from yeaboi.persistence import load_graph_state

    return load_graph_state(project_id)


def _save(project_id: str, state: dict) -> None:
    from yeaboi.persistence import save_project_snapshot

    save_project_snapshot(project_id, state)


def _new_id() -> str:
    from yeaboi.persistence import create_project_id

    return create_project_id()


class UnknownChatError(LookupError):
    """No conversation with that id is open or stored."""


class ChatSupervisor:
    """The open conversations. Thread-safe; one compiled graph for all of them."""

    def __init__(self, *, graph_factory=_compile_graph, loader=_load, saver=_save, id_factory=_new_id) -> None:
        self._graph_factory = graph_factory
        self._loader = loader
        self._saver = saver
        self._id_factory = id_factory
        self._chats: dict[str, LiveChat] = {}
        self._lock = threading.Lock()
        self._graph = None
        self._graph_lock = threading.Lock()

    def graph(self):
        """The compiled planning graph — built once, on first use."""
        with self._graph_lock:
            if self._graph is None:
                logger.info("Compiling the planning graph for the app")
                self._graph = self._graph_factory()
            return self._graph

    def create(self, description: str, *, intake_mode: str = "", solo: bool = False) -> LiveChat:
        """Open a new conversation seeded with the greeting and the description."""
        project_id = self._id_factory()
        chat = LiveChat(
            project_id, ChatSession(self.graph(), start_state(description, intake_mode=intake_mode, solo=solo))
        )
        with self._lock:
            self._chats[project_id] = chat
        logger.info("Chat created: project=%s", project_id)
        return chat

    def open(self, project_id: str) -> LiveChat:
        """The live conversation for an id, resuming it from disk if needed."""
        with self._lock:
            chat = self._chats.get(project_id)
            if chat is not None:
                return chat
        state = self._loader(project_id)
        if state is None:
            raise UnknownChatError(project_id)
        chat = LiveChat(project_id, ChatSession(self.graph(), state))
        with self._lock:
            # Another thread may have resumed the same id first — one live
            # session per conversation, or two turns would fork the state.
            chat = self._chats.setdefault(project_id, chat)
        logger.info("Chat resumed: project=%s", project_id)
        return chat

    def save(self, chat: LiveChat) -> None:
        self._saver(chat.project_id, chat.session.state)

    def close(self, project_id: str) -> None:
        """Forget a conversation (it stays on disk and can be reopened)."""
        with self._lock:
            self._chats.pop(project_id, None)

    def close_all(self) -> None:
        with self._lock:
            self._chats.clear()
