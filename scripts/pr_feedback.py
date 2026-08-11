#!/usr/bin/env python3
"""Decide whether a pull request has review feedback that nobody answered.

Five things comment on a PR in this repo — ``claude-review.yml`` after CI goes
green, the ``pr-opened-dod-audit`` routine, ``auto-version.yml``,
``dependabot-auto.yml``, and ``claude.yml`` when someone types ``@claude`` — and
until this script existed, nothing ever read a word of it back. ``/ship`` exits at
``gh pr create``, minutes before the review posts; ``/babysit-prs`` reads
``gh pr checks`` and nothing else. So findings landed on the timeline and PRs
merged straight past them.

This turns that into a **commit status**, which is the only artefact GitHub will
actually refuse a merge over. Everything else in the loop stays advisory: the
reviewers keep reviewing and never block, and this counts.

**The gate is arithmetic, not judgment.** It does not read a finding and guess
whether it was addressed — the producers stamp a machine-readable verdict into
their own comment (``<!-- pr-feedback: claude-review open=2 -->``), exactly like
the ``<!-- cowork-dod -->`` marker already used to find-and-edit the DoD audit.
A checker that had to interpret prose would be wrong quietly and often; this one
is a subtraction.

Three rules carry the whole design:

* **Only the latest verdict per producer counts.** Every push re-runs CI, which
  re-runs the review, which posts a fresh verdict. Fix the finding and the next
  verdict is ``open=0`` and the gate clears on its own — no reply, no ceremony.
  That is what keeps this from becoming a box nobody can tick.
* **An unfixed finding needs an answer newer than the finding, from someone with
  write access.** A reply carrying ``<!-- addressed: claude-review -->`` clears
  the pass it follows. Deliberate won't-fixes are the only case that ever has to
  be re-stated, and only when the branch moved underneath them. "Newer" is
  measured against when the verdict was last *written*, not created — a producer
  that edits one comment in place would otherwise be answerable in advance — and
  the write-access requirement is because this repo is public.
* **"Not yet" and "never" are different answers.** CI still running is `pending`.
  CI green twenty minutes ago with no review in sight is a `failure` that names
  the missing review — ``claude-review.yml`` has silently stopped firing twice in
  this repo's history (see its own header), and both times nothing noticed.

Usage::

    uv run python scripts/pr_feedback.py --pr 123            # human-readable, exit 1 if open
    uv run python scripts/pr_feedback.py --pr 123 --json     # for /pr-feedback to iterate
    uv run python scripts/pr_feedback.py --pr 123 --status   # post the status (CI only)
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

# The status context a branch ruleset makes required. Changing this string
# silently un-blocks every PR, because the ruleset keeps waiting on a context
# nothing posts any more.
STATUS_CONTEXT = "pr-feedback"

# The one comment this script owns, found and edited in place rather than added
# to. Same trick as `<!-- cowork-dod -->`: one marker, one comment, no pile-up.
STICKY_MARKER = "<!-- pr-feedback-status -->"

# The escape hatch. A gate with no override is a gate that eventually bricks the
# repo — a review that errors out, a producer that changes format, a finding that
# is simply wrong. The label is honoured, and the sticky comment says so out loud
# so an overridden PR never looks like a clean one.
OVERRIDE_LABEL = "feedback-override"

# Mirrors the author filter in `claude-review.yml` and `pr-opened-dod-audit.md`:
# bot-authored PRs are not reviewed unless they came from the cowork implement
# job. Kept identical on purpose — if this gate expected a review the reviewer
# was never going to write, the PR would sit red forever with nothing to fix.
BOT_AUTHORS = frozenset({"dependabot[bot]", "github-actions[bot]"})
COWORK_LABEL = "cowork"

# Branch namespaces an unattended run pushes to: `cowork/…` (cowork-builder),
# `feature/issue-N-…` (claude.yml's implement job), plus the two workflows that
# open their own fix PRs. A PR from one of these was written by a machine, which
# has two consequences below — it is never waved through as "bot-authored, review
# not applicable", and its author cannot answer its own review.
#
# Kept in step with the author filter in `claude-review.yml`: this gate must
# never expect a review that the reviewer was never going to write, or the PR
# sits red with nothing to fix. Widen both or neither.
UNATTENDED_BRANCH_PREFIXES = (
    "cowork/",
    "feature/issue-",
    "security/codeql-triage",
    "ci-sentinel/",
)

# How long after CI goes green a review may take before its absence is treated as
# a fault rather than as latency.
REVIEW_GRACE = timedelta(minutes=20)

# The workflow whose success gates the reviewers (`.github/workflows/ci.yml`).
CI_WORKFLOW_NAME = "CI"

# GitHub truncates a status description past 140 characters.
DESCRIPTION_LIMIT = 140

# How many times Claude Review may speak on one PR before its findings stop
# blocking the merge.
#
# Two, because the loop has no natural terminator otherwise. An adversarial
# reviewer reading a large diff will always find *something* — four consecutive
# rounds on PR #222 each produced real should-fix findings, and there was no
# reason to expect a fifth to be empty. "Merge when the reviewer reports zero" is
# therefore not a condition that reliably arrives, and a gate whose exit
# condition may never occur is a gate that gets deleted.
#
# At the cap the findings do not vanish: they are still listed in the sticky
# comment, the PR is labelled `review-capped`, and the daily standup names it.
# What changes is that they stop holding the merge. That is a real loosening, and
# what makes it defensible is that a merge no longer reaches users — it publishes
# a pre-release, and the weekly promotion is a human checkpoint. If that ever
# stops being true, revisit this number with it.
MAX_REVIEW_ROUNDS = 2

# Applied when the cap is reached with findings still open, so the state is
# visible on the PR list and queryable afterwards rather than buried in a comment.
CAPPED_LABEL = "review-capped"

# One page of review threads. Cheap to raise, and never silently exceeded — a
# truncated page becomes an open item of its own rather than a quiet undercount.
THREAD_PAGE = 100


@dataclass(frozen=True)
class Producer:
    """Something that reviews a PR and stamps a countable verdict on its comment.

    ``required`` is the difference between "this reviewer had nothing to say" and
    "this reviewer never spoke". Claude Review runs on every PR, so its silence is
    a fault. The DoD audit is a hand-registered cowork routine that may not be
    deployed at all, so it is honoured when present and never waited on.
    """

    key: str
    label: str
    required: bool
    pattern: re.Pattern[str]
    # The exact login this producer speaks under, when there is one. Without it a
    # verdict has no author check at all — weaker than the acknowledgement rule
    # below, despite being the stronger statement: an ack answers findings, a
    # verdict replaces the count of them. On a public repo that meant any account
    # could post `open=0` and turn the gate green, and with the round cap it would
    # also mean two comments could exhaust the cap on somebody else's PR.
    authors: frozenset[str] | None = None


# Two of the five commenters named above are registered here, and the omissions
# are deliberate rather than pending: `auto-version` and `dependabot-auto` report
# facts about a bump and never produce a finding, and `claude.yml` answers a
# human who is already in the conversation and will speak again if it is wrong.
# A producer earns a row by being expected to raise something nobody asked for.
PRODUCERS: tuple[Producer, ...] = (
    Producer(
        key="claude-review",
        label="Claude Review",
        required=True,
        pattern=re.compile(r"<!--\s*pr-feedback:\s*claude-review\s+open=(\d+)\s*-->"),
        # `claude-review.yml` posts through the Claude GitHub App. Verified
        # against the live comments on PR #222, not assumed. A `[bot]` suffix
        # cannot be forged — GitHub logins may not contain brackets.
        authors=frozenset({"claude[bot]"}),
    ),
    Producer(
        key="cowork-dod",
        label="DoD audit",
        required=False,
        # No pinned `authors`, deliberately: a cowork routine writes this one, and
        # it may run as the maintainer or as a bot depending on how it is invoked.
        # So it falls back to write access, which still stops a stranger on a
        # public repo. It is advisory and never required — forging its `open=0`
        # suppresses DoD-audit findings and leaves `claude-review`, the producer
        # that gates the merge, untouched. Named here rather than left as an
        # asymmetry to notice.
        # `open=` is what makes it countable. A bare `<!-- cowork-dod -->` is the
        # older format and deliberately reads as no verdict rather than as zero:
        # a routine that has not been updated yet must not be able to clear a gate
        # by saying nothing.
        pattern=re.compile(r"<!--\s*cowork-dod\s+open=(\d+)\s*-->"),
    ),
)

ACK_RE = re.compile(r"<!--\s*addressed:\s*([a-z0-9][a-z0-9-]*)\s*-->", re.IGNORECASE)

# Who may answer a finding. This repo is public, so without this any account on
# the internet could clear the gate on somebody else's PR with a single drive-by
# `<!-- addressed: claude-review -->`. An ack from outside the set is read as
# ordinary prose; the sticky comment still says how to clear the check, so the
# way out of a wrongly-red gate is never closed, only moved to someone with
# write access.
#
# Write access is necessary and, on an unattended PR, not sufficient: see
# `is_acknowledged`, which additionally refuses an ack from the PR's own author.
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


@dataclass(frozen=True)
class Comment:
    """One comment on the PR's issue timeline, or one submitted review's body.

    Two timestamps, because a producer is told to **edit its comment in place**
    rather than pile up a new one on every push (`pr-opened-dod-audit.md` step 5)
    and an edit does not move ``created_at``. ``written_at`` is when the text
    that is there now was last written, and it is what a *verdict* is dated by:
    without it, a reply from the day the PR opened would go on clearing a set of
    findings that were rewritten into that comment ten minutes ago.

    An *acknowledgement* is deliberately dated by ``created_at`` instead, so that
    editing an old reply cannot forward-date it over a finding it never saw. The
    asymmetry is the fail-closed direction of the same fact.

    ``association`` is GitHub's ``author_association``; ``kind`` separates an
    issue comment from a review body, whose ids come from different spaces.
    """

    id: int
    author: str
    body: str
    created_at: datetime
    updated_at: datetime | None = None
    association: str = "NONE"
    kind: str = "comment"  # "comment" | "review"

    @property
    def written_at(self) -> datetime:
        return max(self.created_at, self.updated_at) if self.updated_at else self.created_at


@dataclass(frozen=True)
class Thread:
    """One inline review thread. ``is_resolved`` is GitHub's own resolve button."""

    id: str
    is_resolved: bool
    is_outdated: bool
    path: str | None
    line: int | None
    authors: tuple[str, ...]
    excerpt: str


@dataclass(frozen=True)
class CIState:
    """The CI run for this exact head SHA. ``conclusion`` is None when none ran."""

    conclusion: str | None
    completed_at: datetime | None


@dataclass(frozen=True)
class Snapshot:
    """Everything the verdict is computed from. Fetched once, then never re-read."""

    number: int
    head_sha: str
    author: str
    is_draft: bool
    labels: tuple[str, ...]
    review_decision: str | None
    comments: tuple[Comment, ...]
    threads: tuple[Thread, ...]
    ci: CIState
    threads_truncated: bool = False
    head_ref: str = ""
    # Who applied `feedback-override`, when it is present. Empty when nobody did,
    # or when the timeline could not be read — those are different, and
    # ``classify`` treats them differently.
    override_actor: str = ""


@dataclass(frozen=True)
class OpenItem:
    """One thing standing between this PR and a merge."""

    kind: str  # "producer" | "thread" | "changes-requested" | "truncated"
    key: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "key": self.key, "detail": self.detail}


@dataclass(frozen=True)
class Verdict:
    """The commit status to post, and why."""

    state: str  # "success" | "failure" | "pending"
    description: str
    items: tuple[OpenItem, ...] = ()

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "description": self.description,
            "items": [item.as_dict() for item in self.items],
        }


# --- classification (pure; every test in tests/unit/test_pr_feedback.py lands here)


def parse_timestamp(value: str | None) -> datetime | None:
    """GitHub's ISO-8601, normalised to aware UTC so comparisons cannot explode."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def acknowledged_producers(body: str) -> set[str]:
    """Producer keys this comment claims to have answered."""
    return {match.group(1).lower() for match in ACK_RE.finditer(body)}


def is_authentic_verdict(comment: Comment, producer: Producer) -> bool:
    """Whether this comment is allowed to *be* a verdict from ``producer``.

    Where a producer speaks under a known login, that login is the whole test.
    Claude Review always arrives from ``claude[bot]``, and a ``[bot]`` suffix
    cannot be forged because GitHub logins may not contain brackets.

    Association is deliberately *not* also required: a bot commenting through
    ``GITHUB_TOKEN`` carries ``author_association: NONE``, so demanding write
    access would reject every genuine review and wedge the PR on one that could
    never qualify — the deadlock this module exists to avoid.

    A producer with no pinned login (the DoD audit, which a cowork routine writes
    as the maintainer or as a bot depending on how it runs) falls back to write
    access, which still stops a stranger on a public repo.
    """
    if producer.authors is not None:
        return comment.author in producer.authors
    if comment.author.endswith("[bot]"):
        return True
    return comment.association.upper() in TRUSTED_ASSOCIATIONS


def review_rounds(comments: Iterable[Comment], producer: Producer) -> int:
    """How many times this producer has posted a verdict **that found something**.

    Two deliberate narrowings.

    *Authentic only*, so a forged comment cannot inflate the count toward
    ``MAX_REVIEW_ROUNDS`` and hand somebody a free pass on a PR they do not own.

    *Non-empty only*, which is the difference between bounding the loop and
    breaking the gate. Counting every verdict would cap a PR whose first review
    was clean the moment a second one ran — so a regression introduced after a
    clean pass could never reopen the gate, and "review found a new problem" and
    "review ran out of patience" would be the same state. What runs forever is
    the *findings* loop: find, fix, find again. That is what gets counted.
    """
    total = 0
    for comment in comments:
        match = producer.pattern.search(comment.body)
        if match is None or not is_authentic_verdict(comment, producer):
            continue
        if int(match.group(1)) > 0:
            total += 1
    return total


def latest_verdict(comments: Iterable[Comment], producer: Producer) -> tuple[Comment, int] | None:
    """The newest countable verdict from one producer, or None if it never posted.

    Newest wins outright — an older pass is stale the moment a newer one exists,
    and re-reviewing after a push is precisely how a fixed finding disappears
    without anyone replying to it.

    Dated by ``written_at``: the DoD audit is instructed to edit one comment in
    place on every push, so its ``created_at`` is the hour the PR opened while
    the verdict inside it may be a minute old.

    Comments that are not authentic verdicts (see ``is_authentic_verdict``) are
    skipped entirely, so a forged one cannot even win the newest-wins race.
    """
    best: tuple[Comment, int] | None = None
    for comment in comments:
        match = producer.pattern.search(comment.body)
        if match is None:
            continue
        if not is_authentic_verdict(comment, producer):
            continue
        count = int(match.group(1))
        # Ties broken by id: two comments can share a timestamp at second
        # granularity, and a coin-flip winner would make the gate flap.
        if best is None or (comment.written_at, comment.id) > (best[0].written_at, best[0].id):
            best = (comment, count)
    return best


def is_unattended(snapshot: Snapshot) -> bool:
    """Whether this PR was written by a machine rather than by a person.

    Two signals, either of which is enough: the ``cowork`` label, and a head
    branch in one of the namespaces an unattended run pushes to. The label alone
    was not enough once the auto lane widened — `cowork/house-rules.md` requires
    it on every routine PR, but a run truncated between `git push` and
    `gh pr create --label` leaves an unlabelled machine PR behind, and that is
    exactly the PR that must not be trusted more than a labelled one.
    """
    return COWORK_LABEL in snapshot.labels or snapshot.head_ref.startswith(UNATTENDED_BRANCH_PREFIXES)


def is_acknowledged(
    comments: Iterable[Comment],
    producer_key: str,
    after: datetime,
    after_id: int = 0,
    deny_author: str | None = None,
) -> bool:
    """Whether a trusted reply newer than ``after`` answers this producer's pass.

    ``after_id`` breaks the same second-granularity tie ``latest_verdict`` breaks:
    a reply posted in the same second as the verdict it answers is newer, and a
    strict ``>`` on the timestamp alone would drop it.

    ``deny_author`` is the PR's own author, passed only for an unattended PR, and
    it is what makes this gate mean anything once machines merge their own work.
    A cowork routine posts under an account with write access, so
    ``TRUSTED_ASSOCIATIONS`` alone would let the thing that wrote the change also
    declare the review of it answered — a gate whose key is held by the applicant.
    It may still *fix* a finding: a push triggers a re-review, and
    ``latest_verdict`` then reads ``open=0`` from the reviewer itself. What it may
    no longer do is disagree in a comment and merge anyway.

    A human answering the review on their own PR is untouched: a person read it,
    which is the whole thing being checked for.
    """
    return any(
        (comment.created_at, comment.id) > (after, after_id)
        and comment.association.upper() in TRUSTED_ASSOCIATIONS
        and (deny_author is None or comment.author != deny_author)
        and producer_key in acknowledged_producers(comment.body)
        for comment in comments
    )


def open_threads(snapshot: Snapshot) -> list[OpenItem]:
    """Unresolved inline threads that somebody other than the author started.

    ``isOutdated`` is deliberately not a free pass: a thread going outdated means
    the line moved, not that the point was taken. Only the resolve button closes
    one, which is the same signal a human reviewer already uses.
    """
    items: list[OpenItem] = []
    for thread in snapshot.threads:
        if thread.is_resolved:
            continue
        if not any(author != snapshot.author for author in thread.authors):
            continue
        where = f"{thread.path}:{thread.line}" if thread.path and thread.line else (thread.path or "the PR")
        detail = f"unresolved thread on {where}"
        if thread.excerpt:
            detail = f"{detail} — {thread.excerpt}"
        items.append(OpenItem("thread", thread.id, detail))
    return items


def waiting_reason(snapshot: Snapshot, producer: Producer, now: datetime) -> str | None:
    """Why a missing verdict is latency rather than a fault — None once it is a fault."""
    ci = snapshot.ci
    if ci.conclusion is None:
        return f"waiting for CI before {producer.label}"
    if ci.conclusion != "success":
        return f"CI is {ci.conclusion} — {producer.label} does not run until it is green"
    if ci.completed_at is not None and now - ci.completed_at < REVIEW_GRACE:
        return f"waiting for {producer.label}"
    return None


def open_producers(snapshot: Snapshot, now: datetime) -> tuple[list[OpenItem], str | None]:
    """Producers with unanswered findings, plus the first reason to still be waiting."""
    items: list[OpenItem] = []
    waiting: str | None = None
    for producer in PRODUCERS:
        latest = latest_verdict(snapshot.comments, producer)
        if latest is None:
            if not producer.required:
                continue
            reason = waiting_reason(snapshot, producer, now)
            if reason is not None:
                waiting = waiting or reason
            else:
                items.append(
                    OpenItem(
                        "producer",
                        producer.key,
                        f"{producer.label} never posted a verdict on {snapshot.head_sha[:7]} — "
                        f"check the workflow actually ran",
                    )
                )
            continue
        comment, count = latest
        if count == 0:
            continue
        if is_acknowledged(
            snapshot.comments,
            producer.key,
            after=comment.written_at,
            after_id=comment.id,
            deny_author=snapshot.author if is_unattended(snapshot) else None,
        ):
            continue
        plural = "finding" if count == 1 else "findings"
        items.append(
            OpenItem("producer", producer.key, f"{producer.label}: {count} unanswered {plural} (comment {comment.id})")
        )
    return items, waiting


def describe(items: Sequence[OpenItem]) -> str:
    """The one line GitHub shows next to the check. Counted, then named."""
    noun = "item" if len(items) == 1 else "items"
    head = f"{len(items)} unanswered review {noun}"
    if items:
        head = f"{head}: {items[0].detail}"
        if len(items) > 1:
            head = f"{head} (+{len(items) - 1} more)"
    return head[:DESCRIPTION_LIMIT]


def classify(snapshot: Snapshot, now: datetime) -> Verdict:
    """The whole decision. Everything above feeds this; nothing below re-decides it."""
    if OVERRIDE_LABEL in snapshot.labels:
        # The override is a stronger dismissal than any marker — it clears every
        # finding, every unresolved thread and a CHANGES_REQUESTED review at once —
        # so the rule that governs the marker has to govern it too, or the lane
        # simply uses the bigger lever. On an unattended PR the applicant holds
        # write access, and `gh pr edit --add-label` sits inside the sweeps' bare
        # `Bash` grant, so "a human's call" was a convention rather than a fact.
        #
        # Unknown actor still honours the override. This label exists to unbrick a
        # gate that has genuinely gone wrong, and refusing it on a timeline we
        # could not read would turn one API failure into a PR nobody can merge —
        # the exact outcome it is the escape hatch for.
        if is_unattended(snapshot) and snapshot.override_actor and snapshot.override_actor == snapshot.author:
            return Verdict(
                "failure",
                f"`{OVERRIDE_LABEL}` was applied by this PR's own author — it does not count here",
                (
                    OpenItem(
                        "override",
                        OVERRIDE_LABEL,
                        f"`{OVERRIDE_LABEL}` applied by {snapshot.override_actor}, who authored this "
                        f"machine PR — a human other than the author must apply it, or fix the findings",
                    ),
                ),
            )
        who = f" by {snapshot.override_actor}" if snapshot.override_actor else ""
        return Verdict("success", f"overridden by the `{OVERRIDE_LABEL}` label{who}")
    if snapshot.is_draft:
        return Verdict("success", "draft — review not applicable")
    if snapshot.author in BOT_AUTHORS and not is_unattended(snapshot):
        return Verdict("success", f"{snapshot.author} — review not applicable")

    items = open_threads(snapshot)
    if snapshot.review_decision == "CHANGES_REQUESTED":
        items.append(OpenItem("changes-requested", "review", "a reviewer requested changes and has not re-approved"))
    if snapshot.threads_truncated:
        items.append(
            OpenItem("truncated", "threads", f"more than {THREAD_PAGE} review threads — this gate cannot see them all")
        )

    producer_items, waiting = open_producers(snapshot, now)
    items.extend(producer_items)

    # The cap. Reached only on the required producer's own findings — an
    # unresolved human thread or a requested-changes review is a person waiting
    # for an answer, and running out of *review rounds* says nothing about those.
    # Capping them too would let a PR merge past somebody who is still talking.
    review = PRODUCERS[0]
    if review_rounds(snapshot.comments, review) >= MAX_REVIEW_ROUNDS:
        human = [item for item in items if item.kind != "producer" or item.key != review.key]
        if not human:
            capped = [item for item in items if item.kind == "producer" and item.key == review.key]
            if capped:
                noun = "finding" if len(capped) == 1 else "findings"
                return Verdict(
                    "success",
                    f"review capped at {MAX_REVIEW_ROUNDS} rounds — {len(capped)} {noun} recorded, not fixed",
                    tuple(capped),
                )

    # Open items win over waiting: something is already unanswered, and a later
    # review pass can only add to that.
    if items:
        return Verdict("failure", describe(items), tuple(items))
    if waiting is not None:
        return Verdict("pending", waiting[:DESCRIPTION_LIMIT])
    return Verdict("success", "no open review feedback")


# --- gh ----------------------------------------------------------------------


def _gh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False)


def _gh_json(*args: str) -> object | None:
    result = _gh(*args)
    if result.returncode != 0:
        print(f"[pr-feedback] gh {' '.join(args[:2])} failed: {result.stderr.strip()}", file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout or "null")
    except json.JSONDecodeError:
        print(f"[pr-feedback] gh {' '.join(args[:2])} returned non-JSON", file=sys.stderr)
        return None


REVIEW_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $page: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: $page) {
        pageInfo { hasNextPage }
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          comments(first: 50) { nodes { author { login } body } }
        }
      }
    }
  }
}
"""


def fetch_snapshot(number: int, slug: str) -> Snapshot | None:
    """Read the PR once, from four read-only calls, into the shape ``classify`` wants."""
    owner, _, name = slug.partition("/")

    meta = _gh_json(
        "pr", "view", str(number), "--json", "number,headRefOid,headRefName,isDraft,author,labels,reviewDecision"
    )
    if not isinstance(meta, dict):
        return None
    head_sha = meta.get("headRefOid") or ""

    raw_comments = _gh_json("api", f"repos/{slug}/issues/{number}/comments", "--paginate")
    if not isinstance(raw_comments, list):
        return None
    comments = tuple(
        Comment(
            id=int(item.get("id", 0)),
            author=(item.get("user") or {}).get("login", ""),
            body=item.get("body") or "",
            created_at=parse_timestamp(item.get("created_at")) or datetime.min.replace(tzinfo=UTC),
            updated_at=parse_timestamp(item.get("updated_at")),
            association=item.get("author_association") or "NONE",
        )
        for item in raw_comments
    ) + fetch_reviews(slug, number)

    graph = _gh_json(
        "api",
        "graphql",
        "-f",
        f"query={REVIEW_THREADS_QUERY}",
        "-F",
        f"owner={owner}",
        "-F",
        f"name={name}",
        "-F",
        f"number={number}",
        "-F",
        f"page={THREAD_PAGE}",
    )
    threads: tuple[Thread, ...] = ()
    truncated = False
    if isinstance(graph, dict):
        block = (((graph.get("data") or {}).get("repository") or {}).get("pullRequest") or {}).get(
            "reviewThreads"
        ) or {}
        truncated = bool((block.get("pageInfo") or {}).get("hasNextPage"))
        threads = tuple(_thread(node) for node in block.get("nodes") or [])

    return Snapshot(
        number=number,
        head_sha=head_sha,
        author=(meta.get("author") or {}).get("login", ""),
        is_draft=bool(meta.get("isDraft")),
        labels=tuple(label.get("name", "") for label in meta.get("labels") or []),
        review_decision=meta.get("reviewDecision") or None,
        comments=comments,
        threads=threads,
        ci=fetch_ci(slug, head_sha),
        threads_truncated=truncated,
        head_ref=meta.get("headRefName") or "",
        # Only asked when the label is actually present — one paginated call for a
        # question that is almost always moot.
        override_actor=(
            fetch_override_actor(slug, number)
            if OVERRIDE_LABEL in tuple(label.get("name", "") for label in meta.get("labels") or [])
            else ""
        ),
    )


def _thread(node: dict) -> Thread:
    nodes = ((node.get("comments") or {}).get("nodes")) or []
    authors = tuple((entry.get("author") or {}).get("login", "") for entry in nodes)
    first_body = (nodes[0].get("body") if nodes else "") or ""
    excerpt = " ".join(first_body.split())[:80]
    return Thread(
        id=node.get("id", ""),
        is_resolved=bool(node.get("isResolved")),
        is_outdated=bool(node.get("isOutdated")),
        path=node.get("path"),
        line=node.get("line"),
        authors=authors,
        excerpt=excerpt,
    )


def fetch_reviews(slug: str, number: int) -> tuple[Comment, ...]:
    """Submitted review bodies, folded in beside the issue comments.

    A review's top-level body is not an issue comment and never appears in
    ``/issues/{n}/comments``, so a marker written through the review box rather
    than the comment box would be invisible in *both* directions — a producer's
    verdict, and a maintainer's ``<!-- addressed: … -->``.

    What is deliberately not here: a human review body that carries findings in
    prose and no marker is not counted as an open item. GitHub already has two
    unambiguous ways to say "act on this", an inline thread and a Request-changes
    review, and both are gated. Treating every ``COMMENTED`` review as blocking
    would turn "LGTM, nice work" into a merge block, which is how a check gets
    deleted rather than answered.
    """
    raw = _gh_json("api", f"repos/{slug}/pulls/{number}/reviews", "--paginate")
    if not isinstance(raw, list):
        return ()
    return tuple(
        Comment(
            id=int(item.get("id", 0)),
            author=(item.get("user") or {}).get("login", ""),
            body=item.get("body") or "",
            # Reviews expose no edited-at, so a verdict written into one is dated
            # by submission. Producers post comments, not reviews; this path is
            # for the maintainer who typed the ack into the review box.
            created_at=parse_timestamp(item.get("submitted_at")) or datetime.min.replace(tzinfo=UTC),
            association=item.get("author_association") or "NONE",
            kind="review",
        )
        for item in raw
        if (item.get("body") or "").strip()
    )


def fetch_override_actor(slug: str, number: int) -> str:
    """Who most recently applied ``feedback-override``, or "" if unknown.

    Read from the issue events timeline, which is the only place the *actor* of a
    label is recorded — the labels on a PR carry no provenance at all. Empty on
    any failure, and ``classify`` reads that as "unknown" rather than as "the
    author", so a timeline this cannot fetch never turns into a blocked PR.
    """
    raw = _gh_json("api", f"repos/{slug}/issues/{number}/events", "--paginate")
    if not isinstance(raw, list):
        return ""
    actor = ""
    for item in raw:
        if not isinstance(item, dict) or item.get("event") != "labeled":
            continue
        if ((item.get("label") or {}).get("name")) != OVERRIDE_LABEL:
            continue
        # Events arrive oldest-first; the last one wins, matching "most recently
        # applied" after any remove/re-add.
        actor = ((item.get("actor") or {}).get("login")) or ""
    return actor


def fetch_ci(slug: str, head_sha: str) -> CIState:
    """The CI run for this exact SHA — not the branch's latest, which may be older."""
    if not head_sha:
        return CIState(None, None)
    payload = _gh_json("api", f"repos/{slug}/actions/runs?head_sha={head_sha}&per_page=100")
    if not isinstance(payload, dict):
        return CIState(None, None)
    runs = [run for run in payload.get("workflow_runs") or [] if run.get("name") == CI_WORKFLOW_NAME]
    if not runs:
        return CIState(None, None)
    latest = max(runs, key=lambda run: run.get("created_at") or "")
    return CIState(latest.get("conclusion"), parse_timestamp(latest.get("updated_at")))


def repo_slug() -> str | None:
    payload = _gh_json("repo", "view", "--json", "nameWithOwner")
    return payload.get("nameWithOwner") if isinstance(payload, dict) else None


def current_pr() -> int | None:
    payload = _gh_json("pr", "view", "--json", "number")
    return int(payload["number"]) if isinstance(payload, dict) and payload.get("number") else None


# --- emit --------------------------------------------------------------------


def post_status(slug: str, head_sha: str, verdict: Verdict, target_url: str | None = None) -> bool:
    args = [
        "api",
        "-X",
        "POST",
        f"repos/{slug}/statuses/{head_sha}",
        "-f",
        f"state={verdict.state}",
        "-f",
        f"context={STATUS_CONTEXT}",
        "-f",
        f"description={verdict.description[:DESCRIPTION_LIMIT]}",
    ]
    if target_url:
        args += ["-f", f"target_url={target_url}"]
    result = _gh(*args)
    if result.returncode != 0:
        print(f"[pr-feedback] could not post the status: {result.stderr.strip()}", file=sys.stderr)
        return False
    return True


def sticky_body(snapshot: Snapshot, verdict: Verdict) -> str:
    """The comment a human reads when the check goes red — what, and how to clear it."""
    lines = [STICKY_MARKER]
    if verdict.state == "pending":
        # Pending holds the merge just as firmly as a failure. Calling it clear is
        # the one thing this comment must never say.
        lines += [f"**Review feedback: waiting.** {verdict.description}"]
        return "\n".join(lines)
    if verdict.state == "success" and verdict.items:
        # Green, but not clean, and the comment must not blur the two. These
        # findings were read by nobody and fixed by nobody; the merge stopped
        # waiting for them because the review ran out of rounds, which is a
        # decision about the loop and not a judgement about the code.
        noun = "finding" if len(verdict.items) == 1 else "findings"
        lines += [
            f"**Review capped at {MAX_REVIEW_ROUNDS} rounds — {len(verdict.items)} {noun} "
            f"recorded, not fixed.** This PR can merge.",
            "",
            *[f"- {item.detail}" for item in verdict.items],
            "",
            "An adversarial review of a large diff finds something every time, so the loop is "
            f"bounded rather than run to zero. What is above was left undone deliberately — it is "
            f"worth reading before merging, and worth filing if it matters. The `{CAPPED_LABEL}` "
            "label marks this PR so it can be found again.",
        ]
        return "\n".join(lines)
    if verdict.state != "failure":
        lines += [f"**Review feedback: clear.** {verdict.description}"]
        return "\n".join(lines)
    single = len(verdict.items) == 1
    noun = "item" if single else "items"
    tail = "it is answered" if single else "they are answered"
    lines += [
        f"**{len(verdict.items)} unanswered review {noun}** — this PR is blocked until {tail}.",
        "",
    ]
    lines += [f"- {item.detail}" for item in verdict.items]
    # The advice has to match the PR. On an unattended PR an `<!-- addressed: -->`
    # reply from the PR's own author is discarded, and on the cowork lane that
    # author *is* the maintainer — so a human who reads this comment, disagrees
    # with a finding and replies exactly as instructed would get silence and a
    # re-rendered red check, with nothing anywhere saying why. Telling somebody to
    # do the one thing that cannot work is worse than telling them nothing.
    if is_unattended(snapshot):
        lines += [
            "",
            "Run `/pr-feedback " + str(snapshot.number) + "` to work through them, or by hand: fix and "
            "push — the next review pass reports `open=0` and this clears itself. Hit "
            "**Resolve conversation** on any thread you have answered.",
            "",
            "**This PR is machine-authored, so a reply cannot clear a finding here.** An "
            "`<!-- addressed: … -->` marker written by this PR's own author is ignored: the account "
            "that wrote the change would otherwise be answering the review of it. Fix the finding, or "
            f"apply the `{OVERRIDE_LABEL}` label — a human's call, recorded here.",
        ]
        return "\n".join(lines)
    lines += [
        "",
        "Run `/pr-feedback " + str(snapshot.number) + "` to work through them, or by hand: fix and push "
        "(the next review pass reports `open=0` and this clears itself), or reply with "
        "`<!-- addressed: <producer> -->` and a reason, or hit **Resolve conversation** on a thread you "
        "have answered.",
        "",
        f"_Stuck? The `{OVERRIDE_LABEL}` label clears this check and says so here._",
    ]
    return "\n".join(lines)


def upsert_sticky(slug: str, number: int, snapshot: Snapshot, verdict: Verdict) -> None:
    """One comment, edited in place. Never created just to say everything is fine."""
    existing = None
    for comment in snapshot.comments:
        # `kind` matters: a review body and an issue comment have separate id
        # spaces, so PATCHing the issue-comments endpoint with a review id would
        # edit an unrelated comment.
        if comment.kind == "comment" and STICKY_MARKER in comment.body:
            existing = comment.id
    body = sticky_body(snapshot, verdict)
    if existing is not None:
        _gh("api", "-X", "PATCH", f"repos/{slug}/issues/comments/{existing}", "-f", f"body={body}")
    elif verdict.state == "failure":
        _gh("api", "-X", "POST", f"repos/{slug}/issues/{number}/comments", "-f", f"body={body}")


def apply_capped_label(slug: str, number: int, snapshot: Snapshot, verdict: Verdict) -> None:
    """Mark a PR that merged past unfixed findings, so it can be found again.

    Only added, never removed: the label records that a cap was hit on this PR,
    and a later push that happens to produce a clean review does not undo the
    fact. Idempotent — GitHub ignores adding a label that is already present, and
    the guard below keeps it to one call.

    Applying a label that does not exist silently does nothing, so
    ``review-capped`` is in ``expected_labels()`` and `make cowork-setup` creates
    it. A missing label costs the record, not the merge.
    """
    capped = verdict.state == "success" and bool(verdict.items)
    if not capped or CAPPED_LABEL in snapshot.labels:
        return
    _gh("api", "-X", "POST", f"repos/{slug}/issues/{number}/labels", "-f", f"labels[]={CAPPED_LABEL}")


def render_report(snapshot: Snapshot, verdict: Verdict) -> str:
    icon = {"success": "OK", "pending": "..", "failure": "XX"}[verdict.state]
    lines = [f"[{icon}] PR #{snapshot.number} @ {snapshot.head_sha[:7]} — {verdict.description}"]
    for item in verdict.items:
        lines.append(f"       · {item.detail}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report unanswered review feedback on a pull request.")
    parser.add_argument("--pr", type=int, help="PR number (defaults to the current branch's PR)")
    parser.add_argument("--json", action="store_true", help="machine-readable verdict for /pr-feedback")
    parser.add_argument("--status", action="store_true", help="post the commit status + sticky comment (CI)")
    parser.add_argument("--target-url", help="with --status, the run URL the check links to")
    args = parser.parse_args(argv)

    if shutil.which("gh") is None:
        print("[pr-feedback] `gh` is not on PATH — install it: brew install gh", file=sys.stderr)
        return 2
    slug = repo_slug()
    if slug is None:
        print("[pr-feedback] could not resolve the repo — is this a GitHub checkout?", file=sys.stderr)
        return 2
    number = args.pr or current_pr()
    if number is None:
        print("[pr-feedback] no PR given and none open for this branch — pass --pr", file=sys.stderr)
        return 2

    snapshot = fetch_snapshot(number, slug)
    if snapshot is None:
        if args.status:
            # A *required* status that was never posted shows as "Expected —
            # waiting for status", which looks exactly like this workflow not
            # existing. Say what actually happened, on whatever SHA is reachable.
            meta = _gh_json("pr", "view", str(number), "--json", "headRefOid")
            head = meta.get("headRefOid") if isinstance(meta, dict) else None
            if head:
                post_status(slug, head, Verdict("pending", "could not read the PR — see the run log"), args.target_url)
        return 2
    verdict = classify(snapshot, datetime.now(UTC))

    if args.status:
        # The step succeeds whatever the verdict — the *status* is what blocks a
        # merge. A red step here would look like a broken workflow instead of an
        # unanswered review, and would be the first thing anyone disabled.
        ok = post_status(slug, snapshot.head_sha, verdict, args.target_url)
        upsert_sticky(slug, number, snapshot, verdict)
        apply_capped_label(slug, number, snapshot, verdict)
        print(render_report(snapshot, verdict))
        return 0 if ok else 2

    if args.json:
        print(json.dumps({"number": snapshot.number, "head_sha": snapshot.head_sha, **verdict.as_dict()}, indent=2))
    else:
        print(render_report(snapshot, verdict))
    return 1 if verdict.state == "failure" else 0


if __name__ == "__main__":
    raise SystemExit(main())
