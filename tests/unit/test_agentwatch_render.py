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
    AgentSecurityReport,
    AgentStandupDigest,
    AgentUsageBreakdownRow,
    AgentUsageReport,
    ModelUsageRow,
    SecurityFinding,
)
from yeaboi.agentwatch.render import (
    _ACCENT,
    _SECURITY_ACCENT,
    _STANDUP_ACCENT,
    _tokens,
    format_security_rich,
    format_standup_rich,
    format_usage_rich,
)
from yeaboi.ui.shared._components import (
    AGENT_SECURITY_THEME,
    AGENT_STANDUP_THEME,
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
        assert _STANDUP_ACCENT == AGENT_STANDUP_THEME.accent
        assert _SECURITY_ACCENT == AGENT_SECURITY_THEME.accent


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


class TestStandupRender:
    def test_renders_narrative_and_totals(self):
        digest = AgentStandupDigest(
            window_start="2026-07-30",
            window_end="2026-07-31",
            sessions_worked=5,
            total_cost_usd=3.5,
            agents_seen=("claude_code",),
            narrative="Agents shipped the collector and two tests.",
        )
        out = _plain(format_standup_rich(digest))
        assert "Agent Standup" in out
        assert "5 session(s)" in out
        assert "claude_code" in out
        assert "shipped the collector" in out

    def test_empty_digest_still_renders(self):
        assert "Agent Standup" in _plain(format_standup_rich(AgentStandupDigest()))


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
