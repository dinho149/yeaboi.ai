"""Which session a retro board runs against.

A retro has no wizard — it has one decision, and this is it: the board targets
the most recently modified planning session, taking its project and sprint names
from that session's state. Every surface that opens a board asks the same
question, so it is answered once here rather than by whoever draws the screen.

Deliberately ``setup.py`` and not ``engine.py``: the parity registry treats every
public name in an ``engine.py`` as a capability of its own.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetroTarget:
    """The session a board is about to be opened for.

    ``session_id`` is empty when there is no session yet — the caller shows the
    "create a project in Planning first" notice rather than starting a server.
    """

    session_id: str = ""
    session_name: str = ""
    project_name: str = ""
    sprint_name: str = ""

    def __bool__(self) -> bool:
        return bool(self.session_id)

    def as_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "session_name": self.session_name,
            "project_name": self.project_name,
            "sprint_name": self.sprint_name,
        }


NO_SESSION_MESSAGE = "No project session yet — create one in Planning first, then start a retro."


def resolve_session(*, db_path: Path | None = None) -> RetroTarget:
    """Resolve the retro's target session, or an empty target when there is none.

    Never raises: a store that cannot be opened costs a notice, not a traceback.
    """
    try:
        from yeaboi.paths import get_db_path
        from yeaboi.sessions import SessionStore, make_display_name

        with SessionStore(db_path or get_db_path()) as store:
            session_id = store.get_latest_session_id()
            if not session_id:
                return RetroTarget()
            meta = store.get_session(session_id) or {}
            state = store.load_state(session_id) or {}
    except Exception:
        logger.warning("retro: failed to resolve latest session", exc_info=True)
        return RetroTarget()
    session_name = make_display_name(meta) if meta else session_id
    return RetroTarget(
        session_id=session_id,
        session_name=session_name,
        project_name=state.get("project_name", "") or session_name,
        # Best-effort: the export and report titles degrade gracefully if blank.
        sprint_name=str(state.get("sprint_name", "") or ""),
    )
