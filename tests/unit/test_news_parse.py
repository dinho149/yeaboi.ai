"""The feed parsers (src/yeaboi/news/parse.py), one class per shape."""

from __future__ import annotations

from pathlib import Path

import pytest

from yeaboi.news import parse
from yeaboi.news.fetch import MAX_BODY_BYTES
from yeaboi.news.sources import NewsSource

FIXTURES = Path(__file__).parent.parent / "fixtures" / "news"


def _body(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _source(kind: str, **kw) -> NewsSource:
    base = dict(id="test", name="Test", url="https://outlet.example/feed", kind=kind, column="ai")
    base.update(kw)
    return NewsSource(**base)


class TestRss:
    def test_items_come_through(self):
        items = parse.parse_feed(_source("rss"), _body("sample_rss.xml"))
        assert [item.title for item in items] == ["A new model & a new benchmark", "Second story"]

    def test_fields(self):
        first, second = parse.parse_feed(_source("rss"), _body("sample_rss.xml"))
        assert first.url.startswith("https://outlet.example/posts/new-model")
        assert first.published == "2026-09-03T14:05:00+00:00"
        assert first.image_url == "https://outlet.example/img/new-model.jpg"
        assert first.kind == "article"
        assert first.source_id == "test"
        assert second.published == "2026-09-02T09:30:00+00:00"
        assert second.image_url == "https://outlet.example/img/second.png"
        assert second.summary == "Plain teaser."

    def test_summary_is_plain_text_and_clipped(self):
        first = parse.parse_feed(_source("rss"), _body("sample_rss.xml"))[0]
        assert "<" not in first.summary
        assert first.summary.startswith("The lab released weights today. A long paragraph")
        assert len(first.summary) <= parse.SUMMARY_MAX
        assert first.summary.endswith("…")

    def test_ids_ignore_tracking_query(self):
        first = parse.parse_feed(_source("rss"), _body("sample_rss.xml"))[0]
        assert first.id == parse.item_id("https://outlet.example/posts/new-model")


class TestAtom:
    def test_entries_and_alternate_links(self):
        items = parse.parse_feed(_source("atom"), _body("sample_atom.xml"))
        assert [item.url for item in items] == [
            "https://weblog.example/2026/Sep/4/prompt-injection/",
            "https://weblog.example/2026/Sep/3/quote/",
        ]

    def test_published_beats_updated_and_offsets_survive(self):
        first, second = parse.parse_feed(_source("atom"), _body("sample_atom.xml"))
        assert first.published == "2026-09-04T07:15:00+00:00"
        assert second.published == "2026-09-03T18:00:00+01:00"

    def test_html_summary_is_stripped(self):
        first = parse.parse_feed(_source("atom"), _body("sample_atom.xml"))[0]
        assert first.summary == "A short note on an attack."
        assert first.image_url == "https://weblog.example/img/pi.jpg"

    def test_an_atom_body_under_an_rss_label_still_parses(self):
        items = parse.parse_feed(_source("rss"), _body("sample_atom.xml"))
        assert len(items) == 2


class TestYoutube:
    def test_videos(self):
        items = parse.parse_feed(_source("youtube"), _body("sample_youtube.xml"))
        assert len(items) == 1
        video = items[0]
        assert video.kind == "video"
        assert video.url == "https://www.youtube.com/watch?v=EWvNQjAaOHw"
        assert video.image_url == "https://i2.ytimg.com/vi/EWvNQjAaOHw/hqdefault.jpg"
        assert video.summary == "Epics, stories and a sprint plan in four minutes."
        assert video.published == "2026-09-02T16:00:00+00:00"


class TestJsonFeed:
    def test_items_and_kinds(self):
        items = parse.parse_feed(_source("json_feed"), _body("sample_jsonfeed.json"))
        assert [(item.title, item.kind) for item in items] == [
            ("Hello from the pond", "post"),
            ("yeaboi in two minutes", "video"),
            ("The Mac app", "post"),
        ]

    def test_fields(self):
        post, video, _ = parse.parse_feed(_source("json_feed"), _body("sample_jsonfeed.json"))
        assert post.image_url == "https://yeaboi.ai/assets/og-card.png"
        assert post.summary == "The first post."
        assert video.summary == "A walkthrough."
        assert video.published == "2026-09-01T10:00:00+00:00"
        assert video.image_url is None


class TestHackerNews:
    def test_hits(self):
        items = parse.parse_feed(_source("hn"), _body("sample_hn.json"))
        assert [item.url for item in items] == [
            "https://example.dev/agent",
            "https://news.ycombinator.com/item?id=45000002",
        ]

    def test_points_make_the_summary(self):
        first = parse.parse_feed(_source("hn"), _body("sample_hn.json"))[0]
        assert first.summary == "312 points, 88 comments on Hacker News."
        assert first.published == "2026-09-04T06:00:00+00:00"


class TestListing:
    SOURCE = _source("html_listing", url="https://claude.com/blog", link_prefix="/blog/")

    def test_cards_become_items_with_absolute_urls(self):
        items = parse.parse_feed(self.SOURCE, _body("sample_listing.html"))
        assert [item.url for item in items] == [
            "https://claude.com/blog/anatomy-of-commerce-agents",
            "https://claude.com/blog/claude-for-teachers",
            "https://claude.com/blog/how-warp-builds-agents",
            "https://claude.com/blog/claude-in-chrome-generally-available",
            "https://claude.com/blog/cowork-built-in-browser",
        ]
        assert all(item.kind == "post" for item in items)

    def test_titles_are_the_longest_text_not_the_category(self):
        items = parse.parse_feed(self.SOURCE, _body("sample_listing.html"))
        assert items[0].title == "A guide to the anatomy of effective commerce agents"
        assert items[1].title == "Claude for Teachers, now available for schools and districts"

    def test_dates_inside_after_and_before_the_anchor(self):
        items = parse.parse_feed(self.SOURCE, _body("sample_listing.html"))
        assert items[0].published == "2026-09-02T00:00:00+00:00"
        assert items[1].published == "2026-08-28T00:00:00+00:00"
        assert items[2].published == "2026-08-26T00:00:00+00:00"
        assert items[3].published == "2026-08-26T00:00:00+00:00"
        assert items[4].published == "2026-08-25T00:00:00+00:00"

    def test_a_read_more_link_takes_its_title_from_the_cta_or_the_heading_before_it(self):
        items = parse.parse_feed(self.SOURCE, _body("sample_listing.html"))
        assert items[3].title == "Claude in Chrome is generally available"
        assert items[4].title == "Claude gets its own browser in Cowork"

    def test_a_heading_beats_a_longer_description_inside_the_anchor(self):
        body = (
            b'<a href="/blog/x"><span>News</span><time>Aug 27, 2026</time><h4>Previewing the standard</h4>'
            b"<p>We are opening a research preview of a much longer description than the heading.</p></a>"
        )
        items = parse.parse_feed(self.SOURCE, body)
        assert [item.title for item in items] == ["Previewing the standard"]
        assert items[0].published == "2026-08-27T00:00:00+00:00"

    def test_index_pagination_and_other_sections_are_skipped(self):
        items = parse.parse_feed(self.SOURCE, _body("sample_listing.html"))
        assert not any("page" in item.url or item.url.endswith("/blog") or "/docs/" in item.url for item in items)

    def test_a_page_without_cards_is_empty(self):
        assert parse.parse_feed(self.SOURCE, b"<html><body><a href='/about'>About</a></body></html>") == ()

    def test_a_source_without_a_prefix_is_empty(self):
        assert parse.parse_feed(_source("html_listing"), _body("sample_listing.html")) == ()


class TestDates:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Thu, 03 Sep 2026 14:05:00 GMT", "2026-09-03T14:05:00+00:00"),
            ("2026-09-04T07:15:00Z", "2026-09-04T07:15:00+00:00"),
            ("2026-09-03T18:00:00+01:00", "2026-09-03T18:00:00+01:00"),
            ("2026-09-04T06:00:00.000Z", "2026-09-04T06:00:00+00:00"),
            ("September 2, 2026", "2026-09-02T00:00:00+00:00"),
            ("Sep 1, 2026", "2026-09-01T00:00:00+00:00"),
            ("Sept. 1, 2026", "2026-09-01T00:00:00+00:00"),
            ("", ""),
            ("yesterday-ish", ""),
            ("Foo 40, 2026", ""),
        ],
    )
    def test_shapes(self, raw, expected):
        assert parse.parse_date(raw) == expected


class TestNormaliseUrl:
    def test_tracking_query_and_fragment_go(self):
        url = "HTTPS://Outlet.Example/Posts/a/?utm_source=x&keep=1&fbclid=2#top"
        assert parse.normalise_url(url) == "https://outlet.example/Posts/a?keep=1"

    def test_trailing_slash_and_case_fold_only_the_host(self):
        assert parse.normalise_url("https://a.example/B/") == "https://a.example/B"
        assert parse.normalise_url("https://a.example/") == "https://a.example/"

    def test_item_id_is_stable_across_variants(self):
        assert parse.item_id("https://a.example/x?utm_medium=rss") == parse.item_id("https://A.example/x/")


class TestText:
    def test_strip_html(self):
        assert parse.strip_html("<p>Hi&nbsp;<b>there</b> &amp; you</p>\n\n<p>Next</p>") == "Hi there & you Next"
        assert parse.strip_html("") == ""

    def test_clip_breaks_on_a_word(self):
        text = "word " * 100
        clipped = parse.clip(text, 50)
        assert len(clipped) <= 50
        assert clipped.endswith("…")
        assert not clipped[:-1].endswith(" ")
        assert parse.clip("short", 50) == "short"


class TestGuards:
    def test_a_doctype_is_refused(self):
        assert parse.parse_feed(_source("rss"), _body("sample_doctype.xml")) == ()
        assert parse.parse_feed(_source("rss"), b"<!DOCTYPE rss><rss/>") == ()
        assert parse.parse_feed(_source("rss"), b"<!-- hi -->\n<!DOCTYPE rss><rss/>") == ()

    def test_a_doctype_inside_a_description_is_only_text(self):
        body = (
            b'<rss version="2.0"><channel><item><title>Post</title><link>https://o.example/p</link>'
            b"<description><![CDATA[<!DOCTYPE html><p>Hi</p>]]></description></item></channel></rss>"
        )
        items = parse.parse_feed(_source("rss"), body)
        assert [item.summary for item in items] == ["Hi"]

    def test_an_oversize_body_is_refused(self):
        assert parse.parse_feed(_source("rss"), b"<rss>" + b" " * MAX_BODY_BYTES + b"</rss>") == ()
        assert parse.parse_feed(_source("json_feed"), b"{" + b" " * MAX_BODY_BYTES + b"}") == ()

    def test_broken_bodies_are_empty(self):
        assert parse.parse_feed(_source("rss"), b"<rss><channel><item>") == ()
        assert parse.parse_feed(_source("hn"), b"not json") == ()
        assert parse.parse_feed(_source("json_feed"), b"[1, 2]") == ()
        assert parse.parse_feed(_source("rss"), b"") == ()

    def test_an_unknown_kind_is_empty(self):
        assert parse.parse_feed(_source("carrier-pigeon"), _body("sample_rss.xml")) == ()
