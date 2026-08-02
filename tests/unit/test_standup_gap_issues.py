"""Unit tests for filing standup gaps as GitHub issues.

The repo these are filed to is PUBLIC, so the redaction tests here are not
hygiene — they are the feature working correctly.
"""

from __future__ import annotations

import pytest

from yeaboi.agent.state import (
    ActivityEvidence,
    MemberUpdate,
    StandupGap,
    StandupReport,
    TranscriptClaim,
    TranscriptReview,
    TranscriptSource,
)
from yeaboi.standup import gap_issues


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sessions.db"


@pytest.fixture
def no_token(monkeypatch):
    monkeypatch.setattr("yeaboi.config.get_github_token", lambda: None)


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "ghp_" + "x" * 36)


class FakeIssue:
    def __init__(self, number=1, state="open", body="", title=""):
        self.number = number
        self.state = state
        self.body = body
        self.title = title
        self.html_url = f"https://github.com/omardin14/yeaboi.ai/issues/{number}"
        self.comments: list[str] = []

    def create_comment(self, body):
        self.comments.append(body)
        return object()


class FakeRepo:
    def __init__(self, issues=None):
        self.issues = issues or []
        self.created: list[dict] = []

    def create_issue(self, title, body, labels):
        self.created.append({"title": title, "body": body, "labels": labels})
        issue = FakeIssue(number=100 + len(self.created), body=body, title=title)
        self.issues.append(issue)
        return issue

    def get_issue(self, number):
        for issue in self.issues:
            if issue.number == number:
                return issue
        raise ValueError(f"no issue {number}")

    def get_issues(self, state="open", labels=None):
        return list(self.issues)


@pytest.fixture
def repo(monkeypatch, token):
    fake = FakeRepo()
    monkeypatch.setattr(gap_issues, "_repo", lambda: fake)
    return fake


def _gap(**over) -> StandupGap:
    base = dict(
        fingerprint="abc123def456",
        category="capability_gap_in_supported_source",
        scope="product",
        title="Standup misses Confluence comments",
        detail="Confluence is connected, but standup never fetches comments from it.",
        root_cause="The Confluence collector reads page edits but not comments.",
        priority="high",
        confidence="high",
        feedback_kind="Feature",
        members=("Alice Curtis",),
        claims=(
            TranscriptClaim(
                member="Alice Curtis",
                claim="commented on the design doc",
                quote="Yes, but I also commented on the design doc",
                status="missing",
                source_path="/tmp/t.vtt",
            ),
        ),
        evidence=("Confluence was scanned successfully.",),
        next_steps=("Fetch page comments in standup/collector.py",),
        affected_systems=("confluence",),
    )
    base.update(over)
    return StandupGap(**base)


def _review(**over) -> TranscriptReview:
    base = dict(
        review_id=7,
        session_id="s1",
        standup_date="2026-07-30",
        sources=(TranscriptSource(path="/tmp/t.vtt", filename="t.vtt", fmt="vtt"),),
        claims=(TranscriptClaim(member="Alice Curtis", quote="Yes, but I also commented on the design doc"),),
        gaps=(_gap(),),
    )
    base.update(over)
    return TranscriptReview(**base)


def _report(**over) -> StandupReport:
    base = dict(
        date="2026-07-30",
        activity_window="Wed 2026-07-29 00:00 → now",
        member_updates=(
            MemberUpdate(
                name="Alice Curtis",
                summary="shipped the login redirect",
                documentation_summary="No documentation activity detected.",
                ticketing_evidence=(ActivityEvidence(kind="issue", key="YB-12"),),
            ),
        ),
        activity_counts=(("jira", 3), ("confluence", 2)),
        category_coverage=(("documentation", "covered"),),
    )
    base.update(over)
    return StandupReport(**base)


# ---------------------------------------------------------------------------
# Redaction — the load-bearing safety property
# ---------------------------------------------------------------------------


class TestNameMasking:
    def test_members_become_engineer_labels(self):
        mask = gap_issues.name_mask(_review())
        assert mask["Alice Curtis"] == "Engineer A"

    def test_labels_are_stable_and_distinct(self):
        review = _review(
            claims=(
                TranscriptClaim(member="Alice Curtis"),
                TranscriptClaim(member="Bob Jones"),
            )
        )
        mask = gap_issues.name_mask(review)
        assert set(mask.values()) == {"Engineer A", "Engineer B"}
        assert gap_issues.name_mask(review) == mask

    def test_beyond_26_members_does_not_wrap(self):
        review = _review(claims=tuple(TranscriptClaim(member=f"Person{i:02d}") for i in range(30)), gaps=())
        mask = gap_issues.name_mask(review)
        assert len(mask) == 30
        assert len(set(mask.values())) == 30  # no two people share a label

    def test_scrub_replaces_full_name_before_first_name(self):
        mask = {"Alice Curtis": "Engineer A", "Alice": "Engineer B"}
        assert gap_issues.scrub("Alice Curtis shipped it", mask) == "Engineer A shipped it"


class TestScrubGivenNames:
    """Summaries are LLM-written and use given names — masking only the full
    name leaked first names onto a public repo."""

    def test_masks_a_bare_given_name(self):
        mask = {"Omar Din": "Engineer F"}
        assert gap_issues.scrub("Omar also reviewed the IRSA trust PR", mask) == (
            "Engineer F also reviewed the IRSA trust PR"
        )

    def test_full_name_still_wins_over_the_token(self):
        mask = {"Omar Din": "Engineer F"}
        assert gap_issues.scrub("Omar Din shipped it", mask) == "Engineer F shipped it"

    def test_surnames_are_not_expanded(self):
        """A teammate called Main must not corrupt "merged into main"."""
        mask = {"Nikolai Main": "Engineer E"}
        out = gap_issues.scrub("Nikolai merged the PR into main", mask)
        assert out == "Engineer E merged the PR into main"

    def test_technical_given_names_are_left_alone(self):
        mask = {"Main Person": "Engineer A"}
        assert "main" in gap_issues.scrub("merged into main", mask)

    def test_short_given_names_are_not_expanded(self):
        mask = {"Bo Li": "Engineer A"}
        assert gap_issues.scrub("the bo tree", mask) == "the bo tree"

    def test_substring_of_a_longer_word_is_untouched(self):
        mask = {"Omar Din": "Engineer F"}
        assert "Omarion" in gap_issues.scrub("Omarion is a different word", mask)


class TestScrub:
    def test_relativizes_home(self):
        from pathlib import Path

        text = f"see {Path.home()}/notes.txt"
        assert str(Path.home()) not in gap_issues.scrub(text, {})

    def test_redacts_secrets(self):
        scrubbed = gap_issues.scrub("token is ghp_" + "a" * 36, {})
        assert "ghp_" not in scrubbed
        assert "REDACTED" in scrubbed


class TestLeakCheck:
    @pytest.mark.parametrize(
        "text",
        [
            "contact alice@example.com about it",
            "call 555 123 4567",
            "api_key: " + "x" * 12,
            "password = supersecretvalue",
        ],
    )
    def test_blocks_obvious_leaks(self, text):
        assert gap_issues.leak_check(text)

    def test_allows_a_clean_body(self):
        assert gap_issues.leak_check("Engineer A said they commented on the design doc.") == ""

    def test_does_not_trip_on_its_own_redaction_marker(self):
        assert gap_issues.leak_check("token is [REDACTED]") == ""


class TestIssueBody:
    def test_carries_the_fingerprint_marker(self):
        body = gap_issues.build_gap_issue_body(_gap(), _review())
        assert "<!-- yeaboi-gap: abc123def456 -->" in body

    def test_marker_round_trips_through_the_scan_regex(self):
        body = gap_issues.build_gap_issue_body(_gap(), _review())
        assert gap_issues._MARKER_RE.search(body).group(1) == "abc123def456"

    def test_includes_root_cause_quote_and_next_steps(self):
        body = gap_issues.build_gap_issue_body(_gap(), _review(), report=_report())
        assert "The Confluence collector reads page edits but not comments." in body
        assert "I also commented on the design doc" in body
        assert "Fetch page comments in standup/collector.py" in body

    def test_masks_member_names(self):
        """The repo is public — teammates' names must not appear."""
        body = gap_issues.build_gap_issue_body(_gap(), _review(), report=_report())
        assert "Alice" not in body
        assert "Engineer A" in body

    def test_includes_what_the_report_said(self):
        body = gap_issues.build_gap_issue_body(_gap(), _review(), report=_report())
        assert "No documentation activity detected." in body
        assert "Wed 2026-07-29 00:00" in body

    def test_includes_configuration_at_the_time(self):
        body = gap_issues.build_gap_issue_body(_gap(), _review(), report=_report())
        assert "jira (3)" in body
        assert "documentation=covered" in body

    def test_no_report_degrades_readably(self):
        body = gap_issues.build_gap_issue_body(_gap(), _review(), report=None)
        assert "No standup run was found" in body

    def test_records_recurrence_and_a_closed_predecessor(self):
        body = gap_issues.build_gap_issue_body(_gap(), _review(), occurrences=3, previous_issue=42)
        assert "Seen 3 time(s)" in body
        assert "#42" in body

    def test_quote_is_clipped(self):
        long_quote = "I also " + ("said a lot " * 60)
        gap = _gap(claims=(TranscriptClaim(member="Alice Curtis", quote=long_quote),))
        review = _review(claims=(TranscriptClaim(member="Alice Curtis", quote=long_quote),))
        body = gap_issues.build_gap_issue_body(gap, review)
        quoted = [line for line in body.splitlines() if line.startswith("> I also")][0]
        assert len(quoted) <= gap_issues._QUOTE_CLIP + 4

    def test_never_contains_the_whole_transcript(self):
        body = gap_issues.build_gap_issue_body(_gap(), _review(), report=_report())
        assert "Morning everyone" not in body


class TestCommentBody:
    def test_states_the_recurrence_with_fresh_evidence(self):
        body = gap_issues.build_gap_comment_body(_gap(), _review(), occurrences=4)
        assert "2026-07-30" in body
        assert "occurrence 4" in body
        assert "I also commented on the design doc" in body

    def test_masks_names(self):
        assert "Alice" not in gap_issues.build_gap_comment_body(_gap(), _review())


# ---------------------------------------------------------------------------
# Filing
# ---------------------------------------------------------------------------


class TestFileGap:
    def test_token_path_creates_an_issue(self, repo):
        link = gap_issues.file_gap(_gap(), _review(), report=_report())
        assert link.state == "filed"
        assert link.via == "api"
        assert link.issue_number == 101
        assert len(repo.created) == 1
        assert repo.created[0]["title"].startswith("[Feature]")

    def test_labels_include_the_source_and_category(self, repo):
        gap_issues.file_gap(_gap(), _review())
        labels = repo.created[0]["labels"]
        assert gap_issues.GAP_LABEL in labels
        assert "gap:capability_gap_in_supported_source" in labels
        assert "area:standup" in labels

    def test_api_failure_never_raises(self, monkeypatch, token):
        class Boom:
            def create_issue(self, **kwargs):
                raise RuntimeError("rate limited")

        monkeypatch.setattr(gap_issues, "_repo", lambda: Boom())
        link = gap_issues.file_gap(_gap(), _review())
        assert link.state == "failed"
        assert "rate limited" in link.message

    def test_unreachable_github_never_raises(self, monkeypatch, token):
        def _boom():
            raise RuntimeError("network down")

        monkeypatch.setattr(gap_issues, "_repo", _boom)
        link = gap_issues.file_gap(_gap(), _review())
        # Falls through to the browser path rather than blowing up.
        assert link.state == "browser"

    def test_no_token_opens_a_prefilled_browser_url(self, monkeypatch, no_token):
        opened = []
        monkeypatch.setattr(gap_issues.webbrowser, "open", lambda url: opened.append(url) or True)
        link = gap_issues.file_gap(_gap(), _review())
        assert link.state == "browser"
        assert link.via == "browser"
        assert link.issue_number == 0
        assert opened and "issues/new" in opened[0]

    def test_browser_budget_exhausted_lists_the_url(self, monkeypatch, no_token):
        monkeypatch.setattr(gap_issues.webbrowser, "open", lambda url: True)
        link = gap_issues.file_gap(_gap(), _review(), browser_budget=0)
        assert link.state == "browser"
        assert "copy this URL" in link.message

    def test_browser_open_failure_still_returns_the_url(self, monkeypatch, no_token):
        def _boom(url):
            raise RuntimeError("no display")

        monkeypatch.setattr(gap_issues.webbrowser, "open", _boom)
        link = gap_issues.file_gap(_gap(), _review())
        assert link.issue_url
        assert "Couldn't open a browser" in link.message

    def test_credential_shaped_body_blocks_filing(self, repo):
        """The last gate before publication."""
        gap = _gap(
            claims=(
                TranscriptClaim(
                    member="Alice Curtis",
                    # Built at runtime, not a literal: a high-entropy literal here
                    # trips the repo's own secret scanner on every commit.
                    quote="the token is api_key: " + ("x" * 12) + " and it works",
                ),
            )
        )
        link = gap_issues.file_gap(gap, _review())
        assert link.state == "blocked"
        assert "Filing blocked" in link.message
        assert repo.created == []  # nothing left the machine


class TestCommentOnGap:
    def test_comments_on_an_open_issue(self, repo):
        repo.issues.append(FakeIssue(number=5, state="open"))
        link = gap_issues.comment_on_gap(5, _gap(), _review(), occurrences=2)
        assert link.state == "commented"
        assert repo.issues[-1].comments

    def test_closed_issue_files_a_fresh_one_instead(self, repo):
        """Closure means resolved or rejected — a recurrence deserves a new issue."""
        repo.issues.append(FakeIssue(number=5, state="closed"))
        link = gap_issues.comment_on_gap(5, _gap(), _review(), occurrences=2)
        assert link.state == "filed"
        assert repo.created
        assert "#5" in repo.created[0]["body"]

    def test_no_token_cannot_comment_and_says_so(self, no_token):
        link = gap_issues.comment_on_gap(5, _gap(), _review())
        assert link.state == "skipped"
        assert "GITHUB_TOKEN" in link.message

    def test_api_failure_never_raises(self, monkeypatch, token):
        class Boom:
            def get_issue(self, number):
                raise RuntimeError("gone")

        monkeypatch.setattr(gap_issues, "_repo", lambda: Boom())
        link = gap_issues.comment_on_gap(5, _gap(), _review())
        assert link.state == "failed"

    def test_credential_shaped_comment_is_blocked(self, repo):
        repo.issues.append(FakeIssue(number=5))
        gap = _gap(claims=(TranscriptClaim(member="A", quote="password = mysecretvalue"),))
        link = gap_issues.comment_on_gap(5, gap, _review())
        assert link.state == "blocked"
        assert not repo.issues[-1].comments


class TestFindExistingIssue:
    def test_finds_by_marker(self, repo):
        repo.issues.append(FakeIssue(number=9, body="<!-- yeaboi-gap: abc123def456 -->\nbody"))
        assert gap_issues.find_existing_issue("abc123def456")[0] == 9

    def test_returns_none_when_absent(self, repo):
        assert gap_issues.find_existing_issue("nope123456") is None

    def test_a_marked_issue_is_never_matched_on_its_title(self, repo):
        """A title is not evidence about an issue that already says what it is.

        Some templates render the same sentence for genuinely different gaps —
        an unresolved category becomes "an unknown system" — so trusting the
        title here would post one gap's evidence as a comment on another gap's
        PUBLIC issue, and the second gap would then never get filed at all.
        """
        from yeaboi.standup.gap_issues import issue_title

        title = "Standup cannot see activity in an unknown system"
        repo.issues.append(
            FakeIssue(number=9, body="<!-- yeaboi-gap: aaaaaaaaaaaa -->", title=issue_title("", title).strip())
        )
        assert gap_issues.find_existing_issue("bbbbbbbbbbbb", title=title) is None
        assert gap_issues.find_existing_issue("aaaaaaaaaaaa", title=title)[0] == 9

    def test_an_unmarked_issue_still_matches_on_its_title(self, repo):
        """The fallback exists for issues filed before the marker did."""
        from yeaboi.standup.gap_issues import issue_title

        title = "Standup cannot see activity in Slack"
        repo.issues.append(FakeIssue(number=4, body="filed by hand", title=issue_title("", title).strip()))
        assert gap_issues.find_existing_issue("cccccccccccc", title=title)[0] == 4

    def test_no_token_returns_none(self, no_token):
        assert gap_issues.find_existing_issue("abc123def456") is None

    def test_label_filter_422_falls_back_to_an_unlabelled_scan(self, monkeypatch, token):
        """The very first run has no such label on the repo yet."""
        marked = FakeIssue(number=9, body="<!-- yeaboi-gap: abc123def456 -->")

        class PickyRepo(FakeRepo):
            def get_issues(self, state="open", labels=None):
                if labels:
                    raise RuntimeError("422 Validation Failed")
                return [marked]

        monkeypatch.setattr(gap_issues, "_repo", lambda: PickyRepo())
        assert gap_issues.find_existing_issue("abc123def456")[0] == 9

    def test_search_failure_never_raises(self, monkeypatch, token):
        def _boom():
            raise RuntimeError("network")

        monkeypatch.setattr(gap_issues, "_repo", _boom)
        assert gap_issues.find_existing_issue("abc") is None


# ---------------------------------------------------------------------------
# The explicit "file these" act
# ---------------------------------------------------------------------------


class TestFileReviewGaps:
    def test_files_and_records_in_the_ledger(self, repo, db_path):
        from yeaboi.standup.store import StandupStore

        result = gap_issues.file_review_gaps(_review(), report=_report(), db_path=db_path)
        assert result.filed == 1
        assert result.commented == 0
        with StandupStore(db_path) as store:
            entry = store.get_gap_issue("abc123def456")
        assert entry["state"] == "filed"
        assert entry["issue_number"] == 101

    def test_recurrence_comments_instead_of_filing_again(self, repo, db_path):
        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as store:
            store.upsert_gap_issue("abc123def456", issue_number=5, issue_url="u", state="filed", via="api", title="t")
        repo.issues.append(FakeIssue(number=5, state="open"))
        result = gap_issues.file_review_gaps(_review(), db_path=db_path)
        assert result.commented == 1
        assert repo.created == []  # no duplicate issue

    def test_cold_start_adopts_an_existing_issue(self, repo, db_path):
        """A reset database must not re-file everything onto a public repo."""
        repo.issues.append(FakeIssue(number=9, state="open", body="<!-- yeaboi-gap: abc123def456 -->"))
        result = gap_issues.file_review_gaps(_review(), db_path=db_path)
        assert result.commented == 1
        assert repo.created == []

    def test_gap_ids_filter(self, repo, db_path):
        review = _review(gaps=(_gap(), _gap(fingerprint="other999", title="Another")))
        result = gap_issues.file_review_gaps(review, db_path=db_path, gap_ids=["other999"])
        assert result.filed == 1
        assert repo.created[0]["title"].endswith("Another")

    def test_blocked_gap_is_reported_not_swallowed(self, repo, db_path):
        gap = _gap(claims=(TranscriptClaim(member="A", quote="api_key: " + "x" * 12),))
        result = gap_issues.file_review_gaps(_review(gaps=(gap,)), db_path=db_path)
        assert result.filed == 0
        assert result.skipped == 1
        assert any("Filing blocked" in w for w in result.warnings)

    def test_review_status_updated(self, repo, db_path):
        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as store:
            review_id = store.record_review(_review())
        result = gap_issues.file_review_gaps(_review(review_id=review_id), db_path=db_path)
        assert result.filed == 1
        with StandupStore(db_path) as store:
            assert store.get_reviews("s1")[0]["status"] == "filed"

    def test_no_gaps_is_a_no_op(self, repo, db_path):
        result = gap_issues.file_review_gaps(_review(gaps=()), db_path=db_path)
        assert result.filed == 0
        assert repo.created == []

    def test_config_suggestions_are_never_filed(self, repo, db_path):
        """A config gap is the user's to fix — it must not reach GitHub."""
        review = _review(
            gaps=(),
            config_suggestions=(_gap(fingerprint="cfg1", scope="config", title="Add acme/infra"),),
        )
        result = gap_issues.file_review_gaps(review, db_path=db_path)
        assert result.filed == 0
        assert repo.created == []


class TestReviewPathNeverFiles:
    def test_running_a_review_does_not_touch_github(self, monkeypatch, db_path, tmp_path):
        """Runtime counterpart to the static import check in the review tests."""
        from datetime import date

        from yeaboi.standup import transcript_review

        def _explode(*a, **k):
            raise AssertionError("the review path must never reach GitHub")

        monkeypatch.setattr(gap_issues, "file_gap", _explode)
        monkeypatch.setattr(gap_issues, "comment_on_gap", _explode)
        monkeypatch.setattr(gap_issues, "file_review_gaps", _explode)
        monkeypatch.setattr(gap_issues, "_repo", _explode)
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))

        managed = tmp_path / "transcripts"
        managed.mkdir()
        monkeypatch.setattr("yeaboi.paths.TRANSCRIPTS_DIR", managed)
        (managed / "2026-07-30-standup.txt").write_text("Alice Curtis: Yes, but I also commented on the design doc.\n")

        class _R:
            content = (
                '{"claims": [{"member": "Alice Curtis", "claim": "commented on a page", '
                '"quote": "Yes, but I also commented on the design doc", "status": "missing", '
                '"system_hint": "confluence", "artifact_hint": "comment on a page"}]}'
            )

        monkeypatch.setattr("yeaboi.agent.llm.invoke_json", lambda *a, **k: _R())

        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as store:
            store.record_run(_report(session_id="s1"))

        reviews = transcript_review.sweep_and_review(
            "s1", db_path=db_path, today=date(2026, 8, 1), before_date="2026-07-31"
        )
        assert reviews and reviews[0].gaps  # produced a gap, filed nothing
