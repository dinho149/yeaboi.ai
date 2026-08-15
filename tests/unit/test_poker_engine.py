"""Tests for the poker engine (poker/engine.py) — parse → fallback, never raises."""

from types import SimpleNamespace

import pytest

from yeaboi.poker import engine


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Point the cross-mode context gatherer at an empty tmp DB.

    Without this, an engine call with a non-demo ticket would read the
    developer's real ~/.yeaboi history and make tests environment-dependent.
    """
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "sessions.db")
    monkeypatch.setattr("yeaboi.config.get_sessions_db", lambda: tmp_path / "sessions.db")


def _ticket() -> dict:
    return {
        "source": "demo",
        "key": "T-1",
        "summary": "Do the thing",
        "description": "<div>raw</div>",
        "description_text": "Details here",
        "story_points": 5.0,
    }


class TestFallbackHelpers:
    def test_fallback_note_agreement(self):
        note = engine._build_fallback_note({"Alex": "5", "Sam": "5"})
        assert "agrees on 5" in note

    def test_fallback_note_spread(self):
        note = engine._build_fallback_note({"Alex": "3", "Sam": "13"})
        assert "3–13" in note
        assert "median" in note

    def test_fallback_note_question_marks(self):
        note = engine._build_fallback_note({"Alex": "?", "Sam": "5"})
        assert "'?'" in note

    def test_fallback_note_only_non_numeric(self):
        note = engine._build_fallback_note({"Alex": "☕"})
        assert "re-vote" in note

    def test_fallback_note_empty(self):
        assert "No votes" in engine._build_fallback_note({})

    def test_fallback_suggestion_median_snapped(self):
        assert engine._fallback_suggestion({"a": "3", "b": "5", "c": "13"}) == 5.0
        assert engine._fallback_suggestion({"a": "?"}) is None


class TestParsePerspective:
    def test_happy_path(self):
        note, pts, confidence, evidence = engine._parse_perspective(
            '{"comment": "Looks fine", "suggested_points": 5, "confidence": "high",'
            ' "evidence": ["5-pt stories avg 4.2 days", "PROJ-87 shipped as a 5"]}'
        )
        assert note == "Looks fine"
        assert pts == 5.0
        assert confidence == "high"
        assert evidence == ("5-pt stories avg 4.2 days", "PROJ-87 shipped as a 5")

    def test_old_two_field_shape_still_parses(self):
        assert engine._parse_perspective('{"comment": "ok", "suggested_points": 5}') == ("ok", 5.0, "", ())

    def test_markdown_fences_tolerated(self):
        raw = '```json\n{"comment": "ok", "suggested_points": 8}\n```'
        assert engine._parse_perspective(raw) == ("ok", 8.0, "", ())

    def test_out_of_deck_suggestion_snapped(self):
        _, pts, _, _ = engine._parse_perspective('{"comment": "x", "suggested_points": 11}')
        assert pts == 13.0

    def test_null_suggestion(self):
        assert engine._parse_perspective('{"comment": "x", "suggested_points": null}') == ("x", None, "", ())

    def test_invalid_confidence_collapses(self):
        _, _, confidence, _ = engine._parse_perspective('{"comment": "x", "confidence": "certain"}')
        assert confidence == ""

    def test_evidence_sanitized(self):
        raw = '{"comment": "x", "evidence": ["a", 42, "  ", "' + "b" * 200 + '", "c", "d"]}'
        _, _, _, evidence = engine._parse_perspective(raw)
        assert len(evidence) == engine._MAX_EVIDENCE
        assert evidence[0] == "a"
        assert len(evidence[1]) == engine._MAX_EVIDENCE_LEN  # long entry truncated
        assert all(isinstance(e, str) for e in evidence)

    def test_evidence_non_list_dropped(self):
        assert engine._parse_perspective('{"comment": "x", "evidence": "not a list"}')[3] == ()

    def test_garbage_returns_empty(self):
        assert engine._parse_perspective("not json") == ("", None, "", ())
        assert engine._parse_perspective("[1, 2]") == ("", None, "", ())
        assert engine._parse_perspective("") == ("", None, "", ())


class TestGetPokerPerspective:
    def test_unconfigured_llm_falls_back(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no API key"))
        out = engine.get_poker_perspective(_ticket(), {"Alex": "3", "Sam": "8"})
        assert out["llm_mode"] == "fallback"
        assert out["suggested_points"] == 5.0  # median(3,8)=5.5 → snaps to 5
        assert out["note"]
        assert any("no API key" in w for w in out["warnings"])

    def test_happy_path(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        seen = {}

        def _invoke(prompt, **kwargs):
            seen["prompt"] = prompt
            return SimpleNamespace(content='{"comment": "The 13 voter sees migration risk.", "suggested_points": 8}')

        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", _invoke)
        out = engine.get_poker_perspective(_ticket(), {"Alex": "5", "Sam": "13"})
        assert out == {
            "note": "The 13 voter sees migration risk.",
            "suggested_points": 8.0,
            "confidence": "",
            "evidence": [],
            "llm_mode": "llm",
            "warnings": [],
        }
        # The prompt carries the ticket's display text and the votes as data.
        assert "Do the thing" in seen["prompt"]
        assert "Details here" in seen["prompt"]
        assert "Alex" in seen["prompt"]

    def test_acceptance_text_reaches_the_prompt(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        seen = {}

        def _invoke(prompt, **kwargs):
            seen["prompt"] = prompt
            return SimpleNamespace(content='{"comment": "ok", "suggested_points": 5}')

        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", _invoke)
        ticket = {**_ticket(), "acceptance_text": "AC1: must work offline"}
        engine.get_poker_perspective(ticket, {"Alex": "5", "Sam": "13"})
        assert "AC1: must work offline" in seen["prompt"]
        assert "TICKET acceptance criteria:" in seen["prompt"]

    def test_malformed_json_falls_back(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", lambda *a, **k: SimpleNamespace(content="garbage"))
        out = engine.get_poker_perspective(_ticket(), {"Alex": "5"})
        assert out["llm_mode"] == "fallback"
        assert out["suggested_points"] == 5.0

    def test_llm_exception_never_raises(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))

        def _boom(*a, **k):
            raise RuntimeError("connection reset")

        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", _boom)
        out = engine.get_poker_perspective(_ticket(), {"Alex": "5"})
        assert out["llm_mode"] == "fallback"
        assert out["warnings"]

    def test_empty_ticket_tolerated(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no API key"))
        out = engine.get_poker_perspective({}, {})
        assert out["llm_mode"] == "fallback"
        assert out["suggested_points"] is None


def _history_context():
    from dataclasses import replace

    from yeaboi.poker.context import PokerEstimationContext, format_poker_context_md

    ctx = PokerEstimationContext(
        calibration_lines=("5-pt stories: avg cycle 4.2 days, 20% overshoot (n=12).",),
        calibration_by_value=((5.0, "5-pt stories: avg cycle 4.2 days, 20% overshoot (n=12)."),),
        assignee_lines=("Alex (ticket assignee) reported blockers in the latest standup: waiting on API keys",),
    )
    return replace(ctx, summary_md=format_poker_context_md(ctx))


class TestCrossModeContext:
    def test_context_md_reaches_the_prompt(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        seen = {}

        def _invoke(prompt, **kwargs):
            seen["prompt"] = prompt
            return SimpleNamespace(
                content='{"comment": "ok", "suggested_points": 5, "confidence": "medium", "evidence": ["cal"]}'
            )

        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", _invoke)
        out = engine.get_poker_perspective(_ticket(), {"Alex": "5"}, context=_history_context())
        assert "5-pt stories: avg cycle 4.2 days" in seen["prompt"]
        assert out["confidence"] == "medium"
        assert out["evidence"] == ["cal"]

    def test_injected_context_skips_gathering(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no API key"))

        def _boom(*a, **k):
            raise AssertionError("gather_poker_context should not be called when context is injected")

        monkeypatch.setattr("yeaboi.poker.context.gather_poker_context", _boom)
        out = engine.get_poker_perspective(_ticket(), {"Alex": "5"}, context=_history_context())
        assert out["llm_mode"] == "fallback"

    def test_none_context_triggers_gathering(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no API key"))
        called = {}

        def _gather(ticket, *, project_name=""):
            called["project_name"] = project_name
            from yeaboi.poker.context import PokerEstimationContext

            return PokerEstimationContext()

        monkeypatch.setattr("yeaboi.poker.context.gather_poker_context", _gather)
        engine.get_poker_perspective(_ticket(), {"Alex": "5"}, project_name="Login")
        assert called["project_name"] == "Login"

    def test_fallback_note_includes_history_lines(self):
        note = engine._build_fallback_note({"Alex": "5", "Sam": "5"}, _history_context())
        assert "Team history: 5-pt stories" in note
        assert "waiting on API keys" in note

    def test_fallback_note_without_matching_calibration(self):
        from dataclasses import replace

        from yeaboi.poker.context import PokerEstimationContext

        ctx = replace(
            _history_context(),
            calibration_by_value=((8.0, "8-pt stories: avg cycle 7.5 days (n=4)."),),
        )
        note = engine._build_fallback_note({"Alex": "5", "Sam": "5"}, ctx)
        assert "Team history" not in note  # no calibration for the suggested 5
        assert "waiting on API keys" in note  # blocker line still appended
        assert isinstance(ctx, PokerEstimationContext)

    def test_fallback_note_empty_context_unchanged(self):
        from yeaboi.poker.context import PokerEstimationContext

        with_ctx = engine._build_fallback_note({"Alex": "3", "Sam": "13"}, PokerEstimationContext())
        without = engine._build_fallback_note({"Alex": "3", "Sam": "13"})
        assert with_ctx == without

    def test_fallback_path_carries_empty_confidence_and_evidence(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no API key"))
        out = engine.get_poker_perspective(_ticket(), {"Alex": "5"}, context=_history_context())
        assert out["confidence"] == ""
        assert out["evidence"] == []
        assert "Team history: 5-pt stories" in out["note"]


class TestDebateTranscript:
    def test_transcript_reaches_the_prompt(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        seen = {}

        def _invoke(prompt, **kwargs):
            seen["prompt"] = prompt
            return SimpleNamespace(
                content='{"comment": "Sam argued the migration risk better.", "suggested_points": 8}'
            )

        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", _invoke)
        out = engine.get_poker_perspective(
            _ticket(),
            {"Alex": "5", "Sam": "13"},
            context=_history_context(),
            debate_transcript="Sam (voted 13) — turn 2: the migration is the hard part.",
        )
        assert out["llm_mode"] == "llm"
        assert "DEBATE TRANSCRIPT" in seen["prompt"]
        assert "the migration is the hard part." in seen["prompt"]

    def test_no_transcript_keeps_prompt_free_of_debate_section(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        seen = {}

        def _invoke(prompt, **kwargs):
            seen["prompt"] = prompt
            return SimpleNamespace(content='{"comment": "ok", "suggested_points": 5}')

        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", _invoke)
        engine.get_poker_perspective(_ticket(), {"Alex": "5"}, context=_history_context())
        assert "DEBATE TRANSCRIPT" not in seen["prompt"]

    def test_fallback_mentions_recorded_transcript(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no API key"))
        out = engine.get_poker_perspective(
            _ticket(),
            {"Alex": "5", "Sam": "13"},
            context=_history_context(),
            debate_transcript="x" * 120,
        )
        assert out["llm_mode"] == "fallback"
        # Content-free: the note reports that a transcript exists, never its words.
        assert "120-char duel transcript was recorded" in out["note"]
        assert "x" * 10 not in out["note"]


class TestPerspectiveFailureMessages:
    """What a failed AI perspective tells the host.

    Each branch has a different remedy — wait, fix your key, start Ollama — so
    each says which one it is rather than sending everyone to a log file.
    """

    def _run(self, monkeypatch, exc):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))

        def _boom(prompt, **kwargs):
            raise exc

        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", _boom)
        return engine.get_poker_perspective(_ticket(), {"Alex": "3", "Sam": "8"})

    def test_rate_limit_says_to_try_again(self, monkeypatch):
        import anthropic
        import httpx

        exc = anthropic.RateLimitError(
            "rate limited",
            response=httpx.Response(429, request=httpx.Request("POST", "https://api.anthropic.com")),
            body=None,
        )
        result = self._run(monkeypatch, exc)
        assert result["llm_mode"] == "fallback"
        assert "rate limited" in result["warnings"][0]

    def test_an_unknown_failure_names_the_log_file(self, monkeypatch):
        result = self._run(monkeypatch, RuntimeError("something else"))
        assert "poker.log" in result["warnings"][0]
        assert "see logs" not in result["warnings"][0]
