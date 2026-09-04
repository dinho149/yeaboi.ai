"""From fetch results to a paper: parse, tag, dedupe, group, pick the lead.

Named paper, not engine: it is chrome the home draws, not a capability with
surfaces to reach.

Pure over its inputs: the fetcher, the clock and the previous items are all
passed in, so a test builds a whole paper with no network and no disk.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from yeaboi.news.fetch import Conditional, FetchResult, fetch_all
from yeaboi.news.parse import NewsItem, normalise_url, parse_feed
from yeaboi.news.sources import COLUMNS, NewsSource
from yeaboi.news.topics import tag
from yeaboi.timeparse import parse_datetime

logger = logging.getLogger(__name__)

COLUMN_ORDER = COLUMNS
COLUMN_TITLES: dict[str, str] = {"yeaboi": "yeaboi", "ai": "AI", "engineering": "Engineering", "research": "Research"}
MAX_PER_COLUMN = 12
MAX_AGE_DAYS = 14
LEAD_MAX_AGE_DAYS = 7
TITLE_SIMILARITY = 0.9

# Outlets that repeat another outlet's story lose the dedupe to the original.
_SYNDICATORS = frozenset({"techmeme", "hn-ai"})


@dataclass(frozen=True)
class SourceStatus:
    id: str = ""
    name: str = ""
    home_url: str = ""
    column: str = ""
    ok: bool = False
    fetched_at: str = ""
    error: str = ""
    item_count: int = 0


@dataclass(frozen=True)
class Section:
    column: str = ""
    title: str = ""
    items: tuple[NewsItem, ...] = ()


@dataclass(frozen=True)
class Paper:
    schema: int = 1
    generated_at: str = ""
    stale: bool = False
    lead: NewsItem | None = None
    sections: tuple[Section, ...] = ()
    sources: tuple[SourceStatus, ...] = ()


def when(item: NewsItem) -> datetime:
    """The item's clock time, UTC; the epoch when it has none, so it sorts last."""
    try:
        parsed = parse_datetime(item.published)
    except (TypeError, ValueError):
        return datetime.fromtimestamp(0, tz=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def newest_first(items: Iterable[NewsItem]) -> list[NewsItem]:
    return sorted(items, key=when, reverse=True)


def cap_per_source(items: Sequence[NewsItem], limit: int) -> tuple[NewsItem, ...]:
    return tuple(newest_first(items)[:limit])


def _folded(title: str) -> str:
    return " ".join(title.lower().split())


def dedupe(items: Sequence[NewsItem]) -> tuple[NewsItem, ...]:
    """One item per story: same URL, then near-identical title. The original outlet wins."""
    ranked = sorted(items, key=lambda item: (item.source_id in _SYNDICATORS, -when(item).timestamp()))
    kept: list[NewsItem] = []
    seen_urls: set[str] = set()
    for item in ranked:
        key = normalise_url(item.url)
        if key in seen_urls:
            continue
        title = _folded(item.title)
        if any(SequenceMatcher(None, title, _folded(other.title)).ratio() >= TITLE_SIMILARITY for other in kept):
            continue
        seen_urls.add(key)
        kept.append(item)
    return tuple(kept)


def drop_older_than(items: Iterable[NewsItem], cutoff: datetime) -> tuple[NewsItem, ...]:
    """Everything dated outside the window goes; the yeaboi column and undated items stay."""
    return tuple(item for item in items if item.column == "yeaboi" or not item.published or when(item) >= cutoff)


def pick_lead(sections: Sequence[Section], now: datetime) -> NewsItem | None:
    """The story at the top: a fresh yeaboi post or video, else the newest AI headline."""
    by_column = {section.column: section for section in sections}
    yeaboi = by_column.get("yeaboi")
    if yeaboi is not None:
        fresh = [
            item
            for item in yeaboi.items
            if item.kind in ("post", "video") and when(item) >= now - timedelta(days=LEAD_MAX_AGE_DAYS)
        ]
        if fresh:
            return newest_first(fresh)[0]
    ai = by_column.get("ai")
    if ai is not None and ai.items:
        return newest_first(ai.items)[0]
    return None


def group(items: Iterable[NewsItem], *, per_column: int = MAX_PER_COLUMN) -> tuple[Section, ...]:
    """Sections in column order, newest first, clipped; an empty column is omitted."""
    sections = []
    for column in COLUMN_ORDER:
        rows = newest_first(item for item in items if item.column == column)[:per_column]
        if rows:
            sections.append(Section(column=column, title=COLUMN_TITLES[column], items=tuple(rows)))
    return tuple(sections)


def _without(sections: Sequence[Section], lead: NewsItem | None) -> tuple[Section, ...]:
    if lead is None:
        return tuple(sections)
    out = []
    for section in sections:
        rows = tuple(item for item in section.items if item.id != lead.id)
        if rows:
            out.append(replace(section, items=rows))
    return tuple(out)


def local_only_paper(local_items: Sequence[NewsItem], now: datetime, *, stale: bool = False) -> Paper:
    """The paper with nothing fetched: the yeaboi column from the release notes alone."""
    return Paper(generated_at=now.isoformat(timespec="seconds"), stale=stale, sections=group(local_items))


def build_paper(
    *,
    sources: Sequence[NewsSource],
    now: datetime,
    fetcher: Callable[..., Mapping[str, FetchResult]] = fetch_all,
    conditionals: Mapping[str, Conditional] | None = None,
    previous_items: Mapping[str, tuple[NewsItem, ...]] | None = None,
    local_items: Sequence[NewsItem] = (),
) -> tuple[Paper, dict[str, Conditional], dict[str, tuple[NewsItem, ...]]]:
    """Fetch every source and lay out the paper.

    Returns the paper, the conditionals to send next time, and each source's
    items so an unchanged (304) or failed outlet keeps yesterday's headlines.
    """
    conditionals = dict(conditionals or {})
    previous_items = dict(previous_items or {})
    results = fetcher(sources, conditionals)
    statuses: list[SourceStatus] = []
    items_by_source: dict[str, tuple[NewsItem, ...]] = {}
    next_conditionals: dict[str, Conditional] = {}
    everything: list[NewsItem] = []

    for source in sources:
        result = results.get(source.id) or FetchResult(source_id=source.id, error="not fetched")
        ok = result.status in (200, 304) and not result.error
        if result.status == 200 and not result.error:
            items = tuple(tag(item, source) for item in parse_feed(source, result.body))
            if not items:
                ok = False
                result = replace(result, error="no items")
                logger.warning("news: %s answered 200 with no readable items", source.id)
        else:
            items = ()
        if not items:
            items = previous_items.get(source.id, ())
        items = cap_per_source(items, source.max_items)
        items_by_source[source.id] = items
        if result.status in (200, 304) and (result.conditional.etag or result.conditional.last_modified):
            next_conditionals[source.id] = result.conditional
        statuses.append(
            SourceStatus(
                id=source.id,
                name=source.name,
                home_url=source.home_url,
                column=source.column,
                ok=ok,
                fetched_at=result.fetched_at,
                error=result.error,
                item_count=len(items),
            )
        )
        everything.extend(items)

    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    fresh = drop_older_than(dedupe(everything), cutoff)
    sections = group(dedupe((*local_items, *fresh)))
    lead = pick_lead(sections, now)
    paper = Paper(
        generated_at=now.isoformat(timespec="seconds"),
        stale=False,
        lead=lead,
        sections=_without(sections, lead),
        sources=tuple(statuses),
    )
    return paper, next_conditionals, items_by_source
