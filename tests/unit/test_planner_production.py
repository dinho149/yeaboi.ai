"""Tests for production in the sprint planner — prose, and prose only.

The rule this whole layer exists to protect: ops never performs arithmetic on
capacity, velocity or confidence. It is a reason to leave headroom, stated in
words, and the planner is told so explicitly.
"""

import inspect

import pytest

from yeaboi.connectors.fetching import Gathered, SourceResult
from yeaboi.ops.signals import OpsSignal
from yeaboi.prompts.sprint_planner import get_sprint_planner_prompt

PRODUCTION = "Over the last 30 days, the connected monitoring tools saw:\n- 2 incidents via pagerduty"


def _prompt(**fields) -> str:
    base = {
        "project_name": "Acme Portal",
        "project_description": "A portal.",
        "velocity": 20,
        "target_sprints": 3,
        "stories_block": "US-1 Login (3 pts)",
    }
    base.update(fields)
    return get_sprint_planner_prompt(**base)


class TestThePromptBlock:
    def test_present_or_absent_never_empty(self):
        # A model told about an empty section narrates the emptiness, and
        # "production has been stable" is a claim about a baseline we lack.
        assert "Production Load" not in _prompt(production_context="")
        assert "Production Load" in _prompt(production_context=PRODUCTION)

    def test_nothing_connected_is_byte_identical_to_before_this_existed(self):
        assert _prompt(production_context="") == _prompt()

    def test_it_forbids_arithmetic_on_capacity(self):
        prompt = _prompt(production_context=PRODUCTION)
        assert "do not change the velocity" in prompt
        assert "do not reduce any sprint's capacity_points because of it" in prompt

    def test_it_forbids_inventing_stories(self):
        assert "do not create, rename or re-scope stories from it" in _prompt(production_context=PRODUCTION)

    def test_it_forbids_narrating_calm(self):
        assert "Say nothing about production being quiet or stable." in _prompt(production_context=PRODUCTION)

    def test_the_signals_themselves_reach_the_model(self):
        assert "2 incidents via pagerduty" in _prompt(production_context=PRODUCTION)


class TestItIsThePlannerAndNotTheAnalyzer:
    def test_the_analyzer_prompt_takes_no_production_argument(self):
        from yeaboi.prompts.analyzer import get_analyzer_prompt

        # The analyzer's output feeds feature generation, and a feature invented
        # from an incident is a story nobody asked for.
        params = inspect.signature(get_analyzer_prompt).parameters
        assert not {p for p in params if "production" in p or p.startswith("ops")}

    def test_only_the_planner_node_gathers_it(self):
        from yeaboi.agent import nodes

        source = inspect.getsource(nodes)
        calls = [line for line in source.splitlines() if "_gather_ops_summary()" in line and "def " not in line]
        assert calls == ["        production_context=_gather_ops_summary(),"]


class TestTheGatherer:
    def test_nothing_connected_never_reaches_a_vendor(self, monkeypatch):
        def boom(**kw):
            raise AssertionError("gather must not be called with nothing connected")

        from yeaboi.agent import nodes

        monkeypatch.setattr("yeaboi.connectors.registry.any_fetchable", lambda: False)
        monkeypatch.setattr("yeaboi.connectors.fetching.gather", boom)
        assert nodes._gather_ops_summary() == ""

    def test_it_reads_a_month_and_renders_one_line_per_signal(self, monkeypatch):
        from yeaboi.agent import nodes

        seen = {}
        monkeypatch.setattr("yeaboi.connectors.registry.any_fetchable", lambda: True)
        monkeypatch.setattr(
            "yeaboi.connectors.fetching.gather",
            lambda **kw: (
                seen.update(kw)
                or Gathered(
                    sources=(SourceResult(key="pagerduty", label="PagerDuty", ok=True, count=2),),
                    signals=(OpsSignal(kind="incident", source="pagerduty", count=2, resolved=1, severity="high"),),
                )
            ),
        )
        summary = nodes._gather_ops_summary()
        assert seen["since"] == "30d"
        assert summary == (
            "Over the last 30 days, the connected monitoring tools saw:\n"
            "- 2 incidents via pagerduty · 1 resolved · worst high"
        )

    def test_a_quiet_month_says_nothing_rather_than_saying_it_was_quiet(self, monkeypatch):
        from yeaboi.agent import nodes

        monkeypatch.setattr("yeaboi.connectors.registry.any_fetchable", lambda: True)
        monkeypatch.setattr("yeaboi.connectors.fetching.gather", lambda **kw: Gathered())
        assert nodes._gather_ops_summary() == ""

    def test_a_gather_that_raises_costs_the_note_not_the_plan(self, monkeypatch):
        from yeaboi.agent import nodes

        def boom(**kw):
            raise RuntimeError("network is down")

        monkeypatch.setattr("yeaboi.connectors.registry.any_fetchable", lambda: True)
        monkeypatch.setattr("yeaboi.connectors.fetching.gather", boom)
        assert nodes._gather_ops_summary() == ""


class TestOpsNeverTouchesPerformance:
    @pytest.mark.parametrize("module", ["engine", "context", "evidence"])
    def test_the_performance_package_never_reads_a_connector(self, module):
        import importlib

        source = inspect.getsource(importlib.import_module(f"yeaboi.performance.{module}"))
        for banned in ("yeaboi.connectors", "yeaboi.ops", "OpsSignal", "OpsEvent"):
            assert banned not in source
