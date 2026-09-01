"""Tests for ceremony history — feeding Standup + Retro data into Planning &
Analysis (team-wide gather, deterministic cadence/trends/themes, prompt injection,
backlog seeding, and the Analysis-report ceremony section).

See README: "Session Management" — SQLite persistence.
"""

import json
from dataclasses import asdict

from yeaboi.agent.ceremony_history import (
    CeremonyContext,
    _avg_interval_days,
    _confidence_trend,
    _dedup_action_items,
    _describe_cadence,
    _top_themes,
    format_ceremony_history_md,
    gather_ceremony_context,
)
from yeaboi.agent.state import RetroCard, RetroReport, StandupReport
from yeaboi.retro.store import RetroStore
from yeaboi.standup.store import StandupStore

# ── Seeding helpers (insert rows with explicit run_at for deterministic cadence) ──


def _seed_retro(store, session_id, run_at, project_name, cards):
    report = RetroReport(date=run_at[:10], session_id=session_id, project_name=project_name, cards=tuple(cards))
    store._conn.execute(
        "INSERT INTO retro_history (session_id, run_at, retro_date, project_name, card_count, report_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, run_at, run_at[:10], project_name, len(cards), json.dumps(asdict(report))),
    )


def _seed_standup(store, session_id, run_at, confidence_pct, status="success"):
    report = StandupReport(date=run_at[:10], session_id=session_id, confidence_pct=confidence_pct)
    store._conn.execute(
        "INSERT INTO standup_history "
        "(session_id, run_at, standup_date, sprint_day, confidence_pct, report_json, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (session_id, run_at, run_at[:10], 1, confidence_pct, json.dumps(asdict(report)), status),
    )


def _ac(text):
    return RetroCard(grid="action_items", text=text, author="A")


# ── Pure helpers ────────────────────────────────────────────────────────────


class TestCadence:
    def test_no_runs(self):
        assert "no" in _describe_cadence([], "retro")

    def test_single_run(self):
        assert "1 retro" in _describe_cadence(["2026-06-01T10:00:00+00:00"], "retro")

    def test_weekly_cadence(self):
        runs = ["2026-06-01T10:00:00+00:00", "2026-06-15T10:00:00+00:00", "2026-06-29T10:00:00+00:00"]
        out = _describe_cadence(runs, "retro")
        assert "week" in out and "3 retros" in out

    def test_daily_cadence(self):
        runs = ["2026-06-01T09:00:00+00:00", "2026-06-02T09:00:00+00:00", "2026-06-03T09:00:00+00:00"]
        assert "day" in _describe_cadence(runs, "standup")

    def test_avg_interval_needs_two(self):
        assert _avg_interval_days(["2026-06-01T00:00:00+00:00"]) is None
        assert _avg_interval_days([]) is None

    def test_avg_interval_ignores_unparseable(self):
        runs = ["bad", "2026-06-01T00:00:00+00:00", "2026-06-03T00:00:00+00:00"]
        assert _avg_interval_days(runs) == 2.0


class TestConfidenceTrend:
    def test_empty(self):
        assert _confidence_trend([]) == ("", None)

    def test_average_only_when_few(self):
        hist = [{"confidence_pct": 60, "status": "success"}, {"confidence_pct": 80, "status": "success"}]
        phrase, avg = _confidence_trend(hist)
        assert avg == 70 and "70%" in phrase and "improving" not in phrase

    def test_improving(self):
        # newest-first: recent high, older low → improving
        hist = [{"confidence_pct": p, "status": "success"} for p in (85, 80, 60, 55)]
        phrase, _ = _confidence_trend(hist)
        assert "improving" in phrase

    def test_declining(self):
        hist = [{"confidence_pct": p, "status": "success"} for p in (55, 60, 82, 85)]
        phrase, _ = _confidence_trend(hist)
        assert "declining" in phrase


class TestThemesAndActionItems:
    def test_top_themes_counts_recurring(self):
        reports = [
            RetroReport(cards=(RetroCard(grid="didnt_go_well", text="Flaky CI"),)),
            RetroReport(cards=(RetroCard(grid="didnt_go_well", text="flaky ci"),)),  # normalises to same
            RetroReport(cards=(RetroCard(grid="didnt_go_well", text="Slow reviews"),)),  # only once → excluded
        ]
        themes = _top_themes(reports, "didnt_go_well")
        assert themes == (("Flaky CI", 2),)

    def test_dedup_action_items_newest_wins(self):
        reports = [
            RetroReport(cards=(_ac("Fix CI"), _ac("Add dashboards"))),  # newest
            RetroReport(cards=(_ac("fix ci"),)),  # duplicate (normalised) → skipped
        ]
        items = _dedup_action_items(reports)
        assert items == ("Fix CI", "Add dashboards")

    def test_resolved_carried_items_excluded(self):
        # A later retro reviewed the older retro's actions: "Fix CI" is done, "Add
        # dashboards" is not_relevant, "Ship API" is still carried_over. Only the
        # still-open action survives into the Planning feed.
        newest = RetroReport(
            cards=(_ac("Ship API"),),
            carried_action_items=(
                RetroCard(grid="action_items", text="Fix CI", origin="carryover", status="done"),
                RetroCard(grid="action_items", text="Add dashboards", origin="carryover", status="not_relevant"),
            ),
        )
        older = RetroReport(cards=(_ac("Fix CI"), _ac("Add dashboards"), _ac("Ship API")))
        items = _dedup_action_items([newest, older])
        assert items == ("Ship API",)

    def test_open_carried_items_remain(self):
        newest = RetroReport(
            cards=(),
            carried_action_items=(
                RetroCard(grid="action_items", text="Fix CI", origin="carryover", status="carried_over"),
            ),
        )
        older = RetroReport(cards=(_ac("Fix CI"),))
        assert _dedup_action_items([newest, older]) == ("Fix CI",)

    def test_manual_web_action_item_reaches_planning_feed(self):
        # A human-authored action item (origin="web", never AI-generated) must feed
        # Planning like any other — _dedup_action_items is origin-agnostic.
        report = RetroReport(cards=(RetroCard(grid="action_items", text="Update runbook", author="Sam", origin="web"),))
        assert "Update runbook" in _dedup_action_items([report])

    def test_kept_open_carried_item_not_in_grid_is_emitted(self):
        # "Carried Over" recorded only in carried_action_items (team never clicked
        # Generate to re-add it to the grid) must still reach the Planning feed.
        report = RetroReport(
            cards=(_ac("Grid action"),),
            carried_action_items=(
                RetroCard(grid="action_items", text="Kept for next sprint", status="carried_over"),
                RetroCard(grid="action_items", text="Resolved one", status="done"),
            ),
        )
        items = _dedup_action_items([report])
        assert "Grid action" in items
        assert "Kept for next sprint" in items  # open, emitted
        assert "Resolved one" not in items  # done, excluded


class TestFormat:
    def test_empty_context_renders_empty(self):
        assert format_ceremony_history_md(CeremonyContext()) == ""

    def test_non_empty_has_sections(self):
        ctx = CeremonyContext(
            action_items=("Fix CI",),
            retro_count=2,
            didnt_go_well_themes=(("Flaky CI", 3),),
            confidence_trend="70% average confidence, improving",
            retro_cadence="~every 2 week(s) (2 retros)",
            standup_cadence="roughly daily (10 standups)",
        )
        md = format_ceremony_history_md(ctx)
        assert "Open retro action items" in md and "Fix CI" in md
        assert "Flaky CI (3×)" in md
        assert "70% average confidence" in md
        assert "Cadence" in md


# ── gather_ceremony_context (temp DB, team-wide, project-first) ───────────────


class TestGather:
    def _db(self, tmp_path):
        return tmp_path / "sessions.db"

    def _point_config_at(self, monkeypatch, db):
        monkeypatch.setattr("yeaboi.config.get_sessions_db", lambda: db)

    def test_missing_db_is_empty(self, tmp_path, monkeypatch):
        self._point_config_at(monkeypatch, self._db(tmp_path))  # file never created
        ctx = gather_ceremony_context("Alpha")
        assert ctx.is_empty
        assert ctx.summary_md == ""

    def test_gathers_and_distils(self, tmp_path, monkeypatch):
        db = self._db(tmp_path)
        with RetroStore(db) as r:
            runs = ["2026-06-01T10:00:00+00:00", "2026-06-15T10:00:00+00:00", "2026-06-29T10:00:00+00:00"]
            for i, run in enumerate(runs):
                _seed_retro(
                    r,
                    f"s{i}",
                    run,
                    "Alpha",
                    [
                        RetroCard(grid="went_well", text="Good pairing"),
                        RetroCard(grid="didnt_go_well", text="Flaky CI"),
                        _ac("Fix flaky CI"),
                        _ac(f"Add dashboard {i}"),
                    ],
                )
        with StandupStore(db) as s:
            for i, (run, pct) in enumerate(
                [
                    ("2026-06-20T09:00:00+00:00", 60),
                    ("2026-06-21T09:00:00+00:00", 65),
                    ("2026-06-24T09:00:00+00:00", 78),
                    ("2026-06-25T09:00:00+00:00", 82),
                ]
            ):
                _seed_standup(s, "s0", run, pct)

        self._point_config_at(monkeypatch, db)
        ctx = gather_ceremony_context("Alpha")

        assert ctx.retro_count == 3
        assert ctx.standup_count == 4
        # "Fix flaky CI" recurs across all 3 retros but is deduped to one action item.
        assert ctx.action_items.count("Fix flaky CI") == 1
        assert "week" in ctx.retro_cadence
        assert "improving" in ctx.confidence_trend
        assert ("Flaky CI", 3) in ctx.didnt_go_well_themes
        assert "Open retro action items" in ctx.summary_md

    def test_project_first_ordering(self, tmp_path, monkeypatch):
        db = self._db(tmp_path)
        with RetroStore(db) as r:
            # 'Beta' is more recent, but a project-first query for 'Alpha' must surface Alpha.
            _seed_retro(r, "a", "2026-06-01T00:00:00+00:00", "Alpha", [_ac("Alpha item")])
            _seed_retro(r, "b", "2026-07-01T00:00:00+00:00", "Beta", [_ac("Beta item")])
            reports = r.get_recent_reports(limit=1, project_name="Alpha")
        assert reports[0].project_name == "Alpha"

    def test_get_all_history_spans_sessions(self, tmp_path):
        db = self._db(tmp_path)
        with RetroStore(db) as r:
            _seed_retro(r, "a", "2026-06-01T00:00:00+00:00", "Alpha", [_ac("x")])
            _seed_retro(r, "b", "2026-07-01T00:00:00+00:00", "Beta", [_ac("y")])
            hist = r.get_all_history(limit=10)
        assert len(hist) == 2
        assert {h["project_name"] for h in hist} == {"Alpha", "Beta"}

    def test_standup_recent_reports_skips_failed(self, tmp_path):
        db = self._db(tmp_path)
        with StandupStore(db) as s:
            _seed_standup(s, "s0", "2026-06-01T00:00:00+00:00", 70, status="success")
            _seed_standup(s, "s0", "2026-06-02T00:00:00+00:00", 0, status="error")
            reports = s.get_recent_reports(limit=10)
        assert len(reports) == 1


class TestScopedGather:
    """A ProjectScope hard-filters both store reads to the project's sessions."""

    def _seed_two_projects(self, db):
        with RetroStore(db) as r:
            _seed_retro(r, "proj-sess", "2026-06-01T00:00:00+00:00", "Alpha", [_ac("Alpha item")])
            _seed_retro(r, "other-sess", "2026-07-01T00:00:00+00:00", "Beta", [_ac("Beta item")])
        with StandupStore(db) as s:
            _seed_standup(s, "proj-sess", "2026-06-02T00:00:00+00:00", 70)
            _seed_standup(s, "other-sess", "2026-07-02T00:00:00+00:00", 80)

    def test_scope_filters_both_reads(self, tmp_path, monkeypatch):
        from yeaboi.projects.scope import ProjectScope

        db = tmp_path / "sessions.db"
        self._seed_two_projects(db)
        monkeypatch.setattr("yeaboi.config.get_sessions_db", lambda: db)
        ctx = gather_ceremony_context(scope=ProjectScope("proj-11112222", ("proj-sess",)))
        assert ctx.retro_count == 1
        assert ctx.standup_count == 1
        assert ctx.action_items == ("Alpha item",)

    def test_no_scope_is_the_legacy_teamwide_read(self, tmp_path, monkeypatch):
        db = tmp_path / "sessions.db"
        self._seed_two_projects(db)
        monkeypatch.setattr("yeaboi.config.get_sessions_db", lambda: db)
        ctx = gather_ceremony_context()
        assert ctx.retro_count == 2
        assert ctx.standup_count == 2

    def test_empty_scope_reads_nothing(self, tmp_path, monkeypatch):
        from yeaboi.projects.scope import ProjectScope

        db = tmp_path / "sessions.db"
        self._seed_two_projects(db)
        monkeypatch.setattr("yeaboi.config.get_sessions_db", lambda: db)
        ctx = gather_ceremony_context(scope=ProjectScope("proj-11112222", ()))
        assert ctx.retro_count == 0 and ctx.standup_count == 0


class TestContextDepGates:
    """The retro/standup toggles gate each producer's reads; None-deps is byte-identical."""

    def _seed(self, db):
        with RetroStore(db) as r:
            _seed_retro(r, "proj-sess", "2026-06-01T00:00:00+00:00", "Alpha", [_ac("Alpha item")])
        with StandupStore(db) as s:
            _seed_standup(s, "proj-sess", "2026-06-02T00:00:00+00:00", 70)

    def test_retro_off_skips_every_retro_read(self, tmp_path, monkeypatch):
        from yeaboi.projects.scope import ProjectScope

        db = tmp_path / "sessions.db"
        self._seed(db)
        monkeypatch.setattr("yeaboi.config.get_sessions_db", lambda: db)
        ctx = gather_ceremony_context(scope=ProjectScope("proj-11112222", ("proj-sess",), frozenset({"standup"})))
        assert ctx.retro_count == 0
        assert ctx.action_items == ()
        assert ctx.retro_cadence == "no retros recorded"
        assert ctx.standup_count == 1

    def test_standup_off_skips_every_standup_read(self, tmp_path, monkeypatch):
        from yeaboi.projects.scope import ProjectScope

        db = tmp_path / "sessions.db"
        self._seed(db)
        monkeypatch.setattr("yeaboi.config.get_sessions_db", lambda: db)
        ctx = gather_ceremony_context(scope=ProjectScope("proj-11112222", ("proj-sess",), frozenset({"retro"})))
        assert ctx.standup_count == 0
        assert ctx.confidence_trend == ""
        assert ctx.standup_cadence == "no standups recorded"
        assert ctx.retro_count == 1

    def test_incognito_reads_nothing_at_all(self, tmp_path, monkeypatch):
        from yeaboi.projects.scope import ProjectScope

        db = tmp_path / "sessions.db"
        self._seed(db)
        monkeypatch.setattr("yeaboi.config.get_sessions_db", lambda: db)
        ctx = gather_ceremony_context(scope=ProjectScope("", None, frozenset()))
        assert ctx.retro_count == 0 and ctx.standup_count == 0
        assert ctx.summary_md == gather_ceremony_context(scope=ProjectScope("x", (), frozenset())).summary_md

    def test_none_deps_scope_equals_no_scope(self, tmp_path, monkeypatch):
        from yeaboi.projects.scope import ProjectScope

        db = tmp_path / "sessions.db"
        self._seed(db)
        monkeypatch.setattr("yeaboi.config.get_sessions_db", lambda: db)
        unscoped = gather_ceremony_context()
        carrier = gather_ceremony_context(scope=ProjectScope("", None, None))
        assert asdict(unscoped) == asdict(carrier)

    def test_scoped_cadence_and_trend_ignore_foreign_sessions(self, tmp_path, monkeypatch):
        # The pre-existing leak: get_all_history is team-wide, so a scoped run's
        # cadence/trend must post-filter to the project's own sessions.
        from yeaboi.projects.scope import ProjectScope

        db = tmp_path / "sessions.db"
        with StandupStore(db) as s:
            _seed_standup(s, "proj-sess", "2026-06-01T00:00:00+00:00", 40)
            _seed_standup(s, "proj-sess", "2026-06-02T00:00:00+00:00", 80)
            _seed_standup(s, "other-sess", "2026-06-03T00:00:00+00:00", 5)
        monkeypatch.setattr("yeaboi.config.get_sessions_db", lambda: db)
        scoped = gather_ceremony_context(scope=ProjectScope("proj-11112222", ("proj-sess",)))
        # The foreign 5%-confidence run must not drag the trend: the scoped
        # trend ends on the project's own 80% run (rising), not the outlier.
        assert "5%" not in scoped.confidence_trend
