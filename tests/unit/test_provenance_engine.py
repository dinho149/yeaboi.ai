"""Tests for src/yeaboi/provenance/engine.py — the provenance audit."""

import sqlite3
from datetime import date

from yeaboi.provenance import DecisionRecord, ProvenanceChain
from yeaboi.provenance.engine import run_provenance_audit, trace_entity

TODAY = date(2026, 8, 16)


def _seed(db, *records):
    with ProvenanceChain(db) as chain:
        chain.append_all(list(records))


def _decision(entity_id, **overrides):
    base = {
        "entity_id": entity_id,
        "entity_type": "practice-signal",
        "activity_id": "standup-run:s1",
        "agent_id": "habits.wip-sprawl",
        "timestamp": "2026-08-10T10:00:00+00:00",
    }
    base.update(overrides)
    return DecisionRecord(**base)


class TestAudit:
    def test_clean_chain_reports_intact(self, tmp_path):
        db = tmp_path / "sessions.db"
        _seed(db, _decision("e1"), _decision("e2", entity_type="conflict"))
        report = run_provenance_audit(window_days=30, db_path=db, today=TODAY)
        assert report.chain_valid is True
        assert report.total_records == 2
        assert report.window_records == 2
        assert dict(report.records_by_type) == {"practice-signal": 1, "conflict": 1}
        assert report.breaks == ()
        assert {r.entity_id for r in report.recent} == {"e1", "e2"}

    def test_window_filters_but_verification_covers_everything(self, tmp_path):
        db = tmp_path / "sessions.db"
        _seed(
            db,
            _decision("old", timestamp="2026-01-01T00:00:00+00:00"),
            _decision("new", timestamp="2026-08-10T00:00:00+00:00"),
        )
        report = run_provenance_audit(window_days=30, db_path=db, today=TODAY)
        assert report.total_records == 2
        assert report.window_records == 1
        assert [r.entity_id for r in report.recent] == ["new"]

    def test_tampered_chain_fails_loudly(self, tmp_path):
        db = tmp_path / "sessions.db"
        _seed(db, _decision("e1"), _decision("e2"))
        conn = sqlite3.connect(db)
        conn.execute("UPDATE provenance_records SET detail = 'edited' WHERE entity_id = 'e1'")
        conn.commit()
        conn.close()
        report = run_provenance_audit(window_days=30, db_path=db, today=TODAY)
        assert report.chain_valid is False
        assert any(reason == "checksum_mismatch" for _, _, reason in report.breaks)
        assert any("FAILED" in w for w in report.warnings)

    def test_empty_chain_warns_and_stays_valid(self, tmp_path):
        report = run_provenance_audit(window_days=30, db_path=tmp_path / "sessions.db", today=TODAY)
        assert report.chain_valid is True
        assert report.total_records == 0
        assert any("No decisions recorded" in w for w in report.warnings)

    def test_recent_cap_is_announced_not_silent(self, tmp_path):
        db = tmp_path / "sessions.db"
        _seed(db, *[_decision(f"e{i}") for i in range(60)])
        report = run_provenance_audit(window_days=30, db_path=db, today=TODAY)
        assert len(report.recent) == 50
        assert report.window_records == 60
        assert any("newest 50 of 60" in w for w in report.warnings)


class TestTrace:
    def test_trail_follows_evidence(self, tmp_path):
        db = tmp_path / "sessions.db"
        _seed(
            db,
            _decision("evidence:pr:41", entity_type="evidence"),
            _decision("signal:s1", inputs=("evidence:pr:41",), detail="five changes in flight"),
        )
        trace = trace_entity("signal:s1", db_path=db)
        assert trace.found is True
        assert {r.entity_id for r in trace.records} == {"signal:s1", "evidence:pr:41"}

    def test_unknown_entity_reports_not_found(self, tmp_path):
        trace = trace_entity("ghost", db_path=tmp_path / "sessions.db")
        assert trace.found is False
        assert trace.records == ()
        assert any("No decision recorded" in w for w in trace.warnings)
