"""The beta label reaches every surface, including the ones that can't import it.

HTML, Markdown and the plugin SKILL.md carry hand-written copies of the wording
in ``src/yeaboi.beta``. Nothing else checks them — the docs site has no test and
no CI job — so this file is what stops the copies drifting from the constant, and
what catches a half-finished rollout.

Assertions deliberately use the SHORT phrase, never the full sentence: HTML
re-wraps at whatever width the author's editor chose, so a whole-sentence match
would fail for a purely cosmetic reformat.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from yeaboi.beta import BETA_LABEL, BETA_RGB, BETA_TAG, PERFORMANCE_BETA_PHRASE

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"
PERFORMANCE_SKILL = REPO / "claude-plugin" / "yeaboi" / "skills" / "performance" / "SKILL.md"

# Pages that describe Performance to a reader and must carry the caveat.
BETA_DOC_PAGES = (
    DOCS / "index.html",
    DOCS / "docs" / "modes" / "index.html",
    DOCS / "docs" / "modes" / "performance.html",
)


class TestHandWrittenCopies:
    @pytest.mark.parametrize("page", BETA_DOC_PAGES, ids=lambda p: p.name)
    def test_docs_page_carries_the_phrase(self, page):
        assert PERFORMANCE_BETA_PHRASE in page.read_text(encoding="utf-8")

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


class TestDocsPill:
    def test_beta_pill_class_is_defined(self):
        css = (DOCS / "assets" / "site.css").read_text(encoding="utf-8")
        assert ".beta-pill{" in css

    def test_pill_colour_matches_the_terminal_chip(self):
        css = (DOCS / "assets" / "site.css").read_text(encoding="utf-8")
        pill = css.split(".beta-pill{", 1)[1].split("}", 1)[0]
        assert f"rgb({BETA_RGB[0]},{BETA_RGB[1]},{BETA_RGB[2]})" in pill

    def test_pill_is_not_the_live_badge(self):
        # `.badge::before` injects a green "live" dot, which says the opposite of
        # what a beta marker means. The pill must stay its own class.
        css = (DOCS / "assets" / "site.css").read_text(encoding="utf-8")
        pill = css.split(".beta-pill{", 1)[1].split("}", 1)[0]
        assert "var(--success)" not in pill

    def test_every_page_using_the_pill_links_site_css(self):
        for page in DOCS.rglob("*.html"):
            text = page.read_text(encoding="utf-8")
            if 'class="beta-pill"' in text:
                assert "/assets/site.css" in text, page


class TestCacheBust:
    def test_all_docs_pages_share_one_cache_bust_version(self):
        """A new CSS class behind a stale cached stylesheet is an invisible badge.

        That failure mode is silent — the page renders, the pill just doesn't —
        so a half-finished `?v=` sweep is exactly the kind of mistake that ships.
        """
        versions: dict[str, set[str]] = {}
        for page in DOCS.rglob("*.html"):
            found = set(re.findall(r"\?v=(\d+)", page.read_text(encoding="utf-8")))
            if found:
                versions[page.relative_to(DOCS).as_posix()] = found

        assert versions, "no cache-busted asset links found — did the convention change?"
        all_versions = set().union(*versions.values())
        assert len(all_versions) == 1, f"mixed cache-bust versions: {versions}"


class TestBetaMarkersAgree:
    """The three in-app beta markers must move together.

    A card badged BETA with no ``_BETA_MODES`` entry ships a chip and no notice —
    ``show_beta_notice`` returns True for an unregistered key, so the gate simply
    doesn't appear and nothing complains. Every *other* copy of the wording is
    pinned by the tests above; this is the one link that isn't, so it gets the
    two-way set equality the surface-parity registry uses.
    """

    def _badged_card_keys(self) -> set[str]:
        # Badged cards span both category menus (Humans + Agents).
        from yeaboi.ui.mode_select.screens._screens import _AGENT_CARDS, _MODE_CARDS

        return {card["key"] for card in (*_MODE_CARDS, *_AGENT_CARDS) if card.get("badge") == BETA_LABEL}

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
        from yeaboi.ui.mode_select.screens._screens import _AGENT_CARDS, _MODE_CARDS

        for card in (*_MODE_CARDS, *_AGENT_CARDS):
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
