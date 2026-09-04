"""The front page route — the desktop home's paper, from the news desk.

Serializes what :class:`yeaboi.news.NewsDesk` answers; the desk owns the
cache, the refresh and the off switch, so this handler defines nothing.
"""

from __future__ import annotations

import logging

from yeaboi.app.router import Request, Response, json_response
from yeaboi.mcp.runtime import to_jsonable

logger = logging.getLogger(__name__)


def news(app, request: Request) -> Response:
    """``GET /api/news?refresh=1`` — the cached paper at once; a refresh in the background when it is stale."""
    force = str(request.query.get("refresh", "")).strip().lower() in ("1", "true")
    paper, refreshing = app.news.get_paper(refresh=force)
    logger.info("news requested (refresh=%s, stale=%s, refreshing=%s)", force, paper.stale, refreshing)
    return json_response({"enabled": app.news.enabled(), "refreshing": refreshing, **to_jsonable(paper)})
