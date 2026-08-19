"""Tests for src/yeaboi/provenance/records.py — the decision record."""

from dataclasses import asdict

from yeaboi.provenance.records import ChainVerification, DecisionRecord


class TestDecisionRecord:
    def test_all_fields_default_and_freeze(self):
        record = DecisionRecord()
        assert record.record_kind == "decision"
        assert record.agent_type == "software_agent"
        assert record.is_automated is True
        assert record.inputs == ()
        assert record.extras == ()

    def test_stamped_returns_a_copy(self):
        record = DecisionRecord(entity_id="e1")
        stamped = record.stamped(sequence_id=3, checksum="abc")
        assert stamped.sequence_id == 3
        assert stamped.checksum == "abc"
        assert record.sequence_id == 0  # the original is untouched

    def test_serializes_round_trip(self):
        record = DecisionRecord(
            entity_id="e1",
            inputs=("a", "b"),
            extras=(("rule", "wip-sprawl"),),
        )
        payload = asdict(record)
        rebuilt = DecisionRecord(
            **{
                **payload,
                "inputs": tuple(payload["inputs"]),
                "extras": tuple(tuple(p) for p in payload["extras"]),
            }
        )
        assert rebuilt == record


class TestChainVerification:
    def test_defaults_are_a_clean_verdict(self):
        verdict = ChainVerification()
        assert verdict.valid is True
        assert verdict.total_records == 0
        assert verdict.broken == ()
