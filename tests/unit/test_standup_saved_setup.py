"""Tests for the Standup saved-setup gate: the summary predicate and confirm loop.

The gate's screen builder is covered in test_standup_screen.py with the other
standup render tests.
"""

from datetime import date

import pytest

from yeaboi.agent.state import StandupReport
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


def _report(standup_date: str, confidence: int) -> StandupReport:
    return StandupReport(
        date=standup_date,
        session_id="s1",
        sprint_day=3,
        confidence_pct=confidence,
        confidence_label="On track",
    )


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

        # Counts first, then the names themselves on a second line — a count
        # alone can't tell you whether this is the scope you wanted back.
        assert ("Code", "2 GitHub repo(s) · 1 Azure project(s)\nacme/api, acme/web, Core") in rows

    def test_long_code_scope_is_truncated_with_a_count(self, store, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "gh-token")
        _save(
            store,
            code_sources=["github"],
            github_repositories=["a/one", "a/two", "a/three", "a/four", "a/five"],
            code_scope_configured=True,
        )

        rows = dict(mode_select._standup_saved_setup("s1"))

        assert rows["Code"] == "5 GitHub repo(s)\na/one, a/two, a/three, a/four +1 more"

    def test_empty_code_scope_stays_none(self, store, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "gh-token")
        _save(store, code_sources=[], code_scope_configured=True)

        assert ("Code", "none") in mode_select._standup_saved_setup("s1")

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


class TestNameSummary:
    def test_short_roster_is_named_in_full(self):
        assert mode_select._standup_name_summary(["Alice", "Bob"]) == "Alice, Bob"

    def test_long_roster_is_truncated_with_a_count(self):
        roster = ["Alice", "Bob", "Carol", "Dan", "Erin"]
        assert mode_select._standup_name_summary(roster) == "Alice, Bob, Carol +2 more"

    def test_exactly_the_cutoff_is_not_truncated(self):
        assert mode_select._standup_name_summary(["Alice", "Bob", "Carol"]) == "Alice, Bob, Carol"

    def test_cutoff_is_caller_controlled(self):
        names = ["a", "b", "c", "d", "e"]
        assert mode_select._standup_name_summary(names, shown=4) == "a, b, c, d +1 more"


class TestLastRunLabel:
    """The one row that is context, not a gate — it must never raise."""

    def test_same_day_run_reads_today(self):
        row = {"standup_date": "2026-08-02", "confidence_pct": 90, "status": "success"}

        assert mode_select._standup_last_run_label(row, date(2026, 8, 2)) == "today · 90% confidence"

    def test_previous_day_run_reads_yesterday(self):
        row = {"standup_date": "2026-08-01", "confidence_pct": 84, "status": "success"}

        assert mode_select._standup_last_run_label(row, date(2026, 8, 2)) == "yesterday · 84% confidence"

    def test_older_run_counts_the_days(self):
        row = {"standup_date": "2026-07-30", "confidence_pct": 71, "status": "success"}

        assert mode_select._standup_last_run_label(row, date(2026, 8, 2)) == "3 days ago · 71% confidence"

    def test_missing_confidence_is_omitted(self):
        row = {"standup_date": "2026-08-01", "confidence_pct": None, "status": "success"}

        assert mode_select._standup_last_run_label(row, date(2026, 8, 2)) == "yesterday"

    def test_non_success_status_is_called_out(self):
        row = {"standup_date": "2026-08-01", "confidence_pct": 50, "status": "partial"}

        assert mode_select._standup_last_run_label(row, date(2026, 8, 2)) == "yesterday · 50% confidence · partial"

    def test_falls_back_to_the_run_timestamp(self):
        row = {"standup_date": "", "run_at": "2026-08-01T09:15:00", "status": "success"}

        assert mode_select._standup_last_run_label(row, date(2026, 8, 2)) == "yesterday"

    def test_future_dated_run_states_the_date(self):
        row = {"standup_date": "2026-08-05", "status": "success"}

        assert mode_select._standup_last_run_label(row, date(2026, 8, 2)) == "2026-08-05"

    def test_unparseable_date_drops_the_row(self):
        assert mode_select._standup_last_run_label({"standup_date": "not-a-date"}, date(2026, 8, 2)) is None

    def test_empty_row_drops_the_row(self):
        assert mode_select._standup_last_run_label({}, date(2026, 8, 2)) is None


class TestLastRunRow:
    def _record(self, db, standup_date, confidence=80, status="success"):
        with StandupStore(db) as st:
            st.record_run(_report(standup_date, confidence), status=status)

    def test_no_history_means_no_row(self, store):
        _save(store)

        assert [label for label, _ in mode_select._standup_saved_setup("s1")] == ["Trackers", "Members"]

    def test_history_appends_the_row_last(self, store):
        _save(store)
        self._record(store, date.today().isoformat())

        rows = mode_select._standup_saved_setup("s1")

        assert rows[-1][0] == "Last run"
        assert rows[-1][1].startswith("today")

    def test_unreadable_history_still_reuses_the_setup(self, store, monkeypatch):
        _save(store)

        def _boom(*_args, **_kwargs):
            raise OSError("history table gone")

        monkeypatch.setattr("yeaboi.standup.store.StandupStore.get_history", _boom)

        # Context is never a gate: a broken history read drops the line, it does
        # not send the user back through five pickers.
        assert mode_select._standup_saved_setup("s1") == [
            ("Trackers", "Jira"),
            ("Members", "Alice, Bob"),
        ]


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
