"""Bytes from an outlet → NewsItems, one parser per feed shape.

RSS 2.0, Atom, JSON Feed, the Hacker News search JSON, YouTube's Atom, and
the listing pages of outlets that publish no feed. Every parser is total: a
body it cannot read yields no items and a warning, never an exception.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import xml.etree.ElementTree as ET  # noqa: S405 - bodies are guarded in _safe_root before parsing
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from yeaboi.news.fetch import MAX_BODY_BYTES
from yeaboi.news.sources import NewsSource
from yeaboi.timeparse import parse_datetime

logger = logging.getLogger(__name__)

SUMMARY_MAX = 240
KINDS = ("article", "video", "release", "post")

_ATOM = "{http://www.w3.org/2005/Atom}"
_MEDIA = "{http://search.yahoo.com/mrss/}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
_YT = "{http://www.youtube.com/xml/schemas/2015}"

# Query keys that track a click rather than name a page.
_TRACKING_KEYS = ("fbclid", "gclid", "ref", "mc_cid", "mc_eid")


@dataclass(frozen=True)
class NewsItem:
    """One headline on the wire. ``topic``/``persona``/``column``/``source_name`` are the engine's."""

    id: str = ""
    title: str = ""
    url: str = ""
    source_id: str = ""
    source_name: str = ""
    published: str = ""
    summary: str = ""
    image_url: str | None = None
    kind: str = "article"
    topic: str = ""
    persona: str = ""
    column: str = ""


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


# Tags that end a run of text; a space stands in for the break they made.
_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td", "blockquote"}
)


class _TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(raw: str) -> str:
    """Plain text: tags gone, entities unescaped, whitespace folded."""
    if not raw:
        return ""
    parser = _TextOnly()
    try:
        parser.feed(raw)
        parser.close()
        text = "".join(parser.parts)
    except Exception:  # noqa: BLE001 - a summary is decoration; never let it break a headline
        text = re.sub(r"<[^>]+>", " ", raw)
    return " ".join(html.unescape(text).split())


def clip(text: str, limit: int = SUMMARY_MAX) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return cut + "…"


def web_link(value: object) -> str:
    """A link a feed states, kept only when it is one the browser may open."""
    text = str(value or "").strip()
    return text if text.startswith(("https://", "http://")) else ""


def normalise_url(url: str) -> str:
    """The URL as a key: folded host, no fragment, no tracking query, no trailing slash."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_KEYS
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def item_id(url: str) -> str:
    return hashlib.sha1(normalise_url(url).encode("utf-8")).hexdigest()[:16]  # noqa: S324 - a key, not a secret


_MONTH_NAMES = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTHS = {name: number for number, name in enumerate(_MONTH_NAMES, 1)}
_LISTING_DATE_RE = re.compile(r"\b([A-Z][a-z]{2,8})\.? (\d{1,2}), (\d{4})\b")


def parse_date(raw: str) -> str:
    """ISO 8601 with an offset, from the three shapes outlets use; "" when none fits."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    parsed = _iso(raw) or _rfc822(raw) or _listing_date(raw)
    if parsed is None:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat(timespec="seconds")


def _iso(raw: str) -> datetime | None:
    try:
        return parse_datetime(raw)
    except (TypeError, ValueError):
        return None


def _rfc822(raw: str) -> datetime | None:
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None


def _listing_date(raw: str) -> datetime | None:
    match = _LISTING_DATE_RE.search(raw)
    if match is None:
        return None
    month = _MONTHS.get(match.group(1)[:3].lower())
    if month is None:
        return None
    try:
        return datetime(int(match.group(3)), month, int(match.group(2)), tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------


_PROLOG_RE = re.compile(rb"\s*(?:<\?xml[^>]*\?>)?\s*(?:<!--.*?-->\s*)*", re.S)


def _has_dtd(body: bytes) -> bool:
    """Whether a DOCTYPE opens the document. One inside a CDATA description is text, not a DTD."""
    prolog = _PROLOG_RE.match(body[:8192])
    rest = body[prolog.end() : prolog.end() + 16] if prolog else body[:16]
    return rest.upper().startswith(b"<!DOCTYPE")


def _safe_root(body: bytes) -> ET.Element | None:
    """Parse a feed body, refusing anything a feed never carries."""
    if not body or len(body) > MAX_BODY_BYTES:
        return None
    if _has_dtd(body):
        logger.warning("news: refusing a body with a DTD")
        return None
    try:
        return ET.fromstring(body)  # noqa: S314 - DTDs refused above; the inputs are our own registry's feeds
    except ET.ParseError:
        return None


def _text(element: ET.Element | None, *tags: str) -> str:
    if element is None:
        return ""
    for tag in tags:
        child = element.find(tag)
        if child is not None and child.text:
            return child.text.strip()
    return ""


def _media_image(element: ET.Element) -> str | None:
    """The item's own picture: a media thumbnail, an image media:content, or an image enclosure."""
    for path in (f"{_MEDIA}thumbnail", f"{_MEDIA}group/{_MEDIA}thumbnail"):
        node = element.find(path)
        if node is not None and node.get("url"):
            return node.get("url")
    for node in element.findall(f"{_MEDIA}content"):
        if node.get("url") and (node.get("medium") == "image" or "image" in node.get("type", "")):
            return node.get("url")
    for node in element.findall("enclosure"):
        if node.get("url") and "image" in node.get("type", ""):
            return node.get("url")
    return None


def parse_rss(source: NewsSource, root: ET.Element) -> tuple[NewsItem, ...]:
    items = []
    for node in root.iter("item"):
        title = strip_html(_text(node, "title"))
        url = _text(node, "link", "guid")
        if not title or not url.startswith("http"):
            continue
        items.append(
            NewsItem(
                id=item_id(url),
                title=title,
                url=url,
                source_id=source.id,
                published=parse_date(_text(node, "pubDate", f"{_DC}date")),
                summary=clip(strip_html(_text(node, "description", f"{_CONTENT}encoded"))),
                image_url=_media_image(node),
            )
        )
    return tuple(items)


def _atom_link(entry: ET.Element) -> str:
    links = entry.findall(f"{_ATOM}link")
    for link in links:
        if link.get("rel", "alternate") == "alternate" and link.get("href"):
            return link.get("href", "")
    return links[0].get("href", "") if links else ""


def parse_atom(source: NewsSource, root: ET.Element) -> tuple[NewsItem, ...]:
    items = []
    for entry in root.iter(f"{_ATOM}entry"):
        title = strip_html(_text(entry, f"{_ATOM}title"))
        url = _atom_link(entry)
        if not title or not url.startswith("http"):
            continue
        items.append(
            NewsItem(
                id=item_id(url),
                title=title,
                url=url,
                source_id=source.id,
                published=parse_date(_text(entry, f"{_ATOM}published", f"{_ATOM}updated")),
                summary=clip(strip_html(_text(entry, f"{_ATOM}summary", f"{_ATOM}content"))),
                image_url=_media_image(entry),
            )
        )
    return tuple(items)


def parse_youtube(source: NewsSource, root: ET.Element) -> tuple[NewsItem, ...]:
    items = []
    for entry in root.iter(f"{_ATOM}entry"):
        video_id = _text(entry, f"{_YT}videoId")
        title = strip_html(_text(entry, f"{_ATOM}title"))
        if not video_id or not title:
            continue
        url = f"https://www.youtube.com/watch?v={video_id}"
        group = entry.find(f"{_MEDIA}group")
        thumb = group.find(f"{_MEDIA}thumbnail") if group is not None else None
        items.append(
            NewsItem(
                id=item_id(url),
                title=title,
                url=url,
                source_id=source.id,
                published=parse_date(_text(entry, f"{_ATOM}published", f"{_ATOM}updated")),
                summary=clip(strip_html(_text(group, f"{_MEDIA}description"))),
                image_url=thumb.get("url") if thumb is not None and thumb.get("url") else None,
                kind="video",
            )
        )
    return tuple(items)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


def _load_json(body: bytes) -> dict | None:
    if not body or len(body) > MAX_BODY_BYTES:
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def parse_json_feed(source: NewsSource, data: dict) -> tuple[NewsItem, ...]:
    items = []
    for raw in data.get("items") or []:
        if not isinstance(raw, dict):
            continue
        title = strip_html(str(raw.get("title") or ""))
        url = str(raw.get("url") or "")
        if not title or not url.startswith("http"):
            continue
        extension = raw.get("_yeaboi") if isinstance(raw.get("_yeaboi"), dict) else {}
        kind = extension.get("kind") if extension.get("kind") in KINDS else "post"
        image = raw.get("image") if isinstance(raw.get("image"), str) and raw.get("image") else None
        items.append(
            NewsItem(
                id=item_id(url),
                title=title,
                url=url,
                source_id=source.id,
                published=parse_date(str(raw.get("date_published") or "")),
                summary=clip(strip_html(str(raw.get("summary") or raw.get("content_text") or ""))),
                image_url=image,
                kind=kind,
            )
        )
    return tuple(items)


def parse_hn(source: NewsSource, data: dict) -> tuple[NewsItem, ...]:
    items = []
    for hit in data.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        title = strip_html(str(hit.get("title") or ""))
        object_id = str(hit.get("objectID") or "")
        url = str(hit.get("url") or "") or (f"https://news.ycombinator.com/item?id={object_id}" if object_id else "")
        if not title or not url.startswith("http"):
            continue
        points = hit.get("points")
        comments = hit.get("num_comments")
        summary = f"{points} points, {comments} comments on Hacker News." if points is not None else ""
        items.append(
            NewsItem(
                id=item_id(url),
                title=title,
                url=url,
                source_id=source.id,
                published=parse_date(str(hit.get("created_at") or "")),
                summary=summary,
            )
        )
    return tuple(items)


# ---------------------------------------------------------------------------
# Listing pages
# ---------------------------------------------------------------------------

# How much text around a card may still name its title or date.
_TRAILER_CHARS = 160
_PAGINATION_RE = re.compile(r"(?:[?&]page=|/page/\d)")
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_MIN_TITLE = 8


class _Listing(HTMLParser):
    """Every card link on a listing page, with the text inside it and around it.

    Outlets lay a card out two ways: the title and date inside the link
    (anthropic.com), or beside a link that says only "Read more" and carries
    the title in ``data-cta-copy`` (claude.com). Both are read.
    """

    def __init__(self, prefix: str) -> None:
        super().__init__(convert_charrefs=True)
        self.prefix = prefix
        self.cards: list[dict] = []
        self._open: dict | None = None
        self._depth = 0
        self._heading = 0
        # Text since the last card closed: the next card's "before".
        self._between: list[tuple[str, bool]] = []
        self._trailing: dict | None = None

    def _is_card_link(self, href: str) -> bool:
        path = href
        if href.startswith("http"):
            path = urlsplit(href).path
        if not path.startswith(self.prefix) or path.rstrip("/") == self.prefix.rstrip("/"):
            return False
        return not _PAGINATION_RE.search(href)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _HEADINGS:
            self._heading += 1
        if self._open is not None:
            if tag == "a":
                self._depth += 1
            return
        if tag != "a":
            return
        given = dict(attrs)
        href = given.get("href") or ""
        if href and self._is_card_link(href):
            self._trailing = None
            self._open = {
                "href": href,
                "cta": " ".join((given.get("data-cta-copy") or "").split()),
                "before": list(self._between),
                "inside": [],
                "after": [],
            }
            self._depth = 0

    def handle_endtag(self, tag: str) -> None:
        if tag in _HEADINGS:
            self._heading = max(0, self._heading - 1)
        if tag != "a" or self._open is None:
            return
        if self._depth:
            self._depth -= 1
            return
        self.cards.append(self._open)
        self._trailing = self._open
        self._open = None
        self._between = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        chunk = (text, self._heading > 0)
        if self._open is not None:
            self._open["inside"].append(chunk)
            return
        self._between.append(chunk)
        if self._trailing is not None and sum(len(t) for t, _ in self._trailing["after"]) < _TRAILER_CHARS:
            self._trailing["after"].append(chunk)


# A link that only says to follow it; the title is beside it, not in it.
_CTA_RE = re.compile(r"^(?:read|learn|see|view|continue|watch|explore)\b", re.IGNORECASE)


def _is_date(text: str) -> bool:
    return _LISTING_DATE_RE.search(text) is not None


def _card_title(card: dict) -> str:
    if card["cta"]:
        return card["cta"]
    inside = [text for text, _ in card["inside"] if not _is_date(text)]
    headings = [text for text, is_heading in card["inside"] if is_heading and not _is_date(text)]
    if headings:
        return max(headings, key=len)
    longest = max(inside, key=len, default="")
    if len(longest) >= _MIN_TITLE and not _CTA_RE.match(longest):
        return longest
    before = [text for text, is_heading in reversed(card["before"]) if is_heading and not _is_date(text)]
    return before[0] if before else ""


def _nearest_date(chunks: list[tuple[str, bool]]) -> tuple[int, str]:
    """The first date in ``chunks`` and how much text stands before it; no date is infinitely far."""
    distance = 0
    for text, _ in chunks:
        if _is_date(text):
            return distance, text
        distance += len(text)
    return 1 << 30, ""


def _card_date(card: dict) -> str:
    inside = next((text for text, _ in card["inside"] if _is_date(text)), "")
    if inside:
        return inside
    # A date beside the link belongs to the nearer card: the one before it or the one after it.
    return min(_nearest_date(list(reversed(card["before"]))), _nearest_date(card["after"]))[1]


def parse_listing(source: NewsSource, body: bytes) -> tuple[NewsItem, ...]:
    if not body or len(body) > MAX_BODY_BYTES or not source.link_prefix:
        return ()
    parser = _Listing(source.link_prefix)
    try:
        parser.feed(body.decode("utf-8", errors="replace"))
        parser.close()
    except Exception:  # noqa: BLE001 - an unreadable page is an empty page
        return ()
    by_url: dict[str, NewsItem] = {}
    for card in parser.cards:
        title = _card_title(card)
        if len(title) < _MIN_TITLE:
            continue
        url = urljoin(source.url, card["href"])
        item = NewsItem(
            id=item_id(url),
            title=title,
            url=url,
            source_id=source.id,
            published=parse_date(_card_date(card)),
            kind="post",
        )
        key = normalise_url(url)
        if key not in by_url or len(item.title) > len(by_url[key].title):
            by_url[key] = item
    return tuple(by_url.values())


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def parse_feed(source: NewsSource, body: bytes) -> tuple[NewsItem, ...]:
    """Parse ``body`` as ``source.kind`` says. Never raises."""
    try:
        if source.kind in ("rss", "atom", "youtube"):
            root = _safe_root(body)
            if root is None:
                return ()
            if source.kind == "youtube":
                return parse_youtube(source, root)
            # Atom feeds sometimes arrive under an RSS label and vice versa.
            if root.tag == f"{_ATOM}feed":
                return parse_atom(source, root)
            return parse_rss(source, root)
        if source.kind in ("json_feed", "hn"):
            data = _load_json(body)
            if data is None:
                return ()
            return parse_hn(source, data) if source.kind == "hn" else parse_json_feed(source, data)
        if source.kind == "html_listing":
            return parse_listing(source, body)
    except Exception:  # noqa: BLE001 - one outlet's surprise must not take the paper down
        logger.warning("news: %s could not be parsed", source.id, exc_info=True)
    return ()
