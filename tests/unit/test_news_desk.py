"""The news desk's stale-while-revalidate and the cache under it."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import pytest

from yeaboi.news import cache as news_cache
from yeaboi.news.desk import NewsDesk
from yeaboi.news.fetch import Conditional
from yeaboi.news.paper import Paper, Section, SourceStatus
from yeaboi.news.parse import NewsItem
from yeaboi.news.roster import Roster
from yeaboi.news.sources import NewsSource

T0 = 1_800_000_000.0


def _item(title: str, **kw) -> NewsItem:
    base = dict(id=title.lower(), title=title, url=f"https://x.example/{title.lower()}", column="ai", persona="wizard")
    base.update(kw)
    return NewsItem(**base)


def _paper(*titles: str, stale: bool = False) -> Paper:
    return Paper(
        generated_at="2026-09-04T12:00:00+00:00",
        stale=stale,
        lead=_item(titles[0]) if titles else None,
        sections=(Section(column="ai", title="AI", items=tuple(_item(t) for t in titles[1:])),) if titles else (),
        sources=(SourceStatus(id="a", name="A", ok=True),),
    )


class FakeThread:
    """A thread that runs its target when told to, on the test's own stack."""

    started: list[FakeThread] = []

    def __init__(self, *, target, name, daemon):
        self.target = target
        self.name = name
        self.daemon = daemon
        self.ran = False

    def start(self):
        FakeThread.started.append(self)

    def run(self):
        self.ran = True
        self.target()


@pytest.fixture(autouse=True)
def _reset_threads(monkeypatch, tmp_path):
    FakeThread.started.clear()
    monkeypatch.delenv("YEABOI_NEWS", raising=False)
    monkeypatch.delenv("NEWS_YOUTUBE_CHANNEL", raising=False)
    monkeypatch.setattr("yeaboi.paths.LOGS_DIR", tmp_path / "logs")


def _desk(tmp_path, *, clock, build=None, local=None, sources=None, roster=None):
    built = build or (lambda **kw: (_paper("Built lead", "Built row"), {}, {}))
    return NewsDesk(
        cache_path=lambda: tmp_path / "news_cache.json",
        clock=lambda: clock[0],
        build=built,
        sources=sources or (lambda **kw: (NewsSource(id="a", name="A", url="https://a.example/f"),)),
        roster=roster or (lambda: Roster()),
        local=local or (lambda: (_item("yeaboi 4.1.0", column="yeaboi", kind="release"),)),
        spawn=FakeThread,
    )


def _write(tmp_path, paper: Paper, written_at: float):
    news_cache.write_cache(tmp_path / "news_cache.json", news_cache.CacheEntry(paper=paper, written_at=written_at))


class TestStaleWhileRevalidate:
    def test_a_fresh_cache_is_answered_without_a_refresh(self, tmp_path):
        clock = [T0]
        _write(tmp_path, _paper("Lead", "Row"), T0 - 60)
        paper, refreshing = _desk(tmp_path, clock=clock).get_paper()
        assert paper.lead is not None and paper.lead.title == "Lead"
        assert paper.stale is False
        assert refreshing is False
        assert FakeThread.started == []

    def test_an_expired_cache_is_answered_stale_and_one_refresh_starts(self, tmp_path):
        clock = [T0]
        _write(tmp_path, _paper("Lead", "Row"), T0 - 3600)
        desk = _desk(tmp_path, clock=clock)
        paper, refreshing = desk.get_paper()
        assert paper.stale is True
        assert paper.lead is not None and paper.lead.title == "Lead"
        assert refreshing is True
        assert len(FakeThread.started) == 1
        assert FakeThread.started[0].name == "news-refresh" and FakeThread.started[0].daemon

    def test_a_second_request_during_a_refresh_starts_nothing(self, tmp_path):
        clock = [T0]
        desk = _desk(tmp_path, clock=clock)
        desk.get_paper()
        _, refreshing = desk.get_paper()
        assert refreshing is True
        assert len(FakeThread.started) == 1

    def test_no_cache_answers_the_local_column_stale(self, tmp_path):
        clock = [T0]
        paper, refreshing = _desk(tmp_path, clock=clock).get_paper()
        assert paper.stale is True
        assert refreshing is True
        assert [section.column for section in paper.sections] == ["yeaboi"]

    def test_refresh_forces_a_fetch_on_a_fresh_cache(self, tmp_path):
        clock = [T0]
        _write(tmp_path, _paper("Lead", "Row"), T0 - 60)
        _, refreshing = _desk(tmp_path, clock=clock).get_paper(refresh=True)
        assert refreshing is True
        assert len(FakeThread.started) == 1

    def test_the_refresh_writes_the_cache_and_the_next_read_is_fresh(self, tmp_path, caplog):
        clock = [T0]
        desk = _desk(tmp_path, clock=clock)
        with caplog.at_level(logging.INFO, logger="yeaboi.news.desk"):
            desk.get_paper()
            FakeThread.started[0].run()
        clock[0] = T0 + 5
        paper, refreshing = desk.get_paper()
        assert paper.stale is False
        assert refreshing is False
        assert paper.lead is not None and paper.lead.title == "Built lead"
        assert "refresh started" in caplog.text and "refresh finished" in caplog.text
        assert (tmp_path / "news_cache.json").exists()

    def test_a_failed_build_keeps_the_last_paper_and_releases_the_lock(self, tmp_path, caplog):
        clock = [T0]
        _write(tmp_path, _paper("Lead", "Row"), T0 - 3600)

        def boom(**kw):
            raise RuntimeError("no network")

        desk = _desk(tmp_path, clock=clock, build=boom)
        with caplog.at_level(logging.WARNING, logger="yeaboi.news.desk"):
            desk.get_paper()
            FakeThread.started[0].run()
        assert "refresh failed" in caplog.text
        assert desk.get_paper()[1] is True  # a new refresh could start: the lock was released
        assert len(FakeThread.started) == 2


class TestRefreshNow:
    def test_refresh_now_returns_the_built_paper(self, tmp_path):
        clock = [T0]
        desk = _desk(tmp_path, clock=clock)
        paper = desk.refresh_now()
        assert paper.lead is not None and paper.lead.title == "Built lead"
        entry = news_cache.read_cache(tmp_path / "news_cache.json")
        assert entry is not None and entry.written_at == T0

    def test_the_build_gets_the_previous_conditionals_items_and_local_items(self, tmp_path):
        clock = [T0]
        seen = {}

        def build(**kw):
            seen.update(kw)
            return _paper("L"), {"a": Conditional(etag='"new"')}, {"a": (_item("Fresh"),)}

        entry = news_cache.CacheEntry(
            paper=_paper("Old"),
            conditionals={"a": Conditional(etag='"old"')},
            items_by_source={"a": (_item("Stale"),)},
            written_at=T0 - 3600,
        )
        news_cache.write_cache(tmp_path / "news_cache.json", entry)
        _desk(tmp_path, clock=clock, build=build).refresh_now()
        assert seen["conditionals"] == {"a": Conditional(etag='"old"')}
        assert seen["previous_items"]["a"][0].title == "Stale"
        assert seen["local_items"][0].kind == "release"
        assert seen["now"] == datetime.fromtimestamp(T0, tz=timezone.utc)
        after = news_cache.read_cache(tmp_path / "news_cache.json")
        assert after is not None
        assert after.conditionals["a"].etag == '"new"'
        assert after.items_by_source["a"][0].title == "Fresh"
        assert after.last_fetch_at == {"a": T0}

    def test_an_outlet_asked_too_soon_is_skipped(self, tmp_path, caplog):
        clock = [T0]
        arxiv = NewsSource(id="arxiv", name="arXiv", url="https://a.example/f", min_interval_seconds=3)
        seen = {}

        def build(**kw):
            seen.update(kw)
            return _paper("L"), {}, {}

        entry = news_cache.CacheEntry(paper=_paper("Old"), last_fetch_at={"arxiv": T0 - 1}, written_at=T0 - 3600)
        news_cache.write_cache(tmp_path / "news_cache.json", entry)
        desk = _desk(tmp_path, clock=clock, build=build, sources=lambda **kw: (arxiv,))
        with caplog.at_level(logging.INFO, logger="yeaboi.news.desk"):
            desk.refresh_now()
        assert seen["sources"] == ()
        assert "skipped" in caplog.text

    def test_the_youtube_channel_reaches_the_registry(self, tmp_path, monkeypatch):
        clock = [T0]
        monkeypatch.setenv("NEWS_YOUTUBE_CHANNEL", "UCXuqSBlHAE6Xw-yeJA0Tunw")
        asked = {}

        def sources(**kw):
            asked.update(kw)
            return ()

        _desk(tmp_path, clock=clock, sources=sources).refresh_now()
        assert asked == {"youtube_channel": "UCXuqSBlHAE6Xw-yeJA0Tunw"}


class TestRoster:
    def test_a_switched_off_outlet_is_hidden_from_a_fresh_cache_without_a_refresh(self, tmp_path):
        clock = [T0]
        paper = _paper("Lead", "Row")
        paper = paper.__class__(
            generated_at=paper.generated_at,
            lead=_item("Lead", source_id="a"),
            sections=(
                Section(column="ai", title="AI", items=(_item("Row", source_id="a"), _item("Other", source_id="b"))),
            ),
            sources=(SourceStatus(id="a", name="A", ok=True), SourceStatus(id="b", name="B", ok=True)),
        )
        _write(tmp_path, paper, T0 - 60)
        desk = _desk(tmp_path, clock=clock, roster=lambda: Roster(disabled=frozenset({"a"})))
        shown, refreshing = desk.get_paper()
        assert refreshing is False and FakeThread.started == []
        assert [status.id for status in shown.sources] == ["b"]
        assert shown.lead is not None and shown.lead.title == "Other"
        assert shown.stale is False

    def test_invalidate_makes_a_fresh_cache_stale_and_starts_one_refresh(self, tmp_path):
        clock = [T0]
        _write(tmp_path, _paper("Lead", "Row"), T0 - 60)
        desk = _desk(tmp_path, clock=clock)
        assert desk.invalidate(refresh=False) is False
        assert FakeThread.started == []
        clock[0] = T0 + 1
        paper, refreshing = desk.get_paper()
        assert paper.stale is True and refreshing is True
        assert len(FakeThread.started) == 1

    def test_invalidate_with_refresh_starts_one_at_once(self, tmp_path):
        clock = [T0]
        _write(tmp_path, _paper("Lead", "Row"), T0 - 60)
        desk = _desk(tmp_path, clock=clock)
        assert desk.invalidate() is True
        assert desk.invalidate() is False  # one is running
        assert len(FakeThread.started) == 1

    def test_invalidate_starts_nothing_when_off(self, tmp_path, monkeypatch):
        monkeypatch.setenv("YEABOI_NEWS", "off")
        assert _desk(tmp_path, clock=[T0]).invalidate() is False
        assert FakeThread.started == []

    def test_the_refresh_prunes_a_removed_outlet_and_keeps_a_switched_off_one(self, tmp_path, caplog):
        clock = [T0]
        entry = news_cache.CacheEntry(
            paper=_paper("Old"),
            conditionals={"a": Conditional(etag="a"), "gone": Conditional(etag="g"), "off": Conditional(etag="o")},
            items_by_source={"a": (_item("A"),), "gone": (_item("G"),), "off": (_item("O"),)},
            last_fetch_at={"a": T0 - 9, "gone": T0 - 9, "off": T0 - 9},
            written_at=T0 - 3600,
        )
        news_cache.write_cache(tmp_path / "news_cache.json", entry)
        desk = _desk(tmp_path, clock=clock, roster=lambda: Roster(disabled=frozenset({"off"})))
        with caplog.at_level(logging.INFO, logger="yeaboi.news.desk"):
            desk.refresh_now()
        after = news_cache.read_cache(tmp_path / "news_cache.json")
        assert after is not None
        assert set(after.items_by_source) == {"a", "off"}
        assert set(after.conditionals) == {"a", "off"}
        assert set(after.last_fetch_at) == {"a", "off"}
        assert "pruned 1 removed outlet" in caplog.text

    def test_source_rows_merge_the_cached_statuses(self, tmp_path):
        clock = [T0]
        _write(tmp_path, _paper("Lead"), T0 - 60)
        desk = _desk(tmp_path, clock=clock, roster=lambda: Roster(disabled=frozenset({"techmeme"})))
        rows = {row["id"]: row for row in desk.source_rows()}
        assert rows["techmeme"]["enabled"] is False and rows["techmeme"]["ok"] is None
        assert "yeaboi-site" in rows and rows["yeaboi-site"]["builtin"] is True

    def test_no_cache_rows_have_no_health(self, tmp_path):
        rows = _desk(tmp_path, clock=[T0]).source_rows()
        assert rows and all(row["ok"] is None for row in rows)


class TestOffSwitch:
    @pytest.mark.parametrize("value", ["0", "false", "off", "no", " OFF "])
    def test_off_answers_the_local_column_and_touches_nothing(self, tmp_path, monkeypatch, value):
        monkeypatch.setenv("YEABOI_NEWS", value)
        clock = [T0]
        _write(tmp_path, _paper("Lead", "Row"), T0 - 3600)
        desk = _desk(tmp_path, clock=clock)
        assert desk.enabled() is False
        paper, refreshing = desk.get_paper()
        assert paper.stale is False
        assert refreshing is False
        assert paper.lead is None
        assert [section.column for section in paper.sections] == ["yeaboi"]
        assert FakeThread.started == []

    @pytest.mark.parametrize("value", ["", "true", "on", "1"])
    def test_anything_else_is_on(self, tmp_path, monkeypatch, value):
        monkeypatch.setenv("YEABOI_NEWS", value)
        assert _desk(tmp_path, clock=[T0]).enabled() is True

    def test_a_broken_local_column_is_an_empty_one(self, tmp_path, monkeypatch):
        monkeypatch.setenv("YEABOI_NEWS", "off")

        def broken():
            raise OSError("no changelog")

        paper, _ = _desk(tmp_path, clock=[T0], local=broken).get_paper()
        assert paper.sections == ()


class TestCache:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "news_cache.json"
        entry = news_cache.CacheEntry(
            paper=_paper("Lead", "Row"),
            conditionals={"a": Conditional(etag='"e"', last_modified="Thu")},
            items_by_source={"a": (_item("Kept", image_url="https://x.example/i.png"),)},
            last_fetch_at={"a": T0},
            written_at=T0,
        )
        assert news_cache.write_cache(path, entry) is True
        back = news_cache.read_cache(path)
        assert back == entry

    def test_missing_bad_json_and_unknown_schema_are_none(self, tmp_path, caplog):
        path = tmp_path / "news_cache.json"
        assert news_cache.read_cache(path) is None
        path.write_text("{not json", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="yeaboi.news.cache"):
            assert news_cache.read_cache(path) is None
            path.write_text(json.dumps({"schema": 99, "paper": {}}), encoding="utf-8")
            assert news_cache.read_cache(path) is None
        assert "unreadable" in caplog.text and "unknown schema" in caplog.text

    def test_a_failed_write_leaves_the_old_file_and_no_temp(self, tmp_path, monkeypatch):
        path = tmp_path / "news_cache.json"
        news_cache.write_cache(path, news_cache.CacheEntry(paper=_paper("Old"), written_at=1.0))

        def refuse(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(os, "replace", refuse)
        assert news_cache.write_cache(path, news_cache.CacheEntry(paper=_paper("New"), written_at=2.0)) is False
        kept = news_cache.read_cache(path)
        assert kept is not None and kept.paper.lead is not None and kept.paper.lead.title == "Old"
        assert [p.name for p in tmp_path.iterdir()] == ["news_cache.json"]

    def test_is_fresh(self):
        entry = news_cache.CacheEntry(written_at=T0)
        assert news_cache.is_fresh(entry, now=T0 + 10)
        assert not news_cache.is_fresh(entry, now=T0 + news_cache.CACHE_TTL_SECONDS + 1)
        assert not news_cache.is_fresh(entry, now=T0 - 1)
        assert not news_cache.is_fresh(news_cache.CacheEntry(), now=T0)

    def test_unknown_item_fields_are_ignored_on_read(self, tmp_path):
        path = tmp_path / "news_cache.json"
        raw = news_cache.entry_to_dict(news_cache.CacheEntry(paper=_paper("Lead", "Row"), written_at=T0))
        raw["paper"]["lead"]["mystery"] = 1
        raw["paper"]["sections"][0]["items"][0]["mystery"] = 2
        path.write_text(json.dumps(raw), encoding="utf-8")
        back = news_cache.read_cache(path)
        assert back is not None and back.paper.lead is not None and back.paper.lead.title == "Lead"
