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

    def test_free_text_that_means_the_opposite_does_not_match(self):
        """Q2 takes free text, and a substring test reads "not greenfield" as a
        greenfield project."""
        assert not prior_art.applies({2: "not greenfield, existing codebase"})

    def test_free_text_that_does_mean_greenfield_still_matches(self):
        assert prior_art.applies({2: "a greenfield build, brand new"})
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


def _stub_examples(monkeypatch, examples):
    """Stand in for the analysis-profile read. Mirrors the real signature —
    including `db_path`, whose absence used to surface as "no profile" rather
    than as the TypeError it was."""
    monkeypatch.setattr("yeaboi.agent.nodes._load_team_examples", lambda pid, db_path=None: examples)


class TestShortlist:
    def test_no_profile_reports_the_reason(self, monkeypatch):
        monkeypatch.setattr("yeaboi.agent.nodes._load_team_examples", lambda pid, db_path=None: None)
        result = prior_art.shortlist({1: "x"}, profile_id="p")
        assert result.candidates == ()
        assert result.empty_reason == prior_art.EMPTY_NO_PROFILE
        assert "run Team Analysis" in result.message

    def test_old_profile_reports_a_different_reason(self, monkeypatch):
        # Every profile captured before the inventory landed reaches here, so
        # the copy has to tell the user something they can act on.
        monkeypatch.setattr("yeaboi.agent.nodes._load_team_examples", lambda pid, db_path=None: {"sprint_details": []})
        result = prior_art.shortlist({1: "x"}, profile_id="p")
        assert result.empty_reason == prior_art.EMPTY_NO_INVENTORY
        assert "re-run Team Analysis" in result.message

    def test_no_match_reports_the_third_reason(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.agent.nodes._load_team_examples",
            lambda pid, db_path=None: {"repository_inventory": [_row(description="", languages=["COBOL"])]},
        )
        monkeypatch.setattr("yeaboi.agent.prior_art_feedback.load", lambda db_path=None: Ledger())
        result = prior_art.shortlist({1: "booking", 11: "Python"}, profile_id="p")
        assert result.empty_reason == prior_art.EMPTY_NO_MATCH

    def test_happy_path(self, monkeypatch):
        _stub_examples(monkeypatch, {"repository_inventory": [_row()]})
        monkeypatch.setattr("yeaboi.agent.prior_art_feedback.load", lambda db_path=None: Ledger())
        monkeypatch.setattr(prior_art, "enrich", lambda cs: cs)
        monkeypatch.setattr(prior_art, "pitch", lambda cs, r, ledger=None: cs)
        result = prior_art.shortlist({1: "booking app", 11: "Python"}, profile_id="p")
        assert [c.key for c in result.candidates] == ["github:acme/auth"]
        assert result.empty_reason == ""
        assert result.message == ""

    def test_model_dropping_everything_reads_as_no_match(self, monkeypatch):
        _stub_examples(monkeypatch, {"repository_inventory": [_row()]})
        monkeypatch.setattr("yeaboi.agent.prior_art_feedback.load", lambda db_path=None: Ledger())
        monkeypatch.setattr(prior_art, "enrich", lambda cs: cs)
        monkeypatch.setattr(prior_art, "pitch", lambda cs, r, ledger=None: [])
        assert prior_art.shortlist({1: "booking", 11: "Python"}).empty_reason == prior_art.EMPTY_NO_MATCH

    def test_ledger_feeds_ranking(self, monkeypatch):
        _stub_examples(monkeypatch, {"repository_inventory": [_row()]})
        monkeypatch.setattr(
            "yeaboi.agent.prior_art_feedback.load",
            lambda db_path=None: Ledger(
                rejected=frozenset({"github:acme/auth"}),
                examples=(FeedbackExample("down", "acme/auth", "retired"),),
            ),
        )
        assert prior_art.shortlist({1: "booking", 11: "Python"}).empty_reason == prior_art.EMPTY_NO_MATCH


class TestDbPathSeam:
    """`load_candidates(db_path=…)` must actually reach the profile read.

    It used to be accepted and dropped, so a test passing a temporary database
    still read the developer's real `~/.yeaboi/sessions.db` — and because the
    loader is wrapped in a broad `except`, a signature mismatch surfaced as
    "you have no analysis profile" rather than as an error.
    """

    def test_db_path_is_threaded_to_the_profile_read(self, monkeypatch, tmp_path):
        seen: dict = {}

        def _loader(pid, db_path=None):
            seen["pid"] = pid
            seen["db_path"] = db_path
            return {"repository_inventory": []}

        monkeypatch.setattr("yeaboi.agent.nodes._load_team_examples", _loader)
        prior_art.load_candidates("p", db_path=tmp_path / "x.db")
        assert seen == {"pid": "p", "db_path": tmp_path / "x.db"}

    def test_a_loader_that_rejects_the_kwarg_is_not_silently_an_empty_estate(self, monkeypatch):
        """The failure mode this guards: the reason must not read as a verdict
        about the user's repositories when it was our own call that broke."""
        monkeypatch.setattr("yeaboi.agent.nodes._load_team_examples", lambda pid: {"repository_inventory": [_row()]})
        rows, reason = prior_art.load_candidates("p")
        assert rows == [] and reason == prior_art.EMPTY_NO_PROFILE


class TestNameTokens:
    """A repository name is a path of segments, not a sentence.

    Found by running the feature against a real 300-repo Azure DevOps estate:
    every single repository scored 0.0. `_TOKEN_RE` keeps "." as a word
    character so prose like "node.js" survives, which collapsed
    `YL.Web.Api.Internal.Loan` into one unmatchable token.
    """

    def test_a_dotted_pascal_case_name_splits_into_words(self):
        assert prior_art._name_tokens("YL.Web.DeveloperDashboard") == {"web", "developer", "dashboard"}

    def test_acronym_runs_split_before_a_following_word(self):
        assert prior_art._name_tokens("APIGateway") == {"api", "gateway"}

    def test_dashes_and_underscores_split_too(self):
        assert prior_art._name_tokens("tf-transfer_family") == {"transfer", "family"}

    def test_two_letter_segments_are_dropped_as_noise(self):
        # "YL" is a company prefix, not a word about the repository.
        assert "yl" not in prior_art._name_tokens("YL.Domain.Loan")

    def test_a_dotted_name_actually_scores_now(self):
        reqs = prior_art.requirements_from_answers({1: "a dashboard for loan tracking"})
        value, why = prior_art.score({"name": "YL.Web.DeveloperDashboard"}, reqs)
        assert value >= prior_art._MIN_SCORE
        assert "dashboard" in why[0]


class TestCommonNameTokens:
    """One shared word out of a naming convention is not evidence."""

    def _estate(self, n=40):
        return [{"key": f"azdo:o/r{i}", "name": f"YL.Web.Api.Thing{i}"} for i in range(n)]

    def test_convention_words_are_measured_and_dropped(self):
        common = prior_art._common_name_tokens(self._estate())
        assert {"web", "api"} <= common

    def test_a_rare_word_survives(self):
        rows = self._estate()
        rows.append({"key": "azdo:o/loan", "name": "YL.Domain.Loan"})
        assert "loan" not in prior_art._common_name_tokens(rows)

    def test_a_small_estate_infers_nothing(self):
        """Below a handful of repositories there is no convention to measure,
        and every word would look ubiquitous."""
        assert prior_art._common_name_tokens([{"name": "YL.Web.Api"}]) == frozenset()

    def test_ranking_does_not_return_the_whole_estate_on_a_filler_word(self):
        rows = self._estate()
        reqs = prior_art.requirements_from_answers({1: "a new web api"})
        assert prior_art.rank(rows, reqs) == []


class TestLanguagesFromPaths:
    def test_counts_extensions_most_files_first(self):
        languages = prior_art.languages_from_paths(["a.cs", "b.cs", "c.tsx", "d.tf"])
        assert languages[0] == "C#"
        assert set(languages) == {"C#", "TypeScript", "Terraform"}

    def test_unknown_extensions_are_ignored(self):
        assert prior_art.languages_from_paths(["README.md", "LICENSE"]) == ()

    def test_empty_input_is_empty_output(self):
        assert prior_art.languages_from_paths(None) == ()


class TestAzureEnrichment:
    """Azure reports no description, no languages and no push date, so without
    the tree an AzDO candidate reaches the pitch with only its name — and the
    heaviest scoring term is permanently unavailable to an all-Azure estate."""

    def _candidate(self):
        return prior_art.RepoCandidate(
            key="azdo:o/r", name="YL.Domain.Loan", platform="azdo", url="https://dev.azure/x"
        )

    def test_tree_supplies_languages_and_structure(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.tools.azure_devops.azdevops_repo_tree",
            lambda url: (["src/Loan.cs", "src/B.cs", "tests/LoanTests.cs", ".github/workflows/ci.yml"], ""),
        )
        (out,) = prior_art.enrich([self._candidate()])
        assert out.languages == ("C#",)
        assert "tests" in out.structure

    def test_a_failed_tree_keeps_the_stored_row(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.tools.azure_devops.azdevops_repo_tree",
            lambda url: ([], "Error: no access"),
        )
        (out,) = prior_art.enrich([self._candidate()])
        assert out == self._candidate()

    def test_a_raising_tree_does_not_lose_the_candidate(self, monkeypatch):
        def _boom(url):
            raise RuntimeError("azure down")

        monkeypatch.setattr("yeaboi.tools.azure_devops.azdevops_repo_tree", _boom)
        (out,) = prior_art.enrich([self._candidate()])
        assert out.key == "azdo:o/r"

    def test_a_candidate_with_no_url_is_left_alone(self, monkeypatch):
        def _boom(url):
            raise AssertionError("must not reach the network without a URL")

        monkeypatch.setattr("yeaboi.tools.azure_devops.azdevops_repo_tree", _boom)
        bare = dataclasses.replace(self._candidate(), url="")
        assert prior_art.enrich([bare]) == [bare]
