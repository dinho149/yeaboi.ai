"""Tests for agentwatch render/export and the Agent Usage TUI screen."""

from rich.console import Console

from yeaboi.agent.state import AgentUsageBreakdownRow, AgentUsageReport, DailyUsagePoint, ModelUsageRow
from yeaboi.agentwatch.export import build_usage_markdown
from yeaboi.agentwatch.render import format_usage_rich
from yeaboi.ui.mode_select.screens._screens_agents import _build_agent_usage_screen


def make_report(**overrides) -> AgentUsageReport:
    base = dict(
        period_start="2026-07-10",
        period_end="2026-08-08",
        session_count=3,
        total_cost_usd=31.1,
        total_input_tokens=2_000_000,
        total_output_tokens=1_000_000,
        total_cache_write_tokens=50_000,
        total_cache_read_tokens=900_000,
        unknown_model_cost_share=0.25,
        pricing_as_of="2026-06-24",
        by_model=(
            ModelUsageRow(
                model="claude-opus-5", input_tokens=1_000_000, output_tokens=1_000_000, calls=3, cost_usd=30.0
            ),
            ModelUsageRow(model="mystery", input_tokens=1_000_000, calls=1, cost_usd=1.1, known_pricing=False),
        ),
        by_project=(AgentUsageBreakdownRow(key="webapp", sessions=2, input_tokens=1_500_000, cost_usd=20.5),),
        by_source=(AgentUsageBreakdownRow(key="claude_code", sessions=3, cost_usd=31.1),),
        daily_trend=(DailyUsagePoint(date="2026-08-07", cost_usd=31.1, sessions=3),),
        insights=("spend is concentrated on opus",),
        recommendations=("try haiku for drafts",),
        warnings=("AI output unavailable — no API key set.",),
    )
    base.update(overrides)
    return AgentUsageReport(**base)


def _render(renderable, width=100) -> str:
    console = Console(width=width, force_terminal=False)
    with console.capture() as cap:
        console.print(renderable)
    return cap.get()


class TestRender:
    def test_rich_output_carries_the_essentials(self):
        out = _render(format_usage_rich(make_report()))
        assert "$31.10" in out
        assert "claude-opus-5" in out
        assert "webapp" in out
        assert "spend is concentrated" in out
        assert "AI output unavailable" in out

    def test_unknown_share_is_flagged(self):
        out = _render(format_usage_rich(make_report()))
        assert "25%" in out
        assert "mystery *" in out


class TestMarkdown:
    def test_document_structure(self):
        md = build_usage_markdown(make_report())
        assert md.startswith("# Agent Usage — 2026-07-10 → 2026-08-08")
        assert "## By model" in md
        assert "## Daily trend" in md
        assert "not a provider bill" in md
        assert "| claude-opus-5 | $30.00 |" in md

    def test_export_writes_dated_markdown(self, monkeypatch, tmp_path):
        import yeaboi.agentwatch.export as export_mod

        monkeypatch.setattr("yeaboi.paths.get_agentwatch_export_dir", lambda kind: tmp_path)
        paths = export_mod.export_artifact(make_report(), kind="usage")
        assert set(paths) == {"markdown"}  # HTML is a tracked follow-up, not a silent gap
        assert paths["markdown"].read_text(encoding="utf-8").startswith("# Agent Usage")


class TestScreen:
    def test_running_state_shows_status(self):
        out = _render(
            _build_agent_usage_screen(None, width=100, height=30, shimmer_tick=0.2, status="Pricing 3 session(s)")
        )
        assert "Pricing 3 session(s)" in out

    def test_report_state_shows_dashboard_and_actions(self):
        out = _render(_build_agent_usage_screen(make_report(), width=100, height=40, shimmer_tick=None))
        assert "$31.10" in out
        # Real action buttons (build_action_buttons), not an inlined key strip.
        for action in ("Export", "Copy", "Re-run", "Back"):
            assert action in out

    def test_notice_line_reports_an_export(self):
        out = _render(
            _build_agent_usage_screen(
                make_report(), width=100, height=40, shimmer_tick=None, notice="Exported to /tmp/usage.md"
            )
        )
        assert "Exported to /tmp/usage.md" in out

    def test_row_caps_note_the_export(self):
        many = tuple(ModelUsageRow(model=f"model-{i}", input_tokens=1, cost_usd=float(10 - i)) for i in range(8))
        out = _render(_build_agent_usage_screen(make_report(by_model=many), width=100, height=40, shimmer_tick=None))
        assert "3 more model(s) in the export" in out


class TestProgressScreen:
    """The structured loading state: phase checklist + files meter."""

    @staticmethod
    def _events(*specs):
        from yeaboi.analysis.progress import append_component_progress

        events: list = []
        for spec in specs:
            append_component_progress(events, **spec)
        return events

    def test_checklist_renders_meter_marks_and_pending(self):
        events = self._events(
            dict(
                component_id="scan",
                label="Scanning agent sessions",
                status="running",
                current=3,
                total=10,
                unit="files",
            )
        )
        out = _render(_build_agent_usage_screen(None, width=100, height=40, shimmer_tick=4.0, progress=events))
        assert "▰" in out  # the meter
        assert "3/10 files" in out
        assert "Scan agent sessions" in out
        assert "○" in out  # pending phases
        assert "Price usage" in out
        assert "Write insights" in out
        assert "0:04" in out  # elapsed from the tick

    def test_completed_phase_shows_its_detail(self):
        events = self._events(
            dict(component_id="scan", label="Scan agent sessions", status="completed", detail="2 parsed · 8 cached"),
            dict(component_id="price", label="Price usage", status="running"),
        )
        out = _render(_build_agent_usage_screen(None, width=100, height=40, shimmer_tick=0.2, progress=events))
        assert "✓" in out
        assert "2 parsed · 8 cached" in out

    def test_status_only_fallback_still_renders(self):
        # The legacy one-line path (no structured events) must keep working.
        out = _render(_build_agent_usage_screen(None, width=100, height=30, shimmer_tick=0.2, status="warming up"))
        assert "warming up" in out


class TestRefreshingBanner:
    def test_refreshing_report_carries_age_stamp(self):
        from datetime import UTC, datetime, timedelta

        as_of = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        out = _render(
            _build_agent_usage_screen(
                make_report(), width=100, height=40, shimmer_tick=0.5, refreshing=True, as_of=as_of
            )
        )
        assert "Refreshing…" in out
        assert "2h ago" in out
        assert "$31.10" in out  # the stale report still renders in full

    def test_not_refreshing_shows_no_banner(self):
        out = _render(_build_agent_usage_screen(make_report(), width=100, height=40, shimmer_tick=None))
        assert "Refreshing…" not in out


class TestRelativeAge:
    def test_table(self):
        from datetime import UTC, datetime

        from yeaboi.ui.mode_select.screens._screens_agents import _relative_age

        now = datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC)
        cases = [
            ("2026-08-08T11:59:30+00:00", "just now"),
            ("2026-08-08T11:55:00+00:00", "5m ago"),
            ("2026-08-08T10:00:00+00:00", "2h ago"),
            ("2026-08-05T12:00:00+00:00", "3d ago"),
            ("2026-08-08T10:00:00", "2h ago"),  # naive timestamps are treated as UTC
            ("not a timestamp", ""),
            ("", ""),
        ]
        for iso, expected in cases:
            assert _relative_age(iso, now=now) == expected, iso


class TestRefreshingBannerProgress:
    def test_banner_names_the_running_phase_and_meter(self):
        from yeaboi.analysis.progress import append_component_progress

        events: list = []
        append_component_progress(
            events,
            component_id="scan",
            label="Scan agent sessions",
            status="running",
            current=40,
            total=200,
            unit="files",
        )
        out = _render(
            _build_agent_usage_screen(
                make_report(),
                width=110,
                height=40,
                shimmer_tick=0.5,
                refreshing=True,
                as_of="2020-01-01T00:00:00+00:00",
                progress=events,
            )
        )
        assert "Refreshing — Scan agent sessions 40/200 files" in out

    def test_banner_without_events_keeps_the_plain_form(self):
        out = _render(
            _build_agent_usage_screen(
                make_report(),
                width=100,
                height=40,
                shimmer_tick=0.5,
                refreshing=True,
                as_of="2020-01-01T00:00:00+00:00",
            )
        )
        assert "Refreshing…" in out


class TestStandupPlaintextHeadline:
    """The Slack header must count what the run measured, not what it skipped."""

    def _digest(self, **kw):
        from yeaboi.agent.state import AgentRepoActivityRow, AgentStandupDigest

        base = {"digest_date": "2026-08-13"}
        if kw.pop("with_tracker_row", False):
            kw["repo_activity"] = (AgentRepoActivityRow(kind="pr", title="add retry", repo="webapp", status="merged"),)
        return AgentStandupDigest(**base, **kw)

    def test_sessions_lead_when_there_are_any(self):
        from yeaboi.agentwatch.export import build_standup_plaintext

        text = build_standup_plaintext(self._digest(sessions_worked=3, total_cost_usd=12.5))
        assert "3 session(s), $12.50 estimated" in text

    def test_a_tracker_only_run_never_reports_zero_sessions_and_no_spend(self):
        """ "0 session(s), $0.00 estimated" reads as "the agents were free today"
        when it means "that is not what this run measured"."""
        from yeaboi.agentwatch.export import build_standup_plaintext

        text = build_standup_plaintext(self._digest(with_tracker_row=True))
        assert "1 agent-authored tracker item(s)" in text
        assert "session(s)" not in text
        assert "$0.00" not in text

    def test_a_wholly_empty_digest_says_so_plainly(self):
        from yeaboi.agentwatch.export import build_standup_plaintext

        text = build_standup_plaintext(self._digest())
        assert "no agent activity recorded in the window" in text
        assert "$0.00" not in text

    def test_every_renderer_of_the_same_digest_agrees(self):
        """Three surfaces, one headline decision.

        Slack is the one that gets read, but `export_artifact` writes the markdown
        on every run — so a fix applied only to the plaintext builder still ships
        "0 session(s), $0.00 estimated" to a file, where the coverage note that
        explains it is further away than it is in a Slack post.
        """
        from rich.console import Console

        from yeaboi.agentwatch.export import build_standup_markdown, build_standup_plaintext
        from yeaboi.agentwatch.render import format_standup_rich

        digest = self._digest(with_tracker_row=True)
        console = Console(width=200, no_color=True, legacy_windows=False)
        with console.capture() as captured:
            console.print(format_standup_rich(digest))

        for surface, text in (
            ("plaintext", build_standup_plaintext(digest)),
            ("markdown", build_standup_markdown(digest)),
            ("rich", captured.get()),
        ):
            assert "1 agent-authored tracker item(s)" in text, f"{surface} does not count what the run measured"
            assert "session(s)" not in text, f"{surface} still reports sessions it never collected"
            assert "$0.00" not in text, f"{surface} still reports $0.00, which reads as free"
