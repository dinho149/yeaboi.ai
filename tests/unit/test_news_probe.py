"""The probe (src/yeaboi/news/probe.py): one look at a URL, no network."""

from __future__ import annotations

import pytest

from yeaboi.news import probe as module
from yeaboi.news.fetch import FetchResult
from yeaboi.news.probe import feed_identity, feed_links, probe, sniff

RSS = (
    b'<rss version="2.0"><channel><title>Lobsters</title><link>https://lobste.rs/</link>'
    b"<item><title>One</title><link>https://lobste.rs/s/1</link></item>"
    b"<item><title>Two</title><link>https://lobste.rs/s/2</link></item>"
    b"<item><title>Three</title><link>https://lobste.rs/s/3</link></item>"
    b"<item><title>Four</title><link>https://lobste.rs/s/4</link></item>"
    b"</channel></rss>"
)
ATOM = (
    b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Simon</title>'
    b'<link rel="self" href="https://s.example/atom"/><link rel="alternate" href="https://s.example/"/>'
    b'<entry><title>Post</title><link rel="alternate" href="https://s.example/p"/></entry></feed>'
)
JSON_FEED = (
    b'{"version": "https://jsonfeed.org/version/1.1", "title": "yeaboi news", "home_page_url": "https://yeaboi.ai/",'
    b' "items": [{"id": "1", "url": "https://yeaboi.ai/p", "title": "Hello"}]}'
)
HTML_WITH_FEED = (
    b"<!DOCTYPE html><html><head><title>Site</title>"
    b'<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
    b'<link rel="alternate" type="application/atom+xml" href="http://insecure.example/atom">'
    b"</head><body>hi</body></html>"
)
HTML_PLAIN = b"<html><body>nothing here</body></html>"


def _fetch(body: bytes = b"", error: str = ""):
    seen = []

    def fetch(source, conditional):
        seen.append(source)
        if error:
            return FetchResult(source_id=source.id, error=error, fetched_at="t")
        return FetchResult(source_id=source.id, status=200, body=body, fetched_at="t")

    fetch.seen = seen  # type: ignore[attr-defined]
    return fetch


class TestSniff:
    @pytest.mark.parametrize(
        ("body", "kind"),
        [
            (RSS, "rss"),
            (b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><channel/></rdf:RDF>', "rss"),
            (ATOM, "atom"),
            (JSON_FEED, "json_feed"),
            (HTML_WITH_FEED, "html"),
            (HTML_PLAIN, "html"),
            (b'{"version": 1}', ""),
            (b"garbage", ""),
            (b"", ""),
        ],
    )
    def test_kinds(self, body, kind):
        assert sniff(body) == kind


class TestFeedLinks:
    def test_finds_https_alternates_resolved_against_the_page(self):
        assert feed_links(HTML_WITH_FEED, "https://site.example/blog/") == ("https://site.example/feed.xml",)

    def test_none_on_a_plain_page(self):
        assert feed_links(HTML_PLAIN, "https://site.example/") == ()


class TestFeedIdentity:
    def test_rss(self):
        assert feed_identity(RSS, "rss") == ("Lobsters", "https://lobste.rs/")

    def test_atom_takes_the_alternate_link(self):
        assert feed_identity(ATOM, "atom") == ("Simon", "https://s.example/")

    def test_json_feed(self):
        assert feed_identity(JSON_FEED, "json_feed") == ("yeaboi news", "https://yeaboi.ai/")

    def test_a_feed_with_no_title(self):
        assert feed_identity(b"<rss><channel><item/></channel></rss>", "rss") == ("", "")


class TestProbe:
    def test_http_is_refused_before_any_fetch(self):
        fetch = _fetch(RSS)
        result = probe("http://lobste.rs/rss", fetch=fetch)
        assert result.ok is False and "https" in result.error
        assert fetch.seen == []

    @pytest.mark.parametrize("error", ["http 404", "oversize", "UnsafeUrlError: host resolves to a private address"])
    def test_a_fetch_error_is_the_verdict(self, error):
        result = probe("https://x.example/f", fetch=_fetch(error=error))
        assert result.ok is False and result.error == error

    def test_a_page_with_a_feed_link_offers_it(self):
        result = probe("https://site.example/", fetch=_fetch(HTML_WITH_FEED))
        assert result.ok is False
        assert result.feed_url == "https://site.example/feed.xml"
        assert "web page" in result.error and result.feed_url in result.error

    def test_a_page_without_one_says_where_to_look(self):
        result = probe("https://site.example/", fetch=_fetch(HTML_PLAIN))
        assert result.feed_url == "" and "RSS or Atom" in result.error

    def test_not_a_feed_at_all(self):
        assert "expected RSS" in probe("https://x.example/f", fetch=_fetch(b"garbage")).error

    def test_a_feed_with_no_items(self):
        result = probe("https://x.example/f", fetch=_fetch(b"<rss><channel><title>Empty</title></channel></rss>"))
        assert result.ok is False and result.kind == "rss" and result.name == "Empty"
        assert "no readable items" in result.error

    def test_a_good_feed(self):
        fetch = _fetch(RSS)
        result = probe(" https://lobste.rs/rss ", fetch=fetch)
        assert result.ok is True
        assert result.url == "https://lobste.rs/rss"
        assert result.kind == "rss" and result.name == "Lobsters" and result.home_url == "https://lobste.rs/"
        assert result.item_count == 4 and result.sample_titles == ("One", "Two", "Three")
        assert fetch.seen[0].builtin is False and fetch.seen[0].id == module.PROBE_ID

    def test_a_nameless_feed_is_named_after_its_host(self):
        body = b"<rss><channel><item><title>T</title><link>https://x.example/1</link></item></channel></rss>"
        assert probe("https://x.example/f", fetch=_fetch(body)).name == "x.example"

    def test_an_atom_body_under_an_rss_label_still_parses(self):
        result = probe("https://s.example/atom", fetch=_fetch(ATOM))
        assert result.ok is True and result.kind == "atom" and result.item_count == 1
