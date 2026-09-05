"""Spotify's library, as the desktop's rows.

Read-only but for one verb: ``play`` sends a play command to whatever device
is running Spotify — Premium only, and the desktop falls back to the app when
Spotify says so. Every URL built here is a share link the desktop's own link
grammar accepts: ``https://open.spotify.com/<kind>/<id>``.
"""

from __future__ import annotations

from yeaboi.connectors.library import (
    LIBRARY_TTL_SECONDS,
    SEARCH_TTL_SECONDS,
    Item,
    LibraryError,
    Page,
    cached,
    clamp_limit,
    vendor_get,
    vendor_put,
)

KEY = "spotify"
API = "https://api.spotify.com/v1"
#: Spotify caps a search page at ten since the 2026 migration.
SEARCH_LIMIT = 10

_SHELF_URLS = {
    "playlists": f"{API}/me/playlists",
    "liked": f"{API}/me/tracks",
    "albums": f"{API}/me/albums",
    "recent": f"{API}/me/player/recently-played",
}


def _art(images) -> str:
    if not isinstance(images, list) or not images:
        return ""
    # Spotify lists the largest first; the middle one is plenty for a row.
    pick = images[min(1, len(images) - 1)]
    return str(pick.get("url", "")) if isinstance(pick, dict) else ""


def _names(people) -> str:
    return ", ".join(str(p.get("name", "")) for p in (people or []) if isinstance(p, dict) and p.get("name"))


def track_item(track: dict) -> Item | None:
    if not isinstance(track, dict) or not track.get("id"):
        return None
    album = track.get("album") or {}
    return Item(
        id=str(track["id"]),
        kind="track",
        title=str(track.get("name", "")),
        subtitle=" · ".join(p for p in (_names(track.get("artists")), str(album.get("name", ""))) if p),
        artwork_url=_art(album.get("images")),
        duration_ms=int(track.get("duration_ms") or 0),
        url=f"https://open.spotify.com/track/{track['id']}",
        uri=str(track.get("uri") or f"spotify:track:{track['id']}"),
        preview_url=str(track.get("preview_url") or ""),
    )


def album_item(album: dict) -> Item | None:
    if not isinstance(album, dict) or not album.get("id"):
        return None
    return Item(
        id=str(album["id"]),
        kind="album",
        title=str(album.get("name", "")),
        subtitle=_names(album.get("artists")),
        artwork_url=_art(album.get("images")),
        url=f"https://open.spotify.com/album/{album['id']}",
        uri=str(album.get("uri") or f"spotify:album:{album['id']}"),
        count=int(album.get("total_tracks") or 0),
    )


def playlist_item(playlist: dict) -> Item | None:
    if not isinstance(playlist, dict) or not playlist.get("id"):
        return None
    owner = playlist.get("owner") or {}
    tracks = playlist.get("tracks") or {}
    return Item(
        id=str(playlist["id"]),
        kind="playlist",
        title=str(playlist.get("name", "")),
        subtitle=str(owner.get("display_name") or ""),
        artwork_url=_art(playlist.get("images")),
        url=f"https://open.spotify.com/playlist/{playlist['id']}",
        uri=str(playlist.get("uri") or f"spotify:playlist:{playlist['id']}"),
        count=int(tracks.get("total") or 0) if isinstance(tracks, dict) else 0,
    )


def _offset(cursor: str) -> int:
    try:
        return max(0, int(cursor)) if cursor else 0
    except ValueError:
        return 0


def _next_offset(body: dict, offset: int, limit: int) -> str:
    total = int(body.get("total") or 0)
    return str(offset + limit) if body.get("next") or (total and offset + limit < total) else ""


def library(shelf: str, cursor: str = "", limit: int = 20) -> Page:
    url = _SHELF_URLS.get(shelf)
    if url is None:
        raise LibraryError("unsupported_shelf", f"Spotify has no '{shelf}' shelf", 400)
    limit = clamp_limit(limit)

    def load() -> Page:
        if shelf == "recent":
            params = {"limit": limit, "before": cursor} if cursor else {"limit": limit}
            body = vendor_get(KEY, url, params)
            items = tuple(i for i in (track_item((row or {}).get("track")) for row in body.get("items") or []) if i)
            cursors = body.get("cursors") or {}
            return Page(items, str(cursors.get("before") or "") if body.get("next") else "")
        offset = _offset(cursor)
        body = vendor_get(KEY, url, {"limit": limit, "offset": offset})
        rows = body.get("items") or []
        if shelf == "playlists":
            items = tuple(i for i in (playlist_item(row) for row in rows) if i)
        elif shelf == "albums":
            items = tuple(i for i in (album_item((row or {}).get("album")) for row in rows) if i)
        else:
            items = tuple(i for i in (track_item((row or {}).get("track")) for row in rows) if i)
        return Page(items, _next_offset(body, offset, limit))

    return cached((KEY, "library", shelf, cursor, limit), LIBRARY_TTL_SECONDS, load)


def playlist_items(playlist_id: str, cursor: str = "", limit: int = 50) -> Page:
    limit = clamp_limit(limit, 50)
    offset = _offset(cursor)

    def load() -> Page:
        body = vendor_get(KEY, f"{API}/playlists/{playlist_id}/tracks", {"limit": limit, "offset": offset})
        items = tuple(i for i in (track_item((row or {}).get("track")) for row in body.get("items") or []) if i)
        return Page(items, _next_offset(body, offset, limit))

    return cached((KEY, "playlist", playlist_id, cursor, limit), LIBRARY_TTL_SECONDS, load)


def search(query: str, limit: int = 10) -> Page:
    limit = min(clamp_limit(limit, SEARCH_LIMIT), SEARCH_LIMIT)

    def load() -> Page:
        body = vendor_get(KEY, f"{API}/search", {"q": query, "type": "track,album,playlist", "limit": limit})
        items: list[Item] = []
        for row in (body.get("tracks") or {}).get("items") or []:
            if (item := track_item(row)) is not None:
                items.append(item)
        for row in (body.get("albums") or {}).get("items") or []:
            if (item := album_item(row)) is not None:
                items.append(item)
        for row in (body.get("playlists") or {}).get("items") or []:
            if (item := playlist_item(row)) is not None:
                items.append(item)
        return Page(tuple(items), "")

    return cached((KEY, "search", query.strip().lower(), limit), SEARCH_TTL_SECONDS, load)


_URI_KINDS = ("track", "album", "playlist", "artist")


def play(uri: str, device_id: str = "") -> None:
    """Start ``uri`` on the active Spotify device (Premium). Raises the fallback codes."""
    parts = uri.split(":")
    if len(parts) != 3 or parts[0] != "spotify" or parts[1] not in _URI_KINDS or not parts[2].isalnum():
        raise LibraryError("bad_uri", "That is not a Spotify URI", 400)
    if device_id and not device_id.isalnum():
        raise LibraryError("bad_uri", "That is not a Spotify device id", 400)
    payload = {"uris": [uri]} if parts[1] == "track" else {"context_uri": uri}
    url = f"{API}/me/player/play" + (f"?device_id={device_id}" if device_id else "")
    vendor_put(KEY, url, payload)


def player() -> dict:
    """What Spotify is playing now, for the desktop's Now Playing."""
    body = vendor_get(KEY, f"{API}/me/player")
    if not body:
        return {"playing": False, "progress_ms": 0, "item": None, "device": None}
    item = track_item(body.get("item") or {})
    device = body.get("device") or {}
    return {
        "playing": bool(body.get("is_playing")),
        "progress_ms": int(body.get("progress_ms") or 0),
        "item": item.__dict__ if item else None,
        "device": {"id": str(device.get("id", "")), "name": str(device.get("name", ""))} if device else None,
    }


def devices() -> dict:
    body = vendor_get(KEY, f"{API}/me/player/devices")
    return {
        "devices": [
            {
                "id": str(d.get("id", "")),
                "name": str(d.get("name", "")),
                "type": str(d.get("type", "")),
                "active": bool(d.get("is_active")),
            }
            for d in body.get("devices") or []
            if isinstance(d, dict)
        ]
    }
