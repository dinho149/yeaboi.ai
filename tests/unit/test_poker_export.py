"""Tests for the poker exporter (poker/export.py) — MD/HTML content + escaping."""

from yeaboi.agent.state import PokerReport, PokerTicketResult, PokerVote
from yeaboi.poker.export import build_poker_html, build_poker_markdown, export_poker


def _report() -> PokerReport:
    return PokerReport(
        date="2026-07-25",
        session_id="sess-1",
        project_name="Proj",
        source="jira",
        scope_label="Sprint 42",
        tickets=(
            PokerTicketResult(
                key="PROJ-1",
                url="https://x.atlassian.net/browse/PROJ-1",
                summary="Add <script>alert(1)</script> login",
                description="Details",
                initial_points=None,
                final_points=5.0,
                estimated=True,
                votes=(PokerVote("Alex", "🦊", "5"), PokerVote("Sam", "🐙", "8")),
                ai_note="The 8 voter sees <b>risk</b>",
                duel_transcript="Alex (voted 5) — turn 1:\nIt's a <script>simple</script> endpoint.",
                duel_low="Alex (5)",
                duel_high="Sam (8)",
            ),
            PokerTicketResult(key="PROJ-2", summary="Skipped one", initial_points=3.0),
        ),
        participants=("Alex", "Sam"),
        generated_at="2026-07-25T10:00:00+00:00",
    )


class TestMarkdown:
    def test_content(self):
        md = build_poker_markdown(_report())
        assert "# Planning Poker — Sprint 42" in md
        assert "**Estimated:** 1/2 tickets" in md
        assert "[PROJ-1](https://x.atlassian.net/browse/PROJ-1)" in md
        assert "Alex 5 · Sam 8" in md
        assert "_skipped_" in md  # unestimated ticket
        assert "| 3 |" in md  # initial points rendered without trailing .0
        assert "## AI perspectives" in md
        assert "Alex, Sam" in md

    def test_no_ai_section_when_no_notes(self):
        report = PokerReport(tickets=(PokerTicketResult(key="X-1"),))
        assert "AI perspectives" not in build_poker_markdown(report)

    def test_duel_section(self):
        md = build_poker_markdown(_report())
        assert "## Duels" in md
        assert "**PROJ-1** — Alex (5) vs Sam (8)" in md
        # Transcript is block-quoted, line by line.
        assert "> Alex (voted 5) — turn 1:" in md

    def test_no_duel_section_without_transcripts(self):
        report = PokerReport(tickets=(PokerTicketResult(key="X-1"),))
        assert "## Duels" not in build_poker_markdown(report)


class TestHtml:
    def test_escapes_untrusted_text(self):
        html = build_poker_html(_report())
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "&lt;b&gt;risk&lt;/b&gt;" in html
        # The duel transcript is participant speech — escaped like everything else.
        assert "&lt;script&gt;simple&lt;/script&gt;" in html

    def test_structure(self):
        html = build_poker_html(_report())
        assert html.startswith("<!DOCTYPE html>")
        assert "Planning Poker" in html
        assert "PROJ-1" in html
        assert "skipped" in html

    def test_duel_section(self):
        html = build_poker_html(_report())
        assert "<h2>Duels</h2>" in html
        assert "Alex (5) vs Sam (8)" in html


class TestExportPoker:
    def test_writes_both_files(self, tmp_path, monkeypatch):
        out_dir = tmp_path / "exports"
        monkeypatch.setattr("yeaboi.paths.get_poker_export_dir", lambda key: out_dir / key)
        (out_dir / "proj").mkdir(parents=True)
        paths = export_poker(_report())
        assert paths["markdown"].name == "poker-2026-07-25.md"
        assert paths["html"].name == "poker-2026-07-25.html"
        assert paths["markdown"].read_text(encoding="utf-8").startswith("# Planning Poker")
        assert "<!DOCTYPE html>" in paths["html"].read_text(encoding="utf-8")
