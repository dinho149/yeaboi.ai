"""Unit tests for the Performance → Planning/Analysis context feed."""

from yeaboi.agent.state import OneOnOneRecord, SixMonthReview
from yeaboi.performance import context
from yeaboi.performance.store import PerformanceStore


class TestGatherPerformanceContext:
    def test_empty_when_no_db(self, monkeypatch, tmp_path):
        monkeypatch.setattr("yeaboi.config.get_sessions_db", lambda: tmp_path / "missing.db")
        ctx = context.gather_performance_context()
        assert ctx.is_empty
        assert ctx.summary_md == ""

    def test_summarises_open_actions_and_reviews(self, monkeypatch, tmp_path):
        db = tmp_path / "sessions.db"
        with PerformanceStore(db) as store:
            store.record_completion(OneOnOneRecord(engineer="Ada", date="2026-07-12", action_items=("write tests",)))
            store.record_review(SixMonthReview(engineer="Ada", areas_for_improvement=("delegation",)))
        monkeypatch.setattr("yeaboi.config.get_sessions_db", lambda: db)

        ctx = context.gather_performance_context()
        assert not ctx.is_empty
        assert "Open 1:1 action items" in ctx.summary_md
        assert "write tests" in ctx.summary_md
        assert "delegation" in ctx.summary_md
        assert ctx.engineers_with_actions == 1

    def test_engineer_without_open_actions_excluded(self, monkeypatch, tmp_path):
        db = tmp_path / "sessions.db"
        with PerformanceStore(db) as store:
            store.record_completion(OneOnOneRecord(engineer="Ada", date="2026-07-12", action_items=()))
        monkeypatch.setattr("yeaboi.config.get_sessions_db", lambda: db)
        ctx = context.gather_performance_context()
        assert ctx.engineers_with_actions == 0


class TestPerformanceToggle:
    def test_performance_dep_off_yields_an_empty_context(self, monkeypatch, tmp_path):
        from yeaboi.projects.scope import ProjectScope

        db = tmp_path / "sessions.db"
        with PerformanceStore(db) as store:
            store.record_completion(OneOnOneRecord(engineer="Ada", date="2026-07-12", action_items=("write tests",)))
        monkeypatch.setattr("yeaboi.config.get_sessions_db", lambda: db)
        ctx = context.gather_performance_context(scope=ProjectScope("", None, frozenset()))
        assert ctx.is_empty and ctx.summary_md == ""

    def test_scope_without_a_restriction_never_narrows(self, monkeypatch, tmp_path):
        # Engineer-keyed: session narrowing is deliberately ignored.
        from yeaboi.projects.scope import ProjectScope

        db = tmp_path / "sessions.db"
        with PerformanceStore(db) as store:
            store.record_completion(OneOnOneRecord(engineer="Ada", date="2026-07-12", action_items=("write tests",)))
        monkeypatch.setattr("yeaboi.config.get_sessions_db", lambda: db)
        ctx = context.gather_performance_context(scope=ProjectScope("proj-11112222", ("elsewhere",), None))
        assert not ctx.is_empty
