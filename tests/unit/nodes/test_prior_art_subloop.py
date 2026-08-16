"""The prior-art sub-loop inside project_intake.

Sits between the PTO sub-loop and the confirmation summary. The properties
worth defending: it only fires for greenfield, it never blocks the summary
when there is nothing to ask, every exit routes back through the funnel, and
only explicit verdicts reach the global ledger — a candidate merely left
unpicked in the batch answer is passed over for this project only.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage

from yeaboi.agent import prior_art
from yeaboi.agent.nodes import (
    _finish_prior_art,
    _parse_prior_art_answer,
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

    def test_empty_shortlist_says_why_then_reaches_the_summary(self, monkeypatch):
        """An empty shortlist costs the user one keypress, not a mystery: the
        card names the gap, and the very next input reaches the summary."""
        monkeypatch.setattr(
            prior_art, "shortlist", lambda *a, **k: prior_art.Shortlist(empty_reason=prior_art.EMPTY_NO_PROFILE)
        )
        qs = _qs()
        first = _show_summary_or_pto(qs)
        assert qs._prior_art_stage == "empty"
        assert qs._prior_art_empty_reason == prior_art.EMPTY_NO_PROFILE
        assert "pending_review" not in first
        after = project_intake({"messages": [HumanMessage(content="ok")], "questionnaire": qs})
        assert after["pending_review"] == "project_intake"
        assert qs._prior_art_stage == "done"

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
        # An unrecognised reason has no card copy, so this exits straight to
        # the summary — which is what makes it a clean test of the PTO guard.
        monkeypatch.setattr(prior_art, "shortlist", lambda *a, **k: prior_art.Shortlist(empty_reason="unknown"))
        qs = _qs()
        qs.intake_mode = "smart"
        qs._leave_input_stage = "ask"
        qs._awaiting_leave_input = True
        result = project_intake({"messages": [HumanMessage(content="2")], "questionnaire": qs})
        assert "planned leave" not in result["messages"][0].content.lower()
        assert result["pending_review"] == "project_intake"


class TestParseGrammar:
    """_parse_prior_art_answer — the one grammar every surface speaks."""

    def test_indices_commas_and_whitespace(self):
        assert _parse_prior_art_answer("1 3", 3) == ({0, 2}, set())
        assert _parse_prior_art_answer("1,3", 3) == ({0, 2}, set())
        assert _parse_prior_art_answer(" 2 ", 3) == ({1}, set())

    def test_all_none_skip_and_empty(self):
        assert _parse_prior_art_answer("all", 2) == ({0, 1}, set())
        assert _parse_prior_art_answer("none", 2) == (set(), set())
        assert _parse_prior_art_answer("skip", 2) == (set(), set())
        assert _parse_prior_art_answer("", 2) == (set(), set())

    def test_bang_bans(self):
        assert _parse_prior_art_answer("1 !2", 3) == ({0}, {1})
        assert _parse_prior_art_answer("!3", 3) == (set(), {2})

    def test_ban_wins_over_select_of_the_same_index(self):
        assert _parse_prior_art_answer("1 !1", 2) == (set(), {0})
        assert _parse_prior_art_answer("all !2", 2) == ({0}, {1})

    def test_out_of_range_and_unknown_tokens_fail_whole(self):
        # Half an answer must not be recorded as a verdict.
        assert _parse_prior_art_answer("1 9", 3) is None
        assert _parse_prior_art_answer("0", 3) is None
        assert _parse_prior_art_answer("what?", 3) is None
        assert _parse_prior_art_answer("!x", 3) is None


class TestBatchVerdicts:
    def _state(self, reply, qs):
        return {"messages": [HumanMessage(content=reply)], "questionnaire": qs}

    def _three(self):
        return [
            _candidate(),
            _candidate("github:acme/pay", "acme/pay"),
            _candidate("github:acme/web", "acme/web"),
        ]

    def test_indices_select_relevant_and_finish_in_one_turn(self):
        qs = _qs(stage="ask", candidates=self._three())
        result = project_intake(self._state("1 3", qs))
        assert [c["key"] for c in qs._prior_art_accepted] == ["github:acme/auth", "github:acme/web"]
        assert qs._prior_art_stage == "done"
        # The whole batch is one graph turn — the summary follows immediately.
        assert result["pending_review"] == "project_intake"

    def test_all_selects_everything(self):
        qs = _qs(stage="ask", candidates=self._three())
        project_intake(self._state("all", qs))
        assert len(qs._prior_art_accepted) == 3

    def test_none_selects_nothing_and_writes_nothing(self, _no_ledger_writes):
        qs = _qs(stage="ask", candidates=self._three())
        result = project_intake(self._state("none", qs))
        assert qs._prior_art_accepted == []
        assert qs._prior_art_rejected == []
        # Passing over the batch is not a verdict — nothing reaches the ledger.
        assert _no_ledger_writes == []
        assert result["pending_review"] == "project_intake"

    def test_skip_still_means_none(self, _no_ledger_writes):
        qs = _qs(stage="ask", candidates=self._three())
        result = project_intake(self._state("skip", qs))
        assert qs._prior_art_stage == "done"
        assert _no_ledger_writes == []
        assert result["pending_review"] == "project_intake"

    def test_bang_bans_with_an_empty_reason(self, _no_ledger_writes):
        qs = _qs(stage="ask", candidates=self._three())
        project_intake(self._state("1 !2", qs))
        assert qs._prior_art_rejected == [{"key": "github:acme/pay", "name": "acme/pay", "reason": ""}]
        verdicts = {w["repo_key"]: w["verdict"] for w in _no_ledger_writes}
        assert verdicts == {"github:acme/auth": "up", "github:acme/pay": "down"}

    def test_a_ban_beats_a_select_of_the_same_repo(self):
        qs = _qs(stage="ask", candidates=self._three())
        project_intake(self._state("1 !1", qs))
        assert qs._prior_art_accepted == []
        assert [r["key"] for r in qs._prior_art_rejected] == ["github:acme/auth"]

    def test_invalid_token_reprompts_without_finishing(self):
        qs = _qs(stage="ask", candidates=self._three())
        result = project_intake(self._state("what?", qs))
        assert qs._prior_art_stage == "ask"
        assert qs._prior_art_accepted == []
        assert "Reply with the numbers" in result["messages"][0].content

    def test_a_resubmission_replaces_rather_than_appends(self):
        # The submission is the whole verdict: pre-filled lists (a legacy
        # half-loop carried in by a resumed session) are re-derived, never
        # added to — the double-write guard.
        qs = _qs(stage="ask", candidates=self._three())
        qs._prior_art_accepted = [_candidate("github:acme/pay", "acme/pay")]
        qs._prior_art_rejected = [{"key": "github:acme/web", "name": "acme/web", "reason": "old"}]
        project_intake(self._state("1", qs))
        assert [c["key"] for c in qs._prior_art_accepted] == ["github:acme/auth"]
        assert qs._prior_art_rejected == []

    def test_an_emptied_candidate_list_closes_out_instead_of_looping(self):
        qs = _qs(stage="ask", candidates=[])
        result = project_intake(self._state("1", qs))
        assert qs._prior_art_stage == "done"
        assert result["pending_review"] == "project_intake"


class TestLegacyReasonStage:
    """Sessions serialized mid-"reason" by the old per-repo loop."""

    def test_a_resumed_reason_stage_reasks_the_batch(self, _no_ledger_writes):
        qs = _qs(stage="reason", candidates=[_candidate(), _candidate("github:acme/pay", "acme/pay")])
        result = project_intake({"messages": [HumanMessage(content="it is being retired")], "questionnaire": qs})
        # The text answered a question this build no longer asks — nothing is
        # recorded from it; the batch prompt goes out instead.
        assert qs._prior_art_stage == "ask"
        assert qs._prior_art_rejected == []
        assert _no_ledger_writes == []
        body = result["messages"][0].content
        assert "acme/auth" in body and "acme/pay" in body


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

    def test_confirm_keeps_references_seeded_by_a_headless_caller(self):
        """`prior_art` is a LastValue channel, so returning the empty
        questionnaire tuple would *replace* what `run_planning_pipeline(
        prior_art=…)` seeded — a headless run never walks the sub-loop, so
        the CLI and MCP flags would reach the analyzer as nothing at all."""
        from yeaboi.agent.state import PriorArtRef

        seeded = PriorArtRef(key="github:acme/auth", name="acme/auth", url="", platform="github")
        result = project_intake(
            {
                "messages": [HumanMessage(content="confirm")],
                "questionnaire": _qs(stage="done"),
                "prior_art": (seeded,),
            }
        )
        assert result["prior_art"] == (seeded,)

    def test_an_answered_subloop_wins_over_seeded_references(self):
        """The seed is a fallback, not an override: if the user actually
        answered the sub-loop, their verdicts are the durable ones."""
        from yeaboi.agent.state import PriorArtRef

        qs = _qs(stage="done")
        qs._prior_art_accepted = [_candidate()]
        stale = PriorArtRef(key="github:acme/stale", name="acme/stale", url="", platform="github")
        result = project_intake(
            {"messages": [HumanMessage(content="confirm")], "questionnaire": qs, "prior_art": (stale,)}
        )
        assert [r.key for r in result["prior_art"]] == ["github:acme/auth"]


class TestPrompt:
    def test_lists_every_candidate_numbered(self):
        qs = _qs(stage="ask", candidates=[_candidate(), _candidate("github:acme/pay", "acme/pay")])
        text = _prior_art_prompt(qs)
        assert "**1. acme/auth**" in text
        assert "**2. acme/pay**" in text
        assert "does OIDC login" in text
        assert "Python, FastAPI" in text

    def test_explains_the_answer_grammar(self):
        """The markdown is the whole interface on the text surfaces (REPL,
        form, headless), so the grammar has to be in the prompt itself."""
        qs = _qs(stage="ask", candidates=[_candidate()])
        text = _prior_art_prompt(qs)
        assert '"all"' in text
        assert '"none"' in text
        assert "never suggest" in text

    def test_pitches_are_condensed_to_two_bullets(self):
        candidate = _candidate()
        candidate["pitch"] = ["one", "two", "three", "four"]
        qs = _qs(stage="ask", candidates=[candidate])
        text = _prior_art_prompt(qs)
        assert "one" in text and "two" in text
        assert "three" not in text

    def test_exhausted_list_renders_nothing(self):
        assert _prior_art_prompt(_qs(stage="ask", candidates=[], index=5)) == ""


class TestEmptyCard:
    """Nothing found is a result, not a reason to go quiet.

    All three empty reasons mean something different to the user — never ran
    Team Analysis, ran it before this feature existed, ran it and nothing
    matched — and only two of them are the user's to act on. Falling silently
    through to the summary reads as "your repositories were considered and
    rejected" in every one of them.
    """

    def _empty(self, monkeypatch, reason):
        monkeypatch.setattr(prior_art, "shortlist", lambda *a, **k: prior_art.Shortlist(empty_reason=reason))
        qs = _qs()
        result = _show_summary_or_pto(qs)
        return qs, result["messages"][0].content

    @pytest.mark.parametrize(
        "reason,needle",
        [
            (prior_art.EMPTY_NO_PROFILE, "run Team Analysis"),
            (prior_art.EMPTY_NO_INVENTORY, "re-run Team Analysis"),
            (prior_art.EMPTY_NO_MATCH, "looks close to this project"),
        ],
    )
    def test_each_reason_is_said_out_loud(self, monkeypatch, reason, needle):
        qs, body = self._empty(monkeypatch, reason)
        assert qs._prior_art_stage == "empty"
        assert needle in body
        # The summary must not have been posted underneath it.
        assert "Ready to build the plan" not in body

    def test_the_card_is_not_the_summary(self, monkeypatch):
        """The empty card owns the turn, so pending_review must stay unset —
        emitting it here would mark the intake reviewed before the user has
        seen the summary at all."""
        _, _ = self._empty(monkeypatch, prior_art.EMPTY_NO_PROFILE)
        monkeypatch.setattr(
            prior_art, "shortlist", lambda *a, **k: prior_art.Shortlist(empty_reason=prior_art.EMPTY_NO_PROFILE)
        )
        assert "pending_review" not in _show_summary_or_pto(_qs())

    def test_any_input_acknowledges_it_and_reaches_the_summary(self, monkeypatch):
        monkeypatch.setattr(prior_art, "shortlist", lambda *a, **k: prior_art.Shortlist(empty_reason="no_match"))
        qs = _qs(stage="empty")
        result = project_intake({"messages": [HumanMessage(content="ok")], "questionnaire": qs})
        assert qs._prior_art_stage == "done"
        assert result["pending_review"] == "project_intake"

    def test_an_unrecognised_reason_falls_straight_through(self, monkeypatch):
        """A reason with no copy behind it must not render a blank card — the
        step is optional, and silence beats an empty box with a button."""
        monkeypatch.setattr(prior_art, "shortlist", lambda *a, **k: prior_art.Shortlist(empty_reason="something_new"))
        qs = _qs()
        result = _show_summary_or_pto(qs)
        assert qs._prior_art_stage == "done"
        assert result["pending_review"] == "project_intake"

    def test_a_failed_scan_stays_silent(self, monkeypatch):
        """A crash is ours, not the user's — there is nothing for them to do
        about it, so it is logged and the intake carries on."""

        def _boom(*a, **k):
            raise RuntimeError("github exploded")

        monkeypatch.setattr(prior_art, "shortlist", _boom)
        qs = _qs()
        result = _show_summary_or_pto(qs)
        assert qs._prior_art_stage == "done"
        assert result["pending_review"] == "project_intake"


class TestEmptyCardChoices:
    def test_continue_row_is_offered_and_the_confirm_menu_is_not(self):
        """Both stages run with awaiting_confirmation set past the last
        question, so without the guard the Accept/Edit menu renders over it."""
        from yeaboi.ui.session.chat._question_view import (
            CONFIRM_ACCEPT,
            PRIOR_ART_CONTINUE,
            derive_question_view,
        )

        view = derive_question_view({"questionnaire": _qs(stage="empty")})
        labels = [label for label, _ in view.choices]
        assert labels == [PRIOR_ART_CONTINUE]
        assert CONFIRM_ACCEPT not in labels
