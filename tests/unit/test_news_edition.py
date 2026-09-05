"""The edition rules (src/yeaboi/news/edition.py): story order and the words around them."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from yeaboi.news import edition
from yeaboi.news.paper import Paper, Section, SourceStatus
from yeaboi.news.parse import NewsItem

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _iso(**ago) -> str:
    return (NOW - timedelta(**ago)).isoformat(timespec="seconds")


def _item(title: str, *, column: str = "ai", source_name: str = "Src", **kw) -> NewsItem:
    base = dict(
        id=title.lower().replace(" ", "-"),
        title=title,
        url=f"https://example.test/{title.lower().replace(' ', '-')}",
        source_id="src",
        source_name=source_name,
        column=column,
        published=_iso(hours=2),
    )
    base.update(kw)
    return NewsItem(**base)


def _paper(*sections: tuple[str, list[NewsItem]], lead: NewsItem | None = None, **kw) -> Paper:
    return Paper(
        generated_at=kw.pop("generated_at", _iso(minutes=8)),
        lead=lead,
        sections=tuple(Section(column=column, title=column, items=tuple(items)) for column, items in sections),
        **kw,
    )


def _status(id: str, ok: bool = True) -> SourceStatus:
    return SourceStatus(id=id, name=id, ok=ok)


class TestStories:
    def test_lead_comes_first_then_the_desks_in_turn(self):
        lead = _item("Lead", column="yeaboi")
        paper = _paper(
            ("yeaboi", [_item("Y1", column="yeaboi")]),
            ("ai", [_item("A1"), _item("A2")]),
            ("engineering", [_item("E1", column="engineering")]),
            lead=lead,
        )
        assert [item.title for item in edition.stories(paper)] == ["Lead", "Y1", "A1", "E1", "A2"]

    def test_adjacent_stories_never_share_a_desk_while_every_desk_has_rows(self):
        paper = _paper(
            ("yeaboi", [_item(f"Y{i}", column="yeaboi") for i in range(3)]),
            ("ai", [_item(f"A{i}") for i in range(3)]),
            ("engineering", [_item(f"E{i}", column="engineering") for i in range(3)]),
        )
        columns = [item.column for item in edition.stories(paper)]
        assert all(a != b for a, b in zip(columns, columns[1:], strict=False))

    def test_the_lead_is_not_repeated(self):
        lead = _item("Lead")
        paper = _paper(("ai", [lead, _item("A2")]), lead=lead)
        assert [item.title for item in edition.stories(paper)] == ["Lead", "A2"]

    def test_capped_at_the_edition_size(self):
        paper = _paper(("ai", [_item(f"A{i}") for i in range(20)]))
        assert len(edition.stories(paper)) == edition.EDITION_SIZE
        assert len(edition.stories(paper, size=3)) == 3

    def test_an_unknown_desk_is_left_out(self):
        paper = _paper(("sports", [_item("S1", column="sports")]), ("ai", [_item("A1")]), lead=_item("L", column="x"))
        assert [item.title for item in edition.stories(paper)] == ["A1"]

    def test_empty_paper(self):
        assert edition.stories(Paper()) == ()
        assert edition.is_empty(Paper())
        assert edition.is_empty(_paper(("sports", [_item("S", column="sports")])))
        assert not edition.is_empty(_paper(("ai", [_item("A")])))
        assert not edition.is_empty(_paper(lead=_item("L")))


class TestCopy:
    @pytest.mark.parametrize(
        ("column", "expected"),
        [
            ("yeaboi", "From yeaboi"),
            ("ai", "From the AI desk"),
            ("engineering", "From the engineering desk"),
            ("sports", ""),
        ],
    )
    def test_kicker(self, column, expected):
        assert edition.kicker(column) == expected

    def test_source_tag_falls_back_to_yeaboi(self):
        assert edition.source_tag(_item("A", source_name="Techmeme")) == "Techmeme"
        assert edition.source_tag(_item("A", source_name="")) == "yeaboi"

    def test_read_label(self):
        assert edition.read_label(_item("V", kind="video")) == "Watch on YouTube"
        assert edition.read_label(_item("R", kind="release")) == "Read the release notes"
        assert edition.read_label(_item("S", source_id="yeaboi-site")) == "Read more on yeaboi.ai"
        assert edition.read_label(_item("T", source_name="Techmeme")) == "Read more at Techmeme"

    def test_counter(self):
        assert edition.counter(1, 8) == "2 of 8"
        assert edition.counter(0, 1) == ""
        assert edition.counter(0, 0) == ""

    def test_inside_label(self):
        assert edition.inside_label(2) == ""
        assert edition.inside_label(3) == "Inside this edition, 2 more stories"
        assert edition.inside_label(12) == "Inside this edition, 11 more stories"


class TestRelativeTime:
    def test_empty_and_unparseable(self):
        assert edition.relative_time("", NOW) == ""
        assert edition.relative_time("not a date", NOW) == "not a date"

    @pytest.mark.parametrize(
        ("ago", "expected"),
        [
            (dict(seconds=30), "just now"),
            (dict(minutes=1), "1 minute ago"),
            (dict(minutes=5), "5 minutes ago"),
            (dict(hours=1), "1 hour ago"),
            (dict(hours=3), "3 hours ago"),
        ],
    )
    def test_today(self, ago, expected):
        assert edition.relative_time(_iso(**ago), NOW) == expected

    def test_yesterday_is_a_calendar_day_not_24_hours(self):
        late = datetime(2026, 9, 4, 0, 30, tzinfo=timezone.utc)
        assert edition.relative_time("2026-09-03T23:30:00+00:00", late) == "yesterday"

    def test_this_week_names_the_weekday(self):
        assert edition.relative_time(_iso(days=3), NOW) == "Tuesday"

    def test_older_is_a_short_date(self):
        assert edition.relative_time(_iso(days=7), NOW) == "28 Aug"
        assert edition.relative_time("2025-09-04T12:00:00+00:00", NOW) == "4 Sep 2025"

    def test_a_naive_stamp_is_read_as_utc(self):
        assert edition.relative_time("2026-09-04T10:00:00", NOW) == "2 hours ago"

    def test_byline(self):
        assert edition.byline(_item("A", source_name="Techmeme"), NOW) == "Techmeme, 2 hours ago"
        assert edition.byline(_item("A", source_name="Techmeme", published=""), NOW) == "Techmeme"
        assert edition.byline(_item("A", source_name="", published=""), NOW) == "yeaboi"


class TestEditionLine:
    def test_off(self):
        assert edition.edition_line(_paper(), NOW, enabled=False) == "News is off, showing yeaboi alone."

    def test_stale(self):
        assert edition.edition_line(_paper(stale=True), NOW, enabled=True) == "Refreshing."

    def test_fresh(self):
        assert edition.edition_line(_paper(), NOW, enabled=True) == "Refreshed 8 minutes ago."

    def test_fresh_without_a_readable_stamp(self):
        assert edition.edition_line(_paper(generated_at=""), NOW, enabled=True) == "Refreshed."


class TestSourcesLine:
    def test_none(self):
        assert edition.sources_line(_paper()) == ""

    def test_one(self):
        assert edition.sources_line(_paper(sources=(_status("a"),))) == "Read from 1 outlet."

    def test_all_answering(self):
        paper = _paper(sources=tuple(_status(str(i)) for i in range(14)))
        assert edition.sources_line(paper) == "Read from 14 outlets."

    def test_some_not_answering_spelled_out(self):
        paper = _paper(sources=tuple(_status(str(i)) for i in range(12)) + (_status("x", False), _status("y", False)))
        assert edition.sources_line(paper) == "Read from 12 outlets, two not answering."

    def test_many_not_answering_as_digits(self):
        paper = _paper(sources=tuple(_status(str(i), False) for i in range(12)))
        assert edition.sources_line(paper) == "Read from 0 outlets, 12 not answering."


class TestTurnIndex:
    def test_the_clock_alone(self):
        assert edition.turn_index(0.0, 12.0, 0, 5) == 0
        assert edition.turn_index(11.9, 12.0, 0, 5) == 0
        assert edition.turn_index(12.0, 12.0, 0, 5) == 1
        assert edition.turn_index(61.0, 12.0, 0, 5) == 0

    def test_the_hand_adds_to_the_clock(self):
        assert edition.turn_index(12.0, 12.0, 2, 5) == 3
        assert edition.turn_index(0.0, 12.0, -1, 5) == 4

    def test_no_clock_when_the_period_is_zero(self):
        assert edition.turn_index(999.0, 0.0, 1, 5) == 1

    def test_nothing_to_turn(self):
        assert edition.turn_index(50.0, 12.0, 3, 0) == 0
        assert edition.turn_index(-5.0, 12.0, 0, 3) == 0

    def test_page(self):
        assert edition.page((), 0, NOW) is None
        items = (_item("A", source_name="Techmeme"), _item("B", column="engineering", kind="video"))
        first = edition.page(items, 0, NOW)
        assert first is not None
        assert (first.kicker, first.counter, first.byline, first.read) == (
            "From the AI desk",
            "1 of 2",
            "Techmeme, 2 hours ago",
            "Read more at Techmeme",
        )
        second = edition.page(items, 1, NOW)
        assert second is not None and second.item.title == "B" and second.read == "Watch on YouTube"


class TestPicture:
    def test_scene_by_kind_then_topic(self):
        assert edition.scene_for(_item("R", kind="release", topic="security")) == "dock"
        assert edition.scene_for(_item("V", kind="video")) == "studio"
        assert edition.scene_for(_item("S", topic="security")) == "vault"
        assert edition.scene_for(_item("M", topic="models")) == "observatory"
        assert edition.scene_for(_item("X", topic="sports")) == "newsstand"
        assert edition.scene_for(_item("N")) == "newsstand"

    def test_every_scene_has_the_desktop_caption(self):
        assert edition.caption_for("observatory") == "The wizard, under the stars."
        assert edition.caption_for("dock") == "The wizard, at the dock, with the crates."
        assert edition.caption_for("nowhere") == "The morning papers, at the stand."
        assert set(edition.CAPTIONS) == set(edition.SCENE_BY_TOPIC.values()) | set(edition.SCENE_BY_KIND.values())

    def test_persona_of(self):
        assert edition.persona_of(_item("A", persona="detective")) == "detective"
        assert edition.persona_of(_item("A", persona="pirate")) == "wizard"  # the AI desk's own
        assert edition.persona_of(_item("A", persona="", column="engineering")) == "engineer"
        assert edition.persona_of(_item("A", persona="", column="sports")) == "engineer"

    def test_page_carries_the_picture(self):
        first = edition.page((_item("A", topic="security", persona="detective"),), 0, NOW)
        assert first is not None
        assert (first.persona, first.scene, first.caption) == (
            "detective",
            "vault",
            "The detective, outside the vault.",
        )


class TestMasthead:
    def test_dateline(self):
        assert edition.dateline(datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc)) == "Saturday, 5 September 2026"
        assert edition.dateline(datetime(2026, 1, 1)) == "Thursday, 1 January 2026"

    def test_volume_line(self):
        assert edition.volume_line("3.41.1") == "Vol. 3, No. 41"
        assert edition.volume_line(" 4.1.0-rc1 ") == "Vol. 4, No. 1"
        assert edition.volume_line("0.0.0+dev") == "Vol. 0, No. 0"
        assert edition.volume_line("dev") == ""

    def test_masthead(self):
        now = datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc)
        assert edition.masthead(now, "3.41.1") == "yeaboi · Saturday, 5 September 2026 · Vol. 3, No. 41"
        assert edition.masthead(now, "dev") == "yeaboi · Saturday, 5 September 2026"
