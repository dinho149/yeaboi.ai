"""Interaction tests for the Standup Generate source/member/update sequence."""

import pytest

from yeaboi.standup.store import StandupStore
from yeaboi.ui import mode_select


class _Console:
    size = (100, 36)


class _Live:
    def update(self, _renderable):
        pass


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch, tmp_path):
    """Keep the flow (which now reads saved config) off the real ~/.yeaboi store."""
    monkeypatch.setattr(mode_select, "_ana_dbp", tmp_path / "sessions.db")


def test_generate_confirms_team_before_update_and_engine(monkeypatch):
    calls = []

    def _team(*args):
        calls.append("team")
        return True, "Team saved."

    def _update(*args, **kwargs):
        calls.append("update")
        return ""

    def _code(*args):
        calls.append("code")
        return True, "Code scope saved."

    def _documentation(*args):
        calls.append("documentation")
        return True, "Documentation scope saved."

    def _generate(session_id, on_progress=None):
        calls.append("engine")
        return "Generated."

    monkeypatch.setattr(mode_select, "_standup_team_configure", _team)
    monkeypatch.setattr(mode_select, "_standup_code_configure", _code)
    monkeypatch.setattr(mode_select, "_standup_documentation_configure", _documentation)
    monkeypatch.setattr(mode_select, "_standup_read_line", _update)
    monkeypatch.setattr(mode_select, "_standup_generate", _generate)

    result = mode_select._standup_generate_flow(_Console(), _Live(), lambda **kwargs: "", 0.001, True, "s1")

    assert result == "Generated."
    assert calls == ["team", "code", "documentation", "update", "engine"]


def test_generate_stops_when_team_picker_is_cancelled(monkeypatch):
    monkeypatch.setattr(
        mode_select,
        "_standup_team_configure",
        lambda *args: (False, "Team selection cancelled."),
    )
    monkeypatch.setattr(
        mode_select,
        "_standup_read_line",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("update prompt must not open")),
    )
    monkeypatch.setattr(mode_select, "_standup_code_configure", lambda *args: (True, "Code saved."))
    monkeypatch.setattr(
        mode_select,
        "_standup_generate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("engine must not run")),
    )

    result = mode_select._standup_generate_flow(_Console(), _Live(), lambda **kwargs: "", 0.001, True, "s1")

    assert result == "Team selection cancelled."


def test_update_cancel_happens_after_confirmed_team_and_skips_engine(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mode_select,
        "_standup_team_configure",
        lambda *args: (calls.append("team") or True, "Team saved."),
    )
    monkeypatch.setattr(
        mode_select,
        "_standup_read_line",
        lambda *args, **kwargs: calls.append("update"),
    )
    monkeypatch.setattr(
        mode_select,
        "_standup_code_configure",
        lambda *args: (calls.append("code") or True, "Code saved."),
    )
    monkeypatch.setattr(
        mode_select,
        "_standup_documentation_configure",
        lambda *args: (calls.append("documentation") or True, "Documentation saved."),
    )
    monkeypatch.setattr(
        mode_select,
        "_standup_generate",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("engine must not run")),
    )

    result = mode_select._standup_generate_flow(_Console(), _Live(), lambda **kwargs: "", 0.001, True, "s1")

    assert result is None
    assert calls == ["team", "code", "documentation", "update"]


def test_generate_stops_when_repository_picker_is_cancelled(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mode_select,
        "_standup_team_configure",
        lambda *args: (calls.append("team") or True, "Team saved."),
    )
    monkeypatch.setattr(
        mode_select,
        "_standup_code_configure",
        lambda *args: (calls.append("code") or False, "Repository selection cancelled."),
    )
    monkeypatch.setattr(
        mode_select,
        "_standup_read_line",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("update prompt must not open")),
    )

    result = mode_select._standup_generate_flow(_Console(), _Live(), lambda **kwargs: "", 0.001, True, "s1")

    assert result == "Repository selection cancelled."
    assert calls == ["team", "code"]


def test_generate_stops_when_documentation_picker_is_cancelled(monkeypatch):
    calls = []
    monkeypatch.setattr(
        mode_select,
        "_standup_team_configure",
        lambda *args: (calls.append("team") or True, "Team saved."),
    )
    monkeypatch.setattr(
        mode_select,
        "_standup_code_configure",
        lambda *args: (calls.append("code") or True, "Code saved."),
    )
    monkeypatch.setattr(
        mode_select,
        "_standup_documentation_configure",
        lambda *args: (calls.append("documentation") or False, "Documentation selection cancelled."),
    )
    monkeypatch.setattr(
        mode_select,
        "_standup_read_line",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("update prompt must not open")),
    )

    result = mode_select._standup_generate_flow(_Console(), _Live(), lambda **kwargs: "", 0.001, True, "s1")

    assert result == "Documentation selection cancelled."
    assert calls == ["team", "code", "documentation"]


def test_documentation_picker_persists_explicit_scope(monkeypatch, tmp_path):
    db = tmp_path / "sessions.db"
    monkeypatch.setattr(mode_select, "_ana_dbp", db)
    monkeypatch.setattr("yeaboi.config.get_confluence_space_key", lambda: "ENG")
    monkeypatch.setattr("yeaboi.config.get_notion_root_page_id", lambda: "root")
    monkeypatch.setattr(
        mode_select,
        "_run_standup_source_select",
        lambda *args, **kwargs: ["notion"],
    )

    ok, _message = mode_select._standup_documentation_configure(
        _Console(), _Live(), lambda **kwargs: "", 0.001, True, "s1"
    )

    with StandupStore(db) as store:
        config = store.load_config("s1")
    assert ok is True
    assert config["documentation_sources"] == ["notion"]
    assert config["documentation_scope_configured"] is True


def test_documentation_picker_offers_notion_with_token_and_no_root(monkeypatch, tmp_path):
    db = tmp_path / "sessions.db"
    captured = {}
    monkeypatch.setattr(mode_select, "_ana_dbp", db)
    monkeypatch.setattr("yeaboi.config.get_confluence_space_key", lambda: "")
    monkeypatch.setattr("yeaboi.config.get_notion_root_page_id", lambda: None)
    monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: "token")

    def _select(*args, **kwargs):
        captured["sources"] = args[5]
        return ["notion"]

    monkeypatch.setattr(mode_select, "_run_standup_source_select", _select)

    ok, _message = mode_select._standup_documentation_configure(
        _Console(), _Live(), lambda **kwargs: "", 0.001, True, "s1"
    )

    assert ok is True
    assert captured["sources"] == [("notion", "Notion")]
    with StandupStore(db) as store:
        assert store.load_config("s1")["documentation_sources"] == ["notion"]


class TestSavedSetupGate:
    """Generate offers the saved setup instead of re-walking every picker."""

    def _wire(self, monkeypatch, calls, *, rows, choice):
        monkeypatch.setattr(mode_select, "_standup_saved_setup", lambda _session: rows)
        monkeypatch.setattr(
            mode_select,
            "_run_standup_saved_setup_confirm",
            lambda *args: calls.append("gate") or choice,
        )
        for name in ("team", "code", "documentation"):
            monkeypatch.setattr(
                mode_select,
                f"_standup_{name}_configure",
                lambda *args, _n=name: (calls.append(_n) or True, "saved"),
            )
        monkeypatch.setattr(
            mode_select,
            "_standup_read_line",
            lambda *args, **kwargs: calls.append("update") or "",
        )
        monkeypatch.setattr(
            mode_select,
            "_standup_generate",
            lambda session_id, on_progress=None: calls.append("engine") or "Generated.",
        )

    def test_use_saved_skips_every_picker(self, monkeypatch):
        calls = []
        self._wire(monkeypatch, calls, rows=[("Trackers", "Jira")], choice="use")

        result = mode_select._standup_generate_flow(_Console(), _Live(), lambda **kwargs: "", 0.001, True, "s1")

        assert result == "Generated."
        assert calls == ["gate", "update", "engine"]

    def test_change_runs_the_full_sequence(self, monkeypatch):
        calls = []
        self._wire(monkeypatch, calls, rows=[("Trackers", "Jira")], choice="change")

        result = mode_select._standup_generate_flow(_Console(), _Live(), lambda **kwargs: "", 0.001, True, "s1")

        assert result == "Generated."
        assert calls == ["gate", "team", "code", "documentation", "update", "engine"]

    def test_cancel_stops_before_the_update_prompt(self, monkeypatch):
        calls = []
        self._wire(monkeypatch, calls, rows=[("Trackers", "Jira")], choice="cancel")

        result = mode_select._standup_generate_flow(_Console(), _Live(), lambda **kwargs: "", 0.001, True, "s1")

        assert result is None
        assert calls == ["gate"]

    def test_no_saved_setup_never_opens_the_gate(self, monkeypatch):
        calls = []
        self._wire(monkeypatch, calls, rows=None, choice="use")

        result = mode_select._standup_generate_flow(_Console(), _Live(), lambda **kwargs: "", 0.001, True, "s1")

        assert result == "Generated."
        assert calls == ["team", "code", "documentation", "update", "engine"]


def test_team_confirmation_persists_scope(monkeypatch, tmp_path):
    db_path = tmp_path / "sessions.db"
    monkeypatch.setattr(mode_select, "_ana_dbp", db_path)
    monkeypatch.setattr("yeaboi.config.get_jira_project_key", lambda: "PSOT")
    monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "Core")
    monkeypatch.setattr(mode_select, "_run_standup_source_select", lambda *args: ["jira"])
    monkeypatch.setattr(mode_select, "_run_standup_member_select", lambda *args: ["Alice"])
    monkeypatch.setattr("yeaboi.standup.roster.discover_team_members", lambda *args, **kwargs: ["Alice", "Bob"])

    ok, message = mode_select._standup_team_configure(
        _Console(),
        _Live(),
        lambda **kwargs: "",
        0.001,
        True,
        "s1",
    )

    assert ok is True
    assert "1 member" in message
    with StandupStore(db_path) as store:
        config = store.load_config("s1")
    assert config["tracker_sources"] == ["jira"]
    assert config["team_members"] == ["Alice"]
    assert config["roster_configured"] is True


def test_team_source_cancel_preserves_saved_scope(monkeypatch, tmp_path):
    db_path = tmp_path / "sessions.db"
    monkeypatch.setattr(mode_select, "_ana_dbp", db_path)
    monkeypatch.setattr("yeaboi.config.get_jira_project_key", lambda: "PSOT")
    monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "Core")
    with StandupStore(db_path) as store:
        store.save_config(
            "s1",
            enabled=False,
            time="10:00",
            weekdays="1-5",
            delivery_channels=["terminal"],
            tracker_sources=["azure_devops"],
            team_members=["Bob"],
            roster_configured=True,
        )
    monkeypatch.setattr(mode_select, "_run_standup_source_select", lambda *args: "cancel")

    ok, message = mode_select._standup_team_configure(
        _Console(),
        _Live(),
        lambda **kwargs: "",
        0.001,
        True,
        "s1",
    )

    assert ok is False
    assert message == "Team selection cancelled."
    with StandupStore(db_path) as store:
        config = store.load_config("s1")
    assert config["tracker_sources"] == ["azure_devops"]
    assert config["team_members"] == ["Bob"]
