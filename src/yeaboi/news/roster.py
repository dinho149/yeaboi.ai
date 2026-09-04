"""The outlet roster: which built-in outlets are on, and the feeds the user added.

Data, never code, in ``~/.yeaboi/data/news_roster.json`` — the custom
connectors precedent. A user-added feed is an https URL the validator has
passed and :mod:`yeaboi.news.fetch` guards on every request; its id is derived
from the URL, never authored. The bundled release notes (``local.py``) are not
an outlet and cannot be turned off.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from yeaboi.news.paper import SourceStatus
from yeaboi.news.parse import normalise_url, web_link
from yeaboi.news.sources import COLUMNS, SOURCES, NewsSource, active_sources

logger = logging.getLogger(__name__)

ROSTER_VERSION = 1
MAX_CUSTOM = 20
NAME_MAX = 60
CUSTOM_KINDS = ("rss", "atom", "json_feed")
CUSTOM_PREFIX = "custom-"
CUSTOM_MAX_ITEMS = 6


@dataclass(frozen=True)
class CustomSource:
    """One outlet the user added, as saved."""

    id: str = ""
    name: str = ""
    url: str = ""
    kind: str = "rss"
    column: str = "ai"
    home_url: str = ""
    added_at: str = ""


@dataclass(frozen=True)
class Roster:
    disabled: frozenset[str] = frozenset()
    custom: tuple[CustomSource, ...] = ()


def custom_id(url: str) -> str:
    """``custom-`` + eight hex of the normalised URL — the same feed is always the same id."""
    digest = hashlib.sha1(normalise_url(url).encode("utf-8")).hexdigest()  # noqa: S324 - an id, not a secret
    return f"{CUSTOM_PREFIX}{digest[:8]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _store_path() -> Path:
    from yeaboi.paths import get_news_roster_path

    return get_news_roster_path()


_cache: dict = {"mtime": None, "roster": Roster()}


def invalidate() -> None:
    """Drop the cache — after every write, and by tests."""
    _cache.update({"mtime": None, "roster": Roster()})


def _custom_from_dict(raw: object) -> CustomSource | None:
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url", "") or "").strip()
    if not url.startswith("https://"):
        return None
    kind = str(raw.get("kind", "rss") or "rss")
    column = str(raw.get("column", "ai") or "ai")
    if kind not in CUSTOM_KINDS or column not in COLUMNS:
        return None
    return CustomSource(
        id=custom_id(url),
        name=str(raw.get("name", "") or "").strip()[:NAME_MAX],
        url=url,
        kind=kind,
        column=column,
        home_url=web_link(raw.get("home_url")),
        added_at=str(raw.get("added_at", "") or ""),
    )


def load_roster() -> Roster:
    """The saved roster, tolerant of a damaged file: a warning and an empty roster, never a crash."""
    path = _store_path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return Roster()
    if _cache["mtime"] == mtime:
        return _cache["roster"]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("news: roster %s is unreadable — ignoring it", path.name)
        return Roster()
    if not isinstance(raw, dict) or raw.get("version") != ROSTER_VERSION:
        logger.warning("news: roster %s has an unknown shape — ignoring it", path.name)
        return Roster()
    custom = []
    for entry in raw.get("custom") or []:
        parsed = _custom_from_dict(entry)
        if parsed is None:
            logger.warning("news: roster skipped an entry with no https url")
            continue
        custom.append(parsed)
    disabled = frozenset(str(item) for item in raw.get("disabled") or [] if isinstance(item, str))
    roster = Roster(disabled=disabled, custom=tuple(custom))
    _cache.update({"mtime": mtime, "roster": roster})
    return roster


def save_roster(roster: Roster) -> None:
    path = _store_path()
    payload = {
        "version": ROSTER_VERSION,
        "disabled": sorted(roster.disabled),
        "custom": [asdict(source) for source in roster.custom],
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    invalidate()
    logger.info("news: roster written — %d disabled, %d custom", len(roster.disabled), len(roster.custom))


def roster_problems(
    *,
    url: str,
    name: str,
    column: str,
    kind: str,
    roster: Roster,
    builtin: Sequence[NewsSource] = SOURCES,
) -> list[str]:
    """Every reason not to add this outlet, in order; empty when it may be added."""
    problems: list[str] = []
    url = (url or "").strip()
    if not url.startswith("https://"):
        problems.append("url must start with https://")
    else:
        from yeaboi.connectors.http import UnsafeUrlError, assert_safe_url

        try:
            assert_safe_url(url)
        except UnsafeUrlError as exc:
            problems.append(f"url must be a public host: {exc}")
    if not 1 <= len((name or "").strip()) <= NAME_MAX:
        problems.append(f"name must be 1–{NAME_MAX} characters")
    if column not in COLUMNS:
        problems.append(f"column must be one of {', '.join(COLUMNS)}")
    if kind not in CUSTOM_KINDS:
        problems.append(f"kind must be one of {', '.join(CUSTOM_KINDS)}")
    if len(roster.custom) >= MAX_CUSTOM:
        problems.append(f"at most {MAX_CUSTOM} outlets can be added")
    if url.startswith("https://"):
        key = normalise_url(url)
        if any(normalise_url(source.url) == key for source in builtin):
            problems.append("that feed is already on the front page")
        elif any(source.id == custom_id(url) for source in roster.custom):
            problems.append("that feed has already been added")
    return problems


def add_custom(
    *,
    url: str,
    name: str,
    column: str,
    kind: str,
    home_url: str = "",
    now: Callable[[], str] = _now_iso,
) -> CustomSource:
    """Validate and save one outlet. Raises ValueError with every problem, and writes nothing then."""
    roster = load_roster()
    problems = roster_problems(url=url, name=name, column=column, kind=kind, roster=roster)
    if problems:
        logger.info("news: roster rejected %s: %s", urlsplit(url.strip()).hostname or "?", "; ".join(problems))
        raise ValueError("; ".join(problems))
    url = url.strip()
    added = CustomSource(
        id=custom_id(url),
        name=name.strip(),
        url=url,
        kind=kind,
        column=column,
        home_url=web_link(home_url),
        added_at=now(),
    )
    save_roster(replace(roster, custom=(*roster.custom, added)))
    logger.info("news: roster added %s (%s, %s) at %s", added.id, kind, column, urlsplit(url).hostname or "?")
    return added


def remove_custom(source_id: str) -> bool:
    """Remove one added outlet. False when it was never there."""
    roster = load_roster()
    kept = tuple(source for source in roster.custom if source.id != source_id)
    if len(kept) == len(roster.custom):
        return False
    save_roster(Roster(disabled=roster.disabled - {source_id}, custom=kept))
    logger.info("news: roster removed %s", source_id)
    return True


def known_ids(roster: Roster, *, youtube_channel: str = "") -> frozenset[str]:
    """Every id the roster can switch: the registry, the channel when set, and the added outlets."""
    return frozenset(source.id for source in active_sources(youtube_channel=youtube_channel)) | frozenset(
        source.id for source in roster.custom
    )


def set_enabled(source_id: str, enabled: bool, *, youtube_channel: str = "") -> Roster:
    """Switch one outlet. Raises KeyError for an id the roster does not know."""
    roster = load_roster()
    if source_id not in known_ids(roster, youtube_channel=youtube_channel):
        raise KeyError(source_id)
    disabled = roster.disabled - {source_id} if enabled else roster.disabled | {source_id}
    if disabled != roster.disabled:
        roster = replace(roster, disabled=disabled)
        save_roster(roster)
        logger.info("news: roster %s %s", "enabled" if enabled else "disabled", source_id)
    return roster


def as_source(custom: CustomSource) -> NewsSource:
    return NewsSource(
        custom.id,
        custom.name,
        custom.url,
        kind=custom.kind,
        column=custom.column,
        home_url=custom.home_url,
        max_items=CUSTOM_MAX_ITEMS,
        builtin=False,
    )


def sources_for(roster: Roster, *, youtube_channel: str = "") -> tuple[NewsSource, ...]:
    """What the desk fetches: the registry and the added outlets, minus the switched-off ones."""
    everything = (*active_sources(youtube_channel=youtube_channel), *(as_source(c) for c in roster.custom))
    return tuple(source for source in everything if source.id not in roster.disabled)


def roster_sources(*, youtube_channel: str = "") -> tuple[NewsSource, ...]:
    """The desk's default source list: the saved roster applied to the registry."""
    return sources_for(load_roster(), youtube_channel=youtube_channel)


def source_rows(
    roster: Roster,
    statuses: Mapping[str, SourceStatus],
    *,
    youtube_channel: str = "",
) -> list[dict]:
    """The Settings list: one row per known outlet, with the last refresh's health merged in by id."""
    rows = []
    everything = (*active_sources(youtube_channel=youtube_channel), *(as_source(c) for c in roster.custom))
    for source in everything:
        status = statuses.get(source.id)
        rows.append(
            {
                "id": source.id,
                "name": source.name,
                "home_url": source.home_url,
                "url": source.url,
                "column": source.column,
                "kind": source.kind,
                "builtin": source.builtin,
                "enabled": source.id not in roster.disabled,
                "ok": status.ok if status is not None else None,
                "fetched_at": status.fetched_at if status is not None else "",
                "error": status.error if status is not None else "",
                "item_count": status.item_count if status is not None else 0,
            }
        )
    return rows
