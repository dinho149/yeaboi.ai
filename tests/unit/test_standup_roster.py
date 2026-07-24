"""Tests for Standup tracker source validation and member discovery."""

import pytest

from yeaboi.agent.state import EngineerRef
from yeaboi.standup import roster


def test_default_prefers_jira_then_azure_devops():
    assert roster.default_tracker_sources(jira_project="PSOT", azdo_project="Core") == ["jira"]
    assert roster.default_tracker_sources(azdo_project="Core") == ["azure_devops"]
    assert roster.default_tracker_sources() == ["jira"]


def test_validate_rejects_empty_and_unknown():
    with pytest.raises(ValueError, match="at least one"):
        roster.validate_tracker_sources([])
    with pytest.raises(ValueError, match="unknown"):
        roster.validate_tracker_sources(["github"])


def test_discover_uses_only_selected_tracker(monkeypatch):
    captured = {}

    def _fetch(**kwargs):
        captured.update(kwargs)
        return [EngineerRef(name="Bob"), EngineerRef(name="Alice"), EngineerRef(name="Bob")]

    monkeypatch.setattr("yeaboi.performance.roster.fetch_roster", _fetch)
    assert roster.discover_team_members(["jira"], jira_project="PSOT", azdo_project="Core") == ["Alice", "Bob"]
    assert captured["jira_project"] == "PSOT"
    assert captured["azdo_project"] == ""


def test_discover_unconfigured_selection_is_empty(monkeypatch):
    monkeypatch.setattr(
        "yeaboi.performance.roster.fetch_roster",
        lambda **kwargs: pytest.fail("fetch should not run"),
    )
    assert roster.discover_team_members(["jira"], jira_project="", azdo_project="Core") == []
