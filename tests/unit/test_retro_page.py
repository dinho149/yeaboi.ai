"""Contract tests for the retro board page + the retro config getters.

These are the assertions that must hold even though ``make test`` never runs
Node: the document is self-contained, it leaks nothing, and the JSON island it
hands the bundle is both well-formed and script-safe.

The behavioural tests live in ``frontend/src/retro/*.test.tsx``. What is checked
here is the *seam* — everything Python is responsible for.

Merged from ``test_retro_react_page.py`` when the legacy hand-written board was
deleted: with one page there is one test file, per the one-file-per-module rule.
"""

from __future__ import annotations

import json
import re

import pytest

from yeaboi.retro.board import RETRO_THEMES, RetroBoard
from yeaboi.retro.page import board_config, build_board_html
from yeaboi.retro.server import RetroServer


@pytest.fixture
def page() -> str:
    return build_board_html("Sprint 42")


def _island(html: str) -> dict:
    """The parsed boot payload. Fails loudly if the island is malformed."""
    match = re.search(r'<script type="application/json" id="yeaboi-data">(.*?)</script>', html, re.S)
    assert match is not None, "no boot island in the page"
    return json.loads(match.group(1))


class TestSelfContained:
    def test_is_one_document_with_inline_assets(self, page: str):
        assert page.startswith("<!DOCTYPE html>")
        assert "<style>" in page and "<script>" in page

    def test_no_external_resources(self, page: str):
        # Not a style preference: the tunnel CSP forbids every external origin,
        # and a page opened over file:// cannot fetch one at all. Resource
        # *tags* are banned rather than any URL — the music stream URLs in the
        # island are the one deliberate exception, and they are audio, not code.
        assert "<link" not in page
        assert 'src="http' not in page and 'href="http' not in page
        assert "cdn" not in page.lower()

    def test_bundle_is_not_a_module_script(self, page: str):
        # A type="module" script does not execute over file:// at all, and the
        # boards share their build with the exports. Classic IIFE or nothing.
        assert '<script type="module"' not in page

    def test_mounts_into_root_with_a_noscript_fallback(self, page: str):
        assert '<div id="root">' in page
        assert "<noscript>" in page
        # The board is a live surface with no static rendering to fall back to,
        # so the fallback must point somewhere real rather than just apologise.
        assert "export" in page[page.index("<noscript>") : page.index("</noscript>")]

    def test_declares_the_retro_mode_accent(self, page: str):
        assert 'data-mode="retro"' in page

    def test_no_hand_written_board_survives(self, page: str):
        """The legacy page is gone, not merely unreachable.

        Its two load-bearing markers: the inline bootstrap that read the token
        straight out of ``location.search``, and the ``editingHere`` guard that
        froze a whole column while one person had an editor open. If either
        reappears in the served document, something has re-imported the old
        renderer rather than the bundle.
        """
        assert "let TOKEN = new URLSearchParams" not in page
        assert "editingHere" not in page


class TestBootIsland:
    def test_carries_the_word_lists_and_stations(self, page: str):
        boot = _island(page)
        assert boot["sprint"] == "Sprint 42"
        assert len(boot["adjectives"]) > 5 and len(boot["nouns"]) > 5
        assert all(set(channel) == {"name", "url"} for channel in boot["musicChannels"])
        assert any(channel["name"] == "Lofi" for channel in boot["musicChannels"])

    def test_omits_what_the_generated_enums_already_pin(self):
        """Grids, statuses, emojis, avatars and themes must NOT be in the island.

        They are server-validated tuples, and ``scripts/gen_web_types.py``
        emits them into ``types/enums.ts`` from the same constants with a
        ``--check`` in CI. Carrying them here as well would give one tuple two
        sources of truth, and the island would win at runtime — so a stale
        bundle would render a board whose columns disagree with the server's.
        """
        assert set(board_config()) == {"title", "sprint", "adjectives", "nouns", "musicChannels"}

    def test_theme_names_are_not_duplicated_into_the_payload(self, page: str):
        boot = _island(page)
        assert "themes" not in boot
        # Guards the reasoning above rather than the payload: if this ever fails
        # it means the generated enums drifted from the board.
        assert list(RETRO_THEMES) == ["midnight", "light", "solarized", "synthwave", "forest"]

    def test_island_is_script_safe(self):
        """A card title cannot close the `<script>` element it is embedded in.

        Inside a `<script>` the tokenizer is in script-data state, where
        `</script`, `<!--` and `<script` all change parsing. `json.dumps` leaves
        `<` and `>` literal, so `json_island` escapes them — asserted here with
        a payload built from a hostile sprint name.
        """
        html = build_board_html("</script><img src=x onerror=alert(1)>")
        assert "</script><img" not in html
        assert "\\u003c/script" in html
        # …and it is still valid JSON that parses back to exactly what went in.
        assert _island(html)["sprint"] == "</script><img src=x onerror=alert(1)>"

    def test_island_has_no_secrets(self, page: str):
        boot = _island(page)
        flat = json.dumps(boot).lower()
        for forbidden in ("token", "admin", "secret", "password", "code"):
            assert forbidden not in flat


class TestServedPageIsTokenFree:
    """The real check, against a running server rather than the builder.

    ``GET /`` is unauthenticated, so a token baked into the page would hand
    access to any LAN peer that loads it. Asserting on a string in the source is
    a proxy; asserting the running server's own token is absent from its own
    response body is the property itself.
    """

    def test_served_page_never_contains_the_token(self):
        import urllib.request

        server = RetroServer(RetroBoard("t", sprint_name="Sprint 42"), port=5399)
        server.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=5) as response:
                body = response.read().decode()
        finally:
            server.stop()

        assert server.token not in body
        assert server.admin_token not in body
        assert server.join_code not in body


class TestConfig:
    def test_default_port(self, monkeypatch):
        from yeaboi import config

        monkeypatch.delenv("RETRO_PORT", raising=False)
        assert config.get_retro_server_port() == 5173

    def test_env_override(self, monkeypatch):
        from yeaboi import config

        monkeypatch.setenv("RETRO_PORT", "6000")
        assert config.get_retro_server_port() == 6000

    def test_bad_env_falls_back(self, monkeypatch):
        from yeaboi import config

        monkeypatch.setenv("RETRO_PORT", "notanint")
        assert config.get_retro_server_port() == 5173
