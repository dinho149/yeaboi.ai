"""Tests for agentwatch's Rich renderers (agentwatch/render.py).

Two jobs. The render tests are the usual "does it draw without blowing up, and
does the number the user cares about actually appear" pass. The theme test is
the interesting one: ``render.py`` hardcodes its accents (the same convention
``performance/render.py`` and ``standup/render.py`` use, so an engine-layer
module never imports the TUI), and a hardcoded copy held together by a comment
is drift waiting to happen. This pins the copy to its source instead.
"""

from rich.console import Console

from yeaboi.agent.state import (
    AgentAdvisorReport,
    AgentSecurityReport,
    AgentUsageBreakdownRow,
    AgentUsageReport,
    ModelUsageRow,
    SecurityFinding,
    VolatileFileSignal,
    WasteLineItem,
)
from yeaboi.agentwatch.render import (
    _ACCENT,
    _ADVISOR_ACCENT,
    _SECURITY_ACCENT,
    _tokens,
    format_advisor_rich,
    format_security_rich,
    format_usage_rich,
)
from yeaboi.ui.shared._components import (
    AGENT_ADVISOR_THEME,
    AGENT_SECURITY_THEME,
    AGENT_USAGE_THEME,
)


def _plain(renderable) -> str:
    console = Console(width=100, force_terminal=False)
    with console.capture() as cap:
        console.print(renderable)
    return cap.get()


class TestThemeParity:
    """The CLI renderer's accent must be the TUI theme's accent.

    render.py cannot import the theme (engine layer must not depend on ui/),
    so the two are separate literals. Without this test the only thing holding
    them together is a trailing comment, and a theme tweak would silently make
    the CLI and the TUI disagree about what colour the mode is.
    """

    def test_accents_match_their_themes(self):
        assert _ACCENT == AGENT_USAGE_THEME.accent
        assert _SECURITY_ACCENT == AGENT_SECURITY_THEME.accent
        assert _ADVISOR_ACCENT == AGENT_ADVISOR_THEME.accent


def make_advisor_report(**over) -> AgentAdvisorReport:
    defaults = dict(
        period_start="2026-07-10",
        period_end="2026-08-08",
        session_count=3,
        files_audited=4,
        total_cost_usd=31.1,
        read_calls=40,
        read_bytes=100_000,
        tool_bytes_total=250_000,
        recoverable_usd=2.75,
        recoverable_share=0.0884,
        effective_input_rate_per_mtok=5.0,
        pricing_as_of="2026-06-24",
        line_items=(
            WasteLineItem(
                mechanism="identical-repeat",
                label="Identical re-reads",
                calls=3,
                content_bytes=12_000,
                est_tokens=3_000,
                est_usd=1.5,
                share_of_read_bytes=0.12,
                note="the same file read again, byte-identical",
            ),
            WasteLineItem(
                mechanism="stale-reread",
                label="Stale reads (edited after)",
                calls=1,
                content_bytes=4_000,
                est_tokens=1_000,
                est_usd=0.5,
                share_of_read_bytes=0.04,
                recoverable=False,
                note="context, not savings",
            ),
        ),
        residency_median=6,
        residency_p90=20,
        gaps_over_5m=2,
        gaps_over_1h=1,
        sessions_with_gap=1,
        volatile_signals=(VolatileFileSignal(location="/home/dev/webapp/CLAUDE.md", counts=(("uuid", "2"),), total=2),),
        alignment_score=80,
        insights=("re-reads dominate",),
        recommendations=("read once, edit from context",),
    )
    defaults.update(over)
    return AgentAdvisorReport(**defaults)


class TestAdvisorRender:
    def test_headline_line_items_and_health(self):
        out = _plain(format_advisor_rich(make_advisor_report()))
        assert "~$2.75 recoverable" in out
        assert "$31.10 estimated spend" in out
        assert "Identical re-reads" in out
        # The non-recoverable row is starred and footnoted, never silently summed.
        assert "Stale reads (edited after) *" in out
        assert "not counted in the recoverable total" in out
        assert "alignment 80/100" in out
        assert "CLAUDE.md" in out
        assert "re-reads dominate" in out

    def test_unknown_rate_share_is_flagged(self):
        out = _plain(format_advisor_rich(make_advisor_report(unknown_rate_share=0.4)))
        assert "40% of input tokens priced at a fallback tier" in out

    def test_empty_report_still_renders(self):
        out = _plain(format_advisor_rich(AgentAdvisorReport(warnings=("no sessions",))))
        assert "Agent Advisor" in out
        assert "⚠ no sessions" in out


class TestTokens:
    def test_scales_and_rounds(self):
        assert _tokens(999) == "999"
        assert _tokens(1_500) == "1.5k"
        assert _tokens(2_400_000) == "2.4M"

    def test_zero(self):
        assert _tokens(0) == "0"


class TestUsageRender:
    def _report(self, **over) -> AgentUsageReport:
        base = dict(
            period_start="2026-07-01",
            period_end="2026-07-31",
            session_count=3,
            total_cost_usd=12.3456,
            total_input_tokens=1_200_000,
            total_output_tokens=45_000,
            total_cache_read_tokens=900_000,
            total_cache_write_tokens=30_000,
            by_model=(
                ModelUsageRow(
                    model="claude-opus-5",
                    input_tokens=1_200_000,
                    output_tokens=45_000,
                    cache_write_tokens=30_000,
                    cache_read_tokens=900_000,
                    calls=42,
                    cost_usd=12.3456,
                    known_pricing=True,
                ),
            ),
            by_project=(
                AgentUsageBreakdownRow(
                    key="yeaboi", sessions=3, input_tokens=1_200_000, output_tokens=45_000, cost_usd=12.3456
                ),
            ),
        )
        base.update(over)
        return AgentUsageReport(**base)

    def test_renders_totals_and_model(self):
        out = _plain(format_usage_rich(self._report()))
        assert "Agent Usage" in out
        assert "$12.35" in out  # rounded for display, not for storage
        assert "3 session(s)" in out
        assert "claude-opus-5" in out

    def test_unknown_pricing_share_is_flagged(self):
        out = _plain(format_usage_rich(self._report(unknown_model_cost_share=0.42)))
        assert "42%" in out

    def test_empty_report_still_renders(self):
        out = _plain(format_usage_rich(AgentUsageReport()))
        assert "Agent Usage" in out

    def test_warnings_are_shown(self):
        out = _plain(format_usage_rich(self._report(warnings=("AI output unavailable — no key.",))))
        assert "AI output unavailable" in out


class TestSecurityRender:
    def test_posture_and_findings(self):
        report = AgentSecurityReport(
            scan_date="2026-07-31",
            posture="needs-attention",
            sessions_scanned=9,
            secrets_found=1,
            findings=(
                SecurityFinding(
                    category="secret",
                    severity="high",
                    title="Credential-shaped string in a session",
                    pattern="anthropic-api-key",
                    location="session.jsonl",
                    line_no=12,
                ),
            ),
        )
        out = _plain(format_security_rich(report))
        assert "Agent Security" in out
        assert "needs-attention" in out
        assert "Credential-shaped" in out
        # The detector label must reach the screen: every stored secret signal
        # shares one per-category title, so the pattern is the only thing that
        # says which check fired.
        assert "anthropic-api-key" in out
        assert "session.jsonl:12" in out

    def test_never_renders_matched_content(self):
        # The privacy invariant reaches the screen too, not just the store.
        report = AgentSecurityReport(
            findings=(
                SecurityFinding(
                    category="secret",
                    severity="high",
                    title="Credential-shaped string in a session",
                    pattern="anthropic-api-key",
                    location="session.jsonl",
                    line_no=12,
                ),
            ),
        )
        assert "sk-ant-" not in _plain(format_security_rich(report))

    def test_empty_report_still_renders(self):
        assert "Agent Security" in _plain(format_security_rich(AgentSecurityReport()))


class TestSparkline:
    def test_scales_to_the_peak_and_names_it(self):
        from yeaboi.agent.state import DailyUsagePoint
        from yeaboi.agentwatch.render import sparkline

        points = (
            DailyUsagePoint(date="2026-08-01", cost_usd=1.0),
            DailyUsagePoint(date="2026-08-02", cost_usd=4.0),
            DailyUsagePoint(date="2026-08-03", cost_usd=0.0),
        )
        out = sparkline(points).plain
        assert "█" in out and "08-01 → 08-03" in out and "peak $4.00" in out

    def test_empty_trend_renders_nothing_but_the_label(self):
        from yeaboi.agentwatch.render import sparkline

        assert sparkline(()).plain.strip() == "trend"
