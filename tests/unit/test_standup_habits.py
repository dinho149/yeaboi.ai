"""Unit tests for standup practice detection.

One class per rule, and for each: it fires on the real thing, it stays quiet on
the near-miss, and it stays quiet on the specific false positive the gate exists
for. The gates carry more weight than the detections here — this feature names a
person, so a wrong signal costs more than a missed one.
"""

from yeaboi.agent.state import MemberUpdate, PracticeSignal, StandupReport
from yeaboi.standup import habits
from yeaboi.standup.habits import detect_practices


def _rules(signals: dict, name: str = "Alice") -> list[str]:
    return [s.rule for s in signals.get(name, ())]


def _covered(state: str = "covered") -> dict:
    return {"ticketing": state, "code": "covered", "documentation": "covered"}


def _pr(**over) -> dict:
    item = {
        "kind": "pr",
        "key": "#91",
        "title": "Add retry to the webhook sender",
        "branch": "feature/retry",
        "body": "",
        "status": "merged",
        "repository": "acme/web",
        "url": "https://x/pull/91",
        "work_items_known": True,
    }
    item.update(over)
    return item


def _commit(**over) -> dict:
    item = {
        "kind": "commit",
        "key": "a1b2c3d4",
        "title": "Wire the SSO callback",
        "body": "",
        "repository": "acme/web",
        "url": "https://x/commit/a1b2c3d4",
    }
    item.update(over)
    return item


def _ticket(key: str, status: str, **over) -> dict:
    item = {"kind": "issue", "key": key, "title": "A ticket", "status": status, "url": f"https://x/browse/{key}"}
    item.update(over)
    return item


class TestConfiguration:
    def test_off_returns_nothing(self):
        grouped = {"Alice": [_pr()]}
        assert detect_practices(grouped, config={"habit_detection": "off"}, category_coverage=_covered()) == {}

    def test_on_is_the_default_for_a_missing_key(self):
        grouped = {"Alice": [_pr()]}
        assert detect_practices(grouped, config={}, category_coverage=_covered())

    def test_habit_rules_narrows_to_a_subset(self):
        grouped = {"Alice": [_pr(), *[_commit(key=f"c{i}", title="fix") for i in range(3)]]}
        signals = detect_practices(grouped, config={"habit_rules": "commit-messages"}, category_coverage=_covered())
        assert _rules(signals) == [habits.RULE_COMMIT_MESSAGES]

    def test_empty_habit_rules_means_all(self):
        assert habits.selected_rules({"habit_rules": ""}) == frozenset(habits.ALL_RULES)

    def test_validate_habit_rules_canonicalises_and_rejects(self):
        assert habits.validate_habit_rules("wip-sprawl,untracked-work") == "untracked-work,wip-sprawl"
        try:
            habits.validate_habit_rules("untracked-work,nonsense")
        except ValueError as exc:
            assert "nonsense" in str(exc)
        else:
            raise AssertionError("an unknown rule id must raise, not be dropped")


class TestTrackerKillSwitch:
    """No usable tracker → no ticket-shaped accusations, for anyone."""

    def test_failed_ticketing_suppresses_the_whole_family(self):
        grouped = {"Alice": [_pr(), _ticket("PSOT-1", "In Progress"), _ticket("PSOT-2", "In Progress")]}
        rules = _rules(detect_practices(grouped, category_coverage=_covered("failed")))
        assert habits.RULE_UNTRACKED_WORK not in rules
        assert habits.RULE_WIP_SPRAWL not in rules
        assert habits.RULE_BOARD_NOT_UPDATED not in rules

    def test_not_configured_ticketing_suppresses_it_too(self):
        grouped = {"Alice": [_pr()]}
        assert habits.RULE_UNTRACKED_WORK not in _rules(
            detect_practices(grouped, category_coverage=_covered("not_configured"))
        )

    def test_code_only_rules_survive_the_kill_switch(self):
        # Commit-message quality has nothing to do with the tracker.
        grouped = {"Alice": [_commit(key=f"c{i}", title="wip") for i in range(3)]}
        assert habits.RULE_COMMIT_MESSAGES in _rules(
            detect_practices(grouped, category_coverage=_covered("not_configured"))
        )


class TestUntrackedWork:
    def test_pr_with_no_reference_anywhere_fires(self):
        grouped = {"Alice": [_pr()]}
        signals = detect_practices(grouped, category_coverage=_covered())
        assert habits.RULE_UNTRACKED_WORK in _rules(signals)
        assert signals["Alice"][0].evidence == (("#91", "https://x/pull/91"),)

    def test_key_in_the_title_only_is_enough(self):
        grouped = {"Alice": [_pr(title="PSOT-12 add retry"), _ticket("PSOT-12", "Done")]}
        assert habits.RULE_UNTRACKED_WORK not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_key_in_the_branch_only_is_enough(self):
        grouped = {"Alice": [_pr(branch="feature/PSOT-12-retry"), _ticket("PSOT-12", "Done")]}
        assert habits.RULE_UNTRACKED_WORK not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_key_in_the_body_only_is_enough(self):
        grouped = {"Alice": [_pr(body="Closes PSOT-12."), _ticket("PSOT-12", "Done")]}
        assert habits.RULE_UNTRACKED_WORK not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_azdo_ui_link_counts_even_with_no_text_reference(self):
        grouped = {"Alice": [_pr(work_item_ids=("1234",), work_items_known=True)]}
        assert habits.RULE_UNTRACKED_WORK not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_azdo_pr_whose_links_could_not_be_read_stays_silent(self):
        # Unknown is not absent: the lookup is capped per repo, and a capped PR
        # must never become an accusation.
        grouped = {"Alice": [_pr(work_items_known=False)]}
        assert habits.RULE_UNTRACKED_WORK not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_lookalike_in_the_title_is_not_a_reference(self):
        # "UTF-8" matches the key regex but no tracker produced that prefix.
        grouped = {"Alice": [_pr(title="Switch the parser to UTF-8"), _ticket("PSOT-1", "Done")]}
        assert habits.RULE_UNTRACKED_WORK in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_loose_commit_fires(self):
        grouped = {"Alice": [_commit()]}
        assert habits.RULE_UNTRACKED_WORK in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_merge_commit_never_fires(self):
        grouped = {"Alice": [_commit(title="Merge pull request #91 from acme/feature")]}
        assert habits.RULE_UNTRACKED_WORK not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_squash_merge_subject_never_fires_even_with_no_pr_in_window(self):
        grouped = {"Alice": [_commit(title="fix the login redirect (#91)")]}
        assert habits.RULE_UNTRACKED_WORK not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_revert_never_fires(self):
        grouped = {"Alice": [_commit(title='Revert "chore: bump the deps"')]}
        assert habits.RULE_UNTRACKED_WORK not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_local_git_commit_never_fires(self):
        # No repository means it could never have been matched to a PR.
        grouped = {"Alice": [_commit(repository="")]}
        assert habits.RULE_UNTRACKED_WORK not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_ticket_items_are_not_candidates(self):
        grouped = {"Alice": [_ticket("PSOT-1", "Done")]}
        assert habits.RULE_UNTRACKED_WORK not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_plural_reads_correctly(self):
        grouped = {"Alice": [_pr(), _pr(key="#92", url="https://x/pull/92")]}
        detail = detect_practices(grouped, category_coverage=_covered())["Alice"][0].detail
        assert "1 other change carry" in detail or "1 other change" in detail
        assert "change(s)" not in detail


class TestUntrackedDocs:
    def _page(self, **over) -> dict:
        item = {
            "kind": "page",
            "key": "PAGE-1",
            "title": "edited 'Payments runbook'",
            "summary": "",
            "url": "https://c/1",
        }
        item.update(over)
        return item

    def test_page_with_no_reference_fires(self):
        grouped = {"Alice": [self._page()]}
        assert habits.RULE_UNTRACKED_DOCS in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_page_naming_a_ticket_is_quiet(self):
        grouped = {"Alice": [self._page(summary="PSOT-12 runbook"), _ticket("PSOT-12", "Done")]}
        assert habits.RULE_UNTRACKED_DOCS not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_one_page_edited_by_three_people_is_one_signal_each(self):
        # Confluence emits an item per editor; nobody gets three accusations.
        grouped = {name: [self._page()] for name in ("Alice", "Bo", "Cy")}
        signals = detect_practices(grouped, category_coverage=_covered())
        for name in ("Alice", "Bo", "Cy"):
            assert _rules(signals, name).count(habits.RULE_UNTRACKED_DOCS) == 1

    def test_created_and_edited_for_one_page_is_one_signal(self):
        grouped = {"Alice": [self._page(kind="page-created"), self._page()]}
        detail = detect_practices(grouped, category_coverage=_covered())["Alice"][0].detail
        assert "other page" not in detail


class TestBoardNotUpdated:
    def test_merged_pr_against_a_to_do_ticket_fires(self):
        grouped = {"Alice": [_pr(title="PSOT-12 add retry"), _ticket("PSOT-12", "To Do")]}
        assert habits.RULE_BOARD_NOT_UPDATED in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_open_pr_against_a_to_do_ticket_is_quiet(self):
        # An open PR on a not-started ticket is an ordinary morning.
        grouped = {"Alice": [_pr(title="PSOT-12 add retry", status="open"), _ticket("PSOT-12", "To Do")]}
        assert habits.RULE_BOARD_NOT_UPDATED not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_in_progress_ticket_is_quiet(self):
        grouped = {"Alice": [_pr(title="PSOT-12 add retry"), _ticket("PSOT-12", "In Progress")]}
        assert habits.RULE_BOARD_NOT_UPDATED not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_lookalike_column_is_quiet(self):
        # Exact match only: "Open Questions" is not "Open".
        grouped = {"Alice": [_pr(title="PSOT-12 add retry"), _ticket("PSOT-12", "Open Questions")]}
        assert habits.RULE_BOARD_NOT_UPDATED not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_update_items_do_not_define_the_status(self):
        # A teammate's board move (kind='update', credited to the actor) must
        # not mask the assignee's stale ticket.
        grouped = {
            "Alice": [_pr(title="PSOT-12 add retry"), _ticket("PSOT-12", "To Do")],
            "Bo": [{"kind": "update", "key": "PSOT-12", "title": "moved it", "status": "In Progress"}],
        }
        assert habits.RULE_BOARD_NOT_UPDATED in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_ticket_held_by_someone_else_is_attributed_to_the_shipper(self):
        grouped = {
            "Alice": [_pr(title="PSOT-12 add retry")],
            "Bo": [_ticket("PSOT-12", "To Do")],
        }
        signals = detect_practices(grouped, category_coverage=_covered())
        assert habits.RULE_BOARD_NOT_UPDATED in _rules(signals, "Alice")
        assert habits.RULE_BOARD_NOT_UPDATED not in _rules(signals, "Bo")


class TestWipSprawl:
    def test_four_in_progress_issues_with_no_wip_items_fires(self):
        # THE trap: jira._wip_items skips issues the updated-in-window search
        # already returned, so an active person has zero kind='wip' items.
        grouped = {"Alice": [_ticket(f"PSOT-{i}", "In Progress") for i in range(4)]}
        assert habits.RULE_WIP_SPRAWL in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_three_is_quiet(self):
        grouped = {"Alice": [_ticket(f"PSOT-{i}", "In Progress") for i in range(3)]}
        assert habits.RULE_WIP_SPRAWL not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_wip_and_issue_for_one_ticket_count_once(self):
        grouped = {
            "Alice": [
                _ticket("PSOT-1", "In Progress"),
                _ticket("PSOT-1", "In Progress", kind="wip"),
                _ticket("PSOT-2", "In Progress"),
                _ticket("PSOT-3", "In Progress"),
            ]
        }
        assert habits.RULE_WIP_SPRAWL not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_in_review_does_not_count_as_held(self):
        grouped = {"Alice": [_ticket(f"PSOT-{i}", "In Review") for i in range(5)]}
        assert habits.RULE_WIP_SPRAWL not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_update_items_do_not_count(self):
        grouped = {
            "Alice": [
                {"kind": "update", "key": f"PSOT-{i}", "title": "moved", "status": "In Progress"} for i in range(5)
            ]
        }
        assert habits.RULE_WIP_SPRAWL not in _rules(detect_practices(grouped, category_coverage=_covered()))


class TestLargeChange:
    def _paths(self, n: int, prefix: str = "src/mod") -> tuple[str, ...]:
        return tuple(f"{prefix}{i}.py" for i in range(n))

    def test_forty_reviewable_files_fires(self):
        grouped = {"Alice": [_pr(changed_paths=self._paths(40))]}
        assert habits.RULE_LARGE_CHANGE in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_thirty_nine_is_quiet(self):
        grouped = {"Alice": [_pr(changed_paths=self._paths(39))]}
        assert habits.RULE_LARGE_CHANGE not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_empty_path_list_is_unknown_not_zero(self):
        # The collectors cap detail lookups; an uncounted PR must stay silent
        # rather than be judged either way.
        grouped = {"Alice": [_pr(changed_paths=())]}
        assert habits.RULE_LARGE_CHANGE not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_a_reviewer_is_never_billed_for_the_authors_pr(self):
        # github attaches the PR's file list to every review item it emits.
        grouped = {
            "Alice": [{"kind": "review", "key": "review-1", "title": "reviewed", "changed_paths": self._paths(60)}]
        }
        assert habits.RULE_LARGE_CHANGE not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_generated_bulk_does_not_count(self):
        paths = ("uv.lock", "package-lock.json", *[f"dist/chunk{i}.js" for i in range(50)])
        grouped = {"Alice": [_pr(changed_paths=paths)]}
        assert habits.RULE_LARGE_CHANGE not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_a_docs_only_mega_change_is_quiet(self):
        grouped = {"Alice": [_pr(changed_paths=self._paths(45, prefix="docs/page"))]}
        # …and the paths must really be docs for the exclusion to apply.
        grouped["Alice"][0]["changed_paths"] = tuple(f"docs/page{i}.md" for i in range(45))
        assert habits.RULE_LARGE_CHANGE not in _rules(detect_practices(grouped, category_coverage=_covered()))


class TestNoPullRequest:
    def _commits(self, n: int, repo: str = "acme/web") -> list[dict]:
        return [_commit(key=f"sha{i}", repository=repo, title=f"Add the handler for case {i}") for i in range(n)]

    def test_three_loose_commits_in_a_pr_using_repo_fires(self):
        grouped = {"Alice": [_pr(), *self._commits(3)]}
        assert habits.RULE_NO_PULL_REQUEST in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_two_is_quiet(self):
        grouped = {"Alice": [_pr(), *self._commits(2)]}
        assert habits.RULE_NO_PULL_REQUEST not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_a_repo_with_no_member_pr_is_quiet(self):
        # A trunk-based repo is a team decision, not a personal habit.
        grouped = {"Alice": self._commits(5)}
        assert habits.RULE_NO_PULL_REQUEST not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_the_gate_is_per_repository(self):
        grouped = {"Alice": [_pr(repository="acme/web"), *self._commits(4, repo="acme/other")]}
        assert habits.RULE_NO_PULL_REQUEST not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_merge_commits_do_not_count(self):
        grouped = {
            "Alice": [_pr(), *[_commit(key=f"m{i}", title=f"Merge pull request #{i} from acme/x") for i in range(4)]]
        }
        assert habits.RULE_NO_PULL_REQUEST not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_local_git_commits_do_not_count(self):
        grouped = {"Alice": [_pr(), *self._commits(4, repo="")]}
        assert habits.RULE_NO_PULL_REQUEST not in _rules(detect_practices(grouped, category_coverage=_covered()))


class TestCommitMessages:
    def _thin(self, *subjects) -> dict:
        return {"Alice": [_commit(key=f"s{i}", title=s) for i, s in enumerate(subjects)]}

    def test_three_low_information_subjects_fire(self):
        grouped = self._thin("fix", "wip", "update")
        assert habits.RULE_COMMIT_MESSAGES in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_two_is_quiet(self):
        grouped = self._thin("fix", "wip")
        assert habits.RULE_COMMIT_MESSAGES not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_conventional_commits_with_real_bodies_are_quiet(self):
        grouped = self._thin(
            "fix: null-deref in the auth guard",
            "feat: add the retry backoff",
            "chore: pin the runner image",
        )
        assert habits.RULE_COMMIT_MESSAGES not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_a_bare_conventional_prefix_is_thin(self):
        grouped = self._thin("fix:", "chore:", "feat:")
        assert habits.RULE_COMMIT_MESSAGES in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_collector_tails_do_not_rescue_a_thin_subject(self):
        grouped = self._thin("wip (PR #4)", "fix (acme-web)", "update (acme-web)")
        assert habits.RULE_COMMIT_MESSAGES in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_a_bare_ticket_key_says_nothing_about_what_changed(self):
        grouped = self._thin("PSOT-1", "PSOT-2", "PSOT-3")
        assert habits.RULE_COMMIT_MESSAGES in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_merge_and_revert_subjects_are_skipped(self):
        grouped = self._thin(
            "Merge pull request #1 from acme/x",
            'Revert "wip"',
            "Merged PR 2: fix",
            "fix",
        )
        assert habits.RULE_COMMIT_MESSAGES not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_duplicate_shas_count_once(self):
        grouped = {"Alice": [_commit(key="same", title="fix") for _ in range(5)]}
        assert habits.RULE_COMMIT_MESSAGES not in _rules(detect_practices(grouped, category_coverage=_covered()))


class TestCapsAndRollup:
    def test_capped_at_three_signals_per_member(self):
        grouped = {
            "Alice": [
                _pr(changed_paths=tuple(f"src/m{i}.py" for i in range(40))),
                *[_ticket(f"PSOT-{i}", "In Progress") for i in range(4)],
                *[_commit(key=f"c{i}", title="fix") for i in range(3)],
            ]
        }
        assert len(detect_practices(grouped, category_coverage=_covered())["Alice"]) == 3

    def test_rollup_counts_members_not_signals(self):
        grouped = {name: [_pr(url=f"https://x/pull/{name}")] for name in ("Alice", "Bo")}
        signals = detect_practices(grouped, category_coverage=_covered())
        assert habits.rollup(signals) == ((habits.RULE_UNTRACKED_WORK, 2),)

    def test_rollup_follows_all_rules_order(self):
        signals = {
            "Alice": (
                PracticeSignal(rule=habits.RULE_COMMIT_MESSAGES),
                PracticeSignal(rule=habits.RULE_UNTRACKED_WORK),
            )
        }
        assert [rule for rule, _ in habits.rollup(signals)] == [
            habits.RULE_UNTRACKED_WORK,
            habits.RULE_COMMIT_MESSAGES,
        ]

    def test_members_with_nothing_are_absent(self):
        grouped = {"Alice": [_pr(title="PSOT-1 x"), _ticket("PSOT-1", "Done")], "Quiet": []}
        assert detect_practices(grouped, category_coverage=_covered()) == {}


class TestRepeats:
    def _prev(self, *rules) -> StandupReport:
        return StandupReport(
            date="2026-07-24",
            member_updates=(MemberUpdate(name="Alice", practices=tuple(PracticeSignal(rule=r) for r in rules)),),
        )

    def test_a_rule_that_also_fired_yesterday_is_marked(self):
        grouped = {"Alice": [_pr()]}
        signals = detect_practices(
            grouped, category_coverage=_covered(), previous_report=self._prev(habits.RULE_UNTRACKED_WORK)
        )
        assert signals["Alice"][0].repeat is True

    def test_a_new_rule_is_not_marked(self):
        grouped = {"Alice": [_pr()]}
        signals = detect_practices(
            grouped, category_coverage=_covered(), previous_report=self._prev(habits.RULE_WIP_SPRAWL)
        )
        assert signals["Alice"][0].repeat is False

    def test_no_previous_report_marks_nothing(self):
        grouped = {"Alice": [_pr()]}
        assert detect_practices(grouped, category_coverage=_covered())["Alice"][0].repeat is False


class TestCoverageShapes:
    def test_accepts_the_reports_tuple_of_pairs(self):
        grouped = {"Alice": [_pr()]}
        pairs = (("ticketing", "covered"), ("code", "covered"), ("documentation", "covered"))
        assert habits.RULE_UNTRACKED_WORK in _rules(detect_practices(grouped, category_coverage=pairs))

    def test_missing_coverage_defaults_to_usable(self):
        # A direct caller (or a test) that passes nothing gets the rules, not
        # silence — the kill switch is for a *known* failure.
        grouped = {"Alice": [_pr()]}
        assert habits.RULE_UNTRACKED_WORK in _rules(detect_practices(grouped))


def _open_ticket(key: str, title: str, body: str = "") -> dict:
    """An open ticket nobody touched today — matching context, never activity."""
    return {
        "kind": "ticket_context",
        "key": key,
        "title": title,
        "status": "In Progress",
        "source": "jira",
        "url": f"https://x/browse/{key}",
        "body": body,
    }


class TestRelatednessSuppression:
    """The reported false positive, and the gates that keep the fix honest."""

    _TICKET = _ticket(
        "PSOT-77",
        "In Progress",
        title="Rename the approval plugins",
        body=(
            "The pipeline approval plugin and the access request plugin should use their new names.\n"
            "Definition of done:\n- [ ] Documentation\n- [ ] Proper Testing"
        ),
    )
    _COMMIT = _commit(key="bf132e43", title="Rename the plugins to pipeline-approval and access-request")

    def test_a_commit_matching_a_ticket_is_not_reported(self):
        grouped = {"Alice": [self._TICKET, self._COMMIT]}
        assert habits.RULE_UNTRACKED_WORK not in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_an_unrelated_commit_is_still_reported(self):
        unrelated = _ticket("PSOT-90", "In Progress", title="Migrate the billing schema", body="Backfill the tables.")
        grouped = {"Alice": [unrelated, self._COMMIT]}
        assert habits.RULE_UNTRACKED_WORK in _rules(detect_practices(grouped, category_coverage=_covered()))

    def test_an_open_ticket_nobody_touched_today_can_still_claim_a_commit(self):
        # The largest source of false reports: a ticket raised last sprint and
        # quietly worked ever since sees no board movement today.
        context = _open_ticket("PSOT-77", "Rename the approval plugins", self._TICKET["body"])
        signals = detect_practices(
            {"Alice": [self._COMMIT]},
            category_coverage=_covered(),
            reference_grouped={"Alice": [context]},
            reference_items=[context],
        )
        assert habits.RULE_UNTRACKED_WORK not in _rules(signals)

    def test_a_commit_naming_its_key_survives_a_day_with_no_board_movement(self):
        """The gate is about what the tracker HOLDS, not what moved today.

        Prefixes used to be read off the window's tracker items alone, so on a
        day nobody touched a ticket the set went empty, ``has_tracker_reference``
        short-circuited, and a commit that spells its key out loud was reported
        as untracked. A quiet Monday would accuse a named person of exactly the
        thing they had just done correctly.
        """
        context = _open_ticket("PSOT-77", "Rename the approval plugins", "Unrelated prose, so only the key can match.")
        spelled = _commit(key="d4", title="PSOT-77 rename the plugins")
        signals = detect_practices(
            {"Alice": [spelled]},  # no issue/update/comment item anywhere in the window
            category_coverage=_covered(),
            reference_items=[context],
        )
        assert habits.RULE_UNTRACKED_WORK not in _rules(signals)

    def test_a_key_no_tracker_has_ever_produced_is_still_reported(self):
        # The gate must still be a gate: widening it to the open tickets must not
        # turn it into "any Jira-shaped string counts".
        context = _open_ticket("PSOT-77", "Rename the approval plugins", "Prose.")
        invented = _commit(key="d5", title="NOPE-12 fix the thing")
        signals = detect_practices(
            {"Alice": [invented]},
            category_coverage=_covered(),
            reference_items=[context],
        )
        assert habits.RULE_UNTRACKED_WORK in _rules(signals)

    def test_the_matched_ticket_is_never_named(self):
        # Suppression is silent, which is exactly what makes matching the wrong
        # sibling ticket in an epic cost nothing.
        near = _ticket("PSOT-78", "In Progress", title="Approval plugin follow-up", body="More pipeline approval work.")
        grouped = {"Alice": [near, _commit(key="c1", title="Rename pipeline-approval"), _pr(title="Unrelated thing")]}
        for signal in detect_practices(grouped, category_coverage=_covered()).get("Alice", ()):
            assert "PSOT-78" not in signal.detail
            assert all("PSOT-78" not in label for label, _url in signal.evidence)

    def test_the_message_says_we_checked_only_when_we_could(self):
        unrelated = _ticket("PSOT-90", "In Progress", title="Migrate the billing schema", body="Backfill the tables.")
        with_tickets = detect_practices({"Alice": [unrelated, self._COMMIT]}, category_coverage=_covered())
        without = detect_practices({"Alice": [self._COMMIT]}, category_coverage=_covered())
        assert "matches a ticket the team has open" in with_tickets["Alice"][0].detail
        # No tickets in the window means no check ran, so claiming one would lie.
        assert "matches a ticket" not in without["Alice"][0].detail

    def test_relatedness_can_only_ever_remove_rules(self):
        # The governing invariant, executable. Context goes in as CONTEXT on
        # both runs' inputs, so the only variable is whether the matcher can
        # see it — adding activity would legitimately add rules and prove
        # nothing about relatedness.
        docs = _commit(key="d1", title="Document the approval flow", changed_paths=("docs/pipeline-approval.md",))
        contexts = [
            _open_ticket("PSOT-77", "Rename the approval plugins", self._TICKET["body"]),
            _open_ticket("Q-1", "Something else entirely", "Nothing to do with any of this."),
            _open_ticket("Q-2", "Checkout resilience", "Definition of done:\n- [ ] Documentation"),
        ]
        activities = [
            [self._COMMIT],
            [docs],
            [self._COMMIT, docs, _pr(title="Unrelated thing", branch="x")],
            [*[_commit(key=f"z{i}", title="fix") for i in range(3)], self._COMMIT],
        ]
        for items in activities:
            with_context = set(
                _rules(
                    detect_practices(
                        {"Alice": items},
                        category_coverage=_covered(),
                        reference_grouped={"Alice": contexts},
                        reference_items=contexts,
                    )
                )
            )
            without = set(_rules(detect_practices({"Alice": items}, category_coverage=_covered())))
            assert with_context <= without, items

    def test_board_hygiene_never_consumes_a_fuzzy_match(self):
        # That rule builds its accusation AROUND a named key, so it must stay
        # purely syntactic — its output cannot move when context is added.
        pr = _pr(title="PSOT-77 rename the plugins", status="merged")
        todo = _ticket("PSOT-77", "To Do", title="Rename the approval plugins", body=self._TICKET["body"])
        with_ctx = detect_practices({"Alice": [todo, pr]}, category_coverage=_covered())["Alice"]
        details = [s.detail for s in with_ctx if s.rule == habits.RULE_BOARD_NOT_UPDATED]
        assert details and "PSOT-77" in details[0]


class TestAdjudicationSeam:
    _TICKET = _ticket("PSOT-5", "In Progress", title="Checkout resilience", body="Customers cannot check out.")
    _COMMIT = _commit(key="e1", title="Fix the cart total rounding error")

    def _grouped(self) -> dict:
        return {"Alice": [self._TICKET, self._COMMIT]}

    def test_no_adjudicator_leaves_the_deterministic_verdict(self):
        signals = detect_practices(self._grouped(), category_coverage=_covered())
        assert habits.RULE_UNTRACKED_WORK in _rules(signals)

    def test_an_adjudicator_can_drop_a_report(self):
        seen: list = []

        def adjudicator(cases):
            seen.extend(cases)
            return [case.case_id for case in cases]

        signals = detect_practices(self._grouped(), category_coverage=_covered(), adjudicator=adjudicator)
        assert habits.RULE_UNTRACKED_WORK not in _rules(signals)
        # It is shown the change and the tickets it might belong to, nothing else.
        assert seen and seen[0].subject == "Fix the cart total rounding error"
        assert [key for key, _title, _text in seen[0].candidates] == ["PSOT-5"]

    def test_ids_we_did_not_send_are_ignored(self):
        signals = detect_practices(
            self._grouped(), category_coverage=_covered(), adjudicator=lambda cases: ["made-up", "work-999"]
        )
        assert habits.RULE_UNTRACKED_WORK in _rules(signals)

    def test_an_adjudicator_that_raises_keeps_every_verdict(self):
        def boom(_cases):
            raise RuntimeError("model down")

        signals = detect_practices(self._grouped(), category_coverage=_covered(), adjudicator=boom)
        assert habits.RULE_UNTRACKED_WORK in _rules(signals)

    def test_an_adjudicator_cannot_introduce_a_rule(self):
        # It returns ids to DROP, so there is no shape in which a verdict adds
        # a report. Pinned because that is the whole safety argument.
        quiet = {"Alice": [self._TICKET]}
        assert detect_practices(quiet, category_coverage=_covered(), adjudicator=lambda cases: ["work-0"]) == {}


class TestFeedbackExcuses:
    """The team's own verdicts, applied at detection time.

    Same shape as the adjudicator seam above and for the same reason: it answers
    a yes/no about one change, and a yes removes a report. There is no answer
    that adds one.
    """

    def _excusing(self, *pairs) -> object:
        wanted = set(pairs)
        return lambda rule, handle: (rule, handle) in wanted

    def test_an_excused_change_stops_being_reported(self):
        grouped = {"Alice": [_pr(status="open")]}
        handle = habits.change_handle(_pr(status="open"))
        signals = detect_practices(
            grouped,
            category_coverage=_covered(),
            feedback=self._excusing((habits.RULE_UNTRACKED_WORK, handle)),
        )
        assert habits.RULE_UNTRACKED_WORK not in _rules(signals)

    def test_excusing_one_rule_leaves_the_others_firing(self):
        # The same pull request can be both untracked and oversized; the team
        # said the first was wrong, not the second.
        pr = _pr(status="open", changed_paths=tuple(f"src/f{i}.py" for i in range(40)))
        handle = habits.change_handle(pr)
        signals = detect_practices(
            {"Alice": [pr]},
            category_coverage=_covered(),
            feedback=self._excusing((habits.RULE_UNTRACKED_WORK, handle)),
        )
        assert habits.RULE_UNTRACKED_WORK not in _rules(signals)
        assert habits.RULE_LARGE_CHANGE in _rules(signals)

    def test_a_partly_excused_signal_recounts_its_sentence(self):
        # The reason a verdict is remembered per change rather than per signal:
        # what is left still gets reported, and gets counted correctly.
        prs = [_pr(key=f"#{i}", url=f"https://x/pull/{i}", status="open") for i in (1, 2, 3)]
        excused = habits.change_handle(prs[0])
        signals = detect_practices(
            {"Alice": prs},
            category_coverage=_covered(),
            feedback=self._excusing((habits.RULE_UNTRACKED_WORK, excused)),
        )
        detail = next(s.detail for s in signals["Alice"] if s.rule == habits.RULE_UNTRACKED_WORK)
        assert "1 other change" in detail
        assert excused not in next(s.handles for s in signals["Alice"] if s.rule == habits.RULE_UNTRACKED_WORK)

    def test_excusing_takes_a_threshold_rule_back_under_its_bar(self):
        # Three thin subjects fire; excusing one has to silence the rule, not
        # report two of them.
        commits = [_commit(key=f"s{i}", title=t) for i, t in enumerate(("fix", "wip", "update"))]
        signals = detect_practices(
            {"Alice": commits},
            category_coverage=_covered(),
            feedback=self._excusing((habits.RULE_COMMIT_MESSAGES, habits.change_handle(commits[0]))),
        )
        assert habits.RULE_COMMIT_MESSAGES not in _rules(signals)

    def test_feedback_cannot_introduce_a_rule(self):
        # The mirror of the adjudicator test above — an Excuser that says yes to
        # everything makes the module silent, never louder.
        grouped = {"Alice": [_pr(status="open")]}
        assert detect_practices(grouped, category_coverage=_covered(), feedback=lambda r, h: True) == {}

    def test_an_excused_change_never_reaches_the_adjudicator(self):
        # No point spending a language-model slot on a question the team has
        # already answered.
        seen: list = []
        pr = _pr(status="open")
        signals = detect_practices(
            {"Alice": [_ticket("PSOT-5", "In Progress", title="Retry the webhook sender"), pr]},
            category_coverage=_covered(),
            feedback=self._excusing((habits.RULE_UNTRACKED_WORK, habits.change_handle(pr))),
            adjudicator=lambda cases: seen.extend(cases) or [],
        )
        assert habits.RULE_UNTRACKED_WORK not in _rules(signals)
        assert seen == []

    def test_no_feedback_is_exactly_the_old_behaviour(self):
        grouped = {"Alice": [_pr(status="open")]}
        assert _rules(detect_practices(grouped, category_coverage=_covered())) == _rules(
            detect_practices(grouped, category_coverage=_covered(), feedback=None)
        )
