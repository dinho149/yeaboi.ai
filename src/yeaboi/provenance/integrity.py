"""Checksum of one decision record — the per-link half of tamper evidence.

Vendored and adapted from Semantica (https://github.com/semantica-agi/semantica),
``semantica/provenance/integrity.py`` at commit 15171fd3 — Copyright (c) 2026
Hawksight AI, licensed under the MIT License (see THIRD_PARTY_NOTICES.md).
Changes from upstream: fields are joined with an explicit unit separator
instead of bare concatenation (``"a" + "bc"`` and ``"ab" + "c"`` must not
collide), and the hash covers **every** field — upstream excluded
``entity_id`` (to allow archival relabels the yeaboi chain does not do) and
``metadata`` (which left a record's inputs editable without detection).

The checksum covers the record's ``previous_checksum``, which is what turns
per-record hashes into a chain: rewriting any link changes its checksum, which
breaks the link its successor stored.
"""

from __future__ import annotations

import hashlib
import json

from yeaboi.provenance.records import DecisionRecord

# ASCII unit separator between top-level fields — cannot appear in any sane
# scalar value, so scalar fields can never collide across a boundary.
_SEP = "\x1f"


def _composite(value) -> str:
    """Unambiguous encoding for tuple fields (``inputs``, ``extras``).

    JSON, not a joined string: quoting and escaping make every token boundary
    explicit, so an ``extras`` pair cannot trade an ``=`` across its key/value
    boundary, and an empty ``inputs`` token cannot swap places with an
    adjacent scalar field. Separator-joining had exactly those two collisions,
    and a local edit that moves a boundary without changing the bytes hashed
    is precisely this module's threat model.
    """
    return json.dumps([list(v) if isinstance(v, tuple) else v for v in value], ensure_ascii=False)


def canonical_form(record: DecisionRecord) -> str:
    """The exact byte-stable string the checksum is computed over.

    Everything except ``checksum`` itself is included, in a fixed order.
    ``confidence`` goes through ``repr`` so 1.0 and 1 cannot alias; the tuple
    fields go through ``_composite`` so no boundary is malleable.
    """
    parts = (
        record.entity_id,
        record.entity_type,
        record.record_kind,
        record.activity_id,
        record.agent_id,
        record.agent_type,
        repr(bool(record.is_automated)),
        record.role,
        record.source_document,
        record.timestamp,
        repr(float(record.confidence)),
        _composite(record.inputs),
        record.parent_entity_id,
        record.previous_version_id,
        record.derived_from_id,
        record.detail,
        _composite(record.extras),
        repr(int(record.sequence_id)),
        record.previous_checksum,
    )
    return _SEP.join(parts)


def compute_checksum(record: DecisionRecord) -> str:
    """SHA-256 hex digest of the record's canonical form."""
    return hashlib.sha256(canonical_form(record).encode("utf-8")).hexdigest()


def verify_checksum(record: DecisionRecord) -> bool:
    """True when the stored checksum matches the record's content."""
    return bool(record.checksum) and record.checksum == compute_checksum(record)
