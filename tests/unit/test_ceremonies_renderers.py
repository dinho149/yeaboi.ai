"""Tests for the artifact → Dispatch renderers (ceremonies/renderers.py).

These land in a chat message, so the properties worth pinning are about
restraint: a summary short enough for a desktop notification, a body that stops
rather than pasting a whole report into Slack, and a truncation the reader can
see. The standup's byte-identity pin lives in test_ceremonies_delivery.py, next
to the channels it protects.
"""

from __future__ import annotations

import pytest

from yeaboi.agent.state import (
    AgentAdvisorReport,
    AgentSecurityReport,
    AgentUsageReport,
    DeliveryReport,
    ModelUsageRow,
    ReviewAction,
    SecurityFinding,
    WeeklyReview,
)
from yeaboi.ceremonies import renderers
from yeaboi.ceremonies.catalog import CATALOG, renderer_callable


class TestReportDispatch:
    def test_leads_with_the_headline_and_carries_the_metrics(self):
        d = renderers.report_dispatch(
            DeliveryReport(
                period_label="Last week",
                project_name="Apollo",
                headline="Checkout shipped",
                executive_summary="Two epics closed.",
                metrics=(("Items delivered", "23"), ("Cycle time", "3.1d")),
                highlights=("Checkout live",),
            )
        )
        assert d.title == "Delivery report — Last week"
        assert d.summary == "Checkout shipped"
        assert "Items delivered: 23" in d.body
        assert "Two epics closed." in d.body

    def test_a_report_with_no_llm_prose_still_has_a_summary(self):
        d = renderers.report_dispatch(DeliveryReport(period_label="Last week", project_name="Apollo"))
        assert "Apollo" in d.summary


class TestWeeklyReviewDispatch:
    def test_leads_with_the_on_track_line_and_carries_the_lists(self):
        d = renderers.weekly_review_dispatch(
            WeeklyReview(
                week_label="2026-W35",
                plan_line="Day 4/10 · On track",
                summary="A steady week.",
                went_well=("Shipped login",),
                to_change=("Fewer context switches",),
                actions=(ReviewAction(id="a1", text="Write the ADR"),),
            )
        )
        assert d.title == "Weekly review — 2026-W35"
        assert d.summary == "Day 4/10 · On track"
        assert "A steady week." in d.body
        assert "- Shipped login" in d.body and "- Fewer context switches" in d.body and "- Write the ADR" in d.body

    def test_without_a_plan_line_the_summary_leads(self):
        d = renderers.weekly_review_dispatch(WeeklyReview(week_start="2026-08-24", summary="Quiet."))
        assert d.title == "Weekly review — 2026-08-24"
        assert d.summary == "Quiet."


class TestAgentDispatches:
    def test_usage_leads_with_the_total(self):
        d = renderers.agent_usage_dispatch(
            AgentUsageReport(
                period_start="2026-08-01",
                period_end="2026-08-17",
                session_count=12,
                total_cost_usd=41.5,
                by_model=(ModelUsageRow(model="claude-opus-5", calls=90, cost_usd=38.0),),
            )
        )
        assert "$41.50" in d.summary
        assert "12 agent sessions" in d.summary
        assert "claude-opus-5: $38.00 over 90 calls" in d.body

    def test_a_single_session_is_not_pluralised(self):
        d = renderers.agent_usage_dispatch(AgentUsageReport(session_count=1, total_cost_usd=1.0))
        assert "1 agent session." in d.summary

    def test_advisor_leads_with_recoverable_spend_and_keeps_the_caveat(self):
        d = renderers.agent_advisor_dispatch(
            AgentAdvisorReport(
                session_count=8,
                files_audited=40,
                total_cost_usd=100.0,
                recoverable_usd=12.5,
                recoverable_share=0.125,
                recommendations=("Trim the prefix",),
            )
        )
        assert "$12.50 recoverable (12% of $100.00)" in d.summary  # 12.5 rounds half-to-even
        # The mode's honesty caveat must survive the trip into a chat message.
        assert "estimate" in d.body

    def test_security_leads_with_posture_and_only_the_serious_findings(self):
        d = renderers.agent_security_dispatch(
            AgentSecurityReport(
                posture="needs-attention",
                secrets_found=1,
                summary="One key in a settings file.",
                findings=(
                    SecurityFinding(severity="critical", title="API key", location="~/.claude/settings.json"),
                    SecurityFinding(severity="info", title="Nothing much", location="elsewhere"),
                ),
            )
        )
        assert d.title == "Agent security — needs-attention"
        assert "1 high/critical finding," in d.summary
        assert "[critical] API key" in d.body
        assert "Nothing much" not in d.body
        assert "not a security audit" in d.body


class TestRestraint:
    def test_a_long_list_is_capped_with_the_remainder_named(self):
        # Silently dropping items reads as "that was all of them".
        d = renderers.agent_security_dispatch(
            AgentSecurityReport(
                posture="at-risk",
                findings=tuple(SecurityFinding(severity="high", title=f"f{n}", location="x") for n in range(20)),
            )
        )
        assert "…and 14 more" in d.body

    def test_a_huge_body_is_truncated_visibly(self):
        d = renderers.report_dispatch(DeliveryReport(period_label="Q3", executive_summary="x" * 10_000))
        assert len(d.body) < 4000
        assert "truncated" in d.body

    @pytest.mark.parametrize("mode", CATALOG, ids=lambda m: m.key)
    def test_every_renderer_survives_an_empty_artifact(self, mode):
        # A quiet day is not an error, and a ceremony that crashes on one is a
        # ceremony that stops firing on the day nothing happened.
        import inspect

        renderer = renderer_callable(mode)
        artifact_type = list(inspect.signature(renderer).parameters.values())[0].annotation
        resolved = getattr(__import__("yeaboi.agent.state", fromlist=["x"]), artifact_type)
        dispatch = renderer(resolved())
        assert dispatch.title
        assert dispatch.summary


class TestLocalStamp:
    """The ledger stores UTC; a ceremony's ``at`` is local. Display must convert.

    Slicing the stored string showed a run at 17:13 for a ceremony scheduled at
    18:13, which reads as a bug in the scheduler rather than a timezone in the
    renderer — it was caught by firing one for real, not by a unit test.
    """

    def test_a_utc_stamp_is_shown_in_local_time(self, monkeypatch):
        import os
        import time

        from yeaboi.ceremonies.render import local_stamp

        monkeypatch.setenv("TZ", "Europe/London")
        time.tzset()
        try:
            # 17:13 UTC in August is 18:13 in London.
            assert local_stamp("2026-08-17T17:13:00+00:00") == "2026-08-17 18:13"
            assert local_stamp("2026-08-17T17:13:00+00:00", with_date=False) == "08-17 18:13"
        finally:
            os.environ.pop("TZ", None)
            time.tzset()

    def test_an_unparseable_stamp_is_shown_rather_than_raising(self):
        from yeaboi.ceremonies.render import local_stamp

        # A history row with an odd stamp is still a row worth showing.
        assert local_stamp("not a time") == "not a time"
        assert local_stamp("") == ""
