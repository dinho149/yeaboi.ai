"""Native routes for the Solo world.

The desktop's Solo home renders the same "where am I" snapshot the terminal's
welcome strip does, from the one builder in :mod:`yeaboi.solo.today` — so the
two surfaces cannot say different things about today.
"""

from __future__ import annotations

import logging

from yeaboi.app.router import Request, Response, json_response
from yeaboi.mcp.runtime import to_jsonable

logger = logging.getLogger(__name__)


def today(app, request: Request) -> Response:
    """``GET /api/solo/today?project_id=`` — the TodaySnapshot, text and numbers only."""
    from yeaboi.solo.today import build_today_snapshot

    project_id = (request.query.get("project_id") or "").strip()
    logger.info("solo today requested (project=%s)", project_id or "-")
    return json_response(to_jsonable(build_today_snapshot(project_id=project_id)))
