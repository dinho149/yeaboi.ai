"""Tests for src/yeaboi/provenance/store.py — the hash-chained decision log."""

import sqlite3

import pytest

from yeaboi.provenance.records import DecisionRecord
from yeaboi.provenance.store import ProvenanceChain


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sessions.db"


@pytest.fixture
def chain(db_path):
    with ProvenanceChain(db_path) as c:
        yield c


def _decision(entity_id: str, **overrides) -> DecisionRecord:
    base = {
        "entity_id": entity_id,
        "entity_type": "practice-signal",
        "activity_id": "standup-run:r1",
        "agent_id": "wip-sprawl",
        "detail": f"decision about {entity_id}",
    }
    base.update(overrides)
    return DecisionRecord(**base)


class TestAppend:
    def test_stamps_sequence_timestamp_and_checksum(self, chain):
        stored = chain.append(_decision("e1"))
        assert stored.sequence_id == 1
        assert stored.previous_checksum == ""
        assert len(stored.checksum) == 64
        assert stored.timestamp  # UTC now was stamped

    def test_sequence_ids_are_monotonic_and_linked(self, chain):
        first = chain.append(_decision("e1"))
        second = chain.append(_decision("e2"))
        assert second.sequence_id == 2
        assert second.previous_checksum == first.checksum

    def test_second_record_for_an_entity_links_its_prior_version(self, chain):
        first = chain.append(_decision("e1"))
        second = chain.append(_decision("e1", detail="revised"))
        assert second.previous_version_id == f"seq:{first.sequence_id}"
        assert chain.get("e1").detail == "revised"

    def test_append_all_is_one_batch(self, chain):
        stored = chain.append_all([_decision("e1"), _decision("e2"), _decision("e3")])
        assert [r.sequence_id for r in stored] == [1, 2, 3]
        assert chain.total() == 3

    def test_append_all_empty_is_a_no_op(self, chain):
        assert chain.append_all([]) == []
        assert chain.total() == 0

    def test_caller_timestamp_is_preserved(self, chain):
        stored = chain.append(_decision("e1", timestamp="2026-08-16T10:00:00+00:00"))
        assert stored.timestamp == "2026-08-16T10:00:00+00:00"


class TestInvalidation:
    def test_tombstone_appends_and_marks(self, chain):
        chain.append(_decision("e1"))
        tombstone = chain.invalidate("e1", agent_id="alice", reason="wrong ticket", agent_type="person")
        assert tombstone.record_kind == "invalidation"
        assert chain.is_invalidated("e1")
        # The original is still in the chain — a tombstone, never a delete.
        assert len(chain.history("e1")) == 2

    def test_unknown_entity_raises(self, chain):
        with pytest.raises(ValueError):
            chain.invalidate("ghost", agent_id="alice")

    def test_invalidate_does_not_break_chain(self, chain):
        chain.append(_decision("e1"))
        chain.append(_decision("e2"))
        chain.invalidate("e1", agent_id="alice")
        verdict = chain.verify()
        assert verdict.valid is True
        assert verdict.total_records == 3


class TestReads:
    def test_get_missing_returns_none(self, chain):
        assert chain.get("ghost") is None
        assert chain.is_invalidated("ghost") is False

    def test_history_is_oldest_first(self, chain):
        chain.append(_decision("e1", detail="v1"))
        chain.append(_decision("e2"))
        chain.append(_decision("e1", detail="v2"))
        details = [r.detail for r in chain.history("e1")]
        assert details == ["v1", "v2"]

    def test_trace_follows_inputs(self, chain):
        chain.append(_decision("evidence:pr:42", entity_type="evidence"))
        chain.append(_decision("signal:s1", inputs=("evidence:pr:42",)))
        trail = chain.trace("signal:s1")
        entity_ids = {r.entity_id for r in trail}
        assert entity_ids == {"signal:s1", "evidence:pr:42"}

    def test_trace_depth_caps_the_walk(self, chain):
        chain.append(_decision("a", inputs=("b",)))
        chain.append(_decision("b", inputs=("c",)))
        chain.append(_decision("c"))
        shallow = {r.entity_id for r in chain.trace("a", depth=1)}
        assert shallow == {"a"}

    def test_records_filters_by_type_and_since(self, chain):
        chain.append(_decision("e1", timestamp="2026-08-10T00:00:00+00:00"))
        chain.append(_decision("e2", entity_type="conflict", timestamp="2026-08-15T00:00:00+00:00"))
        assert [r.entity_id for r in chain.records(entity_type="conflict")] == ["e2"]
        assert [r.entity_id for r in chain.records(since="2026-08-12")] == ["e2"]

    def test_counts_by_type(self, chain):
        chain.append(_decision("e1"))
        chain.append(_decision("e2"))
        chain.append(_decision("e3", entity_type="conflict"))
        assert chain.counts_by_type() == {"practice-signal": 2, "conflict": 1}


class TestVerify:
    """The tamper scenarios, ported from semantica's TestHashChain plus the
    in-place-edit case their suite never covered."""

    def _seed(self, chain, n=3):
        for i in range(1, n + 1):
            chain.append(_decision(f"e{i}"))

    def test_clean_history_verifies(self, chain):
        self._seed(chain, 5)
        chain.append(_decision("e1", detail="revised"))  # re-decision, same entity
        verdict = chain.verify()
        assert verdict.valid is True
        assert verdict.total_records == 6
        assert verdict.broken == ()

    def test_empty_chain_is_valid(self, chain):
        verdict = chain.verify()
        assert verdict.valid is True
        assert verdict.total_records == 0

    def test_detects_a_deleted_row(self, chain, db_path):
        self._seed(chain)
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM provenance_records WHERE entity_id = 'e2'")
        conn.commit()
        conn.close()
        verdict = chain.verify()
        assert verdict.valid is False
        assert any(b.reason == "chain_break" for b in verdict.broken)

    def test_detects_an_edited_row(self, chain, db_path):
        # The gap in upstream's suite: content edited in place, chain shape intact.
        self._seed(chain)
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE provenance_records SET confidence = 0.1 WHERE entity_id = 'e2'")
        conn.commit()
        conn.close()
        verdict = chain.verify()
        assert verdict.valid is False
        assert any(b.reason == "checksum_mismatch" and b.entity_id == "e2" for b in verdict.broken)

    def test_detects_a_renumbered_row(self, chain, db_path):
        self._seed(chain)
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE provenance_records SET sequence_id = 10 WHERE entity_id = 'e3'")
        conn.commit()
        conn.close()
        verdict = chain.verify()
        assert verdict.valid is False
        assert any(b.expected_sequence_id == 3 for b in verdict.broken)

    def test_one_corrupt_row_reports_once_not_cascading(self, chain, db_path):
        self._seed(chain, 5)
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE provenance_records SET detail = 'tampered' WHERE entity_id = 'e2'")
        conn.commit()
        conn.close()
        verdict = chain.verify()
        # Exactly one checksum_mismatch; the successor's stored link still
        # matches the stored (tampered-over) checksum, so no spurious breaks.
        mismatches = [b for b in verdict.broken if b.reason == "checksum_mismatch"]
        assert len(mismatches) == 1

    def test_missing_first_record_is_a_break(self, chain, db_path):
        self._seed(chain)
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM provenance_records WHERE sequence_id = 1")
        conn.commit()
        conn.close()
        verdict = chain.verify()
        assert verdict.valid is False
        assert any(b.expected_sequence_id == 1 for b in verdict.broken)


class TestSharedDatabase:
    def test_coexists_with_the_session_store(self, tmp_path):
        # The chain's tables are additive to the shared sessions.db: opening a
        # SessionStore first, then the chain, must not disturb either schema.
        from yeaboi.sessions import SessionStore

        db = tmp_path / "sessions.db"
        store = SessionStore(db)
        assert store.schema_mismatch is False
        store.close()
        with ProvenanceChain(db) as chain:
            chain.append(_decision("e1"))
            assert chain.verify().valid is True
        # Reopening the session store still sees a current schema.
        store = SessionStore(db)
        assert store.schema_mismatch is False
        store.close()
