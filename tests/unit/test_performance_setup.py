"""The roster a Performance surface offers — tracker first, saved plan second."""

from __future__ import annotations

from yeaboi.performance import setup


class _Roster:
    def __init__(self, name: str):
        self.name = name


class _Store:
    """A PerformanceStore stand-in over two dicts."""

    def __init__(self, open_actions: dict, reviews: set):
        self._open = open_actions
        self._reviews = reviews

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_all_open_action_items(self):
        return self._open

    def get_latest_review(self, name: str):
        return object() if name in self._reviews else None


class TestRosterHints:
    def _install(self, monkeypatch, open_actions, reviews):
        monkeypatch.setattr(
            "yeaboi.performance.store.PerformanceStore",
            lambda *a, **k: _Store(open_actions, reviews),
        )

    def test_no_roster_is_no_hints(self):
        assert setup.roster_hints([]) == []

    def test_counts_open_actions_per_engineer(self, monkeypatch):
        self._install(monkeypatch, {"Ada": ("a", "b")}, set())
        assert setup.roster_hints(["Ada", "Bob"], db_path=":memory:") == [
            "2 open 1:1 actions",
            "no open 1:1 actions",
        ]

    def test_one_action_is_singular(self, monkeypatch):
        self._install(monkeypatch, {"Ada": ("a",)}, set())
        assert setup.roster_hints(["Ada"], db_path=":memory:") == ["1 open 1:1 action"]

    def test_a_review_on_file_is_appended(self, monkeypatch):
        self._install(monkeypatch, {}, {"Ada"})
        assert setup.roster_hints(["Ada"], db_path=":memory:") == ["no open 1:1 actions · review on file"]

    def test_a_store_error_still_renders_the_page(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("locked")

        monkeypatch.setattr("yeaboi.performance.store.PerformanceStore", _boom)
        assert setup.roster_hints(["Ada", "Bob"], db_path=":memory:") == [setup.GENERIC_HINT] * 2


class _Sessions:
    def __init__(self, state: dict | None, latest: str = "sess-1"):
        self._state = state
        self._latest = latest

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def load_state(self, session_id: str):
        return self._state

    def get_latest_session_id(self):
        return self._latest

    def get_session(self, session_id: str):
        return {"project_name": "Apollo", "created_at": "2026-08-23T10:00:00"}


class TestSessionTeam:
    def test_reads_the_plans_team_members(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.sessions.SessionStore",
            lambda *a, **k: _Sessions({"selected_team_members": ["Bob", "Ada", "Bob"]}),
        )
        assert setup.session_team("sess-1", db_path=":memory:") == ["Ada", "Bob"]

    def test_a_plan_with_no_team_is_empty(self, monkeypatch):
        monkeypatch.setattr("yeaboi.sessions.SessionStore", lambda *a, **k: _Sessions({}))
        assert setup.session_team("sess-1", db_path=":memory:") == []

    def test_an_unreadable_store_is_an_empty_fallback(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("no db")

        monkeypatch.setattr("yeaboi.sessions.SessionStore", _boom)
        assert setup.session_team("sess-1", db_path=":memory:") == []


class TestCollectRoster:
    def test_the_tracker_roster_wins(self, monkeypatch):
        monkeypatch.setattr("yeaboi.sessions.SessionStore", lambda *a, **k: _Sessions({}))
        monkeypatch.setattr("yeaboi.performance.roster.fetch_roster", lambda: [_Roster("Ada")])
        monkeypatch.setattr(setup, "roster_hints", lambda roster, **k: ["hint"] * len(roster))
        data = setup.collect_roster(db_path=":memory:")
        assert data["roster"] == ["Ada"]
        assert data["session_id"] == "sess-1"
        assert data["session_name"]

    def test_no_tracker_falls_back_to_the_plans_team(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.sessions.SessionStore",
            lambda *a, **k: _Sessions({"selected_team_members": ["Ada", "Bob"]}),
        )
        monkeypatch.setattr("yeaboi.performance.roster.fetch_roster", lambda: [])
        monkeypatch.setattr(setup, "roster_hints", lambda roster, **k: ["hint"] * len(roster))
        assert setup.collect_roster(db_path=":memory:")["roster"] == ["Ada", "Bob"]

    def test_a_tracker_outage_does_not_crash_the_page(self, monkeypatch):
        def _boom():
            raise RuntimeError("401")

        monkeypatch.setattr("yeaboi.sessions.SessionStore", lambda *a, **k: _Sessions({}, latest=""))
        monkeypatch.setattr("yeaboi.performance.roster.fetch_roster", _boom)
        data = setup.collect_roster(db_path=":memory:")
        assert data == {"session_id": "", "session_name": "", "roster": [], "hints": []}

    def test_hints_line_up_one_per_engineer(self, monkeypatch):
        monkeypatch.setattr("yeaboi.sessions.SessionStore", lambda *a, **k: _Sessions({}, latest=""))
        monkeypatch.setattr("yeaboi.performance.roster.fetch_roster", lambda: [_Roster("Ada"), _Roster("Bob")])
        monkeypatch.setattr("yeaboi.performance.store.PerformanceStore", lambda *a, **k: _Store({}, set()))
        data = setup.collect_roster(db_path=":memory:")
        assert len(data["hints"]) == len(data["roster"]) == 2


class TestActions:
    def test_every_action_is_labelled(self):
        assert set(setup.ACTIONS) == set(setup.ACTION_LABELS)
