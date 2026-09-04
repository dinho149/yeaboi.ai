"""The paper on disk: one JSON file under the data dir, written atomically."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from yeaboi.news.fetch import Conditional
from yeaboi.news.paper import Paper, Section, SourceStatus
from yeaboi.news.parse import NewsItem

logger = logging.getLogger(__name__)

CACHE_SCHEMA = 1
CACHE_TTL_SECONDS = 30 * 60


@dataclass(frozen=True)
class CacheEntry:
    paper: Paper = Paper()
    conditionals: dict[str, Conditional] = field(default_factory=dict)
    items_by_source: dict[str, tuple[NewsItem, ...]] = field(default_factory=dict)
    # Per source, when it was last asked — for outlets with a minimum interval.
    last_fetch_at: dict[str, float] = field(default_factory=dict)
    written_at: float = 0.0


def _item(raw: object) -> NewsItem | None:
    if not isinstance(raw, dict):
        return None
    allowed = {name for name in NewsItem.__dataclass_fields__}
    try:
        return NewsItem(**{key: value for key, value in raw.items() if key in allowed})
    except TypeError:
        return None


def paper_to_dict(paper: Paper) -> dict:
    return asdict(paper)


def paper_from_dict(raw: object) -> Paper:
    if not isinstance(raw, dict):
        return Paper()
    sections = []
    for section in raw.get("sections") or []:
        if not isinstance(section, dict):
            continue
        items = tuple(item for item in (_item(row) for row in section.get("items") or []) if item is not None)
        sections.append(
            Section(column=str(section.get("column", "")), title=str(section.get("title", "")), items=items)
        )
    sources = []
    allowed = {name for name in SourceStatus.__dataclass_fields__}
    for status in raw.get("sources") or []:
        if isinstance(status, dict):
            sources.append(SourceStatus(**{key: value for key, value in status.items() if key in allowed}))
    return Paper(
        schema=int(raw.get("schema", CACHE_SCHEMA)),
        generated_at=str(raw.get("generated_at", "")),
        stale=bool(raw.get("stale", False)),
        lead=_item(raw.get("lead")),
        sections=tuple(sections),
        sources=tuple(sources),
    )


def entry_to_dict(entry: CacheEntry) -> dict:
    return {
        "schema": CACHE_SCHEMA,
        "written_at": entry.written_at,
        "paper": paper_to_dict(entry.paper),
        "conditionals": {key: asdict(value) for key, value in entry.conditionals.items()},
        "items_by_source": {key: [asdict(item) for item in items] for key, items in entry.items_by_source.items()},
        "last_fetch_at": dict(entry.last_fetch_at),
    }


def entry_from_dict(raw: object) -> CacheEntry | None:
    if not isinstance(raw, dict) or raw.get("schema") != CACHE_SCHEMA:
        return None
    conditionals = {}
    for key, value in (raw.get("conditionals") or {}).items():
        if isinstance(value, dict):
            conditionals[str(key)] = Conditional(
                etag=str(value.get("etag", "")), last_modified=str(value.get("last_modified", ""))
            )
    items_by_source = {}
    for key, rows in (raw.get("items_by_source") or {}).items():
        items_by_source[str(key)] = tuple(item for item in (_item(row) for row in rows or []) if item is not None)
    last_fetch_at = {
        str(key): float(value)
        for key, value in (raw.get("last_fetch_at") or {}).items()
        if isinstance(value, (int, float))
    }
    written_at = raw.get("written_at")
    return CacheEntry(
        paper=paper_from_dict(raw.get("paper")),
        conditionals=conditionals,
        items_by_source=items_by_source,
        last_fetch_at=last_fetch_at,
        written_at=float(written_at) if isinstance(written_at, (int, float)) else 0.0,
    )


def read_cache(path: Path) -> CacheEntry | None:
    """The cached paper, or None when there is none worth reading."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - a broken cache is an empty cache
        logger.warning("news: cache unreadable at %s", path, exc_info=True)
        return None
    entry = entry_from_dict(raw)
    if entry is None:
        logger.warning("news: cache at %s has an unknown schema, ignoring it", path)
    return entry


def write_cache(path: Path, entry: CacheEntry) -> bool:
    """Write the whole entry, or nothing: a temp file beside it, then a rename."""
    payload = json.dumps(entry_to_dict(entry), separators=(",", ":"))
    tmp = ""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 - a cache that could not be written costs one more fetch
        logger.warning("news: cache not written at %s", path, exc_info=True)
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
        return False
    logger.debug("news: cache written at %s", path)
    return True


def is_fresh(entry: CacheEntry, *, now: float, ttl: float = CACHE_TTL_SECONDS) -> bool:
    return entry.written_at > 0 and 0 <= now - entry.written_at <= ttl
