"""Cross-source conflict cards: the board and the world disagree, out loud.

The standup already holds one tracker-vs-code contradiction rule
(habits._board_not_updated: merged code against a not-started ticket). This
module adds the two mirrors the reader kept having to spot by hand — **a ticket
closed as done while a pull request that names it is still open**, and the same
ticket while an incident that names it is still live — and it
surfaces the disagreement as an explicit card instead of feeding any silent
confidence adjustment. Detection uses the shared conflicts vocabulary
(yeaboi.provenance.conflicts), so every card carries both claims with their
evidence and a recommended action.

Deterministic, engine-layer only: this runs on the aggregate's wire output
*above* the Go-mirrored core, so the byte-parity surface is untouched. The
suppress-only philosophy carries over — an empty value is not a claim, one
source cannot conflict with itself, and a quiet day produces no cards.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping, Sequence

from yeaboi.agent.state import ConflictCard
from yeaboi.provenance.conflicts import Claim, find_conflict
from yeaboi.standup import references

logger = logging.getLogger(__name__)

# Board columns that mean "this work is finished". EXACT matches after
# normalisation, for the same reason habits._TODO_STATUSES is exact: prefix
# families would swallow columns like "Done pending QA" that do not mean done.
_DONE_STATUSES = frozenset(
    {
        "done",
        "closed",
        "resolved",
        "complete",
        "completed",
    }
)

# Pull-request states that mean "this change has not landed". Merged is
# settled and closed-unmerged is abandoned; neither contradicts a done ticket.
_OPEN_PR_STATUSES = frozenset({"open", "active", "draft", "in review"})

# Kinds whose `status` describes where a ticket sits (habits' held-ticket set;
# a Jira changelog `update` is excluded there for credit reasons that do not
# apply here, but the newest-observation rule below makes it redundant anyway).
_TICKET_KINDS = frozenset({"issue", "wip", "work_item"})

_MAX_CARDS = 8
_TITLE_CLIP = 60


def _norm(value: object) -> str:
    return str(value or "").strip().lower()


def _clip(text: str) -> str:
    text = str(text or "").strip()
    return text if len(text) <= _TITLE_CLIP else text[: _TITLE_CLIP - 1] + "…"


def _done_tickets(items: Sequence[Mapping]) -> dict[str, Mapping]:
    """Ticket key → the newest observation that says it is done.

    Newest wins across ALL held-ticket observations: a ticket seen done at
    09:00 and reopened at 11:00 is not done, so a stale done-sighting must
    never survive a later contradicting one.
    """
    latest: dict[str, Mapping] = {}
    stamps: dict[str, str] = {}
    for item in items:
        if item.get("kind") not in _TICKET_KINDS:
            continue
        key = str(item.get("key") or "").strip()
        status = _norm(item.get("status"))
        if not key or not status:
            continue
        stamp = str(item.get("timestamp") or "")
        if key not in latest or stamp >= stamps.get(key, ""):
            latest[key] = item
            stamps[key] = stamp
    return {key: item for key, item in latest.items() if _norm(item.get("status")) in _DONE_STATUSES}


def _referenced_keys(item: Mapping, *, prefixes: Collection[str], work_item_ids: Collection[str]) -> tuple[str, ...]:
    """Ticket handles this change names — same haystacks the habit rules read."""
    keys: list[str] = []
    for text in (str(item.get("title") or ""), str(item.get("branch") or ""), str(item.get("body") or "")):
        keys.extend(references.gated_ticket_keys(text, prefixes=prefixes))
        keys.extend(references.AZDO_REF_RE.findall(text))
        keys.extend(match for match in references.BARE_ID_RE.findall(text) if match in work_item_ids)
    keys.extend(str(w) for w in (item.get("work_item_ids") or ()))
    return tuple(dict.fromkeys(keys))


def _label(item: Mapping) -> str:
    return _clip(str(item.get("title") or item.get("key") or "change"))


def detect_status_conflicts(
    grouped: Mapping[str, Sequence[Mapping]],
    *,
    prefixes: Collection[str],
    work_item_ids: Collection[str],
) -> tuple[tuple[ConflictCard, ...], tuple[str, ...]]:
    """Find done-ticket-vs-open-PR disagreements across the whole team.

    Returns ``(cards, warnings)``. Cards are capped at ``_MAX_CARDS`` in
    deterministic key order; when the cap trims anything a warning names the
    count, because a silently truncated list reads as a complete one.
    """
    all_items = [(name, item) for name, items in grouped.items() for item in items]
    done = _done_tickets([item for _, item in all_items])
    if not done:
        return (), ()

    cards: list[ConflictCard] = []
    seen: set[str] = set()
    for key in sorted(done):
        ticket = done[key]
        # Every open PR that names this finished ticket, with who holds it.
        holders: list[str] = []
        open_prs: list[Mapping] = []
        for name, item in all_items:
            if _norm(item.get("kind")) != "pr" or _norm(item.get("status")) not in _OPEN_PR_STATUSES:
                continue
            named = _referenced_keys(item, prefixes=prefixes, work_item_ids=work_item_ids)
            if key in named or key.lstrip("#") in named or f"#{key}" in named:
                open_prs.append(item)
                if name not in holders:
                    holders.append(name)
        if not open_prs:
            continue
        pr = open_prs[0]
        ticket_status = str(ticket.get("status") or "").strip()
        pr_status = str(pr.get("status") or "").strip() or "open"
        conflict = find_conflict(
            key,
            "status",
            [
                Claim(
                    value=ticket_status,
                    source_document=str(ticket.get("source") or "tracker"),
                    observed_at=str(ticket.get("timestamp") or ""),
                    ref=str(ticket.get("url") or ""),
                ),
                Claim(
                    value=pr_status,
                    source_document=str(pr.get("source") or "code"),
                    observed_at=str(pr.get("timestamp") or ""),
                    ref=str(pr.get("url") or ""),
                ),
            ],
            conflict_type="status_conflict",
            recommended_action=(f"Reopen {key}, or merge/close the pull request, so the board matches the code."),
        )
        if conflict is None or conflict.conflict_id in seen:
            continue
        seen.add(conflict.conflict_id)
        extra = f" (+{len(open_prs) - 1} more)" if len(open_prs) > 1 else ""
        cards.append(
            ConflictCard(
                fingerprint=conflict.conflict_id,
                title=f"{key} — the board says {ticket_status}, but a pull request is still open",
                detail=(
                    f"{key} is {ticket_status} on the board, while “{_label(pr)}”{extra} "
                    f"still names it and has not merged."
                ),
                severity=conflict.severity,
                entity_id=key,
                property_name="status",
                claims=tuple(
                    (claim.source_document, claim.value, _label(ticket if i == 0 else pr), claim.ref)
                    for i, claim in enumerate(conflict.claims)
                ),
                recommended_action=conflict.recommended_action,
                members=tuple(holders),
            )
        )

    warnings: tuple[str, ...] = ()
    if len(cards) > _MAX_CARDS:
        warnings = (f"Conflict cards capped at {_MAX_CARDS}; {len(cards) - _MAX_CARDS} more not shown.",)
        logger.info("standup conflicts: %d detected, capped at %d", len(cards), _MAX_CARDS)
        cards = cards[:_MAX_CARDS]
    if cards:
        logger.info("standup conflicts: %d card(s) detected", len(cards))
    return tuple(cards), warnings


# ---------------------------------------------------------------------------
# Production vs the board
# ---------------------------------------------------------------------------

#: How many ops cards may ride alongside the board's. Their own budget rather
#: than a share of ``_MAX_CARDS``, so a noisy Datadog day cannot evict a single
#: board card and a busy board cannot hide production entirely.
_MAX_OPS_CARDS = 3


def detect_ops_conflicts(
    grouped: Mapping[str, Sequence[Mapping]],
    events: Sequence,
    *,
    prefixes: Collection[str],
) -> tuple[ConflictCard, ...]:
    """Find done-ticket-vs-live-incident disagreements.

    Both sides must make a POSITIVE claim. A settled incident is *agreement*
    with a done ticket, not a contradiction, and an event with no status at all
    asserts nothing — so only an unresolved, status-carrying event can conflict.

    Linking is by ticket key in the event title, behind the same prefix gate the
    board rules use: ``PROJ-12`` counts only when the tracker itself emitted the
    ``PROJ`` prefix in this run. The bare and ``AB#`` forms are deliberately NOT
    admitted here — a monitor named "AB#42 latency" is a monitor name, and an
    alert must not be able to invent a work item.

    ``members`` is always empty: naming whoever merged the PR would reintroduce
    blame by the back door, and nobody is on the hook for an alert firing.
    """
    done = _done_tickets([item for items in grouped.values() for item in items])
    if not done:
        return ()

    by_key: dict[str, list] = {}
    for event in events:
        if not getattr(event, "status", "") or event.resolved:
            continue
        for key in references.gated_ticket_keys(str(getattr(event, "title", "")), prefixes=prefixes):
            if key in done:
                by_key.setdefault(key, []).append(event)

    cards: list[ConflictCard] = []
    for key in sorted(by_key):
        ticket = done[key]
        event = by_key[key][0]
        ticket_status = str(ticket.get("status") or "").strip()
        conflict = find_conflict(
            key,
            "status",
            [
                Claim(
                    value=ticket_status,
                    source_document=str(ticket.get("source") or "tracker"),
                    observed_at=str(ticket.get("timestamp") or ""),
                    ref=str(ticket.get("url") or ""),
                ),
                Claim(
                    value=event.status,
                    source_document=event.source,
                    observed_at=event.started_at,
                    ref=event.url,
                ),
            ],
            conflict_type="ops_conflict",
            recommended_action=(
                f"Reopen {key}, or settle {event.ref or 'the incident'} in {event.source}, "
                "so the board matches production."
            ),
        )
        if conflict is None:
            continue
        extra = f" (+{len(by_key[key]) - 1} more)" if len(by_key[key]) > 1 else ""
        service = f" on {event.service}" if event.service else ""
        cards.append(
            ConflictCard(
                fingerprint=conflict.conflict_id,
                title=f"{key} — the board says {ticket_status}, but production is still {event.status}",
                detail=(
                    f"{key} is {ticket_status} on the board, while “{_clip(event.title)}”{extra}"
                    f"{service} is {event.status} in {event.source}."
                ),
                severity=conflict.severity,
                entity_id=key,
                property_name="status",
                claims=tuple(
                    (claim.source_document, claim.value, _label(ticket) if i == 0 else _clip(event.title), claim.ref)
                    for i, claim in enumerate(conflict.claims)
                ),
                recommended_action=conflict.recommended_action,
                members=(),
            )
        )
    if cards:
        logger.info("standup conflicts: %d production card(s) detected", len(cards))
    return tuple(cards)


def merge_cards(
    board: Sequence[ConflictCard],
    ops: Sequence[ConflictCard],
) -> tuple[tuple[ConflictCard, ...], tuple[str, ...]]:
    """Board cards, then up to ``_MAX_OPS_CARDS`` production ones.

    Neither list is re-sorted — each detector already ordered its own by the key
    it reasons about, and a merge that re-ranked them would put a rule nobody
    can name in front of the reader.
    """
    kept = list(ops[:_MAX_OPS_CARDS])
    warnings: tuple[str, ...] = ()
    if len(ops) > _MAX_OPS_CARDS:
        warnings = (f"Production conflicts capped at {_MAX_OPS_CARDS}; {len(ops) - _MAX_OPS_CARDS} more not shown.",)
    return (*board, *kept), warnings
