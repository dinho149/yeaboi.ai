#!/usr/bin/env python3
"""Decide what a Slack digest thread is asking for, so the relay does not have to.

``cowork/routines/cron/slack-relay.md`` carries a human's ✅/❌ from the digest
thread onto GitHub. Deciding *which* reactions are still unhandled used to be the
routine's own judgement, made at the ``fast`` tier against a fifteen-reply thread,
once an hour. On 2026-08-09 it announced the same approval of issue #172 three
times — at 13:13, 17:12 and 19:12 BST — and left a duplicate audit comment on the
issue, because three things line up against it:

* the relay posts through the Slack connector **as the human**, so its own ack
  replies come back on the next read indistinguishable from human input;
* the early-exit rule was ill-typed — a *reaction* cannot carry the 🤖 marker,
  only the message under it can, so the mapping had to be invented every run;
* it re-read a thread that grew by one self-referential ack each time it misfired.

None of that is a judgement call. It is a filter, a set membership test and a
regex, and this module is where they live — the same split the fleet lifecycle
already uses, where ``cowork_setup.py --triggers`` diffs the routines and the
model posts what it is handed. The routine reads Slack and writes Slack; every
comparison between the two happens here.

**The queue is still GitHub.** This module holds no state and remembers nothing
between runs: the 🤖 marker on a reply is the record, and the emitted command is
checked against live GitHub state by the routine before it runs.

One bound worth knowing: Slack truncates a reaction's ``users`` list on heavily
reacted messages, and a truncated list is indistinguishable here from nobody
having reacted. ``slack_get_reactions`` returns up to 50 per emoji, and a digest
item reply carries two, so this is a long way from mattering — but if it ever
does, it fails toward re-processing an approval rather than toward dropping one.

Input is a JSON array of thread replies on stdin, each ``{ts, text, reactions}``
with ``reactions`` a list of ``{name, users}`` — the shape Slack's own
``conversations.replies`` returns, so a raw API response works unchanged::

    $ uv run python scripts/cowork_relay.py --plan < thread.json
    {"counts": {...}, "plan": [{"ts": "...", "issue": 172, "verb": "approve", ...}]}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# scripts/ is not a package, so the sibling transport is imported by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import _gh_transport as transport  # noqa: E402 - after the sys.path line that makes it importable

ROOT = Path(__file__).resolve().parent.parent
RELAY_ROUTINE = ROOT / "cowork" / "routines" / "cron" / "slack-relay.md"

# The digest's thread-reply contract, from `cowork/routines/cron/digest.md`:
# "#<issue-number> — <verbatim title> — <issue link>, the number leading so
# cron/slack-relay.md can parse it". This regex is the whole reason the relay
# cannot re-parse its own output: an ack reads "added `claude-implement` to #172",
# where the number is not leading, so it never matches and is never an input.
ITEM_RE = re.compile(r"^#(\d+)\s+—\s")

# A member id as the allowlist table writes it. Anything else in that table --
# a name, a placeholder, an example -- is not an id and cannot authorise a verb.
MEMBER_RE = re.compile(r"`(U[A-Z0-9]{6,})`")

DONE = "robot_face"
APPROVE = "white_check_mark"
REJECT = "x"

# What each verb did, in the past tense a human reads. Spelled out because
# deriving it was wrong the moment a verb was not a regular English one:
# `campaign` + "d" is "campaignd". Keys are exactly the verbs `_command` answers
# to, minus the two `_audit` returns ``None`` for.
AUDIT_VERB = {"approve": "approved", "promote": "promoted", "campaign": "approved as a campaign"}

# Why a fleet item ended without shipping. A closed vocabulary, because the whole
# point is to count these: `scripts/cowork_metrics.py` reads the markers below out
# of issue comments, and a free-text reason is one nobody can aggregate. The fleet
# already knew every one of these facts and wrote none of them down — a ❌ was a
# bare `gh issue close`, and the human's reason stayed in Slack.
#
# `slack-veto` is the only one this script emits; the rest are written by the
# sweeps and the digest, and live here because this is the module the reader
# imports. `cowork/sweep-procedure.md` and `cron/digest.md` spell the same set,
# and `tests/unit/test_cowork_relay.py` asserts the docs and this tuple agree —
# the doc is the source, because it is what a model actually reads.
REJECTION_REASONS = (
    "slack-veto",  # a human reacted ❌ in the channel
    "aged-out",  # digest.md step 4 closed a proposal that had already lapsed for 30 days
    "no-longer-reproduces",  # the evidence stopped being true before anyone built it
)

# Why a question stopped being asked without anybody answering it. The third
# family, and the one the first two were quietly absorbing.
#
# `cron/digest.md` step 4 used to *close* a proposal at fourteen days. But both
# dedupe passes — `sweep-procedure.md` step 4 and `cowork-scout.md` step 5 —
# read a closed issue as `A closing is a rejection and a rejection is durable.
# Do not re-file rejected ideas.` They read **state**, not comments; the
# `aged-out` marker that was supposed to tell the two apart is written into a
# comment, and `scripts/cowork_metrics.py` is the only reader of those and runs
# on a human's terminal. So a find nobody had time to look at was suppressed
# permanently, by a timer, and the one record of the difference sat where no
# routine could see it.
#
# A lapse removes `cowork:proposal` and leaves the issue **open**. The slot
# reopens with no arithmetic changing — `open_proposals` filters by label — and
# because nothing closed, no dedupe pass reads a rejection nobody made.
LAPSE_REASONS = ("unanswered",)  # digest.md step 4 — fourteen days with no human verb

# Why a queued item went back to being a question. These mirror the auto-lane
# conditions in `cowork/house-rules.md` rather than inventing a second taxonomy:
# the axes a find fails on are exactly the conditions it had to clear.
BOUNCE_REASONS = (
    "no-repro",  # a `bug` with no regression test that fails before and passes after
    "user-facing-wording",  # behaviour may change, copy may not
    "outside-owns",  # the paths fall outside the charter
    "public-api",  # an API, schema or state-field change
    "needs-judgement",  # arguing with yourself about it means it proposes
)

APPROVAL_LABEL = "claude-implement"
PROMOTE_LABEL = "release:promote"
# Applied only by `cron/release-promote-ask.md`, at creation time, as the
# maintainer. `publish.yml` requires it too — this is the same fact checked on
# both sides of the hand-off.
PROMOTION_LABEL = "release:promotion"

# A promotion ask is the only thread reply in this channel that is not a proposal,
# and it is told apart by the same leading-`#<number>` contract the digest uses,
# plus a fixed second field: `cron/release-promote-ask.md` writes
# `#231 — promote 3.6.1 — <link>` and nothing else may.
#
# This text is still DATA. The digest quotes issue titles verbatim, and on a
# public repo anyone can file an issue titled to look like a promotion ask — so
# at worst a crafted title routes an allowlisted ✅ to `--add-label
# release:promote` on the wrong issue. `publish.yml` refuses any issue that does
# not ALSO carry `release:promotion`, which only the ask routine applies and
# which needs repo write. The regex picks a label; the workflow decides whether
# it means anything.
PROMOTE_RE = re.compile(r"^#(\d+)\s+—\s+promote\s+\d+\.\d+\.\d+\b")

# The integration shortlist is the second thread reply that is not a proposal, and
# it is told apart the same way: the leading-`#<number>` contract plus a fixed
# second field. `cron/integrations-campaign.md` files the issues,
# `cron/digest.md` renders `#241 — integration candidate: gitlab — <link>`.
#
# It exists because reusing `claude-implement` here would be actively destructive
# rather than merely wrong. `claude.yml` fires its 110-turn `implement` job on any
# issue *receiving* that label, so a ✅ meaning "build GitLab this week" would
# instead launch one unattended agent against an issue describing a whole week of
# work across six workstreams' files — which either grab-bags it into one PR or
# stops at the paths rule, and either way spends the approval with nothing to show
# and no second chance until next Monday.
#
# Same defence as the promotion pair: the text is DATA — anyone can file an issue
# titled to match — so the regex only *routes*, and `is_campaign_candidate`
# confirms against the `integration:candidate` label, which needs repo write.
CANDIDATE_RE = re.compile(r"^#(\d+)\s+—\s+integration candidate\b")

# The disclosure post from `cron/security-sweep.md`. It is the one actionable
# message in this channel that is NOT a thread reply and does NOT name a GitHub
# issue: a disclosure-class find is never filed publicly, so the only artefact is
# a private Linear ticket, and before this lane existed a ✅ on it resolved to
# nothing. The find left the fleet at that post and only a human could ever pick
# it up again.
#
# Anchored on the title line's fixed opening rather than on `YEA-\d+` anywhere in
# the text, for the same reason `ITEM_RE` demands a *leading* `#`: this routine's
# own ack says "applied `security:approved` to YEA-94", and a regex that matched a
# bare identifier would read its own output back as a new input every hour.
# Slack returns the emoji as a shortcode and `**bold**` as `*bold*`, so both
# spellings are accepted — the phrase is what is strict.
DISCLOSURE_RE = re.compile(
    r"^(?::closed_lock_with_key:|\U0001f510)\s*\*{0,2}Security\*{0,2}\s*[—-]\s*disclosure filed\b"
)

# The Linear ticket a disclosure post names. Read only out of a message that
# already matched `DISCLOSURE_RE`.
TICKET_RE = re.compile(r"\b(YEA-\d+)\b")

# Stamped by `load_replies` on everything that came from the caller's
# `channel_messages` key, and read by `build_plan` to decide which lanes a message
# may reach. Not a Slack field and never one: `slack_read_channel` and
# `slack_read_thread` return the same message shape, so the boundary has to be
# carried by which key the caller put it in, and stamping it here means exactly
# one place decides. Underscore-led so a future Slack field cannot collide.
CHANNEL_LEVEL = "_channel_level"

# What a ✅ on a disclosure applies, in Linear rather than GitHub. Deliberately
# not `claude-implement`: that label lives on GitHub issues and a disclosure has
# none by construction — filing one is the whole thing the carve-out forbids.
# `cron/security-sweep.md` reads this label on its own next run and drains it.
#
# Same defence as the promotion and candidate pairs, and it matters more here
# because the Slack connector posts as the allowlisted human, so authorship
# cannot tell a real disclosure post from a crafted one. The text is DATA: this
# regex only *routes*, and the routine must confirm the ticket carries
# `workstream:security` — which only the sweep applies — before the label goes on.
SECURITY_APPROVED_LABEL = "security:approved"
SECURITY_WORKSTREAM_LABEL = "workstream:security"

# Applied only by `cron/integrations-campaign.md`, at creation time.
CANDIDATE_LABEL = "integration:candidate"
# What a ✅ on a candidate applies. Deliberately not `claude-implement`: the
# campaign routine reads this label on its own next run and picks the angle up
# itself, so nothing else has to fire.
APPROVED_LABEL = "integration:approved"


class RelayError(RuntimeError):
    """The input is not something a plan can be built from."""


def _is_placeholder(member: str, who: str) -> bool:
    """A row nobody has filled in yet — `UXXXXXXXX`, or a description in angle brackets."""
    return set(member[1:]) <= {"X", "0"} or who.startswith("<") or "placeholder" in who.lower()


def parse_allowlist(text: str) -> dict[str, str]:
    """Pull the authorised member ids out of the relay routine's own table.

    The allowlist lives in the routine file rather than here so that it stays
    versioned in the place a reviewer already looks when asking "who can approve",
    and so that adding a person is a reviewed diff rather than a config change.
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        match = MEMBER_RE.search(cells[0])
        if not match:
            continue
        member, who = match.group(1), cells[1]
        if _is_placeholder(member, who):
            # The routine's stop condition is "any row is a placeholder OR the
            # table is empty — exit without acting". A half-filled table is the
            # more dangerous of the two: it looks configured.
            return {}
        found[member] = who
    return found


def _reactors(reaction: dict[str, Any]) -> list[str]:
    users = reaction.get("users") or []
    return [u for u in users if isinstance(u, str)]


def _by_name(reply: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for reaction in reply.get("reactions") or []:
        name = reaction.get("name")
        if isinstance(name, str):
            out.setdefault(name, []).extend(_reactors(reaction))
    return out


def is_promotion(issue: int, *, runner: Callable[[list[str]], str | None] | None = None) -> bool | None:
    """Whether ``issue`` carries the `release:promotion` label the ask routine applies.

    Read from GitHub rather than inferred from the reply text, because the text is
    attacker-influenceable and the label is not: `cron/release-promote-ask.md`
    applies it at creation time, and applying a label needs repo write.

    **Tristate, and the third state is the point.** True and False are answers;
    ``None`` means the question could not be asked — an unreachable API, a rate
    limit, a malformed response. Collapsing ``None`` into False looks like failing
    closed and is not: the fallback verb is ``approve``, which applies
    `claude-implement`, and `claude.yml`'s implement job fires on *any* issue
    receiving that label. A single `gh` blip would therefore turn the maintainer's
    ✅ on the release ask into an unattended `deep`-tier agent building
    "Promote 3.7.0?" as though it were a feature request — the release not
    happening, nobody told, and something else happening instead.

    ``None`` routes to `ask` in `build_plan`, the verb that already exists for "do
    not guess". Same reasoning as `merge_gate_armed` in `scripts/cowork_setup.py`:
    an unanswerable question is not a no.

    **A CLOSED promotion ask is also ``None``**, and it is the reason this reads
    state at all. `cron/release-promote-ask.md` supersedes an unanswered ask by
    closing it and opening a fresh one, which dedups GitHub — but the Slack half
    does not: last week's thread reply is still in this relay's read window, still
    unmarked, and still actionable. `publish.yml`'s guard fires on the `labeled`
    event and never looks at issue state, so a ✅ on the stale reply would promote
    against a manifest nobody read. The ask routine's own "never leave two open"
    rule cannot defend that, because the artifact lives in Slack. Refusing here is
    where it has to be refused, and `ask` rather than `reject` because the human
    probably does want to promote — just on this week's issue.
    """
    return _has_open_label(issue, PROMOTION_LABEL, runner)


def _has_open_label(issue: int, label: str, runner: Callable[[list[str]], str | None] | None) -> bool | None:
    """Whether ``issue`` is open and carries ``label``. Tristate, like its callers.

    The shared body behind `is_promotion` and `is_campaign_candidate`: both ask the
    same question of a different label, and both need ``None`` to mean "could not
    ask" rather than "no". See `is_promotion` for why that distinction is the whole
    point rather than defensive coding.
    """
    run = runner or _gh_labels
    payload = run(["gh", "issue", "view", str(issue), "--json", "labels,state"])
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    labels = data.get("labels") if isinstance(data, dict) else None
    if not isinstance(labels, list):
        return None
    if not any(isinstance(entry, dict) and entry.get("name") == label for entry in labels):
        return False
    # A payload with no `state` at all predates this check rather than describing a
    # closed issue; absence is not evidence, so it reads as open.
    return None if str(data.get("state", "OPEN")).upper() == "CLOSED" else True


def is_approved(issue: int, *, runner: Callable[[list[str]], str | None] | None = None) -> bool | None:
    """Whether ``issue`` is already carrying `claude-implement`.

    Tristate like its siblings, but read the other way round by its caller: a
    ``None`` here means "could not tell", and the caller keeps the plain `approve`
    verb rather than routing to `ask`. Applying a label that is already present is
    a harmless no-op; refusing to act would strand the ordinary first approval
    behind a `gh` call the routine sessions' egress proxy is known to refuse. The
    asymmetry with `is_promotion` is deliberate — there, guessing wrong starts an
    implementation run against a release ask, and here it does nothing at all.
    """
    return _has_open_label(issue, APPROVAL_LABEL, runner)


def is_campaign_candidate(issue: int, *, runner: Callable[[list[str]], str | None] | None = None) -> bool | None:
    """Whether ``issue`` carries the `integration:candidate` label the campaign applies.

    Tristate for the same reason `is_promotion` is, and with the same fallback:
    ``None`` routes to `ask`. The failure it refuses is the mirror image of the
    promotion one — there, an unanswerable question would turn a release ask into
    an implementation run; here, it would turn "build GitLab" into
    `claude-implement` on an issue that is a week-long plan, which is the exact
    outcome the separate label exists to prevent. Guessing in either direction
    spends a human's ✅ on something they did not ask for.

    **A CLOSED candidate is also ``None``.** Monday supersedes an unanswered
    shortlist by closing it and filing a fresh one, which dedups GitHub but not
    Slack: last week's three thread replies are still in the read window and still
    unmarked. A late ✅ on a superseded pick would approve a campaign against a
    shortlist nobody re-read. `ask` rather than `reject`, because the human
    probably does want a campaign — just this week's one.
    """
    return _has_open_label(issue, CANDIDATE_LABEL, runner)


def _gh_labels(argv: list[str]) -> str | None:
    """Read one issue's labels, through whichever transport this machine has.

    The argv is still the input, and still literal, because the caller's whole
    point is that a command is data here rather than a format string. What
    changed is that a routine session has no `gh` to run it with: every relay run
    there answered ``None`` to "is this the release ask?", which routes to `ask`
    — safe, and silently useless, since the maintainer's ✅ then did nothing at
    all.

    The REST half reads the same issue and reshapes the answer into the
    ``{"labels": [{"name": …}]}`` the caller already parses, rather than teaching
    the caller a second shape.
    """
    if transport.gh_available():
        result = transport._run(argv, capture_output=True, text=True, check=False)  # noqa: S603 - literal argv
        return result.stdout if result.returncode == 0 else None
    slug = transport.resolve_slug(ROOT)
    if not slug:
        return None
    # argv is ["gh", "issue", "view", "<n>", "--json", "labels,state"] — the number
    # is the only part that varies, and it is read rather than reassembled so a
    # future verb cannot quietly change which issue is asked about.
    number = next((part for part in argv if part.isdigit()), None)
    if number is None:
        return None
    answer = transport.api("GET", f"/repos/{slug}/issues/{number}")
    if not answer.ok or not isinstance(answer.data, dict):
        return None
    labels = answer.data.get("labels") or []
    names = [entry.get("name") for entry in labels if isinstance(entry, dict)]
    # REST spells state lowercase, `gh --json` spells it uppercase; the caller
    # upper-cases either way rather than learning both.
    return json.dumps({"labels": [{"name": name} for name in names if name], "state": answer.data.get("state", "open")})


def _command(verb: str, issue: int) -> list[str]:
    """The literal argv for a verb — never a format string, never an API call.

    `gh issue edit --add-label` adds; `gh api -X PUT .../labels` replaces. The
    relay's own file has always specified the first, and on 2026-08-09 something
    ran the second: issue #172 lost `cowork:proposal`, `workstream:web-ux` and
    `type:security` in the same second it gained `claude-implement`. The lost
    workstream label is the one that matters — it is what `claude.yml`'s implement
    job reads to find the charter declaring which paths an unattended run may
    touch. Emitting argv rather than a string is what makes that unreachable from
    here; `tests/unit/test_cowork_relay.py` asserts no emitted command can spell
    it.
    """
    if verb == "approve":
        return ["gh", "issue", "edit", str(issue), "--add-label", APPROVAL_LABEL]
    if verb == "refire":
        # A repeat ✅ on an issue that is already approved. `--add-label` on a label
        # that is already there is a *silent no-op*, and `claude.yml`'s implement
        # job fires on the `labeled` event — so #172 was approved five times
        # between 2026-08-09 and 08-11 and built once, with nothing anywhere
        # reporting that the other four did nothing at all.
        #
        # A comment rather than a remove-then-add: that pair is lossy, and a crash
        # between its two writes leaves the issue carrying no `claude-implement`,
        # which is the exact label `digest.md` queries to report an approval the
        # fleet never acted on. This marker is picked up by `claude.yml`, which
        # then re-reads the label live before doing anything — so the comment
        # requests, and never authorises.
        return [
            "gh",
            "issue",
            "comment",
            str(issue),
            "--body",
            "re-approved via Slack ✅ — already labelled, so re-firing the implement job.\n\n<!-- implement-retry -->",
        ]
    if verb == "promote":
        return ["gh", "issue", "edit", str(issue), "--add-label", PROMOTE_LABEL]
    if verb == "campaign":
        return ["gh", "issue", "edit", str(issue), "--add-label", APPROVED_LABEL]
    if verb == "reject":
        return ["gh", "issue", "close", str(issue)]
    raise RelayError(f"no command for verb {verb!r}")


def _audit(verb: str, issue: int, who: str, ts: str) -> list[str] | None:
    """The comment that records a Slack decision on the GitHub item it moved.

    This used to be the routine's job — `slack-relay.md` told it to leave
    ``approved via Slack ✅ by <who> — <permalink>`` and trusted it to do so once.
    #172 carries two identical ones, because "leave a comment" is a sentence and
    not an argv. Emitting it here makes it diffable and makes it exactly once, the
    same reason `_command` emits argv instead of a format string.

    The trailing marker is the part that is actually load-bearing. A ❌ was a bare
    ``gh issue close``: the fact survived in GitHub and the *reason* stayed in
    Slack, so nothing could ever count why the fleet's proposals died. Now a
    rejection says so in a form `scripts/cowork_metrics.py` can read.

    ``None`` for the two verbs that need no comment: ``refire`` is already a
    comment, and ``ask`` has no command to audit. Callers treat that as "nothing
    to post" rather than as a failure.
    """
    if verb in ("refire", "ask"):
        return None
    if verb == "reject":
        body = (
            f"closed via Slack ❌ by {who} — message `{ts}` in the channel.\n\n"
            f"<!-- rejected: reason=slack-veto by={who} ts={ts} -->"
        )
    else:
        body = (
            f"{AUDIT_VERB[verb]} via Slack ✅ by {who} — message `{ts}` in the channel.\n\n"
            f"<!-- relayed: verb={verb} by={who} ts={ts} -->"
        )
    return ["gh", "issue", "comment", str(issue), "--body", body]


def _disclosure_action(reply: dict[str, Any], text: str, allowlist: dict[str, str]) -> dict[str, Any] | None:
    """A ✅/❌ on `cron/security-sweep.md`'s disclosure post → the action it owes.

    Returns ``None`` when the message is not a disclosure post at all, ``{}`` when
    it is one but owes nothing (already marked, or nobody has reacted), and the
    plan entry otherwise. Three states rather than two because the caller counts
    disclosure posts seen, and "seen and settled" must not read as "never posted".

    The entry carries ``ticket`` where every other entry carries ``issue``, and
    ``command`` is always ``None``: the target is a private Linear ticket and
    there is no Linear CLI to emit argv for. The routine makes that call through
    the connector, the same way it does for `RemoteTrigger` — Python still owns
    which ticket, which label, and whether anything is owed at all.
    """
    if not DISCLOSURE_RE.match(text):
        return None
    ticket = TICKET_RE.search(text)
    if not ticket:
        # A disclosure post with no ticket identifier is a malformed post, not an
        # instruction. Counted as seen so the run log shows it, acted on never.
        return {}

    reactions = _by_name(reply)
    if [u for u in reactions.get(DONE, []) if u in allowlist]:
        return {}
    approvers = [u for u in reactions.get(APPROVE, []) if u in allowlist]
    rejecters = [u for u in reactions.get(REJECT, []) if u in allowlist]
    if not approvers and not rejecters:
        return {}

    who = allowlist[(approvers or rejecters)[0]]
    if approvers and rejecters:
        return {"ts": reply.get("ts"), "ticket": ticket.group(1), "verb": "ask", "who": None, "command": None}
    verb = "disclosure-approve" if approvers else "disclosure-decline"
    return {"ts": reply.get("ts"), "ticket": ticket.group(1), "verb": verb, "who": who, "command": None}


def build_plan(
    replies: list[dict[str, Any]],
    allowlist: dict[str, str],
    *,
    promotion_check: Callable[[int], bool] | None = None,
    candidate_check: Callable[[int], bool] | None = None,
    approved_check: Callable[[int], bool] | None = None,
) -> dict[str, Any]:
    """Turn a thread into the list of actions still owed, oldest first.

    Every skip below is one of the ways the 2026-08-09 run went wrong, made
    total rather than remembered.
    """
    if not allowlist:
        # Matches the routine's own stop condition: an empty or placeholder
        # allowlist means nobody can authorise anything, so nothing is actionable.
        # Same keys as the real path. A counts dict whose shape depends on which
        # branch produced it is one a reader has to `.get()` defensively.
        empty = {
            "replies": len(replies),
            "item_replies": 0,
            "disclosure_posts": 0,
            "channel_ignored": 0,
            "marked": 0,
            "ignored_markers": 0,
            "actionable": 0,
        }
        return {"counts": empty, "plan": []}

    # Injection seam: the real check calls `gh`, and every other decision here is
    # pure. Tests pass a stub; nothing else should.
    is_promotion_issue = promotion_check or is_promotion
    is_candidate_issue = candidate_check or is_campaign_candidate
    is_approved_issue = approved_check or is_approved

    plan: list[dict[str, Any]] = []
    item_replies = marked = ignored_markers = disclosures = channel_ignored = 0

    for reply in replies:
        text = reply.get("text") or ""
        # A channel-level message may reach exactly one lane: the disclosure post.
        # `ITEM_RE` is never run on it, and that is a boundary rather than a
        # nicety. A plain item approval applies `claude-implement` on the strength
        # of the leading `#<number>` alone — the label confirmations guard the
        # *special* verbs, not that one — so the thing standing between a public
        # channel and an unattended implement job is that the text came from a
        # thread the fleet itself posted. Reading top-level messages for the
        # disclosure lane would hand that away: anyone in `#yeaboi-claude` could
        # post `#231 — <plausible title>` at the top level and wait for a ✅ meant
        # for a digest item. Slack cannot tell them apart for us — `slack_read_thread`
        # returns thread replies and `slack_read_channel` returns channel messages,
        # so the caller keeps them in separate keys and `load_replies` stamps the
        # boundary in. `cowork/README.md`'s "a reaction on a parent message
        # resolves to nothing" survives with one named exception instead of a hole.
        if reply.get(CHANNEL_LEVEL):
            entry = _disclosure_action(reply, text, allowlist)
            if entry is not None:
                disclosures += 1
                if entry:
                    plan.append(entry)
            else:
                channel_ignored += 1
            continue
        match = ITEM_RE.match(text)
        if not match:
            # A disclosure post that arrived as a thread reply anyway. Kept
            # reachable here because it costs nothing — a disclosure names no
            # GitHub issue, so it can never satisfy `ITEM_RE` — and because the
            # lane failing shut on a caller that mis-sorted one message would be
            # the dead end this whole path exists to close.
            entry = _disclosure_action(reply, text, allowlist)
            if entry is not None:
                disclosures += 1
                if entry:
                    plan.append(entry)
            continue  # not a digest item reply — the relay's own acks land here
        item_replies += 1

        reactions = _by_name(reply)
        # The marker is gated on the same allowlist as the verbs. It is written by
        # this routine through the Slack connector, which posts as the human — the
        # ✅ and the 🤖 on the #172 reply are both under `U0BLM1QU3JN` — so gating
        # costs nothing and closes a veto: ungated, any member of the channel could
        # mark an item and suppress it from every future run, and the count would
        # simply read as one more handled reply.
        markers = [u for u in reactions.get(DONE, []) if u in allowlist]
        if markers:
            marked += 1
            continue
        if DONE in reactions:
            ignored_markers += 1

        approvers = [u for u in reactions.get(APPROVE, []) if u in allowlist]
        rejecters = [u for u in reactions.get(REJECT, []) if u in allowlist]
        if not approvers and not rejecters:
            continue

        issue = int(match.group(1))
        if approvers and rejecters:
            # The routine's rule: never guess between two verbs from a human.
            plan.append(
                {"ts": reply.get("ts"), "issue": issue, "verb": "ask", "who": None, "command": None, "audit": None}
            )
            continue

        # ❌ on a promotion ask is still `reject`, i.e. `gh issue close` — "not
        # this week". Next Monday's run opens a fresh ask against the same batch.
        #
        # The text shape is a hint, never the decision. The digest quotes issue
        # titles verbatim and `feature-candidate` titles come from the in-app
        # feedback form, so a user can write one that matches `PROMOTE_RE` — and
        # the damage would not be a stray label but a *lost approval*: the ✅ the
        # maintainer meant as "build this" would apply `release:promote` and never
        # `claude-implement`, and nothing would say so. `is_promotion` confirms
        # against the `release:promotion` label, which only the ask routine
        # applies, so a matching title on an ordinary proposal stays an approval.
        #
        # ❌ on an integration candidate is `reject` too: closing it is exactly
        # "not this provider", and Monday's run reads closed candidates as the
        # standing record of what it must not re-propose.
        who = allowlist[(approvers or rejecters)[0]]
        special: tuple[str, Callable[[int], bool | None]] | None = None
        if PROMOTE_RE.match(text):
            special = ("promote", is_promotion_issue)
        elif CANDIDATE_RE.match(text):
            special = ("campaign", is_candidate_issue)

        if not approvers:
            verb = "reject"
        elif special is None:
            # Already approved? Then this ✅ is a re-fire request, not an approval.
            # `None` keeps the existing `approve` behaviour rather than routing to
            # `ask`: applying a label that is already there is harmless, whereas
            # refusing to act on an unreadable answer would strand the ordinary
            # first-approval path behind a `gh` call that routine sessions' egress
            # proxy is known to refuse.
            verb = "refire" if is_approved_issue(issue) else "approve"
        else:
            label_verb, confirm = special
            confirmed = confirm(issue)
            if confirmed is None:
                # Could not tell, or the issue is closed — neither is "not a
                # promotion" and neither is "not a candidate". `approve` applies
                # `claude-implement`, which starts an implementation run against
                # the release ask, or against a week-long campaign plan. Ask a
                # human; that is the verb.
                plan.append(
                    {"ts": reply.get("ts"), "issue": issue, "verb": "ask", "who": who, "command": None, "audit": None}
                )
                continue
            verb = label_verb if confirmed else "approve"
        ts = str(reply.get("ts") or "")
        plan.append(
            {
                "ts": reply.get("ts"),
                "issue": issue,
                "verb": verb,
                "who": who,
                "command": _command(verb, issue),
                "audit": _audit(verb, issue, who, ts),
            }
        )

    plan.sort(key=lambda item: str(item["ts"]))
    counts = {
        "replies": len(replies),
        "item_replies": item_replies,
        "disclosure_posts": disclosures,
        # The channel-level messages that were not disclosure posts. Reported
        # rather than dropped quietly: this is the count that says the widened
        # input is still being read narrowly, and a caller that started sorting
        # thread replies into the channel key would show up here as a number
        # climbing while `item_replies` fell.
        "channel_ignored": channel_ignored,
        "marked": marked,
        # Never silent: a marker from outside the allowlist is disregarded, and
        # saying so is what stops it from looking like a handled item.
        "ignored_markers": ignored_markers,
        "actionable": len(plan),
    }
    return {"counts": counts, "plan": plan}


def load_replies(raw: str) -> list[dict[str, Any]]:
    """Accept a bare array, Slack's ``{"messages": [...]}`` envelope, or both keys.

    The third form is how the disclosure lane gets its input without widening the
    digest lane::

        {"messages": [...thread replies...], "channel_messages": [...top level...]}

    Everything under ``channel_messages`` is stamped ``CHANNEL_LEVEL``, and
    `build_plan` will only ever run the disclosure matcher over those. Sorting is
    the caller's job because only the caller knows which Slack call returned what;
    stamping is this function's, so no other code has to remember to.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RelayError(f"stdin is not JSON: {exc}") from exc
    channel: list[Any] = []
    if isinstance(data, dict):
        channel = data.get("channel_messages") or []
        data = data.get("messages", [])
        if not isinstance(channel, list):
            raise RelayError("`channel_messages` must be a JSON array of channel-level messages")
    if not isinstance(data, list):
        raise RelayError("expected a JSON array of thread replies, or an object with `messages`")
    replies = [reply for reply in data if isinstance(reply, dict)]
    replies += [{**message, CHANNEL_LEVEL: True} for message in channel if isinstance(message, dict)]
    return replies


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", action="store_true", help="emit the actions still owed, as JSON")
    parser.add_argument("--allowlist-from", type=Path, default=RELAY_ROUTINE, help="routine file holding the table")
    args = parser.parse_args(argv)

    if not args.plan:
        parser.error("nothing to do — pass --plan")

    try:
        allowlist = parse_allowlist(args.allowlist_from.read_text())
        result = build_plan(load_replies(sys.stdin.read()), allowlist)
    except (RelayError, OSError) as exc:
        print(f"cowork_relay: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
