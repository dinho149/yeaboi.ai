"""Unit tests for the prior-art engine.

The invariants worth defending: ranking is pure and stable, the ledger
suppresses before scoring, the model can only drop candidates and never add
one, and every impure edge degrades instead of raising.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from yeaboi.agent import prior_art
from yeaboi.agent.prior_art_feedback import FeedbackExample, Ledger

_THIS_YEAR = datetime.now(UTC).year


def _row(**over):
    row = {
        "key": "github:acme/auth",
        "provider": "github",
        "name": "acme/auth",
        "url": "https://github.com/acme/auth",
        "description": "OIDC login and session refresh for internal services",
        "languages": ["Python", "TypeScript"],
        "updated_at": f"{_THIS_YEAR}-06-01T00:00:00+00:00",
    }
    row.update(over)
    return row


_REQS = prior_art.Requirements(
    description="A booking app that needs login",
    outcomes="Users can sign in and book a slot",
    stack="Python, FastAPI",
    integrations="Stripe",
)


class TestApplies:
    def test_greenfield_only(self):
        assert prior_art.applies({2: "Greenfield"})
        assert not prior_art.applies({2: "Existing codebase"})
        assert not prior_art.applies({2: "Hybrid"})

    def test_missing_or_empty_answer(self):
        assert not prior_art.applies({})
        assert not prior_art.applies(None)
        assert not prior_art.applies({2: ""})

    def test_case_and_surrounding_prose_tolerated(self):
        assert prior_art.applies({2: "  greenfield  "})
        assert prior_art.applies({2: "Greenfield project"})


class TestRequirementsFromAnswers:
    def test_pulls_the_ranking_inputs(self):
        reqs = prior_art.requirements_from_answers({1: "desc", 3: "problem", 4: "done", 11: "Go", 12: "Stripe"})
        assert reqs.description == "desc"
        assert reqs.outcomes == "problem done"
        assert reqs.stated_stack == "Go Stripe"
        assert reqs.prose == "desc problem done"

    def test_missing_answers_are_empty_not_none(self):
        reqs = prior_art.requirements_from_answers({})
        assert reqs.description == "" and reqs.prose == "" and reqs.stated_stack == ""


class TestStructureSignals:
    def test_detects_positive_capabilities(self):
        paths = ["tests/test_a.py", ".github/workflows/ci.yml", "infra/main.tf", "pyproject.toml", "docs/adr/1.md"]
        found = prior_art.structure_signals(paths)
        assert {"tests", "CI", "Terraform", "docs", "dependency manifest"} <= set(found)

    def test_empty_tree_is_empty(self):
        assert prior_art.structure_signals([]) == ()
        assert prior_art.structure_signals(None) == ()

    def test_unremarkable_tree_reports_nothing(self):
        assert prior_art.structure_signals(["main.c", "util.c"]) == ()


class TestScore:
    def test_shared_stack_dominates_keyword_overlap(self):
        stack_only, _ = prior_art.score(_row(description="", languages=["Python"]), _REQS)
        words_only, _ = prior_art.score(_row(description="booking slot", languages=["Rust"]), _REQS)
        assert stack_only > words_only

    def test_why_is_human_readable_evidence(self):
        _, why = prior_art.score(_row(), _REQS)
        assert any("Shares your stack" in line for line in why)

    def test_recency_never_filters_only_tiebreaks(self):
        # A mature service nobody has pushed to is often the best thing to copy.
        old, _ = prior_art.score(_row(updated_at="2019-01-01T00:00:00+00:00"), _REQS)
        assert old >= prior_art._MIN_SCORE

    def test_unparseable_date_scores_zero_recency(self):
        assert prior_art._recency_score("not-a-date") == 0.0
        assert prior_art._recency_score("") == 0.0

    def test_stopwords_do_not_create_matches(self):
        value, _ = prior_art.score(_row(description="the and with your", languages=["Rust"]), _REQS)
        assert value < prior_art._MIN_SCORE


class TestRank:
    def test_orders_by_score_descending(self):
        rows = [
            _row(key="github:acme/weak", name="acme/weak", description="", languages=["Rust", "Python"]),
            _row(key="github:acme/strong", name="acme/strong", description="booking login slot"),
        ]
        assert [c.key for c in prior_art.rank(rows, _REQS)] == ["github:acme/strong", "github:acme/weak"]

    def test_ties_break_on_key_so_the_order_is_stable(self):
        rows = [_row(key="github:acme/b", name="acme/b"), _row(key="github:acme/a", name="acme/a")]
        first = [c.key for c in prior_art.rank(rows, _REQS)]
        second = [c.key for c in prior_art.rank(list(reversed(rows)), _REQS)]
        assert first == second == ["github:acme/a", "github:acme/b"]

    def test_rejected_repos_are_dropped_before_scoring(self):
        ledger = Ledger(rejected=frozenset({"github:acme/auth"}))
        assert prior_art.rank([_row()], _REQS, ledger) == []

    def test_noise_below_the_floor_is_excluded(self):
        assert prior_art.rank([_row(description="", languages=["COBOL"])], _REQS) == []

    def test_rows_without_a_key_are_skipped(self):
        assert prior_art.rank([_row(key="")], _REQS) == []

    def test_respects_the_limit(self):
        rows = [_row(key=f"github:acme/r{i}", name=f"acme/r{i}") for i in range(20)]
        assert len(prior_art.rank(rows, _REQS, limit=3)) == 3

    def test_empty_input(self):
        assert prior_art.rank([], _REQS) == []
        assert prior_art.rank(None, _REQS) == []


class TestCandidateStack:
    def test_dedupes_across_the_three_sources_preserving_order(self):
        c = prior_art.RepoCandidate(
            key="k", name="n", languages=("Python",), frameworks=("FastAPI", "Python"), integrations=("Stripe",)
        )
        assert c.stack == ("Python", "FastAPI", "Stripe")


class TestParsePitchResponse:
    def test_reads_bullets_and_the_drop_flag(self):
        raw = '{"repos": [{"key": "github:acme/auth", "pitch": ["does OIDC"], "drop": false}]}'
        out = prior_art._parse_pitch_response(raw, {"github:acme/auth"})
        assert out["github:acme/auth"]["pitch"] == ["does OIDC"]
        assert out["github:acme/auth"]["drop"] is False

    def test_invented_keys_are_discarded(self):
        # This is what makes the loop suppress-only — a hallucinated repo
        # matches no candidate and can never reach the user.
        raw = '{"repos": [{"key": "github:evil/injected", "pitch": ["trust me"]}]}'
        assert prior_art._parse_pitch_response(raw, {"github:acme/auth"}) == {}

    def test_code_fences_are_tolerated(self):
        raw = '```json\n{"repos": [{"key": "github:acme/auth", "pitch": ["x"]}]}\n```'
        assert "github:acme/auth" in prior_art._parse_pitch_response(raw, {"github:acme/auth"})

    def test_bare_list_form_is_accepted(self):
        raw = '[{"key": "github:acme/auth", "pitch": ["x"]}]'
        assert "github:acme/auth" in prior_art._parse_pitch_response(raw, {"github:acme/auth"})

    def test_bullets_are_capped(self):
        raw = '{"repos": [{"key": "k", "pitch": ["a", "b", "c", "d", "e", "f"]}]}'
        assert len(prior_art._parse_pitch_response(raw, {"k"})["k"]["pitch"]) == 4

    def test_garbage_is_empty_not_an_exception(self):
        assert prior_art._parse_pitch_response("not json", {"k"}) == {}
        assert prior_art._parse_pitch_response("", {"k"}) == {}
        assert prior_art._parse_pitch_response('{"repos": "nope"}', {"k"}) == {}


class TestFallbackPitch:
    def test_built_from_facts_the_scan_actually_saw(self):
        c = prior_art.RepoCandidate(
            key="k",
            name="acme/auth",
            description="OIDC login",
            languages=("Python",),
            frameworks=("FastAPI",),
            structure=("tests", "CI"),
            pitch=("Shares your stack: Python",),
        )
        bullets = prior_art._fallback_pitch(c)
        assert bullets[0] == "OIDC login"
        assert any("FastAPI" in b for b in bullets)
        assert any("tests, CI" in b for b in bullets)

    def test_deduped_and_capped(self):
        c = prior_art.RepoCandidate(key="k", name="n", description="dup", pitch=("dup", "dup"))
        assert prior_art._fallback_pitch(c) == ("dup",)

    def test_bare_candidate_yields_nothing_rather_than_filler(self):
        assert prior_art._fallback_pitch(prior_art.RepoCandidate(key="k", name="n")) == ()


class TestPitch:
    def _candidate(self, key="github:acme/auth"):
        return prior_art.RepoCandidate(key=key, name="acme/auth", description="OIDC login")

    def test_llm_bullets_win_over_the_fallback(self, monkeypatch):
        class _Resp:
            content = '{"repos": [{"key": "github:acme/auth", "pitch": ["model bullet"]}]}'

        monkeypatch.setattr("yeaboi.agent.nodes._invoke_json", lambda prompt, **k: _Resp())
        (out,) = prior_art.pitch([self._candidate()], _REQS)
        assert out.pitch == ("model bullet",)

    def test_drop_removes_the_candidate(self, monkeypatch):
        class _Resp:
            content = '{"repos": [{"key": "github:acme/auth", "drop": true}]}'

        monkeypatch.setattr("yeaboi.agent.nodes._invoke_json", lambda prompt, **k: _Resp())
        assert prior_art.pitch([self._candidate()], _REQS) == []

    def test_llm_failure_degrades_to_deterministic_bullets(self, monkeypatch):
        def _boom(prompt, **k):
            raise RuntimeError("no model")

        monkeypatch.setattr("yeaboi.agent.nodes._invoke_json", _boom)
        (out,) = prior_art.pitch([self._candidate()], _REQS)
        assert out.pitch == ("OIDC login",)

    def test_actionable_auth_errors_still_surface(self, monkeypatch):
        def _boom(prompt, **k):
            raise RuntimeError("invalid x-api-key")

        monkeypatch.setattr("yeaboi.agent.nodes._invoke_json", _boom)
        monkeypatch.setattr("yeaboi.agent.nodes._should_reraise_llm_error", lambda exc: True)
        with pytest.raises(RuntimeError):
            prior_art.pitch([self._candidate()], _REQS)

    def test_a_candidate_the_model_ignored_keeps_a_pitch(self, monkeypatch):
        class _Resp:
            content = '{"repos": []}'

        monkeypatch.setattr("yeaboi.agent.nodes._invoke_json", lambda prompt, **k: _Resp())
        (out,) = prior_art.pitch([self._candidate()], _REQS)
        assert out.pitch == ("OIDC login",)

    def test_no_candidates_makes_no_call(self, monkeypatch):
        def _boom(prompt, **k):
            raise AssertionError("must not call the model with nothing to pitch")

        monkeypatch.setattr("yeaboi.agent.nodes._invoke_json", _boom)
        assert prior_art.pitch([], _REQS) == []


class _FakeTool:
    """Stands in for a @tool — enrich only ever calls .invoke()."""

    def __init__(self, result):
        self._result = result
        self.calls: list[dict] = []

    def invoke(self, payload):
        self.calls.append(payload)
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class TestEnrich:
    def _candidate(self, **over):
        base = {"key": "github:acme/auth", "name": "acme/auth", "platform": "github", "url": "https://x/acme/auth"}
        base.update(over)
        return prior_art.RepoCandidate(**base)

    def test_azdo_candidates_pass_through_untouched(self, monkeypatch):
        c = self._candidate(platform="azdo")
        assert prior_art.enrich([c]) == [c]

    def test_a_read_error_keeps_the_stored_row(self, monkeypatch):
        import yeaboi.tools.github as gh

        tool = _FakeTool("Error: 404")
        monkeypatch.setattr(gh, "github_read_repo", tool)
        monkeypatch.setattr(gh, "github_repo_tree", lambda url: ([], "boom"))
        c = self._candidate(languages=("Python",))
        (out,) = prior_art.enrich([c])
        # The tool really was called — an "Error:" string is a soft failure,
        # not an exception, so this must not pass by way of the except clause.
        assert tool.calls == [{"repo_url": "https://x/acme/auth"}]
        assert out.languages == ("Python",)
        assert out.frameworks == ()
        assert out.structure == ()

    def test_a_thrown_exception_is_contained(self, monkeypatch):
        import yeaboi.tools.github as gh

        monkeypatch.setattr(gh, "github_read_repo", _FakeTool(RuntimeError("network gone")))
        monkeypatch.setattr(gh, "github_repo_tree", lambda url: ([], ""))
        (out,) = prior_art.enrich([self._candidate()])
        assert out.key == "github:acme/auth"

    def test_success_path_fills_frameworks_and_structure(self, monkeypatch):
        import yeaboi.tools.github as gh

        summary = "Repository: acme/auth\n\nKey files detected:\n  Dockerfile\n\nLanguages:\n  Python: 100.0%\n"
        monkeypatch.setattr(gh, "github_read_repo", _FakeTool(summary))
        monkeypatch.setattr(gh, "github_repo_tree", lambda url: (["tests/test_a.py", ".github/workflows/ci.yml"], ""))
        (out,) = prior_art.enrich([self._candidate()])
        assert "Docker" in out.frameworks
        assert set(out.structure) >= {"tests", "CI"}

    def test_candidate_without_a_url_is_not_enriched(self):
        c = self._candidate(url="")
        assert prior_art.enrich([c]) == [c]


class TestShortlist:
    def test_no_profile_reports_the_reason(self, monkeypatch):
        monkeypatch.setattr("yeaboi.agent.nodes._load_team_examples", lambda pid: None)
        result = prior_art.shortlist({1: "x"}, profile_id="p")
        assert result.candidates == ()
        assert result.empty_reason == prior_art.EMPTY_NO_PROFILE
        assert "run Team Analysis" in result.message

    def test_old_profile_reports_a_different_reason(self, monkeypatch):
        # Every profile captured before the inventory landed reaches here, so
        # the copy has to tell the user something they can act on.
        monkeypatch.setattr("yeaboi.agent.nodes._load_team_examples", lambda pid: {"sprint_details": []})
        result = prior_art.shortlist({1: "x"}, profile_id="p")
        assert result.empty_reason == prior_art.EMPTY_NO_INVENTORY
        assert "re-run Team Analysis" in result.message

    def test_no_match_reports_the_third_reason(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.agent.nodes._load_team_examples",
            lambda pid: {"repository_inventory": [_row(description="", languages=["COBOL"])]},
        )
        monkeypatch.setattr("yeaboi.agent.prior_art_feedback.load", lambda db_path=None: Ledger())
        result = prior_art.shortlist({1: "booking", 11: "Python"}, profile_id="p")
        assert result.empty_reason == prior_art.EMPTY_NO_MATCH

    def test_happy_path(self, monkeypatch):
        monkeypatch.setattr("yeaboi.agent.nodes._load_team_examples", lambda pid: {"repository_inventory": [_row()]})
        monkeypatch.setattr("yeaboi.agent.prior_art_feedback.load", lambda db_path=None: Ledger())
        monkeypatch.setattr(prior_art, "enrich", lambda cs: cs)
        monkeypatch.setattr(prior_art, "pitch", lambda cs, r, ledger=None: cs)
        result = prior_art.shortlist({1: "booking app", 11: "Python"}, profile_id="p")
        assert [c.key for c in result.candidates] == ["github:acme/auth"]
        assert result.empty_reason == ""
        assert result.message == ""

    def test_model_dropping_everything_reads_as_no_match(self, monkeypatch):
        monkeypatch.setattr("yeaboi.agent.nodes._load_team_examples", lambda pid: {"repository_inventory": [_row()]})
        monkeypatch.setattr("yeaboi.agent.prior_art_feedback.load", lambda db_path=None: Ledger())
        monkeypatch.setattr(prior_art, "enrich", lambda cs: cs)
        monkeypatch.setattr(prior_art, "pitch", lambda cs, r, ledger=None: [])
        assert prior_art.shortlist({1: "booking", 11: "Python"}).empty_reason == prior_art.EMPTY_NO_MATCH

    def test_ledger_feeds_ranking(self, monkeypatch):
        monkeypatch.setattr("yeaboi.agent.nodes._load_team_examples", lambda pid: {"repository_inventory": [_row()]})
        monkeypatch.setattr(
            "yeaboi.agent.prior_art_feedback.load",
            lambda db_path=None: Ledger(
                rejected=frozenset({"github:acme/auth"}),
                examples=(FeedbackExample("down", "acme/auth", "retired"),),
            ),
        )
        assert prior_art.shortlist({1: "booking", 11: "Python"}).empty_reason == prior_art.EMPTY_NO_MATCH


class TestToRef:
    def test_reduces_to_what_the_plan_carries(self):
        c = prior_art.RepoCandidate(
            key="k", name="n", url="u", platform="github", pitch=("a",), languages=("Python",), score=9.0
        )
        ref = prior_art.to_ref(c)
        assert dataclasses.asdict(ref) == {
            "key": "k",
            "name": "n",
            "url": "u",
            "platform": "github",
            "pitch": ("a",),
            "stack": ("Python",),
        }
