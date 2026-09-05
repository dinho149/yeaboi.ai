"""The integrations catalog browser — the one page that names every vendor.

Settings keeps its "no vendor named until connected" invariant
(test_connections_surfaces.py); this page is reached by an explicit gesture and
deliberately shows the whole roster. These tests pin that split: the catalog
names everything, points legacy rows at Credentials, and never lets a typed
secret reach the screen.
"""

from __future__ import annotations

import pytest
from rich.console import Console

from yeaboi.connectors import registry
from yeaboi.connectors.engine import list_connections
from yeaboi.ui.catalog import _entry_lines, build_catalog_screen, visible_rows


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for env in registry.all_envs() + registry.legacy_envs():
        monkeypatch.delenv(env, raising=False)


@pytest.fixture
def payload():
    return list_connections(connected_only=False, include_legacy=True)


def _render(panel, *, width: int = 100, height: int = 44) -> str:
    console = Console(width=width, height=height, force_terminal=False)
    with console.capture() as cap:
        console.print(panel)
    return cap.get()


class TestBrowse:
    def test_the_catalog_names_every_vendor_deliberately(self, payload):
        # Scrolled through the whole list, every entry appears — this page is
        # the sanctioned exception to "hidden until connected".
        labels = {row["label"] for row in payload["connectors"]}
        seen = ""
        for offset in range(0, 80, 10):
            seen += _render(build_catalog_screen(payload, 0, width=120, height=44, scroll_offset=offset))
        for label in labels:
            assert label in seen, f"{label} never renders in the catalog"

    def test_the_music_shelf_names_its_three_services(self, payload):
        # Keyless services are still catalogue entries: the shelf is where a
        # user finds out they exist, on this surface as on the desktop.
        out = _render(build_catalog_screen(payload, 0, filter_text="music", width=120, height=44))
        assert "Music" in out
        for label in ("Spotify", "Apple Music", "YouTube Music"):
            assert label in out, f"{label} missing from the music shelf"

    def test_the_filter_narrows_by_label_summary_and_family(self, payload):
        assert [r["key"] for r in visible_rows(payload, "gitla")] == ["gitlab"]
        assert "sentry" in [r["key"] for r in visible_rows(payload, "error")]  # family label
        assert visible_rows(payload, "zzzznope") == []
        assert len(visible_rows(payload, "")) == len(payload["connectors"])

    def test_a_legacy_row_points_at_credentials(self, payload):
        rows = visible_rows(payload, "github")
        idx = next(i for i, r in enumerate(rows) if r["key"] == "github")
        out = _render(build_catalog_screen(payload, idx, filter_text="github", width=120, height=44))
        assert "via Credentials" in out

    def test_a_connected_connector_wears_the_badge(self, payload, monkeypatch):
        monkeypatch.setenv("PAGERDUTY_API_KEY", "pd-tok")
        fresh = list_connections(connected_only=False, include_legacy=True)
        rows = visible_rows(fresh, "pagerduty")
        out = _render(build_catalog_screen(fresh, 0, filter_text="pagerduty", width=120, height=44))
        assert rows[0]["connected"] is True
        assert "connected" in out

    def test_the_smallest_supported_terminal_survives_it(self, payload):
        out = _render(build_catalog_screen(payload, 0, width=84, height=40), width=84, height=40)
        assert "Integrations catalog" in out

    def test_the_empty_filter_state_offers_the_way_out(self, payload):
        out = _render(build_catalog_screen(payload, 0, filter_text="zzzznope", width=100, height=40))
        assert "Esc clears the filter" in out


class TestEntryPane:
    def _entry_for(self, payload, key: str, *, typed: str) -> dict:
        row = next(r for r in payload["connectors"] if r["key"] == key)
        connector = registry.by_key(key)
        return {
            "row": row,
            "stage": "field",
            "methods": [],
            "method_idx": 0,
            "fields": list(connector.fields),
            "field_idx": 0,
            "typed": typed,
            "notice": "",
            "saved": [],
        }

    def test_a_typed_secret_renders_as_bullets_never_as_itself(self, payload):
        secret = "glpat-super-secret-token"
        entry = self._entry_for(payload, "gitlab", typed=secret)
        text = "".join(line.plain for line in _entry_lines(entry, 100))
        assert secret not in text
        assert "•" * len(secret) in text

    def test_a_non_secret_field_echoes_what_was_typed(self, payload):
        entry = self._entry_for(payload, "bitbucket", typed="acme")
        assert registry.by_key("bitbucket").fields[0].secret is False
        text = "".join(line.plain for line in _entry_lines(entry, 100))
        assert "acme" in text

    def test_the_method_stage_marks_the_recommended_way_in(self, payload):
        row = next(r for r in payload["connectors"] if r["key"] == "aws")
        connector = registry.by_key("aws")
        entry = {
            "row": row,
            "stage": "method",
            "methods": list(connector.auth_methods),
            "method_idx": 0,
            "fields": [],
            "field_idx": 0,
            "typed": "",
            "notice": "",
            "saved": [],
        }
        text = "".join(line.plain for line in _entry_lines(entry, 100))
        assert "recommended" in text
        # Every non-recommended method carries its warning on the same screen.
        for method in connector.auth_methods:
            if not method.recommended:
                assert method.warning in text
