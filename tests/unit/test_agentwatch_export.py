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
