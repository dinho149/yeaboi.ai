"""Tests for scripts/cowork_metrics.py — the fleet's report on itself.

The arithmetic is the part that can be wrong without anyone noticing. A count
that double-reports one work item, or a rejection filed under the wrong cause,
produces a plausible number that a human then acts on — and `cron/digest.md`
step 6 says explicitly what a reader is meant to do about a low approval rate:
*"its charter is pointed at the wrong thing, and the fix is to edit
``workstreams/<name>.md``"*. A wrong number here re-aims a workstream.

So ``build_report`` takes data and makes no requests, and everything below drives
it directly.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]

_MODULE_PATH = ROOT / "scripts" / "cowork_metrics.py"
_spec = importlib.util.spec_from_file_location("cowork_metrics", _MODULE_PATH)
metrics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(metrics)


def lbl(*names: str) -> list[dict]:
    return [{"name": name} for name in names]


def pr(number: int, workstream: str, *, ref: str = "cowork/x", body: str = "", extra: tuple[str, ...] = ()) -> dict:
    return {
        "number": number,
        "title": f"pr {number}",
        "labels": lbl("cowork", f"workstream:{workstream}", "type:chore", *extra),
        "head_ref": ref,
        "body": body,
        "created_at": "2026-08-01T10:00:00Z",
        "merged_at": "2026-08-01T14:00:00Z",
    }


def issue(number: int, workstream: str, *, state: str = "open", labels: tuple[str, ...] = ("cowork:proposal",)) -> dict:
    return {"number": number, "state": state, "labels": lbl(*labels, f"workstream:{workstream}", "type:chore")}


def report(**kwargs) -> dict:
    base = {"prs": [], "issues": [], "reasons": {}, "runs": [], "window": 30}
    return metrics.build_report(**{**base, **kwargs})


class TestTheFunnelReconciles:
    """Every find is in exactly one terminal state.

    ``identified == merged + rejected + still_open`` is the property the whole
    report rests on. It broke the first time it was written: an approved-lane PR
    has both an issue and a PR, so counting issues plus PRs reported one work item
    twice — and flattered precisely the lane a human had already approved.
    """

    def test_an_approved_item_is_one_find_not_two(self) -> None:
        out = report(
            prs=[pr(2, "security", ref="feature/issue-12-x", body="Closes #12")],
            issues=[issue(12, "security", state="closed", labels=("claude-implement",))],
        )
        assert out["identified"] == 1
        assert out["merged"] == 1
        assert out["rejected"] == 0

    def test_an_auto_lane_find_that_filed_no_issue_still_counts_as_found(self) -> None:
        # A sweep builds what it just scouted, so counting issues alone would
        # report a fleet fixing more than it finds.
        out = report(prs=[pr(1, "tui-ux", body="found this run")])
        assert (out["identified"], out["merged"]) == (1, 1)

    def test_the_totals_add_up_across_every_lane_at_once(self) -> None:
        out = report(
            prs=[
                pr(1, "tui-ux", body="no issue"),
                pr(2, "security", ref="feature/issue-12-x", body="Closes #12"),
                pr(3, "integrations", body="campaign angle"),
            ],
            issues=[
                issue(10, "tui-ux", state="closed"),
                issue(11, "poker", state="closed"),
                issue(12, "security", state="closed", labels=("claude-implement",)),
                issue(13, "web-ux"),
            ],
            reasons={10: "slack-veto", 11: "aged-out"},
        )
        assert out["identified"] == out["merged"] + out["rejected"] + out["still_open"]

    def test_a_closed_issue_a_pr_built_is_never_also_a_rejection(self) -> None:
        # A sweep building straight off the queue closes the issue via `Closes #n`
        # and never applies `claude-implement` — so "closed without approval" is
        # true of it and means the opposite of rejected.
        out = report(prs=[pr(5, "retro", body="Closes #20")], issues=[issue(20, "retro", state="closed")])
        assert out["rejected"] == 0
        assert out["merged"] == 1

    def test_approval_is_a_stage_and_not_a_terminal_state(self) -> None:
        out = report(
            prs=[pr(2, "security", ref="feature/issue-12-x", body="Closes #12")],
            issues=[
                issue(12, "security", state="closed", labels=("claude-implement",)),
                issue(14, "retro", labels=("claude-implement",)),
            ],
        )
        # Both were approved; one shipped and one is still open.
        assert out["approved"] == 2
        assert (out["merged"], out["still_open"]) == (1, 1)


class TestRejectionsAreSplitByCause:
    """A human's no and a clock running out are different facts.

    `cron/digest.md` closes an unanswered proposal after fourteen days. Counting
    that beside a human's ❌ reports a workstream nobody had time to read as one
    pointed at the wrong thing — and the reader's prescribed response to the
    second is to re-aim its charter.
    """

    def test_each_reason_is_reported_separately(self) -> None:
        out = report(
            issues=[issue(1, "poker", state="closed"), issue(2, "poker", state="closed")],
            reasons={1: "slack-veto", 2: "aged-out"},
        )
        assert out["rejected_by_reason"] == {"aged-out": 1, "slack-veto": 1}

    def test_a_close_with_no_marker_is_unrecorded_and_says_so(self) -> None:
        # Every close before this shipped looks like this. Reported as its own
        # bucket rather than folded into a real reason, because a guess here is
        # indistinguishable from a measurement.
        out = report(issues=[issue(1, "poker", state="closed")], reasons={})
        assert out["rejected_by_reason"] == {"unrecorded": 1}


class TestLanes:
    def test_the_lane_comes_off_the_branch_prefix(self) -> None:
        assert metrics.lane_of(pr(1, "tui-ux", ref="cowork/tui-ux-dead")) == "auto"
        assert metrics.lane_of(pr(2, "security", ref="feature/issue-9-x")) == "approved"
        assert metrics.lane_of(pr(3, "security", ref="security/codeql-triage-2026")) == "codeql"

    def test_a_campaign_is_read_off_the_label_not_the_branch(self) -> None:
        # The campaign builds on a `cowork/` branch exactly like a sweep does, so
        # the prefix alone files a week of provider work as one auto find.
        assert metrics.lane_of(pr(4, "integrations", ref="cowork/int-notion")) == "campaign"

    def test_a_ref_nobody_read_is_not_filed_as_a_persons(self) -> None:
        """`--no-branches` (which the digest passes) leaves every head_ref empty.

        Calling that "human" printed `by the fleet 0 — the rest is people building
        cowork` as a fact about a lane nothing had looked up, and sent
        `cost_per_merged_pr` to None. A ref that matched no prefix is a person's; a
        ref nobody read is not the same claim.
        """
        assert metrics.lane_of(pr(5, "platform", ref="")) == "other"
        assert metrics.lane_of(pr(6, "platform", ref="some-persons-branch")) == "human"

    def test_every_unattended_prefix_pr_feedback_knows_has_a_lane_here(self) -> None:
        """The two lists must not drift.

        `scripts/pr_feedback.py` decides what counts as an unattended PR from the
        same prefixes. One added there and not here lands silently in `other`,
        which reads as a human's PR — the fleet's own work disappearing from the
        fleet's own report.
        """
        assert set(metrics.prf.UNATTENDED_BRANCH_PREFIXES) == set(metrics.LANE_BY_PREFIX)


class TestQualityCountersAreInTheSet:
    """Numbers that go up when the fleet gets worse.

    Counting merges alone rewards volume, and volume is the one thing an
    unattended fleet can always produce more of.
    """

    def test_a_capped_merge_is_counted(self) -> None:
        out = report(prs=[pr(1, "tui-ux", extra=("review-capped",)), pr(2, "tui-ux")])
        assert out["review_capped"] == 1

    def test_cost_per_merged_pr_is_none_rather_than_zero_when_nothing_merged(self) -> None:
        # Dividing by no merges is not a cost of zero, and a zero here reads as
        # the best possible result.
        out = report(runs=[{"name": "x", "status": "ok", "cost_usd": 4.0}])
        assert out["cost_per_merged_pr"] is None

    def test_cost_per_merged_pr_divides_the_ledger_by_the_merges(self) -> None:
        out = report(prs=[pr(1, "tui-ux"), pr(2, "tui-ux")], runs=[{"name": "x", "status": "ok", "cost_usd": 5.0}])
        assert out["cost_per_merged_pr"] == 2.5


class TestTheLedgerHalf:
    def test_runs_are_grouped_by_routine_with_their_failures(self) -> None:
        out = report(
            runs=[
                {"name": "tui-ux-sweep", "status": "ok", "cost_usd": 1.0},
                {"name": "tui-ux-sweep", "status": "degraded", "cost_usd": 0.5},
                {"name": "digest", "status": "ok", "cost_usd": 0.1},
            ]
        )
        assert out["by_routine"]["tui-ux-sweep"] == {"runs": 2, "cost_usd": 1.5, "failed": 1}
        assert out["run_status"] == {"degraded": 1, "ok": 1 + 1}

    def test_an_empty_ledger_reads_as_no_entries_and_not_as_no_runs(self) -> None:
        # Until every routine has recorded once, an empty ledger is the normal
        # state. Printing "0 runs" would be a lie with a plausible shape.
        text = metrics.render_text(report())
        assert "no ledger entries" in text
        assert "0 runs" not in text


class TestTheOutputIsHonestAboutItself:
    def test_identified_is_marked_as_a_floor(self) -> None:
        # Finds a sweep passed over survive only as a count in a PR body, so the
        # number can never be complete and must not be printed as if it were.
        assert "~" in metrics.render_text(report(prs=[pr(1, "tui-ux")]))

    def test_a_workstream_label_with_no_charter_is_reported_not_dropped(self) -> None:
        # Dropping it would make the totals disagree with the rows beneath them.
        out = report(prs=[pr(1, "ghost-workstream")])
        assert out["unclaimed_workstreams"]["ghost-workstream"]["merged"] == 1
        assert "ghost-workstream" in metrics.render_text(out)

    def test_a_failed_read_is_a_warning_and_never_a_zero(self, monkeypatch) -> None:
        """`slots: null` is the house precedent: a number nobody could read,
        printed as a number, is the one failure a report cannot recover from."""
        monkeypatch.setattr(metrics, "fetch_merged_prs", lambda slug, since: ([], "egress refused"))
        monkeypatch.setattr(metrics, "fetch_proposals", lambda slug, since: ([], ""))
        monkeypatch.setattr(metrics, "fetch_markers", lambda slug, numbers: ({}, {}, ""))
        monkeypatch.setattr(metrics, "fetch_runs", lambda slug, since: ([], ""))
        _, warnings = metrics.collect("o/r", window=30)
        assert any("egress refused" in w for w in warnings)


class TestWindow:
    def test_the_window_is_days_back_from_now_in_utc(self) -> None:
        assert metrics.since_iso(30, datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)) == "2026-08-01T12:00:00Z"


class TestTheReportSerialises:
    def test_the_whole_report_is_json(self) -> None:
        # `--json` is the form a future digest section reads; a value that will
        # not serialise fails here rather than in a Monday post.
        out = report(prs=[pr(1, "tui-ux")], issues=[issue(2, "poker", state="closed")], runs=[{"name": "d"}])
        assert json.loads(json.dumps(out))["merged"] == 1


class TestTheFleetsOwnWorkIsSeparable:
    """A `cowork`-labelled PR is not necessarily the fleet's.

    Ten of the fourteen `cowork` PRs in the first real run were branches like
    `claude-cowork`, `security-fixes` and `feature/cowork-queue-drain` — people
    *building* cowork, carrying the label because they touch it. "The fleet
    merged fourteen PRs" was wrong by a factor of three and a half.
    """

    def test_a_branch_no_unattended_prefix_matches_is_a_persons(self) -> None:
        assert metrics.lane_of(pr(1, "platform", ref="feature/cowork-queue-drain")) == "human"
        assert metrics.lane_of(pr(2, "platform", ref="claude-cowork")) == "human"

    def test_the_fleet_subtotal_excludes_them(self) -> None:
        out = report(
            prs=[
                pr(1, "tui-ux", ref="cowork/tui-ux-dead"),
                pr(2, "platform", ref="feature/cowork-queue-drain"),
                pr(3, "platform", ref="security-fixes"),
            ]
        )
        assert out["merged"] == 3
        assert out["fleet_merged"] == 1
        assert "the rest is people building cowork" in metrics.render_text(out)

    def test_cost_per_pr_divides_by_the_fleets_merges_only(self) -> None:
        # The ledger records routine runs. Charging their cost against a human's
        # PRs too would report the fleet as cheaper the busier its humans were.
        out = report(
            prs=[pr(1, "tui-ux", ref="cowork/x"), pr(2, "platform", ref="feature/hand-written")],
            runs=[{"name": "s", "status": "ok", "cost_usd": 4.0}],
        )
        assert out["cost_per_merged_pr"] == 4.0


class TestWhyWorkDidNotShip:
    """The half `cron/retune.md` reads: which charter fails, and how.

    A fleet-wide rejection count says the fleet is noisy. Only the per-workstream
    split says *which* charter is pointed at the wrong thing, which is the fix
    `cron/digest.md`'s calibration line has been describing since it was written
    and nothing has ever acted on.
    """

    def _issue(self, number: int, workstream: str, *, state: str = "open", labels: tuple[str, ...] = ()) -> dict:
        return {
            "number": number,
            "state": state,
            "created_at": "2026-08-01T00:00:00Z",
            "labels": [{"name": f"workstream:{workstream}"}] + [{"name": name} for name in labels],
        }

    def test_a_bounce_and_a_rejection_are_different_faults(self):
        """One means the find was real and unwanted; the other means it was misclassified."""
        report = metrics.build_report(
            prs=[],
            issues=[self._issue(1, "tui-ux"), self._issue(2, "tui-ux", state="closed")],
            reasons={2: "slack-veto"},
            bounces={1: ["no-repro"]},
            runs=[],
            window=30,
        )
        assert report["bounced"] == 1
        assert report["rejected"] == 1
        assert report["bounced_by_reason"] == {"no-repro": 1}
        assert report["rejected_by_reason"] == {"slack-veto": 1}

    def test_a_bounce_does_not_count_as_a_rejection(self):
        """A bounced item is still open and still the fleet's to build."""
        report = metrics.build_report(
            prs=[], issues=[self._issue(1, "poker")], reasons={}, bounces={1: ["no-repro"]}, runs=[], window=30
        )
        assert report["rejected"] == 0
        assert report["still_open"] == 1

    def test_both_reasons_land_under_the_workstream_that_produced_them(self):
        report = metrics.build_report(
            prs=[],
            issues=[
                self._issue(1, "tui-ux"),
                self._issue(2, "platform"),
                self._issue(3, "platform", state="closed"),
            ],
            reasons={3: "aged-out"},
            bounces={1: ["no-repro"], 2: ["outside-owns"]},
            runs=[],
            window=30,
        )
        assert report["reasons_by_workstream"]["tui-ux"] == {"rejected": {}, "bounced": {"no-repro": 1}}
        assert report["reasons_by_workstream"]["platform"] == {
            "rejected": {"aged-out": 1},
            "bounced": {"outside-owns": 1},
        }

    def test_bouncing_twice_for_two_conditions_records_both(self):
        """Keeping only the last loses the more common of the two."""
        report = metrics.build_report(
            prs=[],
            issues=[self._issue(1, "standup")],
            reasons={},
            bounces={1: ["no-repro", "needs-judgement", "no-repro"]},
            runs=[],
            window=30,
        )
        assert report["reasons_by_workstream"]["standup"]["bounced"] == {"no-repro": 2, "needs-judgement": 1}

    def test_a_workstream_with_nothing_to_answer_for_is_absent(self):
        """Sixteen rows of zeroes is a table nobody reads to the bottom of."""
        report = metrics.build_report(prs=[], issues=[], reasons={}, bounces={}, runs=[], window=30)
        assert report["reasons_by_workstream"] == {}

    def test_the_marker_read_covers_open_issues_too(self, monkeypatch):
        """A `bounced:` marker sits on an issue that is still open, by definition.

        Reading only the closed set — what the reason count did when it shipped —
        sees a rejection rate and no misclassification rate at all.
        """
        seen: list[int] = []

        def _comments(path: str):
            number = int(path.split("/issues/")[1].split("/")[0])
            seen.append(number)
            body = "<!-- bounced: reason=no-repro -->" if number == 7 else ""
            return SimpleNamespace(ok=True, data=[{"body": body}], error="")

        monkeypatch.setattr(metrics.transport, "api_paged", lambda path: _comments(path))
        rejected, bounced, error = metrics.fetch_markers("o/r", [7, 8])
        assert error == ""
        assert seen == [7, 8]
        assert bounced == {7: ["no-repro"]}
        assert rejected == {7: "unrecorded", 8: "unrecorded"}
