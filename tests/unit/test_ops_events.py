"""The shapes production data arrives in, and the one guarantee they carry.

The boundary guard is the point of this file: an ``OpsEvent`` has no field a
stack trace, a log line or a metric series could live in, and none a person's
name could. That is asserted on the dataclass itself rather than on any one
fetcher, so a new vendor inherits it.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from yeaboi.ops.events import (
    EVENT_KINDS,
    SEVERITIES,
    TITLE_MAX,
    OpsEvent,
    clean_severity,
    clean_title,
    iso,
    parse_ts,
    parse_window,
    within,
)

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


class TestTheBoundary:
    """What an OpsEvent cannot carry, whatever a fetcher tries to put there."""

    # The whole promise of the layer: no body-shaped field exists, so no fetcher
    # can leak one by mistake and no future edit can add one quietly.
    FORBIDDEN = {
        "body",
        "text",
        "message",
        "description",
        "stack",
        "stacktrace",
        "stack_trace",
        "traceback",
        "logs",
        "log",
        "series",
        "metrics",
        "points",
        "values",
        "payload",
        "raw",
        "context",
        "breadcrumbs",
    }

    def test_no_field_can_hold_a_body(self):
        names = {f.name for f in dataclasses.fields(OpsEvent)}
        assert not (names & self.FORBIDDEN), "an OpsEvent field could carry an event body"

    def test_no_field_can_hold_a_person(self):
        names = {f.name for f in dataclasses.fields(OpsEvent)}
        assert not (names & {"author", "user", "assignee", "owner", "responder", "email", "name"})

    def test_every_field_is_a_string(self):
        # A string field cannot hold a nested vendor payload; a dict one could.
        for field in dataclasses.fields(OpsEvent):
            assert field.type in ("str", str), field.name

    def test_it_is_frozen(self):
        event = OpsEvent(kind="alert", source="datadog", ref="1", title="x")
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.title = "y"  # type: ignore[misc]


class TestCleanTitle:
    def test_collapses_whitespace_and_newlines(self):
        assert clean_title("  a\n\nb   c ") == "a b c"

    def test_truncates_to_the_cap(self):
        out = clean_title("x" * 500)
        assert len(out) == TITLE_MAX
        assert out.endswith("…")

    def test_empty_stays_empty(self):
        assert clean_title("") == ""
        assert clean_title(None) == ""  # type: ignore[arg-type]


class TestCleanSeverity:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("P1", "critical"),
            ("sev2", "high"),
            ("Warning", "medium"),
            ("error", "high"),
            ("low", "low"),
            ("success", "info"),
        ],
    )
    def test_maps_vendor_words(self, raw, expected):
        assert clean_severity(raw) == expected

    def test_an_unknown_word_becomes_empty(self):
        # A severity a mode cannot order is worse than no severity: it would
        # sort arbitrarily and read as meaningful.
        assert clean_severity("spicy") == ""

    def test_every_alias_lands_in_the_vocabulary(self):
        for word in ("p1", "sev3", "debug", "major"):
            assert clean_severity(word) in SEVERITIES


class TestTimestamps:
    def test_iso_normalises_to_utc_z(self):
        assert iso(datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)) == "2026-01-02T03:04:00Z"

    def test_iso_of_none_is_empty(self):
        assert iso(None) == ""

    def test_a_naive_datetime_is_read_as_utc(self):
        assert iso(datetime(2026, 1, 2, 3, 4)) == "2026-01-02T03:04:00Z"

    def test_parse_accepts_z_and_unix_seconds(self):
        assert parse_ts("2026-01-02T03:04:00Z") == datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
        assert parse_ts("1767322800") is not None

    def test_a_colonless_offset_parses(self):
        # Routed through yeaboi.timeparse, so the forms 3.10's fromisoformat
        # rejects — a colonless offset, a seven-digit fraction — parse anyway.
        assert parse_ts("2026-01-02T03:04:00+0000") == datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
        assert parse_ts("2026-01-02T03:04:00.1234567Z") is not None

    def test_garbage_is_none_not_an_exception(self):
        # One malformed row must not lose the other ninety-nine.
        assert parse_ts("yesterday") is None
        assert parse_ts("") is None


class TestParseWindow:
    @pytest.mark.parametrize(
        ("spec", "days"),
        [("14d", 14), ("14", 14), ("2w", 14), ("1d", 1)],
    )
    def test_units(self, spec, days):
        start, end = parse_window(spec, now=NOW)
        assert end == NOW
        assert (end - start).days == days

    def test_hours(self):
        start, end = parse_window("48h", now=NOW)
        assert (end - start).total_seconds() == 48 * 3600

    @pytest.mark.parametrize("spec", ["", "forever", "-3d", "0d", "3y", "3 days"])
    def test_rejects_nonsense(self, spec):
        with pytest.raises(ValueError):
            parse_window(spec, now=NOW)


class TestWithin:
    def _event(self, started: str) -> OpsEvent:
        return OpsEvent(kind="alert", source="datadog", ref="1", title="x", started_at=started)

    def test_inside_and_outside(self):
        start, end = parse_window("7d", now=NOW)
        assert within(self._event("2026-06-14T00:00:00Z"), start, end)
        assert not within(self._event("2026-01-01T00:00:00Z"), start, end)

    def test_an_undated_event_is_kept(self):
        # The vendor still told us the row exists; dropping it undercounts.
        start, end = parse_window("7d", now=NOW)
        assert within(self._event(""), start, end)


class TestResolved:
    def test_the_closed_words(self):
        for word in ("resolved", "closed", "ok", "recovered"):
            assert OpsEvent(kind="incident", source="x", ref="1", title="t", status=word).resolved

    def test_an_open_one_is_not(self):
        assert not OpsEvent(kind="incident", source="x", ref="1", title="t", status="triggered").resolved


def test_the_kind_vocabulary_is_closed():
    assert EVENT_KINDS == ("incident", "alert", "error_spike", "deploy", "spend_change")
