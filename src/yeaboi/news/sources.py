"""The curated outlet registry — every URL the front page ever fetches.

Fixed https constants, chosen for a clean headline-plus-link story and no API
key. The one variable part is the YouTube channel id, validated before it is
ever interpolated. Anthropic publishes no feed, so its pages are read as
listings (``kind="html_listing"``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

KINDS = ("rss", "atom", "json_feed", "hn", "youtube", "html_listing")
COLUMNS = ("yeaboi", "ai", "engineering")


@dataclass(frozen=True)
class NewsSource:
    """One outlet: where its headlines are, and which column they fill."""

    id: str = ""
    name: str = ""
    url: str = ""
    kind: str = "rss"
    column: str = "ai"
    home_url: str = ""
    max_items: int = 6
    # The politest gap between two requests, for outlets that publish one.
    min_interval_seconds: int = 0
    # html_listing only: the path every post link starts with.
    link_prefix: str = ""


SOURCES: tuple[NewsSource, ...] = (
    NewsSource(
        "yeaboi-site",
        "yeaboi.ai",
        "https://yeaboi.ai/news/feed.json",
        kind="json_feed",
        column="yeaboi",
        home_url="https://yeaboi.ai/",
        max_items=8,
    ),
    NewsSource(
        "deepmind",
        "Google DeepMind",
        "https://deepmind.google/blog/rss.xml",
        column="ai",
        home_url="https://deepmind.google/blog/",
    ),
    NewsSource(
        "google-ai",
        "Google AI",
        "https://blog.google/technology/ai/rss/",
        column="ai",
        home_url="https://blog.google/technology/ai/",
    ),
    NewsSource("openai", "OpenAI", "https://openai.com/news/rss.xml", column="ai", home_url="https://openai.com/news/"),
    NewsSource(
        "claude-blog",
        "Claude",
        "https://claude.com/blog",
        kind="html_listing",
        column="ai",
        home_url="https://claude.com/blog",
        link_prefix="/blog/",
    ),
    NewsSource(
        "anthropic-news",
        "Anthropic",
        "https://www.anthropic.com/news",
        kind="html_listing",
        column="ai",
        home_url="https://www.anthropic.com/news",
        link_prefix="/news/",
    ),
    NewsSource(
        "hn-ai",
        "Hacker News",
        "https://hn.algolia.com/api/v1/search_by_date?query=AI&tags=story&numericFilters=points%3E100",
        kind="hn",
        column="ai",
        home_url="https://news.ycombinator.com/",
    ),
    NewsSource(
        "techmeme", "Techmeme", "https://www.techmeme.com/feed.xml", column="ai", home_url="https://www.techmeme.com/"
    ),
    NewsSource(
        "mit-tr-ai",
        "MIT Technology Review",
        "https://www.technologyreview.com/topic/artificial-intelligence/feed/",
        column="ai",
        home_url="https://www.technologyreview.com/topic/artificial-intelligence/",
    ),
    NewsSource(
        "arstechnica-ai",
        "Ars Technica",
        "https://arstechnica.com/ai/feed/",
        column="ai",
        home_url="https://arstechnica.com/ai/",
    ),
    NewsSource(
        "anthropic-engineering",
        "Anthropic Engineering",
        "https://www.anthropic.com/engineering",
        kind="html_listing",
        column="engineering",
        home_url="https://www.anthropic.com/engineering",
        link_prefix="/engineering/",
    ),
    NewsSource(
        "simonwillison",
        "Simon Willison",
        "https://simonwillison.net/atom/everything/",
        kind="atom",
        column="engineering",
        home_url="https://simonwillison.net/",
    ),
    NewsSource(
        "infoq",
        "InfoQ",
        "https://feed.infoq.com/ai-ml-data-eng/",
        column="engineering",
        home_url="https://www.infoq.com/ai-ml-data-eng/",
    ),
    NewsSource(
        "github-changelog",
        "GitHub Changelog",
        "https://github.blog/changelog/feed/",
        column="engineering",
        home_url="https://github.blog/changelog/",
    ),
    NewsSource(
        "pragmatic-engineer",
        "The Pragmatic Engineer",
        "https://newsletter.pragmaticengineer.com/feed",
        column="engineering",
        home_url="https://newsletter.pragmaticengineer.com/",
    ),
)

YOUTUBE_FEED_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")

_YOUTUBE = NewsSource(
    "yeaboi-youtube",
    "yeaboi on YouTube",
    "",
    kind="youtube",
    column="yeaboi",
    home_url="https://www.youtube.com/",
    max_items=6,
)


def youtube_source(channel_id: str) -> NewsSource | None:
    """The channel's feed as a source, or None unless the id is a real channel id."""
    channel_id = (channel_id or "").strip()
    if not _CHANNEL_ID_RE.match(channel_id):
        return None
    return replace(
        _YOUTUBE,
        url=YOUTUBE_FEED_TEMPLATE.format(channel_id=channel_id),
        home_url=f"https://www.youtube.com/channel/{channel_id}",
    )


def active_sources(*, youtube_channel: str = "") -> tuple[NewsSource, ...]:
    """The registry plus the YouTube channel when one is configured."""
    extra = youtube_source(youtube_channel)
    return (*SOURCES, extra) if extra is not None else SOURCES


def source_by_id(source_id: str, sources: tuple[NewsSource, ...] = SOURCES) -> NewsSource | None:
    return next((source for source in sources if source.id == source_id), None)
