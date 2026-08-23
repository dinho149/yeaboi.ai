"""Conflicts: two sources disagree, and the disagreement gets a card.

A fresh implementation that imitates the vocabulary of Semantica's
``conflicts/`` package (MIT — see THIRD_PARTY_NOTICES.md) without copying its
code. Three deliberate departures from the original shape, each fixing a
verified weakness there: severity is a real enum rather than a commented
string; a value and its source travel together in one ``Claim`` instead of
two positionally-aligned lists; and a conflict carries the provenance record
ids of its evidence explicitly (``provenance_ids``) instead of linking by
naming convention.

The philosophy matches the practice signals': a conflict is an observation
with evidence, not a verdict. Detection only ever *surfaces* a disagreement —
silently lowering confidence because two sources disagree is exactly the
behaviour this module exists to replace. Resolution is a separate, explicit
step, and the default strategy is a human's review.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from yeaboi._compat import StrEnum


class Severity(StrEnum):
    """How bad the disagreement is. str-valued so members serialize as words."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ResolutionStrategy(StrEnum):
    VOTING = "voting"
    CREDIBILITY_WEIGHTED = "credibility_weighted"
    MOST_RECENT = "most_recent"
    FIRST_SEEN = "first_seen"
    HIGHEST_CONFIDENCE = "highest_confidence"
    MANUAL_REVIEW = "manual_review"


# A source we know nothing about is neither trusted nor distrusted.
DEFAULT_CREDIBILITY = 0.5


@dataclass(frozen=True)
class Claim:
    """One source's assertion about one property of one entity."""

    value: str = ""
    source_document: str = ""  # "jira", "github", "azdo", a transcript, …
    confidence: float = 1.0
    observed_at: str = ""  # ISO-8601, when the source said it
    ref: str = ""  # a stable evidence key or url for the click-through


@dataclass(frozen=True)
class Conflict:
    """A detected disagreement between at least two claims."""

    conflict_id: str = ""
    conflict_type: str = "value_conflict"
    entity_id: str = ""
    property_name: str = ""
    claims: tuple[Claim, ...] = ()
    severity: str = Severity.MEDIUM.value
    confidence: float = 1.0
    recommended_action: str = ""
    provenance_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resolution:
    """The outcome of resolving one conflict. ``resolved=False`` with the
    manual-review strategy is a normal outcome, not a failure."""

    conflict_id: str = ""
    resolved: bool = False
    resolved_value: str = ""
    strategy: str = ""
    confidence: float = 0.0
    sources_used: tuple[str, ...] = ()
    notes: str = ""
    extras: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def _distinct_values(claims: Sequence[Claim]) -> list[str]:
    """The distinct asserted values, in first-seen order. An empty value is
    not a claim: "source B says nothing" must never manufacture a conflict."""
    seen: list[str] = []
    for claim in claims:
        value = claim.value.strip()
        if value and value not in seen:
            seen.append(value)
    return seen


def conflict_confidence(claims: Sequence[Claim]) -> float:
    """How sure we are the disagreement is real (imitates upstream's shape:
    average claim confidence scaled up by value diversity, capped at 1)."""
    if not claims:
        return 0.0
    average = sum(c.confidence for c in claims) / len(claims)
    values = [c.value.strip() for c in claims if c.value.strip()]
    diversity = len(set(values)) / len(values) if values else 0.0
    return round(min(1.0, average * (1 + diversity)), 4)


def severity_for(property_name: str, claims: Sequence[Claim], *, critical_properties: Sequence[str] = ()) -> Severity:
    """Severity heuristic: identity-bearing properties are critical, wide
    numeric spreads are high, everything else is medium."""
    if property_name.lower() in {p.lower() for p in critical_properties}:
        return Severity.CRITICAL
    numbers = []
    for claim in claims:
        try:
            numbers.append(float(claim.value))
        except (TypeError, ValueError):
            numbers = []
            break
    if len(numbers) >= 2 and max(numbers) - min(numbers) > 1000:
        return Severity.HIGH
    return Severity.MEDIUM


def find_conflict(
    entity_id: str,
    property_name: str,
    claims: Sequence[Claim],
    *,
    conflict_type: str = "value_conflict",
    critical_properties: Sequence[str] = (),
    recommended_action: str = "",
) -> Conflict | None:
    """Return the conflict in a claim set, or None when the sources agree.

    Two claims are the floor — one source cannot disagree with itself — and
    empty values are dropped before comparing, so absence of a claim never
    reads as disagreement.
    """
    if len(claims) < 2:
        return None
    values = _distinct_values(claims)
    if len(values) < 2:
        return None
    action = recommended_action
    if not action:
        action = (
            "Compare the two sources and correct whichever is stale."
            if len(values) == 2
            else "Multiple sources disagree; a manual review is recommended."
        )
    return Conflict(
        conflict_id=f"{entity_id}:{property_name}:{conflict_type}",
        conflict_type=conflict_type,
        entity_id=entity_id,
        property_name=property_name,
        claims=tuple(claims),
        severity=severity_for(property_name, claims, critical_properties=critical_properties).value,
        confidence=conflict_confidence(claims),
        recommended_action=action,
    )


def resolve(
    conflict: Conflict,
    *,
    strategy: ResolutionStrategy = ResolutionStrategy.MANUAL_REVIEW,
    credibility: Mapping[str, float] | None = None,
) -> Resolution:
    """Resolve one conflict with an explicit strategy.

    ``credibility`` maps a source document to a 0–1 trust score set by the
    operator; it is only consulted by ``CREDIBILITY_WEIGHTED``, where each
    claim's weight is its own confidence times its source's credibility.
    """
    claims = [c for c in conflict.claims if c.value.strip()]
    base = Resolution(conflict_id=conflict.conflict_id, strategy=strategy.value)
    if not claims:
        return base
    if strategy is ResolutionStrategy.MANUAL_REVIEW:
        return Resolution(
            conflict_id=conflict.conflict_id,
            strategy=strategy.value,
            notes="Held for a human decision.",
            extras=(("severity", conflict.severity),),
        )
    if strategy is ResolutionStrategy.VOTING:
        counts = Counter(c.value.strip() for c in claims)
        value, votes = counts.most_common(1)[0]
        return Resolution(
            conflict_id=conflict.conflict_id,
            resolved=True,
            resolved_value=value,
            strategy=strategy.value,
            confidence=round(votes / len(claims), 4),
            sources_used=tuple(c.source_document for c in claims if c.value.strip() == value),
        )
    if strategy is ResolutionStrategy.CREDIBILITY_WEIGHTED:
        scores = credibility or {}
        weights: dict[str, float] = {}
        for claim in claims:
            weight = claim.confidence * scores.get(claim.source_document, DEFAULT_CREDIBILITY)
            weights[claim.value.strip()] = weights.get(claim.value.strip(), 0.0) + weight
        total = sum(weights.values())
        value = max(weights.items(), key=lambda kv: kv[1])[0]
        return Resolution(
            conflict_id=conflict.conflict_id,
            resolved=True,
            resolved_value=value,
            strategy=strategy.value,
            confidence=round(weights[value] / total, 4) if total else 0.0,
            sources_used=tuple(c.source_document for c in claims if c.value.strip() == value),
        )
    if strategy is ResolutionStrategy.MOST_RECENT:
        dated = [c for c in claims if c.observed_at]
        if not dated:
            return Resolution(
                conflict_id=conflict.conflict_id,
                strategy=strategy.value,
                notes="No claim carries a timestamp; most-recent cannot decide.",
            )
        winner = max(dated, key=lambda c: c.observed_at)
        return Resolution(
            conflict_id=conflict.conflict_id,
            resolved=True,
            resolved_value=winner.value.strip(),
            strategy=strategy.value,
            confidence=0.8,
            sources_used=(winner.source_document,),
        )
    if strategy is ResolutionStrategy.FIRST_SEEN:
        winner = claims[0]
        return Resolution(
            conflict_id=conflict.conflict_id,
            resolved=True,
            resolved_value=winner.value.strip(),
            strategy=strategy.value,
            confidence=0.7,
            sources_used=(winner.source_document,),
        )
    # HIGHEST_CONFIDENCE
    winner = max(claims, key=lambda c: c.confidence)
    return Resolution(
        conflict_id=conflict.conflict_id,
        resolved=True,
        resolved_value=winner.value.strip(),
        strategy=strategy.value,
        confidence=round(winner.confidence, 4),
        sources_used=(winner.source_document,),
    )
