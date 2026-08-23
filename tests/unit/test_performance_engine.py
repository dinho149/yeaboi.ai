"""Unit tests for the Performance engine pipelines (mocked LLM + activity)."""

import json
from datetime import date

import pytest

from yeaboi.agent.state import EngineerActivity, EngineerStory
from yeaboi.performance import engine
from yeaboi.performance.store import PerformanceStore


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sessions.db"


class _FakeResp:
    def __init__(self, content):
        self.content = content
        self.response_metadata = {}


def _patch_llm(monkeypatch, content):
    """Make the engine's single LLM call return ``content`` and report configured."""
    monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
    monkeypatch.setattr("yeaboi.agent.llm.track_usage", lambda resp: None)
    monkeypatch.setattr(
        "yeaboi.agent.llm.get_llm",
        lambda **k: type("L", (), {"invoke": lambda self, m: _FakeResp(content)})(),
    )


def _patch_activity(monkeypatch, stories=(), coverage=(), metrics=(), groups=()):
    """Stub the whole evidence gather — the engine's one deterministic input.

    ``coverage`` lets a test assert the artifact carries what was and was not
    scanned; the default leaves it empty, which is the pre-evidence shape.
    """
    from yeaboi.performance.evidence import EngineerEvidence, SourceCoverage

    monkeypatch.setattr(
        engine.evidence_mod,
        "gather_engineer_evidence",
        lambda engineer, **kw: EngineerEvidence(
            engineer=engineer,
            activity=EngineerActivity(
                engineer=engineer, current_sprint="Sprint 5", stories=tuple(stories), total_items=len(stories)
            ),
            coverage=tuple(SourceCoverage(*row) for row in coverage),
            metrics=tuple(metrics),
            groups=tuple(groups),
        ),
    )


@pytest.fixture(autouse=True)
def _no_export(monkeypatch):
    # Keep tests off the real ~/.scrum-agent export dir.
    monkeypatch.setattr("yeaboi.performance.export.export_artifact", lambda *a, **k: {})


class TestOneOnOnePrep:
    def test_happy_path_parses_llm(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, stories=[EngineerStory(key="P-1", title="auth")])
        _patch_llm(
            monkeypatch,
            json.dumps(
                {
                    "talking_points": ["Discuss auth"],
                    "feedback": ["Great ownership"],
                    "goals": ["Ship v2"],
                    "gaps": [],
                    "improvements": ["Write more tests"],
                    "activity_summary": "Worked on auth.",
                }
            ),
        )
        prep = engine.run_one_on_one_prep("Ada", db_path=db_path, today=date(2026, 7, 12))
        assert prep.talking_points == ("Discuss auth",)
        assert prep.feedback == ("Great ownership",)
        assert prep.activity_summary == "Worked on auth."
        # Persisted.
        with PerformanceStore(db_path) as store:
            assert store.get_latest_prep("Ada") is not None

    def test_carried_actions_always_surface(self, monkeypatch, db_path):
        _patch_activity(monkeypatch)
        # Seed a prior completion with an open action.
        from yeaboi.agent.state import OneOnOneRecord

        with PerformanceStore(db_path) as store:
            store.record_completion(
                OneOnOneRecord(engineer="Ada", date="2026-07-01", action_items=("finish migration",))
            )
        # LLM drops the carried action — engine must re-add it.
        _patch_llm(monkeypatch, json.dumps({"talking_points": ["something else"]}))
        prep = engine.run_one_on_one_prep("Ada", db_path=db_path, today=date(2026, 7, 12))
        assert "finish migration" in prep.talking_points
        assert prep.carried_action_items == ("finish migration",)

    def test_llm_not_configured_falls_back(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, stories=[EngineerStory(key="P-1", title="auth")])
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        prep = engine.run_one_on_one_prep("Ada", db_path=db_path, today=date(2026, 7, 12))
        assert prep.warnings and "no key" in prep.warnings[0]
        assert prep.talking_points  # deterministic points present

    def test_code_fence_response_parses(self, monkeypatch, db_path):
        _patch_activity(monkeypatch)
        fenced = "```json\n" + json.dumps({"talking_points": ["x"]}) + "\n```"
        _patch_llm(monkeypatch, fenced)
        prep = engine.run_one_on_one_prep("Ada", db_path=db_path, today=date(2026, 7, 12))
        assert prep.talking_points == ("x",)


class TestCompletion:
    def test_happy_path_and_action_items_persist(self, monkeypatch, db_path):
        _patch_llm(
            monkeypatch,
            json.dumps(
                {
                    "email_subject": "1:1 follow-up",
                    "email_summary": "Hi Ada, great chat.",
                    "action_items": ["Book design review"],
                    "highlights": ["Discussed growth"],
                }
            ),
        )
        record = engine.complete_one_on_one("Ada", "we talked", db_path=db_path, deliver=False, today=date(2026, 7, 12))
        assert record.action_items == ("Book design review",)
        # Flows into the next prep's carried actions.
        with PerformanceStore(db_path) as store:
            assert store.get_open_action_items("Ada") == ("Book design review",)

    def test_empty_transcript_short_circuits(self, monkeypatch, db_path):
        record = engine.complete_one_on_one("Ada", "   ", db_path=db_path, deliver=False)
        assert "No transcript" in record.warnings[0]

    def test_pasted_images_reach_llm_as_blocks(self, monkeypatch, db_path, tmp_path):
        """images= paths become multimodal content blocks on the single LLM call."""
        img = tmp_path / "whiteboard.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        sent_content = {}

        class _L:
            def invoke(self, messages):
                sent_content["content"] = messages[0].content
                return _FakeResp(json.dumps({"email_subject": "s", "email_summary": "b", "action_items": []}))

        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.agent.llm.track_usage", lambda resp: None)
        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **k: _L())

        record = engine.complete_one_on_one(
            "Ada", "we talked", db_path=db_path, deliver=False, today=date(2026, 7, 12), images=[str(img)]
        )
        assert not record.warnings
        content = sent_content["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image"

    def test_missing_image_file_degrades_to_text(self, monkeypatch, db_path, tmp_path):
        """A deleted screenshot file falls back to the plain-string prompt."""
        sent_content = {}

        class _L:
            def invoke(self, messages):
                sent_content["content"] = messages[0].content
                return _FakeResp(json.dumps({"email_subject": "s", "email_summary": "b", "action_items": []}))

        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.agent.llm.track_usage", lambda resp: None)
        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **k: _L())

        engine.complete_one_on_one(
            "Ada", "we talked", db_path=db_path, deliver=False, images=[str(tmp_path / "gone.png")]
        )
        assert isinstance(sent_content["content"], str)

    def test_llm_failure_keeps_transcript(self, monkeypatch, db_path):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.agent.llm.track_usage", lambda resp: None)

        def boom(self, m):
            raise RuntimeError("timeout")

        monkeypatch.setattr("yeaboi.agent.llm.get_llm", lambda **k: type("L", (), {"invoke": boom})())
        record = engine.complete_one_on_one(
            "Ada", "notes here", db_path=db_path, deliver=False, today=date(2026, 7, 12)
        )
        assert record.transcript == "notes here"
        assert record.warnings


class TestReview:
    def test_happy_path_parses(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, stories=[EngineerStory(key="P-1", title="auth", status="Done")])
        _patch_llm(
            monkeypatch,
            json.dumps(
                {
                    "strengths": ["Technical depth"],
                    "areas_for_improvement": ["Delegation"],
                    "achievements": ["Shipped auth"],
                    "goals": ["Lead a project"],
                    "overall": "Strong contributor.",
                }
            ),
        )
        review = engine.run_six_month_review("Ada", db_path=db_path, today=date(2026, 7, 12))
        assert review.strengths == ("Technical depth",)
        assert review.overall == "Strong contributor."
        assert review.framework_used == "default"
        assert review.period_start and review.period_end

    def test_llm_unavailable_falls_back(self, monkeypatch, db_path):
        _patch_activity(monkeypatch)
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))
        review = engine.run_six_month_review("Ada", db_path=db_path, today=date(2026, 7, 12))
        assert review.warnings
        assert review.framework_used == "default"


class TestProvenanceWiring:
    def test_prep_appends_a_chained_record(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, stories=[EngineerStory(key="P-1", title="auth", source="jira")])
        _patch_llm(monkeypatch, json.dumps({"talking_points": ["Discuss auth"]}))
        engine.run_one_on_one_prep("Ada", db_path=db_path, today=date(2026, 7, 12))

        from yeaboi.provenance import ProvenanceChain

        with ProvenanceChain(db_path) as chain:
            record = chain.get("performance:Ada:prep:2026-07-12")
            assert record is not None
            assert "jira:P-1" in record.inputs
            assert chain.verify().valid is True

    def test_failed_audit_write_warns_but_never_fails_the_run(self, monkeypatch, db_path):
        _patch_activity(monkeypatch)
        _patch_llm(monkeypatch, json.dumps({"talking_points": ["x"]}))
        from yeaboi.performance import provenance_log

        def _boom(*args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(provenance_log, "record_prep", _boom)
        prep = engine.run_one_on_one_prep("Ada", db_path=db_path, today=date(2026, 7, 12))
        assert any("Audit trail not recorded" in w for w in prep.warnings)

    def test_review_links_the_one_on_one_history(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, stories=[EngineerStory(key="P-2", title="export", source="jira")])
        _patch_llm(monkeypatch, json.dumps({"email_subject": "s", "email_summary": "b", "action_items": ["a"]}))
        engine.complete_one_on_one("Ada", "we talked", deliver=False, db_path=db_path, today=date(2026, 5, 1))

        _patch_llm(monkeypatch, json.dumps({"strengths": ["delivery"], "overall": "solid"}))
        engine.run_six_month_review("Ada", db_path=db_path, today=date(2026, 7, 12))

        from yeaboi.provenance import ProvenanceChain

        with ProvenanceChain(db_path) as chain:
            review = chain.get("performance:Ada:review:2026-07-12")
            assert review is not None
            assert "performance:Ada:one-on-one:2026-05-01" in review.inputs
            assert "jira:P-2" in review.inputs
            assert chain.verify().valid is True


class TestEvidenceStamp:
    """What ``_with_evidence`` puts on an artifact, and what it lets a page say."""

    def test_the_numbers_and_rows_reach_the_artifact(self, monkeypatch, db_path):
        from yeaboi.agent.state import ActivityEvidence, EvidenceGroup, PerfMetric

        metric = PerfMetric(key="spill_rate", label="Spill rate", value=18.0, unit="%", source="analysis")
        group = EvidenceGroup(source="code", label="Code", items=(ActivityEvidence(kind="pr", key="#91"),))
        _patch_activity(monkeypatch, stories=(EngineerStory(key="YB-1"),), metrics=(metric,), groups=(group,))
        _patch_llm(monkeypatch, json.dumps({"talking_points": ["a"]}))

        prep = engine.run_one_on_one_prep("Ada", db_path=db_path, today=date(2026, 7, 12))
        assert prep.metrics == (metric,)
        assert prep.evidence_items == (group,)
        assert prep.activity.total_items == 1

    def test_a_fallback_artifact_still_says_what_was_scanned(self, monkeypatch, db_path):
        # A run with no model is exactly when the lead most needs to know.
        from yeaboi.agent.state import PerfMetric

        metric = PerfMetric(key="spill_rate", value=18.0)
        _patch_activity(monkeypatch, coverage=(("retro", "failed", "unreadable"),), metrics=(metric,))
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "no key"))

        prep = engine.run_one_on_one_prep("Ada", db_path=db_path, today=date(2026, 7, 12))
        assert prep.metrics == (metric,)
        assert ("retro", "failed", "unreadable") in prep.evidence_coverage

    def test_an_empty_section_inherits_the_state_of_the_source_that_would_have_fed_it(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, coverage=(("analysis", "not_configured", "No saved team analysis."),))
        _patch_llm(monkeypatch, json.dumps({"talking_points": ["a"]}))

        prep = engine.run_one_on_one_prep("Ada", db_path=db_path, today=date(2026, 7, 12))
        states = dict((s, st) for s, st, _ in prep.section_states)
        assert states["talking_points"] == "covered"  # it produced items
        assert states["gaps"] == "not_configured"  # nobody looked — not "nothing found"

    def test_a_populated_section_is_covered_whatever_its_sources_did(self, monkeypatch, db_path):
        _patch_activity(monkeypatch, coverage=(("analysis", "failed", "unreadable"),))
        _patch_llm(monkeypatch, json.dumps({"talking_points": ["a"], "gaps": ["thin on deploys"]}))

        prep = engine.run_one_on_one_prep("Ada", db_path=db_path, today=date(2026, 7, 12))
        assert dict((s, st) for s, st, _ in prep.section_states)["gaps"] == "covered"


class TestCompletionCarriesTheEvidence:
    def test_the_summary_cites_the_prep_it_followed(self, monkeypatch, db_path):
        from yeaboi.agent.state import PerfMetric

        metric = PerfMetric(key="spill_rate", value=18.0)
        _patch_activity(monkeypatch, metrics=(metric,), coverage=(("code", "covered", "all of it"),))
        _patch_llm(monkeypatch, json.dumps({"talking_points": ["a"]}))
        engine.run_one_on_one_prep("Ada", db_path=db_path, today=date(2026, 7, 12))

        _patch_llm(monkeypatch, json.dumps({"email_summary": "ok", "action_items": ["x"]}))
        record = engine.complete_one_on_one("Ada", "we talked", db_path=db_path, deliver=False, today=date(2026, 7, 12))
        assert record.metrics == (metric,)
        assert ("code", "covered", "all of it") in record.evidence_coverage

    def test_no_prior_prep_leaves_the_evidence_empty_rather_than_invented(self, monkeypatch, db_path):
        _patch_activity(monkeypatch)
        _patch_llm(monkeypatch, json.dumps({"email_summary": "ok"}))
        record = engine.complete_one_on_one("Ada", "we talked", db_path=db_path, deliver=False, today=date(2026, 7, 12))
        assert record.metrics == ()
        assert record.evidence_coverage == ()


class TestCarriedEvidenceIsBounded:
    """``get_latest_prep`` knows nothing about which meeting a prep was for.

    Unbounded, a 1:1 run months after the last prep inherits that prep's numbers
    and evidence rows and prints them as facts about today — in the artifact that
    gets emailed to the engineer.
    """

    @staticmethod
    def _prep(date_str: str):
        from yeaboi.agent.state import OneOnOnePrep, PerfMetric

        return OneOnOnePrep(
            engineer="Ada",
            date=date_str,
            metrics=(PerfMetric(key="tickets_total", label="Tickets worked", value=12.0),),
            evidence_sources=("tickets",),
            section_states=(("gaps", "partial", "Only two months were scanned."),),
        )

    @staticmethod
    def _record(date_str: str = "2026-07-12"):
        from yeaboi.agent.state import OneOnOneRecord

        return OneOnOneRecord(engineer="Ada", date=date_str)

    def test_a_recent_prep_is_carried_and_dated(self):
        got = engine._carry_evidence(self._record(), self._prep("2026-07-10"))

        assert got.metrics and got.evidence_sources == ("tickets",)
        assert got.evidence_date == "2026-07-10", "the reader must be told when the scan was taken"

    def test_a_stale_prep_is_not_carried_at_all(self):
        got = engine._carry_evidence(self._record(), self._prep("2026-01-05"))

        assert got.metrics == () and got.evidence_sources == ()
        assert got.evidence_date == ""

    def test_a_prep_dated_after_the_meeting_is_not_this_meetings_prep(self):
        got = engine._carry_evidence(self._record(), self._prep("2026-08-01"))

        assert got.metrics == ()

    def test_an_unreadable_prep_date_is_not_carried(self):
        got = engine._carry_evidence(self._record(), self._prep("not-a-date"))

        assert got.metrics == (), "a prep we cannot date is a prep we cannot claim is this one's"

    def test_no_prep_at_all_is_left_alone(self):
        record = self._record()

        assert engine._carry_evidence(record, None) is record

    def test_the_preps_section_states_are_not_carried(self):
        got = engine._carry_evidence(self._record(), self._prep("2026-07-10"))

        assert not hasattr(got, "section_states"), "their keys are the prep's sections; nothing on a record reads them"


class TestWithinDays:
    def test_the_window_is_inclusive_at_both_ends(self):
        assert engine._within_days("2026-07-01", "2026-07-01", 45)
        assert engine._within_days("2026-06-01", "2026-07-16", 45)
        assert not engine._within_days("2026-06-01", "2026-07-17", 45)

    def test_out_of_order_is_false(self):
        assert not engine._within_days("2026-07-02", "2026-07-01", 45)

    def test_garbage_is_false_not_an_exception(self):
        assert not engine._within_days("", "2026-07-01", 45)
        assert not engine._within_days("2026-07-01", "yesterday", 45)
