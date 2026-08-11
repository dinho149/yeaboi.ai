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
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

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


def is_promotion(issue: int, *, runner: Callable[[list[str]], str | None] | None = None) -> bool:
    """Whether ``issue`` carries the `release:promotion` label the ask routine applies.

    Read from GitHub rather than inferred from the reply text, because the text is
    attacker-influenceable and the label is not: `cron/release-promote-ask.md`
    applies it at creation time, and applying a label needs repo write.

    Fails **closed** — an unreachable API, a malformed response, or an issue that
    simply does not carry it all return False, which downgrades the verb to an
    ordinary approval. That is the safe direction: a promotion not carried out is
    re-asked next Monday, while a release cut by mistake cannot be unpublished.
    """
    run = runner or _gh_labels
    payload = run(["gh", "issue", "view", str(issue), "--json", "labels"])
    if not payload:
        return False
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return False
    labels = data.get("labels") if isinstance(data, dict) else None
    if not isinstance(labels, list):
        return False
    return any(isinstance(entry, dict) and entry.get("name") == PROMOTION_LABEL for entry in labels)


def _gh_labels(argv: list[str]) -> str | None:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)  # noqa: S603 - literal argv
    return result.stdout if result.returncode == 0 else None


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
    if verb == "promote":
        return ["gh", "issue", "edit", str(issue), "--add-label", PROMOTE_LABEL]
    if verb == "reject":
        return ["gh", "issue", "close", str(issue)]
    raise RelayError(f"no command for verb {verb!r}")


def build_plan(
    replies: list[dict[str, Any]],
    allowlist: dict[str, str],
    *,
    promotion_check: Callable[[int], bool] | None = None,
) -> dict[str, Any]:
    """Turn a thread into the list of actions still owed, oldest first.

    Every skip below is one of the ways the 2026-08-09 run went wrong, made
    total rather than remembered.
    """
    if not allowlist:
        # Matches the routine's own stop condition: an empty or placeholder
        # allowlist means nobody can authorise anything, so nothing is actionable.
        empty = {"replies": len(replies), "item_replies": 0, "marked": 0, "ignored_markers": 0, "actionable": 0}
        return {"counts": empty, "plan": []}

    # Injection seam: the real check calls `gh`, and every other decision here is
    # pure. Tests pass a stub; nothing else should.
    is_promotion_issue = promotion_check or is_promotion

    plan: list[dict[str, Any]] = []
    item_replies = marked = ignored_markers = 0

    for reply in replies:
        text = reply.get("text") or ""
        match = ITEM_RE.match(text)
        if not match:
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
            plan.append({"ts": reply.get("ts"), "issue": issue, "verb": "ask", "who": None, "command": None})
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
        verb = (
            ("promote" if (PROMOTE_RE.match(text) and is_promotion_issue(issue)) else "approve")
            if approvers
            else "reject"
        )
        who = allowlist[(approvers or rejecters)[0]]
        plan.append({"ts": reply.get("ts"), "issue": issue, "verb": verb, "who": who, "command": _command(verb, issue)})

    plan.sort(key=lambda item: str(item["ts"]))
    counts = {
        "replies": len(replies),
        "item_replies": item_replies,
        "marked": marked,
        # Never silent: a marker from outside the allowlist is disregarded, and
        # saying so is what stops it from looking like a handled item.
        "ignored_markers": ignored_markers,
        "actionable": len(plan),
    }
    return {"counts": counts, "plan": plan}


def load_replies(raw: str) -> list[dict[str, Any]]:
    """Accept a bare array or Slack's ``{"messages": [...]}`` envelope."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RelayError(f"stdin is not JSON: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("messages", [])
    if not isinstance(data, list):
        raise RelayError("expected a JSON array of thread replies")
    return [reply for reply in data if isinstance(reply, dict)]


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
