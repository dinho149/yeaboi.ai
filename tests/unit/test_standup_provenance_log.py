"""Tests for src/yeaboi/standup/provenance_log.py — the standup audit trail."""

import pytest

from yeaboi.agent.state import ConflictCard, OpsSignal
from yeaboi.ops.events import OpsEvent
from yeaboi.provenance import ProvenanceChain
from yeaboi.standup import provenance_log

RESULT = {
    "practices": {
        "alice": [
            {
                "rule": "wip-sprawl",
                "title": "Sprawling WIP",
                "detail": "five changes in flight",
                "evidence": [["PR #41", "https://g/41"]],
                "repeat": True,
                "handles": ["pr|https://g/41"],
            }
        ],
        "bob": [],
    },
    "blocker_signals": {"alice": ["YEA-9 is in Blocked"]},
    "progress": {
        "sprint_day": 3,
        "sprint_total_days": 10,
        "confidence_pct": 63,
        "confidence_label": "Behind",
        "confidence_rationale": "12 of 40 points done by day 3; quiet day dampened the score.",
        "confidence_delta": -7,
        "confidence_trend": "declining",
    },
}

CARD = ConflictCard(
    fingerprint="YEA-12:status:status_conflict",
    detail="YEA-12 is Done on the board while a pull request is still open.",
    severity="medium",
    entity_id="YEA-12",
    claims=(("jira", "Done", "YEA-12", "https://j/12"), ("github", "open", "fix", "https://g/41")),
)


class TestBuild:
    def test_one_record_per_signal_with_stable_entity_ids(self):
        records = provenance_log.build_decision_records(
            result=RESULT,
            date_str="2026-08-16",
            session_id="s1",
            dropped_case_ids=["case-7"],
            conflict_cards=[CARD],
        )
        by_id = {r.entity_id: r for r in records}
        assert set(by_id) == {
            "standup:2026-08-16:practice:wip-sprawl:alice",
            "standup:2026-08-16:blocker:alice",
            "standup:2026-08-16:confidence",
            "standup:2026-08-16:adjudication:case-7",
            "standup:2026-08-16:conflict:YEA-12:status:status_conflict",
        }
        assert all(r.activity_id == "standup-run:s1:2026-08-16" for r in records)

    def test_practice_record_carries_handles_and_member(self):
        records = provenance_log.build_decision_records(result=RESULT, date_str="2026-08-16", session_id="s1")
        practice = next(r for r in records if r.entity_type == "practice-signal")
        assert practice.agent_id == "habits.wip-sprawl"
        assert practice.role == "generator"
        assert practice.inputs == ("pr|https://g/41",)
        assert ("member", "alice") in practice.extras
        assert ("repeat", "true") in practice.extras

    def test_confidence_record_carries_the_arithmetic(self):
        records = provenance_log.build_decision_records(result=RESULT, date_str="2026-08-16", session_id="s1")
        confidence = next(r for r in records if r.entity_type == "confidence")
        assert confidence.confidence == pytest.approx(0.63)
        assert ("pct", "63") in confidence.extras
        assert ("trend", "declining") in confidence.extras
        assert "dampened" in confidence.detail

    def test_adjudication_drop_is_a_suppressor_record(self):
        records = provenance_log.build_decision_records(
            result=RESULT, date_str="2026-08-16", session_id="s1", dropped_case_ids=["c1"]
        )
        drop = next(r for r in records if r.entity_type == "adjudication-drop")
        assert drop.role == "suppressor"
        assert drop.agent_id == "practice-adjudicator"

    def test_conflict_record_inputs_are_the_claim_refs(self):
        records = provenance_log.build_decision_records(
            result=RESULT, date_str="2026-08-16", session_id="s1", conflict_cards=[CARD]
        )
        conflict = next(r for r in records if r.entity_type == "conflict")
        assert conflict.inputs == ("https://j/12", "https://g/41")
        assert ("severity", "medium") in conflict.extras

    def test_empty_result_builds_nothing(self):
        assert provenance_log.build_decision_records(result={}, date_str="2026-08-16", session_id="s1") == []


class TestRecordRun:
    def test_records_chain_and_verify(self, tmp_path):
        db = tmp_path / "sessions.db"
        count = provenance_log.record_run(
            db,
            result=RESULT,
            date_str="2026-08-16",
            session_id="s1",
            dropped_case_ids=["c1"],
            conflict_cards=[CARD],
        )
        assert count == 5
        with ProvenanceChain(db) as chain:
            verdict = chain.verify()
            assert verdict.valid is True
            assert verdict.total_records == 5
            assert chain.get("standup:2026-08-16:confidence") is not None

    def test_empty_run_writes_nothing(self, tmp_path):
        db = tmp_path / "sessions.db"
        assert provenance_log.record_run(db, result={}, date_str="2026-08-16", session_id="s1") == 0

    def test_second_run_links_prior_versions(self, tmp_path):
        db = tmp_path / "sessions.db"
        for _ in range(2):
            provenance_log.record_run(db, result=RESULT, date_str="2026-08-16", session_id="s1")
        with ProvenanceChain(db) as chain:
            history = chain.history("standup:2026-08-16:confidence")
            assert len(history) == 2
            assert history[1].previous_version_id == f"seq:{history[0].sequence_id}"
            assert chain.verify().valid is True


_OPS_SIGNAL = OpsSignal(
    kind="incident",
    source="pagerduty",
    count=2,
    resolved=1,
    severity="high",
    window_start="2026-06-26T00:00:00+00:00",
    window_end="2026-07-10T00:00:00+00:00",
)
_OPS_EVENTS = (
    OpsEvent(kind="incident", source="pagerduty", ref="PD-1", title="down", url="https://pd/1"),
    OpsEvent(kind="incident", source="pagerduty", ref="PD-2", title="flapping", url="https://pd/2"),
    OpsEvent(kind="alert", source="datadog", ref="DD-9", title="cpu"),
)


def _ops_records(**over):
    kw = dict(result={}, date_str="2026-07-10", session_id="s1", ops_signals=(_OPS_SIGNAL,), ops_events=_OPS_EVENTS)
    kw.update(over)
    return [r for r in provenance_log.build_decision_records(**kw) if r.entity_type == "ops-signal"]


class TestProductionRecords:
    def test_one_record_per_roll_up(self):
        assert len(_ops_records()) == 1

    def test_inputs_are_handles_never_urls(self):
        # A rotated url makes an old record unverifiable; a handle is what the
        # vendor itself will still answer to.
        record = _ops_records()[0]
        assert record.inputs == ("pagerduty:PD-1", "pagerduty:PD-2")
        assert not any("http" in i for i in record.inputs)

    def test_only_this_signals_own_events_ride_along(self):
        assert "datadog:DD-9" not in _ops_records()[0].inputs

    def test_the_window_and_counts_are_on_the_record(self):
        extras = dict(_ops_records()[0].extras)
        assert extras["count"] == "2" and extras["resolved"] == "1" and extras["severity"] == "high"
        assert extras["window_start"] == "2026-06-26T00:00:00+00:00"

    def test_an_uncapped_input_list_says_so(self):
        assert dict(_ops_records()[0].extras)["inputs_capped"] == "false"

    def test_a_capped_one_says_so_too(self):
        # A truncated input list that does not admit it is a chain that lies
        # by omission.
        many = tuple(
            OpsEvent(kind="incident", source="pagerduty", ref=f"PD-{n}", title="x")
            for n in range(provenance_log._INPUT_CAP + 5)
        )
        record = _ops_records(ops_events=many)[0]
        assert len(record.inputs) == provenance_log._INPUT_CAP
        assert dict(record.extras)["inputs_capped"] == "true"

    def test_nothing_connected_records_nothing(self):
        assert _ops_records(ops_signals=(), ops_events=()) == []


class TestWhichDetectorSaidSo:
    def test_a_production_conflict_names_its_own_detector(self):
        cards = (ConflictCard(fingerprint="YEA-9:status:ops_conflict", entity_id="YEA-9"),)
        record = provenance_log.build_decision_records(
            result={}, date_str="2026-07-10", session_id="s1", conflict_cards=cards
        )[0]
        assert record.agent_id == "conflicts.production"

    def test_a_board_conflict_still_names_the_status_detector(self):
        cards = (ConflictCard(fingerprint="YEA-9:status:status_conflict", entity_id="YEA-9"),)
        record = provenance_log.build_decision_records(
            result={}, date_str="2026-07-10", session_id="s1", conflict_cards=cards
        )[0]
        assert record.agent_id == "conflicts.status"
