"""Tests for production in the delivery report — its own field, its own heading.

The two load-bearing assertions: the window is the report's own (not a lookback
from now), and nothing about production reaches the LLM design call.
"""

import json

import pytest

from yeaboi.agent.state import DeliveryReport, SupportingSignal
from yeaboi.connectors.fetching import Gathered, SourceResult
from yeaboi.ops.signals import OpsSignal
from yeaboi.reporting import context as rc


def _signal(**fields) -> OpsSignal:
    base = {
        "kind": "incident",
        "family": "incidents",
        "source": "pagerduty",
        "count": 2,
        "resolved": 1,
        "severity": "high",
        "services": ("checkout",),
        "window_start": "2026-06-15T00:00:00+00:00",
        "window_end": "2026-07-13T23:59:59+00:00",
        "samples": ("Checkout latency",),
    }
    base.update(fields)
    return OpsSignal(**base)


def _report(**fields) -> DeliveryReport:
    base = {
        "period_label": "Last month",
        "period_start": "2026-06-15",
        "period_end": "2026-07-13",
        "project_name": "Acme Portal",
        "headline": "Two sprints of strong delivery.",
        "executive_summary": "We shipped SSO.",
        "metrics": (("Items delivered", "12"),),
        "ops_signals": (_signal(),),
    }
    base.update(fields)
    return DeliveryReport(**base)


class TestTheSentence:
    def test_it_never_claims_corroboration(self):
        sentence = rc.ops_sentence((_signal(),))
        assert "orroborat" not in sentence
        assert sentence == "Production saw 2 incidents over the same period"

    def test_one_of_a_kind_is_singular(self):
        assert rc.ops_sentence((_signal(count=1),)).endswith("1 incident over the same period")

    def test_a_kind_outside_the_closed_three_still_reaches_the_sentence(self):
        # The bug that made OpsSignal a sibling rather than an extension:
        # signals_sentence sums a fixed three positionally.
        assert "1 error spike" in rc.ops_sentence((_signal(kind="error_spike", count=1),))

    def test_nothing_to_say_says_nothing(self):
        assert rc.ops_sentence(()) == ""


class TestTheWindowIsTheReportsOwn:
    def test_the_period_is_passed_through_not_a_lookback(self, monkeypatch):
        seen = {}
        monkeypatch.setattr("yeaboi.connectors.registry.any_fetchable", lambda: True)
        monkeypatch.setattr(
            "yeaboi.connectors.fetching.gather",
            lambda **kw: seen.update(kw) or Gathered(),
        )
        rc.gather_ops_signals(period_start="2026-06-15", period_end="2026-07-13")
        start, end = seen["window"]
        assert (start.date().isoformat(), end.date().isoformat()) == ("2026-06-15", "2026-07-13")
        assert "since" not in seen  # a lookback cannot express a finished sprint

    def test_nothing_connected_never_reaches_a_vendor(self, monkeypatch):
        def boom(**kw):
            raise AssertionError("gather must not be called with nothing connected")

        monkeypatch.setattr("yeaboi.connectors.registry.any_fetchable", lambda: False)
        monkeypatch.setattr("yeaboi.connectors.fetching.gather", boom)
        assert rc.gather_ops_signals(period_start="2026-06-15", period_end="2026-07-13") == ((), [])

    def test_a_connected_vendor_that_failed_becomes_a_warning(self, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.registry.any_fetchable", lambda: True)
        monkeypatch.setattr(
            "yeaboi.connectors.fetching.gather",
            lambda **kw: Gathered(sources=(SourceResult(key="datadog", label="Datadog", error="rate limited"),)),
        )
        _, warnings = rc.gather_ops_signals(period_start="2026-06-15", period_end="2026-07-13")
        assert warnings == ["Datadog: rate limited"]

    def test_a_gather_that_raises_costs_the_ops_read_not_the_report(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("network is down")

        monkeypatch.setattr("yeaboi.connectors.registry.any_fetchable", lambda: True)
        monkeypatch.setattr("yeaboi.connectors.fetching.gather", boom)
        signals, warnings = rc.gather_ops_signals(period_start="2026-06-15", period_end="2026-07-13")
        assert signals == () and len(warnings) == 1


class TestItStaysOutOfTheLLM:
    def test_the_design_prompt_never_sees_production(self):
        from yeaboi.prompts.reporting import get_delivery_report_prompt

        prompt = get_delivery_report_prompt(
            delivered_items=[{"key": "ACME-1", "title": "SSO", "status": "Done"}],
            project_name="Acme Portal",
            period_label="Last month",
            sprint_names=["Sprint 11"],
            supporting_signals=[{"kind": "pull_requests", "source": "github", "count": 24, "samples": []}],
        )
        for word in ("incident", "pagerduty", "production", "Production"):
            assert word not in prompt

    def test_the_prompt_takes_no_production_argument_at_all(self):
        import inspect

        from yeaboi.prompts.reporting import get_delivery_report_prompt

        params = inspect.signature(get_delivery_report_prompt).parameters
        assert not {p for p in params if "production" in p or "ops" in p}


class TestRenderers:
    def test_the_terminal_earns_the_heading(self):
        from yeaboi.reporting.render import format_report_rich

        rendered = _render(format_report_rich(_report()))
        assert "Production" in rendered
        assert "Production saw 2 incidents" in rendered

    def test_no_signals_means_no_heading_anywhere(self):
        from yeaboi.reporting.export import build_report_markdown
        from yeaboi.reporting.render import format_report_rich

        report = _report(ops_signals=())
        assert "Production" not in _render(format_report_rich(report))
        assert "Production" not in build_report_markdown(report)

    def test_markdown_carries_the_counts_and_the_disclaimer(self):
        from yeaboi.reporting.export import build_report_markdown

        md = build_report_markdown(_report())
        assert "## 🚨 Production" in md
        assert "**Incident · pagerduty:** 2 (1 resolved) · worst high" in md
        assert "_Team-wide, and not attributed to anyone._" in md

    def test_a_monitor_name_cannot_mint_a_link_in_markdown(self):
        from yeaboi.reporting.export import build_report_markdown

        md = build_report_markdown(
            _report(ops_signals=(_signal(samples=("Latency [above SLO](javascript:alert(1))",)),))
        )
        assert "Latency \\[above SLO\\](javascript:alert(1))" in md

    def test_production_is_never_folded_into_the_corroboration_sentence(self):
        from yeaboi.reporting.export import build_report_markdown

        md = build_report_markdown(
            _report(supporting_signals=(SupportingSignal(kind="pull_requests", source="github", count=24),))
        )
        corroboration = next(line for line in md.splitlines() if "Corroborated by" in line)
        assert "incident" not in corroboration

    def test_the_html_payload_carries_rows_but_no_person(self):
        from tests._pages import island
        from yeaboi.reporting.export import build_report_html

        payload = island(build_report_html(_report()))["report"]["production"]
        assert payload == [
            {
                "kind": "incident",
                "source": "pagerduty",
                "family": "incidents",
                "count": 2,
                "resolved": 1,
                "severity": "high",
                "services": ["checkout"],
                "samples": ["Checkout latency"],
                "window": {"start": "2026-06-15T00:00:00+00:00", "end": "2026-07-13T23:59:59+00:00"},
            }
        ]
        assert not {k for row in payload for k in row if k in ("author", "assignee", "members")}


class TestTheDeck:
    def test_production_gets_a_slide_of_its_own(self):
        from yeaboi.reporting.presentation import _build_slides
        from yeaboi.reporting.style import DeckStyle

        slides = _build_slides(_report(), DeckStyle())
        production = [s for s in slides if s.get("title") == "Production"]
        assert len(production) == 1
        assert production[0]["items"] == ["2 incidents via pagerduty · 1 resolved · worst high · services: checkout"]

    def test_the_slide_uses_no_key_the_front_end_does_not_already_draw(self):
        from yeaboi.reporting.presentation import _build_slides
        from yeaboi.reporting.style import DeckStyle

        slides = _build_slides(_report(), DeckStyle())
        drawn = {k for s in slides if s["type"] == "list" for k in s}
        production = next(s for s in slides if s.get("title") == "Production")
        assert set(production) <= drawn

    def test_it_never_becomes_a_second_footnote_on_the_metrics_slide(self):
        from yeaboi.reporting.presentation import _build_slides
        from yeaboi.reporting.style import DeckStyle

        metrics = next(s for s in _build_slides(_report(), DeckStyle()) if s["type"] == "metrics")
        assert "incident" not in str(metrics.get("footnote", ""))

    def test_the_style_flag_drops_it(self):
        from yeaboi.reporting.presentation import _build_slides
        from yeaboi.reporting.style import DeckStyle

        slides = _build_slides(_report(), DeckStyle(include_production=False))
        assert not [s for s in slides if s.get("title") == "Production"]


class TestTheStoreRoundTrip:
    def test_a_signal_survives_json(self):
        from dataclasses import asdict

        from yeaboi.reporting.store import _dict_to_report

        raw = json.loads(json.dumps(asdict(_report())))
        assert _dict_to_report(raw).ops_signals == (_signal(),)

    def test_a_report_saved_before_production_existed_still_loads(self):
        from yeaboi.reporting.store import _dict_to_report

        assert _dict_to_report({"period_label": "Last month"}).ops_signals == ()


def _render(renderable) -> str:
    from rich.console import Console

    console = Console(width=200, record=True, force_terminal=False)
    console.print(renderable)
    return console.export_text()


@pytest.fixture(autouse=True)
def _no_vendors(monkeypatch):
    """No test here may reach a real connector."""
    monkeypatch.setattr("yeaboi.connectors.registry.any_fetchable", lambda: False, raising=False)
