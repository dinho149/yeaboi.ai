"""Unit tests for the transcript review pipeline (extraction → clamps → diagnosis)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from yeaboi.agent.state import ActivityEvidence, MemberUpdate, StandupReport, TranscriptSource
from yeaboi.standup import transcript_review, transcripts

FIXTURES = Path(__file__).parent.parent / "fixtures" / "transcripts"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sessions.db"


@pytest.fixture
def managed(tmp_path, monkeypatch):
    d = tmp_path / "transcripts"
    d.mkdir()
    monkeypatch.setattr("yeaboi.paths.TRANSCRIPTS_DIR", d)
    return d


@pytest.fixture
def llm_on(monkeypatch):
    monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))


def _report(**over) -> StandupReport:
    base = dict(
        date="2026-07-30",
        session_id="s1",
        my_name="Alice",
        team_summary="the team shipped login",
        member_updates=(
            MemberUpdate(
                name="Alice",
                summary="shipped the login redirect",
                ticketing_summary="closed YB-12",
                ticketing_evidence=(ActivityEvidence(kind="issue", key="YB-12", title="Login redirect"),),
            ),
            MemberUpdate(name="Bob", summary="reviewed two PRs"),
        ),
        activity_counts=(("jira", 3), ("github", 5), ("confluence", 2)),
    )
    base.update(over)
    return StandupReport(**base)


TRANSCRIPT = (
    "Alice: Morning. I finished the login redirect and moved YB-12 to done.\n"
    "Bob: Alice, did you do YB-12 like the report says?\n"
    "Alice: Yes, but I also commented on the design doc.\n"
)


def _sources(text: str = TRANSCRIPT, **over):
    base = dict(
        path="/tmp/t.txt",
        filename="t.txt",
        fmt="txt",
        covered_date="2026-07-30",
        speakers=("Alice", "Bob"),
        attribution="labelled",
    )
    base.update(over)
    return [(TranscriptSource(**base), transcripts.parse(text, "txt"))]


def _mock_llm(monkeypatch, payload):
    """Patch invoke_json where transcript_review imports it from."""

    class _Response:
        content = payload if isinstance(payload, str) else json.dumps(payload)

    monkeypatch.setattr("yeaboi.agent.llm.invoke_json", lambda *a, **k: _Response())


# ---------------------------------------------------------------------------
# Speaker attribution
# ---------------------------------------------------------------------------


class TestResolveSpeakers:
    def test_exact_name(self):
        assert transcript_review.resolve_speakers(("Alice",), {"Alice": {"alice"}}) == {"Alice": "Alice"}

    def test_alias_match(self):
        """A speaker labelled by their GitHub handle still resolves."""
        resolved = transcript_review.resolve_speakers(("acurtis",), {"Alice Curtis": {"alice curtis", "acurtis"}})
        assert resolved == {"acurtis": "Alice Curtis"}

    def test_unique_first_token(self):
        resolved = transcript_review.resolve_speakers(
            ("Alexandru",), {"Alexandru Popescu": {"alexandru popescu"}, "Bob Jones": {"bob jones"}}
        )
        assert resolved == {"Alexandru": "Alexandru Popescu"}

    def test_ambiguous_first_token_is_not_resolved(self):
        """A mis-attributed claim would file an issue against the wrong evidence."""
        resolved = transcript_review.resolve_speakers(
            ("Alex",), {"Alex Popescu": {"alex popescu"}, "Alex Jones": {"alex jones"}}
        )
        assert resolved == {}

    def test_unknown_speaker_is_not_resolved(self):
        assert transcript_review.resolve_speakers(("Stranger",), {"Alice": {"alice"}}) == {}


# ---------------------------------------------------------------------------
# Clamps
# ---------------------------------------------------------------------------


class TestClampClaims:
    def _clamp(self, raw, **over):
        kwargs = dict(report=_report(), transcript_text=TRANSCRIPT)
        kwargs.update(over)
        return transcript_review.clamp_claims(raw, **kwargs)

    def test_keeps_a_grounded_claim(self):
        claims, _ = self._clamp(
            [
                {
                    "member": "Alice",
                    "claim": "commented on the design doc",
                    "quote": "I also commented on the design doc",
                    "status": "missing",
                    "system_hint": "confluence",
                    "artifact_hint": "comment on a page",
                }
            ]
        )
        assert len(claims) == 1
        assert claims[0].member == "Alice"

    def test_drops_a_hallucinated_quote(self):
        """The strongest guard: a quote not in the transcript is invented."""
        claims, notes = self._clamp(
            [{"member": "Alice", "claim": "x", "quote": "I deployed to production", "status": "missing"}]
        )
        assert claims == ()
        assert any("could not be verified" in n for n in notes)

    def test_quote_matching_ignores_whitespace_differences(self):
        claims, _ = self._clamp(
            [
                {
                    "member": "Alice",
                    "claim": "x",
                    "quote": "I  also   commented\non the design doc",
                    "status": "missing",
                }
            ]
        )
        assert len(claims) == 1

    def test_drops_an_unknown_member(self):
        claims, _ = self._clamp(
            [{"member": "Mallory", "claim": "x", "quote": "I also commented on the design doc", "status": "missing"}]
        )
        assert claims == ()

    def test_drops_unclear_claims(self):
        claims, _ = self._clamp(
            [{"member": "Alice", "claim": "x", "quote": "I also commented on the design doc", "status": "unclear"}]
        )
        assert claims == ()

    def test_unrecognised_status_becomes_unclear_and_is_dropped(self):
        claims, _ = self._clamp(
            [{"member": "Alice", "claim": "x", "quote": "I also commented on the design doc", "status": "probably"}]
        )
        assert claims == ()

    def test_missing_is_upgraded_to_matched_when_the_key_is_in_evidence(self):
        """The model's verdict never survives where a key can be re-checked."""
        claims, _ = self._clamp(
            [
                {
                    "member": "Alice",
                    "claim": "closed YB-12",
                    "quote": "I finished the login redirect and moved YB-12 to done",
                    "status": "missing",
                    "matched_key": "YB-12",
                }
            ]
        )
        assert claims[0].status == "matched"

    def test_key_does_not_confirm_an_unfetched_artifact(self):
        """ "I logged six hours against YB-12" is not confirmed by YB-12 being in
        the evidence: the ticket is tracked, the worklog is not."""
        claims, _ = self._clamp(
            [
                {
                    "member": "Alice",
                    "claim": "logged six hours against YB-12",
                    "quote": "I finished the login redirect and moved YB-12 to done",
                    "status": "matched",
                    "matched_key": "YB-12",
                    "system_hint": "jira",
                    "artifact_hint": "time logged against the ticket",
                }
            ]
        )
        assert claims[0].status == "missing"

    def test_key_still_confirms_a_fetched_artifact(self):
        claims, _ = self._clamp(
            [
                {
                    "member": "Alice",
                    "claim": "closed YB-12",
                    "quote": "I finished the login redirect and moved YB-12 to done",
                    "status": "missing",
                    "matched_key": "YB-12",
                    "system_hint": "jira",
                    "artifact_hint": "moved the ticket to done",
                }
            ]
        )
        assert claims[0].status == "matched"

    def test_matched_is_downgraded_when_the_key_is_absent(self):
        claims, _ = self._clamp(
            [
                {
                    "member": "Alice",
                    "claim": "closed YB-99",
                    "quote": "I finished the login redirect and moved YB-12 to done",
                    "status": "matched",
                    "matched_key": "YB-99",
                }
            ]
        )
        assert claims[0].status == "missing"

    def test_per_member_cap(self):
        raw = [
            {
                "member": "Alice",
                "claim": f"thing {i}",
                "quote": "I also commented on the design doc",
                "status": "missing",
            }
            for i in range(12)
        ]
        claims, _ = self._clamp(raw)
        assert len(claims) == transcript_review._MAX_CLAIMS_PER_MEMBER

    def test_speaker_map_translates_labels(self):
        claims, _ = self._clamp(
            [{"member": "acurtis", "claim": "x", "quote": "I also commented on the design doc", "status": "missing"}],
            speaker_map={"acurtis": "Alice"},
        )
        assert claims[0].member == "Alice"

    def test_non_dict_entries_ignored(self):
        claims, _ = self._clamp(["nonsense", 42, None])
        assert claims == ()

    def test_a_model_invented_root_cause_field_is_ignored(self):
        """Diagnosis is not the model's job; stray fields must not leak through."""
        claims, _ = self._clamp(
            [
                {
                    "member": "Alice",
                    "claim": "x",
                    "quote": "I also commented on the design doc",
                    "status": "missing",
                    "root_cause": "yeaboi is broken",
                    "category": "integration_missing",
                }
            ]
        )
        assert len(claims) == 1
        assert not hasattr(claims[0], "root_cause")


# ---------------------------------------------------------------------------
# The end-to-end review
# ---------------------------------------------------------------------------


class TestReviewTranscripts:
    def test_happy_path_produces_a_product_gap(self, monkeypatch, llm_on):
        _mock_llm(
            monkeypatch,
            {
                "claims": [
                    {
                        "member": "Alice",
                        "claim": "closed YB-12",
                        "quote": "I finished the login redirect and moved YB-12 to done",
                        "status": "matched",
                        "matched_key": "YB-12",
                        "system_hint": "jira",
                        "artifact_hint": "ticket",
                    },
                    {
                        "member": "Alice",
                        "claim": "commented on the design doc",
                        "quote": "I also commented on the design doc",
                        "status": "missing",
                        "system_hint": "confluence",
                        "artifact_hint": "comment on a page",
                    },
                ]
            },
        )
        review = transcript_review.review_transcripts(
            "s1", report=_report(), sources=_sources(), standup_date="2026-07-30"
        )
        assert review.llm_mode == "llm"
        assert review.claims_matched == 1
        assert review.claims_missing == 1
        assert [g.category for g in review.gaps] == ["capability_gap_in_supported_source"]
        assert review.gaps[0].scope == "product"

    def test_config_gap_becomes_a_suggestion_not_an_issue(self, monkeypatch, llm_on):
        _mock_llm(
            monkeypatch,
            {
                "claims": [
                    {
                        "member": "Alice",
                        "claim": "opened a PR in acme/infra",
                        "quote": "I also commented on the design doc",
                        "status": "missing",
                        "system_hint": "github",
                        "artifact_hint": "pull request in acme/infra",
                    }
                ]
            },
        )
        review = transcript_review.review_transcripts(
            "s1",
            report=_report(),
            sources=_sources(),
            config={"github_repositories": ["acme/web"]},
            standup_date="2026-07-30",
        )
        assert review.gaps == ()
        assert [g.category for g in review.config_suggestions] == ["scope_gap_repository"]
        assert review.config_suggestions[0].remedy

    def test_untracked_work_is_counted_not_reported_as_a_defect(self, monkeypatch, llm_on):
        _mock_llm(
            monkeypatch,
            {
                "claims": [
                    {
                        "member": "Bob",
                        "claim": "paired with Alice",
                        "quote": "Alice, did you do YB-12 like the report says?",
                        "status": "missing",
                        "system_hint": "none",
                        "artifact_hint": "pairing session",
                    }
                ]
            },
        )
        review = transcript_review.review_transcripts(
            "s1", report=_report(), sources=_sources(), standup_date="2026-07-30"
        )
        assert review.gaps == ()
        assert review.untracked_count == 1
        assert "no digital footprint" in review.accuracy_note

    def test_fenced_json_parses(self, monkeypatch, llm_on):
        _mock_llm(monkeypatch, '```json\n{"claims": []}\n```')
        review = transcript_review.review_transcripts(
            "s1", report=_report(), sources=_sources(), standup_date="2026-07-30"
        )
        assert review.llm_mode == "llm"
        assert review.claims == ()

    def test_unparseable_response_degrades(self, monkeypatch, llm_on):
        _mock_llm(monkeypatch, "I'm afraid I can't do that")
        review = transcript_review.review_transcripts(
            "s1", report=_report(), sources=_sources(), standup_date="2026-07-30"
        )
        assert review.claims == ()
        assert review.gaps == ()

    def test_no_llm_degrades_with_a_warning(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no API key"))
        review = transcript_review.review_transcripts(
            "s1", report=_report(), sources=_sources(), standup_date="2026-07-30"
        )
        assert review.llm_mode == "deterministic"
        assert any("no API key" in w for w in review.warnings)

    def test_auth_error_never_raises(self, monkeypatch, llm_on):
        import anthropic

        def _boom(*a, **k):
            raise anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)

        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", _boom)
        review = transcript_review.review_transcripts(
            "s1", report=_report(), sources=_sources(), standup_date="2026-07-30"
        )
        assert review.llm_mode == "deterministic"
        assert any("billing" in w for w in review.warnings)

    def test_generic_llm_error_never_raises(self, monkeypatch, llm_on):
        def _boom(*a, **k):
            raise RuntimeError("connection reset")

        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", _boom)
        review = transcript_review.review_transcripts(
            "s1", report=_report(), sources=_sources(), standup_date="2026-07-30"
        )
        assert review.llm_mode == "deterministic"

    def test_no_report_for_the_date_says_so(self, monkeypatch, llm_on):
        review = transcript_review.review_transcripts("s1", report=None, sources=_sources(), standup_date="2026-07-30")
        assert review.llm_mode == "deterministic"
        assert any("No standup run found" in w for w in review.warnings)

    def test_empty_transcript_says_so(self, monkeypatch, llm_on):
        review = transcript_review.review_transcripts(
            "s1", report=_report(), sources=_sources(""), standup_date="2026-07-30"
        )
        assert any("no readable speech" in w for w in review.warnings)

    def test_truncated_source_is_surfaced(self, monkeypatch, llm_on):
        _mock_llm(monkeypatch, {"claims": []})
        review = transcript_review.review_transcripts(
            "s1", report=_report(), sources=_sources(truncated=True), standup_date="2026-07-30"
        )
        assert any("longer than the read limit" in w for w in review.warnings)

    def test_gap_cap_applied(self, monkeypatch, llm_on):
        # Many distinct unsupported systems → many distinct gaps.
        systems = ["slack", "teams", "linear", "gitlab", "bitbucket", "figma", "miro", "sentry", "datadog", "trello"]
        _mock_llm(
            monkeypatch,
            {
                "claims": [
                    {
                        "member": "Alice",
                        "claim": f"work in {s}",
                        "quote": "I also commented on the design doc",
                        "status": "missing",
                        "system_hint": s,
                        "artifact_hint": "message",
                    }
                    for s in systems
                ]
            },
        )
        review = transcript_review.review_transcripts(
            "s1", report=_report(), sources=_sources(), standup_date="2026-07-30"
        )
        assert len(review.gaps) <= transcript_review._MAX_GAPS_PER_REVIEW

    def test_repeated_claims_collapse_into_one_gap(self, monkeypatch, llm_on):
        _mock_llm(
            monkeypatch,
            {
                "claims": [
                    {
                        "member": name,
                        "claim": "commented on a page",
                        "quote": "I also commented on the design doc",
                        "status": "missing",
                        "system_hint": "confluence",
                        "artifact_hint": "comment on a page",
                    }
                    for name in ("Alice", "Bob")
                ]
            },
        )
        review = transcript_review.review_transcripts(
            "s1", report=_report(), sources=_sources(), standup_date="2026-07-30"
        )
        assert len(review.gaps) == 1
        assert set(review.gaps[0].members) == {"Alice", "Bob"}


class TestMemberPayload:
    def test_small_team_gets_full_evidence_depth(self):
        report = _report(
            member_updates=(
                MemberUpdate(
                    name="Alice",
                    code_evidence=tuple(ActivityEvidence(kind="commit", key=f"s{i}") for i in range(40)),
                ),
            )
        )
        payload = transcript_review._member_payload(report)
        assert len(payload[0]["evidence"]) == transcript_review._EVIDENCE_PER_MEMBER

    def test_large_roster_is_bounded_in_total(self):
        """This call sits on the standup critical path — a big team must not
        double the prompt."""
        report = _report(
            member_updates=tuple(
                MemberUpdate(
                    name=f"Eng {n}",
                    code_evidence=tuple(ActivityEvidence(kind="commit", key=f"s{i}") for i in range(40)),
                )
                for n in range(20)
            )
        )
        payload = transcript_review._member_payload(report)
        total = sum(len(m["evidence"]) for m in payload)
        assert total <= transcript_review._MAX_TOTAL_EVIDENCE
        # …but never so thin that recognition becomes impossible.
        assert all(len(m["evidence"]) >= transcript_review._MIN_EVIDENCE_PER_MEMBER for m in payload)

    def test_urls_are_stripped(self):
        report = _report(
            member_updates=(
                MemberUpdate(
                    name="Alice",
                    code_evidence=(ActivityEvidence(kind="pr", key="#1", url="https://example/1"),),
                ),
            )
        )
        payload = transcript_review._member_payload(report)
        assert "url" not in payload[0]["evidence"][0]


class TestNeverTouchesGitHub:
    def test_module_does_not_import_gap_issues(self):
        """Structural guarantee behind "draft, then confirm": the drafting path
        contains no code that could file an issue, even by accident.

        (The runtime counterpart — monkeypatching gap_issues.file_gap to explode
        and driving a full review — lives in test_standup_gap_issues.py.)
        """
        import ast

        tree = ast.parse(Path(transcript_review.__file__).read_text())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(a.name for a in node.names)
        assert not any("gap_issues" in name for name in imported), sorted(imported)


# ---------------------------------------------------------------------------
# Sweep + carry-forward
# ---------------------------------------------------------------------------


class TestSweepAndReview:
    def test_reviews_and_persists(self, managed, db_path, monkeypatch, llm_on):
        from yeaboi.standup.store import StandupStore

        (managed / "2026-07-30-standup.txt").write_text(TRANSCRIPT)
        _mock_llm(
            monkeypatch,
            {
                "claims": [
                    {
                        "member": "Alice",
                        "claim": "commented on a page",
                        "quote": "I also commented on the design doc",
                        "status": "missing",
                        "system_hint": "confluence",
                        "artifact_hint": "comment on a page",
                    }
                ]
            },
        )
        with StandupStore(db_path) as store:
            store.record_run(_report())

        reviews = transcript_review.sweep_and_review(
            "s1", db_path=db_path, today=date(2026, 8, 1), before_date="2026-07-31"
        )
        assert len(reviews) == 1
        assert reviews[0].review_id > 0
        assert reviews[0].run_id > 0
        assert reviews[0].gaps

        with StandupStore(db_path) as store:
            assert store.get_latest_review("s1") is not None
            assert store.reviewed_transcript_hashes("s1")
            ledger = store.get_gap_issues()
        assert ledger[0]["state"] == "drafted"

    def test_second_sweep_skips_the_reviewed_transcript(self, managed, db_path, monkeypatch, llm_on):
        (managed / "2026-07-30-standup.txt").write_text(TRANSCRIPT)
        _mock_llm(monkeypatch, {"claims": []})
        first = transcript_review.sweep_and_review(
            "s1", db_path=db_path, today=date(2026, 8, 1), before_date="2026-07-31"
        )
        second = transcript_review.sweep_and_review(
            "s1", db_path=db_path, today=date(2026, 8, 1), before_date="2026-07-31"
        )
        assert len(first) == 1
        assert second == []

    def test_groups_by_date_one_review_each(self, managed, db_path, monkeypatch, llm_on):
        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as store:
            for day in (28, 29, 30):
                store.record_run(_report(date=f"2026-07-{day}"))
        for day in (28, 29, 30):
            (managed / f"2026-07-{day}-standup.txt").write_text(TRANSCRIPT)
        calls = []
        monkeypatch.setattr(
            "yeaboi.agent.llm.invoke_json",
            lambda *a, **k: calls.append(1) or type("R", (), {"content": '{"claims": []}'})(),
        )
        reviews = transcript_review.sweep_and_review(
            "s1", db_path=db_path, today=date(2026, 8, 1), before_date="2026-07-31"
        )
        assert len(reviews) == 3
        assert len(calls) == 3  # one LLM call per DATE, not per file

    def test_max_dates_defers_the_rest(self, managed, db_path, monkeypatch, llm_on):
        for day in (26, 27, 28, 29, 30):
            (managed / f"2026-07-{day}-standup.txt").write_text(TRANSCRIPT)
        _mock_llm(monkeypatch, {"claims": []})
        reviews = transcript_review.sweep_and_review(
            "s1", db_path=db_path, today=date(2026, 8, 1), before_date="2026-07-31", max_dates=2
        )
        assert len(reviews) == 2
        assert any("next run" in w for w in reviews[0].warnings)

    def test_nothing_to_review_returns_empty(self, managed, db_path):
        assert transcript_review.sweep_and_review("s1", db_path=db_path, today=date(2026, 8, 1)) == []

    def test_unreadable_file_is_not_fatal(self, managed, db_path, monkeypatch, llm_on):
        (managed / "2026-07-30-standup.txt").write_text(TRANSCRIPT)
        monkeypatch.setattr(transcripts, "read_transcript", lambda *a, **k: (_ for _ in ()).throw(OSError("disk gone")))
        assert transcript_review.sweep_and_review("s1", db_path=db_path, today=date(2026, 8, 1)) == []


class TestCarryForward:
    def _review(self, **over):
        from yeaboi.agent.state import StandupGap, TranscriptClaim, TranscriptReview

        base = dict(
            standup_date="2026-07-30",
            claims=(
                TranscriptClaim(member="Alice", claim="commented on the design doc", status="missing"),
                TranscriptClaim(member="Alice", claim="closed YB-12", status="matched"),
            ),
            gaps=(StandupGap(title="Standup misses Confluence comments", scope="product"),),
            config_suggestions=(StandupGap(title="acme/infra out of scope", scope="config", remedy="Add it."),),
        )
        base.update(over)
        return TranscriptReview(**base)

    def test_missing_claims_become_corrections_for_that_date(self):
        corrections, _ = transcript_review.carry_forward([self._review()], _report(date="2026-07-30"))
        assert corrections == {"Alice": ["commented on the design doc"]}

    def test_matched_claims_are_not_corrections(self):
        corrections, _ = transcript_review.carry_forward([self._review()], _report(date="2026-07-30"))
        assert "closed YB-12" not in corrections["Alice"]

    def test_other_dates_do_not_carry_forward(self):
        corrections, _ = transcript_review.carry_forward([self._review()], _report(date="2026-07-29"))
        assert corrections == {}

    def test_corrections_are_capped_per_member(self):
        from yeaboi.agent.state import TranscriptClaim

        claims = tuple(TranscriptClaim(member="Alice", claim=f"thing {i}", status="missing") for i in range(10))
        corrections, _ = transcript_review.carry_forward([self._review(claims=claims)], _report(date="2026-07-30"))
        assert len(corrections["Alice"]) == 3

    def test_gaps_and_suggestions_become_warnings(self):
        _, warnings = transcript_review.carry_forward([self._review()], _report(date="2026-07-30"))
        assert any("Confluence comments" in w and "issue drafted" in w for w in warnings)
        assert any("acme/infra" in w and "Add it." in w for w in warnings)

    def test_no_reviews_is_empty(self):
        assert transcript_review.carry_forward([], _report()) == ({}, [])

    def test_no_previous_report_still_yields_warnings(self):
        corrections, warnings = transcript_review.carry_forward([self._review()], None)
        assert corrections == {}
        assert warnings


class TestUnclassifiedIsReported:
    """A claim the ladder cannot diagnose is counted, not quietly discarded."""

    def test_counted_in_the_accuracy_note(self, monkeypatch, llm_on):
        _mock_llm(
            monkeypatch,
            {
                "claims": [
                    {
                        "member": "Alice",
                        "claim": "did a thing",
                        "quote": "I also commented on the design doc",
                        "status": "missing",
                        "system_hint": "unknown",
                        "artifact_hint": "a thing",
                    }
                ]
            },
        )
        review = transcript_review.review_transcripts(
            "s1", report=_report(), sources=_sources(), standup_date="2026-07-30"
        )
        assert review.gaps == ()
        assert "could not attribute to a cause" in review.accuracy_note

    def test_diagnose_returns_the_unclassified_count(self):
        from yeaboi.agent.state import TranscriptClaim

        claims = (TranscriptClaim(member="Alice", status="missing", system_hint="unknown", artifact_hint="a thing"),)
        _gaps, _suggestions, _untracked, unclassified = transcript_review.diagnose(claims, report=_report())
        assert unclassified == 1
