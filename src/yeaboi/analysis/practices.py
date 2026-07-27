"""Per-member engineering-practice hygiene over attributed commits/PRs.

Answers "how well does each member work", not "do they use AI": do their
changes ship with tests, touch or mention docs, reference a ticket, and carry
meaningful PR descriptions. Measured over ALL of a member's deduped,
member-filtered work — not just AI-marked items, because AI detection is a
lower bound and per-member AI-only slices would be too sparse to be honest.

Pure over its inputs. File-based signals (tests, docs files) only cover items
annotated with ``changed_file_paths`` by run_ai_adoption's code-change lookup;
items without file data stay out of those denominators so partial coverage
never silently deflates a rate.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from yeaboi.analysis.code_health import _is_test_path

# Below this many items in a cell's denominator, renderers show the raw
# fraction instead of a percentage (a 100% built on 2 items is noise).
MIN_PRACTICE_SAMPLE = 5

# ── Ticket references ────────────────────────────────────────────────────────
# Jira-style ABC-123 keys. The denylist guards technical tokens that share the
# shape (UTF-8, SHA-256, CVE-2024-1234, GPT-4, …) — they are not tickets.
_JIRA_REF = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-\d+\b")
_JIRA_FALSE_PREFIXES = frozenset(
    {
        "UTF",
        "SHA",
        "MD",
        "ISO",
        "RFC",
        "CVE",
        "GPT",
        "AES",
        "RSA",
        "TLS",
        "IPV",
        "HTTP",
        "HTML",
        "CSS",
        "ES",
        "OAUTH",
        "X",
        "PY",
        "V",
    }
)
_AZDO_REF = re.compile(r"\bAB#\d+\b", re.IGNORECASE)
# Bare #123 only counts at start-of-text or after whitespace/(/[ so that URL
# fragments (example.com/a#123) never match.
_ISSUE_REF = re.compile(r"(?:^|[\s(\[])#\d+\b")
# GitHub squash-merge appends "(#123)" to commit titles automatically — that
# is tooling, not an author practice, so commit titles drop it before matching.
# PR titles keep theirs: an author typing the reference is the practice.
_SQUASH_SUFFIX = re.compile(r"\s*\(#\d+\)\s*$")
# Branch names are usually lowercase (feature/abc-123-login); match keys at a
# segment start and denylist common branch vocabulary that looks key-shaped.
_BRANCH_REF = re.compile(r"(?:^|[/_])([A-Za-z][A-Za-z0-9]{1,9})-\d+", re.IGNORECASE)
_BRANCH_FALSE_PREFIXES = frozenset(
    {
        "bugfix",
        "fix",
        "feature",
        "feat",
        "release",
        "hotfix",
        "version",
        "v",
        "part",
        "step",
        "phase",
        "wip",
        "dev",
        "test",
        "issue",
        "pr",
        "sprint",
    }
) | {prefix.lower() for prefix in _JIRA_FALSE_PREFIXES}


def _jira_hit(text: str) -> bool:
    return any(match.group(1) not in _JIRA_FALSE_PREFIXES for match in _JIRA_REF.finditer(text))


def has_ticket_reference(item: dict) -> bool:
    """True when the item's title, body, or branch references a work ticket."""
    title = str(item.get("title", "") or "")
    if item.get("kind") == "commit":
        title = _SQUASH_SUFFIX.sub("", title)
    body = str(item.get("body", "") or "")
    branch = str(item.get("branch", "") or "")
    text = f"{title}\n{body}"
    if _jira_hit(text) or _AZDO_REF.search(text) or _ISSUE_REF.search(text):
        return True
    if _AZDO_REF.search(branch):
        return True
    return any(match.group(1).lower() not in _BRANCH_FALSE_PREFIXES for match in _BRANCH_REF.finditer(branch))


# ── Docs & context ───────────────────────────────────────────────────────────
_DOC_SUFFIXES = {".md", ".mdx", ".rst", ".adoc"}
_DOC_PARTS = {"docs", "doc", "documentation", "wiki", "adr", "adrs", "rfcs"}
_DOC_NAMES = {"readme", "contributing", "changelog", "architecture", "runbook"}
# Word-bounded so "docstring" or "adrenaline" never count as a docs mention.
_DOC_MENTION = re.compile(r"\b(documentation|docs|readme|changelog|runbook|adr)\b", re.IGNORECASE)


def _is_docs_path(path: str) -> bool:
    p = PurePosixPath(path)
    stem = p.name.lower().split(".", 1)[0]
    return (
        p.suffix.lower() in _DOC_SUFFIXES or bool({part.lower() for part in p.parts} & _DOC_PARTS) or stem in _DOC_NAMES
    )


def _touches_docs(item: dict) -> bool:
    paths = item.get("changed_file_paths") or []
    if any(_is_docs_path(path) for path in paths):
        return True
    text = f"{item.get('title', '') or ''}\n{item.get('body', '') or ''}"
    return bool(_DOC_MENTION.search(text))


# ── Description quality (PRs only) ───────────────────────────────────────────
# Deterministic and cheap: a description is meaningful when it carries real
# substance — one solid paragraph, multiple lines, or markdown structure with
# at least minimal length. Thresholds are pinned by tests.
_DESC_LONG = 120
_DESC_MULTILINE = 60
_DESC_STRUCTURED = 40
_STRUCTURE = re.compile(r"(?m)^\s*(#{1,6}\s|[-*]\s|\d+\.\s|\[[ xX]\])")


def has_meaningful_description(body: str) -> bool:
    """True when a PR description carries substance, not blank/one-liner filler."""
    text = (body or "").strip()
    if not text:
        return False
    lines = [line for line in text.splitlines() if line.strip()]
    return (
        len(text) >= _DESC_LONG
        or (len(lines) >= 2 and len(text) >= _DESC_MULTILINE)
        or (bool(_STRUCTURE.search(text)) and len(text) >= _DESC_STRUCTURED)
    )


# ── Aggregation ──────────────────────────────────────────────────────────────
_AGENT_ROW = "AI agent accounts"


def _new_row(member: str) -> dict:
    return {
        "member": member,
        "commits": 0,
        "prs": 0,
        "with_file_data": 0,
        "tests_num": 0,
        "tests_den": 0,
        "tests_rate": None,
        "docs_num": 0,
        "docs_den": 0,
        "docs_rate": None,
        "ticket_num": 0,
        "ticket_den": 0,
        "ticket_rate": None,
        "desc_num": 0,
        "desc_den": 0,
        "desc_rate": None,
    }


def _score_item(row: dict, item: dict) -> None:
    kind = item.get("kind")
    row["commits" if kind == "commit" else "prs"] += 1

    row["ticket_den"] += 1
    if has_ticket_reference(item):
        row["ticket_num"] += 1

    if kind == "pr":
        row["desc_den"] += 1
        if has_meaningful_description(str(item.get("body", "") or "")):
            row["desc_num"] += 1

    paths = item.get("changed_file_paths")
    if paths is None:
        return  # no change metadata fetched — stays out of file-based denominators
    row["with_file_data"] += 1
    row["docs_den"] += 1
    if _touches_docs(item):
        row["docs_num"] += 1
    production = [p for p in paths if not _is_test_path(p) and not _is_docs_path(p)]
    if production:
        # Tests-only / docs-only changes have nothing to pair a test with, so
        # they never enter the tests denominator.
        row["tests_den"] += 1
        if any(_is_test_path(p) for p in paths):
            row["tests_num"] += 1


def _finalize(row: dict) -> dict:
    for prefix in ("tests", "docs", "ticket", "desc"):
        den = row[f"{prefix}_den"]
        row[f"{prefix}_rate"] = round(row[f"{prefix}_num"] / den * 100, 1) if den else None
    return row


def member_practices(items: list[dict], selected_users: list[str]) -> dict:
    """Score practice hygiene per selected member over commit/PR items.

    Attribution mirrors the member_activity table: human items land on their
    ``matched_members`` rows; bot-authored items retained by the member filter
    land on a trailing "AI agent accounts" row. The team row is recomputed
    over the union of items, never averaged from member rates.
    """
    member_rows = {member: _new_row(member) for member in selected_users}
    agent_row = _new_row(_AGENT_ROW)
    team_row = _new_row("Team")
    with_file_data = 0
    total = 0
    for item in items:
        if item.get("kind") not in ("commit", "pr"):
            continue
        total += 1
        if item.get("changed_file_paths") is not None:
            with_file_data += 1
        targets = [member_rows[m] for m in item.get("matched_members", ()) if m in member_rows]
        if not targets and item.get("agent_authored"):
            targets = [agent_row]
        for row in targets:
            _score_item(row, item)
        _score_item(team_row, item)

    members = sorted(
        member_rows.values(),
        key=lambda row: (-(row["commits"] + row["prs"]), row["member"]),
    )
    if agent_row["commits"] or agent_row["prs"]:
        members.append(agent_row)
    return {
        "members": [_finalize(row) for row in members],
        "team": _finalize(team_row),
        "min_sample": MIN_PRACTICE_SAMPLE,
        "file_data": {"with_file_data": with_file_data, "total": total},
    }
