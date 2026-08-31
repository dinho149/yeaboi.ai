"""The issue-tracker registry — the lookup a dozen dispatch sites now share."""

from __future__ import annotations

from yeaboi import trackers


def _patch(monkeypatch, jira: bool = False, azdo: bool = False) -> None:
    monkeypatch.setattr("yeaboi.trackers._jira_configured", lambda: jira)
    monkeypatch.setattr("yeaboi.trackers._azdevops_configured", lambda: azdo)


class TestRegistry:
    def test_every_spec_carries_its_key_and_a_label(self):
        for key, spec in trackers.TRACKERS.items():
            assert spec.key == key
            assert spec.label.strip()

    def test_jira_offers_first(self):
        # The offer order is the auto-detect order — Jira has always won it.
        assert next(iter(trackers.TRACKERS)) == "jira"

    def test_by_key_misses_cleanly(self):
        assert trackers.by_key("linear-notyet") is None


class TestConfigured:
    def test_lists_only_what_is_configured_in_offer_order(self, monkeypatch):
        _patch(monkeypatch, jira=True, azdo=True)
        assert trackers.configured() == ["jira", "azdevops"]
        _patch(monkeypatch, azdo=True)
        assert trackers.configured() == ["azdevops"]
        _patch(monkeypatch)
        assert trackers.configured() == []


class TestPick:
    def test_prefers_the_asked_for_tracker_when_configured(self, monkeypatch):
        _patch(monkeypatch, jira=True, azdo=True)
        assert trackers.pick("azdevops").key == "azdevops"

    def test_an_unconfigured_preference_yields_none_not_a_fallback(self, monkeypatch):
        # A user who chose a tracker that then lost its credentials should see
        # the failure, not silently sync to a different board.
        _patch(monkeypatch, azdo=True)
        assert trackers.pick("jira") is None

    def test_no_preference_takes_the_first_configured(self, monkeypatch):
        _patch(monkeypatch, azdo=True)
        assert trackers.pick().key == "azdevops"
        _patch(monkeypatch)
        assert trackers.pick() is None

    def test_label_for_matches_pick(self, monkeypatch):
        _patch(monkeypatch, jira=True)
        assert trackers.label_for() == "Jira"
        _patch(monkeypatch)
        assert trackers.label_for() == ""


class TestResolveChoice:
    OPTIONS = ["jira", "azdevops"]

    def test_a_one_based_index_maps_to_the_offered_list(self):
        assert trackers.resolve_choice("1", self.OPTIONS) == "jira"
        assert trackers.resolve_choice("2", self.OPTIONS) == "azdevops"

    def test_keys_and_labels_both_resolve(self):
        assert trackers.resolve_choice("azdevops", self.OPTIONS) == "azdevops"
        assert trackers.resolve_choice("Azure DevOps", self.OPTIONS) == "azdevops"
        assert trackers.resolve_choice("JIRA", self.OPTIONS) == "jira"

    def test_bare_azure_still_means_the_boards(self):
        assert trackers.resolve_choice("azure", self.OPTIONS) == "azdevops"

    def test_anything_else_falls_back_to_the_first_option(self):
        assert trackers.resolve_choice("", self.OPTIONS) == "jira"
        assert trackers.resolve_choice("gibberish", self.OPTIONS) == "jira"


class TestDispatch:
    def test_fetchers_answer_the_no_tracker_case_themselves(self, monkeypatch):
        _patch(monkeypatch)
        assert trackers.fetch_velocity() is None
        assert trackers.fetch_active_sprint() == (None, None, "No tracker configured")
        assert trackers.fetch_sprint_targets() == ([], "No tracker configured")

    def test_the_preferred_tracker_is_the_one_asked(self, monkeypatch):
        _patch(monkeypatch, jira=True, azdo=True)
        monkeypatch.setattr("yeaboi.trackers._azdevops_velocity", lambda: {"team_velocity": 7.0})
        monkeypatch.setattr("yeaboi.trackers._jira_velocity", lambda: {"team_velocity": 9.0})
        assert trackers.fetch_velocity("azdevops") == {"team_velocity": 7.0}
        assert trackers.fetch_velocity() == {"team_velocity": 9.0}

    def test_result_summary_flattens_each_modules_own_result_shape(self):
        class JiraResult:
            epic_key = "PROJ-1"
            sprints_created = {"Sprint 1": "101"}
            sprints_updated = {}

        class AzdoResult:
            epic_id = "42"
            iterations_created = {"Sprint 1": "\\P\\Sprint 1"}
            iterations_updated = {}

        jira = trackers.TRACKERS["jira"].result_summary(JiraResult())
        azdo = trackers.TRACKERS["azdevops"].result_summary(AzdoResult())
        assert jira == {"epic": "PROJ-1", "sprints_created": {"Sprint 1": "101"}, "sprints_updated": {}}
        assert azdo == {"epic": "42", "sprints_created": {"Sprint 1": "\\P\\Sprint 1"}, "sprints_updated": {}}
