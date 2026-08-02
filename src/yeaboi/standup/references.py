"""Ticket-reference and pull-request parsing shared across the standup pipeline.

Three consumers used to keep their own copies of these patterns: ``export.py``
(linkifying ticket keys mentioned in prose), ``engine.py`` (the deterministic
carried-over-work note, and folding commits under their PR), and now
``habits.py`` (deciding whether a change references a ticket at all). One copy
drifting from another is a silent bug — a key that linkifies in the HTML but
doesn't count as "tracked" would accuse someone of untracked work while showing
them the very link that disproves it.

**The gate.** Ticket-shaped text is not evidence of a ticket. ``UTF-8``,
``SHA-256``, ``ISO-8601`` and ``HTTP-2`` all match a Jira key regex, and on
GitHub ``#91`` is a pull-request number, not a work item. So each syntax is
admitted only on evidence the *tracker itself* produced in this run:

- ``PROJ-123`` — prefix-gated: counted only when ``PROJ`` is a prefix the
  trackers actually emitted (``tracker_prefixes``).
- ``AB#123`` — ungated. Azure DevOps' ARM syntax is unambiguous; nothing else
  spells it that way, so it carries its own evidence.
- ``#123`` — id-gated: counted only when ``123`` is a work-item id the Azure
  Boards collector emitted in this window. On a GitHub-only setup that set is
  empty and a bare ``#91`` never counts, which is exactly right.

Evidence from the tracker unlocks a pattern; a pattern never unlocks itself.

Pure: no I/O, no config, no LLM. Imported by ``export``, ``engine`` and
``habits``, so it must never import any of them.

# See docs: "Daily Standup" — exports
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping

# Jira-style ticket keys ("PSOT-12"). AzDO work items ("#1234" / "AB#1234")
# deliberately don't match — they have their own patterns below, with their own
# gates, because a bare number is far more ambiguous than a prefixed key.
TICKET_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")

# Azure DevOps' "artifact reference" syntax. Case-insensitive because commit
# subjects spell it "ab#123" as often as "AB#123", and the leading boundary
# stops "LAB#12" from matching.
AZDO_REF_RE = re.compile(r"(?<![A-Za-z0-9])AB#(\d+)\b", re.IGNORECASE)

# A bare "#123". The lookbehind excludes "AB#123" (already matched above) and
# anything word-adjacent, so "utf#8" and "v1.2#3" don't produce a reference.
BARE_ID_RE = re.compile(r"(?<![\w#])#(\d+)\b")

# Commit → PR association lives in title text only: collectors emit pr_id and
# branch on PR items, but a commit names its PR solely via its subject. The
# real-world formats, one pattern each: GitHub/AzDO merge commits
# ("Merge pull request #91 …" / "Merge pull request 48806 …"), AzDO squash
# merges ("Merged PR 123: Title"), and parenthesised references — GitHub squash
# merges end in "(#91)" and the collector's own PR-branch scan appends
# "(PR #91)".
PR_NUMBER_RES = (
    re.compile(r"Merge pull request #?(\d+)"),
    re.compile(r"Merged PR (\d+):"),
    re.compile(r"\((?:PR )?#(\d+)\)"),
)
MERGE_BRANCH_RE = re.compile(r"Merge pull request .*? from (\S+)")

# Tail the collectors append to a commit subject for provenance: " (PR #91)"
# from github's PR-branch scan, " (my-repo)" from every AzDO commit and PR.
# Both inflate a one-word subject, so commit-message judgement strips them.
_SUBJECT_TAIL_RE = re.compile(r"\s*\((?:PR #\d+|[^()]{1,60})\)\s*$")

# Kinds whose ``key`` is a tracker handle rather than a sha or PR number. The
# prefix/id gates are built from these and only these.
# ``ticket_context`` belongs here for the same reason the rest do: these items
# came back from Jira/Azure themselves, key and all. Leaving it out made the gate
# a function of *board activity* rather than of what the tracker contains, so on
# a day nobody moved a ticket the prefixes went empty and a commit titled
# "PROJ-12 fix login" read as untracked work — an accusation aimed at a named
# person, produced by a quiet Monday.
_TRACKER_KINDS = frozenset({"issue", "wip", "work_item", "update", "comment", "ticket_context"})

# The subset whose keys are Azure Boards ids, for the bare ``#1234`` gate. A Jira
# ``ticket_context`` key ("PROJ-12") is filtered out by the isdigit() check below
# rather than by kind, so one membership list serves both trackers.
_WORK_ITEM_KINDS = frozenset({"work_item", "wip", "ticket_context"})


def find_ticket_keys(text: str) -> tuple[str, ...]:
    """Every Jira-shaped key in ``text``, ungated, in order of appearance."""
    return tuple(TICKET_KEY_RE.findall(text or ""))


def prefixes_of(keys: Iterable[str]) -> frozenset[str]:
    """Project prefixes of a set of ticket keys ("PSOT-12" → "PSOT")."""
    return frozenset(key.split("-")[0] for key in keys if key)


def tracker_prefixes(items: Iterable[Mapping]) -> frozenset[str]:
    """Project prefixes the trackers actually produced in this window.

    Built from tracker-sourced item *keys*, never from prose: a key invented by
    an LLM (or a "UTF-8" in a commit subject) must not be able to widen the gate
    that is meant to exclude it.
    """
    return prefixes_of(str(item.get("key") or "").strip() for item in items if item.get("kind") in _TRACKER_KINDS)


def tracker_work_item_ids(items: Iterable[Mapping]) -> frozenset[str]:
    """Azure Boards work-item ids seen this window ("#1234" → "1234").

    The gate for bare ``#123`` references. Empty on a Jira-only or GitHub-only
    setup, which is what keeps a GitHub PR number from reading as a work item.
    """
    ids: set[str] = set()
    for item in items:
        if item.get("kind") not in _WORK_ITEM_KINDS:
            continue
        key = str(item.get("key") or "").strip().lstrip("#")
        if key.isdigit():
            ids.add(key)
    return frozenset(ids)


def gated_ticket_keys(text: str, *, prefixes: Collection[str]) -> tuple[str, ...]:
    """Jira-shaped keys in ``text`` whose project prefix the tracker produced."""
    return tuple(key for key in find_ticket_keys(text) if key.split("-")[0] in prefixes)


def has_tracker_reference(
    *texts: str,
    prefixes: Collection[str] = (),
    work_item_ids: Collection[str] = (),
) -> bool:
    """Whether any of ``texts`` references a ticket, under all three gates.

    Used by the untracked-work rule, so it answers the question one way only:
    True means *we found positive evidence of a link*. False means we found
    none — never "there is none", which is why the caller also has to be sure it
    could have seen the link in the first place (see ``habits``).
    """
    for text in texts:
        if not text:
            continue
        if AZDO_REF_RE.search(text):
            return True
        if prefixes and gated_ticket_keys(text, prefixes=prefixes):
            return True
        if work_item_ids and any(match in work_item_ids for match in BARE_ID_RE.findall(text)):
            return True
    return False


def pr_reference(subject: str) -> str:
    """The PR number a commit subject claims, or "" — text evidence only."""
    for pattern in PR_NUMBER_RES:
        if match := pattern.search(subject or ""):
            return match.group(1)
    return ""


def merge_source_branch(subject: str) -> str:
    """The source branch named by a GitHub merge subject, or ""."""
    match = MERGE_BRANCH_RE.search(subject or "")
    return match.group(1) if match else ""


def claims_pull_request(subject: str) -> bool:
    """Whether a commit subject textually claims a PR, parent found or not.

    ``engine._nest_pr_commits`` needs a *real* parent before folding a commit
    under it. The habit rules need the weaker fact: a subject that says "Merge
    pull request #91" belongs to a PR whether or not that PR is inside the
    collection window, and judging it as loose untracked work would be wrong.
    """
    return bool(pr_reference(subject) or merge_source_branch(subject))


def is_merge_subject(subject: str) -> bool:
    """Whether a subject is an actual merge commit — not merely PR-referencing.

    Narrower than ``claims_pull_request`` on purpose. The parenthesised form
    ("fix login (#91)", or the " (PR #91)" the collector appends itself) is
    *provenance on an authored commit*, so a rule that judges what the author
    wrote must still see it — excluding it would make commit-message quality
    unmeasurable for any team that squash-merges, which is most of them.
    """
    text = subject or ""
    return bool(PR_NUMBER_RES[0].search(text) or PR_NUMBER_RES[1].search(text) or MERGE_BRANCH_RE.search(text))


def normalize_commit_subject(subject: str) -> str:
    """Strip collector-added provenance tails so a subject reads as authored.

    " (PR #91)" (github.py's PR-branch scan) and " (my-repo)" (every AzDO
    commit) make a one-word subject look substantial. Judging message quality
    without stripping them would let "wip" pass as "wip (my-repo)".
    """
    text = (subject or "").strip()
    while match := _SUBJECT_TAIL_RE.search(text):
        text = text[: match.start()].rstrip()
    return text
