#!/usr/bin/env python3
"""What the fleet did, and what it cost.

The fleet ran fifteen workstreams and could not answer three questions about
itself: how much it merged, how much of what it *found* it actually fixed, and
what any of that cost. Every number was either computed once and posted to Slack
(`cron/shipped-standup.md` reports a day, `cron/digest.md` reports a Monday) or
never computed at all. Nothing looked at a month.

The split this file is built on is not a design choice, it is the shape of the
data:

- **Outcomes are already durable, and only need counting.** The fleet is
  stateless on purpose — `cowork/README.md`: "GitHub issues **are** the queue —
  there is no other shared state between routine runs" — and that turns out to be
  a complete audit log. Every proposal, approval, PR and merge is in GitHub with
  a timestamp on it. This half needs no recording, and this file is the first
  thing to read it.
- **Runs are not durable at all, and had to be pushed.** A run's cost, duration
  and outcome exist only inside the container that produced them, and
  `agentwatch.collector` reads a filesystem, so nothing can go and fetch them
  afterwards. `scripts/cowork_checkin.py --record` appends them to a monthly
  ledger issue as they happen; this file reads it back.

**Nothing here is part of a run.** This is a human's command — `make
cowork-metrics` — and no routine invokes it. That is what lets it read the ledger
without giving the fleet a memory: statelessness means no run's behaviour depends
on another run's state, and a report nobody's run consults does not touch that.

Two honest limits, printed in the output rather than buried here:

- **"Identified" is a floor.** An auto-lane find that goes straight to a PR never
  becomes an issue, so it is counted from the PR. Finds a sweep *passed over* are
  recorded only as a count in a PR body (`sweep-procedure.md` step 5), and are not
  counted at all. The number is marked `~` wherever it appears.
- **Rejections are split by cause, and the split is the point.** A human closing a
  proposal is a decision; `digest.md`'s fourteen-day clock running out is the
  absence of one. Counting them together reports a workstream nobody had time to
  read as a workstream pointed at the wrong thing — which is precisely the
  conclusion `digest.md` step 6 tells a reader to act on.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _gh_transport as transport  # noqa: E402 - after the sys.path line that makes it importable
import pr_feedback as prf  # noqa: E402 - same
from cowork_setup import (  # noqa: E402 - same
    LEDGER_LABEL,
    PROPOSAL_LABEL,
    QUEUE_LABEL,
    parse_workstreams,
)

ROOT = Path(__file__).resolve().parent.parent

APPROVAL_LABEL = "claude-implement"
CAPPED_LABEL = prf.CAPPED_LABEL

# The markers the fleet now writes when something it found does not ship. Read
# here; written by `cowork_relay.py` (a human's ❌), `cowork/sweep-procedure.md`
# (a bounce, a stale close) and `cron/digest.md` (the fourteen-day clock).
REJECTED_RE = re.compile(r"<!--\s*rejected:\s*reason=([a-z-]+)")
BOUNCED_RE = re.compile(r"<!--\s*bounced:\s*reason=([a-z-]+)")

# One ledger comment. `cowork_checkin.py` writes a human-readable line, then this.
# `Closes #<n>` in a PR body — the DoD's own convention, and the only thing that
# says a PR and an issue are one work item rather than two. Without it the
# approved lane counts twice: `claude.yml` builds from an issue, so the issue and
# the PR are the same find, while an auto-lane sweep that filed nothing has only
# a PR. Counting "issues plus PRs" reports the first as two finds and the second
# as one, which flatters exactly the lane a human already approved.
CLOSES_RE = re.compile(r"\b(?:closes|fixes|resolves)\s+#(\d+)", re.I)

LEDGER_MARKER = "<!-- fleet-run -->"
LEDGER_JSON = re.compile(r"```json\n(.*?)\n```", re.S)

# Which lane a merged PR came down. Read off the branch name, because that is the
# one field every producer sets and none of them can forget: `pr_feedback.py`
# already keys its unattended check on the same prefixes, and importing them
# rather than re-spelling them is what stops a new lane being invisible here.
# A prefix added there and not here would silently land in `other`.
LANE_BY_PREFIX = {
    "cowork/": "auto",  # a sweep's own build
    "feature/issue-": "approved",  # claude.yml, after a human's claude-implement
    "security/codeql-triage": "codeql",
    "ci-sentinel/": "sentinel",
}
CAMPAIGN_WORKSTREAM = "integrations"

# Lanes the fleet drove itself. The rest of a `cowork`-labelled PR set is people
# *building* cowork — `claude-cowork`, `security-fixes`, `feature/cowork-queue-drain`
# — which carries the label because it touches the system and is not output of it.
# Reporting the two together answers "what did the fleet merge" with a number
# mostly made of human work, which is the one number everybody quotes.
FLEET_LANES = frozenset({"auto", "approved", "campaign", "codeql", "sentinel"})


def since_iso(days: int, now: datetime | None = None) -> str:
    return ((now or datetime.now(UTC)) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _labels(item: dict) -> set[str]:
    return {label["name"] for label in item.get("labels", []) if isinstance(label, dict) and "name" in label}


def workstream_of(item: dict) -> str:
    for name in _labels(item):
        if name.startswith("workstream:"):
            return name.split(":", 1)[1]
    return ""


def type_of(item: dict) -> str:
    for name in _labels(item):
        if name.startswith("type:"):
            return name.split(":", 1)[1]
    return ""


def lane_of(pr: dict) -> str:
    """Which lane merged this. Campaign wins on the label, everything else on the
    branch — the campaign builds on a `cowork/` branch like a sweep does, so the
    prefix alone would file a week of provider work as an ordinary auto find."""
    if workstream_of(pr) == CAMPAIGN_WORKSTREAM:
        return "campaign"
    ref = str(pr.get("head_ref") or "")
    if not ref:
        # No ref read at all — under `--no-branches`, or a fetch that failed. That
        # is not the same as a ref that matched no prefix, and calling it "human"
        # printed `by the fleet 0 — the rest is people building cowork` as a fact
        # about a lane nobody looked up. `fetch_pr_branches`, `collect`'s warning
        # and `--no-branches`' own help all already promised "other".
        return "other"
    for prefix, lane in LANE_BY_PREFIX.items():
        if ref.startswith(prefix):
            return lane
    # Not "unknown": `pr_feedback.py` draws this exact line to decide whether a PR
    # is unattended, and a branch matching none of its prefixes is a person's.
    return "human"


# --- fetching ----------------------------------------------------------------
#
# Every read is REST, never GraphQL: `cowork/sweep-procedure.md` records that a
# routine session's egress proxy refuses the GraphQL half, and while this command
# is a human's, sharing the transport means the same code answers in both places.


def fetch_merged_prs(slug: str, since: str) -> tuple[list[dict], str]:
    """Fleet PRs merged inside the window, slimmed to what the report reads.

    The search endpoint rather than `/pulls`, because merge time is what the
    window is drawn on and `/pulls` cannot filter by it — listing every PR and
    discarding the old ones works until the repo has more than `MAX_PAGES` of
    them, and then it silently reports a short month.
    """
    query = f"repo:{slug}+is:pr+is:merged+label:cowork+merged:>={since}"
    found = transport.api_paged(f"/search/issues?q={query}", key="items")
    if not found.ok:
        return [], found.error
    prs = []
    for item in found.data if isinstance(found.data, list) else []:
        prs.append(
            {
                "number": item.get("number"),
                "title": item.get("title", ""),
                "labels": item.get("labels", []),
                "created_at": item.get("created_at", ""),
                "merged_at": (item.get("pull_request") or {}).get("merged_at", ""),
                # Carried for its `Closes #<n>` lines, which are what say whether
                # this PR and some issue are the same work item counted twice.
                "body": item.get("body") or "",
                # Search does not return the head ref, so the lane comes from the
                # title prefix when it is there and from a follow-up read when it
                # is not. Recorded as empty rather than guessed.
                "head_ref": "",
            }
        )
    return prs, ""


def fetch_pr_branches(slug: str, prs: list[dict]) -> str:
    """Fill in each PR's branch, one call each, because the lane is read off it.

    Deliberately a separate pass rather than folded into the search: it is the
    expensive half (one request per PR), and a caller that only wants counts can
    skip it and get `lane: other` throughout rather than a partial month.
    """
    for pr in prs:
        result = transport.api("GET", f"/repos/{slug}/pulls/{pr['number']}")
        if not result.ok:
            return result.error
        data = result.data if isinstance(result.data, dict) else {}
        pr["head_ref"] = str((data.get("head") or {}).get("ref") or "")
        pr["merged_at"] = str(data.get("merged_at") or pr["merged_at"] or "")
    return ""


def fetch_proposals(slug: str, since: str) -> tuple[list[dict], str]:
    """Every cowork issue touched in the window, open and closed.

    `since` filters on *update* time, not creation, which is what the funnel
    wants: a proposal filed five weeks ago and approved yesterday is this month's
    approval. It is also why the counts below are keyed off what happened rather
    than off `created_at`.
    """
    issues: list[dict] = []
    for label in (PROPOSAL_LABEL, QUEUE_LABEL, APPROVAL_LABEL):
        found = transport.api_paged(f"/repos/{slug}/issues?labels={transport.segment(label)}&state=all&since={since}")
        if not found.ok:
            return [], found.error
        for item in found.data if isinstance(found.data, list) else []:
            # `/issues` answers with pull requests too, and a PR is not a find.
            if isinstance(item, dict) and "pull_request" not in item:
                issues.append(item)
    # One issue can carry two of those labels across its life, and the three
    # queries overlap by design. Deduped on number, keeping the first read.
    seen: dict[int, dict] = {}
    for issue in issues:
        seen.setdefault(int(issue["number"]), issue)
    return list(seen.values()), ""


def fetch_markers(slug: str, numbers: list[int]) -> tuple[dict[int, str], dict[int, list[str]], str]:
    """Both failure markers on each issue: why it closed, and every time it bounced.

    Read over *every* issue in the window rather than only the closed ones,
    because the two markers live at opposite ends of an issue's life. A
    `rejected:` marker is a terminal state, so a closed-unapproved issue is the
    only place it can be; a `bounced:` marker is written when a queued item is
    pushed back out of the queue, so it sits on issues that are still **open**.
    Reading only the closed set — which is what the reason count did when it
    shipped — sees a rejection rate and no misclassification rate at all, and
    the second is the one that says a charter is pointed wrong.

    The cost is one request per issue instead of one per rejection. That is
    real, and it is why this is a terminal tool rather than something a routine
    runs.

    An issue with no marker reads `unrecorded`, which is honest and is what
    every close before the markers shipped looks like.
    """
    rejected: dict[int, str] = {}
    bounced: dict[int, list[str]] = {}
    for number in numbers:
        found = transport.api_paged(f"/repos/{slug}/issues/{number}/comments")
        if not found.ok:
            return rejected, bounced, found.error
        rejected[number] = "unrecorded"
        for comment in found.data if isinstance(found.data, list) else []:
            body = str(comment.get("body", ""))
            if match := REJECTED_RE.search(body):
                rejected[number] = match.group(1)
            # Appended, not replaced: an item bounced twice for two different
            # conditions is two facts about the charter, and keeping only the
            # last one loses the more common of the two.
            for reason in BOUNCED_RE.findall(body):
                bounced.setdefault(number, []).append(reason)
    return rejected, bounced, ""


def fetch_runs(slug: str, since: str) -> tuple[list[dict], str]:
    """Every run recorded in the ledger issues covering the window.

    *Every* ledger issue, not the newest: two runs racing on the first of a month
    both open one, and reading only the latest would report half a month as the
    whole of it. `cowork_checkin.py` tolerates that race precisely because this
    reader does.
    """
    found = transport.api_paged(f"/repos/{slug}/issues?labels={transport.segment(LEDGER_LABEL)}&state=all")
    if not found.ok:
        return [], found.error
    runs: list[dict] = []
    for issue in found.data if isinstance(found.data, list) else []:
        if not isinstance(issue, dict) or "pull_request" in issue:
            continue
        comments = transport.api_paged(f"/repos/{slug}/issues/{issue['number']}/comments")
        if not comments.ok:
            return runs, comments.error
        for comment in comments.data if isinstance(comments.data, list) else []:
            body = str(comment.get("body", ""))
            # The marker, not the fence: a human answering in the issue could
            # write a JSON block, and parsing it would invent a run.
            if LEDGER_MARKER not in body:
                continue
            block = LEDGER_JSON.search(body)
            if not block:
                continue
            try:
                row = json.loads(block.group(1))
            except ValueError:
                continue
            if isinstance(row, dict) and str(row.get("ended_at") or comment.get("created_at", "")) >= since:
                runs.append(row)
    return runs, ""


# --- the report --------------------------------------------------------------


def build_report(
    *,
    prs: list[dict],
    issues: list[dict],
    reasons: dict[int, str],
    runs: list[dict],
    window: int,
    bounces: dict[int, list[str]] | None = None,
) -> dict:
    """Every number, from data already fetched. No network, so it is testable.

    Split from the fetching deliberately: the arithmetic here is the part that
    can be wrong in a way nobody notices, and a function that also makes requests
    is one nobody writes a table-driven test for.
    """
    workstreams = sorted(parse_workstreams())
    by_workstream: dict[str, dict] = {
        name: {"merged": 0, "identified": 0, "approved": 0, "rejected": 0, "open": 0, "cost_usd": 0.0, "runs": 0}
        for name in workstreams
    }
    unclaimed: dict[str, dict] = {}

    def row(name: str) -> dict:
        if name in by_workstream:
            return by_workstream[name]
        # A workstream label nobody's charter declares. Reported rather than
        # dropped: it means a label outlived its charter, and silently binning
        # its work would make the totals disagree with the per-workstream rows.
        return unclaimed.setdefault(
            name, {"merged": 0, "identified": 0, "approved": 0, "rejected": 0, "open": 0, "cost_usd": 0.0, "runs": 0}
        )

    lanes: dict[str, int] = defaultdict(int)
    types: dict[str, int] = defaultdict(int)
    ages: list[float] = []
    capped = 0
    linked = {int(n) for pr in prs for n in CLOSES_RE.findall(str(pr.get("body") or ""))}
    for pr in prs:
        row(workstream_of(pr))["merged"] += 1
        # A merged PR counts as a find only when no issue already did. The auto
        # lane often files nothing — it builds what it just scouted — so counting
        # issues alone would report a fleet that fixes more than it finds; and a
        # PR that closes its issue is that issue, so counting both would report
        # the approved lane twice.
        if not CLOSES_RE.search(str(pr.get("body") or "")):
            row(workstream_of(pr))["identified"] += 1
        lanes[lane_of(pr)] += 1
        types[type_of(pr) or "untagged"] += 1
        if CAPPED_LABEL in _labels(pr):
            capped += 1
        opened, merged = pr.get("created_at"), pr.get("merged_at")
        if opened and merged:
            span = _moment(merged) - _moment(opened)
            ages.append(span.total_seconds() / 3600)

    rejected_by_reason: dict[str, int] = defaultdict(int)
    bounced_by_reason: dict[str, int] = defaultdict(int)
    # The per-workstream split is what `cron/retune.md` reads. A fleet-wide
    # rejection count says the fleet is noisy; only this says *which charter* is
    # pointed at the wrong thing, which is the fix the digest has been
    # describing since it was written.
    by_reason: dict[str, dict[str, dict[str, int]]] = {}

    def reason_row(name: str, kind: str) -> dict[str, int]:
        return by_reason.setdefault(name, {"rejected": {}, "bounced": {}})[kind]

    bounces = bounces or {}
    for issue in issues:
        name = workstream_of(issue)
        labels = _labels(issue)
        row(name)["identified"] += 1
        for reason in bounces.get(int(issue["number"]), []):
            bounced_by_reason[reason] += 1
            counts = reason_row(name, "bounced")
            counts[reason] = counts.get(reason, 0) + 1
        if APPROVAL_LABEL in labels:
            # A stage, not an outcome: an approved find is also a merged one once
            # its PR lands, so this is counted for every issue that reached it.
            row(name)["approved"] += 1
        if int(issue["number"]) in linked:
            # Closed by one of this window's PRs, so its terminal state is
            # `merged` and the PR loop already recorded it. Falling through would
            # file it as a rejection as well — closed, and carrying no approval
            # label when a sweep built it straight off the queue.
            continue
        if issue.get("state") == "open":
            row(name)["open"] += 1
        elif APPROVAL_LABEL not in labels:
            row(name)["rejected"] += 1
            reason = reasons.get(int(issue["number"]), "unrecorded")
            rejected_by_reason[reason] += 1
            counts = reason_row(name, "rejected")
            counts[reason] = counts.get(reason, 0) + 1

    statuses: dict[str, int] = defaultdict(int)
    cost = 0.0
    tokens = 0
    by_routine: dict[str, dict] = {}
    for run in runs:
        statuses[str(run.get("status") or "unknown")] += 1
        cost += float(run.get("cost_usd") or 0.0)
        tokens += int(run.get("total_tokens") or 0)
        entry = by_routine.setdefault(str(run.get("name") or "?"), {"runs": 0, "cost_usd": 0.0, "failed": 0})
        entry["runs"] += 1
        entry["cost_usd"] += float(run.get("cost_usd") or 0.0)
        if run.get("status") in ("failed", "degraded"):
            entry["failed"] += 1

    merged = len(prs)
    fleet_merged = sum(count for lane, count in lanes.items() if lane in FLEET_LANES)
    return {
        "window_days": window,
        "merged": merged,
        # The number people mean by "what did the fleet do". Kept beside the total
        # rather than replacing it, because the total is what a `cowork` label
        # search returns and a report that silently disagrees with the obvious
        # query is one nobody trusts twice.
        "fleet_merged": fleet_merged,
        "by_lane": dict(sorted(lanes.items())),
        "by_type": dict(sorted(types.items())),
        "identified": sum(r["identified"] for r in list(by_workstream.values()) + list(unclaimed.values())),
        "approved": sum(r["approved"] for r in list(by_workstream.values()) + list(unclaimed.values())),
        "rejected": sum(r["rejected"] for r in list(by_workstream.values()) + list(unclaimed.values())),
        "still_open": sum(r["open"] for r in list(by_workstream.values()) + list(unclaimed.values())),
        "rejected_by_reason": dict(sorted(rejected_by_reason.items())),
        # A bounce is a *misclassification*, and a different fault from a
        # rejection: a rejected find was real and unwanted, a bounced one was
        # called `auto` by a scout that could not prove it.
        "bounced": sum(bounced_by_reason.values()),
        "bounced_by_reason": dict(sorted(bounced_by_reason.items())),
        "reasons_by_workstream": {k: v for k, v in sorted(by_reason.items()) if any(v.values())},
        # Merged past findings the review ran out of rounds to pursue. In the set
        # on purpose: it is the one number that goes *up* when the fleet ships
        # faster, so a throughput report without it can only ever look good.
        "review_capped": capped,
        "median_hours_to_merge": round(_median(ages), 1) if ages else None,
        "runs": len(runs),
        "run_status": dict(sorted(statuses.items())),
        "cost_usd": round(cost, 2),
        "total_tokens": tokens,
        # The efficiency number the whole ledger exists for. `None` rather than
        # zero when nothing merged: dividing by no merges is not a cost of zero.
        "cost_per_merged_pr": round(cost / fleet_merged, 2) if fleet_merged and cost else None,
        "by_routine": dict(sorted(by_routine.items())),
        "by_workstream": {k: v for k, v in sorted(by_workstream.items()) if any(v.values())},
        "unclaimed_workstreams": dict(sorted(unclaimed.items())),
    }


def _moment(stamp: str) -> datetime:
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


# --- rendering ---------------------------------------------------------------


def render_text(report: dict) -> str:
    """Plain lines, matching `cowork_setup.py --text` rather than reaching for
    Rich: this prints in a terminal, in CI, and pasted into a Slack code block,
    and only one of those renders markup."""
    out: list[str] = []
    add = out.append
    add(f"FLEET · last {report['window_days']} days")
    add("─" * 56)

    lanes = " · ".join(f"{count} {lane}" for lane, count in report["by_lane"].items())
    add(f"{'Merged':<18}{report['merged']:>5} PRs" + (f"   ({lanes})" if lanes else ""))
    add(f"{'  by the fleet':<18}{report['fleet_merged']:>5}    the rest is people building cowork")
    add(f"{'Identified':<18}{report['identified']:>5}    ~floor: an auto find files no issue")
    add(f"{'Fixed':<18}{report['merged']:>5}    {_pct(report['merged'], report['identified'])} of identified")
    add(f"{'Rejected':<18}{report['rejected']:>5}")
    for reason, count in report["rejected_by_reason"].items():
        add(f"    {reason:<26}{count:>3}")
    if report["bounced"]:
        add(f"{'Bounced':<18}{report['bounced']:>5}    queued, then pushed back out — a misclassification")
        for reason, count in report["bounced_by_reason"].items():
            add(f"    {reason:<26}{count:>3}")
    add(f"{'Still open':<18}{report['still_open']:>5}")
    if report["median_hours_to_merge"] is not None:
        add(f"{'Median to merge':<18}{report['median_hours_to_merge']:>5} h")
    add(f"{'Merged capped':<18}{report['review_capped']:>5}    findings recorded, not fixed")

    if report["by_workstream"]:
        add("")
        add(f"{'By workstream':<24}{'merged':>7}{'found':>7}{'rejected':>10}{'open':>6}")
        for name, row in report["by_workstream"].items():
            add(f"  {name:<22}{row['merged']:>7}{row['identified']:>7}{row['rejected']:>10}{row['open']:>6}")

    add("")
    if report["runs"]:
        statuses = " · ".join(f"{count} {status}" for status, count in report["run_status"].items())
        add(f"{'Runs':<18}{report['runs']:>5}    ({statuses})")
        add(
            f"{'Cost':<18}{'$' + format(report['cost_usd'], '.2f'):>5}",
        )
        if report["cost_per_merged_pr"] is not None:
            add(f"{'Per merged PR':<18}{'$' + format(report['cost_per_merged_pr'], '.2f'):>5}")
    else:
        # Not "the fleet ran nothing". Until every routine has closed with
        # `--record` at least once, this is what an empty ledger looks like, and
        # reporting it as zero runs would be a lie with a plausible shape.
        add("Runs               —    no ledger entries in this window")
        add("                        (routines record on check-in; see cowork/check-in.md)")

    if report["reasons_by_workstream"]:
        add("")
        add("Why work did not ship, by workstream — what cron/retune.md reads")
        for name, kinds in report["reasons_by_workstream"].items():
            parts = [
                f"{kind}: " + ", ".join(f"{r}×{n}" for r, n in sorted(counts.items()))
                for kind, counts in kinds.items()
                if counts
            ]
            add(f"  {name:<22}{' · '.join(parts)}")

    if report["unclaimed_workstreams"]:
        add("")
        add("Labels with no charter: " + ", ".join(report["unclaimed_workstreams"]))
    return "\n".join(out)


def _pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "—"


def collect(
    slug: str, *, window: int, branches: bool = True, runs: bool = True, now: datetime | None = None
) -> tuple[dict, list[str]]:
    """Fetch everything and build the report. Returns ``(report, warnings)``.

    ``runs=False`` skips the ledger entirely, and it exists for exactly one
    caller: `cron/retune.md`, which is a *routine*. **No routine may read the
    run ledger.** Outcomes — issues, PRs, markers, `calibration.md` — are
    durable, human-readable records of decisions, and reading them is what every
    sweep already does. Run telemetry is a different thing: it is what the fleet
    *spent*, and a routine that decides anything from it makes the fleet's
    behaviour a function of its own resource consumption, which nobody can audit
    and which points somewhere bad. The flag is the seam, and
    `tests/unit/test_cowork_retune.py` asserts the routine passes it.

    A failed read is a warning and a missing section, never an exception and
    never a zero: `cowork_setup.py` draws the same line with `slots: null`, and
    for the same reason — a number nobody could read, printed as a number, is the
    one failure mode a report cannot recover from afterwards.
    """
    since = since_iso(window, now)
    warnings: list[str] = []

    prs, error = fetch_merged_prs(slug, since)
    if error:
        warnings.append(f"merged PRs: {error}")
    if prs and branches:
        if branch_error := fetch_pr_branches(slug, prs):
            warnings.append(f"PR branches (lanes will read 'other'): {branch_error}")

    issues, error = fetch_proposals(slug, since)
    if error:
        warnings.append(f"proposals: {error}")

    reasons, bounces, error = fetch_markers(slug, [int(issue["number"]) for issue in issues])
    if error:
        warnings.append(f"failure markers: {error}")

    ledger: list[dict] = []
    if runs:
        ledger, error = fetch_runs(slug, since)
        if error:
            warnings.append(f"run ledger: {error}")
    else:
        warnings.append("run ledger not read (--no-runs): cost and duration are absent by request")

    report = build_report(prs=prs, issues=issues, reasons=reasons, bounces=bounces, runs=ledger, window=window)
    report["warnings"] = warnings
    return report, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--window", type=int, default=30, help="days to report on (default: 30)")
    parser.add_argument("--json", action="store_true", help="the whole report as JSON")
    parser.add_argument("--no-branches", action="store_true", help="skip the per-PR branch read; lanes read 'other'")
    parser.add_argument(
        "--no-runs",
        action="store_true",
        help="skip the run ledger — required of cron/retune.md, the one routine that reads this",
    )
    args = parser.parse_args(argv)

    slug = transport.resolve_slug(ROOT)
    if not slug:
        print("[metrics] could not resolve the repository slug", file=sys.stderr)
        return 2

    report, warnings = collect(slug, window=args.window, branches=not args.no_branches, runs=not args.no_runs)
    print(json.dumps(report, indent=2) if args.json else render_text(report))
    for warning in warnings:
        print(f"[metrics] {warning}", file=sys.stderr)
    # Warnings are not a failure: a partial report is the useful thing, and the
    # missing half is named on stderr rather than swallowed.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
