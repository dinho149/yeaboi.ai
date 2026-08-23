"""Tests for the retro's session resolution (retro/setup.py)."""

from __future__ import annotations

from yeaboi.retro import setup


class FakeStore:
    def __init__(self, *, session_id="", meta=None, state=None, raises=False) -> None:
        self._session_id = session_id
        self._meta = meta
        self._state = state
        self._raises = raises

    def __enter__(self):
        if self._raises:
            raise RuntimeError("database is locked")
        return self

    def __exit__(self, *_):
        return False

    def get_latest_session_id(self):
        return self._session_id

    def get_session(self, _session_id):
        return self._meta

    def load_state(self, _session_id):
        return self._state


def _patch(monkeypatch, store):
    import yeaboi.sessions as sessions

    monkeypatch.setattr(sessions, "SessionStore", lambda *_a, **_k: store)


class TestResolveSession:
    def test_no_session_is_falsy_and_empty(self, monkeypatch, tmp_path):
        _patch(monkeypatch, FakeStore())
        target = setup.resolve_session(db_path=tmp_path / "db")
        assert not target
        assert target.session_id == ""

    def test_reads_the_project_and_sprint_off_the_state(self, monkeypatch, tmp_path):
        _patch(
            monkeypatch,
            FakeStore(
                session_id="s1",
                meta={"project_name": "Apollo", "created_at": "2026-08-01T09:00:00"},
                state={"project_name": "Apollo", "sprint_name": "Sprint 3"},
            ),
        )
        target = setup.resolve_session(db_path=tmp_path / "db")
        assert target
        assert target.session_id == "s1"
        assert target.project_name == "Apollo"
        assert target.sprint_name == "Sprint 3"

    def test_project_falls_back_to_the_display_name(self, monkeypatch, tmp_path):
        meta = {"project_name": "Apollo", "created_at": "2026-08-01T09:00:00"}
        _patch(monkeypatch, FakeStore(session_id="s1", meta=meta, state={}))
        assert setup.resolve_session(db_path=tmp_path / "db").project_name == "apollo-2026-08-01"

    def test_session_name_falls_back_to_the_id(self, monkeypatch, tmp_path):
        _patch(monkeypatch, FakeStore(session_id="s1", meta=None, state={}))
        assert setup.resolve_session(db_path=tmp_path / "db").session_name == "s1"

    def test_a_missing_sprint_is_blank_not_none(self, monkeypatch, tmp_path):
        _patch(monkeypatch, FakeStore(session_id="s1", meta={"session_id": "s1"}, state={"sprint_name": None}))
        assert setup.resolve_session(db_path=tmp_path / "db").sprint_name == ""

    def test_a_broken_store_costs_a_notice_not_a_traceback(self, monkeypatch, tmp_path):
        _patch(monkeypatch, FakeStore(raises=True))
        assert not setup.resolve_session(db_path=tmp_path / "db")

    def test_as_dict_carries_all_four_names(self, monkeypatch, tmp_path):
        _patch(monkeypatch, FakeStore(session_id="s1", meta={"session_id": "s1"}, state={"sprint_name": "S"}))
        assert set(setup.resolve_session(db_path=tmp_path / "db").as_dict()) == {
            "session_id",
            "session_name",
            "project_name",
            "sprint_name",
        }


def test_no_session_message_points_at_planning():
    assert "Planning" in setup.NO_SESSION_MESSAGE
