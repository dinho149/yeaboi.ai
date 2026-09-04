"""The paper builder (src/yeaboi/news/paper.py) over a fake fetcher."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from yeaboi.news import paper as engine
from yeaboi.news.fetch import Conditional, FetchResult
from yeaboi.news.parse import NewsItem, item_id
from yeaboi.news.sources import NewsSource

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _item(title: str, *, url: str = "", column: str = "ai", days_ago: float = 0.5, **kw) -> NewsItem:
    url = url or f"https://example.test/{title.lower().replace(' ', '-')}"
    base = dict(id=item_id(url), title=title, url=url, source_id="src", source_name="Src", column=column)
    base["published"] = _iso(days_ago)
    base.update(kw)
    return NewsItem(**base)


def _rss(*rows: tuple[str, str, str]) -> bytes:
    items = "".join(
        f"<item><title>{title}</title><link>{url}</link><pubDate>{date}</pubDate></item>" for title, url, date in rows
    )
    return f'<rss version="2.0"><channel>{items}</channel></rss>'.encode()


def _rfc(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%a, %d %b %Y %H:%M:%S GMT")


SRC_A = NewsSource(id="a", name="Outlet A", url="https://a.example/feed", column="ai", home_url="https://a.example/")
SRC_B = NewsSource(id="b", name="Outlet B", url="https://b.example/feed", column="engineering", max_items=2)
SRC_Y = NewsSource(id="y", name="yeaboi.ai", url="https://yeaboi.ai/news/feed.json", kind="json_feed", column="yeaboi")


def _fetcher(**bodies: bytes | FetchResult):
    def fetch(sources, conditionals):
        out = {}
        for source in sources:
            given = bodies.get(source.id)
            if isinstance(given, FetchResult):
                out[source.id] = given
            elif given is not None:
                out[source.id] = FetchResult(source_id=source.id, status=200, body=given, fetched_at="t")
        return out

    return fetch


class TestBuildPaper:
    def test_sections_come_in_column_order_newest_first(self):
        fetch = _fetcher(
            a=_rss(("Old story", "https://a.example/old", _rfc(3)), ("New story", "https://a.example/new", _rfc(1))),
            b=_rss(("Eng story", "https://b.example/eng", _rfc(2))),
        )
        paper, _, _ = engine.build_paper(sources=(SRC_B, SRC_A), now=NOW, fetcher=fetch)
        # The lead took the newest AI story, so the AI section keeps the old one.
        assert [section.column for section in paper.sections] == ["ai", "engineering"]
        assert paper.lead is not None and paper.lead.title == "New story"
        assert [item.title for item in paper.sections[0].items] == ["Old story"]
        assert paper.sections[0].title == "AI"

    def test_per_source_cap(self):
        rows = tuple((f"Story {n}", f"https://b.example/{n}", _rfc(n)) for n in range(5))
        paper, _, items = engine.build_paper(sources=(SRC_B,), now=NOW, fetcher=_fetcher(b=_rss(*rows)))
        assert len(items["b"]) == 2
        assert [item.title for item in items["b"]] == ["Story 0", "Story 1"]

    def test_old_stories_are_dropped_except_in_the_yeaboi_column(self):
        yeaboi = (
            b'{"items": [{"url": "https://yeaboi.ai/old", "title": "An old post", '
            b'"date_published": "2026-01-01T00:00:00Z"}]}'
        )
        fetch = _fetcher(a=_rss(("Ancient", "https://a.example/ancient", _rfc(30))), y=yeaboi)
        paper, _, _ = engine.build_paper(sources=(SRC_A, SRC_Y), now=NOW, fetcher=fetch)
        assert [section.column for section in paper.sections] == ["yeaboi"]
        assert paper.sections[0].items[0].title == "An old post"

    def test_undated_items_survive_the_age_filter(self):
        undated = _item("No date", published="")
        assert engine.drop_older_than((undated,), NOW) == (undated,)

    def test_statuses_report_every_source(self):
        fetch = _fetcher(a=_rss(("S", "https://a.example/s", _rfc(1))))
        paper, _, _ = engine.build_paper(sources=(SRC_A, SRC_B), now=NOW, fetcher=fetch)
        by_id = {status.id: status for status in paper.sources}
        assert by_id["a"].ok and by_id["a"].item_count == 1 and by_id["a"].home_url == "https://a.example/"
        assert not by_id["b"].ok and by_id["b"].error == "not fetched" and by_id["b"].item_count == 0

    def test_generated_at_is_the_clock(self):
        paper, _, _ = engine.build_paper(sources=(), now=NOW, fetcher=_fetcher())
        assert paper.generated_at == "2026-09-04T12:00:00+00:00"
        assert paper.stale is False


class TestFallbacks:
    def test_a_304_keeps_the_previous_items_and_is_ok(self):
        previous = {"a": (_item("Kept", url="https://a.example/kept", source_id="a"),)}
        fetch = _fetcher(a=FetchResult(source_id="a", status=304, conditional=Conditional(etag='"e"'), fetched_at="t"))
        paper, conditionals, items = engine.build_paper(
            sources=(SRC_A,), now=NOW, fetcher=fetch, previous_items=previous, conditionals={"a": Conditional('"e"')}
        )
        assert items["a"][0].title == "Kept"
        assert paper.sources[0].ok
        assert conditionals["a"].etag == '"e"'

    def test_a_failure_keeps_the_previous_items_but_says_so(self):
        previous = {"a": (_item("Kept", url="https://a.example/kept", source_id="a"),)}
        fetch = _fetcher(a=FetchResult(source_id="a", status=0, error="timeout", fetched_at="t"))
        paper, _, items = engine.build_paper(sources=(SRC_A,), now=NOW, fetcher=fetch, previous_items=previous)
        assert items["a"][0].title == "Kept"
        assert not paper.sources[0].ok
        assert paper.sources[0].error == "timeout"

    def test_an_unreadable_feed_is_not_ok_and_the_paper_still_builds(self):
        fetch = _fetcher(a=b"<rss><channel><item>", b=_rss(("Fine", "https://b.example/fine", _rfc(1))))
        paper, _, _ = engine.build_paper(sources=(SRC_A, SRC_B), now=NOW, fetcher=fetch)
        by_id = {status.id: status for status in paper.sources}
        assert not by_id["a"].ok and by_id["a"].error == "no items"
        assert by_id["b"].ok
        assert paper.lead is None
        assert [section.column for section in paper.sections] == ["engineering"]

    def test_an_empty_world_is_the_local_column_only(self):
        local = (_item("yeaboi 4.1.0: A front page", column="yeaboi", kind="release", days_ago=2),)
        paper, _, _ = engine.build_paper(sources=(SRC_A,), now=NOW, fetcher=_fetcher(), local_items=local)
        assert [section.column for section in paper.sections] == ["yeaboi"]
        assert paper.lead is None

    def test_conditionals_come_back_from_200s(self):
        result = FetchResult(
            source_id="a",
            status=200,
            body=_rss(("S", "https://a.example/s", _rfc(1))),
            conditional=Conditional(last_modified="Thu"),
            fetched_at="t",
        )
        _, conditionals, _ = engine.build_paper(sources=(SRC_A,), now=NOW, fetcher=_fetcher(a=result))
        assert conditionals == {"a": Conditional(last_modified="Thu")}


class TestDedupe:
    def test_same_url_with_tracking_query_is_one_story(self):
        a = _item("Story", url="https://x.example/story?utm_source=rss")
        b = _item("Story", url="https://x.example/story/")
        assert len(engine.dedupe((a, b))) == 1

    def test_near_identical_titles_collapse_and_the_original_wins(self):
        original = _item("OpenAI releases a new model", url="https://openai.example/post", source_id="openai")
        copy = _item("OpenAI releases a new model.", url="https://techmeme.example/x", source_id="techmeme")
        kept = engine.dedupe((copy, original))
        assert [item.source_id for item in kept] == ["openai"]

    def test_different_stories_stay(self):
        a = _item("A story about cats")
        b = _item("A story about ducks")
        assert len(engine.dedupe((a, b))) == 2


class TestLead:
    def test_a_fresh_yeaboi_post_leads_and_leaves_its_section(self):
        post = _item("Hello", column="yeaboi", kind="post", days_ago=3)
        release = _item("yeaboi 4.1.0", column="yeaboi", kind="release", days_ago=1)
        ai = _item("AI thing", column="ai", days_ago=0.1)
        sections = engine.group((post, release, ai))
        lead = engine.pick_lead(sections, NOW)
        assert lead is post
        left = engine._without(sections, lead)
        assert [item.title for item in left[0].items] == ["yeaboi 4.1.0"]

    def test_a_stale_yeaboi_post_yields_to_the_newest_ai_story(self):
        post = _item("Hello", column="yeaboi", kind="post", days_ago=8)
        older = _item("Older AI", column="ai", days_ago=2)
        newer = _item("Newer AI", column="ai", days_ago=1)
        assert engine.pick_lead(engine.group((post, older, newer)), NOW) is newer

    def test_a_release_never_leads(self):
        release = _item("yeaboi 4.1.0", column="yeaboi", kind="release", days_ago=0.1)
        eng = _item("Eng", column="engineering")
        assert engine.pick_lead(engine.group((release, eng)), NOW) is None

    def test_nothing_leads_an_empty_paper(self):
        assert engine.pick_lead((), NOW) is None


class TestGroup:
    def test_empty_columns_are_omitted_and_rows_are_capped(self):
        rows = tuple(_item(f"R{n}", column="engineering", days_ago=n) for n in range(engine.MAX_PER_COLUMN + 3))
        sections = engine.group(rows)
        assert [section.column for section in sections] == ["engineering"]
        assert len(sections[0].items) == engine.MAX_PER_COLUMN
        assert sections[0].items[0].title == "R0"

    def test_local_only_paper(self):
        paper = engine.local_only_paper((_item("yeaboi 4.1.0", column="yeaboi", kind="release"),), NOW, stale=True)
        assert paper.stale is True
        assert paper.lead is None
        assert [section.column for section in paper.sections] == ["yeaboi"]

    def test_when_handles_missing_dates(self):
        assert engine.when(_item("x", published="")) == datetime.fromtimestamp(0, tz=timezone.utc)
        assert engine.when(_item("x", published="2026-09-04T12:00:00")) == NOW
