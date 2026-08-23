"""Reading a stored artifact back by reference (sharing/resolve.py)."""

from __future__ import annotations

import pytest

from yeaboi.agent.state import MemberUpdate, RetroReport, StandupReport
from yeaboi.sharing import resolve


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A real sessions.db, so the stores create their own schema."""
    import yeaboi.paths as paths

    path = tmp_path / "sessions.db"
    monkeypatch.setattr(paths, "get_db_path", lambda: path)
    return path


def _standup(**kw) -> StandupReport:
    defaults = {
        "session_id": "s1",
        "date": "2026-07-10",
        "team_summary": "steady",
        "member_updates": (MemberUpdate(name="Ada", summary="login page"),),
    }
    return StandupReport(**{**defaults, **kw})


class TestKinds:
    def test_an_unknown_kind_is_a_caller_bug(self, db):
        with pytest.raises(ValueError, match="cannot be resolved"):
            resolve.load("roadmap", session_id="s1")

    def test_poker_exports_and_nothing_else(self):
        """The estimates go back to the tracker rather than out as a page, so
        the capability table says so instead of leaving a surface to discover
        it by being refused."""
        poker = next(row for row in resolve.capabilities() if row["kind"] == "poker")
        assert poker["export"] and not poker["share"]
        assert "poker" not in resolve.SHAREABLE_KINDS

    def test_a_poker_share_document_is_refused_by_name(self):
        stub = resolve.Resolved(kind="poker", artifact=object(), title="t", project_name="p")
        with pytest.raises(ValueError, match="exports instead"):
            resolve.document(stub)

    def test_a_team_profile_is_read_only(self):
        analysis = next(row for row in resolve.capabilities() if row["kind"] == "analysis")
        assert analysis["share"] and not analysis["edit"]

    def test_every_resolvable_kind_has_a_capability_row(self):
        assert {row["kind"] for row in resolve.capabilities()} == set(resolve.RESOLVABLE_KINDS)

    def test_a_missing_database_is_nothing_to_act_on(self, tmp_path, monkeypatch):
        import yeaboi.paths as paths

        monkeypatch.setattr(paths, "get_db_path", lambda: tmp_path / "absent.db")
        assert resolve.load("standup", session_id="s1") is None


class TestStandup:
    def test_a_missing_run_is_none(self, db):
        from yeaboi.standup.store import StandupStore

        with StandupStore(db):
            pass
        assert resolve.load("standup", session_id="s1") is None

    def test_resolves_the_stored_run(self, db):
        from yeaboi.standup.store import StandupStore

        with StandupStore(db) as store:
            run_id = store.record_run(_standup())
        resolved = resolve.load("standup", session_id="s1")
        assert resolved is not None
        assert resolved.kind == "standup"
        assert resolved.run_id == run_id
        assert resolved.title == "Daily Standup — 2026-07-10"
        assert resolved.editable

    def test_the_markdown_is_the_export_markdown(self, db):
        from yeaboi.standup.store import StandupStore

        with StandupStore(db) as store:
            store.record_run(_standup())
        text = resolve.markdown(resolve.load("standup", session_id="s1"))
        assert "Ada" in text

    def test_a_run_with_no_id_is_not_editable(self):
        stub = resolve.Resolved(kind="standup", artifact=object(), title="t", project_name="p", run_id=0)
        assert not stub.editable

    def test_editable_session_is_none_for_a_read_only_artifact(self, db):
        stub = resolve.Resolved(kind="analysis", artifact=object(), title="t", project_name="p")
        assert resolve.editable_session(stub, db_path=db) is None


class TestRetro:
    def test_resolves_and_titles_by_sprint(self, db):
        from yeaboi.retro.store import RetroStore

        report = RetroReport(session_id="s1", date="2026-07-10", sprint_name="Sprint 7")
        with RetroStore(db) as store:
            run_id = store.record_run(report)
        resolved = resolve.load("retro", session_id="s1")
        assert resolved.run_id == run_id
        assert resolved.title == "Retro — Sprint 7"

    def test_falls_back_to_the_date_when_there_is_no_sprint(self, db):
        from yeaboi.retro.store import RetroStore

        with RetroStore(db) as store:
            store.record_run(RetroReport(session_id="s1", date="2026-07-10"))
        assert resolve.load("retro", session_id="s1").title == "Retro — 2026-07-10"


class TestBaseRun:
    def test_a_correction_does_not_move_the_anchor(self, db):
        """The log is recorded against the generated run and replayed onto it —
        anchoring to a corrected row would replay every earlier correction a
        second time."""
        from yeaboi.standup.store import StandupStore

        with StandupStore(db) as store:
            base_id = store.record_run(_standup())
            store.record_run(_standup(team_summary="corrected"), origin="edited", edited_from_id=base_id)
        assert resolve.load("standup", session_id="s1").run_id == base_id
