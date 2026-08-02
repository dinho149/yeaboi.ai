"""Unit tests for standup reference parsing: ticket keys, PR claims, subjects.

The gate is the point of this module — ticket-*shaped* text is not evidence of a
ticket — so most of these pin what must NOT be recognised.
"""

import pytest

from yeaboi.standup import references


class TestTicketKeys:
    def test_finds_jira_shaped_keys(self):
        assert references.find_ticket_keys("Fixes PSOT-12 and ACME-3") == ("PSOT-12", "ACME-3")

    def test_longer_key_never_half_matches(self):
        assert references.find_ticket_keys("PSOT-123") == ("PSOT-123",)

    def test_prefixes_of_splits_on_the_first_dash(self):
        assert references.prefixes_of(("PSOT-12", "PSOT-3", "ACME-1")) == frozenset({"PSOT", "ACME"})

    @pytest.mark.parametrize("text", ["UTF-8", "SHA-256", "ISO-8601", "HTTP-2"])
    def test_lookalikes_are_gated_out_without_the_prefix(self, text):
        # They DO match the raw regex — that is exactly why the gate exists.
        assert references.find_ticket_keys(text)
        assert references.gated_ticket_keys(text, prefixes={"PSOT"}) == ()

    def test_lookalike_passes_when_the_tracker_really_uses_that_prefix(self):
        # A project genuinely called UTF is not this module's problem to guess at.
        assert references.gated_ticket_keys("UTF-8", prefixes={"UTF"}) == ("UTF-8",)


class TestTrackerGates:
    def test_prefixes_come_only_from_tracker_kinds(self):
        items = [
            {"kind": "issue", "key": "PSOT-1"},
            {"kind": "commit", "key": "DEAD-1"},  # a sha-ish key must not widen the gate
            {"kind": "pr", "key": "#91"},
        ]
        assert references.tracker_prefixes(items) == frozenset({"PSOT"})

    def test_work_item_ids_come_only_from_azure_boards_kinds(self):
        items = [
            {"kind": "work_item", "key": "#1234"},
            {"kind": "wip", "key": "#77"},
            {"kind": "pr", "key": "#91"},  # a GitHub PR number is not a work item
            {"kind": "issue", "key": "PSOT-1"},
        ]
        assert references.tracker_work_item_ids(items) == frozenset({"1234", "77"})


class TestHasTrackerReference:
    def test_gated_jira_key_counts(self):
        assert references.has_tracker_reference("feature/PSOT-12-retry", prefixes={"PSOT"})
        assert not references.has_tracker_reference("feature/PSOT-12-retry", prefixes={"ACME"})

    def test_azdo_ab_syntax_is_ungated(self):
        # AB#123 spells its own evidence; nothing else uses that syntax.
        assert references.has_tracker_reference("Fixes AB#1234")
        assert references.has_tracker_reference("fixes ab#1234")

    def test_ab_prefix_needs_a_boundary(self):
        assert not references.has_tracker_reference("LAB#1234")

    def test_bare_hash_needs_a_matching_work_item_id(self):
        assert not references.has_tracker_reference("Closes #91")
        assert references.has_tracker_reference("Closes #91", work_item_ids={"91"})

    def test_bare_hash_on_a_github_only_setup_never_counts(self):
        # The empty id set is what stops a PR number reading as a work item.
        assert not references.has_tracker_reference("Merge (#91)", work_item_ids=frozenset())

    def test_empty_texts_are_not_evidence(self):
        assert not references.has_tracker_reference("", "", prefixes={"PSOT"})


class TestPullRequestClaims:
    @pytest.mark.parametrize(
        ("subject", "expected"),
        [
            ("Merge pull request #91 from acme/feature", "91"),
            ("Merge pull request 48806 from acme/x", "48806"),
            ("Merged PR 123: Add retry", "123"),
            ("fix the login redirect (#91)", "91"),
            ("fix the login redirect (PR #91)", "91"),
            ("just a normal commit", ""),
        ],
    )
    def test_pr_reference(self, subject, expected):
        assert references.pr_reference(subject) == expected

    def test_merge_source_branch(self):
        assert references.merge_source_branch("Merge pull request #91 from acme/feature") == "acme/feature"
        assert references.merge_source_branch("regular commit") == ""

    def test_claims_pull_request_is_true_without_a_parent_in_window(self):
        # The habit rules need the weaker fact than _nest_pr_commits does: a
        # merge subject is plumbing whether or not its PR was collected.
        assert references.claims_pull_request("Merge pull request #99999 from acme/x")
        assert not references.claims_pull_request("add the SAML config")


class TestNormalizeCommitSubject:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("wip (PR #4)", "wip"),
            ("wip (my-repo)", "wip"),
            ("wip (my-repo) (PR #4)", "wip"),
            ("fix the retry loop", "fix the retry loop"),
            ("", ""),
        ],
    )
    def test_strips_collector_tails(self, raw, expected):
        assert references.normalize_commit_subject(raw) == expected


class TestExportParity:
    """The refactor must not have changed what export.py's key map admits."""

    def test_matches_the_old_inline_loop(self):
        prose = "Shipped PSOT-12 using UTF-8 encoding, tracked in ACME-3 and SHA-256 hashed."
        known = {"PSOT", "ACME"}
        expected = [k for k in references.TICKET_KEY_RE.findall(prose) if k.split("-")[0] in known]
        assert list(references.gated_ticket_keys(prose, prefixes=known)) == expected
        assert expected == ["PSOT-12", "ACME-3"]


class TestMergeSubjectIsNarrowerThanPrClaim:
    """The two gates the habit rules need, and why they are not one gate.

    A squash-merge subject ends "(#91)" and the collector's own PR-branch scan
    appends " (PR #91)". Both *belong to* a PR — but both are authored subjects,
    so a rule about message quality must still judge them. Conflating the two
    made commit-message detection dead for any squash-merging team.
    """

    def test_real_merge_subjects_are_both(self):
        for subject in ("Merge pull request #91 from acme/x", "Merged PR 123: Add retry"):
            assert references.claims_pull_request(subject), subject
            assert references.is_merge_subject(subject), subject

    def test_parenthesised_references_claim_a_pr_but_are_not_merges(self):
        for subject in ("fix login (#91)", "wip (PR #91)"):
            assert references.claims_pull_request(subject), subject
            assert not references.is_merge_subject(subject), subject

    def test_a_plain_subject_is_neither(self):
        assert not references.claims_pull_request("Add the SAML config")
        assert not references.is_merge_subject("Add the SAML config")
