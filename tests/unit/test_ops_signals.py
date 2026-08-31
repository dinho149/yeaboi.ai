"""Roll-up: how events become the bounded counts a mode renders."""

from __future__ import annotations

import dataclasses

from yeaboi.ops.events import OpsEvent
from yeaboi.ops.signals import SAMPLE_CAP, OpsSignal, roll_up, worst_severity


def event(**kw) -> OpsEvent:
    base = {"kind": "alert", "source": "datadog", "ref": "1", "title": "high latency"}
    return OpsEvent(**{**base, **kw})


class TestRollUp:
    def test_groups_by_kind_and_source_not_by_service(self):
        # Forty monitors on forty services must not become forty rows.
        events = tuple(event(service=f"svc-{i}") for i in range(40))
        signals = roll_up(events)
        assert len(signals) == 1
        assert signals[0].count == 40

    def test_two_sources_are_two_signals(self):
        signals = roll_up((event(), event(source="grafana")))
        assert {s.source for s in signals} == {"datadog", "grafana"}

    def test_two_kinds_from_one_source_are_two_signals(self):
        signals = roll_up((event(), event(kind="incident")))
        assert {s.kind for s in signals} == {"alert", "incident"}

    def test_a_kind_with_no_event_is_omitted_never_zero(self):
        # The PerfMetric rule: a team whose Sentry was never read did not have
        # zero regressions.
        signals = roll_up((event(),))
        assert [s.kind for s in signals] == ["alert"]
        assert all(s.count > 0 for s in signals)

    def test_nothing_in_nothing_out(self):
        assert roll_up(()) == ()

    def test_counts_the_resolved_ones(self):
        signals = roll_up((event(status="resolved"), event(status="firing")))
        assert (signals[0].count, signals[0].resolved) == (2, 1)

    def test_services_are_sorted_and_deduped(self):
        signals = roll_up((event(service="web"), event(service="api"), event(service="web"), event()))
        assert signals[0].services == ("api", "web")

    def test_samples_are_the_commonest_titles_and_bounded(self):
        events = tuple(event(title=f"monitor {i}") for i in range(20)) + (event(title="the loud one"),) * 30
        signals = roll_up(events)
        assert len(signals[0].samples) == SAMPLE_CAP
        assert signals[0].samples[0] == "the loud one"

    def test_ordered_by_count_descending_then_by_name(self):
        events = (event(), event(source="grafana"), event(source="grafana"))
        assert [s.source for s in roll_up(events)] == ["grafana", "datadog"]

    def test_carries_the_window_it_measured(self):
        # A signal riding on a one-day standup that measured fourteen days must
        # be able to say so.
        signals = roll_up((event(),), window_start="2026-06-01T00:00:00+00:00", window_end="2026-06-15T00:00:00+00:00")
        assert signals[0].window_start.startswith("2026-06-01")
        assert signals[0].window_end.startswith("2026-06-15")

    def test_family_comes_from_the_lookup(self):
        signals = roll_up((event(),), family_of={"datadog": "observability"})
        assert signals[0].family == "observability"

    def test_an_unknown_source_gets_no_family_rather_than_a_guess(self):
        assert roll_up((event(source="mystery"),))[0].family == ""


class TestWorstSeverity:
    def test_picks_the_most_severe_word_present(self):
        batch = (event(severity="low"), event(severity="critical"), event(severity="medium"))
        assert worst_severity(batch) == "critical"

    def test_empty_when_nothing_said(self):
        assert worst_severity((event(), event())) == ""

    def test_reaches_the_signal(self):
        assert roll_up((event(severity="low"), event(severity="high")))[0].severity == "high"


class TestTheSignalShape:
    def test_it_is_frozen_and_carries_no_body(self):
        names = {f.name for f in dataclasses.fields(OpsSignal)}
        assert not (names & {"body", "text", "events", "raw", "author", "members"})
        assert OpsSignal.__dataclass_params__.frozen

    def test_it_is_a_sibling_of_supporting_signal_not_a_subclass(self):
        # signals_sentence() sums a closed three-set positionally; an ops kind
        # inside SupportingSignal would fall out of the sentence while still
        # printing in the export.
        from yeaboi.agent.state import SupportingSignal

        assert not issubclass(OpsSignal, SupportingSignal)
        assert {f.name for f in dataclasses.fields(OpsSignal)} - {f.name for f in dataclasses.fields(SupportingSignal)}
