"""Which analysis setup steps apply, and what a finished wizard asks for."""

from yeaboi.analysis import setup

GRID = {"delivery": ["jira", "azdevops"], "code": ["github"], "docs": ["confluence", "notion"]}


class TestFilteredGrid:
    def test_a_component_nothing_selected_reads_is_dropped(self):
        assert setup.filtered_grid(GRID, ["documentation"]) == {
            "delivery": [],
            "code": [],
            "docs": ["confluence", "notion"],
        }

    def test_either_code_feature_opens_the_code_row(self):
        for feature in setup.CODE_FEATURES:
            assert setup.filtered_grid(GRID, [feature])["code"] == ["github"]

    def test_no_features_leaves_nothing_selectable(self):
        assert setup.filtered_grid(GRID, []) == {"delivery": [], "code": [], "docs": []}


class TestDepth:
    def test_depth_needs_something_with_ticket_text(self):
        assert setup.depth_applies(["delivery"])
        assert setup.depth_applies(["ai_footprint"])
        assert not setup.depth_applies(["documentation"])

    def test_a_stale_deep_cannot_leak_into_a_docs_only_run(self):
        assert setup.effective_depth("deep", ["documentation"]) == "quick"
        assert setup.effective_depth("deep", ["delivery"]) == "deep"


class TestStepApplies:
    def test_three_steps_always_apply(self):
        for step in setup.ALWAYS_APPLICABLE:
            assert setup.step_applies(step, features=[])

    def test_every_step_is_answerable(self):
        assert set(setup.STEPS) >= setup.ALWAYS_APPLICABLE
        for step in setup.STEPS:
            setup.step_applies(step, features=["delivery"], components={"delivery": ["jira"]})

    def test_a_host_scope_step_needs_that_host_selected(self):
        kw = {"features": ["ai_footprint"]}
        assert setup.step_applies("github_owners", components={"code": ["github"]}, **kw)
        assert not setup.step_applies("github_owners", components={"code": ["azdo"]}, **kw)
        assert not setup.step_applies("azdo_projects", components={"code": ["github"]}, **kw)

    def test_a_host_scope_step_needs_a_code_feature(self):
        assert not setup.step_applies("github_owners", features=["documentation"], components={"code": ["github"]})

    def test_the_model_step_needs_a_deep_run_and_an_offer(self):
        assert setup.step_applies("model", features=["delivery"], depth="deep", model_offered=True)
        assert not setup.step_applies("model", features=["delivery"], depth="deep", model_offered=False)
        assert not setup.step_applies("model", features=["delivery"], depth="quick", model_offered=True)

    def test_the_window_step_belongs_to_the_scanning_features(self):
        assert setup.step_applies("window", features=["documentation"])
        assert setup.step_applies("window", features=["code_health"])
        assert not setup.step_applies("window", features=["delivery"])

    def test_members_re_scope_delivery_and_code_but_not_docs(self):
        assert setup.step_applies("members", features=["delivery"])
        assert setup.step_applies("members", features=["ai_footprint"])
        assert not setup.step_applies("members", features=["documentation"])

    def test_an_unknown_step_never_applies(self):
        assert not setup.step_applies("astrology", features=["delivery"])

    def test_a_solo_run_never_asks_for_members(self):
        # The Solo world has no roster to narrow; every other step is untouched.
        assert not setup.step_applies("members", features=["delivery"], solo=True)
        assert not setup.step_applies("members", features=["ai_footprint"], solo=True)
        assert setup.step_applies("features", features=["delivery"], solo=True)
        assert setup.step_applies("window", features=["documentation"], solo=True)


class TestRunConfig:
    def _state(self, **kw):
        base = {
            "features": ["delivery", "ai_footprint"],
            "components": {"delivery": ["jira"], "code": ["github"]},
            "github_owners": ["acme"],
            "azdo_projects": ["Infra"],
            "depth": "deep",
            "model": "llama3",
            "window_days": 60,
            "members": ["Ana"],
        }
        return {**base, **kw}

    def test_a_full_selection_reaches_the_run(self):
        config = setup.run_config(self._state(), roster_fallback=["jira"], model_offered=True)
        assert config["depth"] == "deep"
        assert config["model"] == "llama3"
        assert config["window_days"] == 60
        assert config["analysis_scope"] == {"github": ["acme"]}
        assert config["members_map"] == {"jira": ["Ana"]}

    def test_deselecting_a_code_host_coerces_its_stale_scope_out(self):
        state = self._state(components={"delivery": ["jira"], "code": []})
        assert setup.run_config(state, roster_fallback=["jira"])["analysis_scope"] == {}

    def test_an_unoffered_model_never_reaches_the_run(self):
        assert setup.run_config(self._state(), roster_fallback=["jira"], model_offered=False)["model"] is None

    def test_a_docs_only_run_is_quick_with_no_members(self):
        state = self._state(features=["documentation"], components={"docs": ["notion"]})
        config = setup.run_config(state, roster_fallback=["jira"])
        assert config["depth"] == "quick"
        assert config["members"] is None and config["members_map"] is None

    def test_a_solo_run_coerces_a_stale_member_pick_out(self):
        # A pick made before flipping to Solo must not narrow the run.
        config = setup.run_config(self._state(), roster_fallback=["jira"], model_offered=True, solo=True)
        assert config["members"] is None and config["members_map"] is None
        assert config["depth"] == "deep"  # everything else is untouched

    def test_the_window_falls_back_when_nothing_scans(self):
        state = self._state(features=["delivery"], components={"delivery": ["jira"]})
        assert setup.run_config(state, roster_fallback=["jira"])["window_days"] == setup.DEFAULT_WINDOW_DAYS

    def test_the_roster_fallback_names_the_trackers_when_delivery_is_off(self):
        state = self._state(features=["ai_footprint"], components={"code": ["github"]})
        assert setup.run_config(state, roster_fallback=["azdevops"])["members_map"] == {"azdevops": ["Ana"]}


class TestAvailableGrid:
    def test_it_reports_every_component(self, monkeypatch):
        monkeypatch.setattr(setup, "available_trackers", lambda: ["jira"])
        monkeypatch.setattr(setup, "offerable_code_sources", lambda: ["github"])
        monkeypatch.setattr(setup, "available_doc_sources", lambda: [])
        assert setup.available_grid() == {"delivery": ["jira"], "code": ["github"], "docs": []}

    def test_a_feature_exists_for_every_component_row(self):
        assert set(setup.FEATURES) == {"delivery", "ai_footprint", "code_health", "documentation"}
