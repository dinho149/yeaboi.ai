"""Apple's catalogue, through the public iTunes Search API — no sign-in.

The library itself lives in the Music app on the user's Mac, and the desktop
browses that directly; this only searches the catalogue and hands back rows
whose URL the desktop's link grammar accepts (``music.apple.com/<cc>/album/
<slug>/<collectionId>?i=<trackId>``), with the 30-second preview Apple offers.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from yeaboi.connectors import http
from yeaboi.connectors.library import SEARCH_TTL_SECONDS, Item, LibraryError, Page, cached, clamp_limit

KEY = "apple_music"
SEARCH_URL = "https://itunes.apple.com/search"
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


def slug(name: str) -> str:
    """Apple's own path word for a name — lower, dashed, never empty."""
    cleaned = _NOT_SLUG.sub("-", (name or "").lower()).strip("-")
    return cleaned or "album"


def _country(value: str) -> str:
    value = (value or "").strip().lower()
    return value if len(value) == 2 and value.isalpha() else "us"


def _artwork(row: dict) -> str:
    art = str(row.get("artworkUrl100") or "")
    return art.replace("100x100bb", "300x300bb") if art else ""


def result_item(row: dict, country: str) -> Item | None:
    if not isinstance(row, dict):
        return None
    collection = row.get("collectionId")
    if not collection:
        return None
    base = f"https://music.apple.com/{country}/album/{slug(str(row.get('collectionName', '')))}/{collection}"
    if row.get("wrapperType") == "track" and row.get("trackId"):
        return Item(
            id=str(row["trackId"]),
            kind="song",
            title=str(row.get("trackName", "")),
            subtitle=" · ".join(p for p in (str(row.get("artistName", "")), str(row.get("collectionName", ""))) if p),
            artwork_url=_artwork(row),
            duration_ms=int(row.get("trackTimeMillis") or 0),
            url=f"{base}?i={row['trackId']}",
            preview_url=str(row.get("previewUrl") or ""),
        )
    if row.get("wrapperType") == "collection":
        return Item(
            id=str(collection),
            kind="album",
            title=str(row.get("collectionName", "")),
            subtitle=str(row.get("artistName", "")),
            artwork_url=_artwork(row),
            url=base,
            count=int(row.get("trackCount") or 0),
        )
    return None


def search(query: str, limit: int = 20, country: str = "us") -> Page:
    limit = clamp_limit(limit)
    country = _country(country)

    def load() -> Page:
        params = urlencode(
            {"term": query, "media": "music", "entity": "song,album", "limit": limit, "country": country}
        )
        try:
            resp = http.get_json(f"{SEARCH_URL}?{params}", headers={})
        except Exception as exc:  # noqa: BLE001
            raise LibraryError("unavailable", f"Could not reach Apple: {type(exc).__name__}", 502) from None
        if resp.status_code == 429:
            raise LibraryError("rate_limited", "Apple is rate-limiting yeaboi — try again in a moment", 429, 20)
        if resp.status_code != 200:
            raise LibraryError("unavailable", f"Apple answered {resp.status_code}", 502)
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            raise LibraryError("unavailable", "Apple did not answer with JSON", 502) from None
        items = tuple(i for i in (result_item(row, country) for row in body.get("results") or []) if i)
        return Page(items, "")

    return cached((KEY, "search", query.strip().lower(), limit, country), SEARCH_TTL_SECONDS, load)
