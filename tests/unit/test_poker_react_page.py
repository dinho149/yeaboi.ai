"""Contract tests for the React planning-poker page.

The seam only — everything Python is responsible for. The board's behaviour is
tested in ``frontend/src/poker/*.test.tsx``, and the wire shape the bundle is
coded against is pinned by ``test_web_wire_shapes.py``.

Merges into ``test_poker_page.py`` when the flag goes, exactly as retro's did.
"""

from __future__ import annotations

import json
import re

import pytest

from yeaboi.poker.board import POKER_DECK, PokerBoard
from yeaboi.poker.page import board_config, build_poker_html, build_react_poker_html
from yeaboi.poker.server import PokerServer
from yeaboi.retro.board import AVATARS, RETRO_THEMES


@pytest.fixture
def page() -> str:
    return build_react_poker_html("yeaboi", "Sprint 42")


def _island(html: str) -> dict:
    match = re.search(r'<script type="application/json" id="yeaboi-data">(.*?)</script>', html, re.S)
    assert match is not None, "no boot island in the page"
    return json.loads(match.group(1))


class TestSelfContained:
    def test_is_one_document_with_inline_assets(self, page: str):
        assert page.startswith("<!DOCTYPE html>")
        assert "<style>" in page and "<script>" in page

    def test_no_external_resources(self, page: str):
        assert "<link" not in page
        assert 'src="http' not in page and 'href="http' not in page
        assert "cdn" not in page.lower()

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
    def test_carries_the_titles_word_lists_and_stations(self, page: str):
        boot = _island(page)
        assert boot["title"] == "yeaboi"
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
        assert set(board_config()) == {"title", "scope", "adjectives", "nouns", "musicChannels"}

    def test_deck_and_avatars_are_not_duplicated_into_the_payload(self, page: str):
        boot = _island(page)
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
        html = build_react_poker_html("</script><img src=x onerror=alert(1)>")
        assert "</script><img" not in html
        assert "\\u003c/script" in html
        assert _island(html)["title"] == "</script><img src=x onerror=alert(1)>"

    def test_island_has_no_secrets(self, page: str):
        boot = _island(page)
        flat = json.dumps(boot).lower()
        for forbidden in ("token", "admin", "secret", "password", "code"):
            assert forbidden not in flat


class TestUiFlag:
    def test_defaults_to_the_legacy_page(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("POKER_UI", raising=False)
        # The legacy page's marker: a hand-written inline bootstrap, not a bundle.
        assert "let TOKEN = new URLSearchParams" in build_poker_html()

    def test_react_flag_selects_the_bundle(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("POKER_UI", "react")
        html = build_poker_html("yeaboi", "Sprint 42")
        assert "let TOKEN = new URLSearchParams" not in html
        assert 'id="yeaboi-data"' in html

    def test_unknown_value_keeps_the_legacy_page(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("POKER_UI", "preact")
        assert "let TOKEN = new URLSearchParams" in build_poker_html()


class TestServedPageIsTokenFree:
    """``GET /`` is unauthenticated, so a baked token would hand out access."""

    @pytest.mark.parametrize("ui", ["legacy", "react"])
    def test_served_page_never_contains_the_token(self, monkeypatch: pytest.MonkeyPatch, ui: str):
        import urllib.request

        monkeypatch.setenv("POKER_UI", ui)
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

    def test_react_page_carries_the_board_titles(self, monkeypatch: pytest.MonkeyPatch):
        """The server has to hand the board's own names to the builder.

        Easy to get wrong invisibly: ``build_poker_html()`` still has defaults
        for both, so forgetting to pass them renders a board with a blank
        subtitle rather than raising anything.
        """
        import urllib.request

        monkeypatch.setenv("POKER_UI", "react")
        board = PokerBoard("t", project_name="yeaboi", scope_label="Sprint 42", tickets=[{"key": "YB-1"}])
        server = PokerServer(board, port=5499)
        server.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=5) as response:
                boot = _island(response.read().decode())
        finally:
            server.stop()

        assert boot["title"] == "yeaboi"
        assert boot["scope"] == "Sprint 42"
