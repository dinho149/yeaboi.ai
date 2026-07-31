"""Tests for the poker exporter (poker/export.py) — the Markdown, and the payload.

The HTML is drawn by ``frontend/src/export`` from a JSON island, so what this
module can assert about it is the *payload*: that every field of the session
reached it, correctly shaped. How a skipped ticket looks, or how the votes lay
out, is asserted in ``Poker.test.tsx``, where the component actually runs.
"""

from tests._pages import island
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
    def test_untrusted_text_travels_as_data_not_markup(self):
        html = build_poker_html(_report())
        # The probe reaches the payload, JSON-escaped — `<` inside a JSON island
        # is written `<` so the string can never close the <script> around it.
        assert "<script>alert(1)</script>" not in html
        ticket = island(html)["report"]["tickets"][0]
        assert ticket["summary"] == "Add <script>alert(1)</script> login"
        assert ticket["aiNote"] == "The 8 voter sees <b>risk</b>"
        assert "<script>simple</script>" in ticket["duel"]["transcript"]

    def test_structure(self):
        html = build_poker_html(_report())
        assert html.startswith("<!DOCTYPE html>")
        boot = island(html)
        assert boot["chrome"]["title"] == "Planning Poker — Sprint 42"
        assert boot["chrome"]["wordmark"] == "poker"
        assert boot["report"]["kind"] == "poker"

    def test_facts_name_the_session(self):
        facts = dict(tuple(f) for f in island(build_poker_html(_report()))["chrome"]["facts"])
        assert facts == {
            "SOURCE": "jira",
            "SCOPE": "Sprint 42",
            "DATE": "2026-07-25",
            "ESTIMATED": "1/2",
        }

    def test_noscript_names_the_markdown_twin(self):
        # The page draws client-side; with scripting off the sibling .md is the
        # whole content, so the note has to name a file that actually exists.
        assert "poker-2026-07-25.md" in build_poker_html(_report())

    def test_duel_payload(self):
        ticket = island(build_poker_html(_report()))["report"]["tickets"][0]
        assert ticket["duel"]["low"] == "Alex (5)"
        assert ticket["duel"]["high"] == "Sam (8)"

    def test_skipped_ticket_carries_no_final(self):
        # A number beside "skipped" is a contradiction; the payload never offers one.
        skipped = island(build_poker_html(_report()))["report"]["tickets"][1]
        assert skipped["estimated"] is False
        assert skipped["final"] is None
        assert skipped["before"] == 3.0

    def test_unsafe_ticket_url_is_dropped(self):
        report = PokerReport(tickets=(PokerTicketResult(key="X-1", url="javascript:alert(1)"),))
        assert "url" not in island(build_poker_html(report))["report"]["tickets"][0]

    def test_votes_without_a_value_are_not_votes(self):
        report = PokerReport(
            tickets=(PokerTicketResult(key="X-1", votes=(PokerVote("Alex", "🦊", ""), PokerVote("Sam", "🐙", "5"))),)
        )
        votes = island(build_poker_html(report))["report"]["tickets"][0]["votes"]
        assert votes == [{"voter": "Sam", "value": "5"}]


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
        assert 'data-mode="poker"' in html  # the accent, set before first paint
        assert 'src="http' not in html and "<link" not in html  # self-contained

    def test_nav_lists_only_the_sections_that_exist(self):
        nav = [tuple(entry) for entry in island(build_poker_html(_report()))["chrome"]["nav"]]
        assert nav == [
            ("overview", "Overview"),
            ("tickets", "Tickets"),
            ("ai", "AI perspectives"),
            ("duels", "Duels"),
        ]

    def test_nav_drops_absent_sections(self):
        report = PokerReport(tickets=(PokerTicketResult(key="X-1"),))
        nav = [tuple(entry) for entry in island(build_poker_html(report))["chrome"]["nav"]]
        assert nav == [("overview", "Overview"), ("tickets", "Tickets")]


class TestTrend:
    def test_trend_from_history(self):
        history = _history(("2026-07-25", 8, 5), ("2026-07-11", 10, 9), ("2026-06-27", 7, 7))
        trend = island(build_poker_html(_report(), history=history))["report"]["trend"]
        assert trend["title"] == "Estimation trend"
        assert trend["points"][0] == ["2026-06-27", 7]  # oldest first

    def test_no_history_no_trend(self):
        # None, not an absent key — "the server decided there is no chart" has
        # to be distinguishable from "the field is missing".
        assert island(build_poker_html(_report()))["report"]["trend"] is None

    def test_self_contained_with_history(self):
        html = build_poker_html(_report(), history=_history(("2026-07-25", 8, 5), ("2026-07-11", 10, 9)))
        assert 'src="http' not in html and "<link" not in html

    def test_export_forwards_history(self, tmp_path, monkeypatch):
        out_dir = tmp_path / "exports"
        monkeypatch.setattr("yeaboi.paths.get_poker_export_dir", lambda key: out_dir / key)
        (out_dir / "proj").mkdir(parents=True)
        paths = export_poker(_report(), history=_history(("2026-07-25", 8, 5), ("2026-07-11", 10, 9)))
        assert island(paths["html"].read_text(encoding="utf-8"))["report"]["trend"] is not None
