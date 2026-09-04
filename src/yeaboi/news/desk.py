"""The news desk: the paper the route answers with, and the refresh behind it.

Stale-while-revalidate: a request always gets a paper at once (the cache, or
the offline yeaboi column) and, when the cache has expired, starts one
background refresh. Two requests during a refresh start nothing more.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from yeaboi.news.cache import CACHE_TTL_SECONDS, CacheEntry, is_fresh, read_cache, write_cache
from yeaboi.news.local import changelog_items
from yeaboi.news.paper import Paper, build_paper, local_only_paper
from yeaboi.news.parse import NewsItem
from yeaboi.news.sources import NewsSource, active_sources
from yeaboi.paths import get_news_cache_path

logger = logging.getLogger(__name__)

# The off switch the privacy table names; read here and nowhere else.
NEWS_ENV = "YEABOI_NEWS"
YOUTUBE_ENV = "NEWS_YOUTUBE_CHANNEL"
_OFF = ("0", "false", "off", "no")


def news_enabled() -> bool:
    return os.environ.get(NEWS_ENV, "").strip().lower() not in _OFF


class NewsDesk:
    """One per app process. Every collaborator is injectable for the tests."""

    def __init__(
        self,
        *,
        cache_path: Callable[[], Path] = get_news_cache_path,
        clock: Callable[[], float] = time.time,
        build=build_paper,
        sources: Callable[..., Sequence[NewsSource]] = active_sources,
        local: Callable[[], Sequence[NewsItem]] = changelog_items,
        spawn: Callable[..., threading.Thread] = threading.Thread,
        ttl: float = CACHE_TTL_SECONDS,
    ) -> None:
        self._cache_path = cache_path
        self._clock = clock
        self._build = build
        self._sources = sources
        self._local = local
        self._spawn = spawn
        self._ttl = ttl
        self._refreshing = threading.Lock()

    def enabled(self) -> bool:
        return news_enabled()

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), tz=timezone.utc)

    def _local_items(self) -> tuple[NewsItem, ...]:
        try:
            return tuple(self._local())
        except Exception:  # noqa: BLE001 - the offline column must never take the page down
            logger.warning("news: local release notes unavailable", exc_info=True)
            return ()

    def get_paper(self, *, refresh: bool = False) -> tuple[Paper, bool]:
        """The paper to answer with now, and whether a refresh is running for it."""
        now = self._now()
        if not self.enabled():
            logger.info("news: disabled via %s, answering the yeaboi column only", NEWS_ENV)
            return local_only_paper(self._local_items(), now), False
        entry = read_cache(self._cache_path())
        if entry is not None and not refresh and is_fresh(entry, now=self._clock(), ttl=self._ttl):
            return entry.paper, self._refreshing.locked()
        started = self._start_refresh()
        refreshing = started or self._refreshing.locked()
        if entry is not None:
            return replace(entry.paper, stale=True), refreshing
        return local_only_paper(self._local_items(), now, stale=True), refreshing

    def _start_refresh(self) -> bool:
        """Start one background refresh; False when one is already running."""
        if not self._refreshing.acquire(blocking=False):
            return False
        thread = self._spawn(target=self._refresh_holding_lock, name="news-refresh", daemon=True)
        thread.start()
        return True

    def _refresh_holding_lock(self) -> None:
        try:
            self._refresh()
        finally:
            self._refreshing.release()

    def refresh_now(self) -> Paper:
        """Refresh synchronously — the thread's body, and the test entry point."""
        with self._refreshing:
            return self._refresh()

    def _refresh(self) -> Paper:
        from yeaboi.logging_setup import mode_log

        with mode_log("news"):
            started = self._clock()
            now = self._now()
            path = self._cache_path()
            previous = read_cache(path) or CacheEntry()
            sources = self._due_sources(previous, started)
            logger.info("news: refresh started (%d sources)", len(sources))
            try:
                paper, conditionals, items = self._build(
                    sources=sources,
                    now=now,
                    conditionals=previous.conditionals,
                    previous_items=previous.items_by_source,
                    local_items=self._local_items(),
                )
            except Exception:  # noqa: BLE001 - the last paper stays; the log says why there is no new one
                logger.warning("news: refresh failed", exc_info=True)
                return previous.paper
            fetched_at = {**previous.last_fetch_at, **{source.id: started for source in sources}}
            write_cache(
                path,
                CacheEntry(
                    paper=paper,
                    conditionals={**previous.conditionals, **conditionals},
                    items_by_source={**previous.items_by_source, **items},
                    last_fetch_at=fetched_at,
                    written_at=self._clock(),
                ),
            )
            ok = sum(1 for status in paper.sources if status.ok)
            count = sum(len(section.items) for section in paper.sections) + (1 if paper.lead else 0)
            logger.info(
                "news: refresh finished — %d items, %d/%d sources ok, %.1fs",
                count,
                ok,
                len(paper.sources),
                self._clock() - started,
            )
            return paper

    def _due_sources(self, previous: CacheEntry, now: float) -> tuple[NewsSource, ...]:
        """The registry, minus any outlet asked again sooner than it allows."""
        channel = os.environ.get(YOUTUBE_ENV, "")
        due = []
        for source in self._sources(youtube_channel=channel):
            last = previous.last_fetch_at.get(source.id, 0.0)
            if source.min_interval_seconds and 0 <= now - last < source.min_interval_seconds:
                logger.info("news: %s skipped, asked %.0fs ago", source.id, now - last)
                continue
            due.append(source)
        return tuple(due)
