"""One shape for what a music service holds, and one door to ask it.

Every vendor answers with its own nouns; the desktop wants one row: a title, a
line under it, some art, a length, and a URL its own link grammar already
accepts. The vendor modules beside this one map their payloads onto
:class:`Item`; this module owns the shape, the bearer, the status translation
and a small cache.

Errors are a vocabulary rather than a message: ``signed_out`` (the desktop
offers Sign in), ``premium_required`` and ``no_active_device`` (it hands the
track to the Spotify app instead), ``quota_exceeded`` and ``rate_limited``
(wait), ``not_allowlisted`` (paste your own client), ``unavailable`` (later).
A ``signed_out`` is a 409, never a 401: the desktop's bearer handling must not
mistake a vendor's refusal for a failure of its own wire.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field

from yeaboi.connectors import http, oauth

logger = logging.getLogger(__name__)

#: Cache lifetimes: a library page changes rarely; a search costs real quota.
LIBRARY_TTL_SECONDS = 60
SEARCH_TTL_SECONDS = 600
CACHE_ENTRIES = 64

SHELVES: tuple[str, ...] = ("playlists", "liked", "albums", "recent")
MAX_LIMIT = 50


@dataclass(frozen=True)
class Item:
    id: str
    #: track | album | playlist | video | song
    kind: str
    title: str
    subtitle: str = ""
    artwork_url: str = ""
    duration_ms: int = 0
    #: A share link the desktop's link grammar parses (open.spotify.com,
    #: youtube.com/watch, music.apple.com).
    url: str = ""
    #: The vendor's own handle, when it has one (a Spotify URI).
    uri: str = ""
    #: A 30-second preview, when the vendor offers one without a sign-in.
    preview_url: str = ""
    #: How many tracks a playlist or album holds, when known.
    count: int = 0


@dataclass(frozen=True)
class Page:
    items: tuple[Item, ...] = ()
    next_cursor: str = ""

    def as_dict(self) -> dict:
        return {"items": [asdict(item) for item in self.items], "next_cursor": self.next_cursor}


@dataclass(frozen=True)
class LibraryError(Exception):
    code: str
    message: str
    status: int = 502
    retry_after: int = 0
    extra: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    def as_dict(self) -> dict:
        body = {"error": self.message, "code": self.code, **self.extra}
        if self.retry_after:
            body["retry_after"] = self.retry_after
        return body


def _retry_after(resp) -> int:
    try:
        return max(0, int(resp.headers.get("Retry-After", "0")))
    except (TypeError, ValueError):
        return 0


def _error_reason(resp) -> str:
    """The vendor's own reason code, when the body carries one."""
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 — a non-JSON error body is just a status
        return ""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, dict):
        reason = error.get("reason") or ""
        if not reason:
            errors = error.get("errors") or []
            if errors and isinstance(errors[0], dict):
                reason = errors[0].get("reason") or ""
        return str(reason)
    return str(body.get("error_description") or error or "")


def translate(key: str, resp) -> LibraryError:
    """The :class:`LibraryError` a non-200 vendor answer means."""
    status = resp.status_code
    reason = _error_reason(resp)
    if status == 401:
        return LibraryError("signed_out", "Sign in again to keep browsing", 409)
    if status == 403:
        if reason == "PREMIUM_REQUIRED":
            return LibraryError(
                "premium_required", "Playing from here needs Spotify Premium — opening in the app instead", 403
            )
        if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
            return LibraryError(
                "quota_exceeded", "YouTube's daily quota is used up — paste your own client in Settings", 429
            )
        return LibraryError(
            "not_allowlisted",
            "The service refused this account — an unapproved app allows only a few sign-ins; "
            "paste your own client ID in Settings",
            403,
        )
    if status == 404 and reason == "NO_ACTIVE_DEVICE":
        return LibraryError(
            "no_active_device", "Nothing is playing Spotify right now — opening in the app instead", 409
        )
    if status == 429:
        return LibraryError(
            "rate_limited", "The service is rate-limiting yeaboi — try again in a moment", 429, _retry_after(resp)
        )
    return LibraryError("unavailable", f"The service answered {status}", 502)


def _bearer(key: str) -> str:
    try:
        return oauth.bearer_for(key)
    except oauth.SignedOutError as exc:
        raise LibraryError("signed_out", str(exc), 409) from None


def vendor_get(key: str, url: str, params: dict | None = None) -> dict:
    """``GET`` a vendor URL as the signed-in user. Translates every failure."""
    from urllib.parse import urlencode

    full = f"{url}?{urlencode({k: v for k, v in (params or {}).items() if v not in (None, '')})}" if params else url
    resp = _request("GET", key, full)
    return _body(key, resp)


def vendor_put(key: str, url: str, payload: dict) -> None:
    """``PUT`` a JSON body as the signed-in user; 2xx is success."""
    _request("PUT", key, url, payload)


def _request(method: str, key: str, url: str, payload: dict | None = None):
    token = _bearer(key)
    resp = _send(method, url, token, payload)
    if resp.status_code == 401:
        # One forced refresh: an access token can be revoked under a valid refresh token.
        oauth.reset_cache_for(key)
        resp = _send(method, url, _bearer(key), payload)
    logger.info("music: %s %s → %d (%d bytes)", key, method, resp.status_code, len(resp.content or b""))
    if resp.status_code >= 400:
        raise translate(key, resp)
    return resp


def _send(method: str, url: str, token: str, payload: dict | None):
    headers = {"Authorization": f"Bearer {token}"}
    try:
        if method == "PUT":
            return http.put_json(url, headers=headers, payload=payload or {})
        return http.get_json(url, headers=headers)
    except http.UnsafeUrlError as exc:
        raise LibraryError("unavailable", str(exc), 502) from None
    except Exception as exc:  # noqa: BLE001 — transport failures are one message
        raise LibraryError("unavailable", f"Could not reach the service: {type(exc).__name__}", 502) from None


def _body(key: str, resp) -> dict:
    if resp.status_code == 204 or not resp.content:
        return {}
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        raise LibraryError("unavailable", "The service did not answer with JSON", 502) from None
    return body if isinstance(body, dict) else {"items": body}


# -- a small cache -------------------------------------------------------------

_cache: dict[tuple, tuple[float, Page]] = {}
_cache_lock = threading.Lock()


def cached(cache_key: tuple, ttl: int, load) -> Page:
    """The page under ``cache_key`` while it is fresh, else ``load()`` and keep it."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit and hit[0] > now:
            return hit[1]
    page = load()
    with _cache_lock:
        if len(_cache) >= CACHE_ENTRIES:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            _cache.pop(oldest, None)
        _cache[cache_key] = (now + ttl, page)
    return page


def forget(key: str) -> None:
    """Drop every cached page for one service — on sign-out."""
    with _cache_lock:
        for cache_key in [k for k in _cache if k and k[0] == key]:
            _cache.pop(cache_key, None)


def clamp_limit(raw: str | int | None, default: int = 20) -> int:
    try:
        value = int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        value = default
    return max(1, min(MAX_LIMIT, value))


def ms(seconds: float | int | None) -> int:
    try:
        return max(0, int(float(seconds or 0) * 1000))
    except (TypeError, ValueError):
        return 0
