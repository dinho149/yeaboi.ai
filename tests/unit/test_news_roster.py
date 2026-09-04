"""The outlet roster (src/yeaboi/news/roster.py): the store, the validator, the source list."""

from __future__ import annotations

import json
import logging

import pytest

from yeaboi.news import roster
from yeaboi.news.paper import SourceStatus
from yeaboi.news.roster import CustomSource, Roster
from yeaboi.news.sources import SOURCES

PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 443))]
FEED = "https://lobste.rs/rss"


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "news_roster.json"
    monkeypatch.setattr("yeaboi.news.roster._store_path", lambda: path)
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: PUBLIC)
    roster.invalidate()
    yield path
    roster.invalidate()


def _custom(url: str = FEED, **kw) -> CustomSource:
    base = dict(id=roster.custom_id(url), name="Lobsters", url=url, kind="rss", column="engineering")
    base.update(kw)
    return CustomSource(**base)


class TestCustomId:
    def test_is_derived_from_the_normalised_url(self):
        assert roster.custom_id(FEED) == roster.custom_id("https://LOBSTE.RS/rss/?utm_source=x")
        assert roster.custom_id(FEED) != roster.custom_id("https://lobste.rs/atom")

    def test_shape(self):
        value = roster.custom_id(FEED)
        assert value.startswith("custom-") and len(value) == len("custom-") + 8
        int(value[len("custom-") :], 16)


class TestLoad:
    def test_missing_file_is_an_empty_roster(self, store):
        assert roster.load_roster() == Roster()

    def test_bad_json_and_unknown_version_are_empty_with_a_warning(self, store, caplog):
        store.write_text("{not json", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="yeaboi.news.roster"):
            assert roster.load_roster() == Roster()
            roster.invalidate()
            store.write_text(json.dumps({"version": 2, "custom": []}), encoding="utf-8")
            assert roster.load_roster() == Roster()
        assert "unreadable" in caplog.text and "unknown shape" in caplog.text

    def test_an_entry_without_an_https_url_is_skipped(self, store):
        store.write_text(
            json.dumps(
                {
                    "version": 1,
                    "disabled": ["techmeme", 3],
                    "custom": [{"url": "http://x.example/f", "name": "x"}, {"url": FEED, "name": "Lobsters"}],
                }
            ),
            encoding="utf-8",
        )
        loaded = roster.load_roster()
        assert loaded.disabled == frozenset({"techmeme"})
        assert [c.url for c in loaded.custom] == [FEED]

    def test_the_id_is_recomputed_from_the_url_on_load(self, store):
        store.write_text(
            json.dumps({"version": 1, "disabled": [], "custom": [{"id": "custom-bogus000", "url": FEED, "name": "L"}]}),
            encoding="utf-8",
        )
        assert roster.load_roster().custom[0].id == roster.custom_id(FEED)

    def test_the_mtime_cache_is_dropped_by_a_write(self, store):
        roster.save_roster(Roster(disabled=frozenset({"a"})))
        first = roster.load_roster()
        assert roster.load_roster() is first
        roster.save_roster(Roster(disabled=frozenset({"b"})))
        assert roster.load_roster().disabled == frozenset({"b"})


class TestSave:
    def test_round_trip_is_stable_and_leaves_no_temp(self, store, tmp_path):
        original = Roster(disabled=frozenset({"techmeme"}), custom=(_custom(added_at="2026-09-04T12:00:00+00:00"),))
        roster.save_roster(original)
        first = store.read_text(encoding="utf-8")
        assert roster.load_roster() == original
        roster.save_roster(roster.load_roster())
        assert store.read_text(encoding="utf-8") == first
        assert first.endswith("\n")
        assert [p.name for p in tmp_path.iterdir()] == ["news_roster.json"]


class TestProblems:
    def _problems(self, **kw):
        base = dict(url=FEED, name="Lobsters", column="engineering", kind="rss", roster=Roster())
        base.update(kw)
        return roster.roster_problems(**base)

    def test_a_valid_outlet_has_none(self, store):
        assert self._problems() == []

    @pytest.mark.parametrize(
        ("field", "value", "fragment"),
        [
            ("url", "http://lobste.rs/rss", "https://"),
            ("url", "https://10.0.0.1/feed", "public host"),
            ("url", "https://localhost/feed", "public host"),
            ("name", "", "1–60"),
            ("name", "x" * 61, "1–60"),
            ("column", "research", "column must be one of"),
            ("kind", "hn", "kind must be one of"),
            ("url", SOURCES[1].url, "already on the front page"),
        ],
    )
    def test_each_rule(self, store, field, value, fragment):
        problems = self._problems(**{field: value})
        assert any(fragment in problem for problem in problems), problems

    def test_a_duplicate_with_tracking_params_is_refused(self, store):
        full = Roster(custom=(_custom(),))
        assert any("already been added" in p for p in self._problems(url=FEED + "?utm_medium=rss", roster=full))

    def test_the_cap(self, store):
        full = Roster(custom=tuple(_custom(url=f"https://x{i}.example/f") for i in range(roster.MAX_CUSTOM)))
        assert any("at most" in p for p in self._problems(roster=full))

    def test_every_problem_is_reported_at_once(self, store):
        problems = self._problems(url="http://x", name="", column="?", kind="?")
        assert len(problems) == 4


class TestWrites:
    def test_add_custom_writes_and_returns_the_row(self, store, caplog):
        with caplog.at_level(logging.INFO, logger="yeaboi.news.roster"):
            added = roster.add_custom(
                url=FEED, name=" Lobsters ", column="engineering", kind="rss", home_url="https://lobste.rs/"
            )
        assert added.id == roster.custom_id(FEED) and added.name == "Lobsters" and added.added_at
        assert roster.load_roster().custom == (added,)
        assert "roster added" in caplog.text
        assert "/rss" not in caplog.text  # the host is named, never the typed URL

    def test_add_custom_keeps_only_a_web_home(self, store):
        added = roster.add_custom(url=FEED, name="L", column="ai", kind="rss", home_url="javascript:alert(1)")
        assert added.home_url == ""
        assert roster.load_roster().custom[0].home_url == ""

    def test_add_custom_is_all_or_nothing(self, store, caplog):
        with caplog.at_level(logging.INFO, logger="yeaboi.news.roster"), pytest.raises(ValueError, match="https"):
            roster.add_custom(url="http://x", name="", column="ai", kind="rss")
        assert not store.exists()
        assert "roster rejected" in caplog.text

    def test_remove_custom(self, store):
        added = roster.add_custom(url=FEED, name="L", column="ai", kind="rss")
        roster.set_enabled(added.id, False)
        assert roster.remove_custom("custom-nope0000") is False
        assert roster.remove_custom(added.id) is True
        assert roster.load_roster() == Roster()

    def test_set_enabled_on_every_kind_of_id(self, store):
        added = roster.add_custom(url=FEED, name="L", column="ai", kind="rss")
        assert roster.set_enabled("techmeme", False).disabled == frozenset({"techmeme"})
        assert roster.set_enabled(added.id, False).disabled == frozenset({"techmeme", added.id})
        assert roster.set_enabled("techmeme", True).disabled == frozenset({added.id})
        with pytest.raises(KeyError):
            roster.set_enabled("yeaboi-youtube", False)
        channel = "UCXuqSBlHAE6Xw-yeJA0Tunw"
        assert "yeaboi-youtube" in roster.set_enabled("yeaboi-youtube", False, youtube_channel=channel).disabled
        with pytest.raises(KeyError):
            roster.set_enabled("yeaboi-changelog", False)

    def test_enabling_an_enabled_outlet_writes_nothing(self, store):
        roster.set_enabled("techmeme", True)
        assert not store.exists()


class TestSources:
    def test_sources_for_applies_the_roster(self, store):
        added = _custom()
        chosen = Roster(disabled=frozenset({"techmeme", added.id}), custom=(added, _custom(url="https://y.example/f")))
        ids = [source.id for source in roster.sources_for(chosen)]
        assert "techmeme" not in ids and added.id not in ids
        assert ids[-1] == roster.custom_id("https://y.example/f")
        custom = roster.sources_for(chosen)[-1]
        assert custom.builtin is False and custom.max_items == roster.CUSTOM_MAX_ITEMS
        assert len(ids) == len(SOURCES) - 1 + 1

    def test_the_channel_is_appended(self, store):
        ids = [s.id for s in roster.sources_for(Roster(), youtube_channel="UCXuqSBlHAE6Xw-yeJA0Tunw")]
        assert ids[len(SOURCES)] == "yeaboi-youtube"

    def test_roster_sources_reads_the_store(self, store):
        roster.set_enabled("techmeme", False)
        assert "techmeme" not in [s.id for s in roster.roster_sources()]


class TestRows:
    def test_order_merge_and_defaults(self, store):
        added = _custom()
        chosen = Roster(disabled=frozenset({"techmeme"}), custom=(added,))
        statuses = {"techmeme": SourceStatus(id="techmeme", ok=True, fetched_at="t", item_count=4)}
        rows = roster.source_rows(chosen, statuses)
        assert [row["id"] for row in rows] == [*(s.id for s in SOURCES), added.id]
        techmeme = next(row for row in rows if row["id"] == "techmeme")
        assert techmeme["enabled"] is False and techmeme["ok"] is True and techmeme["item_count"] == 4
        assert techmeme["builtin"] is True and techmeme["url"].startswith("https://")
        last = rows[-1]
        assert last["ok"] is None and last["fetched_at"] == "" and last["builtin"] is False
        assert set(last) == {
            "id",
            "name",
            "home_url",
            "url",
            "column",
            "kind",
            "builtin",
            "enabled",
            "ok",
            "fetched_at",
            "error",
            "item_count",
        }
        assert "yeaboi-changelog" not in {row["id"] for row in rows}
