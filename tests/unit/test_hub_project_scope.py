"""The saved-runs hubs under an active project.

Standup, retro and weekly review filter their store reads to the project's
sessions; reporting, poker and ship cannot (their stores carry no filter) and
say "all runs" instead. Captures the hub's injected callables rather than
running its loop, the same shape as test_solo_hub.py.
"""

from __future__ import annotations

import pytest

from yeaboi.agent.state import DeliveryReport, RetroReport, StandupReport, WeeklyReview
from yeaboi.projects import active


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """Two planning sessions — p1 inside Apollo, p2 loose — each with one run per mode."""
    import yeaboi.ui.mode_select as ms
    from yeaboi.projects.engine import create_project
    from yeaboi.reporting.store import ReportingStore
    from yeaboi.retro.store import RetroStore
    from yeaboi.sessions import SessionStore
    from yeaboi.solo.store import WeeklyReviewStore
    from yeaboi.standup.store import StandupStore

    db = tmp_path / "sessions.db"
    monkeypatch.setattr(ms, "_ana_dbp", db)
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
    pid = create_project("Apollo", db_path=db)["project_id"]
    with SessionStore(db) as store:
        store.create_session("p1", "Apollo", project_id=pid)
        store.create_session("p2", "Loose")
    for sid in ("p1", "p2"):
        with StandupStore(db) as store:
            store.record_run(StandupReport(session_id=sid, date="2026-09-01"))
        with RetroStore(db) as store:
            store.record_run(RetroReport(session_id=sid, date="2026-09-01"))
        with ReportingStore(db) as store:
            store.record_run(DeliveryReport(period_label="Last week"), session_id=sid)
        with WeeklyReviewStore(db) as store:
            store.record_run(WeeklyReview(session_id=sid, week_label="2026-W36"))
    active.set_active_project("")
    yield {"db": db, "pid": pid, "ms": ms}
    active.set_active_project("")


def _capture(ms, hub_fn, monkeypatch) -> dict:
    captured: dict = {}
    monkeypatch.setattr(ms, "_run_mode_hub", lambda *_a, **kw: captured.update(kw))
    hub_fn(object(), object(), lambda **_k: "esc", 0.001, True)
    return captured


@pytest.mark.parametrize(
    ("hub", "subtitle"),
    [
        ("_run_standup_hub", "Saved standups"),
        ("_run_retro_hub", "Saved retros"),
        ("_run_solo_review_hub", "Saved weekly reviews"),
    ],
)
class TestScopedHubs:
    def test_unscoped_lists_every_run(self, world, monkeypatch, hub, subtitle):
        ms = world["ms"]
        captured = _capture(ms, getattr(ms, hub), monkeypatch)
        assert {r.session_id for r in captured["load_runs"]()} == {"p1", "p2"}
        assert captured["subtitle"] == subtitle

    def test_an_active_project_narrows_the_list_and_names_itself(self, world, monkeypatch, hub, subtitle):
        ms = world["ms"]
        active.set_active_project(world["pid"])
        captured = _capture(ms, getattr(ms, hub), monkeypatch)
        assert {r.session_id for r in captured["load_runs"]()} == {"p1"}
        assert captured["subtitle"] == f"{subtitle} — Apollo"


class TestUnscopedHubs:
    def test_reporting_stays_machine_wide_and_says_so(self, world, monkeypatch):
        ms = world["ms"]
        active.set_active_project(world["pid"])
        captured = _capture(ms, ms._run_reporting_hub, monkeypatch)
        assert {r.session_id for r in captured["load_runs"]()} == {"p1", "p2"}
        assert captured["subtitle"] == "Saved reports — all runs"

    def test_no_project_means_the_plain_subtitle(self, world, monkeypatch):
        ms = world["ms"]
        captured = _capture(ms, ms._run_reporting_hub, monkeypatch)
        assert captured["subtitle"] == "Saved reports"


class TestHelpers:
    def test_scope_ids_is_none_without_a_project(self, world):
        assert world["ms"]._scope_ids() is None

    def test_scope_ids_are_the_projects_sessions(self, world):
        active.set_active_project(world["pid"])
        assert world["ms"]._scope_ids() == ("p1",)

    def test_an_unknown_project_reads_as_nothing(self, world):
        active.set_active_project("proj-00000000")
        assert world["ms"]._scope_ids() == ()
        assert world["ms"]._active_project_name() == ""
        assert world["ms"]._hub_subtitle("Saved standups", scoped=True) == "Saved standups"

    def test_active_repo_path(self, world):
        from yeaboi.projects.engine import set_project_defaults

        ms = world["ms"]
        assert ms._active_repo_path() == ""
        active.set_active_project(world["pid"])
        assert ms._active_repo_path() == ""
        set_project_defaults(world["pid"], {"repo_path": "/srv/apollo"}, db_path=world["db"])
        assert ms._active_repo_path() == "/srv/apollo"
