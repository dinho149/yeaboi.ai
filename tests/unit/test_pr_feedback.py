"""Tests for scripts/pr_feedback.py — the gate that blocks a merge on unread review feedback.

The script's whole value is that it is *fail-closed and cannot deadlock*, and both
halves of that are easy to break in a way nothing notices. A gate that is too
lenient re-creates the original problem silently — PRs merge past findings again,
and the check sits green while they do. A gate that is too strict is worse in
practice, because the first PR it wedges is the one that gets it deleted.

So every state in the table below is pinned here, on hand-built snapshots. Nothing
in this file calls ``gh`` or touches the network: ``classify`` and everything it
calls are pure, which is exactly why the fetching was kept out of them.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# scripts/ is not a package, so load the module straight from its file path.
_MODULE_PATH = ROOT / "scripts" / "pr_feedback.py"
_spec = importlib.util.spec_from_file_location("pr_feedback", _MODULE_PATH)
prf = importlib.util.module_from_spec(_spec)
# Registered before exec: @dataclass resolves annotations through
# sys.modules[cls.__module__], which is None for a module loaded off a path.
sys.modules["pr_feedback"] = prf
_spec.loader.exec_module(prf)

NOW = datetime(2026, 8, 6, 12, 0, 0, tzinfo=UTC)
AUTHOR = "dinho"
HEAD = "abc1234def5678"

REVIEW_MARK = "<!-- pr-feedback: claude-review open={n} -->"
DOD_MARK = "<!-- cowork-dod open={n} -->"


def comment(
    body: str,
    *,
    minutes_ago: int = 60,
    author: str = "github-actions[bot]",
    ident: int = 1,
    association: str = "NONE",
    edited_minutes_ago: int | None = None,
    kind: str = "comment",
):
    return prf.Comment(
        id=ident,
        author=author,
        body=body,
        created_at=NOW - timedelta(minutes=minutes_ago),
        updated_at=None if edited_minutes_ago is None else NOW - timedelta(minutes=edited_minutes_ago),
        association=association,
        kind=kind,
    )


# The login `claude-review.yml` actually posts under — the Claude GitHub App.
# `is_authentic_verdict` pins the producer to it, so a fixture using any other
# identity is not testing the real thing.
REVIEWER = "claude[bot]"


def review(
    n: int,
    *,
    minutes_ago: int = 60,
    ident: int = 1,
    edited_minutes_ago: int | None = None,
    author: str = REVIEWER,
):
    return comment(
        f"Findings...\n\n{REVIEW_MARK.format(n=n)}",
        minutes_ago=minutes_ago,
        ident=ident,
        author=author,
        edited_minutes_ago=edited_minutes_ago,
    )


def forged(
    n: int = 0,
    *,
    producer: str = "claude-review",
    minutes_ago: int = 5,
    ident: int = 9,
    author: str = "a-stranger",
    association: str = "NONE",
):
    """A verdict somebody other than the producer wrote, claiming ``n`` findings."""
    mark = REVIEW_MARK if producer == "claude-review" else DOD_MARK
    return comment(mark.format(n=n), minutes_ago=minutes_ago, ident=ident, author=author, association=association)


def ack(
    producer: str = "claude-review",
    *,
    minutes_ago: int = 30,
    ident: int = 2,
    association: str = "OWNER",
    author: str = AUTHOR,
    **kw,
):
    """A won't-fix reply. Trusted by default — the untrusted case is its own test."""
    return comment(
        f"Won't fix, because the caller already guards it.\n\n<!-- addressed: {producer} -->",
        minutes_ago=minutes_ago,
        author=author,
        ident=ident,
        association=association,
        **kw,
    )


def thread(
    *, resolved: bool = False, authors: tuple[str, ...] = ("reviewer",), ident: str = "T1", outdated: bool = False
):
    return prf.Thread(
        id=ident,
        is_resolved=resolved,
        is_outdated=outdated,
        path="src/yeaboi/standup/collector.py",
        line=88,
        authors=authors,
        excerpt="this drops the last page",
    )


def snapshot(**overrides):
    """A PR whose CI went green an hour ago — old enough that the grace has lapsed."""
    base = dict(
        number=123,
        head_sha=HEAD,
        author=AUTHOR,
        is_draft=False,
        labels=(),
        review_decision=None,
        comments=(),
        threads=(),
        ci=prf.CIState("success", NOW - timedelta(minutes=60)),
        threads_truncated=False,
    )
    base.update(overrides)
    return prf.Snapshot(**base)


class TestProducerVerdicts:
    def test_a_clean_review_passes(self):
        verdict = prf.classify(snapshot(comments=(review(0),)), NOW)
        assert verdict.state == "success"
        assert verdict.items == ()

    def test_unanswered_findings_fail(self):
        verdict = prf.classify(snapshot(comments=(review(2),)), NOW)
        assert verdict.state == "failure"
        assert len(verdict.items) == 1
        assert "2 unanswered findings" in verdict.items[0].detail

    def test_one_finding_reads_singular(self):
        verdict = prf.classify(snapshot(comments=(review(1),)), NOW)
        assert "1 unanswered finding (" in verdict.items[0].detail

    def test_a_newer_acknowledgement_clears_them(self):
        snap = snapshot(comments=(review(2, minutes_ago=60), ack(minutes_ago=30)))
        assert prf.classify(snap, NOW).state == "success"

    def test_an_older_acknowledgement_does_not(self):
        # The reply predates the review pass, so it answered something else — the
        # case a naive "is there an ack anywhere" check would wave straight through.
        snap = snapshot(comments=(review(2, minutes_ago=30), ack(minutes_ago=60)))
        assert prf.classify(snap, NOW).state == "failure"

    def test_an_acknowledgement_is_scoped_to_its_producer(self):
        snap = snapshot(comments=(review(2, minutes_ago=60), ack("cowork-dod", minutes_ago=30)))
        assert prf.classify(snap, NOW).state == "failure"

    def test_only_the_latest_pass_counts(self):
        # The fix landed and the re-review said so. No reply should be needed —
        # this is what stops the gate from becoming a box nobody can tick.
        snap = snapshot(comments=(review(3, minutes_ago=90, ident=1), review(0, minutes_ago=10, ident=2)))
        assert prf.classify(snap, NOW).state == "success"

    def test_a_regression_reopens_the_gate(self):
        snap = snapshot(comments=(review(0, minutes_ago=90, ident=1), review(1, minutes_ago=10, ident=2)))
        assert prf.classify(snap, NOW).state == "failure"

    def test_equal_timestamps_break_ties_on_id(self):
        snap = snapshot(comments=(review(4, minutes_ago=10, ident=1), review(0, minutes_ago=10, ident=2)))
        assert prf.classify(snap, NOW).state == "success"

    def test_the_dod_audit_blocks_when_it_reports_unmet_items(self):
        snap = snapshot(comments=(review(0), comment(DOD_MARK.format(n=2), ident=3)))
        verdict = prf.classify(snap, NOW)
        assert verdict.state == "failure"
        assert verdict.items[0].key == "cowork-dod"

    def test_the_dod_audit_is_never_waited_on(self):
        # It is a hand-registered cowork routine that may not be deployed at all.
        assert prf.classify(snapshot(comments=(review(0),)), NOW).state == "success"

    def test_a_markerless_dod_comment_is_not_a_verdict(self):
        # The pre-`open=` format must read as "said nothing", not as "said zero" —
        # otherwise a routine nobody updated could clear the gate by staying quiet.
        snap = snapshot(comments=(review(0), comment("<!-- cowork-dod -->", ident=3)))
        assert prf.latest_verdict(snap.comments, prf.PRODUCERS[1]) is None


class TestTheReviewCap:
    """The loop needs a terminator, and the terminator must not break the gate.

    Four consecutive rounds on PR #222 each produced real should-fix findings.
    An adversarial review of a large diff always finds something, so "merge when
    the reviewer reports zero" is not a condition that reliably arrives — and a
    gate whose exit may never occur is a gate that gets deleted.
    """

    def test_under_the_cap_findings_still_block(self):
        snap = snapshot(comments=(review(2, minutes_ago=60, ident=1),))
        assert prf.classify(snap, NOW).state == "failure"

    def test_at_the_cap_the_gate_opens(self):
        snap = snapshot(comments=(review(2, minutes_ago=90, ident=1), review(1, minutes_ago=10, ident=2)))
        verdict = prf.classify(snap, NOW)
        assert verdict.state == "success"
        assert "capped" in verdict.description

    def test_the_findings_are_kept_not_discarded(self):
        """Green is not clean. The items ride along so the comment can list them."""
        snap = snapshot(comments=(review(2, minutes_ago=90, ident=1), review(3, minutes_ago=10, ident=2)))
        verdict = prf.classify(snap, NOW)
        assert verdict.items and verdict.items[0].key == "claude-review"
        assert "not fixed" in verdict.description

    def test_a_clean_pass_does_not_spend_a_round(self):
        """Otherwise a regression after a clean review could never reopen the gate.

        Counting every verdict would make "review found a new problem" and
        "review ran out of patience" the same state.
        """
        snap = snapshot(comments=(review(0, minutes_ago=90, ident=1), review(1, minutes_ago=10, ident=2)))
        assert prf.classify(snap, NOW).state == "failure"

    def test_a_forged_verdict_cannot_burn_a_round(self):
        """Otherwise two comments from anybody would cap somebody else's PR."""
        forged = comment(REVIEW_MARK.format(n=1), minutes_ago=50, ident=9, author="a-stranger")
        rounds = prf.review_rounds((review(1, minutes_ago=60, ident=1), forged), prf.PRODUCERS[0])
        assert rounds == 1

    def test_an_unresolved_human_thread_is_never_capped(self):
        """A person waiting for an answer is not a loop that has run too long."""
        snap = snapshot(
            comments=(review(1, minutes_ago=90, ident=1), review(1, minutes_ago=10, ident=2)),
            threads=(thread(authors=("a-reviewer",)),),
        )
        assert prf.classify(snap, NOW).state == "failure"

    def test_a_changes_requested_review_is_never_capped(self):
        snap = snapshot(
            comments=(review(1, minutes_ago=90, ident=1), review(1, minutes_ago=10, ident=2)),
            review_decision="CHANGES_REQUESTED",
        )
        assert prf.classify(snap, NOW).state == "failure"

    def test_the_capped_comment_does_not_read_as_clean(self):
        snap = snapshot(comments=(review(2, minutes_ago=90, ident=1), review(2, minutes_ago=10, ident=2)))
        body = prf.sticky_body(snap, prf.classify(snap, NOW))
        assert "recorded, not fixed" in body
        assert "clear" not in body.lower().split("recorded")[0]
        assert prf.CAPPED_LABEL in body

    def test_the_workflow_and_the_script_cap_at_the_same_number(self):
        """Reviewing past the cap would write findings nothing will ever act on."""
        text = (ROOT / ".github" / "workflows" / "claude-review.yml").read_text(encoding="utf-8")
        assert f"-ge {prf.MAX_REVIEW_ROUNDS} ]" in text, "claude-review.yml stopped agreeing with MAX_REVIEW_ROUNDS"


class TestWaitingVersusMissing:
    def test_no_ci_run_yet_is_pending(self):
        snap = snapshot(ci=prf.CIState(None, None))
        assert prf.classify(snap, NOW).state == "pending"

    def test_red_ci_is_pending_and_says_so(self):
        snap = snapshot(ci=prf.CIState("failure", NOW - timedelta(hours=2)))
        verdict = prf.classify(snap, NOW)
        assert verdict.state == "pending"
        assert "CI is failure" in verdict.description

    def test_inside_the_grace_window_is_pending(self):
        snap = snapshot(ci=prf.CIState("success", NOW - timedelta(minutes=5)))
        assert prf.classify(snap, NOW).state == "pending"

    def test_past_the_grace_window_a_missing_review_is_a_failure(self):
        # claude-review.yml has silently stopped firing twice in this repo. That
        # is the failure this state exists to make loud.
        snap = snapshot(ci=prf.CIState("success", NOW - timedelta(minutes=45)))
        verdict = prf.classify(snap, NOW)
        assert verdict.state == "failure"
        assert "never posted a verdict" in verdict.items[0].detail

    def test_an_open_thread_beats_waiting_for_the_review(self):
        snap = snapshot(ci=prf.CIState("success", NOW - timedelta(minutes=1)), threads=(thread(),))
        assert prf.classify(snap, NOW).state == "failure"


class TestHumanThreads:
    def test_an_unresolved_thread_blocks(self):
        verdict = prf.classify(snapshot(comments=(review(0),), threads=(thread(),)), NOW)
        assert verdict.state == "failure"
        assert "collector.py:88" in verdict.items[0].detail

    def test_a_resolved_thread_does_not(self):
        snap = snapshot(comments=(review(0),), threads=(thread(resolved=True),))
        assert prf.classify(snap, NOW).state == "success"

    def test_the_author_talking_to_themselves_does_not(self):
        snap = snapshot(comments=(review(0),), threads=(thread(authors=(AUTHOR, AUTHOR)),))
        assert prf.classify(snap, NOW).state == "success"

    def test_an_outdated_thread_still_blocks(self):
        # The line moved; the point was not taken. Only Resolve closes one.
        snap = snapshot(comments=(review(0),), threads=(thread(outdated=True),))
        assert prf.classify(snap, NOW).state == "failure"

    def test_changes_requested_blocks(self):
        snap = snapshot(comments=(review(0),), review_decision="CHANGES_REQUESTED")
        verdict = prf.classify(snap, NOW)
        assert verdict.state == "failure"
        assert verdict.items[0].kind == "changes-requested"

    def test_truncated_thread_pages_are_reported_not_swallowed(self):
        snap = snapshot(comments=(review(0),), threads_truncated=True)
        verdict = prf.classify(snap, NOW)
        assert verdict.state == "failure"
        assert verdict.items[0].kind == "truncated"


class TestNotApplicable:
    def test_a_draft_passes(self):
        # Mirrors claude-review.yml's own filter: it never reviews a draft, so a
        # gate that waited for one would wedge the PR with nothing to fix.
        snap = snapshot(is_draft=True, ci=prf.CIState("success", NOW - timedelta(hours=3)))
        assert prf.classify(snap, NOW).state == "success"

    def test_a_dependabot_pr_passes(self):
        snap = snapshot(author="dependabot[bot]", ci=prf.CIState("success", NOW - timedelta(hours=3)))
        assert prf.classify(snap, NOW).state == "success"

    def test_a_bot_pr_labelled_cowork_is_still_gated(self):
        # The claude.yml implement job opens PRs unattended as a bot. Those are
        # exactly the PRs nobody watched being written.
        snap = snapshot(
            author="github-actions[bot]",
            labels=("cowork",),
            ci=prf.CIState("success", NOW - timedelta(hours=3)),
        )
        assert prf.classify(snap, NOW).state == "failure"

    def test_the_override_label_clears_everything(self):
        snap = snapshot(comments=(review(5),), threads=(thread(),), labels=("feedback-override",))
        verdict = prf.classify(snap, NOW)
        assert verdict.state == "success"
        assert "feedback-override" in verdict.description

    def test_the_override_is_reported_in_the_sticky_comment(self):
        snap = snapshot(comments=(review(5),), labels=("feedback-override",))
        body = prf.sticky_body(snap, prf.classify(snap, NOW))
        assert "feedback-override" in body


class TestDescription:
    def test_it_fits_githubs_limit(self):
        items = tuple(prf.OpenItem("thread", f"T{i}", "x" * 200) for i in range(5))
        assert len(prf.describe(items)) <= prf.DESCRIPTION_LIMIT

    def test_it_counts_then_names(self):
        snap = snapshot(comments=(review(0),), threads=(thread(ident="T1"), thread(ident="T2")))
        verdict = prf.classify(snap, NOW)
        assert verdict.description.startswith("2 unanswered review items")
        assert "+1 more" in verdict.description


class TestMarkers:
    @pytest.mark.parametrize(
        "body,expected",
        [
            ("<!-- addressed: claude-review -->", {"claude-review"}),
            ("<!--addressed:claude-review-->", {"claude-review"}),
            ("<!-- Addressed: Claude-Review -->", {"claude-review"}),
            ("text\n<!-- addressed: cowork-dod -->\nmore", {"cowork-dod"}),
            ("nothing here", set()),
        ],
    )
    def test_acknowledgement_parsing(self, body, expected):
        assert prf.acknowledged_producers(body) == expected

    def test_timestamps_are_normalised_to_utc(self):
        assert prf.parse_timestamp("2026-08-06T12:00:00Z") == NOW
        assert prf.parse_timestamp("2026-08-06T12:00:00+00:00") == NOW
        assert prf.parse_timestamp(None) is None
        assert prf.parse_timestamp("not a date") is None


class TestStickyComment:
    def test_a_failing_verdict_lists_every_item_and_the_way_out(self):
        snap = snapshot(comments=(review(2),), threads=(thread(),))
        body = prf.sticky_body(snap, prf.classify(snap, NOW))
        assert prf.STICKY_MARKER in body
        assert "/pr-feedback 123" in body
        assert "collector.py:88" in body
        assert "<!-- addressed:" in body

    @pytest.mark.parametrize(
        "threads,expected",
        [((), "until it is answered"), ((thread(ident="T2"),), "until they are answered")],
    )
    def test_it_agrees_with_itself_on_number(self, threads, expected):
        snap = snapshot(comments=(review(2),), threads=threads)
        assert expected in prf.sticky_body(snap, prf.classify(snap, NOW))

    def test_a_clear_verdict_says_so_in_one_line(self):
        snap = snapshot(comments=(review(0),))
        body = prf.sticky_body(snap, prf.classify(snap, NOW))
        assert "clear" in body.lower()


class TestEditedComments:
    """A verdict is dated by when its text was last written, not when it appeared.

    ``pr-opened-dod-audit.md`` step 5 tells the DoD audit to **edit one comment in
    place** on every push rather than pile up a new one, and an edit does not move
    ``created_at``. Dating a verdict by creation therefore let a reply written the
    hour the PR opened go on clearing findings that were rewritten into that same
    comment minutes ago — a silent false green in the one producer designed to
    behave this way.
    """

    def test_an_edited_in_verdict_outranks_an_older_acknowledgement(self):
        snap = snapshot(
            comments=(
                comment(DOD_MARK.format(n=3), minutes_ago=600, edited_minutes_ago=5, ident=1),
                ack("cowork-dod", minutes_ago=300, ident=2),
                review(0, minutes_ago=60, ident=3),
            )
        )
        verdict = prf.classify(snap, NOW)
        assert verdict.state == "failure"
        assert verdict.items[0].key == "cowork-dod"

    def test_an_acknowledgement_after_the_edit_clears_it(self):
        snap = snapshot(
            comments=(
                comment(DOD_MARK.format(n=3), minutes_ago=600, edited_minutes_ago=5, ident=1),
                ack("cowork-dod", minutes_ago=1, ident=2),
                review(0, minutes_ago=60, ident=3),
            )
        )
        assert prf.classify(snap, NOW).state == "success"

    def test_the_edited_comment_is_the_latest_verdict_even_when_created_first(self):
        old_but_edited = comment(
            REVIEW_MARK.format(n=4), minutes_ago=600, edited_minutes_ago=1, ident=1, author=REVIEWER
        )
        newer = review(0, minutes_ago=60, ident=2)
        found = prf.latest_verdict((old_but_edited, newer), prf.PRODUCERS[0])
        assert found[1] == 4

    def test_editing_an_old_reply_cannot_forward_date_it_over_a_finding(self):
        """The mirror rule, and the fail-closed half of the same fact.

        An acknowledgement is dated by ``created_at`` on purpose: if an edit moved
        it, fixing a typo in last week's reply would silently answer this
        morning's findings.
        """
        snap = snapshot(
            comments=(
                ack(minutes_ago=300, ident=1, edited_minutes_ago=1),
                review(2, minutes_ago=60, ident=2),
            )
        )
        assert prf.classify(snap, NOW).state == "failure"


class TestWhoMayAnswer:
    """This repo is public, so anybody at all can comment on a pull request."""

    @pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR", "owner"])
    def test_write_access_clears_a_finding(self, association):
        snap = snapshot(comments=(review(2, minutes_ago=60), ack(minutes_ago=30, association=association)))
        assert prf.classify(snap, NOW).state == "success"

    @pytest.mark.parametrize("association", ["NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "MANNEQUIN"])
    def test_a_drive_by_acknowledgement_does_not(self, association):
        """One stranger's comment must not be able to clear somebody else's gate."""
        snap = snapshot(comments=(review(2, minutes_ago=60), ack(minutes_ago=30, association=association)))
        assert prf.classify(snap, NOW).state == "failure"

    def test_an_acknowledgement_in_the_same_second_still_counts(self):
        """`latest_verdict` breaks second-granularity ties on id; so does this."""
        snap = snapshot(comments=(review(2, minutes_ago=30, ident=1), ack(minutes_ago=30, ident=2)))
        assert prf.classify(snap, NOW).state == "success"


class TestForgedVerdicts:
    """A verdict is stronger than an ack, and until this existed it had no checks.

    Blocking the `<!-- addressed: -->` marker closed the weaker hole. Anyone could
    still post `<!-- pr-feedback: claude-review open=0 -->`, which does not
    *answer* the findings — it replaces the count of them — and the gate went
    green. On a public repo that was any account at all; on a cowork PR it was the
    routine that wrote the code. Survivable while a human clicked merge, and the
    entire gate now that `sweep-procedure.md` arms `gh pr merge --auto`.
    """

    def test_a_stranger_cannot_post_a_verdict(self):
        snap = snapshot(comments=(review(3, minutes_ago=60), forged(0, minutes_ago=5)))
        assert prf.classify(snap, NOW).state == "failure"

    def test_write_access_alone_cannot_post_one_either(self):
        """This is the applicant's route: a cowork routine posts as the maintainer."""
        snap = snapshot(
            labels=("cowork",),
            author="cowork-bot",
            comments=(review(3, minutes_ago=60), forged(0, minutes_ago=5, author="cowork-bot", association="OWNER")),
        )
        assert prf.classify(snap, NOW).state == "failure"

    def test_the_real_reviewer_still_clears_it(self):
        """The honest path must survive: a re-review reports zero and the gate opens."""
        snap = snapshot(comments=(review(3, minutes_ago=60, ident=1), review(0, minutes_ago=5, ident=2)))
        assert prf.classify(snap, NOW).state == "success"

    def test_a_bot_association_is_not_required_to_be_trusted(self):
        """A bot commenting through GITHUB_TOKEN carries `author_association: NONE`.

        Requiring write access *as well* would reject every genuine Claude Review
        and wedge every PR on a review that could never qualify — the deadlock
        this module is built to avoid. Pinned so the rule is not "hardened" into
        one later.
        """
        assert prf.is_authentic_verdict(
            comment(REVIEW_MARK.format(n=0), author=REVIEWER, association="NONE"),
            prf.PRODUCERS[0],
        )

    def test_a_forged_verdict_cannot_win_the_newest_wins_race(self):
        """It is skipped outright, not merely outranked."""
        latest = prf.latest_verdict(
            (review(3, minutes_ago=60, ident=1), forged(0, minutes_ago=1, ident=2)),
            prf.PRODUCERS[0],
        )
        assert latest is not None and latest[1] == 3

    def test_a_rejected_verdict_reads_as_absent_not_as_zero(self):
        """The fail-closed direction: red saying the review never posted."""
        snap = snapshot(comments=(forged(0, minutes_ago=5),))
        verdict = prf.classify(snap, NOW)
        assert verdict.state == "failure"
        assert "never posted a verdict" in verdict.description

    def test_only_the_reviewer_login_may_post_a_review_verdict(self):
        """ "Any bot" is not specific enough where the applicant is also a bot.

        `claude.yml`, `codeql-triage.yml` and `ci-sentinel.yml` all open their PRs
        from an Actions job, so on those lanes the author and the reviewer are
        indistinguishable by bot-ness, and a job on the applicant side could post
        the reviewer's `open=0`. Only comment ordering stopped it — a coincidence,
        not a check.
        """
        producer = prf.PRODUCERS[0]
        assert prf.is_authentic_verdict(comment(REVIEW_MARK.format(n=0), author=REVIEWER), producer)
        for impostor in ("github-actions[bot]", "dependabot[bot]", "some-app[bot]"):
            assert not prf.is_authentic_verdict(comment(REVIEW_MARK.format(n=0), author=impostor), producer)

    def test_another_bot_cannot_clear_the_gate_on_a_machine_pr(self):
        snap = snapshot(
            author="github-actions[bot]",
            head_ref="feature/issue-9-thing",
            comments=(
                review(2, minutes_ago=60, ident=1),
                comment(REVIEW_MARK.format(n=0), minutes_ago=5, ident=2, author="github-actions[bot]"),
            ),
        )
        assert prf.classify(snap, NOW).state == "failure"

    def test_the_dod_audit_accepts_a_maintainer_because_a_routine_writes_it(self):
        """Advisory and never required — and a stranger still cannot write it."""
        producer = next(p for p in prf.PRODUCERS if p.key == "cowork-dod")
        maintainer = comment(DOD_MARK.format(n=1), author=AUTHOR, association="OWNER")
        stranger = comment(DOD_MARK.format(n=1), author="a-stranger", association="NONE")
        assert prf.is_authentic_verdict(maintainer, producer)
        assert not prf.is_authentic_verdict(stranger, producer)


class TestUnattendedPRs:
    """A machine PR may fix a finding. It may not declare one answered.

    Once security, bug and chore fixes merge without a human clicking anything,
    `TRUSTED_ASSOCIATIONS` stops being a gate: a cowork routine posts under an
    account with write access, so the thing that wrote the change could also
    write `<!-- addressed: claude-review -->` under the review of it and merge.
    The way out is a push — a re-review then reports `open=0` on its own — which
    is why these tests check that the honest path still clears.
    """

    @pytest.mark.parametrize(
        "branch",
        ["cowork/security-pin-shas", "feature/issue-231-fix", "security/codeql-triage-2026-08", "ci-sentinel/red-main"],
    )
    def test_every_machine_branch_counts_as_unattended(self, branch):
        assert prf.is_unattended(snapshot(head_ref=branch)) is True

    @pytest.mark.parametrize("branch", ["", "feature/nice-thing", "main", "coworker/typo"])
    def test_an_ordinary_branch_does_not(self, branch):
        assert prf.is_unattended(snapshot(head_ref=branch)) is False

    def test_the_label_alone_is_enough(self):
        assert prf.is_unattended(snapshot(labels=("cowork",))) is True

    def test_the_author_cannot_answer_its_own_review(self):
        snap = snapshot(
            labels=("cowork",),
            comments=(review(2, minutes_ago=60), ack(minutes_ago=30, association="OWNER")),
        )
        assert prf.classify(snap, NOW).state == "failure"

    def test_an_unlabelled_machine_branch_is_gated_the_same_way(self):
        """A run truncated between `git push` and `gh pr create --label` lands here."""
        snap = snapshot(
            head_ref="cowork/standup-confidence",
            comments=(review(2, minutes_ago=60), ack(minutes_ago=30, association="OWNER")),
        )
        assert prf.classify(snap, NOW).state == "failure"

    def test_somebody_else_with_write_access_still_can(self):
        snap = snapshot(
            labels=("cowork",),
            comments=(
                review(2, minutes_ago=60),
                ack(minutes_ago=30, association="OWNER", author="a-different-human"),
            ),
        )
        assert prf.classify(snap, NOW).state == "success"

    def test_a_fix_still_clears_it(self):
        """The honest path: push, get re-reviewed, and the reviewer reports zero."""
        snap = snapshot(
            labels=("cowork",),
            comments=(review(2, minutes_ago=60, ident=1), review(0, minutes_ago=10, ident=3)),
        )
        assert prf.classify(snap, NOW).state == "success"

    def test_a_bot_pr_on_a_machine_branch_is_not_waved_through(self):
        """Without this, the widest lane in the fleet has no gate at all."""
        snap = snapshot(
            author="github-actions[bot]",
            head_ref="cowork/platform-pin-actions",
            comments=(review(2),),
            ci=prf.CIState("success", NOW - timedelta(hours=3)),
        )
        assert prf.classify(snap, NOW).state == "failure"

    def test_a_human_answering_their_own_pr_is_untouched(self):
        """Nothing above applies to a person: one already read the review."""
        snap = snapshot(comments=(review(2, minutes_ago=60), ack(minutes_ago=30, association="OWNER")))
        assert prf.classify(snap, NOW).state == "success"


class TestReviewBodies:
    """A review's top-level body is not an issue comment and needs folding in."""

    def test_an_acknowledgement_typed_into_the_review_box_counts(self):
        snap = snapshot(comments=(review(2, minutes_ago=60), ack(minutes_ago=30, kind="review", ident=9)))
        assert prf.classify(snap, NOW).state == "success"

    def test_an_untrusted_review_body_still_does_not(self):
        snap = snapshot(
            comments=(review(2, minutes_ago=60), ack(minutes_ago=30, kind="review", ident=9, association="NONE"))
        )
        assert prf.classify(snap, NOW).state == "failure"


class TestTheOverrideIsNotAFreeLever:
    """`feedback-override` clears more than a marker ever could, so it needs the rule.

    It returns success before the producers, the threads and a
    CHANGES_REQUESTED review are even looked at. The sweeps hold bare `Bash` and
    already run `gh pr merge --auto` on their own PR, so `gh pr edit --add-label
    feedback-override` sits inside the same grant — meaning "a human's call" was a
    convention, and the lane that just lost the `<!-- addressed: -->` route had a
    bigger lever sitting beside it.
    """

    def _snap(self, **overrides):
        base = dict(labels=("cowork", prf.OVERRIDE_LABEL), comments=(review(3),))
        base.update(overrides)
        return snapshot(**base)

    def test_the_author_of_a_machine_pr_cannot_override_their_own(self):
        snap = self._snap(author="cowork-bot", override_actor="cowork-bot")
        verdict = prf.classify(snap, NOW)
        assert verdict.state == "failure"
        assert "own author" in verdict.description

    def test_another_human_still_can(self):
        snap = self._snap(author="cowork-bot", override_actor="a-different-human")
        verdict = prf.classify(snap, NOW)
        assert verdict.state == "success"
        assert "a-different-human" in verdict.description

    def test_an_unknown_actor_still_honours_it(self):
        """This label exists to unbrick a wedged gate.

        Refusing it on a timeline we could not read would turn one API failure
        into a PR nobody can merge — precisely what it is the escape hatch for.
        """
        snap = self._snap(author="cowork-bot", override_actor="")
        assert prf.classify(snap, NOW).state == "success"

    def test_an_ordinary_pr_is_untouched(self):
        """A person overriding their own PR is a person making a call."""
        snap = self._snap(labels=(prf.OVERRIDE_LABEL,), author=AUTHOR, override_actor=AUTHOR)
        assert prf.classify(snap, NOW).state == "success"

    def test_the_actor_is_read_from_the_timeline_newest_wins(self):
        events = [
            {"event": "labeled", "label": {"name": prf.OVERRIDE_LABEL}, "actor": {"login": "first"}},
            {"event": "labeled", "label": {"name": "cowork"}, "actor": {"login": "noise"}},
            {"event": "labeled", "label": {"name": prf.OVERRIDE_LABEL}, "actor": {"login": "second"}},
        ]
        with_json = lambda *a: events  # noqa: E731 - a one-line stub
        original = prf._gh_json
        try:
            prf._gh_json = with_json
            assert prf.fetch_override_actor("o/r", 1) == "second"
        finally:
            prf._gh_json = original

    def test_an_unreadable_timeline_reads_as_unknown_not_as_the_author(self):
        original = prf._gh_json
        try:
            prf._gh_json = lambda *a: None
            assert prf.fetch_override_actor("o/r", 1) == ""
        finally:
            prf._gh_json = original


class TestStickyAdviceMatchesThePR:
    """The check must not instruct a human to do the one thing that cannot work.

    On a cowork PR the author is the maintainer's own account, so a person who
    reads the red check, disagrees with a finding and replies exactly as told gets
    silence and the same red check re-rendered, with no diagnostic anywhere.
    """

    def _body(self, **overrides):
        snap = snapshot(comments=(review(2),), **overrides)
        return prf.sticky_body(snap, prf.classify(snap, NOW))

    def test_an_ordinary_pr_still_offers_the_reply_route(self):
        body = self._body()
        assert "<!-- addressed: <producer> -->" in body

    def test_a_machine_pr_says_the_reply_route_does_not_apply(self):
        body = self._body(labels=("cowork",))
        assert "cannot clear a finding here" in body
        assert "<!-- addressed: <producer> -->" not in body

    def test_a_machine_pr_still_names_a_way_out(self):
        """Never a dead end: fixing, or the override, and the override is a human's."""
        body = self._body(head_ref="cowork/platform-x")
        assert "fix and " in body
        assert prf.OVERRIDE_LABEL in body


class TestStickyIsInert:
    def test_a_pending_verdict_is_not_called_clear(self):
        """Pending holds the merge exactly as firmly as a failure does."""
        body = prf.sticky_body(snapshot(), prf.Verdict("pending", "waiting for Claude Review"))
        assert "waiting" in body.lower()
        assert "clear" not in body.lower()

    @pytest.mark.parametrize(
        "verdict",
        [
            prf.Verdict("success", "no open review feedback"),
            prf.Verdict("pending", "waiting for Claude Review"),
            prf.Verdict(
                "failure", "1 unanswered review item", (prf.OpenItem("producer", "claude-review", "2 findings"),)
            ),
        ],
    )
    def test_the_gate_cannot_clear_itself_through_its_own_comment(self, verdict):
        """The sticky is a comment on the PR, so the next run reads it back.

        Nothing in it may parse as a verdict or as an acknowledgement — a wording
        change that made it do so would make every red gate go green one run later.
        """
        body = prf.sticky_body(snapshot(), verdict)
        assert prf.acknowledged_producers(body) == set()
        for producer in prf.PRODUCERS:
            assert producer.pattern.search(body) is None


class FakeGh:
    """Record every ``gh`` invocation; reply from a per-test script.

    ``_gh`` is the single seam every GitHub call goes through, so the whole
    fetching and posting half is reachable with one monkeypatch and none of it
    touches the network. Replies are matched on a substring of the joined args,
    because these calls are URLs rather than the two-word subcommands
    ``test_cowork_setup.py`` keys on.
    """

    def __init__(self):
        self.calls: list[tuple[str, ...]] = []
        self.replies: list[tuple[str, int, str]] = []

    def reply(self, needle, payload, code=0):
        self.replies.append((needle, code, payload if isinstance(payload, str) else json.dumps(payload)))

    def sent(self, needle):
        return [args for args in self.calls if needle in " ".join(args)]

    def __call__(self, *args):
        self.calls.append(args)
        joined = " ".join(args)
        for needle, code, out in self.replies:
            if needle in joined:
                return subprocess.CompletedProcess(args, code, out, "boom" if code else "")
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGh()
    monkeypatch.setattr(prf, "_gh", fake)
    return fake


def _seed_pr(gh, **over):
    gh.reply(
        "pr view",
        {
            "number": 123,
            "headRefOid": HEAD,
            "isDraft": False,
            "author": {"login": AUTHOR},
            "labels": [{"name": "cowork"}],
            "reviewDecision": None,
            **over,
        },
    )
    gh.reply("issues/123/comments", [])
    gh.reply("pulls/123/reviews", [])
    gh.reply("graphql", {"data": {"repository": {"pullRequest": {"reviewThreads": {"pageInfo": {}, "nodes": []}}}}})
    gh.reply("actions/runs", {"workflow_runs": []})


class TestFetch:
    def test_a_pr_is_read_into_the_shape_classify_wants(self, gh):
        _seed_pr(gh, reviewDecision="CHANGES_REQUESTED")
        gh.replies.insert(
            0,
            (
                "issues/123/comments",
                0,
                json.dumps(
                    [
                        {
                            "id": 7,
                            "user": {"login": "claude[bot]"},
                            "body": REVIEW_MARK.format(n=1),
                            "created_at": "2026-08-06T10:00:00Z",
                            "updated_at": "2026-08-06T11:00:00Z",
                            "author_association": "NONE",
                        }
                    ]
                ),
            ),
        )
        snap = prf.fetch_snapshot(123, "o/r")
        assert snap.head_sha == HEAD
        assert snap.author == AUTHOR
        assert snap.labels == ("cowork",)
        assert snap.review_decision == "CHANGES_REQUESTED"
        assert snap.comments[0].written_at > snap.comments[0].created_at

    def test_a_failed_read_is_none_rather_than_a_half_snapshot(self, gh):
        gh.reply("pr view", "", code=1)
        assert prf.fetch_snapshot(123, "o/r") is None

    def test_threads_are_normalised_and_truncation_is_carried(self, gh):
        _seed_pr(gh)
        gh.replies.insert(
            0,
            (
                "graphql",
                0,
                json.dumps(
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "pageInfo": {"hasNextPage": True},
                                        "nodes": [
                                            {
                                                "id": "T1",
                                                "isResolved": False,
                                                "isOutdated": True,
                                                "path": "a.py",
                                                "line": 3,
                                                "comments": {
                                                    "nodes": [{"author": {"login": "r"}, "body": "  spaced\n out "}]
                                                },
                                            }
                                        ],
                                    }
                                }
                            }
                        }
                    }
                ),
            ),
        )
        snap = prf.fetch_snapshot(123, "o/r")
        assert snap.threads_truncated is True
        assert snap.threads[0].authors == ("r",)
        assert snap.threads[0].excerpt == "spaced out"

    def test_review_bodies_are_folded_in_and_empty_ones_dropped(self, gh):
        _seed_pr(gh)
        gh.replies.insert(
            0,
            (
                "pulls/123/reviews",
                0,
                json.dumps(
                    [
                        {"id": 1, "user": {"login": "r"}, "body": "", "submitted_at": "2026-08-06T10:00:00Z"},
                        {
                            "id": 2,
                            "user": {"login": AUTHOR},
                            "body": "<!-- addressed: claude-review -->",
                            "submitted_at": "2026-08-06T11:00:00Z",
                            "author_association": "OWNER",
                        },
                    ]
                ),
            ),
        )
        bodies = [c for c in prf.fetch_snapshot(123, "o/r").comments if c.kind == "review"]
        assert len(bodies) == 1
        assert bodies[0].association == "OWNER"

    def test_ci_takes_the_latest_run_for_this_sha_and_ignores_other_workflows(self, gh):
        gh.reply(
            "actions/runs",
            {
                "workflow_runs": [
                    {
                        "name": "CI",
                        "conclusion": "failure",
                        "created_at": "2026-08-06T09:00:00Z",
                        "updated_at": "2026-08-06T09:10:00Z",
                    },
                    {
                        "name": "CI",
                        "conclusion": "success",
                        "created_at": "2026-08-06T10:00:00Z",
                        "updated_at": "2026-08-06T10:10:00Z",
                    },
                    {
                        "name": "CodeQL",
                        "conclusion": "failure",
                        "created_at": "2026-08-06T11:00:00Z",
                        "updated_at": "2026-08-06T11:10:00Z",
                    },
                ]
            },
        )
        state = prf.fetch_ci("o/r", HEAD)
        assert state.conclusion == "success"
        assert state.completed_at == datetime(2026, 8, 6, 10, 10, tzinfo=UTC)

    def test_no_run_for_this_sha_is_not_a_green_one(self, gh):
        gh.reply("actions/runs", {"workflow_runs": []})
        assert prf.fetch_ci("o/r", HEAD) == prf.CIState(None, None)
        assert prf.fetch_ci("o/r", "") == prf.CIState(None, None)

    def test_the_repo_and_pr_lookups_survive_a_gh_failure(self, gh):
        gh.reply("repo view", "", code=1)
        gh.reply("pr view", "", code=1)
        assert prf.repo_slug() is None
        assert prf.current_pr() is None


class TestPosting:
    def test_the_status_carries_the_context_the_ruleset_waits_on(self, gh):
        prf.post_status("o/r", HEAD, prf.Verdict("failure", "x" * 300), "https://run")
        sent = " ".join(gh.sent(f"statuses/{HEAD}")[0])
        assert f"context={prf.STATUS_CONTEXT}" in sent
        assert "state=failure" in sent
        assert "target_url=https://run" in sent
        assert "x" * (prf.DESCRIPTION_LIMIT + 1) not in sent

    def test_a_failed_post_is_reported_rather_than_swallowed(self, gh, capsys):
        gh.reply("statuses", "", code=1)
        assert prf.post_status("o/r", HEAD, prf.Verdict("success", "fine")) is False
        assert "could not post the status" in capsys.readouterr().err

    def test_a_clear_verdict_never_creates_a_comment(self, gh):
        """Only ever edited into an existing sticky. A PR with nothing to say
        about its review feedback should not acquire a comment saying so."""
        prf.upsert_sticky("o/r", 123, snapshot(), prf.Verdict("success", "no open review feedback"))
        assert gh.sent("issues/123/comments") == []

    def test_a_failing_verdict_creates_one(self, gh):
        prf.upsert_sticky("o/r", 123, snapshot(), prf.Verdict("failure", "1 unanswered review item"))
        assert len(gh.sent("issues/123/comments")) == 1

    def test_an_existing_sticky_is_edited_in_place(self, gh):
        snap = snapshot(comments=(comment(f"{prf.STICKY_MARKER}\nold", ident=55),))
        prf.upsert_sticky("o/r", 123, snap, prf.Verdict("success", "no open review feedback"))
        assert gh.sent("issues/comments/55")
        assert gh.sent("issues/123/comments") == []

    def test_a_review_body_is_never_mistaken_for_the_sticky_comment(self, gh):
        """Review ids and issue-comment ids come from different spaces, so
        PATCHing the issue-comments endpoint with a review id edits something
        unrelated."""
        snap = snapshot(comments=(comment(f"{prf.STICKY_MARKER}\nold", ident=55, kind="review"),))
        prf.upsert_sticky("o/r", 123, snap, prf.Verdict("failure", "1 unanswered review item"))
        assert gh.sent("issues/comments/55") == []
        assert len(gh.sent("issues/123/comments")) == 1

    def test_the_report_names_every_open_item(self):
        items = (prf.OpenItem("thread", "T1", "unresolved thread on a.py:3"),)
        out = prf.render_report(snapshot(), prf.Verdict("failure", "1 unanswered review item", items))
        assert "PR #123" in out and HEAD[:7] in out and "unresolved thread on a.py:3" in out


class TestMain:
    @pytest.fixture(autouse=True)
    def _gh_on_path(self, monkeypatch):
        monkeypatch.setattr(prf.shutil, "which", lambda _: "/usr/bin/gh")

    def _repo(self, gh):
        gh.reply("repo view", {"nameWithOwner": "o/r"})

    def test_open_feedback_exits_nonzero(self, gh, capsys):
        self._repo(gh)
        _seed_pr(gh)
        gh.replies.insert(0, ("actions/runs", 0, json.dumps({"workflow_runs": []})))
        assert prf.main(["--pr", "123"]) == 0  # pending, not a failure
        gh.replies.insert(
            0,
            (
                "issues/123/comments",
                0,
                json.dumps(
                    [{"id": 1, "user": {"login": REVIEWER}, "body": REVIEW_MARK.format(n=2), "created_at": None}]
                ),
            ),
        )
        assert prf.main(["--pr", "123"]) == 1
        assert "unanswered" in capsys.readouterr().out

    def test_json_is_machine_readable(self, gh, capsys):
        self._repo(gh)
        _seed_pr(gh)
        assert prf.main(["--pr", "123", "--json"]) in (0, 1)
        payload = json.loads(capsys.readouterr().out)
        assert payload["number"] == 123 and payload["head_sha"] == HEAD and "state" in payload

    def test_status_mode_succeeds_whatever_the_verdict(self, gh):
        """A red *step* would read as a broken workflow rather than as unanswered
        review, and would be the first thing anyone disabled. The status blocks."""
        self._repo(gh)
        _seed_pr(gh)
        assert prf.main(["--pr", "123", "--status"]) == 0
        assert gh.sent(f"statuses/{HEAD}")

    def test_a_required_check_never_stays_silent(self, gh):
        """A required status that was never posted is indistinguishable in the UI
        from this workflow not existing, and blocks the merge with nothing to do."""
        self._repo(gh)
        gh.reply("issues/123/comments", "", code=1)
        gh.reply("pr view", {"headRefOid": HEAD})
        assert prf.main(["--pr", "123", "--status"]) == 2
        posted = " ".join(gh.sent(f"statuses/{HEAD}")[0])
        assert "state=pending" in posted

    def test_a_missing_gh_says_how_to_get_one(self, monkeypatch, capsys):
        monkeypatch.setattr(prf.shutil, "which", lambda _: None)
        assert prf.main(["--pr", "1"]) == 2
        assert "brew install gh" in capsys.readouterr().err

    def test_no_pr_and_none_on_this_branch_is_an_error_not_a_pass(self, gh, capsys):
        self._repo(gh)
        gh.reply("pr view", "", code=1)
        assert prf.main([]) == 2
        assert "no PR given" in capsys.readouterr().err
