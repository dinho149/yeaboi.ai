"""Unit tests for the deterministic root-cause ladder.

Each rule gets a trigger case AND a near-miss, because the value of a rule
ladder over an LLM is precisely that it does NOT fire on the near-miss.
"""

from __future__ import annotations

import pytest

from yeaboi.agent.state import (
    MEMBER_EVIDENCE_CAP,
    ActivityEvidence,
    MemberUpdate,
    StandupReport,
    TranscriptClaim,
)
from yeaboi.standup import gap_taxonomy


def _claim(**over) -> TranscriptClaim:
    base = dict(
        member="Alice",
        claim="I also commented on the design doc",
        quote="I also commented on the design doc",
        status="missing",
        system_hint="confluence",
        artifact_hint="comment on the design doc",
    )
    base.update(over)
    return TranscriptClaim(**base)


def _report(**over) -> StandupReport:
    base = dict(
        date="2026-07-30",
        session_id="s1",
        member_updates=(MemberUpdate(name="Alice", summary="shipped login"),),
        activity_counts=(("jira", 3), ("github", 5), ("confluence", 2)),
    )
    base.update(over)
    return StandupReport(**base)


class TestVocabulary:
    def test_system_aliases_normalise(self):
        assert gap_taxonomy.normalize_system("azure_repos") == "azdo_repos"
        assert gap_taxonomy.normalize_system("Azure Boards") == "azure_devops"
        assert gap_taxonomy.normalize_system("GITHUB") == "github"

    def test_unknown_system_is_unknown(self):
        assert gap_taxonomy.normalize_system("some-random-tool") == "unknown"
        assert gap_taxonomy.normalize_system("") == "unknown"

    def test_known_unsupported_passes_through(self):
        assert gap_taxonomy.normalize_system("slack") == "slack"

    @pytest.mark.parametrize(
        ("hint", "expected"),
        [
            ("opened a pull request in acme/infra", "pull_request"),
            ("commented on the design doc", "comment"),
            ("reviewed Bob's PR", "review"),
            # "comment" must outrank "review": a review comment is a comment,
            # and "review" appears constantly inside object names.
            ("review comments on PR 48780", "comment"),
            ("comments on the Access Audit Review page", "comment"),
            ("approved the pull request", "review"),
            ("logged 3 hours against the ticket", "worklog"),
            ("pushed a commit", "commit"),
            ("moved the work item to done", "ticket"),
            ("updated the runbook page", "page"),
            ("posted in the channel", "message"),
            ("a completely unrelated phrase", "unknown"),
        ],
    )
    def test_artifact_kind_slugs(self, hint, expected):
        assert gap_taxonomy.artifact_kind(hint) == expected

    def test_manifest_covers_every_collector_source(self):
        """The manifest is what makes 'integration_missing' a fact, not a guess."""
        from yeaboi.standup.collector import ALL_SOURCES

        covered = {system for system, _kind in gap_taxonomy.CAPABILITY_MANIFEST}
        assert covered == set(ALL_SOURCES), (
            "CAPABILITY_MANIFEST and collector.ALL_SOURCES disagree — a collector grew or shrank "
            "without the taxonomy being updated"
        )

    def test_manifest_values_are_valid(self):
        assert set(gap_taxonomy.CAPABILITY_MANIFEST.values()) <= {
            gap_taxonomy.FETCHED,
            gap_taxonomy.NOT_FETCHED,
        }

    def test_every_category_has_a_valid_scope(self):
        for cat in gap_taxonomy.CATEGORIES:
            assert cat.scope in (gap_taxonomy.SCOPE_CONFIG, gap_taxonomy.SCOPE_PRODUCT, gap_taxonomy.SCOPE_NONE)
            assert cat.feedback_kind in ("Bug", "Feature", "Improvement", "Other")


class TestFingerprint:
    def test_stable_across_member_date_and_wording(self):
        """The same gap raised by anyone, any week, is ONE issue."""
        a = gap_taxonomy.fingerprint("capability_gap_in_supported_source", ("confluence",), "comment")
        b = gap_taxonomy.fingerprint("capability_gap_in_supported_source", ("confluence",), "comment")
        assert a == b

    def test_differs_by_system(self):
        a = gap_taxonomy.fingerprint("capability_gap_in_supported_source", ("confluence",), "comment")
        b = gap_taxonomy.fingerprint("capability_gap_in_supported_source", ("notion",), "comment")
        assert a != b

    def test_differs_by_kind(self):
        a = gap_taxonomy.fingerprint("capability_gap_in_supported_source", ("jira",), "comment")
        b = gap_taxonomy.fingerprint("capability_gap_in_supported_source", ("jira",), "worklog")
        assert a != b

    def test_differs_by_scope_token(self):
        a = gap_taxonomy.fingerprint("scope_gap_repository", ("github",), "commit", "acme/infra")
        b = gap_taxonomy.fingerprint("scope_gap_repository", ("github",), "commit", "acme/web")
        assert a != b

    def test_system_order_does_not_matter(self):
        a = gap_taxonomy.fingerprint("x", ("github", "jira"), "commit")
        b = gap_taxonomy.fingerprint("x", ("jira", "github"), "commit")
        assert a == b

    def test_version_prefixed(self):
        """Changing the scheme must re-file deliberately, not by accident."""
        import hashlib

        expected = hashlib.sha256(b"v1|cat|jira|comment|").hexdigest()[:16]
        assert gap_taxonomy.fingerprint("cat", ("jira",), "comment") == expected


class TestIntegrationMissing:
    def test_fires_for_a_system_with_no_collector(self):
        d = gap_taxonomy.classify(_claim(system_hint="slack", artifact_hint="posted in the channel"), report=_report())
        assert d.category.id == "integration_missing"
        assert d.category.scope == gap_taxonomy.SCOPE_PRODUCT
        assert d.category.feedback_kind == "Feature"

    def test_does_not_fire_for_a_supported_system(self):
        d = gap_taxonomy.classify(_claim(system_hint="confluence"), report=_report())
        assert d is None or d.category.id != "integration_missing"

    def test_unknown_system_with_no_category_clue_produces_nothing(self):
        claim = _claim(system_hint="unknown", artifact_hint="a thing", claim="I did a thing")
        assert gap_taxonomy.classify(claim, report=_report()) is None


class TestUnknownSystemInference:
    """The model is told never to guess a system, so "unknown" is the honest
    answer for "commented on the design doc". The ladder resolves it from the
    artifact's CATEGORY and the run's own configuration, or not at all."""

    def test_resolves_when_exactly_one_source_serves_the_category(self):
        # Only Confluence is scanned for documentation.
        d = gap_taxonomy.classify(
            _claim(system_hint="unknown", artifact_hint="comment on the design doc"), report=_report()
        )
        assert d.category.id == "capability_gap_in_supported_source"
        assert d.systems == ("confluence",)

    def test_stays_ambiguous_when_two_sources_serve_the_category(self):
        report = _report(activity_counts=(("confluence", 2), ("notion", 1)))
        d = gap_taxonomy.classify(
            _claim(system_hint="unknown", artifact_hint="comment on the design doc"), report=report
        )
        assert d is None

    def test_no_source_for_the_category_is_a_config_gap(self):
        report = _report(activity_counts=(("jira", 3),))
        d = gap_taxonomy.classify(
            _claim(system_hint="unknown", artifact_hint="updated the runbook page"), report=report
        )
        assert d.category.id == "source_not_configured"
        assert d.scope_token == "documentation"
        assert "Confluence" in d.remedy and "Notion" in d.remedy

    def test_code_category_inferred_from_the_object(self):
        report = _report(activity_counts=(("github", 5),))
        d = gap_taxonomy.classify(
            _claim(system_hint="unknown", artifact_hint="opened a pull request", matched_key=""),
            report=report,
        )
        # GitHub PRs are fetched, so no capability gap — but it resolved the system.
        assert d is None or d.systems == ("github",)

    @pytest.mark.parametrize(
        ("hint", "expected"),
        [
            ("commented on the design doc", "documentation"),
            ("updated the runbook", "documentation"),
            ("opened a pull request", "code"),
            ("pushed a commit", "code"),
            ("moved the ticket", "ticketing"),
            ("talked to a customer", ""),
        ],
    )
    def test_category_inference(self, hint, expected):
        assert gap_taxonomy.infer_category(hint) == expected


class TestUntrackedWork:
    def test_none_system_is_expected_not_a_defect(self):
        d = gap_taxonomy.classify(_claim(system_hint="none", artifact_hint="paired with Bob"), report=_report())
        assert d.category.id == "untracked_work"
        assert d.category.scope == gap_taxonomy.SCOPE_NONE


class TestSourceNotConfigured:
    def test_fires_when_the_source_was_never_scanned(self):
        report = _report(activity_counts=(("jira", 3),))
        d = gap_taxonomy.classify(_claim(system_hint="confluence"), report=report)
        assert d.category.id == "source_not_configured"
        assert d.category.scope == gap_taxonomy.SCOPE_CONFIG
        assert d.remedy  # config gaps must carry an exact remedy

    def test_does_not_fire_when_the_source_was_scanned(self):
        d = gap_taxonomy.classify(_claim(system_hint="confluence"), report=_report())
        assert d is None or d.category.id != "source_not_configured"


class TestSourceFailed:
    def test_failure_beats_not_configured(self):
        """A 401 must never be reported as 'we don't fetch that'."""
        report = _report(
            activity_counts=(("jira", 3),),
            warnings=("Confluence: authentication failed (401)",),
        )
        d = gap_taxonomy.classify(_claim(system_hint="confluence"), report=report)
        assert d.category.id == "source_configured_but_failed"
        assert d.category.scope == gap_taxonomy.SCOPE_PRODUCT

    def test_skip_reason_that_reads_as_failure_counts(self):
        report = _report(skipped_sources=(("confluence", "request timed out"),))
        d = gap_taxonomy.classify(_claim(system_hint="confluence"), report=report)
        assert d.category.id == "source_configured_but_failed"

    def test_plain_missing_config_is_not_a_failure(self):
        report = _report(
            activity_counts=(("jira", 3),),
            skipped_sources=(("confluence", "CONFLUENCE_SPACE_KEY not set"),),
        )
        d = gap_taxonomy.classify(_claim(system_hint="confluence"), report=report)
        assert d.category.id == "source_not_configured"


class TestScopeGapRepository:
    def test_fires_for_a_repo_outside_the_configured_scope(self):
        d = gap_taxonomy.classify(
            _claim(system_hint="github", artifact_hint="pull request in acme/infra"),
            report=_report(),
            config={"github_repositories": ["acme/web"]},
        )
        assert d.category.id == "scope_gap_repository"
        assert d.scope_token == "acme/infra"
        assert "acme/infra" in d.remedy

    def test_does_not_fire_when_the_repo_is_in_scope(self):
        d = gap_taxonomy.classify(
            _claim(system_hint="github", artifact_hint="pull request in acme/web"),
            report=_report(),
            config={"github_repositories": ["acme/web"]},
        )
        assert d is None or d.category.id != "scope_gap_repository"

    def test_does_not_fire_without_a_configured_scope(self):
        """With no scope configured we cannot say the repo is outside it."""
        d = gap_taxonomy.classify(
            _claim(system_hint="github", artifact_hint="pull request in acme/infra"),
            report=_report(),
            config={},
        )
        assert d is None or d.category.id != "scope_gap_repository"

    def test_does_not_fire_for_a_non_code_system(self):
        d = gap_taxonomy.classify(
            _claim(system_hint="confluence", artifact_hint="page in space/docs"),
            report=_report(),
            config={"github_repositories": ["acme/web"]},
        )
        assert d is None or d.category.id != "scope_gap_repository"


class TestCapabilityGap:
    def test_fires_for_confluence_comments(self):
        d = gap_taxonomy.classify(_claim(system_hint="confluence", artifact_hint="comment on a page"), report=_report())
        assert d.category.id == "capability_gap_in_supported_source"
        assert d.category.feedback_kind == "Feature"

    def test_does_not_fire_for_a_kind_that_is_fetched(self):
        d = gap_taxonomy.classify(_claim(system_hint="confluence", artifact_hint="updated a page"), report=_report())
        assert d is None or d.category.id != "capability_gap_in_supported_source"

    def test_fires_for_azdo_work_item_comments(self):
        report = _report(activity_counts=(("azure_devops", 4),))
        d = gap_taxonomy.classify(
            _claim(system_hint="azure_devops", artifact_hint="comment on the work item"), report=report
        )
        assert d.category.id == "capability_gap_in_supported_source"

    def test_azdo_pr_comments_are_fetched_so_no_gap(self):
        """Azure Repos fetches PR thread comments (and votes) now, so a member's
        'commented on the PR' must not be excused as a capability gap."""
        report = _report(activity_counts=(("azdo_repos", 4),))
        d = gap_taxonomy.classify(
            _claim(system_hint="azure_repos", artifact_hint="commented on the pull request"), report=report
        )
        assert d is None or d.category.id != "capability_gap_in_supported_source"

    def test_azdo_work_item_comments_resolve_to_comment_not_ticket(self):
        """The load-bearing ordering case: Azure Boards fetches work items but
        not their discussion, so 'commented on the work item' must not be read
        as a ticket — that would hide a real capability gap."""
        report = _report(activity_counts=(("azure_devops", 4),))
        d = gap_taxonomy.classify(
            _claim(system_hint="azure_devops", artifact_hint="commented on the work item"), report=report
        )
        assert d.category.id == "capability_gap_in_supported_source"
        assert d.kind == "comment"

    def test_manifest_matches_the_azdo_repos_fetchers(self):
        """Truth-lock: azdevops_recent_reviews emits both thread comments and
        dated reviewer votes, so the manifest must claim both. (The suite's
        other manifest tests check key-shape only — this inverted once.)"""
        assert gap_taxonomy.CAPABILITY_MANIFEST[("azdo_repos", "review")] == gap_taxonomy.FETCHED
        assert gap_taxonomy.CAPABILITY_MANIFEST[("azdo_repos", "comment")] == gap_taxonomy.FETCHED

    def test_github_pr_comments_are_fetched_so_no_gap(self):
        d = gap_taxonomy.classify(
            _claim(system_hint="github", artifact_hint="comment on the pull request"), report=_report()
        )
        assert d is None or d.category.id != "capability_gap_in_supported_source"


class TestAutomationFalsePositive:
    """Grounded in the report's OWN notice — the rule can only fire where
    standup already admits it dropped something from a member's credit."""

    def _report_with_notice(self, **over):
        base = dict(
            warnings=(
                "Excluded 31 review item(s) posted under 'Alice' that look automated (matched 'wiz') "
                "— service-hook activity is not credited as personal work.",
            ),
            activity_counts=(("jira", 3), ("azdo_repos", 40)),
        )
        base.update(over)
        return _report(**base)

    def test_fires_for_reviews_excluded_as_automation(self):
        d = gap_taxonomy.classify(
            _claim(system_hint="azure_repos", artifact_hint="reviewed the terraform PRs"),
            report=self._report_with_notice(),
        )
        assert d.category.id == "automation_filter_false_positive"
        assert d.category.scope == gap_taxonomy.SCOPE_PRODUCT

    def test_does_not_fire_without_an_automation_notice(self):
        d = gap_taxonomy.classify(
            _claim(system_hint="azure_repos", artifact_hint="reviewed the terraform PRs"),
            report=_report(activity_counts=(("azdo_repos", 40),)),
        )
        assert d is None or d.category.id != "automation_filter_false_positive"

    def test_does_not_fire_for_a_different_member(self):
        d = gap_taxonomy.classify(
            _claim(member="Bob", system_hint="azure_repos", artifact_hint="reviewed the PRs"),
            report=_report(
                member_updates=(MemberUpdate(name="Alice"), MemberUpdate(name="Bob")),
                warnings=(
                    "Excluded 31 review item(s) posted under 'Alice' that look automated "
                    "— service-hook activity is not credited as personal work.",
                ),
                activity_counts=(("azdo_repos", 40),),
            ),
        )
        assert d is None or d.category.id != "automation_filter_false_positive"

    def test_configured_marker_in_the_claim_also_fires(self):
        d = gap_taxonomy.classify(
            _claim(system_hint="azure_repos", artifact_hint="the wiz remediation work"),
            report=self._report_with_notice(),
            config={"automation_markers": "wiz"},
        )
        assert d.category.id == "automation_filter_false_positive"

    def test_beats_the_capability_manifest(self):
        """The item WAS collected — reporting 'we don't fetch that' would send
        the maintainer down the wrong path."""
        d = gap_taxonomy.classify(
            _claim(system_hint="azure_repos", artifact_hint="commented on the pull request"),
            report=self._report_with_notice(),
        )
        assert d.category.id == "automation_filter_false_positive"

    def test_unrelated_artifact_does_not_fire(self):
        d = gap_taxonomy.classify(
            _claim(system_hint="jira", artifact_hint="moved the ticket to done", matched_key=""),
            report=self._report_with_notice(),
        )
        assert d is None or d.category.id != "automation_filter_false_positive"


class TestSummaryDroppedIt:
    def _report_with_evidence(self, summary: str) -> StandupReport:
        return _report(
            member_updates=(
                MemberUpdate(
                    name="Alice",
                    summary=summary,
                    documentation_summary=summary,
                    documentation_evidence=(ActivityEvidence(kind="page", key="DOC-9", title="Runbook"),),
                ),
            )
        )

    def test_fires_when_collected_evidence_is_absent_from_the_summary(self):
        d = gap_taxonomy.classify(
            _claim(matched_key="DOC-9", artifact_hint="updated the runbook page"),
            report=self._report_with_evidence("shipped login"),
        )
        assert d.category.id == "summary_dropped_it"

    def test_does_not_fire_when_the_summary_mentions_it(self):
        d = gap_taxonomy.classify(
            _claim(matched_key="DOC-9", artifact_hint="updated the runbook page"),
            report=self._report_with_evidence("updated DOC-9"),
        )
        assert d is None or d.category.id != "summary_dropped_it"


class TestEvidenceCapTruncation:
    """Rule 8 reads "the list is exactly at its cap" as proof items were cut.

    Every case here is written against ``MEMBER_EVIDENCE_CAP`` rather than a
    literal, because the literal is what broke: the engine's cap moved to 30
    while this rule kept assuming 8, and a suite full of eight-row fixtures went
    on passing while the rule fired on any member whose commits merely nested
    under a PR.
    """

    def _member(self, rows: int, counted: int) -> MemberUpdate:
        return MemberUpdate(
            name="Alice",
            code_activity_count=counted,
            code_evidence=tuple(ActivityEvidence(kind="commit", key=f"sha{i}") for i in range(rows)),
        )

    def test_the_rule_and_the_engine_read_one_constant(self):
        """A truth-lock, in the spirit of the manifest one above.

        These two live in modules that cannot import each other, so nothing but
        this test stops them drifting again — and the drift is silent, ending as
        a public GitHub issue recommending a change to a cap that is already fine.
        """
        import inspect

        from yeaboi.standup.engine import _member_evidence

        assert inspect.signature(gap_taxonomy.classify).parameters["evidence_cap"].default == MEMBER_EVIDENCE_CAP
        assert inspect.signature(_member_evidence).parameters["cap"].default == MEMBER_EVIDENCE_CAP

    def test_fires_when_evidence_is_at_the_cap_and_more_was_counted(self):
        report = _report(member_updates=(self._member(MEMBER_EVIDENCE_CAP, MEMBER_EVIDENCE_CAP + 15),))
        d = gap_taxonomy.classify(
            _claim(system_hint="github", artifact_hint="pushed a commit", matched_key=""), report=report
        )
        assert d.category.id == "evidence_cap_truncation"
        assert str(MEMBER_EVIDENCE_CAP + 15) in d.detail

    def test_does_not_fire_when_nothing_was_cut(self):
        report = _report(member_updates=(self._member(MEMBER_EVIDENCE_CAP, MEMBER_EVIDENCE_CAP),))
        d = gap_taxonomy.classify(_claim(system_hint="github", artifact_hint="pushed a commit"), report=report)
        assert d is None or d.category.id != "evidence_cap_truncation"

    def test_does_not_fire_on_a_busy_member_below_the_cap(self):
        """The regression this constant exists to prevent.

        Fifteen code activities that dedupe and nest down to eleven rows: more
        counted than kept, but nothing cut — eleven is nowhere near the cap.
        Under the old default of 8 this reported "Alice had 15 code items but
        the report kept only 11" and blamed a cap that never applied.
        """
        report = _report(member_updates=(self._member(11, 15),))
        d = gap_taxonomy.classify(
            _claim(system_hint="github", artifact_hint="pushed a commit", matched_key=""), report=report
        )
        assert d is None or d.category.id != "evidence_cap_truncation"


class TestContradiction:
    def test_requires_a_key_present_in_the_evidence(self):
        report = _report(
            member_updates=(
                MemberUpdate(
                    name="Alice",
                    ticketing_evidence=(ActivityEvidence(kind="issue", key="YB-12", title="Login"),),
                ),
            )
        )
        d = gap_taxonomy.classify_contradiction(
            _claim(status="contradicted", system_hint="jira", matched_key="YB-12"), report=report
        )
        assert d.category.id == "report_inaccurate"

    def test_self_correction_without_a_key_produces_nothing(self):
        """'No, I didn't finish that' is usually self-correction, not a defect."""
        d = gap_taxonomy.classify_contradiction(
            _claim(status="contradicted", system_hint="jira", matched_key=""), report=_report()
        )
        assert d is None

    def test_key_not_in_evidence_produces_nothing(self):
        d = gap_taxonomy.classify_contradiction(
            _claim(status="contradicted", system_hint="jira", matched_key="YB-99"), report=_report()
        )
        assert d is None


class TestBuildGap:
    def test_produces_a_titled_actionable_gap(self):
        d = gap_taxonomy.classify(_claim(system_hint="confluence", artifact_hint="comment on a page"), report=_report())
        claims = (_claim(),)
        gap = gap_taxonomy.build_gap(d, claims)
        assert gap.fingerprint
        assert gap.scope == gap_taxonomy.SCOPE_PRODUCT
        assert "Confluence" in gap.title
        assert gap.next_steps
        assert gap.members == ("Alice",)
        assert gap.claims == claims
        assert gap.affected_systems == ("confluence",)

    def test_contradiction_gap_is_only_medium_confidence(self):
        report = _report(
            member_updates=(
                MemberUpdate(name="Alice", ticketing_evidence=(ActivityEvidence(kind="issue", key="YB-12"),)),
            )
        )
        d = gap_taxonomy.classify_contradiction(
            _claim(status="contradicted", system_hint="jira", matched_key="YB-12"), report=report
        )
        assert gap_taxonomy.build_gap(d, ()).confidence == "medium"

    def test_other_gaps_are_high_confidence(self):
        d = gap_taxonomy.classify(_claim(system_hint="slack"), report=_report())
        assert gap_taxonomy.build_gap(d, ()).confidence == "high"

    def test_next_steps_are_templated_not_empty(self):
        for system, hint in (("slack", "message"), ("confluence", "comment on a page")):
            d = gap_taxonomy.classify(_claim(system_hint=system, artifact_hint=hint), report=_report())
            assert gap_taxonomy.build_gap(d, ()).next_steps


class TestReportFacts:
    def test_scanned_sources(self):
        assert gap_taxonomy.scanned_sources(_report()) == {"jira", "github", "confluence"}

    def test_failed_sources_reads_warnings(self):
        report = _report(warnings=("GitHub: 403 forbidden",))
        assert "github" in gap_taxonomy.failed_sources(report)

    def test_benign_warning_is_not_a_failure(self):
        report = _report(warnings=("AI summary unavailable — key not set",))
        assert gap_taxonomy.failed_sources(report) == set()

    def test_configured_scope_lowercases_and_merges(self):
        scope = gap_taxonomy.configured_scope({"github_repositories": ["Acme/Web"], "azdo_projects": ["Platform"]})
        assert scope == {"acme/web", "platform"}
