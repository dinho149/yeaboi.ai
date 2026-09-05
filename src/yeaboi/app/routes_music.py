"""The music services' sign-in and library — the desktop's Browse behind the Music page.

Chrome, not a capability: nothing here is work anyone would ask an agent to
do. The sign-in is a session the app holds (``app.oauth_signin``), driven a
poll at a time exactly like the subscription sign-in in ``routes_settings``;
the credential it mints is persisted server-side and never appears in a body.
A vendor's refusal comes back as a typed ``{error, code}`` with the status the
code implies — and ``signed_out`` is a 409 on purpose, so the desktop never
reads it as a failure of its own bearer.
"""

from __future__ import annotations

import logging

from yeaboi.app.router import HTTPError, Request, Response, json_response

logger = logging.getLogger(__name__)


def _connector(key: str):
    from yeaboi.connectors import registry

    connector = registry.by_key(key)
    if connector is None or not connector.can_sign_in:
        raise HTTPError(404, f"{key} has no sign-in")
    return connector


# -- sign-in ----------------------------------------------------------------------


def signin_start(app, request: Request) -> Response:
    """``POST /api/connections/{key}/signin`` — open the listener and hand back the URL to open."""
    from yeaboi.connectors.oauth import OAuthSignIn

    key = request.params["key"]
    _connector(key)
    with app.oauth_signin_lock:
        if app.oauth_signin is not None:
            app.oauth_signin.cancel()
            app.oauth_signin = None
        session = OAuthSignIn(key)
        if not session.start():
            logger.warning("music: %s sign-in could not start", key)
            return json_response({"started": False, "url": "", "message": session.message})
        app.oauth_signin = session
    return json_response({"started": True, "url": session.url, "message": ""})


def signin_status(app, request: Request) -> Response:
    """``GET /api/connections/{key}/signin`` — poll; persisted before ``saved`` is reported."""
    key = request.params["key"]
    with app.oauth_signin_lock:
        session = app.oauth_signin
        if session is None or session.key != key:
            return json_response({"active": False})
        session.poll()
        return json_response(
            {
                "active": True,
                "done": session.done,
                "ok": session.ok,
                "saved": session.persisted,
                "account": session.account if session.persisted else "",
                "message": session.message if session.done else "",
            }
        )


def signin_cancel(app, request: Request) -> Response:
    """``POST /api/connections/{key}/signin/cancel`` — stop and discard the session."""
    key = request.params["key"]
    with app.oauth_signin_lock:
        session, keep = (
            (app.oauth_signin, None) if app.oauth_signin and app.oauth_signin.key == key else (None, app.oauth_signin)
        )
        app.oauth_signin = keep
    if session is not None:
        session.cancel()
        logger.info("music: %s sign-in cancelled", key)
    return json_response({"ok": True})


def signout(app, request: Request) -> Response:
    """``POST /api/connections/{key}/signout`` — forget the token and the name."""
    from yeaboi.connectors import library, oauth

    key = request.params["key"]
    _connector(key)
    oauth.sign_out(key)
    library.forget(key)
    return json_response({"ok": True, "signed_in": False})


# -- the library --------------------------------------------------------------------


def _answer(call) -> Response:
    from yeaboi.connectors.library import LibraryError

    try:
        result = call()
    except LibraryError as exc:
        logger.info("music: %s (%d)", exc.code, exc.status)
        return json_response(exc.as_dict(), exc.status)
    return json_response(result.as_dict() if hasattr(result, "as_dict") else result)


def _vendor(key: str):
    from yeaboi.connectors import library_spotify, library_youtube

    if key == "spotify":
        return library_spotify
    if key == "youtube_music":
        return library_youtube
    raise HTTPError(404, f"{key} has no library here")


def library(app, request: Request) -> Response:
    """``GET /api/music/{key}/library?shelf=&cursor=&limit=`` → ``{items, next_cursor}``."""
    from yeaboi.connectors.library import SHELVES

    key = request.params["key"]
    vendor = _vendor(key)
    shelf = request.query.get("shelf", "playlists")
    if shelf not in SHELVES:
        raise ValueError(f"shelf must be one of {', '.join(SHELVES)}")
    return _answer(lambda: vendor.library(shelf, request.query.get("cursor", ""), request.query.get("limit", "")))


def playlist(app, request: Request) -> Response:
    """``GET /api/music/{key}/playlist/{playlist_id}/items?cursor=`` → ``{items, next_cursor}``."""
    key = request.params["key"]
    vendor = _vendor(key)
    playlist_id = request.params["playlist_id"]
    if not playlist_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("playlist id is not well-formed")
    return _answer(
        lambda: vendor.playlist_items(playlist_id, request.query.get("cursor", ""), request.query.get("limit", ""))
    )


def search(app, request: Request) -> Response:
    """``GET /api/music/{key}/search?q=&limit=`` → ``{items, next_cursor}``; Apple needs no sign-in."""
    from yeaboi.connectors import library_apple

    key = request.params["key"]
    query = request.query.get("q", "").strip()
    if not query:
        raise ValueError("q must not be empty")
    limit = request.query.get("limit", "")
    if key == "apple_music":
        return _answer(lambda: library_apple.search(query, limit, request.query.get("country", "us")))
    vendor = _vendor(key)
    return _answer(lambda: vendor.search(query, limit))


def spotify_play(app, request: Request) -> Response:
    """``POST /api/music/spotify/play`` body ``{uri, device_id?}`` → ``{ok}``; Premium only."""
    from yeaboi.connectors import library_spotify

    payload = request.json()
    uri = payload.get("uri")
    if not isinstance(uri, str) or not uri:
        raise ValueError("uri must be a non-empty string")
    device = payload.get("device_id", "")
    if not isinstance(device, str):
        raise ValueError("device_id must be a string")

    def run():
        library_spotify.play(uri, device)
        logger.info("music: spotify play sent")
        return {"ok": True}

    return _answer(run)


def spotify_player(app, request: Request) -> Response:
    """``GET /api/music/spotify/player`` → ``{playing, progress_ms, item, device}``."""
    from yeaboi.connectors import library_spotify

    return _answer(library_spotify.player)


def spotify_devices(app, request: Request) -> Response:
    """``GET /api/music/spotify/devices`` → ``{devices: [{id, name, type, active}]}``."""
    from yeaboi.connectors import library_spotify

    return _answer(library_spotify.devices)
