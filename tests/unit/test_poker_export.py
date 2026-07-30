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
        # The shared page shell carries its own theme-switcher <script>, so
        # assert on the attack strings specifically.
        assert "<script>alert(1)</script>" not in html
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


def _history(*rows):
    """Newest-first rows mirroring PokerStore.get_history output."""
    return [
        {
            "id": i,
            "run_at": f"{d}T15:00:00",
            "poker_date": d,
            "project_name": "Proj",
            "source": "jira",
            "scope_label": "Sprint",
            "ticket_count": t,
            "estimated_count": e,
        }
        for i, (d, t, e) in enumerate(rows)
    ]


class TestSharedDesignSystem:
    def test_poker_html_uses_shared_theme(self):
        html = build_poker_html(_report())
        assert 'data-theme="midnight"' in html
        assert "yeaboi-export-theme" in html  # theme switcher present
        assert 'src="http' not in html and "<link" not in html  # self-contained

    def test_nav_sections(self):
        html = build_poker_html(_report())
        assert '<section id="overview">' in html
        assert '<section id="tickets">' in html
        assert '<section id="ai">' in html
        assert '<section id="duels">' in html


class TestVisuals:
    def test_stat_tiles_and_split_bar(self):
        html = build_poker_html(_report())
        assert ">Tickets</div>" in html and ">Estimated</div>" in html and ">Participants</div>" in html
        # class attribute, not the bare token — ".seg-track" also lives in the stylesheet.
        assert 'class="seg-track"' in html
        assert "Estimated 1" in html and "Skipped 1" in html

    def test_participant_and_voter_avatars(self):
        html = build_poker_html(_report())
        assert html.count('class="avatar"') >= 4  # 2 participants + 2 voters
        assert ">A</span>" in html and ">S</span>" in html

    def test_avatar_name_escaped(self):
        rep = PokerReport(
            date="2026-07-25",
            tickets=(
                PokerTicketResult(
                    key="X-1", estimated=True, final_points=3.0, votes=(PokerVote("<b>Eve</b>", "🦊", "3"),)
                ),
            ),
        )
        html = build_poker_html(rep)
        assert "<b>Eve</b>" not in html

    def test_ticket_key_is_badge_link(self):
        html = build_poker_html(_report())
        assert (
            "<a class='badge' href='https://x.atlassian.net/browse/PROJ-1' target='_blank' rel='noopener'>PROJ-1</a>"
            in html
        )

    def test_ai_notes_split_into_bullets(self):
        rep = PokerReport(
            date="2026-07-25",
            tickets=(PokerTicketResult(key="X-1", ai_note="Estimate looks high. Risk is contained; scope is clear."),),
        )
        html = build_poker_html(rep)
        assert "<li>Estimate looks high.</li>" in html
        assert "<li>Risk is contained</li>" in html
        assert "<li>scope is clear.</li>" in html

    def test_sparkline_from_history(self):
        history = _history(("2026-07-25", 8, 5), ("2026-07-11", 10, 9), ("2026-06-27", 7, 7))
        html = build_poker_html(_report(), history=history)
        assert 'class="spark-wrap"' in html
        assert "Estimation trend" in html
        assert "2026-06-27" in html  # oldest label rendered

    def test_no_history_no_sparkline(self):
        assert 'class="spark-wrap"' not in build_poker_html(_report())

    def test_self_contained_with_history(self):
        html = build_poker_html(_report(), history=_history(("2026-07-25", 8, 5), ("2026-07-11", 10, 9)))
        assert 'src="http' not in html and "<link" not in html

    def test_export_forwards_history(self, tmp_path, monkeypatch):
        out_dir = tmp_path / "exports"
        monkeypatch.setattr("yeaboi.paths.get_poker_export_dir", lambda key: out_dir / key)
        (out_dir / "proj").mkdir(parents=True)
        paths = export_poker(_report(), history=_history(("2026-07-25", 8, 5), ("2026-07-11", 10, 9)))
        assert 'class="spark-wrap"' in paths["html"].read_text(encoding="utf-8")
