"""Unit tests for reporting/activity.gather_delivered_work (status filtering)."""

import pytest

from yeaboi.reporting import activity


@pytest.fixture(autouse=True)
def _no_sprint_context(monkeypatch):
    # Isolate from any live tracker sprint read.
    import yeaboi.standup.sprint_context as sc

    monkeypatch.setattr(sc, "gather", lambda *a, **k: sc.SprintContext(sprint_name="Sprint 9"))


def _fake_jira(items):
    def _f(project_key="", days=1):
        return items

    return _f


class TestIsCompleted:
    @pytest.mark.parametrize("status", ["Done", "done", "Closed", "Resolved", "Released", "Completed", "Shipped"])
    def test_completed_statuses(self, status):
        assert activity._is_completed(status)

    @pytest.mark.parametrize("status", ["In Progress", "To Do", "", "Blocked", "In Review"])
    def test_non_completed_statuses(self, status):
        assert not activity._is_completed(status)


class TestGatherDeliveredWork:
    def test_no_tracker_returns_warning(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_jira_project_key", lambda: None)
        monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: None)
        items, sprints, warnings = activity.gather_delivered_work("last_month")
        assert items == []
        assert warnings and "board" in warnings[0].lower()

    def test_filters_to_completed_only(self, monkeypatch):
        raw = [
            {"key": "P-1", "title": "done thing", "status": "Done", "author": "Ada", "timestamp": "2026-07-10"},
            {"key": "P-2", "title": "wip thing", "status": "In Progress", "author": "Bo", "timestamp": "2026-07-10"},
            {"key": "P-3", "title": "closed thing", "status": "Closed", "author": "Cy", "timestamp": "2026-07-10"},
        ]
        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", _fake_jira(raw), raising=False)
        items, sprints, warnings = activity.gather_delivered_work("last_sprint", jira_project="PROJ")
        keys = {i.key for i in items}
        assert keys == {"P-1", "P-3"}
        assert all(i.source == "jira" for i in items)
        assert items[0].assignee in {"Ada", "Cy"}
        assert sprints == ["Sprint 9"]

    def test_window_end_excludes_later_completions(self, monkeypatch):
        # The fetch is lookback-from-today, so a report window ending in the past
        # must drop tickets completed after window_end (undated rows are kept).
        raw = [
            {"key": "P-1", "title": "in window", "status": "Done", "timestamp": "2026-06-20T10:00:00"},
            {"key": "P-2", "title": "after window", "status": "Done", "timestamp": "2026-07-15T09:00:00"},
            {"key": "P-3", "title": "before window", "status": "Done", "timestamp": "2026-05-30"},
            {"key": "P-4", "title": "undated", "status": "Done", "timestamp": ""},
        ]
        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", _fake_jira(raw), raising=False)
        items, _sprints, _warnings = activity.gather_delivered_work(
            "window", jira_project="PROJ", days_override=60, window_start="2026-06-01", window_end="2026-06-30"
        )
        assert {i.key for i in items} == {"P-1", "P-4"}

    def test_no_window_keeps_every_completed_row(self, monkeypatch):
        raw = [{"key": "P-1", "title": "t", "status": "Done", "timestamp": "2099-01-01"}]
        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", _fake_jira(raw), raising=False)
        items, _sprints, _warnings = activity.gather_delivered_work("last_sprint", jira_project="PROJ")
        assert len(items) == 1

    def test_ticket_and_changelog_rows_collapse_to_one_item(self, monkeypatch):
        # The activity feed emits BOTH the ticket row and a "moved … to Done"
        # changelog row for a completed ticket — the report must show one row.
        raw = [
            {"key": "P-1", "title": "Ship the thing", "status": "Done", "kind": "issue", "author": "Ada"},
            {
                "key": "P-1",
                "title": "moved P-1 'Ship the thing' to Done",
                "status": "Done",
                "kind": "update",
                "author": "Ada",
            },
        ]
        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", _fake_jira(raw), raising=False)
        items, _, _ = activity.gather_delivered_work("last_sprint", jira_project="PROJ")
        assert len(items) == 1
        assert items[0].title == "Ship the thing"

    def test_changelog_row_first_still_upgrades_to_ticket_title(self, monkeypatch):
        raw = [
            {
                "key": "P-1",
                "title": "moved P-1 'Ship the thing' to Done",
                "status": "Done",
                "kind": "update",
                "author": "Ada",
            },
            {"key": "P-1", "title": "Ship the thing", "status": "Done", "kind": "issue", "author": "Ada"},
        ]
        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", _fake_jira(raw), raising=False)
        items, _, _ = activity.gather_delivered_work("last_sprint", jira_project="PROJ")
        assert len(items) == 1
        assert items[0].title == "Ship the thing"

    def test_changelog_only_ticket_keeps_its_single_row(self, monkeypatch):
        raw = [
            {
                "key": "P-9",
                "title": "moved P-9 'Old thing' to Done",
                "status": "Done",
                "kind": "update",
                "author": "Bo",
            },
        ]
        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", _fake_jira(raw), raising=False)
        items, _, _ = activity.gather_delivered_work("last_sprint", jira_project="PROJ")
        assert len(items) == 1
        assert items[0].title == "moved P-9 'Old thing' to Done"

    def test_distinct_keys_are_not_collapsed(self, monkeypatch):
        raw = [
            {"key": "P-1", "title": "One", "status": "Done", "kind": "issue", "author": "Ada"},
            {"key": "P-2", "title": "Two", "status": "Closed", "kind": "issue", "author": "Bo"},
            {"key": "", "title": "keyless a", "status": "Done", "kind": "issue", "author": "Cy"},
            {"key": "", "title": "keyless b", "status": "Done", "kind": "issue", "author": "Cy"},
        ]
        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", _fake_jira(raw), raising=False)
        items, _, _ = activity.gather_delivered_work("last_sprint", jira_project="PROJ")
        assert [i.title for i in items] == ["One", "Two", "keyless a", "keyless b"]

    def test_days_override_skips_period_days_and_sprint_context(self, monkeypatch):
        captured = {}

        def _fake_jira_recent(project_key="", days=1):
            captured["days"] = days
            return [{"key": "P-1", "title": "t", "status": "Done", "author": "Ada", "timestamp": "2026-07-10"}]

        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", _fake_jira_recent, raising=False)

        # period_days would return 14 for last_sprint; days_override must win.
        items, sprints_out, warnings = activity.gather_delivered_work(
            "last_sprint", jira_project="PROJ", days_override=90
        )
        assert captured["days"] == 90
        assert sprints_out == []  # sprint-context probe skipped
        assert len(items) == 1

    def test_activity_but_nothing_done_warns(self, monkeypatch):
        raw = [{"key": "P-9", "title": "wip", "status": "In Progress", "author": "Ada", "timestamp": "2026-07-10"}]
        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", _fake_jira(raw), raising=False)
        items, sprints, warnings = activity.gather_delivered_work("last_sprint", jira_project="PROJ")
        assert items == []
        assert any("Done/Closed" in w for w in warnings)

    def test_on_progress_emits_fetch_and_count(self, monkeypatch):
        raw = [{"key": "P-1", "title": "t", "status": "Done", "author": "Ada", "timestamp": "2026-07-10"}]
        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", _fake_jira(raw), raising=False)
        seen: list[str] = []
        activity.gather_delivered_work("last_sprint", jira_project="PROJ", on_progress=seen.append)
        assert any("Jira" in m for m in seen)
        assert any("1 delivered item" in m for m in seen)

    def test_broken_on_progress_is_swallowed(self, monkeypatch):
        raw = [{"key": "P-1", "title": "t", "status": "Done", "author": "Ada", "timestamp": "2026-07-10"}]
        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", _fake_jira(raw), raising=False)

        def _boom(msg):
            raise RuntimeError("bad callback")

        items, _, _ = activity.gather_delivered_work("last_sprint", jira_project="PROJ", on_progress=_boom)
        assert len(items) == 1  # gather survives a broken callback


class TestNormalizeSources:
    def test_none_is_full_auto(self):
        delivery, code, docs = activity.normalize_sources(None)
        assert delivery is None  # auto — every configured tracker
        assert code == ["github", "azuredevops"]
        assert docs == ["confluence", "notion"]

    def test_missing_keys_are_auto(self):
        delivery, code, docs = activity.normalize_sources({"delivery": ["jira"]})
        assert delivery == {"jira"}
        assert code == ["github", "azuredevops"]  # key absent → auto
        assert docs == ["confluence", "notion"]

    def test_explicit_empty_is_not_auto(self):
        delivery, code, docs = activity.normalize_sources({"delivery": [], "code": [], "docs": []})
        assert delivery == set()  # "none selected", NOT auto
        assert code == []
        assert docs == []

    def test_azdo_aliases_map_to_canonical(self):
        for alias in ("azdevops", "azure_devops", "azdo", "AzureDevOps", "AZDO"):
            delivery, code, _ = activity.normalize_sources({"delivery": [alias], "code": [alias]})
            assert delivery == {"azuredevops"}, alias
            assert code == ["azuredevops"], alias

    def test_unknown_tokens_dropped(self):
        delivery, code, docs = activity.normalize_sources(
            {"delivery": ["jira", "gitlab"], "code": ["svn"], "docs": ["notion", "wiki"]}
        )
        assert delivery == {"jira"}
        assert code == []
        assert docs == ["notion"]

    def test_non_dict_is_auto(self):
        delivery, code, docs = activity.normalize_sources("jira")  # sloppy MCP caller
        assert delivery is None
        assert code and docs

    def test_duplicates_deduped(self):
        _, code, _ = activity.normalize_sources({"code": ["github", "azdo", "azuredevops", "github"]})
        assert code == ["github", "azuredevops"]


class TestAvailableReportSources:
    def _clear_all(self, monkeypatch):
        for getter in (
            "get_jira_project_key",
            "get_azure_devops_project",
            "get_azure_devops_token",
            "get_github_token",
            "get_standup_github_repo",
            "get_confluence_base_url",
            "get_confluence_token",
            "get_notion_token",
        ):
            monkeypatch.setattr(f"yeaboi.config.{getter}", lambda: "", raising=False)

    def test_nothing_configured(self, monkeypatch):
        self._clear_all(monkeypatch)
        assert activity.available_report_sources() == {"delivery": [], "code": [], "docs": []}

    def test_reflects_configured_integrations(self, monkeypatch):
        self._clear_all(monkeypatch)
        monkeypatch.setattr("yeaboi.config.get_jira_project_key", lambda: "PROJ")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_project", lambda: "Team")
        monkeypatch.setattr("yeaboi.config.get_azure_devops_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: "tok")
        monkeypatch.setattr("yeaboi.config.get_standup_github_repo", lambda: "org/repo")
        monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: "tok")
        grid = activity.available_report_sources()
        assert grid["delivery"] == ["jira", "azuredevops"]
        assert grid["code"] == ["github", "azuredevops"]
        assert grid["docs"] == ["notion"]  # confluence unset

    def test_probe_failure_degrades_to_partial(self, monkeypatch):
        self._clear_all(monkeypatch)
        monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: "tok")

        def _boom():
            raise RuntimeError("config exploded")

        monkeypatch.setattr("yeaboi.config.get_jira_project_key", _boom)
        grid = activity.available_report_sources()  # must not raise
        assert grid["docs"] == ["notion"]


class TestDeliverySourcesGate:
    def test_jira_only_selection_never_fetches_azdo(self, monkeypatch):
        raw = [{"key": "P-1", "title": "t", "status": "Done", "author": "Ada", "timestamp": "2026-07-10"}]
        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", _fake_jira(raw), raising=False)

        def _azdo_must_not_run(*a, **k):
            raise AssertionError("Azure DevOps fetch must be gated off by delivery_sources")

        monkeypatch.setattr("yeaboi.tools.azure_devops.azdevops_recent_activity", _azdo_must_not_run, raising=False)
        items, _, warnings = activity.gather_delivered_work(
            "last_sprint",
            jira_project="PROJ",
            azdo_project="Team",
            days_override=30,
            delivery_sources={"jira"},
        )
        assert [i.key for i in items] == ["P-1"]
        assert not warnings

    def test_selection_excluding_configured_warns(self, monkeypatch):
        # Only Jira configured, but the selection wants AzDO only → empty + warning.
        items, sprints, warnings = activity.gather_delivered_work(
            "last_sprint", jira_project="PROJ", days_override=30, delivery_sources={"azuredevops"}
        )
        assert items == [] and sprints == []
        assert any("ticketing source" in w.lower() for w in warnings)

    def test_none_selection_keeps_both(self, monkeypatch):
        raw_j = [{"key": "P-1", "title": "j", "status": "Done", "author": "A", "timestamp": "2026-07-10"}]
        raw_a = [{"key": "42", "title": "a", "status": "Closed", "author": "B", "timestamp": "2026-07-10"}]
        monkeypatch.setattr("yeaboi.tools.jira.jira_recent_activity", _fake_jira(raw_j), raising=False)
        monkeypatch.setattr(
            "yeaboi.tools.azure_devops.azdevops_recent_activity", lambda p="", days=1: raw_a, raising=False
        )
        items, _, _ = activity.gather_delivered_work(
            "last_sprint", jira_project="PROJ", azdo_project="Team", days_override=30, delivery_sources=None
        )
        assert {i.source for i in items} == {"jira", "azuredevops"}
