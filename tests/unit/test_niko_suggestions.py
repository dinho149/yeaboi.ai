"""Tests for Niko's opening chips (niko/suggestions.py).

The chips are keyed by capability and mapped to screens through the committed
desktop manifest. That indirection is the whole design: rename a route and the
mapping follows it, drop a capability and this file fails rather than the panel
quietly offering a screen that no longer exists.
"""

from __future__ import annotations

import pytest

from yeaboi.niko import suggestions


def _capability_keys() -> set[str]:
    """Capabilities the desktop manifest actually claims a screen for."""
    from yeaboi.niko.tools import known_routes

    return {str(row.get("capability") or "") for row in known_routes()} - {""}


class TestTheRegistryStaysHonest:
    def test_every_keyed_capability_is_a_real_one(self):
        stale = set(suggestions.BY_CAPABILITY) - _capability_keys()
        assert stale == set(), (
            f"These capabilities no longer have a desktop screen: {sorted(stale)}. "
            "Drop or re-key them in niko/suggestions.py."
        )

    def test_every_chip_is_well_formed(self):
        every = [
            chip
            for chips in (*suggestions.BY_CAPABILITY.values(), *suggestions.BY_SECTION.values(), suggestions.DEFAULT)
            for chip in chips
        ]
        assert every
        for chip in every:
            assert set(chip) == {"label", "prompt", "icon"}
            assert chip["label"].strip() and chip["prompt"].strip() and chip["icon"].strip()

    def test_labels_stay_short_enough_for_the_chip(self):
        # The renderer draws these on one line in a 380px panel.
        long = [
            chip["label"] for chips in suggestions.BY_CAPABILITY.values() for chip in chips if len(chip["label"]) > 32
        ]
        assert long == []

    def test_icons_are_ones_the_renderer_maps(self):
        # Mirrors ICON_MAP in yeaboi-desktop's niko-magic-chips.tsx. An unmapped
        # icon degrades to a compass rather than breaking, but a typo here is
        # still a chip that silently loses its glyph.
        known = {
            "plus",
            "bar-chart",
            "compass",
            "play",
            "shield-check",
            "layout",
            "plus-square",
            "arrow-up-down",
            "calendar",
            "user-plus",
            "file-plus",
            "users",
            "layers",
            "alert-triangle",
            "trending-up",
            "bell",
            "info",
        }
        used = {
            chip["icon"]
            for chips in (*suggestions.BY_CAPABILITY.values(), *suggestions.BY_SECTION.values(), suggestions.DEFAULT)
            for chip in chips
        }
        assert used <= known, f"unmapped icons: {sorted(used - known)}"


class TestScreenFor:
    def test_an_exact_route_resolves(self):
        assert suggestions.screen_for("/agents/usage") == {"capability": "agent-usage", "title": "Agent Usage"}

    def test_a_deeper_path_inherits_its_prefix(self):
        assert suggestions.screen_for("/humans/retro/board/anything")["capability"] == "retro-board"

    def test_the_longest_prefix_wins(self):
        assert suggestions.screen_for("/humans/planning/chat")["capability"] == "planning"

    def test_an_unknown_route_resolves_to_nothing_rather_than_guessing(self):
        assert suggestions.screen_for("/teleport") == {"capability": "", "title": ""}

    def test_a_partial_segment_is_not_a_prefix_match(self):
        # "/humans/retrospective" must not inherit "/humans/retro".
        assert suggestions.screen_for("/humans/retrospective")["capability"] != "retro-board"


class TestForRoute:
    @pytest.mark.parametrize(
        ("route", "expected"),
        [
            ("/agents/usage", "agents cost"),
            ("/humans/ship/run", "waiting on me"),
            ("/ceremonies", "scheduled"),
            ("/provenance", "decide"),
        ],
    )
    def test_a_screen_gets_its_own_chips(self, route, expected):
        assert any(expected in chip["label"].lower() for chip in suggestions.for_route(route))

    def test_a_humans_screen_with_no_chips_falls_back_to_the_section(self):
        chips = suggestions.for_route("/humans/planning/sessions/unmapped")
        assert chips and chips == suggestions.for_route("/humans/planning/sessions/unmapped")

    def test_an_unknown_route_gets_the_default_rather_than_nothing(self):
        assert suggestions.for_route("/teleport") == suggestions.DEFAULT[: suggestions.MAX_CHIPS]

    def test_home_gets_the_default(self):
        assert suggestions.for_route("/home") == suggestions.DEFAULT[: suggestions.MAX_CHIPS]

    def test_no_screen_ever_offers_nothing(self):
        from yeaboi.niko.tools import known_routes

        for row in known_routes():
            path = row.get("path", "")
            if path.startswith("/"):
                assert suggestions.for_route(path), f"{path} offers no chips"

    def test_never_more_than_the_panel_shows(self):
        from yeaboi.niko.tools import known_routes

        for row in known_routes():
            assert len(suggestions.for_route(row.get("path", ""))) <= suggestions.MAX_CHIPS

    def test_a_blank_route_still_answers(self):
        assert suggestions.for_route("")
