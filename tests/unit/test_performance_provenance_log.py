"""Tests for src/yeaboi/performance/provenance_log.py — the compliance trail."""

from yeaboi.agent.state import (
    EngineerActivity,
    EngineerStory,
    OneOnOnePrep,
    OneOnOneRecord,
    SixMonthReview,
)
from yeaboi.performance import provenance_log
from yeaboi.provenance import ProvenanceChain

ACTIVITY = EngineerActivity(
    engineer="Ada",
    stories=(
        EngineerStory(key="YEA-1", title="auth", status="Done", source="jira"),
        EngineerStory(key="YEA-2", title="export", status="In Progress", source="jira"),
    ),
    total_items=2,
)


class TestPrep:
    def test_record_names_the_evidence(self, tmp_path):
        db = tmp_path / "sessions.db"
        prep = OneOnOnePrep(
            engineer="Ada",
            date="2026-08-16",
            talking_points=("YEA-1 shipped", "carry: refactor tests"),
            carried_action_items=("carry: refactor tests",),
        )
        provenance_log.record_prep(db, prep, activity=ACTIVITY, used_llm=True)
        with ProvenanceChain(db) as chain:
            record = chain.get("performance:Ada:prep:2026-08-16")
            assert record is not None
            assert record.entity_type == "one-on-one-prep"
            assert "jira:YEA-1" in record.inputs
            assert "carry: refactor tests" in record.inputs
            assert ("llm", "yes") in record.extras
            assert chain.verify().valid is True

    def test_fallback_prep_is_labelled_fallback(self, tmp_path):
        db = tmp_path / "sessions.db"
        prep = OneOnOnePrep(engineer="Ada", date="2026-08-16")
        provenance_log.record_prep(db, prep, activity=None, used_llm=False)
        with ProvenanceChain(db) as chain:
            record = chain.get("performance:Ada:prep:2026-08-16")
            assert record.agent_id == "performance.prep-fallback"
            assert record.inputs == ()


class TestCompletion:
    def test_transcript_content_never_enters_the_chain(self, tmp_path):
        db = tmp_path / "sessions.db"
        secret = "Ada told me something personal in confidence"
        record = OneOnOneRecord(
            engineer="Ada",
            date="2026-08-16",
            transcript=secret,
            action_items=("follow up on growth plan",),
        )
        provenance_log.record_completion(db, record, used_llm=True)
        with ProvenanceChain(db) as chain:
            stored = chain.get("performance:Ada:one-on-one:2026-08-16")
            assert stored is not None
            assert ("action_items", "1") in stored.extras
            # Counts only — the transcript stays in the performance store.
            import json
            from dataclasses import asdict

            assert secret not in json.dumps(asdict(stored))


class TestReview:
    def test_review_names_every_evidence_stream(self, tmp_path):
        db = tmp_path / "sessions.db"
        review = SixMonthReview(
            engineer="Ada",
            period_start="2026-02-16",
            period_end="2026-08-16",
            framework_used="default ladder",
        )
        provenance_log.record_review(
            db,
            review,
            delivery=ACTIVITY,
            one_on_one_dates=("2026-05-01", "2026-07-01"),
            used_llm=True,
        )
        with ProvenanceChain(db) as chain:
            record = chain.get("performance:Ada:review:2026-08-16")
            assert "jira:YEA-2" in record.inputs
            assert "performance:Ada:one-on-one:2026-05-01" in record.inputs
            assert ("framework", "default ladder") in record.extras
            assert chain.verify().valid is True
