"""Tests for the per-member engineering-practices scorer (analysis/practices.py)."""

from __future__ import annotations

from yeaboi.analysis.practices import (
    MIN_PRACTICE_SAMPLE,
    _is_docs_path,
    has_meaningful_description,
    has_ticket_reference,
    member_practices,
)


def _item(**overrides) -> dict:
    base = {"kind": "commit", "author": "Ava", "title": "", "body": "", "matched_members": ["Ava"]}
    base.update(overrides)
    return base


class TestTicketReference:
    def test_jira_key_in_title(self):
        assert has_ticket_reference(_item(title="PAY-123 fix rounding"))

    def test_azdo_ref_in_body(self):
        assert has_ticket_reference(_item(body="Fixes AB#42"))
        assert has_ticket_reference(_item(body="fixes ab#42"))

    def test_bare_issue_ref_in_pr_body(self):
        assert has_ticket_reference(_item(kind="pr", body="Closes #123"))

    def test_branch_reference_counts(self):
        assert has_ticket_reference(_item(kind="pr", branch="feature/ABC-123-login"))
        assert has_ticket_reference(_item(kind="pr", branch="feature/abc-123-login"))

    def test_technical_tokens_are_not_tickets(self):
        for text in ("encode as UTF-8", "SHA-256 digest", "patched CVE-2024-1234", "uses GPT-4"):
            assert not has_ticket_reference(_item(title=text))

    def test_url_fragment_is_not_an_issue_ref(self):
        assert not has_ticket_reference(_item(body="see example.com/a#123 for details"))

    def test_squash_suffix_ignored_on_commits_but_counted_on_prs(self):
        # GitHub appends "(#123)" to squash-merged commit titles automatically.
        assert not has_ticket_reference(_item(kind="commit", title="fix rounding (#123)"))
        assert has_ticket_reference(_item(kind="pr", title="fix rounding (#123)"))

    def test_branch_vocabulary_is_not_a_ticket(self):
        assert not has_ticket_reference(_item(kind="pr", branch="bugfix-2"))
        assert not has_ticket_reference(_item(kind="pr", branch="release/v-2"))


class TestMeaningfulDescription:
    def test_empty_and_whitespace_fail(self):
        assert not has_meaningful_description("")
        assert not has_meaningful_description("   \n  ")

    def test_short_one_liner_fails(self):
        assert not has_meaningful_description("fix stuff")

    def test_long_paragraph_passes(self):
        assert has_meaningful_description("This change reworks the rounding logic in the payment engine " * 3)

    def test_two_substantial_lines_pass(self):
        assert has_meaningful_description("Reworks payment rounding to banker's rounding.\nAdds regression coverage.")

    def test_markdown_checklist_passes(self):
        assert has_meaningful_description("## Changes\n- [x] rounding fix\n- [ ] docs")


class TestDocsPath:
    def test_docs_paths_detected(self):
        for path in ("docs/guide.md", "README.md", "adr/0001-choice.rst", "wiki/setup.adoc", "CHANGELOG"):
            assert _is_docs_path(path), path

    def test_source_paths_are_not_docs(self):
        for path in ("src/api.py", "tests/test_api.py", "lib/readme_parser.py"):
            assert not _is_docs_path(path), path


class TestMemberPractices:
    def _items(self):
        return [
            # Ava commit: prod + test files, ticket in title.
            _item(
                title="PAY-9 add retries",
                changed_file_paths=["src/api.py", "tests/test_api.py"],
            ),
            # Ava PR: prod only, meaningful description, docs mention in body.
            _item(
                kind="pr",
                title="harden client",
                body="Reworks the client retry loop with jitter.\nUpdates the docs for the new flag.",
                changed_file_paths=["src/client.py"],
            ),
            # Ava commit without file data: counts for tickets only.
            _item(title="tidy imports"),
            # Ben tests-only commit: file data present, out of the tests denominator.
            _item(
                author="Ben",
                matched_members=["Ben"],
                title="add regression test",
                changed_file_paths=["tests/test_edge.py"],
            ),
            # Ben PR: blank description, no ticket, prod file, no tests.
            _item(
                kind="pr",
                author="Ben",
                matched_members=["Ben"],
                title="quick fix",
                body="",
                changed_file_paths=["src/fix.py"],
            ),
            # Agent-authored commit lands on the agent row.
            _item(
                author="devin-ai-integration[bot]",
                matched_members=[],
                agent_authored=True,
                title="agent change",
                changed_file_paths=["src/agent.py"],
            ),
            # Review items are ignored entirely.
            _item(kind="review", title="LGTM"),
        ]

    def test_rates_and_denominators(self):
        result = member_practices(self._items(), ["Ava", "Ben"])
        ava, ben, agents = result["members"]
        assert [row["member"] for row in result["members"]] == ["Ava", "Ben", "AI agent accounts"]

        assert ava["commits"] == 2 and ava["prs"] == 1 and ava["with_file_data"] == 2
        assert (ava["tests_num"], ava["tests_den"]) == (1, 2)  # no-file-data commit excluded
        assert (ava["docs_num"], ava["docs_den"]) == (1, 2)  # PR docs mention counts
        assert (ava["ticket_num"], ava["ticket_den"]) == (1, 3)  # all items count
        assert (ava["desc_num"], ava["desc_den"]) == (1, 1)  # PRs only
        assert ava["tests_rate"] == 50.0 and ava["desc_rate"] == 100.0

        assert (ben["tests_num"], ben["tests_den"]) == (0, 1)  # tests-only commit out of den
        assert (ben["docs_num"], ben["docs_den"]) == (0, 2)
        assert (ben["desc_num"], ben["desc_den"]) == (0, 1)
        assert ben["ticket_rate"] == 0.0

        assert agents["commits"] == 1 and agents["tests_den"] == 1

    def test_team_row_is_recomputed_not_averaged(self):
        result = member_practices(self._items(), ["Ava", "Ben"])
        team = result["team"]
        # Union of all commit/pr items, including the agent-authored one.
        assert team["commits"] == 4 and team["prs"] == 2
        assert (team["tests_num"], team["tests_den"]) == (1, 4)
        assert (team["ticket_num"], team["ticket_den"]) == (1, 6)

    def test_file_data_and_min_sample_reported(self):
        result = member_practices(self._items(), ["Ava", "Ben"])
        assert result["file_data"] == {"with_file_data": 5, "total": 6}
        assert result["min_sample"] == MIN_PRACTICE_SAMPLE

    def test_zero_denominators_give_none_rates(self):
        result = member_practices([_item(title="loose change")], ["Ava"])
        row = result["members"][0]
        assert row["tests_rate"] is None and row["docs_rate"] is None and row["desc_rate"] is None
        assert row["ticket_rate"] == 0.0

    def test_sorted_by_volume_then_name(self):
        items = [
            _item(author="Ben", matched_members=["Ben"]),
            _item(author="Ben", matched_members=["Ben"]),
            _item(author="Ava", matched_members=["Ava"]),
        ]
        result = member_practices(items, ["Ava", "Ben"])
        assert [row["member"] for row in result["members"]] == ["Ben", "Ava"]
