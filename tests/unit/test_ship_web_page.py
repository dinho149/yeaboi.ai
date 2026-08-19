"""Contract tests for the ship board web page + its config getters.

These are the assertions that must hold even though ``make test`` never runs
Node: the document is self-contained, it leaks nothing (``GET /`` is
unauthenticated), and the JSON island handed to the bundle is well-formed and
script-safe. The behavioural tests live in ``frontend/src/ship/*``.

Named ``test_ship_web_page`` rather than ``test_ship_page`` because the latter
already exists for the *TUI* page loop (``ui/mode_select/_ship.py``). This file
covers ``ship/page.py`` — the browser document — the seam Python owns.
"""

from __future__ import annotations

import json

import pytest

from tests._pages import assert_self_contained, island
from yeaboi.ship.board import ShipBoard
from yeaboi.ship.page import _document_title, board_config, build_board_html
from yeaboi.ship.server import ShipServer


@pytest.fixture
def page() -> str:
    return build_board_html("Add a rate limiter", "yeaboi")


class TestSelfContained:
    def test_is_one_document_with_inline_assets(self, page: str):
        assert page.startswith("<!DOCTYPE html>")
        assert "<style>" in page and "<script>" in page

    def test_no_external_resources(self, page: str):
        # Not a preference: the tunnel CSP forbids every external origin and a
        # page opened over file:// cannot fetch one at all.
        assert_self_contained(page)
        assert 'src="http' not in page and 'href="http' not in page
        assert "cdn" not in page.lower()

    def test_bundle_is_not_a_module_script(self, page: str):
        # A type="module" script does not execute over file:// at all.
        assert '<script type="module"' not in page

    def test_mounts_into_root_with_a_noscript_fallback(self, page: str):
        assert '<div id="root">' in page
        assert "<noscript>" in page
        # The fallback points somewhere real — the run is fully watchable and
        # approvable from the terminal.
        assert "terminal" in page[page.index("<noscript>") : page.index("</noscript>")]

    def test_declares_the_ship_mode_accent(self, page: str):
        assert 'data-mode="ship"' in page


class TestBootIsland:
    def test_carries_only_the_static_chrome_and_names(self, page: str):
        boot = island(page)
        assert boot["story"] == "Add a rate limiter"
        assert boot["project"] == "yeaboi"
        assert "chrome" in boot

    def test_carries_no_run_state(self):
        """Status, phases, the diff and the verdict arrive over ``/api/state``.

        The page HTML is built once at server start, so anything live baked into
        the island would freeze at go-time — and the diff especially must never
        be here, since ``GET /`` is unauthenticated.
        """
        assert set(board_config()) == {"chrome", "story", "project"}

    def test_island_is_script_safe(self):
        """A hostile story title cannot close the ``<script>`` it rides in.

        ``json.dumps`` leaves ``<``/``>`` literal, so ``json_island`` escapes
        them — asserted here with a payload built from a hostile title.
        """
        html = build_board_html("</script><img src=x onerror=alert(1)>")
        assert "</script><img" not in html
        assert "\\u003c/script" in html
        assert island(html)["story"] == "</script><img src=x onerror=alert(1)>"

    def test_island_has_no_secrets(self, page: str):
        boot = island(page)
        flat = json.dumps(boot).lower()
        for forbidden in ("token", "admin", "secret", "password", "diff"):
            assert forbidden not in flat


class TestServedPageIsTokenFree:
    """The real check, against a running server rather than the builder.

    ``GET /`` is unauthenticated, so a token, the admin secret, or the join code
    baked into the page would hand access (or worse) to any peer that loads it.
    """

    def test_served_page_never_contains_a_secret(self):
        import urllib.request

        board = ShipBoard("run-t", db_path=None, story_title="Add a rate limiter", project_name="yeaboi")
        server = ShipServer(board, port=5491)
        server.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=5) as response:
                body = response.read().decode()
        finally:
            server.stop()

        assert server.token not in body
        assert server.admin_token not in body
        assert server.join_code not in body


class TestDocumentTitle:
    def test_names_both_when_both_are_known(self):
        assert _document_title("Add a rate limiter", "yeaboi") == "Ship — yeaboi · Add a rate limiter"

    @pytest.mark.parametrize(
        ("story", "project", "expected"),
        [
            ("Add a rate limiter", "", "Ship — Add a rate limiter"),
            ("", "yeaboi", "Ship — yeaboi"),
        ],
    )
    def test_names_whichever_it_has(self, story, project, expected):
        assert _document_title(story, project) == expected

    def test_falls_back_to_the_bare_mode_name(self):
        # A trailing "— " in the tab is worse than no suffix at all.
        assert _document_title("", "") == "Ship"
