"""The Connections tab, and the invariant that governs the whole feature.

With no ops vendor connected every surface must behave exactly as it did
before the connector layer existed — not more quietly, identically. These are
the tests that hold that line.
"""

from __future__ import annotations

import pytest
from rich.console import Console

from yeaboi.connectors import registry
from yeaboi.connectors.spec import ACCENT_RE
from yeaboi.ui.mode_select.screens._screens_secondary import (
    _SETTINGS_TAB_SECTIONS,
    _SETTINGS_TABS,
    _build_settings_screen,
)

CONNECTIONS_TAB = _SETTINGS_TABS.index("Connections")
API_KEY = "dd-api-key-abcdefghijkl"
APP_KEY = "dd-app-key-abcdefghijkl"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for env in registry.all_envs():
        monkeypatch.delenv(env, raising=False)


def _render(config_data: dict, *, width: int = 100, height: int = 44, tab: int = CONNECTIONS_TAB) -> str:
    console = Console(width=width, height=height, force_terminal=False)
    panel = _build_settings_screen(
        config_data, scroll_offset=0, scroll_meta={}, width=width, height=height, active_tab=tab
    )
    with console.capture() as cap:
        console.print(panel)
    return cap.get()


class TestTheTab:
    def test_connections_is_its_own_tab(self):
        assert _SETTINGS_TAB_SECTIONS["Connections"] == ["connections"]

    def test_the_zero_box_case_renders(self):
        # The one genuinely new rendering condition: a section that legitimately
        # has nothing to draw. "Hidden until connected" depends on it.
        out = _render({"_config_path": "/tmp/.env"})
        assert "Connections" in out
        assert "Nothing connected yet" in out

    def test_the_smallest_supported_terminal_survives_it(self):
        assert _render({"_config_path": "/tmp/.env"}, width=84, height=40)

    def test_an_unconnected_vendor_is_not_named(self):
        # The nag test: a user who has never heard of Datadog must not read its
        # name in their settings.
        assert "Datadog" not in _render({"_config_path": "/tmp/.env"})

    def test_a_connected_vendor_renders_with_its_identity(self, monkeypatch):
        from yeaboi.connectors.datadog import CONNECTOR

        monkeypatch.setenv("DATADOG_API_KEY", API_KEY)
        monkeypatch.setenv("DATADOG_APP_KEY", APP_KEY)
        out = _render({"_config_path": "/tmp/.env", "DATADOG_API_KEY": API_KEY, "DATADOG_APP_KEY": APP_KEY})
        assert "Datadog" in out
        assert CONNECTOR.mark in out
        assert "Observability" in out

    def test_each_connector_wears_its_own_accent(self):
        # The catalog must read as several things, not one wall of rows.
        from yeaboi.connectors.datadog import CONNECTOR

        console = Console(width=100, height=44, force_terminal=True, color_system="truecolor")
        panel = _build_settings_screen(
            {"_config_path": "/tmp/.env", "DATADOG_API_KEY": API_KEY, "DATADOG_APP_KEY": APP_KEY},
            scroll_offset=0,
            scroll_meta={},
            width=100,
            height=44,
            active_tab=CONNECTIONS_TAB,
        )
        with console.capture() as cap:
            console.print(panel)
        r, g, b = ACCENT_RE.match(CONNECTOR.accent).groups()
        assert f"{r};{g};{b}" in cap.get(), "the connector's accent never reached the screen"

    def test_secrets_are_masked_on_the_page(self):
        out = _render({"_config_path": "/tmp/.env", "DATADOG_API_KEY": API_KEY, "DATADOG_APP_KEY": APP_KEY})
        assert API_KEY not in out
        assert APP_KEY not in out
        assert "•" in out

    def test_a_non_secret_is_shown_plainly(self):
        out = _render(
            {
                "_config_path": "/tmp/.env",
                "DATADOG_API_KEY": API_KEY,
                "DATADOG_APP_KEY": APP_KEY,
                "DATADOG_SITE": "datadoghq.eu",
            }
        )
        assert "datadoghq.eu" in out


class TestNothingConnectedChangesNothing:
    """The governing invariant, asserted directly."""

    def test_the_agent_tool_list_is_untouched(self):
        # Ops connectors deliberately do NOT join get_tools(): that list is paid
        # for on every ReAct turn.
        from yeaboi.tools import get_tools

        names = {t.name for t in get_tools()}
        assert not any("datadog" in n for n in names)
        assert not any("connection" in n for n in names)

    def test_no_connector_tool_is_risk_classified(self):
        # A connector lives outside tools/, so it is invisible to the risk
        # registry by construction rather than by remembering.
        from yeaboi.tools.risk import TOOL_RISK

        assert not any("datadog" in name for name in TOOL_RISK)

    def test_no_settings_tab_names_an_unconnected_vendor(self):
        # The invariant across the whole screen, not just its own tab: a user who
        # has connected nothing must not read a vendor name anywhere in Settings.
        names = {c.label for c in registry.all_connectors()}
        for tab in range(len(_SETTINGS_TABS)):
            out = _render({"_config_path": "/tmp/.env"}, tab=tab)
            leaked = {name for name in names if name in out}
            assert not leaked, f"{_SETTINGS_TABS[tab]} named {sorted(leaked)}"

    def test_the_engine_snapshot_carries_no_connector_value(self):
        # The engine keeps the fields — update_setting validates against them, so
        # dropping them would make a connector unconnectable from the desktop —
        # but an unset one must report nothing beyond its own name.
        from yeaboi.settings.engine import get_settings

        connector_envs = set(registry.all_envs())
        rows = [f for f in get_settings().fields if f.env in connector_envs]
        assert rows, "the connector fields left the snapshot; writes validate against them"
        assert not any(f.is_set for f in rows)
        assert not any(f.value for f in rows)

    def test_enter_on_the_connections_tab_does_not_launch_the_setup_wizard(self):
        # The tab action table falls through to the first-run wizard, so a tab
        # that does not name itself drops the user into "choose an LLM provider".
        from yeaboi.ui.mode_select.screens._screens_secondary import settings_tab_action

        assert settings_tab_action(CONNECTIONS_TAB) == "connections"
        assert {settings_tab_action(i) for i in range(len(_SETTINGS_TABS))} == {
            "setup",
            "connections",
            "sharing",
            "loglevel",
        }

    def test_the_catalog_is_empty_and_says_nothing(self):
        from yeaboi.connectors.engine import list_connections

        assert list_connections() == {"connectors": [], "families": [], "connected": []}
