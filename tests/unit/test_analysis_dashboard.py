"""The team-analysis card vocabulary — shared by the TUI and the desktop."""

from yeaboi.analysis import dashboard
from yeaboi.team_profile import AiAdoptionSignal, DocQualitySignal, TeamProfile

PROFILE = TeamProfile(team_id="jira-PROJ-1", source="jira", project_key="PROJ")


class TestVocabulary:
    def test_every_card_has_a_title(self):
        assert all(dashboard.CARD_TITLES[key] for key in dashboard.CARD_ORDER)

    def test_the_global_cards_are_not_delivery_cards(self):
        assert not set(dashboard.GLOBAL_CARDS) & set(dashboard.DELIVERY_CARD_ORDER)

    def test_every_feature_unlocks_a_real_card(self):
        assert set(dashboard.FEATURE_CARDS.values()) <= set(dashboard.CARD_ORDER)


class TestVisibleCardOrder:
    def test_a_delivery_profile_shows_the_delivery_cards_with_insights_last(self):
        order = dashboard.visible_card_order(PROFILE, False, False)
        assert order[0] == "velocity"
        assert order[-1] == "insights"
        assert set(order) == set(dashboard.DELIVERY_CARD_ORDER)

    def test_the_global_cards_do_not_need_a_tracker(self):
        # They are global scans — switching the delivery toggle must not move them.
        assert dashboard.visible_card_order(None, True, True) == ("ai-adoption", "documentation")

    def test_code_health_sorts_above_the_ai_powered_cards(self):
        order = dashboard.visible_card_order(None, True, True, has_code_health=True)
        assert order.index("code-health") < order.index("ai-adoption")

    def test_an_explicit_feature_list_gates_the_global_cards(self):
        order = dashboard.visible_card_order(
            None, True, True, has_code_health=True, analysis_features=["documentation"]
        )
        assert order == ("documentation",)

    def test_no_feature_list_means_everything_that_ran(self):
        order = dashboard.visible_card_order(None, True, True, has_code_health=True, analysis_features=None)
        assert order == ("code-health", "ai-adoption", "documentation")

    def test_never_empty(self):
        # A blank results page would be worse than a card explaining itself.
        assert dashboard.visible_card_order(None, False, False) == ("ai-adoption",)


class TestComponentPresence:
    def test_a_fresh_run_reads_the_top_level_signals(self):
        present = dashboard.component_presence(None, code_signal=object(), doc_signal=object())
        assert present["code"] and present["docs"]

    def test_a_stored_profile_reads_the_scan_off_itself(self):
        # No top-level signals when browsing history — the global scan was
        # persisted onto the profile.
        stored = TeamProfile(
            team_id="jira-PROJ-1",
            source="jira",
            project_key="PROJ",
            ai_adoption=AiAdoptionSignal(scanned_commits=12),
            doc_quality=DocQualitySignal(pages_scanned=4),
        )
        present = dashboard.component_presence(stored)
        assert present["code"] and present["docs"]

    def test_an_empty_scan_is_not_presence(self):
        stored = TeamProfile(
            team_id="jira-PROJ-1",
            source="jira",
            project_key="PROJ",
            ai_adoption=AiAdoptionSignal(),
            doc_quality=DocQualitySignal(),
        )
        present = dashboard.component_presence(stored)
        assert not present["code"] and not present["docs"]

    def test_repository_health_in_either_place_earns_the_card(self):
        assert dashboard.component_presence(None, code_examples={"repository_health": {"x": 1}})["code_health"]
        assert dashboard.component_presence(None, examples={"ai_adoption": {"repository_health": {"x": 1}}})[
            "code_health"
        ]

    def test_a_selected_code_health_feature_earns_the_card_when_the_scan_ran(self):
        assert dashboard.component_presence(None, code_examples={}, analysis_features=["code_health"])["code_health"]
        # …but not when no code scan ran at all.
        assert not dashboard.component_presence(None, analysis_features=["code_health"])["code_health"]


class TestCards:
    def test_cards_carry_key_and_title_in_order(self):
        cards = dashboard.cards(PROFILE, code_signal=object())
        assert [c["key"] for c in cards] == list(
            dashboard.visible_card_order(PROFILE, True, False),
        )
        assert all(c["title"] == dashboard.CARD_TITLES[c["key"]] for c in cards)


class TestTuiParity:
    def test_the_tui_page_draws_exactly_these_cards(self):
        from yeaboi.ui.mode_select.screens._analysis_sections import _TA_CARDS

        assert list(_TA_CARDS) == list(dashboard.CARD_ORDER)
        assert {k: v["title"] for k, v in _TA_CARDS.items()} == dashboard.CARD_TITLES

    def test_every_card_has_a_terminal_builder(self):
        from yeaboi.ui.mode_select.screens._analysis_sections import _TA_CARDS

        assert all(_TA_CARDS[key]["builders"] for key in dashboard.CARD_ORDER)
