"""Tests for src/yeaboi/standup/ops.py — production, held beside the standup.

The load-bearing assertions here are the two the design rests on: an
unconnected vendor cannot reach any surface, and nothing in the bundle is
attributable to a person.
"""

import dataclasses

import pytest

from yeaboi.connectors.fetching import Gathered, SourceResult
from yeaboi.ops.events import OpsEvent
from yeaboi.ops.signals import OpsSignal
from yeaboi.standup import ops


def _signal(**fields) -> OpsSignal:
    base = {
        "kind": "incident",
        "family": "incidents",
        "source": "pagerduty",
        "count": 2,
        "resolved": 1,
        "severity": "high",
        "services": ("checkout",),
        "window_start": "2026-08-17T00:00:00+00:00",
        "window_end": "2026-08-31T00:00:00+00:00",
        "samples": ("Checkout latency",),
    }
    base.update(fields)
    return OpsSignal(**base)


def _wire(monkeypatch, gathered: Gathered) -> None:
    monkeypatch.setattr("yeaboi.connectors.fetching.gather", lambda **kw: gathered)


class TestTheBundleNamesNobody:
    def test_no_field_can_hold_a_person(self):
        # The whole reason ops is a sibling of ActivityBundle rather than a
        # field on it: there is nothing here for `_rebuild_bundle` to filter on.
        names = {f.name for f in dataclasses.fields(ops.OpsBundle)}
        assert not names & {"author", "members", "grouped", "by_author", "attributed"}

    def test_there_is_no_skipped_list(self):
        # A vendor nobody connected must be unnameable downstream. `errors` is
        # a different thing: it can only hold a vendor the user did connect.
        names = {f.name for f in dataclasses.fields(ops.OpsBundle)}
        assert "skipped" not in names
        assert "errors" in names


class TestCollect:
    def test_nothing_connected_is_an_empty_bundle(self, monkeypatch):
        _wire(monkeypatch, Gathered(since="14d"))
        bundle = ops.collect()
        assert bundle.signals == () and bundle.events == () and bundle.errors == ()
        assert not bundle

    def test_signals_and_window_travel_together(self, monkeypatch):
        _wire(
            monkeypatch,
            Gathered(
                since="14d",
                window_start="2026-08-17T00:00:00+00:00",
                window_end="2026-08-31T00:00:00+00:00",
                sources=(SourceResult(key="pagerduty", label="PagerDuty", ok=True, count=2),),
                signals=(_signal(),),
            ),
        )
        bundle = ops.collect()
        assert bundle.window_days == 14
        assert bundle.window_start == "2026-08-17T00:00:00+00:00"
        assert bundle

    def test_a_connected_vendor_that_failed_is_an_error_not_a_silence(self, monkeypatch):
        _wire(
            monkeypatch,
            Gathered(
                since="14d",
                sources=(SourceResult(key="datadog", label="Datadog", error="rate limited"),),
            ),
        )
        assert ops.collect().errors == (("Datadog", "rate limited"),)

    def test_a_gather_that_raises_costs_the_ops_read_not_the_standup(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("network is down")

        monkeypatch.setattr("yeaboi.connectors.fetching.gather", boom)
        assert ops.collect() == ops.OpsBundle()

    def test_the_window_reaches_the_gather(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            "yeaboi.connectors.fetching.gather",
            lambda **kw: seen.update(kw) or Gathered(since=kw["since"]),
        )
        ops.collect(window_days=30)
        assert seen["since"] == "30d"


class TestConnected:
    def test_false_when_no_ops_vendor_has_credentials(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.registry.is_connected", lambda c: False)
        assert ops.connected() is False

    def test_true_when_one_is_connected_and_can_be_gathered_from(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.registry.is_connected", lambda c: bool(c.fetch))
        assert ops.connected() is True


class TestSignalLine:
    def test_resolved_is_stated_only_when_some_are(self):
        assert "resolved" not in ops.signal_line(_signal(resolved=0))
        assert "1 resolved" in ops.signal_line(_signal(resolved=1))

    def test_one_event_is_singular(self):
        assert ops.signal_line(_signal(count=1, resolved=0)).startswith("1 incident via")

    def test_the_kind_reads_as_words(self):
        assert "error spike" in ops.signal_line(_signal(kind="error_spike"))

    def test_extra_services_are_counted_not_listed(self):
        line = ops.signal_line(_signal(services=("aa", "bb", "cc", "dd", "ee")))
        assert line.endswith("services: aa, bb, cc +2")


class TestForPrompt:
    def test_the_model_gets_counts_words_and_examples(self):
        rows = ops.for_prompt(ops.OpsBundle(signals=(_signal(),)))
        assert rows == [
            {
                "kind": "incident",
                "source": "pagerduty",
                "count": 2,
                "resolved": 1,
                "worst_severity": "high",
                "services": ["checkout"],
                "examples": ["Checkout latency"],
            }
        ]

    def test_an_empty_bundle_gives_the_prompt_nothing(self):
        assert ops.for_prompt(ops.OpsBundle()) == []

    @pytest.mark.parametrize("banned", ["url", "ref", "started_at", "author"])
    def test_the_prompt_carries_no_link_handle_or_person(self, banned):
        # Urls are for rendering, refs are for provenance, and neither is
        # something the model should be reasoning over or repeating.
        assert banned not in ops.for_prompt(ops.OpsBundle(signals=(_signal(),)))[0]


class TestWindowLabel:
    def test_it_says_how_far_back_it_looked(self):
        assert ops.window_label(ops.OpsBundle(window_days=14)) == "the last 14 days"

    def test_an_unset_window_falls_back_to_the_default(self):
        assert ops.window_label(ops.OpsBundle()) == f"the last {ops.WINDOW_DAYS} days"


class TestTheEventsSurviveForConflicts:
    def test_raw_events_are_carried_beside_the_roll_up(self, monkeypatch):
        # Roll-up destroys the per-event status a conflict is detected from, so
        # the bundle keeps both altitudes.
        event = OpsEvent(kind="incident", source="pagerduty", ref="PD-1", title="YEA-1 down", status="triggered")
        _wire(
            monkeypatch,
            Gathered(
                since="14d",
                sources=(SourceResult(key="pagerduty", label="PagerDuty", ok=True, count=1),),
                events=(event,),
                signals=(_signal(count=1),),
            ),
        )
        assert ops.collect().events == (event,)
