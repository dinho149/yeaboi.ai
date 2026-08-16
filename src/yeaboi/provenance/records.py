"""The decision record: what was decided, from what evidence, by whom.

Vendored and adapted from Semantica (https://github.com/semantica-agi/semantica),
``semantica/provenance/schemas.py`` at commit 15171fd3 — Copyright (c) 2026
Hawksight AI, licensed under the MIT License (see THIRD_PARTY_NOTICES.md).
Changes from upstream: the 39-field ``ProvenanceEntry`` was trimmed to the
decision-record core and frozen (yeaboi artifacts are immutable value objects);
``metadata`` became the hash-covered ``extras`` tuple; invalidation became a
tombstone *record kind* rather than mutable columns, because the yeaboi chain
is append-only and never relabels a stored row.

A ``DecisionRecord`` is one link in a tamper-evident chain: every deterministic
signal yeaboi surfaces (a practice nudge, a blocker flag, a confidence
adjustment, a conflict card) can be recorded as a decision with its inputs, so
"why did it say that?" has a durable, verifiable answer. The field names map
onto W3C PROV-O the same way upstream's did:

- ``entity_id``            → prov:Entity (the decision, stable key)
- ``activity_id``          → prov:Activity (the run that produced it)
- ``agent_id``/``agent_type`` → prov:Agent (rule id, model id, or a person)
- ``role``                 → prov:hadRole
- ``inputs``               → prov:used (the evidence keys the decision rests on)
- ``parent_entity_id``     → prov:wasDerivedFrom
- ``previous_version_id``  → the prior record for the same entity
- ``timestamp``            → prov:generatedAtTime
- ``record_kind="invalidation"`` → prov:Invalidation (a tombstone, never a delete)

Unlike upstream, **every field is inside the checksum** (see ``integrity.py``):
upstream excluded ``entity_id`` and ``metadata`` to make in-place archival
relabels possible, which also made edits to a record's inputs undetectable.
An append-only chain has no relabels, so nothing needs to stay editable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# The two record kinds. An invalidation is itself a chained record naming who
# retracted the decision and why — deleting history is the one thing the chain
# exists to make detectable, so retraction appends rather than removes.
KIND_DECISION = "decision"
KIND_INVALIDATION = "invalidation"

AGENT_SOFTWARE = "software_agent"
AGENT_PERSON = "person"


@dataclass(frozen=True)
class DecisionRecord:
    """One auditable decision. Frozen; the chain stamps the mutable-looking
    fields (``sequence_id``, ``previous_checksum``, ``checksum``, and a missing
    ``timestamp``) by returning a stamped copy, never by mutation."""

    entity_id: str = ""
    entity_type: str = ""
    record_kind: str = KIND_DECISION
    activity_id: str = ""
    agent_id: str = "yeaboi"
    agent_type: str = AGENT_SOFTWARE
    is_automated: bool = True
    role: str = ""
    source_document: str = ""
    timestamp: str = ""
    confidence: float = 1.0
    inputs: tuple[str, ...] = ()
    parent_entity_id: str = ""
    previous_version_id: str = ""
    derived_from_id: str = ""
    detail: str = ""
    extras: tuple[tuple[str, str], ...] = ()
    sequence_id: int = 0
    previous_checksum: str = ""
    checksum: str = ""

    def stamped(self, **changes: object) -> DecisionRecord:
        """A copy with the chain-assigned fields filled in."""
        return replace(self, **changes)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ChainBreak:
    """One verification failure, pointing at the record where the chain snaps."""

    sequence_id: int = 0
    entity_id: str = ""
    reason: str = ""  # "checksum_mismatch" | "chain_break"
    expected_previous_checksum: str = ""
    actual_previous_checksum: str = ""
    expected_sequence_id: int = 0


@dataclass(frozen=True)
class ChainVerification:
    """The verdict ``ProvenanceChain.verify`` returns.

    ``valid`` means all three invariants held for every record: the stored
    checksum recomputes, each ``previous_checksum`` matches its predecessor's
    checksum, and sequence ids run 1..N with no gap — so an edited row, a
    deleted row, and a renumbered row are each detectable.
    """

    valid: bool = True
    total_records: int = 0
    broken: tuple[ChainBreak, ...] = field(default_factory=tuple)
