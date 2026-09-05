"""The context-flow facts every Projects surface draws (projects/flow.py)."""

from __future__ import annotations

import re

from yeaboi.projects.flow import AGENTS_FLOW_LINE, FLOW, flow_for
from yeaboi.projects.scope import CONTEXT_DEP_TOKENS
from yeaboi.ui.mode_select.screens._screens import _MODE_CARDS, _SOLO_CARDS

_TEAM_KEYS = {card["key"] for card in _MODE_CARDS}
_SOLO_KEYS = {card["key"] for card in _SOLO_CARDS}


class TestFacts:
    def test_every_read_is_a_context_dep_token(self):
        for step in FLOW:
            assert set(step.reads) <= set(CONTEXT_DEP_TOKENS), step.key

    def test_every_key_is_a_team_card(self):
        assert {step.key for step in FLOW} <= _TEAM_KEYS

    def test_fragments_follow_the_door_rules(self):
        for text in [step.leaves for step in FLOW] + [AGENTS_FLOW_LINE]:
            assert "→" not in text and "·" not in text
            assert not re.search(r"\b[A-Z]{2,}\b", text), text
            assert text[0].islower() or text == AGENTS_FLOW_LINE

    def test_plan_comes_first(self):
        assert FLOW[0].key == "project-planning"


class TestFlowFor:
    def test_team_keeps_every_step_in_order(self):
        assert [s.key for s in flow_for("team", _TEAM_KEYS)] == [s.key for s in FLOW]

    def test_solo_drops_retro_and_poker(self):
        keys = [s.key for s in flow_for("solo", _SOLO_KEYS)]
        assert "retro" not in keys and "poker" not in keys
        assert keys[0] == "project-planning" and "daily-standup" in keys

    def test_agents_has_no_flow(self):
        assert flow_for("agents", _TEAM_KEYS) == ()

    def test_unknown_keys_are_ignored(self):
        assert flow_for("team", {"nope"}) == ()
