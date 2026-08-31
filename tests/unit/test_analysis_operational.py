"""Tests for the Operations analysis component — a rate, not an anecdote.

Analysis is the only mode whose window is wide enough to normalise, so the two
things asserted hardest here are that the rate is computed from the window that
was actually read, and that nothing in the result can name a person.
"""

from datetime import datetime, timezone

import pytest

from yeaboi.analysis import operational
from yeaboi.connectors.fetching import Gathered, SourceResult
from yeaboi.ops.signals import OpsSignal

NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)


def _signal(**fields) -> OpsSignal:
    base = {
        "kind": "incident",
        "family": "incidents",
        "source": "pagerduty",
        "count": 12,
        "resolved": 10,
        "severity": "high",
        "services": ("checkout",),
        "window_start": "2026-05-03T00:00:00+00:00",
        "window_end": "2026-08-31T00:00:00+00:00",
        "samples": ("Checkout latency",),
    }
    base.update(fields)
    return OpsSignal(**base)


def _wire(monkeypatch, gathered: Gathered) -> None:
    monkeypatch.setattr("yeaboi.connectors.fetching.gather", lambda **kw: gathered)


@pytest.fixture
def gathered():
    return Gathered(
        window_start="2026-05-03T00:00:00+00:00",
        window_end="2026-08-31T00:00:00+00:00",
        sources=(SourceResult(key="pagerduty", label="PagerDuty", ok=True, count=12),),
        signals=(_signal(),),
    )


class TestTheRate:
    def test_it_normalises_to_thirty_days(self, monkeypatch, gathered):
        _wire(monkeypatch, gathered)
        _, blob = operational.run_operational(120, now=NOW)
        assert blob["per_30_days"] == {"incident": 3.0}
        assert blob["signals"][0]["per_30_days"] == 3.0

    def test_the_window_it_measured_travels_with_it(self, monkeypatch, gathered):
        _wire(monkeypatch, gathered)
        _, blob = operational.run_operational(120, now=NOW)
        assert blob["window"] == {
            "start": "2026-05-03T00:00:00+00:00",
            "end": "2026-08-31T00:00:00+00:00",
            "days": 120,
        }
        assert blob["rate_days"] == operational.RATE_DAYS

    def test_the_window_reaching_the_gather_ends_now_not_at_a_lookback_spec(self, monkeypatch):
        seen = {}
        monkeypatch.setattr("yeaboi.connectors.fetching.gather", lambda **kw: seen.update(kw) or Gathered())
        operational.run_operational(120, now=NOW)
        start, end = seen["window"]
        assert end == NOW and (end - start).days == 120


class TestItNamesNobody:
    def test_no_row_can_hold_a_person(self, monkeypatch, gathered):
        _wire(monkeypatch, gathered)
        _, blob = operational.run_operational(120, now=NOW)
        keys = {k for row in blob["signals"] for k in row}
        assert not keys & {"author", "assignee", "members", "owner", "on_call"}

    def test_it_recommends_nothing(self, monkeypatch, gathered):
        # An action drawn from a count with no baseline is a guess wearing a
        # recommendation's clothes.
        _wire(monkeypatch, gathered)
        _, blob = operational.run_operational(120, now=NOW)
        assert blob["action_plan"] == []


class TestSubSourceNarrowing:
    def test_an_unchosen_vendor_is_dropped(self, monkeypatch):
        _wire(
            monkeypatch,
            Gathered(
                sources=(
                    SourceResult(key="pagerduty", label="PagerDuty", ok=True, count=12),
                    SourceResult(key="sentry", label="Sentry", ok=True, count=4),
                ),
                signals=(_signal(), _signal(source="sentry", kind="error_spike", count=4)),
            ),
        )
        _, blob = operational.run_operational(120, sub_sources=["pagerduty"], now=NOW)
        assert {row["source"] for row in blob["signals"]} == {"pagerduty"}
        assert [s["key"] for s in blob["sources"]] == ["pagerduty"]


class TestFailure:
    def test_a_gather_that_raises_is_a_missing_component_not_a_crash(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("network is down")

        monkeypatch.setattr("yeaboi.connectors.fetching.gather", boom)
        assert operational.run_operational(120, now=NOW) == (None, None)

    def test_a_failed_vendor_lands_in_the_coverage_report(self, monkeypatch):
        _wire(
            monkeypatch,
            Gathered(
                sources=(SourceResult(key="datadog", label="Datadog", error="rate limited"),),
            ),
        )
        _, blob = operational.run_operational(120, now=NOW)
        coverage = blob["coverage_report"]
        assert coverage["status"] == "failed" and coverage["failed"] == 1
        assert coverage["grouped_errors"] == [{"detail": "Datadog: rate limited"}]

    def test_one_vendor_down_keeps_the_other(self, monkeypatch):
        _wire(
            monkeypatch,
            Gathered(
                sources=(
                    SourceResult(key="datadog", label="Datadog", error="rate limited"),
                    SourceResult(key="pagerduty", label="PagerDuty", ok=True, count=12),
                ),
                signals=(_signal(),),
            ),
        )
        _, blob = operational.run_operational(120, now=NOW)
        assert blob["coverage_report"]["status"] == "partial"
        assert blob["totals"] == {"incident": 12}


class TestTheFeatureIsOptIn:
    def test_no_legacy_boolean_turns_it_on(self):
        from yeaboi.analysis.engine import _resolve_components

        comps = _resolve_components("jira", None, include_ai_usage=True, include_doc_quality=True)
        assert comps["ops"] == []

    def test_it_is_unselectable_with_nothing_connected(self, monkeypatch):
        from yeaboi.analysis import setup

        monkeypatch.setattr("yeaboi.connectors.registry.is_connected", lambda c: False)
        assert setup.available_ops_sources() == []

    def test_selecting_it_with_no_connector_is_refused(self):
        from yeaboi.analysis.engine import _resolve_analysis_features

        with pytest.raises(ValueError, match="Nothing to analyse"):
            _resolve_analysis_features(["operational"], {"delivery": [], "code": [], "docs": [], "ops": []})

    def test_the_window_step_applies_to_it(self):
        from yeaboi.analysis import setup

        assert setup.step_applies("window", features=["operational"])

    def test_the_members_step_does_not(self):
        from yeaboi.analysis import setup

        # There is nobody to scope to: an ops event has no author.
        assert not setup.step_applies("members", features=["operational"])
