"""The prior-art sub-loop inside project_intake.

Sits between the PTO sub-loop and the confirmation summary. The properties
worth defending: it only fires for greenfield, it never blocks the summary
when there is nothing to ask, every exit routes back through the funnel, and
rejections reach the global ledger while a bail-out does not.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from yeaboi.agent import prior_art
from yeaboi.agent.nodes import (
    _finish_prior_art,
    _prior_art_advance,
    _prior_art_applies,
    _prior_art_prompt,
    _show_summary_or_pto,
    project_intake,
)
from yeaboi.agent.state import TOTAL_QUESTIONS, QuestionnaireState


def _candidate(key="github:acme/auth", name="acme/auth"):
    return {
        "key": key,
        "name": name,
        "platform": "github",
        "url": f"https://github.com/{name}",
        "pitch": ["does OIDC login", "has session refresh"],
        "stack": ["Python", "FastAPI"],
        "languages": ["Python"],
    }


def _qs(*, greenfield=True, stage="", candidates=None, index=0):
    qs = QuestionnaireState(current_question=TOTAL_QUESTIONS + 1)
    qs.answers = {i: f"answer {i}" for i in range(1, TOTAL_QUESTIONS + 1)}
    qs.answers[1] = "A booking app"
    qs.answers[2] = "Greenfield" if greenfield else "Existing codebase"
    qs.awaiting_confirmation = True
    # PTO already resolved — the prior-art guard sits after it.
    qs._leave_input_stage = "done"
    qs._prior_art_stage = stage
    qs._prior_art_candidates = list(candidates or [])
    qs._prior_art_index = index
    return qs


@pytest.fixture(autouse=True)
def _no_ledger_writes(monkeypatch):
    """Capture ledger writes instead of touching the real database."""
    written: list[dict] = []
    monkeypatch.setattr(
        "yeaboi.agent.prior_art_feedback.apply_verdict",
        lambda **kw: written.append(kw) or True,
    )
    return written


class TestApplies:
    def test_greenfield_only(self):
        assert _prior_art_applies(_qs(greenfield=True))
        assert not _prior_art_applies(_qs(greenfield=False))


class TestGuard:
    def test_non_greenfield_goes_straight_to_the_summary(self, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("must not scan for a non-greenfield project")

        monkeypatch.setattr(prior_art, "shortlist", _boom)
        result = _show_summary_or_pto(_qs(greenfield=False))
        assert result["pending_review"] == "project_intake"

    def test_empty_shortlist_does_not_block_the_summary(self, monkeypatch):
        monkeypatch.setattr(
            prior_art, "shortlist", lambda *a, **k: prior_art.Shortlist(empty_reason=prior_art.EMPTY_NO_PROFILE)
        )
        qs = _qs()
        result = _show_summary_or_pto(qs)
        assert result["pending_review"] == "project_intake"
        assert qs._prior_art_stage == "done"
        # The reason is recorded so the card can say which gap this was.
        assert qs._prior_art_empty_reason == prior_art.EMPTY_NO_PROFILE

    def test_a_scan_failure_skips_the_step_rather_than_failing_intake(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("network gone")

        monkeypatch.setattr(prior_art, "shortlist", _boom)
        qs = _qs()
        result = _show_summary_or_pto(qs)
        assert result["pending_review"] == "project_intake"
        assert qs._prior_art_stage == "done"

    def test_candidates_open_the_subloop_and_withhold_the_summary(self, monkeypatch):
        monkeypatch.setattr(
            prior_art,
            "shortlist",
            lambda *a, **k: prior_art.Shortlist(
                candidates=(prior_art.RepoCandidate(key="github:acme/auth", name="acme/auth", pitch=("does OIDC",)),)
            ),
        )
        qs = _qs()
        result = _show_summary_or_pto(qs)
        # No pending_review — the summary has not been shown yet.
        assert "pending_review" not in result
        assert qs._prior_art_stage == "ask"
        assert "acme/auth" in result["messages"][0].content

    def test_a_finished_subloop_is_not_reopened(self, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("must not re-scan once the step is done")

        monkeypatch.setattr(prior_art, "shortlist", _boom)
        result = _show_summary_or_pto(_qs(stage="done"))
        assert result["pending_review"] == "project_intake"


class TestPtoExitsReachTheGuard:
    """The regression the reroute exists to prevent."""

    def test_no_leave_exit_still_runs_the_prior_art_step(self, monkeypatch):
        seen: list = []
        monkeypatch.setattr(
            prior_art,
            "shortlist",
            lambda *a, **k: seen.append(1) or prior_art.Shortlist(empty_reason=prior_art.EMPTY_NO_MATCH),
        )
        qs = _qs()
        qs._leave_input_stage = "ask"
        qs._awaiting_leave_input = True
        project_intake({"messages": [HumanMessage(content="2")], "questionnaire": qs})
        assert seen == [1], "the PTO 'no' exit must route through the funnel, not build the summary inline"

    def test_pto_guard_does_not_refire_after_the_reroute(self, monkeypatch):
        monkeypatch.setattr(prior_art, "shortlist", lambda *a, **k: prior_art.Shortlist(empty_reason="no_match"))
        qs = _qs()
        qs.intake_mode = "smart"
        qs._leave_input_stage = "ask"
        qs._awaiting_leave_input = True
        result = project_intake({"messages": [HumanMessage(content="2")], "questionnaire": qs})
        assert "planned leave" not in result["messages"][0].content.lower()
        assert result["pending_review"] == "project_intake"


class TestVerdicts:
    def _state(self, reply, qs):
        return {"messages": [HumanMessage(content=reply)], "questionnaire": qs}

    def test_accept_advances_and_records_the_candidate(self):
        qs = _qs(stage="ask", candidates=[_candidate(), _candidate("github:acme/pay", "acme/pay")])
        result = project_intake(self._state("1", qs))
        assert [c["key"] for c in qs._prior_art_accepted] == ["github:acme/auth"]
        assert qs._prior_art_index == 1
        assert "acme/pay" in result["messages"][0].content

    def test_reject_asks_why_before_advancing(self):
        qs = _qs(stage="ask", candidates=[_candidate()])
        result = project_intake(self._state("2", qs))
        assert qs._prior_art_stage == "reason"
        assert qs._prior_art_index == 0
        assert "Why isn't" in result["messages"][0].content

    def test_the_reason_is_recorded_then_the_loop_advances(self, _no_ledger_writes):
        qs = _qs(stage="reason", candidates=[_candidate()])
        project_intake(self._state("it's the service we're retiring", qs))
        assert qs._prior_art_rejected == [
            {"key": "github:acme/auth", "name": "acme/auth", "reason": "it's the service we're retiring"}
        ]
        assert qs._prior_art_stage == "done"

    def test_an_empty_reason_is_still_a_rejection(self, _no_ledger_writes):
        # Demanding a reason would train people to accept things to get past
        # the prompt.
        qs = _qs(stage="reason", candidates=[_candidate()])
        project_intake(self._state("", qs))
        assert qs._prior_art_rejected[0]["reason"] == ""
        assert any(w["verdict"] == "down" for w in _no_ledger_writes)

    def test_skip_the_rest_ends_the_loop_without_rejecting_anything(self, _no_ledger_writes):
        qs = _qs(stage="ask", candidates=[_candidate(), _candidate("github:acme/pay", "acme/pay")])
        result = project_intake(self._state("3", qs))
        assert qs._prior_art_stage == "done"
        assert qs._prior_art_rejected == []
        # Bailing out is not a verdict — nothing reaches the ledger.
        assert _no_ledger_writes == []
        assert result["pending_review"] == "project_intake"

    def test_unrecognised_reply_reprompts_without_advancing(self):
        qs = _qs(stage="ask", candidates=[_candidate()])
        result = project_intake(self._state("what?", qs))
        assert qs._prior_art_index == 0
        assert qs._prior_art_stage == "ask"
        assert "Please choose" in result["messages"][0].content

    def test_word_forms_work_as_well_as_digits(self):
        qs = _qs(stage="ask", candidates=[_candidate()])
        project_intake(self._state("yes", qs))
        assert len(qs._prior_art_accepted) == 1

    def test_an_emptied_candidate_list_closes_out_instead_of_looping(self):
        qs = _qs(stage="ask", candidates=[])
        result = project_intake(self._state("1", qs))
        assert qs._prior_art_stage == "done"
        assert result["pending_review"] == "project_intake"


class TestLedger:
    def test_rejections_and_acceptances_are_both_written(self, _no_ledger_writes):
        qs = _qs(stage="ask", candidates=[_candidate()])
        qs._prior_art_accepted = [_candidate("github:acme/ok", "acme/ok")]
        qs._prior_art_rejected = [{"key": "github:acme/no", "name": "acme/no", "reason": "retired"}]
        _finish_prior_art(qs)
        verdicts = {w["repo_key"]: w["verdict"] for w in _no_ledger_writes}
        assert verdicts == {"github:acme/ok": "up", "github:acme/no": "down"}

    def test_the_rejection_reason_is_carried(self, _no_ledger_writes):
        qs = _qs()
        qs._prior_art_rejected = [{"key": "github:acme/no", "name": "acme/no", "reason": "retired"}]
        _finish_prior_art(qs)
        assert _no_ledger_writes[0]["reason"] == "retired"


class TestSummaryAndPromotion:
    def test_accepted_repos_appear_in_the_intake_summary(self, monkeypatch):
        monkeypatch.setattr(prior_art, "shortlist", lambda *a, **k: prior_art.Shortlist(empty_reason="no_match"))
        qs = _qs(stage="done")
        qs._prior_art_accepted = [_candidate()]
        result = _show_summary_or_pto(qs)
        body = result["messages"][0].content
        assert "Prior art" in body and "acme/auth" in body

    def test_no_prior_art_section_when_nothing_was_accepted(self, monkeypatch):
        monkeypatch.setattr(prior_art, "shortlist", lambda *a, **k: prior_art.Shortlist(empty_reason="no_match"))
        assert "Prior art" not in _show_summary_or_pto(_qs(stage="done"))["messages"][0].content

    def test_confirm_promotes_accepted_refs_onto_graph_state(self):
        qs = _qs(stage="done")
        qs._prior_art_accepted = [_candidate()]
        result = project_intake({"messages": [HumanMessage(content="confirm")], "questionnaire": qs})
        (ref,) = result["prior_art"]
        assert ref.key == "github:acme/auth"
        assert ref.pitch == ("does OIDC login", "has session refresh")
        assert ref.stack == ("Python", "FastAPI")

    def test_confirm_with_no_prior_art_yields_an_empty_tuple(self):
        result = project_intake({"messages": [HumanMessage(content="confirm")], "questionnaire": _qs(stage="done")})
        assert result["prior_art"] == ()


class TestPrompt:
    def test_shows_position_pitch_and_stack(self):
        qs = _qs(stage="ask", candidates=[_candidate(), _candidate("github:acme/pay", "acme/pay")])
        text = _prior_art_prompt(qs)
        assert "1 of 2" in text
        assert "acme/auth" in text
        assert "does OIDC login" in text
        assert "Python, FastAPI" in text

    def test_exhausted_list_renders_nothing(self):
        assert _prior_art_prompt(_qs(stage="ask", candidates=[], index=5)) == ""

    def test_advance_past_the_end_finishes(self, _no_ledger_writes):
        qs = _qs(stage="ask", candidates=[_candidate()], index=0)
        result = _prior_art_advance(qs)
        assert qs._prior_art_stage == "done"
        assert result["pending_review"] == "project_intake"
