"""Provenance: tamper-evident decision records behind yeaboi's signals.

Adapted from Semantica's ``provenance/`` package (MIT — see
THIRD_PARTY_NOTICES.md). Every deterministic signal yeaboi surfaces can be
recorded here as a hash-chained decision with its evidence, so "why did it
say that?" has a durable, verifiable answer — and so does "has anyone edited
that answer since".
"""

from yeaboi.provenance.conflicts import (
    Claim,
    Conflict,
    Resolution,
    ResolutionStrategy,
    Severity,
    find_conflict,
    resolve,
)
from yeaboi.provenance.integrity import compute_checksum, verify_checksum
from yeaboi.provenance.records import (
    AGENT_PERSON,
    AGENT_SOFTWARE,
    KIND_DECISION,
    KIND_INVALIDATION,
    ChainBreak,
    ChainVerification,
    DecisionRecord,
)
from yeaboi.provenance.store import ProvenanceChain

__all__ = [
    "AGENT_PERSON",
    "AGENT_SOFTWARE",
    "KIND_DECISION",
    "KIND_INVALIDATION",
    "ChainBreak",
    "ChainVerification",
    "Claim",
    "Conflict",
    "DecisionRecord",
    "ProvenanceChain",
    "Resolution",
    "ResolutionStrategy",
    "Severity",
    "compute_checksum",
    "find_conflict",
    "resolve",
    "verify_checksum",
]
