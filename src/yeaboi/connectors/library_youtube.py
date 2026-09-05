"""YouTube's library, as the desktop's rows — playlists and liked videos.

Read-only (``youtube.readonly``). A search costs a hundred units of the
project's daily ten thousand, shared by everyone on the built-in client, so
search pages are cached the longest. Every URL built here is a watch or
playlist link the desktop's own grammar accepts.
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
)

KEY = "youtube_music"
API = "https://www.googleapis.com/youtube/v3"
#: The liked-videos playlist every account has.
LIKED_PLAYLIST = "LL"
#: YouTube's music category, so a search does not return lectures.
MUSIC_CATEGORY = "10"


def _thumb(snippet: dict) -> str:
    thumbs = snippet.get("thumbnails") or {}
    for size in ("medium", "high", "default"):
        entry = thumbs.get(size)
        if isinstance(entry, dict) and entry.get("url"):
            return str(entry["url"])
    return ""


def _video_id(row: dict) -> str:
    rid = row.get("id")
    if isinstance(rid, dict):
        return str(rid.get("videoId") or "")
    snippet = row.get("snippet") or {}
    resource = snippet.get("resourceId") or {}
    content = row.get("contentDetails") or {}
    return str(resource.get("videoId") or content.get("videoId") or "")


def _playlist_id(row: dict) -> str:
    rid = row.get("id")
    if isinstance(rid, dict):
        return str(rid.get("playlistId") or "")
    return str(rid or "")


def video_item(row: dict) -> Item | None:
    vid = _video_id(row)
    snippet = row.get("snippet") or {}
    if not vid or snippet.get("title") in ("Private video", "Deleted video"):
        return None
    return Item(
        id=vid,
        kind="video",
        title=str(snippet.get("title", "")),
        subtitle=str(snippet.get("videoOwnerChannelTitle") or snippet.get("channelTitle") or ""),
        artwork_url=_thumb(snippet),
        url=f"https://www.youtube.com/watch?v={vid}",
    )


def playlist_item(row: dict) -> Item | None:
    pid = _playlist_id(row)
    snippet = row.get("snippet") or {}
    if not pid:
        return None
    content = row.get("contentDetails") or {}
    return Item(
        id=pid,
        kind="playlist",
        title=str(snippet.get("title", "")),
        subtitle=str(snippet.get("channelTitle") or ""),
        artwork_url=_thumb(snippet),
        url=f"https://www.youtube.com/playlist?list={pid}",
        count=int(content.get("itemCount") or 0),
    )


def _page(body: dict, mapper) -> Page:
    items = tuple(i for i in (mapper(row) for row in body.get("items") or [] if isinstance(row, dict)) if i)
    return Page(items, str(body.get("nextPageToken") or ""))


def library(shelf: str, cursor: str = "", limit: int = 20) -> Page:
    limit = clamp_limit(limit)
    if shelf == "playlists":

        def load() -> Page:
            body = vendor_get(
                KEY,
                f"{API}/playlists",
                {"part": "snippet,contentDetails", "mine": "true", "maxResults": limit, "pageToken": cursor},
            )
            return _page(body, playlist_item)

        return cached((KEY, "library", shelf, cursor, limit), LIBRARY_TTL_SECONDS, load)
    if shelf == "liked":
        return playlist_items(LIKED_PLAYLIST, cursor, limit)
    raise LibraryError("unsupported_shelf", f"YouTube has no '{shelf}' shelf", 400)


def playlist_items(playlist_id: str, cursor: str = "", limit: int = 50) -> Page:
    limit = clamp_limit(limit, 50)

    def load() -> Page:
        body = vendor_get(
            KEY,
            f"{API}/playlistItems",
            {"part": "snippet,contentDetails", "playlistId": playlist_id, "maxResults": limit, "pageToken": cursor},
        )
        return _page(body, video_item)

    return cached((KEY, "playlist", playlist_id, cursor, limit), LIBRARY_TTL_SECONDS, load)


def search(query: str, limit: int = 10) -> Page:
    limit = clamp_limit(limit, 10)

    def load() -> Page:
        body = vendor_get(
            KEY,
            f"{API}/search",
            {
                "part": "snippet",
                "q": query,
                "type": "video,playlist",
                "videoCategoryId": MUSIC_CATEGORY,
                "maxResults": limit,
            },
        )
        items: list[Item] = []
        for row in body.get("items") or []:
            if not isinstance(row, dict):
                continue
            rid = row.get("id") or {}
            item = playlist_item(row) if isinstance(rid, dict) and rid.get("playlistId") else video_item(row)
            if item is not None:
                items.append(item)
        return Page(tuple(items), "")

    return cached((KEY, "search", query.strip().lower(), limit), SEARCH_TTL_SECONDS, load)
