"""The curated outlet registry (src/yeaboi/news/sources.py)."""

from __future__ import annotations

import pytest

from yeaboi.news import sources


class TestRegistry:
    def test_ids_are_unique(self):
        ids = [source.id for source in sources.SOURCES]
        assert len(ids) == len(set(ids))

    def test_every_source_is_https_with_a_home(self):
        for source in sources.SOURCES:
            assert source.url.startswith("https://"), source.id
            assert source.home_url.startswith("https://"), source.id
            assert source.name.strip(), source.id

    def test_every_source_has_a_known_kind_and_column(self):
        for source in sources.SOURCES:
            assert source.kind in sources.KINDS, source.id
            assert source.column in sources.COLUMNS, source.id

    def test_listings_carry_their_link_prefix(self):
        for source in sources.SOURCES:
            if source.kind == "html_listing":
                assert source.link_prefix.startswith("/") and source.link_prefix.endswith("/"), source.id
            else:
                assert source.link_prefix == "", source.id

    def test_every_column_has_an_outlet(self):
        by_column: dict[str, int] = {}
        for source in sources.SOURCES:
            by_column[source.column] = by_column.get(source.column, 0) + 1
        assert by_column["ai"] >= 5
        assert by_column["engineering"] >= 3
        assert by_column["yeaboi"] >= 1
        assert set(by_column) == set(sources.COLUMNS)

    def test_source_by_id(self):
        assert sources.source_by_id("openai") is not None
        assert sources.source_by_id("nope") is None


class TestYoutube:
    def test_a_real_channel_id_becomes_a_source(self):
        source = sources.youtube_source("UCXuqSBlHAE6Xw-yeJA0Tunw")
        assert source is not None
        assert source.kind == "youtube"
        assert source.column == "yeaboi"
        assert source.url == "https://www.youtube.com/feeds/videos.xml?channel_id=UCXuqSBlHAE6Xw-yeJA0Tunw"
        assert source.home_url.endswith("/channel/UCXuqSBlHAE6Xw-yeJA0Tunw")

    @pytest.mark.parametrize(
        "raw",
        ["", "   ", "@handle", "UC123", "https://www.youtube.com/channel/UCXuqSBlHAE6Xw-yeJA0Tunw", "UC" + "x" * 23],
    )
    def test_anything_else_is_no_source(self, raw):
        assert sources.youtube_source(raw) is None

    def test_active_sources_appends_the_channel_only_when_it_validates(self):
        assert sources.active_sources() == sources.SOURCES
        assert sources.active_sources(youtube_channel="nope") == sources.SOURCES
        with_channel = sources.active_sources(youtube_channel=" UCXuqSBlHAE6Xw-yeJA0Tunw ")
        assert len(with_channel) == len(sources.SOURCES) + 1
        assert with_channel[-1].id == "yeaboi-youtube"
