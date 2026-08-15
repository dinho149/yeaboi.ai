"""Contract tests for the planning-poker page.

The seam only — everything Python is responsible for. The board's behaviour is
tested in ``frontend/src/poker/*.test.tsx``, and the wire shape the bundle is
coded against is pinned by ``test_web_wire_shapes.py``.

Absorbed the old ``test_poker_page.py`` when the hand-written page was deleted:
with one page there is one test file, per the one-file-per-module rule.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._pages import assert_self_contained, island, without_inline_payloads
from yeaboi.poker.board import POKER_DECK, PokerBoard
from yeaboi.poker.page import _document_title, board_config, build_poker_html
from yeaboi.poker.server import PokerServer
from yeaboi.retro.board import AVATARS, RETRO_THEMES


@pytest.fixture
def page() -> str:
    return build_poker_html("yeaboi", "Sprint 42")


class TestSelfContained:
    def test_is_one_document_with_inline_assets(self, page: str):
        assert page.startswith("<!DOCTYPE html>")
        assert "<style>" in page and "<script>" in page

    def test_no_external_resources(self, page: str):
        assert_self_contained(page)
        # Against the markup, not the inlined bytes: a base64 font contains any
        # three-letter run by chance, and an external reference can only ever
        # appear in markup anyway.
        markup_only = without_inline_payloads(page)
        assert 'src="http' not in markup_only and 'href="http' not in markup_only
        assert "cdn" not in markup_only.lower()

    def test_bundle_is_not_a_module_script(self, page: str):
        assert '<script type="module"' not in page

    def test_mounts_into_root_with_a_noscript_fallback(self, page: str):
        assert '<div id="root">' in page
        assert "<noscript>" in page
        assert "export" in page[page.index("<noscript>") : page.index("</noscript>")]

    def test_declares_the_poker_mode_accent(self, page: str):
        """`data-mode="poker"` is what makes the board gold rather than teal.

        The accent is not decoration here: it is how someone with two tunnel
        tabs open tells a retro from a poker session at a glance.
        """
        assert 'data-mode="poker"' in page


class TestBootIsland:
    def test_carries_the_chrome_word_lists_and_stations(self, page: str):
        boot = island(page)
        assert boot["chrome"]["title"] == "Planning Poker"
        assert boot["chrome"]["subtitle"] == "yeaboi"
        assert boot["chrome"]["facts"] == [["PROJECT", "yeaboi"], ["SCOPE", "Sprint 42"]]
        assert boot["scope"] == "Sprint 42"
        assert len(boot["adjectives"]) > 5 and len(boot["nouns"]) > 5
        assert any(channel["name"] == "Lofi" for channel in boot["musicChannels"])

    def test_omits_what_the_generated_enums_already_pin(self):
        """The deck, the avatars and the palettes must NOT be in the island.

        All three are server-validated tuples that ``scripts/gen_web_types.py``
        emits into ``types/enums.ts`` from these same constants. Shipping them
        here as well would let a stale bundle offer a card the board refuses,
        because the island would win at runtime.
        """
        assert set(board_config()) == {"chrome", "scope", "adjectives", "nouns", "musicChannels"}

    def test_deck_and_avatars_are_not_duplicated_into_the_payload(self, page: str):
        boot = island(page)
        flat = json.dumps(boot, ensure_ascii=False)
        assert "deck" not in boot and "avatars" not in boot and "themes" not in boot
        assert "☕" not in flat  # the deck's most distinctive member
        # Guards the reasoning rather than the payload: a failure here means the
        # generated enums drifted from the board's own tuples.
        assert POKER_DECK[-1] == "☕"
        assert list(RETRO_THEMES) == ["midnight", "light", "solarized", "synthwave", "forest"]
        assert AVATARS[0] == "🤠"

    def test_island_is_script_safe(self):
        """A project name cannot close the `<script>` it is embedded in."""
        html = build_poker_html("</script><img src=x onerror=alert(1)>")
        assert "</script><img" not in html
        assert "\\u003c/script" in html
        assert island(html)["chrome"]["subtitle"] == "</script><img src=x onerror=alert(1)>"

    def test_island_has_no_secrets(self, page: str):
        boot = island(page)
        flat = json.dumps(boot).lower()
        for forbidden in ("token", "admin", "secret", "password", "code"):
            assert forbidden not in flat


class TestNoHandWrittenPageSurvives:
    """The old page is gone, not merely unreachable.

    Its load-bearing markers: the inline bootstrap that read the token straight
    out of ``location.search``, and ``paintConsole``, which repainted the host
    dock by id on every poll. If either reappears in the served document,
    something has re-imported the old renderer rather than the bundle.
    """

    def test_the_inline_bootstrap_is_gone(self, page: str):
        assert "let TOKEN = new URLSearchParams" not in page
        assert "paintConsole" not in page

    def test_the_page_no_longer_reaches_into_another_mode(self):
        """The word lists come from a shared leaf module, not retro's renderer.

        ``poker/page.py`` used to import ``_ADJECTIVES`` — a private name — out
        of ``retro/page.py``, which was the last cross-mode page coupling. It
        only existed because that file happened to be where the lists lived.
        """
        source = (Path(__file__).resolve().parents[2] / "src" / "yeaboi" / "poker" / "page.py").read_text()
        # Imports, not the word: the docstring is free to *mention* retro, and
        # asserting on the raw string would forbid explaining the history.
        imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        assert not [line for line in imports if "retro" in line], imports
        assert any("from yeaboi.names import" in line for line in imports)


class TestServedPageIsTokenFree:
    """``GET /`` is unauthenticated, so a baked token would hand out access."""

    def test_served_page_never_contains_the_token(self):
        import urllib.request

        board = PokerBoard("t", project_name="yeaboi", scope_label="Sprint 42", tickets=[{"key": "YB-1"}])
        server = PokerServer(board, port=5499)
        server.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=5) as response:
                body = response.read().decode()
        finally:
            server.stop()

        assert server.token not in body
        assert server.admin_token not in body
        assert server.join_code not in body

    def test_served_page_carries_the_board_titles(self):
        """The server has to hand the board's own names to the builder.

        Easy to get wrong invisibly: ``build_poker_html()`` still has defaults
        for both, so forgetting to pass them renders a board with a blank
        subtitle rather than raising anything.
        """
        import urllib.request

        board = PokerBoard("t", project_name="yeaboi", scope_label="Sprint 42", tickets=[{"key": "YB-1"}])
        server = PokerServer(board, port=5499)
        server.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=5) as response:
                boot = island(response.read().decode())
        finally:
            server.stop()

        assert boot["chrome"]["subtitle"] == "yeaboi"
        assert boot["scope"] == "Sprint 42"


class TestDocumentTitle:
    """A host with a board per team had every tab reading "Planning Poker"."""

    def test_names_both_when_both_are_known(self):
        assert _document_title("yeaboi", "Sprint 42") == "Planning Poker — yeaboi · Sprint 42"

    @pytest.mark.parametrize(
        ("project", "scope", "expected"),
        [
            ("yeaboi", "", "Planning Poker — yeaboi"),
            ("", "Sprint 42", "Planning Poker — Sprint 42"),
        ],
    )
    def test_names_whichever_it_has(self, project, scope, expected):
        assert _document_title(project, scope) == expected

    def test_falls_back_to_the_bare_mode_name(self):
        # A headless or demo board has neither name to offer.
        assert _document_title("", "") == "Planning Poker"
