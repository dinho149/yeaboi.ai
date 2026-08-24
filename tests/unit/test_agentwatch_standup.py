"""Tests for run_agent_standup (agentwatch/engine.py) and its builders."""

from datetime import date

import pytest

from yeaboi.agentwatch import engine
from yeaboi.agentwatch.store import AgentWatchStore

MONDAY = date(2026, 8, 10)
FRIDAY_SESSION_TS = "2026-08-07T15:00:00+00:00"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sessions.db"


@pytest.fixture(autouse=True)
def no_ingest_no_export(monkeypatch):
    from yeaboi.agentwatch.collector import IngestStats

    monkeypatch.setattr(engine.collector, "refresh", lambda store, **kw: IngestStats())
    import yeaboi.agentwatch.export as export_mod

    monkeypatch.setattr(export_mod, "export_artifact", lambda artifact, *, kind: {})


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    import yeaboi.config

    monkeypatch.setattr(yeaboi.config, "is_llm_configured", lambda: (False, "no API key set"))


@pytest.fixture(autouse=True)
def no_trackers(monkeypatch):
    """Default: the tracker fan-out is stubbed empty (tests opt into items)."""
    import yeaboi.analysis.ai_usage as ai_usage

    monkeypatch.setattr(ai_usage, "collect_ai_activity", lambda *a, **kw: ([], [], ["GitHub: no token"], []))


def _seed_session(db_path, session_id="s1", ended_at=FRIDAY_SESSION_TS, project="/home/dev/webapp"):
    with AgentWatchStore(db_path) as store:
        store.upsert_session(
            session_id,
            source="claude_code",
            source_path=f"/x/{session_id}.jsonl",
            project_path=project,
            git_branch="feature/x",
            cli_version="2.1.0",
            started_at=ended_at,
            ended_at=ended_at,
            turns=4,
            model_usage={"claude-opus-5": {"input": 1_000_000, "output": 1_000_000, "calls": 2}},
            tool_counts={"Bash": 7, "Edit": 3, "Read": 12, "Write": 1},
        )


AGENT_PR = {
    "kind": "pr",
    "title": "add retry logic",
    "body": "Co-authored-by: Claude <noreply@anthropic.com>",
    "author": "dev",
    "author_email": "dev@x.com",
    "branch": "",
    "status": "merged",
    "repository": "org/webapp",
    "source": "github",
    "url": "https://github.com/org/webapp/pull/1",
    "timestamp": "2026-08-07T16:00:00+00:00",
    "key": "#1",
}

HUMAN_COMMIT = {
    "kind": "commit",
    "title": "fix typo",
    "body": "just me",
    "author": "dev",
    "author_email": "dev@x.com",
    "repository": "org/webapp",
    "source": "github",
    "url": "",
    "timestamp": "2026-08-07T16:00:00+00:00",
    "key": "abc1234",
}


class TestWindow:
    def test_monday_reaches_back_to_friday(self, db_path):
        _seed_session(db_path)
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert digest.window_start == "2026-08-07"  # Friday
        assert digest.sessions_worked == 1

    def test_explicit_days(self, db_path):
        _seed_session(db_path, ended_at="2026-08-01T10:00:00+00:00")
        digest = engine.run_agent_standup(days=3, db_path=db_path, today=MONDAY)
        assert digest.sessions_worked == 0  # Aug 1 is outside a 3-day window from Aug 10


class TestSessions:
    def test_session_summary_shape(self, db_path):
        _seed_session(db_path)
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        (summary,) = digest.session_summaries
        assert summary.project == "webapp"
        assert summary.models == ("claude-opus-5",)
        assert summary.cost_usd == pytest.approx(30.0)
        assert summary.top_tools[0] == ("Read", "12")
        assert digest.total_cost_usd == pytest.approx(30.0)
        assert "claude_code" in digest.agents_seen


class TestTrackerActivity:
    def test_agent_items_kept_and_humans_dropped(self, db_path, monkeypatch):
        import yeaboi.analysis.ai_usage as ai_usage

        monkeypatch.setattr(
            ai_usage, "collect_ai_activity", lambda *a, **kw: ([AGENT_PR, HUMAN_COMMIT], ["github"], [], ["org/webapp"])
        )
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert len(digest.repo_activity) == 1
        row = digest.repo_activity[0]
        assert row.kind == "pr"
        assert row.repo == "webapp"
        assert row.agent_marker == "claude"
        assert row.status == "merged"

    def test_local_only_skips_trackers(self, db_path, monkeypatch):
        import yeaboi.analysis.ai_usage as ai_usage

        def _boom(*a, **kw):
            raise AssertionError("tracker_sources=[] must not scan")

        monkeypatch.setattr(ai_usage, "collect_ai_activity", _boom)
        digest = engine.run_agent_standup(tracker_sources=[], db_path=db_path, today=MONDAY)
        assert digest.repo_activity == ()
        assert any("skipped" in note for note in digest.coverage_notes)

    def test_tracker_failure_is_a_note_not_a_crash(self, db_path, monkeypatch):
        import yeaboi.analysis.ai_usage as ai_usage

        def _boom(*a, **kw):
            raise RuntimeError("github down")

        monkeypatch.setattr(ai_usage, "collect_ai_activity", _boom)
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert any("github down" in note for note in digest.coverage_notes)

    def test_coverage_notes_surface(self, db_path):
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert any("no token" in note for note in digest.coverage_notes)

    def test_open_agent_pr_lands_in_flight(self, db_path, monkeypatch):
        import yeaboi.analysis.ai_usage as ai_usage

        open_pr = {**AGENT_PR, "status": "open", "title": "wip: migrate config"}
        monkeypatch.setattr(ai_usage, "collect_ai_activity", lambda *a, **kw: ([open_pr], [], [], []))
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert digest.in_flight == ("wip: migrate config (webapp)",)


class TestProse:
    def test_fallback_narrative_and_highlights(self, db_path, monkeypatch):
        import yeaboi.analysis.ai_usage as ai_usage

        monkeypatch.setattr(ai_usage, "collect_ai_activity", lambda *a, **kw: ([AGENT_PR], [], [], []))
        _seed_session(db_path)
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert "1 local agent session(s)" in digest.narrative
        assert any("Merged: add retry logic" in h for h in digest.highlights)

    def test_llm_prose_used_when_available(self, db_path, monkeypatch):
        _seed_session(db_path)
        monkeypatch.setattr(
            engine,
            "_invoke_llm",
            lambda prompt, *, what: (
                {"narrative": "the agents shipped things", "highlights": ["h1"], "attention_items": ["a1"]},
                [],
            ),
        )
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert digest.narrative == "the agents shipped things"
        assert digest.highlights == ("h1",)
        assert digest.attention_items == ("a1",)

    def test_empty_window_warns(self, db_path):
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert digest.sessions_worked == 0
        assert any("No agent activity" in w for w in digest.warnings)

    def test_no_local_sessions_is_stated_as_a_coverage_note(self, db_path):
        # An environment with no ~/.claude history in the window says so, rather
        # than reporting a quiet day: "the agents were idle" and "this machine
        # can't see them" would otherwise look identical. A scheduled cloud run no
        # longer relies on this note — it passes include_local_sessions=False and
        # gets the distinct one — but a local run against an empty window still
        # lands here, which is the case this pins.
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert any("tracker activity only" in note for note in digest.coverage_notes)


class TestDelivery:
    def test_deliver_failure_becomes_a_warning(self, db_path, monkeypatch):
        _seed_session(db_path)
        monkeypatch.setattr(engine, "_deliver_digest", lambda digest: {"slack": False, "desktop": True})
        digest = engine.run_agent_standup(deliver=True, db_path=db_path, today=MONDAY)
        assert any("Delivery failed: slack" in w for w in digest.warnings)

    def test_no_webhook_reports_false(self, monkeypatch, db_path):
        from yeaboi import config as config_mod

        monkeypatch.setattr(config_mod, "get_slack_webhook_url", lambda: "", raising=False)
        monkeypatch.setattr("yeaboi.standup.delivery.notify_desktop", lambda title, body: False)
        _seed_session(db_path)
        digest = engine.run_agent_standup(deliver=True, db_path=db_path, today=MONDAY)
        assert any("Delivery failed" in w for w in digest.warnings)

    def test_history_recorded(self, db_path):
        _seed_session(db_path)
        engine.run_agent_standup(db_path=db_path, today=MONDAY)
        with AgentWatchStore(db_path) as store:
            rows = store.list_reports("standup")
        assert rows[0]["report"]["sessions_worked"] == 1


class TestBuilders:
    def test_markdown_and_plaintext(self, db_path, monkeypatch):
        import yeaboi.analysis.ai_usage as ai_usage
        from yeaboi.agentwatch.export import build_standup_markdown, build_standup_plaintext

        monkeypatch.setattr(ai_usage, "collect_ai_activity", lambda *a, **kw: ([AGENT_PR], [], [], []))
        _seed_session(db_path)
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        md = build_standup_markdown(digest)
        assert md.startswith("# Agent Standup — 2026-08-10")
        assert "## Local sessions" in md
        assert "add retry logic" in md
        text = build_standup_plaintext(digest)
        assert "*Agent Standup — 2026-08-10*" in text
        assert "$30.00" in text

    def test_rich_render(self, db_path):
        from rich.console import Console

        from yeaboi.agentwatch.render import format_standup_rich

        _seed_session(db_path)
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        console = Console(width=100)
        with console.capture() as cap:
            console.print(format_standup_rich(digest))
        out = cap.get()
        assert "Agent Standup" in out
        assert "webapp" in out


class TestProgressPhases:
    def test_local_only_run_marks_trackers_skipped(self, db_path):
        from yeaboi.analysis.progress import is_component_progress

        events: list = []
        engine.run_agent_standup(
            days=3, tracker_sources=[], db_path=db_path, today=MONDAY, dry_run=True, on_progress=events.append
        )
        assert all(is_component_progress(e) for e in events)
        seq = [(e["component_id"], e["status"]) for e in events]
        assert ("scan", "running") in seq
        assert ("scan", "completed") in seq
        assert ("trackers", "no_data") in seq
        assert ("digest", "no_data") in seq  # dry_run: the LLM step is skipped


class TestProgressScreen:
    def test_checklist_and_refresh_banner(self, db_path):
        from rich.console import Console

        from yeaboi.analysis.progress import append_component_progress
        from yeaboi.ui.mode_select.screens._screens_agents import _build_agent_standup_screen

        events: list = []
        append_component_progress(
            events,
            component_id="scan",
            label="Scanning agent sessions",
            status="running",
            current=1,
            total=4,
            unit="files",
        )

        def render(panel):
            console = Console(width=100, force_terminal=False)
            with console.capture() as cap:
                console.print(panel)
            return cap.get()

        out = render(_build_agent_standup_screen(None, width=100, height=40, shimmer_tick=0.2, progress=events))
        assert "1/4 files" in out
        assert "Scan trackers" in out
        assert "Write the digest" in out

        digest = engine.run_agent_standup(days=3, tracker_sources=[], db_path=db_path, today=MONDAY, dry_run=True)
        out = render(
            _build_agent_standup_screen(
                digest, width=100, height=40, shimmer_tick=0.2, refreshing=True, as_of="2020-01-01T00:00:00+00:00"
            )
        )
        assert "Refreshing…" in out


class TestTrackerOnly:
    """`include_local_sessions=False` — the digest that does not look at this machine.

    Session logs come from the host's own `~/.claude`, so a digest run somewhere
    else does not see *fewer* sessions, it sees a different set. On 2026-08-13 the
    cloud routine reported "1 session · $0.10" — its own session, presented as the
    fleet's day's work, while the same window on the user's machine held 74.
    """

    def test_the_local_half_is_not_scanned_at_all(self, db_path, monkeypatch):
        """Skipped, never scanned-then-discarded: scanning is what found the phantom."""
        _seed_session(db_path)
        called = []
        monkeypatch.setattr(engine, "_deterministic_standup_digest", lambda **kw: called.append(kw))
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY, include_local_sessions=False)
        assert called == [], "the local half ran despite include_local_sessions=False"
        assert digest.sessions_worked == 0
        assert digest.session_summaries == ()
        assert digest.total_cost_usd == 0.0

    def test_a_seeded_session_is_not_reported(self, db_path):
        _seed_session(db_path)
        assert engine.run_agent_standup(db_path=db_path, today=MONDAY).sessions_worked == 1
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY, include_local_sessions=False)
        assert digest.sessions_worked == 0

    def test_its_coverage_note_is_not_the_no_history_one(self, db_path):
        """Two empties that must never read alike: "this machine has nothing" and
        "nobody looked". Confusing them is how an unasked question reads as an idle fleet."""
        not_collected = engine.run_agent_standup(db_path=db_path, today=MONDAY, include_local_sessions=False)
        no_history = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert not_collected.coverage_notes != no_history.coverage_notes
        assert "were not collected" in not_collected.coverage_notes[0]
        assert "not a statement about how much local agent work happened" in not_collected.coverage_notes[0]
        assert "no agent session history" in no_history.coverage_notes[0]

    def test_the_empty_warning_claims_nothing_about_local_work(self, db_path):
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY, include_local_sessions=False)
        assert digest.warnings
        assert not any("nothing worked locally" in w for w in digest.warnings)

    def test_tracker_rows_still_land(self, db_path, monkeypatch):
        import yeaboi.analysis.ai_usage as ai_usage

        monkeypatch.setattr(ai_usage, "collect_ai_activity", lambda *a, **kw: ([AGENT_PR], [], [], []))
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY, include_local_sessions=False)
        assert len(digest.repo_activity) == 1
        assert digest.repo_activity[0].title == "add retry logic"


class TestHighlightsAreAboutWork:
    """A highlight has to be about what an agent did, not what it cost.

    The fallback used to append the top three sessions by cost unconditionally, so
    a one-session day produced "⭐ Highlights (1) — Session on yeaboi.ai
    (claude_code, $0.10)": a highlight only because it was `summaries[0]`.
    """

    def test_a_lone_session_is_not_a_highlight(self, db_path):
        _seed_session(db_path)
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert digest.sessions_worked == 1
        assert digest.highlights == ()

    def test_a_merged_pr_outranks_sessions_entirely(self, db_path, monkeypatch):
        import yeaboi.analysis.ai_usage as ai_usage

        monkeypatch.setattr(ai_usage, "collect_ai_activity", lambda *a, **kw: ([AGENT_PR], [], [], []))
        _seed_session(db_path)
        _seed_session(db_path, session_id="s2", project="/home/dev/other")
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert any(h.startswith("Merged:") for h in digest.highlights)
        assert not any("$" in h and "—" in h for h in digest.highlights), "sessions padded a real highlight list"

    def test_several_sessions_are_described_by_what_they_did(self, db_path):
        _seed_session(db_path)
        _seed_session(db_path, session_id="s2", project="/home/dev/other")
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert digest.highlights
        for line in digest.highlights:
            assert "feature/x" in line, "the branch is the only thing here saying where the work went"
            assert "mostly " in line, "tools are what distinguish a session from a cost"

    def test_the_prompt_is_given_the_branch_and_tools(self, db_path):
        """Without them the model can only rank by cost, which is the same bug."""
        from yeaboi.prompts.agentwatch import get_standup_digest_prompt

        prompt = get_standup_digest_prompt(
            digest_date="2026-08-10",
            window_start="2026-08-07",
            total_cost_usd=30.0,
            sessions=[("webapp", "claude_code", 30.0, 4, ["claude-opus-5"], "feature/x", ["Read", "Bash"])],
            repo_items=[],
        )
        assert "feature/x" in prompt and "mostly Read, Bash" in prompt
