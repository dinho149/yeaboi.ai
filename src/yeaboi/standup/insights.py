"""Deterministic day-over-day standup insights: blocker evidence + yesterday context.

Two jobs, both pure (no I/O, no LLM):

- ``detect_blocker_signals`` scans the grouped activity for evidence a member
  is blocked — a ticket sitting in a blocked-ish column, a PR still open since
  the previous standup, or unusually heavy comment traffic on one ticket. The
  signals are passed to the LLM as verified evidence it must reflect in
  ``blockers`` and, in the no-LLM fallback, become the blockers text directly.
- ``yesterday_context`` distills the previous standup report (loaded from
  ``StandupStore.get_previous_report``) into per-member comparison context so
  the LLM can write a "since last standup" progress note and the day-ahead
  outlook.

Precision over recall, mirroring ``automation.py``: a false "you look blocked"
erodes trust faster than a missed blocker, so statuses match a narrow list,
the cross-standup PR rule needs the exact URL to reappear, and comment churn
needs several comments from several people before it fires.

# See docs: "Daily Standup" — blockers and confidence
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from yeaboi.agent.state import StandupReport

# Ticket/work-item kinds whose ``status`` is a board column; Jira changelog
# "update" items carry the *destination* column, so a move to Blocked counts.
_STATUS_KINDS = frozenset({"issue", "wip", "work_item", "update"})

# Narrow blocked-ish vocabulary. Exact matches plus two prefix families —
# "waiting for X" / "waiting on X" are blocked columns, but a bare "waiting"
# prefix would also catch benign columns like "Waiting Deploy Queue"; the
# space-suffixed prefixes keep it attribution-shaped.
_BLOCKED_EXACT = frozenset({"blocked", "on hold", "impeded", "stuck", "paused"})
_BLOCKED_PREFIXES = ("blocked", "on hold", "waiting for", "waiting on")

# Comment-churn thresholds: N comment items on one ticket key from at least
# M distinct members before "heavy discussion" fires.
_CHURN_MIN_COMMENTS = 4
_CHURN_MIN_MEMBERS = 2

_MAX_SIGNALS_PER_MEMBER = 3
_TITLE_CLIP = 60  # keep each signal line short enough for a chip/bullet
_YESTERDAY_CLIP = 300  # prompt-budget cap per carried-over field


def _is_blocked_status(status: str) -> bool:
    normalized = " ".join(status.strip().lower().split())
    if not normalized:
        return False
    if normalized in _BLOCKED_EXACT:
        return True
    return any(normalized.startswith(prefix) for prefix in _BLOCKED_PREFIXES)


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _item_label(item: Mapping) -> str:
    """Human-readable handle for an item: ticket key + clipped title when available."""
    key = str(item.get("key") or "").strip()
    title = _clip(str(item.get("title") or ""), _TITLE_CLIP)
    if key and title:
        return f"{key} '{title}'"
    return key or title or "an item"


def _previous_pr_urls(previous_report: StandupReport | None) -> dict[str, set[str]]:
    """Per-member set of code-evidence URLs from the previous standup report."""
    if previous_report is None:
        return {}
    urls: dict[str, set[str]] = {}
    for member in previous_report.member_updates:
        member_urls = {url for _label, url in member.code_links if url}
        # Legacy reports (pre category split) carried everything in `links`.
        member_urls.update(url for _label, url in member.links if url)
        if member_urls:
            urls[member.name] = member_urls
    return urls


def detect_blocker_signals(
    grouped: Mapping[str, Sequence[Mapping]],
    *,
    previous_report: StandupReport | None = None,
) -> dict[str, tuple[str, ...]]:
    """Per-member deterministic blocker evidence strings (members with none are absent).

    ``grouped`` is the engine's ``_group_activity_by_author`` output: items keep
    ``kind, title, status, source, key, url, repository`` — everything the three
    rules need.
    """
    prev_urls = _previous_pr_urls(previous_report)

    # Rule 3 pre-pass — comment traffic per ticket key across the whole team.
    # The member grouping IS the author dimension: a key discussed by many
    # people appears in many members' comment lists.
    comment_counts: dict[str, int] = {}
    comment_members: dict[str, set[str]] = {}
    for name, items in grouped.items():
        for item in items:
            if item.get("kind") != "comment":
                continue
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            comment_counts[key] = comment_counts.get(key, 0) + 1
            comment_members.setdefault(key, set()).add(name)
    churn_keys = {
        key
        for key, count in comment_counts.items()
        if count >= _CHURN_MIN_COMMENTS and len(comment_members[key]) >= _CHURN_MIN_MEMBERS
    }

    signals: dict[str, tuple[str, ...]] = {}
    for name, items in grouped.items():
        found: list[str] = []
        seen_handles: set[str] = set()  # dedupe by ticket key / URL across rules

        for item in items:
            kind = str(item.get("kind") or "")
            status = str(item.get("status") or "")

            # Rule 1: ticket sitting in (or just moved to) a blocked-ish column.
            if kind in _STATUS_KINDS and _is_blocked_status(status):
                handle = str(item.get("key") or item.get("url") or item.get("title") or "")
                if handle and handle in seen_handles:
                    continue
                seen_handles.add(handle)
                found.append(f"{_item_label(item)} is in {status.strip()}")
                continue

            # Rule 2: a PR that was already evidence in the previous standup and
            # is still open today — unmerged across two standups.
            if kind == "pr" and status.lower() == "open":
                url = str(item.get("url") or "")
                if url and url in prev_urls.get(name, set()) and url not in seen_handles:
                    seen_handles.add(url)
                    found.append(f"PR {_item_label(item)} still open since the last standup")

        # Rule 3: heavy discussion — attributed to the member who OWNS the
        # ticket (holds an issue/wip/work_item with that key), not to every
        # commenter; an orphan key nobody owns is dropped (precision first).
        for item in items:
            if item.get("kind") not in ("issue", "wip", "work_item"):
                continue
            key = str(item.get("key") or "").strip()
            if key in churn_keys and key not in seen_handles:
                seen_handles.add(key)
                found.append(f"Heavy discussion on {key} ({comment_counts[key]} comments)")

        if found:
            signals[name] = tuple(found[:_MAX_SIGNALS_PER_MEMBER])
    return signals


def corrected_members(corrections: Sequence[object]) -> dict[str, list[str]]:
    """Map ``{member name: [field, ...]}`` from a previous run's edit log.

    The names come back out of the paths — ``member_updates[name=Ada].blockers``
    — because that is where they are, and re-deriving them here keeps the log
    itself free of anything but what was written.

    Anything unparseable is skipped rather than raised on: a hint that cannot be
    built is a hint the run does without, and a standup must not fail because a
    correction from last week was recorded by an older version.
    """
    from yeaboi.artifacts.paths import PathError, parse_path

    out: dict[str, list[str]] = {}
    for edit in corrections:
        path = getattr(edit, "path", "")
        if not path:
            continue
        try:
            segments = parse_path(path)
        except PathError:
            continue
        if len(segments) != 2 or segments[0].field != "member_updates" or not segments[0].value:
            continue
        out.setdefault(segments[0].value, []).append(segments[1].field)
    return out


def yesterday_context(
    previous_report: StandupReport | None,
    transcript_corrections: dict[str, list[str]] | None = None,
    *,
    corrections: Sequence[object] = (),
    corrected_fields: Mapping[str, list[str]] | None = None,
) -> dict[str, dict]:
    """Per-member comparison context distilled from the previous standup report.

    Returns ``{name: {"summary": ..., "blockers": ..., "outlook": ...}}`` with
    each value clipped to keep the LLM prompt bounded; members with a fully
    empty previous update are omitted. ``{}`` when there is no previous report.

    Two different kinds of correction reach this function, and they stay
    separate because they mean different things to the prompt:

    ``corrections`` (keyword) is the previous run's **edit log** — fields the
    team fixed by hand. The affected members carry a ``corrected`` list naming
    those fields. The corrected text itself already feeds forward for free — a
    corrected row supersedes its parent in ``get_previous_run`` — but that alone
    only stops the model repeating a wrong *fact*. The flag is what lets the
    prompt say the team looked at this and disagreed.

    ``transcript_corrections`` is work a member stated in the last standup
    MEETING that the last report missed (see ``standup/transcript_review.py``).
    They are fed FORWARD rather than written back into yesterday's stored
    report: ``standup_history`` is an append-only record of what was said at the
    time, and rewriting it to make today tidy would falsify that record. A
    member with a transcript correction but no previous update still gets an
    entry — the correction is the only thing we know about their yesterday.

    ``corrected_fields`` is the already-parsed form of ``corrections`` — the
    aggregate seam pre-parses the edit log in Python because parsing needs
    ``yeaboi.artifacts.paths``, which the wire (and the Go port) never carries.
    When given, it wins and ``corrections`` is ignored.
    """
    fixed = dict(corrected_fields) if corrected_fields is not None else corrected_members(corrections)
    context: dict[str, dict] = {}
    if previous_report is not None:
        for member in previous_report.member_updates:
            entry: dict = {
                "summary": _clip(member.summary, _YESTERDAY_CLIP),
                "blockers": _clip(member.blockers, _YESTERDAY_CLIP),
                "outlook": _clip(getattr(member, "outlook", ""), _YESTERDAY_CLIP),
            }
            if any(entry.values()):
                if member.name in fixed:
                    entry["corrected"] = sorted(set(fixed[member.name]))
                context[member.name] = entry
    for name, items in (transcript_corrections or {}).items():
        # Filter AFTER clipping: a whitespace-only string is truthy but clips to
        # nothing, and an empty "correction" in the prompt is worse than none.
        clipped = [c for item in items if (c := _clip(item, _YESTERDAY_CLIP).strip())][:_MAX_SIGNALS_PER_MEMBER]
        if not clipped:
            continue
        entry = context.setdefault(name, {"summary": "", "blockers": "", "outlook": ""})
        entry["corrections"] = clipped
    return context
