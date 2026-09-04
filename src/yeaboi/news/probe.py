"""Looking at a URL before it becomes an outlet: is it a feed, what is it called, how many stories.

One guarded fetch through the same edge a refresh uses (same timeout and size
cap, the SSRF guard on, redirects re-checked), then the body is sniffed rather
than trusted: a JSON Feed, an Atom or RSS document, or a web page — in which
case the page's own ``<link rel="alternate">`` is offered back as the feed to
try. Never saves anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from yeaboi.news.fetch import Conditional, Fetcher, fetch_one
from yeaboi.news.parse import _ATOM, _load_json, _safe_root, _text, clip, parse_feed, strip_html
from yeaboi.news.sources import NewsSource

logger = logging.getLogger(__name__)

PROBE_ID = "probe"
SAMPLE_TITLES = 3
NAME_MAX = 60
_FEED_TYPES = frozenset({"application/rss+xml", "application/atom+xml", "application/feed+json", "application/json"})
_JSON_FEED_VERSION = "https://jsonfeed.org/version/"


@dataclass(frozen=True)
class Probe:
    ok: bool = False
    url: str = ""
    # Set when the body was a web page that advertises a feed.
    feed_url: str = ""
    kind: str = ""
    name: str = ""
    home_url: str = ""
    item_count: int = 0
    sample_titles: tuple[str, ...] = ()
    error: str = ""


class _FeedLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        found = {key: (value or "") for key, value in attrs}
        rels = found.get("rel", "").lower().split()
        if "alternate" in rels and found.get("type", "").lower().strip() in _FEED_TYPES and found.get("href"):
            self.hrefs.append(found["href"])


def sniff(body: bytes) -> str:
    """``json_feed``, ``atom``, ``rss``, ``html``, or "" for anything else."""
    data = _load_json(body)
    if data is not None:
        version = data.get("version")
        if isinstance(version, str) and version.startswith(_JSON_FEED_VERSION) and isinstance(data.get("items"), list):
            return "json_feed"
        return ""
    root = _safe_root(body)
    if root is not None:
        if root.tag == f"{_ATOM}feed":
            return "atom"
        if root.tag == "rss" or root.tag.endswith("}RDF"):
            return "rss"
    head = body[:4096].lower()
    if b"<html" in head or b"<!doctype html" in head:
        return "html"
    return ""


def feed_links(body: bytes, base_url: str) -> tuple[str, ...]:
    """The https feeds a web page advertises, in page order."""
    parser = _FeedLinks()
    try:
        parser.feed(body.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - a page that will not parse advertises nothing
        return ()
    links = (urljoin(base_url, href) for href in parser.hrefs)
    return tuple(link for link in links if link.startswith("https://"))


def feed_identity(body: bytes, kind: str) -> tuple[str, str]:
    """The feed's own title and home link, as far as it states them."""
    if kind == "json_feed":
        data = _load_json(body) or {}
        return clip(strip_html(str(data.get("title", "") or "")), NAME_MAX), str(data.get("home_page_url", "") or "")
    root = _safe_root(body)
    if root is None:
        return "", ""
    if kind == "atom":
        home = next(
            (
                link.get("href", "")
                for link in root.findall(f"{_ATOM}link")
                if link.get("rel", "alternate") == "alternate" and link.get("href")
            ),
            "",
        )
        return clip(strip_html(_text(root, f"{_ATOM}title")), NAME_MAX), home
    channel = root.find("channel")
    if channel is None:
        return "", ""
    return clip(strip_html(_text(channel, "title")), NAME_MAX), _text(channel, "link")


def probe(url: str, *, fetch: Fetcher = fetch_one) -> Probe:
    """Look at one URL. Never raises; the verdict is in the result."""
    url = (url or "").strip()
    if not url.startswith("https://"):
        return Probe(url=url, error="url must start with https://")
    result = fetch(NewsSource(id=PROBE_ID, url=url, kind="rss", builtin=False), Conditional())
    if result.error:
        logger.info("news: probe %s rejected: %s", url, result.error)
        return Probe(url=url, error=result.error)
    kind = sniff(result.body)
    if kind == "html":
        links = feed_links(result.body, url)
        hint = f"it lists one at {links[0]}" if links else "look for the site's RSS or Atom link"
        logger.info("news: probe %s is a web page (%d feed links)", url, len(links))
        return Probe(url=url, feed_url=links[0] if links else "", error=f"not a feed — this is a web page; {hint}")
    if not kind:
        logger.info("news: probe %s rejected: not a feed", url)
        return Probe(url=url, error="not a feed — expected RSS, Atom or JSON Feed")
    name, home_url = feed_identity(result.body, kind)
    items = parse_feed(NewsSource(id=PROBE_ID, name=name, url=url, kind=kind, builtin=False), result.body)
    if not items:
        logger.info("news: probe %s rejected: %s with no readable items", url, kind)
        return Probe(url=url, kind=kind, name=name, home_url=home_url, error="feed has no readable items")
    logger.info("news: probe %s → %s, %d items", url, kind, len(items))
    return Probe(
        ok=True,
        url=url,
        kind=kind,
        name=name or (urlsplit(url).hostname or ""),
        home_url=home_url,
        item_count=len(items),
        sample_titles=tuple(item.title for item in items[:SAMPLE_TITLES]),
    )
