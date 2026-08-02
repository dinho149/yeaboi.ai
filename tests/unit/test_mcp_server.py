"""Tests for the MCP server core (yeaboi.mcp.server + runtime + deterministic tools)."""

import json

import anyio
import pytest

from tests._node_helpers import (
    make_completed_questionnaire,
    make_dummy_analysis,
    make_sample_features,
    make_sample_sprints,
    make_sample_stories,
)

pytest.importorskip("mcp", reason="mcp extra not installed")

from mcp.shared.memory import create_connected_server_and_client_session  # noqa: E402

from yeaboi.beta import BETA_LABEL, PERFORMANCE_BETA_NOTICE, PERFORMANCE_BETA_PHRASE  # noqa: E402
from yeaboi.mcp.runtime import LLM_HINT, envelope, error_envelope, to_jsonable  # noqa: E402
from yeaboi.mcp.server import create_app  # noqa: E402

EXPECTED_TOOLS = {
    "artifact_edit_apply",
    "artifact_edit_history",
    "artifact_fields",
    "plan_generate",
    "intake_questions",
    "plan_get",
    "plan_export",
    "plan_publish",
    "plan_sync",
    "sessions_list",
    "session_get",
    "session_delete",
    "usage_get",
    "standup_run",
    "standup_history",
    "standup_members",
    "standup_repositories",
    "standup_review",
    "standup_gaps",
    "standup_config_get",
    "standup_config_set",
    "report_delivery",
    "reporting_history",
    "reporting_export",
    "perf_roster",
    "perf_one_on_one_prep",
    "perf_one_on_one_complete",
    "perf_six_month_review",
    "perf_note_add",
    "retro_history",
    "retro_export",
    "poker_history",
    "poker_export",
    "team_profile_get",
    "team_compare_plan_to_actuals",
    "team_analyze",
    "team_roster",
    "anonymize_text",
}


def call_tool(name: str, arguments: dict | None = None) -> dict:
    """Drive the real FastMCP app through the SDK's in-memory transport."""

    async def _run():
        app = create_app()
        async with create_connected_server_and_client_session(app._mcp_server) as client:
            result = await client.call_tool(name, arguments or {})
            return json.loads(result.content[0].text)

    return anyio.run(_run)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Redirect every store to a per-test sessions DB."""
    db = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
    return db


@pytest.fixture
def seeded_session(tmp_db):
    """A complete planning session saved through the real SessionStore."""
    from yeaboi.sessions import SessionStore

    state = {
        "questionnaire": make_completed_questionnaire(),
        "project_analysis": make_dummy_analysis(),
        "features": make_sample_features(),
        "stories": make_sample_stories(),
        "sprints": make_sample_sprints(),
    }
    with SessionStore(tmp_db) as store:
        store.create_session("new-abcd1234-2026-07-20")
        store.save_state("new-abcd1234-2026-07-20", state)
        store.update_project_name("new-abcd1234-2026-07-20", "Test Project")
    return "new-abcd1234-2026-07-20"


class TestEnvelope:
    def test_success_shape(self):
        result = envelope({"a": 1}, llm_mode="provider", warnings=["w"])
        assert result == {"ok": True, "llm_mode": "provider", "warnings": ["w"], "data": {"a": 1}}

    def test_error_shape(self):
        result = error_envelope(ValueError("nope"))
        assert result["ok"] is False
        assert result["error"] == {"type": "ValueError", "message": "nope"}
        assert "hint" not in result

    def test_auth_error_gets_hint(self):
        result = error_envelope(RuntimeError("Invalid API key provided"))
        assert result["hint"] == LLM_HINT

    def test_to_jsonable_flattens_dataclasses_and_tuples(self):
        analysis = make_dummy_analysis()
        data = to_jsonable(analysis)
        assert data["project_name"] == "Test Project"
        assert isinstance(data["goals"], list)
        json.dumps(data)  # fully serializable


class TestToolInventory:
    def test_all_tools_registered(self):
        async def _run():
            app = create_app()
            async with create_connected_server_and_client_session(app._mcp_server) as client:
                listed = await client.list_tools()
                return {tool.name for tool in listed.tools}

        assert anyio.run(_run) == EXPECTED_TOOLS

    def test_stdout_stays_clean(self, capsys, tmp_db):
        # stdio transport rule: stdout carries JSON-RPC, so tool calls must
        # never print to it (stderr is fine).
        call_tool("sessions_list")
        assert capsys.readouterr().out == ""

    def test_perf_tools_are_marked_beta(self):
        # The prefixes are hand-written literals (FastMCP reads __doc__ at
        # decoration time, so they can't be f-strings) — this is what keeps
        # them in sync with the constant.
        async def _run():
            app = create_app()
            async with create_connected_server_and_client_session(app._mcp_server) as client:
                listed = await client.list_tools()
                return {tool.name: tool.description or "" for tool in listed.tools}

        descriptions = anyio.run(_run)
        for name, description in descriptions.items():
            if name.startswith("perf_"):
                assert BETA_LABEL in description, name
                assert PERFORMANCE_BETA_PHRASE in description, name

    def test_non_perf_tools_are_not_marked_beta(self):
        async def _run():
            app = create_app()
            async with create_connected_server_and_client_session(app._mcp_server) as client:
                listed = await client.list_tools()
                return {tool.name: tool.description or "" for tool in listed.tools}

        descriptions = anyio.run(_run)
        assert BETA_LABEL not in descriptions["report_delivery"]


class TestSessionTools:
    def test_sessions_list_empty(self, tmp_db):
        payload = call_tool("sessions_list")
        assert payload["ok"] is True
        assert payload["data"] == []

    def test_sessions_list_seeded(self, seeded_session):
        payload = call_tool("sessions_list")
        assert payload["ok"] is True
        assert payload["data"][0]["session_id"] == seeded_session
        assert payload["data"][0]["project_name"] == "Test Project"
        assert "session_state_raw" not in payload["data"][0]

    def test_session_get_defaults_to_latest(self, seeded_session):
        payload = call_tool("session_get")
        assert payload["ok"] is True
        data = payload["data"]
        assert data["session_id"] == seeded_session
        assert data["artifacts"]["stories"] == len(make_sample_stories())
        assert data["artifacts"]["sprints"] == len(make_sample_sprints())
        assert data["questionnaire_completed"] is True

    def test_session_get_unknown_id_errors(self, tmp_db):
        payload = call_tool("session_get", {"session_id": "new-ffffffff-2026-01-01"})
        assert payload["ok"] is False
        assert payload["error"]["type"] == "ValueError"


class TestPlanningTools:
    def test_intake_questions_contract(self, tmp_db):
        payload = call_tool("intake_questions")
        assert payload["ok"] is True
        data = payload["data"]
        assert len(data["questions"]) == 30
        assert 6 in data["smart_essentials"]
        assert data["defaults"]  # non-empty
        assert data["choice_metadata"]["10"]["options"]  # Q10 is a choice question

    def test_plan_get_seeded(self, seeded_session):
        payload = call_tool("plan_get")
        assert payload["ok"] is True
        plan = payload["data"]
        assert plan["session_id"] == seeded_session
        assert plan["stories"]
        assert plan["sprints"]

    def test_plan_get_no_sessions_errors(self, tmp_db):
        payload = call_tool("plan_get")
        assert payload["ok"] is False
        assert "No saved sessions" in payload["error"]["message"]

    def test_plan_export_markdown(self, seeded_session, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)  # exporter writes relative to CWD
        payload = call_tool("plan_export", {"format": "markdown"})
        assert payload["ok"] is True
        from pathlib import Path

        assert Path(payload["data"]["path"]).exists()

    def test_plan_export_bad_format(self, seeded_session):
        payload = call_tool("plan_export", {"format": "pdf"})
        assert payload["ok"] is False
        assert "Unsupported format" in payload["error"]["message"]


class TestHistoryTools:
    def test_standup_history_empty(self, seeded_session):
        payload = call_tool("standup_history")
        assert payload["ok"] is True
        assert payload["data"]["history"] == []
        assert payload["data"]["latest_report"] is None

    def test_retro_history_empty(self, seeded_session):
        payload = call_tool("retro_history")
        assert payload["ok"] is True
        assert payload["data"]["history"] == []

    def test_poker_history_empty(self, seeded_session):
        payload = call_tool("poker_history")
        assert payload["ok"] is True
        assert payload["data"]["history"] == []
        assert payload["data"]["latest_report"] is None

    def test_poker_history_after_run(self, seeded_session, tmp_db):
        from yeaboi.agent.state import PokerReport, PokerTicketResult
        from yeaboi.poker.store import PokerStore

        report = PokerReport(
            date="2026-07-25",
            session_id=seeded_session,
            scope_label="Sprint 42",
            tickets=(PokerTicketResult(key="PROJ-1", summary="S", final_points=5.0, estimated=True),),
        )
        with PokerStore(tmp_db) as store:
            store.record_run(report)
        payload = call_tool("poker_history", {"session_id": seeded_session})
        assert payload["ok"] is True
        assert len(payload["data"]["history"]) == 1
        assert payload["data"]["latest_report"]["scope_label"] == "Sprint 42"

    def test_reporting_history_empty(self, seeded_session):
        payload = call_tool("reporting_history")
        assert payload["ok"] is True
        assert payload["data"]["history"] == []
        assert payload["data"]["latest_report"] is None

    def test_reporting_history_after_run(self, seeded_session, tmp_db):
        from yeaboi.agent.state import DeliveryReport
        from yeaboi.reporting.store import ReportingStore

        with ReportingStore(tmp_db) as store:
            store.record_run(DeliveryReport(period_label="Last month", headline="Shipped."), session_id=seeded_session)
        payload = call_tool("reporting_history", {"session_id": seeded_session})
        assert payload["ok"] is True
        assert len(payload["data"]["history"]) == 1
        assert payload["data"]["latest_report"]["headline"] == "Shipped."

    def test_reporting_export_no_report(self, seeded_session):
        payload = call_tool("reporting_export")
        # No report recorded → the tool raises, surfaced as ok=False in the envelope.
        assert payload["ok"] is False

    def test_reporting_export_style_merges_over_saved_prefs(self, seeded_session, tmp_db, monkeypatch, tmp_path):
        from yeaboi.agent.state import DeliveryReport
        from yeaboi.reporting.store import ReportingStore
        from yeaboi.reporting.style import DeckStyle

        with ReportingStore(tmp_db) as store:
            store.record_run(DeliveryReport(period_label="Last month"), session_id=seeded_session)
        # Saved prefs say classic font; the per-call dict overrides only the layout.
        monkeypatch.setattr("yeaboi.reporting.style.load_deck_style", lambda: DeckStyle(font_family="classic"))
        seen = {}

        def _capture(report, **kw):
            seen.update(kw)
            p = tmp_path / "out.md"
            p.write_text("x")
            return {"markdown": p, "html": p, "slides": p}

        monkeypatch.setattr("yeaboi.reporting.export.export_report", _capture)
        payload = call_tool("reporting_export", {"style": {"layout": "compact", "footer_text": "ACME"}})
        assert payload["ok"] is True
        assert seen["style"] == DeckStyle(font_family="classic", layout="compact", footer_text="ACME")

    def test_reporting_export_tolerates_garbage_style(self, seeded_session, tmp_db, monkeypatch, tmp_path):
        from yeaboi.agent.state import DeliveryReport
        from yeaboi.reporting.store import ReportingStore
        from yeaboi.reporting.style import DEFAULT_STYLE, DeckStyle

        with ReportingStore(tmp_db) as store:
            store.record_run(DeliveryReport(period_label="Last month"), session_id=seeded_session)
        monkeypatch.setattr("yeaboi.reporting.style.load_deck_style", lambda: DeckStyle())
        seen = {}

        def _capture(report, **kw):
            seen.update(kw)
            p = tmp_path / "out.md"
            p.write_text("x")
            return {"markdown": p, "html": p, "slides": p}

        monkeypatch.setattr("yeaboi.reporting.export.export_report", _capture)
        payload = call_tool("reporting_export", {"style": {"layout": "diagonal", "max_bullets": "lots"}})
        assert payload["ok"] is True  # tolerant validation, never a crash
        assert seen["style"] == DEFAULT_STYLE

    def test_team_profile_get_no_db(self, tmp_db):
        payload = call_tool("team_profile_get")
        assert payload["ok"] is True
        assert payload["data"]["profiles"] == []


@pytest.fixture
def provider_mode(monkeypatch):
    """Pin the LLM mode to 'provider' so engine-tool tests are deterministic."""
    monkeypatch.setenv("YEABOI_MCP_LLM", "provider")
    monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, "ok"))


class TestEngineTools:
    """The LLM-backed tools with their engines monkeypatched (no real LLM/tracker calls)."""

    def test_plan_generate(self, seeded_session, provider_mode, monkeypatch):
        from yeaboi.sessions import SessionStore

        def fake_pipeline(questionnaire, *, on_progress=None, **kwargs):
            assert questionnaire.answers[1] == "A todo app"
            assert questionnaire.answers[6] == "4"
            assert questionnaire.answers[11] == "Python"  # explicit answer won
            if on_progress:
                on_progress("project_analyzer", 0)
            from yeaboi.paths import get_db_path

            with SessionStore(get_db_path()) as store:
                state = store.load_state("new-abcd1234-2026-07-20")
            state["_session_id"] = "new-abcd1234-2026-07-20"
            return state

        monkeypatch.setattr("yeaboi.agent.headless.run_planning_pipeline", fake_pipeline)
        payload = call_tool(
            "plan_generate",
            {"description": "A todo app", "team_size": 4, "answers": {"11": "Python"}},
        )
        assert payload["ok"] is True
        assert payload["llm_mode"] == "provider"
        assert payload["data"]["session_id"] == "new-abcd1234-2026-07-20"
        assert payload["data"]["stories"]

    def test_plan_generate_requires_description(self, tmp_db, provider_mode):
        payload = call_tool("plan_generate", {"description": "   "})
        assert payload["ok"] is False
        assert "description is required" in payload["error"]["message"]

    def test_plan_generate_rejects_bad_answer_keys(self, tmp_db, provider_mode):
        payload = call_tool("plan_generate", {"description": "An app", "answers": {"55": "x"}})
        assert payload["ok"] is False
        assert "question numbers 1-30" in payload["error"]["message"]

    def test_standup_run_defaults_no_delivery(self, seeded_session, provider_mode, monkeypatch):
        captured: dict = {}

        def fake_run_standup(session_id, *, deliver, days=None, **kwargs):
            captured.update(session_id=session_id, deliver=deliver, days=days)
            return {"team_summary": "all good", "warnings": ["Jira skipped"]}

        monkeypatch.setattr("yeaboi.standup.engine.run_standup", fake_run_standup)
        payload = call_tool("standup_run")
        assert payload["ok"] is True
        assert captured == {"session_id": seeded_session, "deliver": False, "days": None}
        assert payload["warnings"] == ["Jira skipped"]

    def test_standup_review_passes_transcript_text_through(self, seeded_session, provider_mode, monkeypatch):
        """An agent that already HAS the transcript shouldn't have to ask the
        user to save it to a file first."""
        from yeaboi.agent.state import TranscriptReview

        captured: dict = {}

        def fake_review(session_id, **kwargs):
            captured.update(session_id=session_id, **kwargs)
            return TranscriptReview(standup_date="2026-07-30")

        monkeypatch.setattr("yeaboi.standup.engine.run_transcript_review", fake_review)
        payload = call_tool("standup_review", {"transcript_text": "Alice: shipped auth"})
        assert payload["ok"] is True
        assert captured["transcript_text"] == "Alice: shipped auth"
        assert payload["data"]["standup_date"] == "2026-07-30"

    def test_standup_review_defaults_to_no_text(self, seeded_session, provider_mode, monkeypatch):
        from yeaboi.agent.state import TranscriptReview

        captured: dict = {}
        monkeypatch.setattr(
            "yeaboi.standup.engine.run_transcript_review",
            lambda sid, **kw: captured.update(kw) or TranscriptReview(),
        )
        call_tool("standup_review")
        assert captured["transcript_text"] == ""

    def test_report_delivery_validates_period(self, tmp_db, provider_mode):
        payload = call_tool("report_delivery", {"period": "fortnight"})
        assert payload["ok"] is False
        assert "period must be one of" in payload["error"]["message"]

    def test_report_delivery(self, tmp_db, provider_mode, monkeypatch):
        def fake_report(period, **kwargs):
            assert period == "last_sprint"
            return {"executive_summary": "shipped", "warnings": []}

        monkeypatch.setattr("yeaboi.reporting.engine.run_delivery_report", fake_report)
        payload = call_tool("report_delivery", {"period": "last_sprint"})
        assert payload["ok"] is True
        assert payload["data"]["executive_summary"] == "shipped"

    def test_perf_roster(self, tmp_db, monkeypatch):
        monkeypatch.setattr("yeaboi.performance.roster.fetch_roster", lambda **kw: [{"name": "Sam"}])
        payload = call_tool("perf_roster")
        assert payload["ok"] is True
        assert payload["data"]["engineers"] == [{"name": "Sam"}]

    def test_perf_one_on_one_prep(self, tmp_db, provider_mode, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.performance.engine.run_one_on_one_prep",
            lambda engineer, **kw: {"engineer": engineer, "talking_points": ["velocity"], "warnings": []},
        )
        payload = call_tool("perf_one_on_one_prep", {"engineer": "Sam"})
        assert payload["ok"] is True
        assert payload["data"]["engineer"] == "Sam"

    def test_perf_one_on_one_complete_requires_transcript(self, tmp_db, provider_mode):
        payload = call_tool("perf_one_on_one_complete", {"engineer": "Sam", "transcript": " "})
        assert payload["ok"] is False
        assert "transcript is required" in payload["error"]["message"]

    def test_perf_one_on_one_complete_defaults_no_delivery(self, tmp_db, provider_mode, monkeypatch):
        captured: dict = {}

        def fake_complete(engineer, transcript, *, deliver, recipients=None, **kwargs):
            captured.update(engineer=engineer, deliver=deliver)
            return {"summary": "done", "warnings": []}

        monkeypatch.setattr("yeaboi.performance.engine.complete_one_on_one", fake_complete)
        payload = call_tool("perf_one_on_one_complete", {"engineer": "Sam", "transcript": "we talked"})
        assert payload["ok"] is True
        assert captured == {"engineer": "Sam", "deliver": False}

    def test_perf_six_month_review(self, tmp_db, provider_mode, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.performance.engine.run_six_month_review",
            lambda engineer, *, period_months, **kw: {"engineer": engineer, "months": period_months, "warnings": []},
        )
        payload = call_tool("perf_six_month_review", {"engineer": "Sam", "period_months": 12})
        assert payload["ok"] is True
        assert payload["data"]["months"] == 12

    def test_perf_envelope_carries_the_beta_warning(self, tmp_db, monkeypatch):
        # `warnings` is the only envelope field the server instructions tell the
        # client to surface to the user; the description reaches only the model.
        monkeypatch.setattr("yeaboi.performance.roster.fetch_roster", lambda **kw: [{"name": "Sam"}])
        payload = call_tool("perf_roster")
        assert payload["warnings"][0] == PERFORMANCE_BETA_NOTICE

    def test_beta_warning_preserves_engine_warnings(self, tmp_db, provider_mode, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.performance.engine.run_one_on_one_prep",
            lambda engineer, **kw: {"engineer": engineer, "warnings": ["Jira returned 401"]},
        )
        payload = call_tool("perf_one_on_one_prep", {"engineer": "Sam"})
        assert payload["warnings"][0] == PERFORMANCE_BETA_NOTICE
        assert "Jira returned 401" in payload["warnings"]

    def test_error_envelope_has_no_beta_warning(self, tmp_db, provider_mode):
        payload = call_tool("perf_one_on_one_complete", {"engineer": "Sam", "transcript": " "})
        assert payload["ok"] is False
        assert "warnings" not in payload

    def test_non_perf_envelope_is_unaffected(self, tmp_db, provider_mode, monkeypatch):
        # Proves the wrapper stayed local to tools_performance and didn't leak
        # into the shared runtime every other tool module uses.
        monkeypatch.setattr(
            "yeaboi.reporting.engine.run_delivery_report",
            lambda *a, **kw: {"executive_summary": "shipped", "warnings": []},
        )
        payload = call_tool("report_delivery", {"period": "last_sprint"})
        assert payload["warnings"] == []

    def test_standup_run_channels_passthrough(self, seeded_session, provider_mode, monkeypatch):
        captured: dict = {}

        def fake_run_standup(session_id, *, deliver, days=None, channels=None, **kwargs):
            captured.update(channels=channels)
            return {"team_summary": "ok", "warnings": []}

        monkeypatch.setattr("yeaboi.standup.engine.run_standup", fake_run_standup)
        payload = call_tool("standup_run", {"channels": ["slack", "email"]})
        assert payload["ok"] is True
        assert captured["channels"] == ["slack", "email"]

    def test_standup_run_documentation_sources_passthrough(self, seeded_session, provider_mode, monkeypatch):
        captured: dict = {}

        def fake_run_standup(session_id, *, deliver, days=None, documentation_sources=None, **kwargs):
            captured["documentation_sources"] = documentation_sources
            return {"team_summary": "ok", "warnings": []}

        monkeypatch.setattr("yeaboi.standup.engine.run_standup", fake_run_standup)
        payload = call_tool("standup_run", {"documentation_sources": ["confluence", "notion"]})
        assert payload["ok"] is True
        assert captured["documentation_sources"] == ["confluence", "notion"]

    def test_standup_run_rejects_bad_channel(self, seeded_session, provider_mode):
        payload = call_tool("standup_run", {"channels": ["pager"]})
        assert payload["ok"] is False
        assert "unknown delivery channel" in payload["error"]["message"]

    def test_report_delivery_window_passthrough(self, tmp_db, provider_mode, monkeypatch):
        captured: dict = {}

        def fake_report(period, **kwargs):
            captured.update(kwargs)
            return {"executive_summary": "q3", "warnings": []}

        monkeypatch.setattr("yeaboi.reporting.engine.run_delivery_report", fake_report)
        payload = call_tool(
            "report_delivery",
            {
                "period": "quarter",
                "window_start": "2026-04-01",
                "window_end": "2026-06-30",
                "sprint_names": ["Sprint 7", "Sprint 8"],
                "period_label_override": "Q2 2026",
            },
        )
        assert payload["ok"] is True
        assert captured["window_start"] == "2026-04-01"
        assert captured["window_end"] == "2026-06-30"
        assert captured["sprint_names"] == ("Sprint 7", "Sprint 8")
        assert captured["period_label_override"] == "Q2 2026"

    def test_perf_one_on_one_complete_images_passthrough(self, tmp_db, provider_mode, monkeypatch):
        captured: dict = {}

        def fake_complete(engineer, transcript, *, images=(), **kwargs):
            captured.update(images=images)
            return {"summary": "done", "warnings": []}

        monkeypatch.setattr("yeaboi.performance.engine.complete_one_on_one", fake_complete)
        payload = call_tool(
            "perf_one_on_one_complete",
            {"engineer": "Sam", "transcript": "notes", "images": ["/tmp/board.png"]},
        )
        assert payload["ok"] is True
        assert captured["images"] == ("/tmp/board.png",)

    def test_team_analyze(self, tmp_db, provider_mode, monkeypatch):
        captured: dict = {}

        def fake_analysis(**kwargs):
            captured.update(kwargs)
            return {"source": "jira", "profile": {"velocity_avg": 30.0}, "warnings": ["log skipped"]}

        # Patch the package re-export — tools_team imports from yeaboi.analysis.
        monkeypatch.setattr("yeaboi.analysis.run_team_analysis", fake_analysis)
        payload = call_tool("team_analyze", {"sprint_count": 4, "generate_samples": True})
        assert payload["ok"] is True
        assert payload["data"]["source"] == "jira"
        assert payload["warnings"] == ["log skipped"]
        assert captured["sprint_count"] == 4
        assert captured["generate_samples"] is True
        assert captured["analysis_depth"] == "deep"
        assert captured["analysis_window_days"] == 120

    def test_team_analyze_deep_passthrough(self, tmp_db, provider_mode, monkeypatch):
        captured: dict = {}

        monkeypatch.setattr(
            "yeaboi.analysis.run_team_analysis",
            lambda **kwargs: captured.update(kwargs) or {"warnings": []},
        )
        payload = call_tool(
            "team_analyze",
            {"analysis_depth": "deep", "analysis_features": ["code_health"]},
        )
        assert payload["ok"] is True
        assert captured["analysis_depth"] == "deep"
        assert captured["analysis_features"] == ["code_health"]

    def test_anonymize_text(self, tmp_db, provider_mode, monkeypatch):
        captured: dict = {}

        def fake_anonymize(text, **kwargs):
            captured["text"] = text
            captured.update(kwargs)
            from yeaboi.agent.state import AnonymizedOutput

            return AnonymizedOutput(
                anonymized_text="[COMPANY] shipped the feature",
                replacements=(("Acme", "[COMPANY]"),),
                source_mode="reporting",
                warnings=(),
            )

        monkeypatch.setattr("yeaboi.anonymize.engine.run_anonymize", fake_anonymize)
        payload = call_tool(
            "anonymize_text",
            {"text": "Acme shipped the feature", "instruction": "mask everything", "source_mode": "reporting"},
        )
        assert payload["ok"] is True
        assert "Acme" not in payload["data"]["anonymized_text"]
        assert payload["data"]["replacements"] == [["Acme", "[COMPANY]"]]
        assert captured["text"] == "Acme shipped the feature"
        assert captured["instruction"] == "mask everything"

    def test_team_compare_plan_to_actuals(self, tmp_db, monkeypatch):
        from types import SimpleNamespace

        from yeaboi.tools import team_learning

        monkeypatch.setattr(
            team_learning,
            "compare_plan_to_actuals",
            SimpleNamespace(invoke=lambda args: '{"accuracy_pct": 82}'),
        )
        payload = call_tool("team_compare_plan_to_actuals")
        assert payload["ok"] is True
        assert payload["data"]["accuracy_pct"] == 82


class TestPlanSync:
    def test_sync_to_jira(self, seeded_session, monkeypatch):
        from types import SimpleNamespace

        captured: dict = {}

        def fake_sync(state, on_progress=None):
            captured["stories_in_state"] = len(state.get("stories") or [])
            if on_progress:
                on_progress(1, 3, "Creating epic")
            result = SimpleNamespace(
                epic_key="PROJ-1",
                stories_created={"s1": "PROJ-2"},
                tasks_created={},
                sprints_created={"sp1": "17"},
                errors=["Sprint 2 board missing"],
                skipped=1,
            )
            return result, dict(state, jira_epic_key="PROJ-1")

        monkeypatch.setattr("yeaboi.jira_sync.sync_all_to_jira", fake_sync)
        payload = call_tool("plan_sync", {"destination": "jira"})
        assert payload["ok"] is True
        assert payload["data"]["epic"] == "PROJ-1"
        assert payload["data"]["stories_created"] == {"s1": "PROJ-2"}
        assert payload["data"]["skipped_existing"] == 1
        assert payload["warnings"] == ["Sprint 2 board missing"]
        assert captured["stories_in_state"] > 0

        # The updated state (created keys) must persist so a re-sync skips them.
        from yeaboi.paths import get_db_path
        from yeaboi.sessions import SessionStore

        with SessionStore(get_db_path()) as store:
            assert store.load_state(seeded_session)["jira_epic_key"] == "PROJ-1"

    def test_sync_bad_destination(self, seeded_session):
        payload = call_tool("plan_sync", {"destination": "linear"})
        assert payload["ok"] is False
        assert "jira" in payload["error"]["message"]

    def test_sync_no_sessions_errors(self, tmp_db):
        payload = call_tool("plan_sync", {"destination": "jira"})
        assert payload["ok"] is False


class TestRetroExport:
    def test_no_retro_recorded_errors(self, seeded_session):
        payload = call_tool("retro_export")
        assert payload["ok"] is False
        assert "No retro recorded" in payload["error"]["message"]

    def test_exports_latest_report(self, seeded_session, monkeypatch, tmp_path):
        from yeaboi.agent.state import RetroReport
        from yeaboi.paths import get_db_path
        from yeaboi.retro.store import RetroStore

        with RetroStore(get_db_path()) as store:
            store.record_run(RetroReport(date="2026-07-18", session_id=seeded_session, project_name="Test Project"))
        monkeypatch.setattr("yeaboi.paths.get_retro_export_dir", lambda key: tmp_path)
        payload = call_tool("retro_export")
        assert payload["ok"] is True
        assert payload["data"]["retro_date"] == "2026-07-18"
        from pathlib import Path

        assert Path(payload["data"]["markdown"]).exists()
        assert Path(payload["data"]["html"]).exists()


class TestSessionDelete:
    def test_deletes_by_exact_id(self, seeded_session):
        payload = call_tool("session_delete", {"session_id": seeded_session})
        assert payload["ok"] is True
        assert payload["data"]["deleted"] is True
        assert call_tool("sessions_list")["data"] == []

    def test_blank_id_refused(self, seeded_session):
        payload = call_tool("session_delete", {"session_id": "  "})
        assert payload["ok"] is False
        assert "never defaults" in payload["error"]["message"]

    def test_unknown_id_errors(self, tmp_db):
        payload = call_tool("session_delete", {"session_id": "new-ffffffff-2026-01-01"})
        assert payload["ok"] is False
        assert "not found" in payload["error"]["message"].lower()


class TestUsageGet:
    def test_no_db_returns_zeros(self, tmp_db):
        payload = call_tool("usage_get")
        assert payload["ok"] is True
        assert payload["data"]["total_tokens"] == 0
        assert "host agent" in payload["data"]["note"]

    def test_reads_recorded_usage(self, seeded_session, tmp_db):
        from yeaboi.sessions import SessionStore

        with SessionStore(tmp_db) as store:
            store.record_token_usage(100, 50, model="model-x", provider="anthropic")
        payload = call_tool("usage_get")
        assert payload["ok"] is True
        assert payload["data"]["input_tokens"] == 100
        assert payload["data"]["output_tokens"] == 50
        assert payload["data"]["call_count"] == 1


class TestInputValidation:
    """Friendly fail-fast errors instead of deep engine failures (audit hardening)."""

    def test_report_delivery_rejects_bad_window_date(self, tmp_db, provider_mode):
        payload = call_tool("report_delivery", {"period": "quarter", "window_start": "July 1st"})
        assert payload["ok"] is False
        assert "YYYY-MM-DD" in payload["error"]["message"]

    def test_report_delivery_rejects_inverted_window(self, tmp_db, provider_mode):
        payload = call_tool(
            "report_delivery", {"period": "quarter", "window_start": "2026-06-30", "window_end": "2026-04-01"}
        )
        assert payload["ok"] is False
        assert "before window_start" in payload["error"]["message"]

    def test_team_analyze_rejects_bad_source(self, tmp_db, provider_mode):
        payload = call_tool("team_analyze", {"source": "linear"})
        assert payload["ok"] is False
        assert "jira" in payload["error"]["message"]

    def test_team_analyze_both_source(self, tmp_db, provider_mode, monkeypatch):
        def fake_analysis(**kwargs):
            return {
                "source": "both",
                "results": {
                    "jira": {"profile": {"velocity_avg": 23.0}},
                    "azdevops": {"profile": {"velocity_avg": 15.0}},
                },
                "comparison": [["Avg velocity", "23", "15"]],
                "warnings": [],
            }

        monkeypatch.setattr("yeaboi.analysis.run_team_analysis", fake_analysis)
        payload = call_tool("team_analyze", {"source": "both"})
        assert payload["ok"] is True
        assert payload["data"]["source"] == "both"
        assert set(payload["data"]["results"]) == {"jira", "azdevops"}
        assert payload["data"]["comparison"] == [["Avg velocity", "23", "15"]]

    def test_team_analyze_components_and_members(self, tmp_db, provider_mode, monkeypatch):
        captured: dict = {}

        def fake_analysis(**kwargs):
            captured.update(kwargs)
            return {"delivery": {}, "code": None, "docs": None, "warnings": []}

        monkeypatch.setattr("yeaboi.analysis.run_team_analysis", fake_analysis)
        payload = call_tool(
            "team_analyze",
            {
                "components": {"delivery": ["jira"], "code": ["github"], "docs": ["notion"]},
                "members": {"jira": ["Alice"]},
            },
        )
        assert payload["ok"] is True
        assert captured["components"] == {"delivery": ["jira"], "code": ["github"], "docs": ["notion"]}
        assert captured["members"] == {"jira": ["Alice"]}

    def test_team_analyze_rejects_bad_component_key(self, tmp_db, provider_mode):
        payload = call_tool("team_analyze", {"components": {"velocity": ["jira"]}})
        assert payload["ok"] is False
        assert "delivery" in payload["error"]["message"]

    def test_team_analyze_rejects_bad_sub_source(self, tmp_db, provider_mode):
        # 'jira' is a delivery tracker, not a code host → rejected under code.
        payload = call_tool("team_analyze", {"components": {"code": ["jira"]}})
        assert payload["ok"] is False
        assert "sub-sources" in payload["error"]["message"]

    def test_team_roster(self, tmp_db, provider_mode, monkeypatch):
        monkeypatch.setattr("yeaboi.analysis.get_team_roster", lambda source, project_key: ["Alice", "Bob"])
        payload = call_tool("team_roster", {"source": "jira", "project_key": "P"})
        assert payload["ok"] is True
        assert payload["data"]["members"] == ["Alice", "Bob"]

    def test_perf_prep_rejects_unknown_engineer(self, tmp_db, provider_mode, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr("yeaboi.performance.roster.fetch_roster", lambda **kw: [SimpleNamespace(name="Sam Chen")])
        payload = call_tool("perf_one_on_one_prep", {"engineer": "Zed"})
        assert payload["ok"] is False
        assert "Sam Chen" in payload["error"]["message"]
        assert "perf_roster" in payload["error"]["message"]

    def test_perf_prep_roster_unavailable_proceeds(self, tmp_db, provider_mode, monkeypatch):
        from yeaboi.agent.state import OneOnOnePrep

        def broken_roster(**kw):
            raise RuntimeError("tracker down")

        monkeypatch.setattr("yeaboi.performance.roster.fetch_roster", broken_roster)
        monkeypatch.setattr(
            "yeaboi.performance.engine.run_one_on_one_prep", lambda engineer, **kw: OneOnOnePrep(engineer=engineer)
        )
        payload = call_tool("perf_one_on_one_prep", {"engineer": "Zed"})
        assert payload["ok"] is True  # best-effort: an unreachable tracker must not block the workflow

    def test_perf_prep_matches_engineer_case_insensitively(self, tmp_db, provider_mode, monkeypatch):
        from types import SimpleNamespace

        from yeaboi.agent.state import OneOnOnePrep

        monkeypatch.setattr("yeaboi.performance.roster.fetch_roster", lambda **kw: [SimpleNamespace(name="Sam Chen")])
        monkeypatch.setattr(
            "yeaboi.performance.engine.run_one_on_one_prep", lambda engineer, **kw: OneOnOnePrep(engineer=engineer)
        )
        payload = call_tool("perf_one_on_one_prep", {"engineer": "sam chen"})
        assert payload["ok"] is True


class TestPlanPublish:
    def test_publish_success(self, seeded_session, monkeypatch):
        from yeaboi.export_targets import PublishResult

        captured: dict = {}

        def fake_publish(destination, *, title, markdown):
            captured.update(destination=destination, title=title)
            assert markdown  # a real markdown document was built
            return PublishResult(ok=True, message="Published", url="https://notion.so/x")

        monkeypatch.setattr("yeaboi.export_targets.publish_markdown", fake_publish)
        payload = call_tool("plan_publish", {"destination": "notion"})
        assert payload["ok"] is True
        assert payload["data"]["url"] == "https://notion.so/x"
        assert captured["destination"] == "notion"
        assert captured["title"].startswith("Sprint Plan")

    def test_publish_failure_surfaces_message(self, seeded_session, monkeypatch):
        from yeaboi.export_targets import PublishResult

        monkeypatch.setattr(
            "yeaboi.export_targets.publish_markdown",
            lambda destination, *, title, markdown: PublishResult(ok=False, message="Notion not configured"),
        )
        payload = call_tool("plan_publish", {"destination": "notion"})
        assert payload["ok"] is False
        assert "Notion not configured" in payload["error"]["message"]

    def test_publish_bad_destination(self, seeded_session):
        payload = call_tool("plan_publish", {"destination": "sharepoint"})
        assert payload["ok"] is False
        assert "Unsupported destination" in payload["error"]["message"]


class TestPerfNotes:
    def test_note_add_and_visible_to_store(self, tmp_db):
        payload = call_tool("perf_note_add", {"engineer": "Sam", "note_text": "great incident response"})
        assert payload["ok"] is True
        assert payload["data"]["note_id"] > 0

        from yeaboi.performance.store import PerformanceStore

        with PerformanceStore(tmp_db) as store:
            notes = store.get_notes("Sam")
        assert notes[0]["note_text"] == "great incident response"

    def test_note_add_requires_text(self, tmp_db):
        payload = call_tool("perf_note_add", {"engineer": "Sam", "note_text": "  "})
        assert payload["ok"] is False
        assert "note_text is required" in payload["error"]["message"]


class TestStandupConfigTools:
    def test_config_get_unset(self, seeded_session):
        payload = call_tool("standup_config_get")
        assert payload["ok"] is True
        assert payload["data"]["config"] is None
        assert "slack" in payload["data"]["valid_channels"]

    def test_config_set_creates_with_defaults(self, seeded_session):
        payload = call_tool("standup_config_set", {"time": "09:15", "delivery_channels": ["slack"]})
        assert payload["ok"] is True
        config = payload["data"]["config"]
        assert config["time"] == "09:15"
        assert config["delivery_channels"] == ["slack"]
        assert config["weekdays"] == "1-5"  # default kept
        assert config["enabled"] is False  # not enabled unless asked

    def test_config_set_merges_over_existing(self, seeded_session):
        call_tool("standup_config_set", {"time": "09:15", "delivery_channels": ["slack"]})
        payload = call_tool("standup_config_set", {"enabled": True})
        config = payload["data"]["config"]
        assert config["enabled"] is True
        assert config["time"] == "09:15"  # earlier value preserved
        assert config["delivery_channels"] == ["slack"]

    def test_config_set_saves_authoritative_team_scope(self, seeded_session):
        payload = call_tool(
            "standup_config_set",
            {
                "tracker_sources": ["jira", "azure_devops"],
                "team_members": ["Alice", "Bob", "Alice"],
            },
        )
        config = payload["data"]["config"]
        assert config["tracker_sources"] == ["jira", "azure_devops"]
        assert config["team_members"] == ["Alice", "Bob"]
        assert config["roster_configured"] is True

    def test_members_previews_selected_trackers(self, seeded_session, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_jira_project_key", lambda: "PSOT")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "Core")
        monkeypatch.setattr(
            "yeaboi.standup.roster.discover_team_members",
            lambda sources, **kwargs: ["Alice", "Bob"],
        )
        payload = call_tool("standup_members", {"tracker_sources": ["jira"]})
        assert payload["ok"] is True
        assert payload["data"]["tracker_sources"] == ["jira"]
        assert payload["data"]["members"] == ["Alice", "Bob"]

    def test_config_set_rejects_sandboxed_repo_path(self, seeded_session, tmp_path):
        """A repo_path outside the sandbox whitelist is refused at write time."""
        payload = call_tool("standup_config_set", {"repo_path": "/denied-sandbox-dir/repo"})
        assert payload["ok"] is False
        assert "YEABOI_ALLOWED_PATHS" in payload["error"]["message"]

    def test_config_set_accepts_whitelisted_repo_path(self, seeded_session, tmp_path):
        repo = tmp_path / "repo"  # tmp_path is whitelisted by the conftest fixture
        repo.mkdir()
        payload = call_tool("standup_config_set", {"repo_path": str(repo)})
        assert payload["ok"] is True
        assert payload["data"]["config"]["repo_path"] == str(repo)

    def test_config_set_rejects_bad_time(self, seeded_session):
        payload = call_tool("standup_config_set", {"time": "quarter past nine"})
        assert payload["ok"] is False
        assert "HH:MM" in payload["error"]["message"]

    def test_config_set_rejects_bad_channel(self, seeded_session):
        payload = call_tool("standup_config_set", {"delivery_channels": ["pager"]})
        assert payload["ok"] is False
        assert "unknown delivery channel" in payload["error"]["message"]

    def test_config_set_automation_fields_merge(self, seeded_session):
        call_tool("standup_config_set", {"time": "09:15"})
        payload = call_tool("standup_config_set", {"automation_markers": "wiz", "automation_handling": "off"})
        config = payload["data"]["config"]
        assert config["automation_markers"] == "wiz"
        assert config["automation_handling"] == "off"
        assert config["time"] == "09:15"  # earlier value preserved
        # Omitting both keeps the tuned values.
        payload = call_tool("standup_config_set", {"enabled": True})
        config = payload["data"]["config"]
        assert config["automation_markers"] == "wiz"
        assert config["automation_handling"] == "off"

    def test_config_set_rejects_bad_automation_handling(self, seeded_session):
        payload = call_tool("standup_config_set", {"automation_handling": "flag"})
        assert payload["ok"] is False
        assert "automation_handling" in payload["error"]["message"]


class TestServerEntry:
    def test_import_without_mcp_is_safe(self):
        # The package must import fine even where the extra is missing —
        # server.py defers the mcp import into create_app()/main().
        import yeaboi.mcp
        import yeaboi.mcp.server  # noqa: F401

        assert hasattr(yeaboi.mcp.server, "main")
