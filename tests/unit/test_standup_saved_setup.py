"""Tests for the Standup saved-setup gate: the summary predicate and confirm loop.

The gate's screen builder is covered in test_standup_screen.py with the other
standup render tests.
"""

import pytest

from yeaboi.standup.store import StandupStore
from yeaboi.ui import mode_select
from yeaboi.ui.mode_select.screens._screens_secondary import _SAVED_SETUP_ACTIONS


class _Console:
    size = (100, 36)


class _Live:
    def __init__(self):
        self.frames = []

    def update(self, renderable):
        self.frames.append(renderable)


def _reader(keys):
    """Return a read_key stub that plays a key script, then idles."""
    queue = list(keys)

    def _read(**_kwargs):
        return queue.pop(0) if queue else ""

    return _read


@pytest.fixture(autouse=True)
def store(monkeypatch, tmp_path):
    """Autouse: the predicate reads the store, so no test may reach the real one."""
    db = tmp_path / "sessions.db"
    monkeypatch.setattr(mode_select, "_ana_dbp", db)
    return db


@pytest.fixture(autouse=True)
def no_integrations(monkeypatch):
    """Start every case from "only a Jira tracker is configured"."""
    monkeypatch.setattr("yeaboi.config.get_jira_project_key", lambda: "PSOT")
    monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "")
    monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "")
    monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "")
    monkeypatch.setattr("yeaboi.config.get_azure_devops_org_url", lambda: "")
    monkeypatch.setattr("yeaboi.config.get_confluence_space_key", lambda: "")
    monkeypatch.setattr("yeaboi.config.get_notion_root_page_id", lambda: "")
    monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: "")


def _save(db, **overrides):
    fields = {
        "enabled": False,
        "time": "10:00",
        "weekdays": "1-5",
        "delivery_channels": ["terminal"],
        "tracker_sources": ["jira"],
        "team_members": ["Alice", "Bob"],
        "roster_configured": True,
    }
    fields.update(overrides)
    with StandupStore(db) as st:
        st.save_config("s1", **fields)


class TestSavedSetupSummary:
    def test_no_saved_config_asks(self, store):
        assert mode_select._standup_saved_setup("s1") is None

    def test_blank_session_asks(self, store):
        assert mode_select._standup_saved_setup("") is None

    def test_team_only_setup_is_reusable_when_nothing_else_applies(self, store):
        _save(store)

        assert mode_select._standup_saved_setup("s1") == [
            ("Trackers", "Jira"),
            ("Members", "Alice, Bob"),
        ]

    def test_unconfigured_roster_asks(self, store):
        _save(store, roster_configured=False)

        assert mode_select._standup_saved_setup("s1") is None

    def test_empty_roster_asks(self, store):
        _save(store, team_members=[])

        assert mode_select._standup_saved_setup("s1") is None

    def test_no_tracker_in_env_asks(self, store, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_jira_project_key", lambda: "")
        _save(store)

        assert mode_select._standup_saved_setup("s1") is None

    def test_applicable_code_scope_must_be_configured(self, store, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "gh-token")
        _save(store)

        assert mode_select._standup_saved_setup("s1") is None

    def test_configured_code_scope_is_summarised(self, store, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "gh-token")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_org_url", lambda: "https://dev.azure.com/acme")
        _save(
            store,
            code_sources=["github", "azure_devops"],
            github_repositories=["acme/api", "acme/web"],
            azdo_projects=["Core"],
            code_scope_configured=True,
        )

        rows = mode_select._standup_saved_setup("s1")

        assert ("Code", "2 GitHub repo(s) · 1 Azure project(s)") in rows

    def test_applicable_documentation_must_be_configured(self, store, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_confluence_space_key", lambda: "ENG")
        _save(store)

        assert mode_select._standup_saved_setup("s1") is None

    def test_documentation_opted_out_is_still_an_answer(self, store, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_confluence_space_key", lambda: "ENG")
        _save(store, documentation_sources=[], documentation_scope_configured=True)

        assert ("Docs", "none") in mode_select._standup_saved_setup("s1")

    def test_documentation_sources_are_named(self, store, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: "secret")
        _save(store, documentation_sources=["notion"], documentation_scope_configured=True)

        assert ("Docs", "Notion") in mode_select._standup_saved_setup("s1")

    def test_unreadable_store_asks_instead_of_raising(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mode_select, "_ana_dbp", tmp_path / "sessions.db")

        def _boom(*_args, **_kwargs):
            raise OSError("disk gone")

        monkeypatch.setattr("yeaboi.standup.store.StandupStore.load_config", _boom)

        assert mode_select._standup_saved_setup("s1") is None


class TestRemovedIntegrationsAreReconfirmed:
    """Walking the pickers pruned a removed integration; reuse must not skip that."""

    def test_tracker_no_longer_configured_asks(self, store):
        _save(store, tracker_sources=["jira", "azure_devops"])  # AzDO since removed from env

        assert mode_select._standup_saved_setup("s1") is None

    def test_code_source_no_longer_configured_asks(self, store, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "gh-token")
        _save(
            store,
            code_sources=["github", "azure_devops"],  # Azure Repos since removed
            github_repositories=["acme/api"],
            code_scope_configured=True,
        )

        assert mode_select._standup_saved_setup("s1") is None

    def test_documentation_source_no_longer_configured_asks(self, store, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_confluence_space_key", lambda: "ENG")
        _save(
            store,
            documentation_sources=["confluence", "notion"],  # Notion since removed
            documentation_scope_configured=True,
        )

        assert mode_select._standup_saved_setup("s1") is None

    def test_still_available_selection_is_reused(self, store, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "Core")
        _save(store, tracker_sources=["jira", "azure_devops"])

        assert mode_select._standup_saved_setup("s1")[0] == ("Trackers", "Jira, Azure DevOps")


class TestSourceLabels:
    def test_keys_render_as_picker_names(self):
        assert mode_select._standup_source_labels(["jira", "azure_devops"]) == "Jira, Azure DevOps"

    def test_unknown_key_passes_through(self):
        assert mode_select._standup_source_labels(["gitlab"]) == "gitlab"


class TestMemberSummary:
    def test_short_roster_is_named_in_full(self):
        assert mode_select._standup_member_summary(["Alice", "Bob"]) == "Alice, Bob"

    def test_long_roster_is_truncated_with_a_count(self):
        roster = ["Alice", "Bob", "Carol", "Dan", "Erin"]
        assert mode_select._standup_member_summary(roster) == "Alice, Bob, Carol +2 more"

    def test_exactly_the_cutoff_is_not_truncated(self):
        assert mode_select._standup_member_summary(["Alice", "Bob", "Carol"]) == "Alice, Bob, Carol"


class TestConfirmLoop:
    ROWS = [("Trackers", "Jira"), ("Members", "2 selected")]

    def _run(self, keys):
        live = _Live()
        outcome = mode_select._run_standup_saved_setup_confirm(_Console(), live, _reader(keys), 0.001, True, self.ROWS)
        return outcome, live

    def test_enter_uses_the_saved_setup(self):
        assert self._run(["enter"])[0] == "use"

    def test_right_then_enter_changes_it(self):
        assert self._run(["right", "enter"])[0] == "change"

    def test_third_button_cancels(self):
        assert self._run(["right", "right", "enter"])[0] == "cancel"

    def test_esc_cancels(self):
        assert self._run(["esc"])[0] == "cancel"

    def test_left_wraps_to_the_last_button(self):
        assert self._run(["left", "enter"])[0] == "cancel"

    def test_idle_ticks_keep_rendering(self):
        outcome, live = self._run(["", "", "enter"])
        assert outcome == "use"
        assert len(live.frames) == 3


class TestConfirmLoopClicks:
    """A clicked button must resolve to the outcome its label promises."""

    ROWS = [("Trackers", "Jira")]

    def _click(self, monkeypatch, index):
        monkeypatch.setattr(mode_select, "button_click", lambda *_args, **_kwargs: index)
        return mode_select._run_standup_saved_setup_confirm(
            _Console(), _Live(), _reader(["click:10:20", "esc"]), 0.001, True, self.ROWS
        )

    @pytest.mark.parametrize(
        ("label", "expected"),
        [("Use saved", "use"), ("Change", "change"), ("Back", "cancel")],
    )
    def test_each_label_maps_to_its_outcome(self, monkeypatch, label, expected):
        assert self._click(monkeypatch, _SAVED_SETUP_ACTIONS.index(label)) == expected

    def test_click_outside_the_buttons_keeps_waiting(self, monkeypatch):
        # button_click returns None → the loop must not resolve; the queued esc does.
        assert self._click(monkeypatch, None) == "cancel"
