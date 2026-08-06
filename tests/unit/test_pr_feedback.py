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


def review(n: int, *, minutes_ago: int = 60, ident: int = 1, edited_minutes_ago: int | None = None):
    return comment(
        f"Findings...\n\n{REVIEW_MARK.format(n=n)}",
        minutes_ago=minutes_ago,
        ident=ident,
        edited_minutes_ago=edited_minutes_ago,
    )


def ack(producer: str = "claude-review", *, minutes_ago: int = 30, ident: int = 2, association: str = "OWNER", **kw):
    """A won't-fix reply. Trusted by default — the untrusted case is its own test."""
    return comment(
        f"Won't fix, because the caller already guards it.\n\n<!-- addressed: {producer} -->",
        minutes_ago=minutes_ago,
        author=AUTHOR,
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
        old_but_edited = comment(REVIEW_MARK.format(n=4), minutes_ago=600, edited_minutes_ago=1, ident=1)
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
                            "user": {"login": "github-actions[bot]"},
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
                json.dumps([{"id": 1, "user": {"login": "b"}, "body": REVIEW_MARK.format(n=2), "created_at": None}]),
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
