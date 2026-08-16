"""Tests for src/yeaboi/provenance/integrity.py — record checksums."""

from yeaboi.provenance.integrity import canonical_form, compute_checksum, verify_checksum
from yeaboi.provenance.records import DecisionRecord


def _record(**overrides) -> DecisionRecord:
    base = {
        "entity_id": "standup:2026-08-16:practice:wip-sprawl:alice",
        "entity_type": "practice-signal",
        "activity_id": "standup-run:r1",
        "agent_id": "wip-sprawl",
        "timestamp": "2026-08-16T10:00:00+00:00",
        "inputs": ("pr:42", "pr:43"),
        "detail": "five changes in flight",
        "sequence_id": 1,
    }
    base.update(overrides)
    return DecisionRecord(**base)


class TestChecksum:
    def test_checksum_is_sha256_hex(self):
        assert len(compute_checksum(_record())) == 64

    def test_verify_round_trip(self):
        record = _record()
        stamped = record.stamped(checksum=compute_checksum(record))
        assert verify_checksum(stamped)

    def test_empty_checksum_never_verifies(self):
        assert not verify_checksum(_record())

    def test_every_field_is_covered(self):
        # Upstream excluded entity_id and metadata from the hash; both
        # exclusions were deliberate there and wrong here. Changing ANY field
        # must change the checksum.
        base = compute_checksum(_record())
        assert compute_checksum(_record(entity_id="other")) != base
        assert compute_checksum(_record(extras=(("k", "v"),))) != base
        assert compute_checksum(_record(inputs=("pr:42",))) != base
        assert compute_checksum(_record(detail="edited")) != base
        assert compute_checksum(_record(confidence=0.5)) != base
        assert compute_checksum(_record(sequence_id=2)) != base
        assert compute_checksum(_record(previous_checksum="x")) != base
        assert compute_checksum(_record(record_kind="invalidation")) != base
        assert compute_checksum(_record(is_automated=False)) != base

    def test_fields_cannot_collide_across_boundaries(self):
        # Bare concatenation would hash ("ab", "c") and ("a", "bc") the same;
        # the unit separator is what prevents it.
        one = compute_checksum(_record(agent_id="ab", agent_type="c"))
        two = compute_checksum(_record(agent_id="a", agent_type="bc"))
        assert one != two

    def test_canonical_form_is_stable(self):
        # The chain's integrity depends on this string never changing shape
        # for the same record.
        assert canonical_form(_record()) == canonical_form(_record())
