"""The yeaboi column's offline half: the bundled release notes as headlines."""

from __future__ import annotations

from collections.abc import Sequence

from yeaboi.changelog import ChangelogEntry
from yeaboi.news.parse import NewsItem, clip, item_id, parse_date

SOURCE_ID = "yeaboi-changelog"
SOURCE_NAME = "yeaboi"
RELEASE_URL = "https://pypi.org/project/yeaboi/{version}/"


def changelog_items(entries: Sequence[ChangelogEntry] | None = None, *, limit: int = 5) -> tuple[NewsItem, ...]:
    """The newest releases as items, ready for the yeaboi column. Never touches the network."""
    if entries is None:
        from yeaboi.changelog import load_changelog

        entries = load_changelog()
    items = []
    for entry in entries[:limit]:
        if not entry.version:
            continue
        url = RELEASE_URL.format(version=entry.version)
        items.append(
            NewsItem(
                id=item_id(url),
                title=f"yeaboi {entry.version}: {entry.headline}" if entry.headline else f"yeaboi {entry.version}",
                url=url,
                source_id=SOURCE_ID,
                source_name=SOURCE_NAME,
                published=parse_date(entry.date),
                summary=clip(entry.summary),
                kind="release",
                topic="models",
                persona="wizard",
                column="yeaboi",
            )
        )
    return tuple(items)
