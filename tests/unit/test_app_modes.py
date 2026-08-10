"""The mode catalogue, and its agreement with the terminal app.

The web app declares its own list rather than importing the TUI's — see
`app/modes.py` for why. That only works if something notices when the two
drift, which is what this file is.
"""

from __future__ import annotations

import json

import pytest

from tests._app import call, sign_in
from yeaboi.app.modes import BY_KEY, MODES, payload
from yeaboi.app.server import AppServer
from yeaboi.app.store import AppStore


@pytest.fixture
def app(tmp_path):
    return AppServer(AppStore(tmp_path / "app.db"))


def _tui_cards():
    from yeaboi.ui.mode_select.screens._screens import _AGENT_CARDS, _MODE_CARDS

    return _MODE_CARDS, _AGENT_CARDS


class TestItMatchesTheTerminalApp:
    def test_the_same_modes_exist_in_the_same_order(self):
        human, agent = _tui_cards()
        assert [m.key for m in MODES if m.family == "humans"] == [c["key"] for c in human]
        assert [m.key for m in MODES if m.family == "agents"] == [c["key"] for c in agent]

    def test_titles_agree(self):
        human, agent = _tui_cards()
        for card in [*human, *agent]:
            assert BY_KEY[card["key"]].title == card["title"], card["key"]

    def test_descriptions_agree(self):
        # The description is the one sentence that says what a mode is for.
        # Two copies that disagree means the app is describing a different
        # product to the terminal.
        human, agent = _tui_cards()
        for card in [*human, *agent]:
            assert BY_KEY[card["key"]].description == card["description"], card["key"]

    def test_beta_badges_agree(self):
        human, agent = _tui_cards()
        for card in [*human, *agent]:
            assert BY_KEY[card["key"]].beta == bool(card.get("badge")), card["key"]


class TestTheCatalogueIsSound:
    def test_every_accent_is_one_the_design_layer_defines(self):
        """An accent the stylesheet has never heard of silently does nothing.

        The marketing site carries `--m-*` custom properties for all twelve;
        the *design layer* only has `[data-mode]` rules for eight, and those
        are the ones with a light variant and a contrast audit. Naming one of
        the other four here would render the default and look like a bug.
        """
        from pathlib import Path

        tokens = (
            Path(__file__).resolve().parents[2] / "frontend" / "src" / "design" / "tokens.css"
        ).read_text()
        for mode in MODES:
            if mode.accent:
                assert f'[data-mode="{mode.accent}"]' in tokens, (
                    f"{mode.key} names accent {mode.accent!r}, which design/tokens.css does not define"
                )

    def test_keys_are_unique(self):
        assert len({m.key for m in MODES}) == len(MODES)

    def test_a_mode_that_cannot_run_says_why_or_is_obvious(self):
        # A card that does nothing with no explanation reads as broken.
        for mode in MODES:
            if mode.support == "soon" and not mode.note:
                # Allowed only where the title alone makes it obvious; assert
                # the exception list stays small and deliberate.
                assert mode.key in {"daily-standup", "reporting", "performance"}, mode.key

    def test_payload_is_json_serialisable(self):
        assert json.loads(json.dumps(payload())) == payload()


class TestTheEndpoint:
    def test_it_needs_a_session(self, app):
        assert call(app, "GET", "/api/modes").code == 401

    def test_it_returns_every_mode(self, app):
        cookies, _ = sign_in(app)
        body = json.loads(call(app, "GET", "/api/modes", cookies=cookies).body)
        assert len(body["modes"]) == len(MODES)
        assert {m["family"] for m in body["modes"]} == {"humans", "agents"}


class TestEveryModeIsReachableOnARefresh:
    """The client router and the server's shell routes have to agree.

    A path the client knows and the server does not is a 404 on a hard refresh
    or a shared link — the screen works until someone reloads it, which is the
    worst moment to find out.
    """

    def test_a_mode_url_serves_the_shell(self, app):
        from yeaboi.app.router import parse_request

        for mode in MODES:
            response = app.handle(parse_request("GET", f"/modes/{mode.key}", {}))
            assert response.code == 200, mode.key
            assert response.content_type.startswith("text/html")

    def test_the_client_router_knows_the_same_pattern(self):
        from pathlib import Path

        app_tsx = (
            Path(__file__).resolve().parents[2] / "frontend" / "src" / "app" / "App.tsx"
        ).read_text()
        assert "'/modes/{key}'" in app_tsx
