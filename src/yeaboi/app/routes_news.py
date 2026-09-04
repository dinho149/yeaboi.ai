"""The front page routes — the desktop home's paper, and the outlet roster behind it.

Serializes what :class:`yeaboi.news.NewsDesk` answers; the desk owns the
cache, the refresh and the off switch. The roster routes are thin adapters
over :mod:`yeaboi.news.roster` (the validator is the gate) and
:mod:`yeaboi.news.probe` (one guarded look at a URL, never a save).
"""

from __future__ import annotations

import logging

from yeaboi.app.router import HTTPError, Request, Response, json_response
from yeaboi.mcp.runtime import to_jsonable

logger = logging.getLogger(__name__)


def news(app, request: Request) -> Response:
    """``GET /api/news?refresh=1`` — the cached paper at once; a refresh in the background when it is stale."""
    force = str(request.query.get("refresh", "")).strip().lower() in ("1", "true")
    paper, refreshing = app.news.get_paper(refresh=force)
    logger.info("news requested (refresh=%s, stale=%s, refreshing=%s)", force, paper.stale, refreshing)
    return json_response({"enabled": app.news.enabled(), "refreshing": refreshing, **to_jsonable(paper)})


def sources(app, request: Request) -> Response:
    """``GET /api/news/sources`` — every outlet the roster knows, with how its last read went."""
    from yeaboi.news.roster import MAX_CUSTOM
    from yeaboi.news.sources import COLUMNS

    return json_response({"sources": app.news.source_rows(), "max_custom": MAX_CUSTOM, "columns": list(COLUMNS)})


def _row(app, source_id: str) -> dict:
    return next((row for row in app.news.source_rows() if row["id"] == source_id), {})


def _required_str(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def source_probe(app, request: Request) -> Response:
    """``POST /api/news/sources/probe`` — look at a URL; the verdict is in the body, always 200."""
    from yeaboi.logging_setup import mode_log
    from yeaboi.news.probe import probe

    url = _required_str(request.json(), "url")
    with mode_log("news"):
        return json_response(to_jsonable(probe(url)))


def source_add(app, request: Request) -> Response:
    """``POST /api/news/sources`` — probe, validate, save, and refresh with the new outlet."""
    from yeaboi.logging_setup import mode_log
    from yeaboi.news.probe import probe
    from yeaboi.news.roster import add_custom

    payload = request.json()
    url = _required_str(payload, "url")
    column = _required_str(payload, "column")
    name = str(payload.get("name", "") or "").strip()
    with mode_log("news"):
        looked = probe(url)
        if not looked.ok:
            raise HTTPError(400, looked.error)
        try:
            added = add_custom(
                url=looked.url, name=name or looked.name, column=column, kind=looked.kind, home_url=looked.home_url
            )
        except ValueError as exc:
            raise HTTPError(400, str(exc)) from None
        refreshing = app.news.invalidate()
    return json_response({"source": _row(app, added.id), "refreshing": refreshing})


def source_enabled(app, request: Request) -> Response:
    """``POST /api/news/sources/{source_id}/enabled`` — switch one outlet on or off."""
    from yeaboi.logging_setup import mode_log
    from yeaboi.news.roster import set_enabled

    source_id = request.params["source_id"]
    enabled = request.json().get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    with mode_log("news"):
        try:
            set_enabled(source_id, enabled, youtube_channel=app.news.youtube_channel())
        except KeyError:
            raise HTTPError(404, f"no outlet named {source_id}") from None
        refreshing = app.news.invalidate(refresh=enabled)
    return json_response({"source": _row(app, source_id), "refreshing": refreshing})


def source_delete(app, request: Request) -> Response:
    """``POST /api/news/sources/{source_id}/delete`` — remove an outlet the user added."""
    from yeaboi.logging_setup import mode_log
    from yeaboi.news.roster import CUSTOM_PREFIX, remove_custom

    source_id = request.params["source_id"]
    if not source_id.startswith(CUSTOM_PREFIX):
        raise HTTPError(400, "built-in outlets can be turned off, not deleted")
    with mode_log("news"):
        if not remove_custom(source_id):
            raise HTTPError(404, f"no outlet named {source_id}")
        refreshing = app.news.invalidate()
    return json_response({"deleted": source_id, "refreshing": refreshing})
