"""The beta label reaches every surface, including the ones that can't import it.

Markdown and the plugin SKILL.md carry hand-written copies of the wording in
``src/yeaboi.beta``, and nothing else checks them — so this file is what stops
the copies drifting from the constant, and what catches a half-finished rollout.

The website's copies live in the yeaboi-site repo and are pinned there against
``contracts/site.json``; ``tests/unit/test_site_contract.py`` is the hop that
keeps that vocabulary equal to these constants.

Assertions deliberately use the SHORT phrase, never the full sentence: HTML
re-wraps at whatever width the author's editor chose, so a whole-sentence match
would fail for a purely cosmetic reformat.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yeaboi.beta import BETA_LABEL, BETA_TAG, PERFORMANCE_BETA_PHRASE

REPO = Path(__file__).resolve().parents[2]
PERFORMANCE_SKILL = REPO / "claude-plugin" / "yeaboi" / "skills" / "performance" / "SKILL.md"


class TestHandWrittenCopies:
    def test_plugin_skill_carries_the_phrase(self):
        assert PERFORMANCE_BETA_PHRASE in PERFORMANCE_SKILL.read_text(encoding="utf-8")

    def test_plugin_skill_frontmatter_is_tagged(self):
        # Reaches Claude at skill-selection time, before any tool description does.
        lines = PERFORMANCE_SKILL.read_text(encoding="utf-8").splitlines()
        description = next(line for line in lines if line.startswith("description:"))
        assert BETA_TAG in description

    def test_plugin_skill_name_is_untouched(self):
        # Load-bearing for test_claude_plugin and the surface-parity registry.
        lines = PERFORMANCE_SKILL.read_text(encoding="utf-8").splitlines()
        assert lines[1] == "name: performance"

    def test_readme_marks_the_mode(self):
        # Matched on "modes, one command" rather than the spelled-out count: the
        # count is incidental to what this asserts, and hardcoding it breaks the
        # test every time a mode ships (it did, when Poker made the count seven).
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        modes_line = next(line for line in readme.splitlines() if "modes, one command" in line)
        assert "beta" in modes_line.lower()


class TestBetaMarkersAgree:
    """The three in-app beta markers must move together.

    A card badged BETA with no ``_BETA_MODES`` entry ships a chip and no notice —
    ``show_beta_notice`` returns True for an unregistered key, so the gate simply
    doesn't appear and nothing complains. Every *other* copy of the wording is
    pinned by the tests above; this is the one link that isn't, so it gets the
    two-way set equality the surface-parity registry uses.
    """

    def _badged_card_keys(self) -> set[str]:
        # Badged cards span every category menu (Solo + Team + Agents).
        from yeaboi.ui.mode_select.screens._screens import _AGENT_CARDS, _MODE_CARDS, _SOLO_CARDS

        return {card["key"] for card in (*_SOLO_CARDS, *_MODE_CARDS, *_AGENT_CARDS) if card.get("badge") == BETA_LABEL}

    def test_every_badged_card_has_an_entry_notice(self):
        from yeaboi.ui.shared._beta_notice import _BETA_MODES

        assert self._badged_card_keys() == set(_BETA_MODES)

    def test_every_badged_card_has_a_beta_tip(self):
        from yeaboi.ui.shared._tips import _FEATURE_TIPS

        beta_tip_modes = {tip.mode_key for tip in _FEATURE_TIPS if tip.is_beta}
        assert self._badged_card_keys() == beta_tip_modes

    def test_beta_is_not_also_flagged_new(self):
        # Different claims: new is recent-and-verified, beta is unverified.
        from yeaboi.ui.shared._tips import _FEATURE_TIPS

        assert [tip.key for tip in _FEATURE_TIPS if tip.is_beta and tip.is_new] == []

    def test_a_badged_card_stays_selectable(self):
        # Beta labels a mode; it does not take it away. `available` gates Enter,
        # the click handler and the welcome screen's `g` jump key.
        from yeaboi.ui.mode_select.screens._screens import _AGENT_CARDS, _MODE_CARDS, _SOLO_CARDS

        for card in (*_SOLO_CARDS, *_MODE_CARDS, *_AGENT_CARDS):
            if card.get("badge") == BETA_LABEL:
                assert card["available"] is True, card["key"]


class TestConstantsAreUsedNotRetyped:
    """Python surfaces import the constants; only the un-importable ones retype."""

    @pytest.mark.parametrize(
        "module",
        ["cli.py", "ui/shared/_tips.py", "ui/shared/_beta_notice.py", "mcp/tools_performance.py"],
    )
    def test_python_surface_imports_from_beta(self, module):
        source = (REPO / "src" / "yeaboi" / module).read_text(encoding="utf-8")
        assert "from yeaboi.beta import" in source, module

    def test_mcp_descriptions_are_the_one_allowed_python_retype(self):
        # FastMCP captures fn.__doc__ at decoration time, so these can't be
        # f-strings; this assertion is what keeps the five literals honest.
        # Matched at the docstring opening so the explanatory comment above them
        # doesn't count itself.
        source = (REPO / "src" / "yeaboi" / "mcp" / "tools_performance.py").read_text(encoding="utf-8")
        assert source.count(f'"""{BETA_LABEL} — ') == 5
