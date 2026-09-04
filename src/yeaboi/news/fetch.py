"""The network edge: one conditional GET per outlet, on a thread pool.

Stdlib ``urllib`` with a short timeout, the ``update_check`` precedent: every
failure is a result with an ``error``, never an exception, so one outlet down
costs one column a source and nothing else.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

from yeaboi.news.sources import NewsSource

logger = logging.getLogger(__name__)

FETCH_TIMEOUT_SECONDS = 6.0
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_WORKERS = 6

_ACCEPT = "application/rss+xml, application/atom+xml, application/xml, application/json, text/html;q=0.9, */*;q=0.5"


@dataclass(frozen=True)
class Conditional:
    """What the cache remembers per outlet so an unchanged feed costs a 304."""

    etag: str = ""
    last_modified: str = ""


@dataclass(frozen=True)
class FetchResult:
    source_id: str = ""
    # 200, 304, another HTTP status, or 0 on a transport error.
    status: int = 0
    body: bytes = b""
    conditional: Conditional = Conditional()
    error: str = ""
    fetched_at: str = ""


Fetcher = Callable[[NewsSource, Conditional], FetchResult]


def user_agent() -> str:
    from yeaboi import __version__

    return f"yeaboi/{__version__} (+https://yeaboi.ai)"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_one(source: NewsSource, conditional: Conditional, *, timeout: float = FETCH_TIMEOUT_SECONDS) -> FetchResult:
    """GET one outlet. Never raises."""
    if not source.url.startswith("https://"):
        return FetchResult(source_id=source.id, error="not https", fetched_at=_now_iso())
    headers = {"User-Agent": user_agent(), "Accept": _ACCEPT}
    if conditional.etag:
        headers["If-None-Match"] = conditional.etag
    if conditional.last_modified:
        headers["If-Modified-Since"] = conditional.last_modified
    req = urllib.request.Request(source.url, headers=headers)  # noqa: S310 - https constants from the registry
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https constants from the registry
            body = resp.read(MAX_BODY_BYTES + 1)
            fresh = Conditional(
                etag=resp.headers.get("ETag", "") or "",
                last_modified=resp.headers.get("Last-Modified", "") or "",
            )
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return FetchResult(source_id=source.id, status=304, conditional=conditional, fetched_at=_now_iso())
        logger.warning("news: %s answered http %s", source.id, exc.code)
        return FetchResult(source_id=source.id, status=exc.code, error=f"http {exc.code}", fetched_at=_now_iso())
    except Exception as exc:  # noqa: BLE001 - offline is a normal state for a news fetch
        error = f"{type(exc).__name__}: {exc}"[:200]
        logger.warning("news: %s failed: %s", source.id, error)
        return FetchResult(source_id=source.id, error=error, fetched_at=_now_iso())
    if len(body) > MAX_BODY_BYTES:
        logger.warning("news: %s body over %d bytes, dropped", source.id, MAX_BODY_BYTES)
        return FetchResult(source_id=source.id, status=200, error="oversize", fetched_at=_now_iso())
    return FetchResult(source_id=source.id, status=200, body=body, conditional=fresh, fetched_at=_now_iso())


def fetch_all(
    sources: Iterable[NewsSource],
    conditionals: Mapping[str, Conditional],
    *,
    fetch: Fetcher = fetch_one,
    max_workers: int = MAX_WORKERS,
) -> dict[str, FetchResult]:
    """Fetch every outlet at once; a result per source id, whatever happened."""
    sources = tuple(sources)
    if not sources:
        return {}
    results: dict[str, FetchResult] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(sources)), thread_name_prefix="news-fetch") as pool:
        futures = {pool.submit(fetch, source, conditionals.get(source.id, Conditional())): source for source in sources}
        for future, source in futures.items():
            try:
                results[source.id] = future.result()
            except Exception as exc:  # noqa: BLE001 - a fetcher that raised still yields a result
                results[source.id] = FetchResult(
                    source_id=source.id, error=f"{type(exc).__name__}: {exc}"[:200], fetched_at=_now_iso()
                )
    return results
