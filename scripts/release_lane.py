#!/usr/bin/env python3
"""Which lane a merge to `main` came from — the fleet's, or a human's.

`publish.yml` asks this once per push and releases on the answer: a human's merge
cuts the official `X.Y.Z` on the spot, an unattended one publishes `X.Y.ZrcN`
and waits for a human merge to ship it.

**The predicate is not defined here.** `scripts/pr_feedback.py` owns it, and it is
the same one `claude-review.yml` and the pr-feedback gate use to decide whether a
PR gets an unattended review. Re-spelling it would put a second copy in a second
language, and the direction that drift runs is the dangerous one: a prefix added
to the Python and not to the copy turns a fleet merge into an official release
with nothing to notice.

This lives in a file rather than a `python3 -c` inside the workflow because a
`run: |` block scalar ends at the first column-1 line, and top-level Python
statements cannot be indented — the two cannot share a step. `publish.yml`
already carries a comment about that failure mode; this is the same one.

Reads the associated PRs as JSON on stdin and prints ``fleet`` or ``human``.
Accepts a list of ``{"labels": [...], "head": "branch"}`` (what the commits-to-PRs
endpoint returns), a bare object, or ``null`` for "no PR".

**A list is classified `fleet` if ANY of its entries is.** That endpoint can return
more than one PR for a commit — an open PR that rebased onto `main`, a cherry-pick
— and the array order is not documented, so picking one is picking arbitrarily.
Of the two ways that goes wrong, reading a human merge as fleet only makes it wait,
while reading a fleet merge as human publishes an official release that cannot be
unpublished.

Note the shape the caller must send: ``--jq '.[0] | …'`` is NOT it. jq cannot
iterate the null an empty array yields, so that form exits 5 and turns "no PR"
into "failed lookup" — `publish.yml` sends
``if length == 0 then null else [.[] | …] end`` for exactly that reason.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pr_feedback as prf  # noqa: E402 - after the sys.path line that makes it importable

FLEET = "fleet"
HUMAN = "human"


def classify(pr: dict | None) -> str:
    """``fleet`` when the PR is unattended work, ``human`` otherwise.

    ``None`` — no PR behind the commit — is a human. The default branch enforces
    `pull_request`, so reaching this means somebody with repo write pushed
    straight to `main`, which is a human act and the most direct sign-off there
    is. It is deliberately NOT the safe-looking answer: the caller handles a
    *failed* lookup separately, because "I could not tell" and "there was nothing
    to tell" want opposite defaults.
    """
    if not pr:
        return HUMAN
    labels = pr.get("labels") or []
    head = pr.get("head") or ""
    unattended = prf.COWORK_LABEL in labels or head.startswith(prf.UNATTENDED_BRANCH_PREFIXES)
    return FLEET if unattended else HUMAN


def classify_all(prs: object) -> str:
    """``fleet`` if any associated PR is unattended.

    The commits-to-PRs endpoint can return several, in an order it does not
    document, so this asks about all of them rather than trusting a position.
    An empty list is ``None``'s case — no PR behind the commit.
    """
    if isinstance(prs, list):
        if not prs:
            return classify(None)
        entries = [pr for pr in prs if isinstance(pr, dict)]
        return FLEET if any(classify(pr) == FLEET for pr in entries) else HUMAN
    return classify(prs if isinstance(prs, dict) else None)


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw or raw == "null":
        print(classify(None))
        return 0
    try:
        prs = json.loads(raw)
    except json.JSONDecodeError:
        # Unparseable is not "human". The caller treats a non-zero exit as
        # unclassified and stays on the pre-release channel, which is the
        # recoverable direction — PyPI has no delete.
        print("could not parse the PR payload", file=sys.stderr)
        return 2
    print(classify_all(prs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
